"""Live terminal display (rich): progress bar + per-plug table, and the
end-of-run summary."""

import asyncio
import time

from rich.console import Console, Group
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table
from rich.live import Live

from lem.model import PlugState

REFRESH_INTERVAL = 0.25

_STATUS_STYLE = {"ok": "green", "connecting": "yellow", "retrying": "yellow", "error": "red"}


def _short_error(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_w(mw: float | None) -> str:
    return f"{mw / 1000:.3f} W" if mw is not None else "—"


def _build_table(states: list[PlugState]) -> Table:
    table = Table(box=None, pad_edge=False)
    table.add_column("PLUG", style="bold")
    table.add_column("IP")
    table.add_column("POWER", justify="right")
    table.add_column("SAMPLES", justify="right")
    table.add_column("STATUS")
    for s in states:
        style = _STATUS_STYLE.get(s.status, "white")
        status = s.status if not s.last_error else f"{s.status} ({_short_error(s.last_error)})"
        table.add_row(
            s.alias, s.ip, _fmt_w(s.last_power_mw), str(s.sample_count),
            f"[{style}]{status}[/{style}]",
        )
    return table


def make_display(states: list[PlugState], duration: float | None, console: Console):
    """Returns an async callable(stop_event) for runner.run()'s display slot."""

    async def display_task(stop_event: asyncio.Event) -> None:
        if duration is not None:
            progress = Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
                console=console,
            )
            task_id = progress.add_task(f"Measuring {len(states)} plug(s)", total=duration)
        else:
            progress = Progress(
                TextColumn("[progress.description]{task.description}"),
                TextColumn("elapsed {task.completed:.0f}s — Ctrl-C to stop"),
                console=console,
            )
            task_id = progress.add_task(f"Measuring {len(states)} plug(s)", total=None)

        start = time.monotonic()
        with Live(console=console, refresh_per_second=8) as live:
            while not stop_event.is_set():
                elapsed = time.monotonic() - start
                progress.update(task_id, completed=elapsed)
                live.update(Group(progress, _build_table(states)))
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=REFRESH_INTERVAL)
                except TimeoutError:
                    pass
            progress.update(task_id, completed=duration if duration is not None else elapsed)
            live.update(Group(progress, _build_table(states)))

    return display_task


def print_summary(
    states: list[PlugState], paths, elapsed: float, interrupted: bool, console: Console
) -> None:
    console.print()
    verb = "Stopped" if interrupted else "Done"
    console.print(f"[bold]{verb}[/bold] after {elapsed:.0f}s")
    table = Table(box=None, pad_edge=False)
    table.add_column("PLUG", style="bold")
    table.add_column("SAMPLES", justify="right")
    table.add_column("MEAN", justify="right")
    table.add_column("MIN", justify="right")
    table.add_column("MAX", justify="right")
    for s in states:
        if s.sample_count:
            table.add_row(
                s.alias, str(s.sample_count),
                _fmt_w(s.mean_mw), _fmt_w(s.min_mw), _fmt_w(s.max_mw),
            )
        else:
            table.add_row(s.alias, "0", "—", "—", "—")
    console.print(table)
    console.print()
    for p in paths:
        console.print(f"  → {p}")
