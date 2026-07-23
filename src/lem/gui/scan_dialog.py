"""Dialog presenting scan results: tick which plugs to use/refresh, edit names.

Plugs already configured at the same IP are pre-ticked and shown with their
current alias — accepting them refreshes their nickname in place (upsert).
Nothing here removes plugs (that's the main window's Remove button)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QLineEdit, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from lem.scan import ENERGY_MODELS, sanitize_alias, unique_alias


class ScanResultsDialog(QDialog):

    def __init__(self, found, existing_aliases, ip_to_alias, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plugs found on the network")
        self._found = found
        self._ip_to_alias = ip_to_alias

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"Found {len(found)} Tapo device(s). Tick the ones to use; already-"
            "configured plugs (✓) will have their names refreshed."
        ))

        self.table = QTableWidget(len(found), 4, self)
        self.table.setHorizontalHeaderLabels(["Use", "Device", "Model", "Name"])
        self.table.verticalHeader().setVisible(False)
        self._alias_edits = []
        # Reserve the aliases of plugs NOT shown here so we don't collide with them.
        shown_aliases = {ip_to_alias[d["ip"]] for d in found if d["ip"] in ip_to_alias}
        suggested = set(existing_aliases) - shown_aliases
        for row, d in enumerate(found):
            known = ip_to_alias.get(d["ip"])

            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, check)

            label = d["ip"] + (f'  "{d["nickname"]}"' if d["nickname"] else "")
            if known:
                label += f"  [currently '{known}']"
            self.table.setItem(row, 1, QTableWidgetItem(label))

            model = d["model"]
            if not model.startswith(ENERGY_MODELS):
                model += "  (no energy meter?)"
            self.table.setItem(row, 2, QTableWidgetItem(model))

            # Default name: keep an existing plug's own alias; else slug the nickname.
            base = known or (sanitize_alias(d["nickname"]) if d["nickname"]
                             else "plug" + d["ip"].rsplit(".", 1)[1])
            default = unique_alias(base, suggested)
            suggested.add(default)
            edit = QLineEdit(default)
            self._alias_edits.append(edit)
            self.table.setCellWidget(row, 3, edit)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selection(self) -> list[tuple]:
        """Ticked rows as [(alias, ip, tapo_name), ...], aliases sanitized/unique."""
        taken = set()
        accepted = []
        for row, d in enumerate(self._found):
            if self.table.item(row, 0).checkState() != Qt.Checked:
                continue
            base = sanitize_alias(self._alias_edits[row].text())
            alias = unique_alias(base, taken)
            taken.add(alias)
            accepted.append((alias, d["ip"], d.get("nickname") or None))
        return accepted
