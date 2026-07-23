"""REM join / status dialogs for the GUI. Mirror the `lem rem` CLI commands
so the desktop app and the script expose the same REM features."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout,
)

from lem.rem_client import RemError, parse_join_code


class JoinDialog(QDialog):
    """Paste a join code; live-parses it and calls back to run hello()."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to REM")
        self.join = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Paste the REM join code your operator gave you.\n"
            "LEM measures locally and never contacts the TP-Link cloud — "
            "your data goes only to your REM server."
        ))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("REM1-…")
        self.edit.textChanged.connect(self._validate)
        layout.addWidget(self.edit)
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Join")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._validate("")

    def _validate(self, text):
        try:
            self.join = parse_join_code(text)
            self.feedback.setText(
                f"[✓] Code for experiment id '{self.join.experiment_id}' at {self.join.url}"
            )
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)
        except RemError as e:
            self.join = None
            self.feedback.setText("" if not text.strip() else str(e))
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)


class StatusDialog(QDialog):
    """Show the current REM connection with Sync-now and Disconnect actions.
    Buttons emit their intent via the returned action string."""

    def __init__(self, rem_config, unsynced_count, uploader_state, parent=None):
        super().__init__(parent)
        self.setWindowTitle("REM connection")
        self.action = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{rem_config.experiment_name or rem_config.experiment_id}</b>"))
        layout.addWidget(QLabel(f"Server: {rem_config.url}"))
        if uploader_state is not None:
            layout.addWidget(QLabel(
                f"This session: uploaded {uploader_state.rows_uploaded} rows "
                f"({uploader_state.status})"
            ))
        if unsynced_count:
            layout.addWidget(QLabel(
                f"{unsynced_count} earlier run(s) not fully uploaded to REM."
            ))

        row = QHBoxLayout()
        if unsynced_count:
            sync_btn = QPushButton(f"Sync {unsynced_count} run(s) now")
            sync_btn.clicked.connect(lambda: self._choose("sync"))
            row.addWidget(sync_btn)
        disconnect_btn = QPushButton("Disconnect")
        disconnect_btn.clicked.connect(lambda: self._choose("leave"))
        row.addWidget(disconnect_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def _choose(self, action):
        self.action = action
        self.accept()
