"""REM join / status dialogs for the GUI. Mirror the `lem rem` CLI commands
so the desktop app and the script expose the same REM features."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout,
)

from lem.rem_client import DEFAULT_REM_URL, JOIN_CODE_PREFIX


class JoinDialog(QDialog):
    """Enter a join code (short like K7F3QP, or a REM1-… code) and the server."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to REM")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Enter the join code your operator gave you.\n"
            "LEM measures locally and never contacts the TP-Link cloud — "
            "your data goes only to your REM server."
        ))
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("K7F3QP  or  REM1-…")
        self.code_edit.textChanged.connect(self._sync)
        layout.addWidget(QLabel("Join code:"))
        layout.addWidget(self.code_edit)

        layout.addWidget(QLabel("REM server:"))
        self.url_edit = QLineEdit(DEFAULT_REM_URL)
        layout.addWidget(self.url_edit)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Join")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._sync("")

    def _sync(self, text):
        # Self-contained REM1- codes carry their own URL, so grey the URL box.
        is_long = self.code_edit.text().strip().startswith(JOIN_CODE_PREFIX)
        self.url_edit.setEnabled(not is_long)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self.code_edit.text().strip()))

    def code(self) -> str:
        return self.code_edit.text().strip()

    def url(self) -> str:
        return self.url_edit.text().strip() or DEFAULT_REM_URL


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
