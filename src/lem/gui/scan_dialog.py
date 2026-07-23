"""Dialog presenting scan results: tick which plugs to use/refresh.

Names come from the device's Tapo nickname (the source of truth) and are not
editable here — LEM can't change a plug's name. Plugs already configured at
the same IP are pre-ticked and shown with their current handle; accepting them
refreshes their nickname in place (upsert). Nothing here removes plugs."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from lem.scan import ENERGY_MODELS, sanitize_alias, unique_alias


class ScanResultsDialog(QDialog):

    def __init__(self, found, existing_aliases, ip_to_alias, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plugs found on the network")
        self._found = found

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"Found {len(found)} Tapo device(s). Tick the ones to use; already-"
            "configured plugs (✓) will have their names refreshed.\n"
            "Names are the devices' Tapo nicknames — change them in the Tapo app."
        ))

        self.table = QTableWidget(len(found), 4, self)
        self.table.setHorizontalHeaderLabels(["Use", "Tapo nickname", "Model", "Saved as"])
        self.table.verticalHeader().setVisible(False)
        # Pre-compute the internal filename-safe handle per row (auto-derived,
        # not user-editable). Keep an existing plug's own handle; else slug the
        # nickname. Reserve handles of plugs not shown so we don't collide.
        shown = {ip_to_alias[d["ip"]] for d in found if d["ip"] in ip_to_alias}
        taken = set(existing_aliases) - shown
        self._aliases = []
        for row, d in enumerate(found):
            known = ip_to_alias.get(d["ip"])

            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, check)

            nickname = d["nickname"] or "—"
            nick_item = QTableWidgetItem(nickname + (f"   [{known}]" if known else ""))
            nick_item.setFlags(Qt.ItemIsEnabled)  # read-only
            self.table.setItem(row, 1, nick_item)

            model = d["model"]
            if not model.startswith(ENERGY_MODELS):
                model += "  (no energy meter?)"
            model_item = QTableWidgetItem(model)
            model_item.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 2, model_item)

            base = known or (sanitize_alias(d["nickname"]) if d["nickname"]
                             else "plug" + d["ip"].rsplit(".", 1)[1])
            alias = unique_alias(base, taken)
            taken.add(alias)
            self._aliases.append(alias)
            handle_item = QTableWidgetItem(alias)
            handle_item.setFlags(Qt.ItemIsEnabled)  # read-only
            self.table.setItem(row, 3, handle_item)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selection(self) -> list[tuple]:
        """Ticked rows as [(alias, ip, tapo_name), ...]."""
        accepted = []
        for row, d in enumerate(self._found):
            if self.table.item(row, 0).checkState() != Qt.Checked:
                continue
            accepted.append((self._aliases[row], d["ip"], d.get("nickname") or None))
        return accepted
