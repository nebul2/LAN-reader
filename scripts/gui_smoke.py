"""Offscreen GUI smoke test (no display or hardware needed):

    QT_QPA_PLATFORM=offscreen venv/bin/python scripts/gui_smoke.py

Drives the real MainWindow through a fake-device measurement and checks the
CSVs land, then exercises the unlimited-duration + Stop path.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from lem.gui.app import MainWindow

tmp = Path(tempfile.mkdtemp(prefix="lanreader_gui_smoke_"))
cfg = tmp / "config.toml"
cfg.write_text(
    '[defaults]\ninterval = 0.5\nduration = "3s"\nresults_dir = "results"\n\n'
    '[plugs.fake1]\ntype = "fake"\n\n[plugs.fake2]\ntype = "fake"\n'
)

app = QApplication(sys.argv)
win = MainWindow(config_path=cfg)
assert win.plug_list.count() == 2, "plugs not loaded from config"


def run_until_done(timeout=20):
    deadline = time.time() + timeout
    while win.worker is not None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    assert win.worker is None, "measurement did not finish in time"


# --- finite run over both fake plugs ---------------------------------------
for i in range(win.plug_list.count()):
    win.plug_list.item(i).setCheckState(Qt.Checked)
win.duration_edit.setText("3s")
win.start_clicked()
assert win.worker is not None, "worker did not start"
run_until_done()

assert all(s.sample_count > 0 for s in win.states.values()), "no samples recorded"
csvs = sorted(p.name for p in (tmp / "results").glob("*.csv"))
assert len(csvs) == 3, f"expected 3 CSVs, got {csvs}"
assert "Done." in win.status_label.text(), win.status_label.text()
print("finite run OK:", csvs)

# --- unlimited run + Stop button -------------------------------------------
win.duration_edit.setText("unlimited")
win.start_clicked()
t0 = time.time()
while time.time() - t0 < 1.5:
    app.processEvents()
    time.sleep(0.02)
win.stop_clicked()
run_until_done()
assert "Stopped." in win.status_label.text() or "Done." in win.status_label.text()
csvs = sorted(p.name for p in (tmp / "results").glob("*.csv"))
assert len(csvs) == 6, f"expected 6 CSVs after second run, got {csvs}"
print("unlimited+stop OK:", len(csvs), "CSV files total")

print("GUI smoke test passed.")
