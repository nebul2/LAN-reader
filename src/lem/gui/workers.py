"""Background threads bridging the asyncio core to the Qt event loop.

Each worker runs its own asyncio loop on a QThread. The GUI thread reads the
shared PlugState objects on a timer (safe: plain attribute reads under the
GIL) and requests a stop via call_soon_threadsafe.
"""

import asyncio

from PySide6.QtCore import QThread, Signal

from lem import runner
from lem.scan import scan_network


class MeasurementWorker(QThread):
    completed = Signal(bool)   # interrupted?
    failed = Signal(str)

    def __init__(self, plugs, states, sinks, interval, duration, run_name, parent=None):
        super().__init__(parent)
        self._plugs = plugs
        self._states = states
        self._sinks = sinks
        self._interval = interval
        self._duration = duration
        self._run_name = run_name
        self._loop = None
        self._stop = None

    def run(self):
        try:
            interrupted = asyncio.run(self._main())
        except Exception as e:
            self.failed.emit(str(e) or type(e).__name__)
            return
        self.completed.emit(interrupted)

    async def _main(self) -> bool:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        for sink in self._sinks:
            await sink.open(self._run_name, [p.alias for p in self._plugs])
        return await runner.run(
            self._plugs, self._states, self._sinks, self._interval, self._duration,
            stop_event=self._stop, handle_sigint=False,
        )

    def request_stop(self):
        if self._loop and self._stop:
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:
                pass  # loop already closed


class ScanWorker(QThread):
    found = Signal(list)
    failed = Signal(str)

    def __init__(self, network, username, password, parent=None):
        super().__init__(parent)
        self._network = network
        self._username = username
        self._password = password

    def run(self):
        try:
            devices = asyncio.run(scan_network(self._network, self._username, self._password))
        except Exception as e:
            self.failed.emit(str(e) or type(e).__name__)
            return
        self.found.emit(devices)
