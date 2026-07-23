"""Background threads bridging the asyncio core to the Qt event loop.

Each worker runs its own asyncio loop on a QThread. The GUI thread reads the
shared PlugState objects on a timer (safe: plain attribute reads under the
GIL) and requests a stop via call_soon_threadsafe.
"""

import asyncio

from PySide6.QtCore import QThread, Signal

from lem import runner
from lem.rem_client import RemClient, RemError, resolve_code
from lem.scan import scan_network
from lem.uploader import (
    UploaderState, find_unsynced, init_sidecar, run_uploader, sync_run,
)


class MeasurementWorker(QThread):
    completed = Signal(bool)   # interrupted?
    failed = Signal(str)

    def __init__(self, plugs, states, sinks, interval, duration, run_name,
                 uploader_spec=None, parent=None):
        super().__init__(parent)
        self._plugs = plugs
        self._states = states
        self._sinks = sinks
        self._interval = interval
        self._duration = duration
        self._run_name = run_name
        # (RemClient, alias_map, UploaderState) or None — same core uploader
        # the CLI uses, so both frontends stream identically.
        self._uploader_spec = uploader_spec
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

        tasks = []
        if self._uploader_spec is not None:
            client, alias_map, up_state = self._uploader_spec
            combined = self._sinks[0].combined_path
            init_sidecar(combined, getattr(client, "experiment_id", ""))
            tasks.append(asyncio.create_task(run_uploader(
                combined, alias_map, client, up_state, self._stop,
                list(alias_map.values()),
            )))
        try:
            return await runner.run(
                self._plugs, self._states, self._sinks, self._interval, self._duration,
                stop_event=self._stop, handle_sigint=False,
            )
        finally:
            for t in tasks:
                await t

    def request_stop(self):
        if self._loop and self._stop:
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:
                pass  # loop already closed


class RemJoinWorker(QThread):
    """Resolve a code (short or long) + hello() off the UI thread."""
    joined = Signal(object, object)   # (RemJoin, HelloResult)
    failed = Signal(str)

    def __init__(self, code, url, parent=None):
        super().__init__(parent)
        self._code = code
        self._url = url

    def run(self):
        try:
            join = resolve_code(self._code, self._url)
            hello = RemClient(join.url, join.token).hello()
        except RemError as e:
            self.failed.emit(str(e))
            return
        self.joined.emit(join, hello)


class RemSyncWorker(QThread):
    """Backfill any unsynced runs (or one file) via the shared uploader."""
    progress = Signal(str)
    done = Signal(int)
    failed = Signal(str)

    def __init__(self, results_dir, alias_map, client, one_file=None, parent=None):
        super().__init__(parent)
        self._results_dir = results_dir
        self._alias_map = alias_map
        self._client = client
        self._one_file = one_file

    def run(self):
        try:
            asyncio.run(self._main())
        except RemError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(str(e) or type(e).__name__)

    async def _main(self):
        targets = [self._one_file] if self._one_file else find_unsynced(self._results_dir)
        total = 0
        for combined in targets:
            state = UploaderState()
            self.progress.emit(f"Uploading {combined.name} …")
            await sync_run(combined, self._alias_map, self._client, state)
            total += state.rows_uploaded
        self.done.emit(total)


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
