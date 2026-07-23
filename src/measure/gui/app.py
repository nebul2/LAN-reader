"""PySide6 desktop frontend — same core as the CLI, different face.

Entry point: measure-gui (see pyproject.toml). Set LAN_READER_SMOKE=1 to
construct the window and exit immediately (used by packaging smoke tests).
"""

import os
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDoubleSpinBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from measure import scan as scan_mod
from measure.cli import parse_duration
from measure.config import DEFAULT_PATHS, ConfigError, load_config
from measure.gui.scan_dialog import ScanResultsDialog
from measure.gui.workers import MeasurementWorker, ScanWorker
from measure.model import PlugState
from measure.sinks.csv_sink import CsvSink


def default_config_path() -> Path:
    for p in DEFAULT_PATHS:
        if p.exists():
            return p
    # A double-clicked .app has no meaningful cwd — create under ~/.config.
    return DEFAULT_PATHS[1]


def fmt_w(mw) -> str:
    return f"{mw / 1000:.3f}" if mw is not None else "—"


class MainWindow(QMainWindow):

    def __init__(self, config_path: Path | None = None):
        super().__init__()
        self.config_path = config_path or default_config_path()
        self.config = None
        self.worker = None
        self.scan_worker = None
        self.states = {}
        self.current_sink = None
        self.start_time = None
        self.run_duration = None
        # Credentials typed this session, kept so a failed scan doesn't
        # re-prompt (they're only written to config after a successful scan).
        self._session_creds = None

        self.setWindowTitle("LAN-reader")
        self.resize(680, 640)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.addWidget(QLabel("Plugs (tick the ones to measure):"))
        self.plug_list = QListWidget()
        self.plug_list.setMaximumHeight(160)
        layout.addWidget(self.plug_list)

        scan_row = QHBoxLayout()
        self.scan_button = QPushButton("Scan network for plugs…")
        self.scan_button.clicked.connect(self.scan_clicked)
        scan_row.addWidget(self.scan_button)
        reload_button = QPushButton("Reload config")
        reload_button.clicked.connect(self.reload_config)
        scan_row.addWidget(reload_button)
        scan_row.addStretch()
        layout.addLayout(scan_row)

        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel("Duration:"))
        self.duration_edit = QLineEdit("10m")
        self.duration_edit.setToolTip("e.g. 90s, 10m, 2h, or 'unlimited'")
        self.duration_edit.setMaximumWidth(110)
        settings_row.addWidget(self.duration_edit)
        settings_row.addWidget(QLabel("Interval:"))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.1, 3600.0)
        self.interval_spin.setValue(2.0)
        self.interval_spin.setDecimals(1)
        self.interval_spin.setSuffix(" s")
        settings_row.addWidget(self.interval_spin)
        settings_row.addStretch()
        layout.addLayout(settings_row)

        run_row = QHBoxLayout()
        self.start_button = QPushButton("Start measurement")
        self.start_button.clicked.connect(self.start_clicked)
        run_row.addWidget(self.start_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_clicked)
        run_row.addWidget(self.stop_button)
        self.remaining_label = QLabel("")
        run_row.addWidget(self.remaining_label)
        run_row.addStretch()
        layout.addLayout(run_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Plug", "IP", "Power (W)", "Samples", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        bottom_row = QHBoxLayout()
        results_button = QPushButton("Open results folder")
        results_button.clicked.connect(self.open_results)
        bottom_row.addWidget(results_button)
        bottom_row.addStretch()
        layout.addLayout(bottom_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)

        self.reload_config()

    # ------------------------------------------------------------------ config

    def reload_config(self):
        self.plug_list.clear()
        self.config = None
        if not self.config_path.exists():
            self.status_label.setText(
                f"No config yet ({self.config_path}). Use 'Scan network for plugs…' to create one."
            )
            return
        try:
            self.config = load_config(self.config_path)
        except ConfigError as e:
            QMessageBox.warning(self, "Config error", str(e))
            return
        for alias, plug in self.config.plugs.items():
            item = QListWidgetItem(f"{alias}   ({plug.type}, {plug.ip})")
            item.setData(Qt.UserRole, alias)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.plug_list.addItem(item)
        self.duration_edit.setText(str(self.config.duration))
        self.interval_spin.setValue(self.config.interval)
        self.status_label.setText(f"Config: {self.config_path} — {len(self.config.plugs)} plug(s)")

    def results_dir(self) -> Path:
        base = self.config.results_dir if self.config else Path("results")
        return base if base.is_absolute() else self.config_path.parent / base

    def checked_aliases(self) -> list[str]:
        out = []
        for i in range(self.plug_list.count()):
            item = self.plug_list.item(i)
            if item.checkState() == Qt.Checked:
                out.append(item.data(Qt.UserRole))
        return out

    # ------------------------------------------------------------- measurement

    def start_clicked(self):
        if self.config is None:
            QMessageBox.warning(self, "No config", "Scan the network first to set up plugs.")
            return
        aliases = self.checked_aliases()
        if not aliases:
            QMessageBox.warning(self, "No plugs selected", "Tick at least one plug to measure.")
            return
        try:
            duration = parse_duration(self.duration_edit.text() or "unlimited")
        except ValueError as e:
            QMessageBox.warning(self, "Invalid duration", str(e))
            return
        interval = self.interval_spin.value()

        plugs = [self.config.plugs[a] for a in aliases]
        self.states = {p.alias: PlugState(alias=p.alias, ip=p.ip) for p in plugs}
        self.current_sink = CsvSink(self.results_dir())
        run_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_duration = duration
        self.start_time = time.monotonic()

        self.table.setRowCount(len(plugs))
        for row, p in enumerate(plugs):
            for col, text in enumerate([p.alias, p.ip, "—", "0", "connecting"]):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

        self.worker = MeasurementWorker(
            plugs, self.states, [self.current_sink], interval, duration, run_name
        )
        self.worker.completed.connect(self.run_finished)
        self.worker.failed.connect(self.run_failed)
        self.worker.start()

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.scan_button.setEnabled(False)
        if duration is None:
            self.progress.setRange(0, 0)  # busy indicator
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(0)
        self.status_label.setText(f"Measuring {len(plugs)} plug(s)… data is saved continuously.")
        self.timer.start()

    def stop_clicked(self):
        if self.worker:
            self.worker.request_stop()
            self.status_label.setText("Stopping…")

    def tick(self):
        for row, state in enumerate(self.states.values()):
            self.table.item(row, 2).setText(fmt_w(state.last_power_mw))
            self.table.item(row, 3).setText(str(state.sample_count))
            status = state.status
            if state.last_error:
                status += f" ({' '.join(state.last_error.split())[:60]})"
            self.table.item(row, 4).setText(status)
        if self.run_duration is not None and self.start_time is not None:
            elapsed = time.monotonic() - self.start_time
            self.progress.setValue(min(1000, int(elapsed / self.run_duration * 1000)))
            remaining = max(0, int(self.run_duration - elapsed))
            self.remaining_label.setText(f"{remaining // 60:02d}:{remaining % 60:02d} remaining")
        elif self.start_time is not None:
            elapsed = int(time.monotonic() - self.start_time)
            self.remaining_label.setText(f"{elapsed // 60:02d}:{elapsed % 60:02d} elapsed")

    def _run_teardown(self):
        self.timer.stop()
        self.tick() if self.states else None
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.scan_button.setEnabled(True)
        self.progress.setRange(0, 1000)
        self.worker = None

    def run_finished(self, interrupted: bool):
        self._run_teardown()
        self.progress.setValue(1000)
        parts = []
        for s in self.states.values():
            if s.sample_count:
                parts.append(
                    f"{s.alias}: {s.sample_count} samples, "
                    f"mean {fmt_w(s.mean_mw)} W (min {fmt_w(s.min_mw)} / max {fmt_w(s.max_mw)})"
                )
            else:
                parts.append(f"{s.alias}: no samples")
        where = f"Saved in {self.results_dir()}"
        self.status_label.setText(("Stopped. " if interrupted else "Done. ") + " | ".join(parts) + f". {where}")
        self.remaining_label.setText("")

    def run_failed(self, message: str):
        self._run_teardown()
        QMessageBox.critical(self, "Measurement failed", message)
        self.status_label.setText(f"Measurement failed: {message}")

    # -------------------------------------------------------------------- scan

    def scan_clicked(self):
        raw = {}
        if self.config_path.exists():
            with open(self.config_path, "rb") as f:
                raw = tomllib.load(f)
        creds = raw.get("credentials", {}).get("tapo", {})
        username = os.environ.get("TAPO_USERNAME") or creds.get("username") or ""
        password = os.environ.get("TAPO_PASSWORD") or creds.get("password") or ""
        if (not username or not password) and self._session_creds:
            username, password = self._session_creds
        if not username or not password:
            username, ok = QInputDialog.getText(
                self, "Tapo account", "Tapo cloud username (email):", text=username
            )
            if not ok or not username:
                return
            password, ok = QInputDialog.getText(
                self, "Tapo account", "Tapo cloud password:", QLineEdit.Password
            )
            if not ok or not password:
                return
            self._session_creds = (username, password)

        try:
            default = str(scan_mod.default_network())
        except OSError:
            default = ""
        subnet, ok = QInputDialog.getText(
            self, "Scan network", "Subnet to scan:", text=default
        )
        if not ok:
            return
        try:
            network = scan_mod.resolve_network(subnet or "auto")
        except ValueError as e:
            QMessageBox.warning(self, "Invalid subnet", str(e))
            return

        self._scan_raw = raw
        self._scan_creds = (username, password)
        self.scan_worker = ScanWorker(network, username, password)
        self.scan_worker.found.connect(self.scan_finished)
        self.scan_worker.failed.connect(self.scan_failed)
        self.scan_worker.start()
        self.scan_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.status_label.setText(f"Scanning {network}… this takes a minute or so.")

    def _scan_teardown(self):
        self.scan_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.scan_worker = None

    def scan_failed(self, message: str):
        self._scan_teardown()
        QMessageBox.critical(self, "Scan failed", message)
        self.status_label.setText(f"Scan failed: {message}")

    def scan_finished(self, found: list):
        self._scan_teardown()
        if not found:
            QMessageBox.information(
                self, "No plugs found",
                "No Tapo devices answered. If plugs are definitely on this network, "
                "check the Tapo username/password — a failed login looks identical "
                "to a non-Tapo device.",
            )
            self.status_label.setText("Scan finished: no Tapo devices found.")
            return

        username, password = self._scan_creds
        note = scan_mod.ensure_credentials_saved(self.config_path, self._scan_raw, username, password)

        existing = self.config.plugs if self.config else {}
        tapo_aliases = {a for a, p in existing.items() if p.type == "tapo"}
        ip_to_alias = {p.ip: a for a, p in existing.items() if p.type == "tapo"}
        dialog = ScanResultsDialog(found, set(existing), tapo_aliases, ip_to_alias, self)
        if dialog.exec():
            mode, accepted = dialog.selection()
            if accepted or mode == "replace":
                scan_mod.write_plugs(
                    self.config_path, accepted,
                    tapo_aliases if mode == "replace" else frozenset(),
                )
        self.reload_config()
        if note:
            self.status_label.setText(note + "  " + self.status_label.text())

    # ------------------------------------------------------------------- misc

    def open_results(self):
        folder = self.results_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            self.worker.wait(5000)  # sinks flush per-row; data is safe regardless
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("LAN-reader")
    window = MainWindow()
    if os.environ.get("LAN_READER_SMOKE"):
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
