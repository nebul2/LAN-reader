"""Offscreen GUI + REM end-to-end smoke test.

Runs REM's field_api as a real HTTP server (stubbed DB), drives the actual
MainWindow through a join and a fake-plug measurement, and checks rows land
in the DB under the Tapo nickname — proving the GUI uses the same REM path
as the CLI.

    QT_QPA_PLATFORM=offscreen python scripts/gui_rem_smoke.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/Users/nebul2/dev/rem/admin")

import field_api
import urllib.request
import uvicorn
from fastapi import FastAPI
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

# Modal dialogs would block forever offscreen — make them no-ops for the test.
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)

tmp = Path(tempfile.mkdtemp(prefix="gui_rem_"))
EXPERIMENTS = {"tv": {"id": "tv", "name": "TV Study", "is_current": True,
                      "linked_groups": [], "target_cadence_s": 10}}
GROUPS, DB = {}, []


class C:
    def cursor(self): return self
    def close(self): pass
    def commit(self): pass


field_api.configure(
    data_dir=tmp, get_db_connection=lambda: C(),
    load_groups=lambda: GROUPS, save_groups=lambda g: GROUPS.update(g),
    load_experiments=lambda: EXPERIMENTS, save_experiments=lambda e: EXPERIMENTS.update(e),
    public_url="http://127.0.0.1:8286",
)
field_api.execute_values = lambda cur, sql, values: DB.extend(values)

app_api = FastAPI()
app_api.include_router(field_api.router)
server = uvicorn.Server(uvicorn.Config(app_api, host="127.0.0.1", port=8286, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(50):
    if server.started:
        break
    time.sleep(0.1)
assert server.started

req = urllib.request.Request("http://127.0.0.1:8286/api/experiments/tv/field-token", method="POST")
join_code = json.loads(urllib.request.urlopen(req).read())["join_code"]

cfg = tmp / "config.toml"
cfg.write_text(
    f'[defaults]\ninterval = 0.5\nduration = "2s"\nresults_dir = {json.dumps(str(tmp / "results"))}\n\n'
    '[plugs.desk]\ntype = "fake"\ntapo_name = "Lyon TV"\n'
)

from lem.gui.app import MainWindow

qapp = QApplication(sys.argv)
win = MainWindow(config_path=cfg)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)


# --- join via the real worker + dialog plumbing (bypass modal exec) ---
from lem.rem_client import parse_join_code
win._pending_join = parse_join_code(join_code)
from lem.gui.workers import RemJoinWorker
w = RemJoinWorker(win._pending_join.url, win._pending_join.token)
result = {}
w.joined.connect(lambda h: (win._rem_joined(h), result.update(ok=True)))
w.failed.connect(lambda m: result.update(err=m))
w.start()
while not result:
    pump(0.1)
assert result.get("ok"), result
assert win.config.rem is not None, "join did not persist [rem]"
assert win.rem_button.text().startswith("REM:")
assert win.interval_spin.value() == 10.0, "cadence not adopted"
print("join OK — cadence adopted, [rem] written")

# --- measure with REM streaming ---
win.plug_list.item(0).setCheckState(Qt.Checked)
win.duration_edit.setText("2s")
win.start_clicked()
assert win.worker is not None and win.rem_state is not None
deadline = time.time() + 20
while win.worker is not None and time.time() < deadline:
    pump(0.1)
assert win.worker is None, "run did not finish"
pump(0.5)

aliases = {r[1] for r in DB}
assert "Lyon TV" in aliases and "desk" not in aliases, aliases
combined = next((tmp / "results").glob("*_combined.csv"))
csv_rows = combined.read_text().strip().splitlines()[1:]
assert len(csv_rows) == len(DB), (len(csv_rows), len(DB))
assert win.rem_state.rows_uploaded == len(DB)
verdict = (f"GUI+REM smoke PASSED — join cadence adopted ({win.interval_spin.value()}s), "
           f"{len(DB)} rows uploaded under Tapo nickname (csv==db=={len(DB)})")
Path("/tmp/lem_gui_rem_verdict.txt").write_text(verdict)
server.should_exit = True

import os
os._exit(0)
