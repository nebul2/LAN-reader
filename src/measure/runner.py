"""Async orchestration: one polling task per plug, shared stop event,
sink fan-out, graceful Ctrl-C handling."""

import asyncio
import signal
from datetime import datetime, timezone

from measure.config import PlugConfig
from measure.devices import make_device
from measure.model import PlugState, Sample
from measure.sinks.base import BaseSink

CONNECT_BACKOFF_INITIAL = 2.0
CONNECT_BACKOFF_MAX = 15.0
MAX_CONSECUTIVE_READ_FAILURES = 3


async def poll_plug(
    plug: PlugConfig,
    state: PlugState,
    sinks: list[BaseSink],
    interval: float,
    stop_event: asyncio.Event,
) -> None:
    backoff = CONNECT_BACKOFF_INITIAL
    while not stop_event.is_set():
        device = make_device(plug.type)
        try:
            state.status = "connecting"
            await device.connect(plug.ip, **plug.credentials)
        except Exception as e:
            state.status = "retrying"
            state.last_error = f"connect failed: {str(e) or type(e).__name__}"
            await _wait(stop_event, backoff)
            backoff = min(backoff * 2, CONNECT_BACKOFF_MAX)
            continue

        backoff = CONNECT_BACKOFF_INITIAL
        state.status = "ok"
        failures = 0
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        try:
            while not stop_event.is_set():
                try:
                    mw = await device.get_power_mw()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    failures += 1
                    state.status = "error"
                    state.last_error = str(e) or type(e).__name__
                    if failures >= MAX_CONSECUTIVE_READ_FAILURES:
                        break  # reconnect
                else:
                    failures = 0
                    sample = Sample(
                        ts=datetime.now(timezone.utc), alias=plug.alias, power_mw=mw
                    )
                    for sink in sinks:
                        await sink.write(sample)
                    state.record(sample)

                next_tick += interval
                delay = next_tick - loop.time()
                if delay > 0:
                    await _wait(stop_event, delay)
                else:
                    next_tick = loop.time()  # fell behind; realign
        finally:
            try:
                await device.disconnect()
            except Exception:
                pass


async def _wait(stop_event: asyncio.Event, seconds: float) -> None:
    """Sleep that wakes early when the stop event fires."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run(
    plugs: list[PlugConfig],
    states: dict[str, PlugState],
    sinks: list[BaseSink],
    interval: float,
    duration: float | None,
    display_coro=None,
) -> bool:
    """Run the measurement. Returns True if stopped by Ctrl-C."""
    stop_event = asyncio.Event()
    interrupted = False

    loop = asyncio.get_running_loop()

    def on_sigint():
        nonlocal interrupted
        if not stop_event.is_set():
            interrupted = True
            stop_event.set()
        else:
            # Second Ctrl-C: hard stop. Data is already flushed per row.
            raise KeyboardInterrupt

    loop.add_signal_handler(signal.SIGINT, on_sigint)

    tasks = [
        asyncio.create_task(poll_plug(p, states[p.alias], sinks, interval, stop_event))
        for p in plugs
    ]
    if duration is not None:
        tasks.append(asyncio.create_task(_timer(stop_event, duration)))
    if display_coro is not None:
        tasks.append(asyncio.create_task(display_coro(stop_event)))

    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        loop.remove_signal_handler(signal.SIGINT)
        for sink in sinks:
            await sink.close()
    return interrupted


async def _timer(stop_event: asyncio.Event, duration: float) -> None:
    await _wait(stop_event, duration)
    stop_event.set()
