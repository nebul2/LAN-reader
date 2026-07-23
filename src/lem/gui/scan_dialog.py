"""Dialog presenting scan results: accept/refuse each plug, edit its alias,
and choose add-vs-replace when the config already has tapo plugs."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QLineEdit, QRadioButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from lem.scan import ENERGY_MODELS, sanitize_alias, unique_alias


class ScanResultsDialog(QDialog):

    def __init__(self, found, existing_aliases, tapo_aliases, ip_to_alias, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plugs found on the network")
        self._found = found
        self._tapo_aliases = tapo_aliases
        self._ip_to_alias = ip_to_alias
        self._non_tapo_aliases = set(existing_aliases) - set(tapo_aliases)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"Found {len(found)} Tapo device(s). Tick the ones to use and give each a name."
        ))

        self.radio_add = self.radio_replace = None
        if tapo_aliases:
            self.radio_add = QRadioButton(
                f"Add to the {len(tapo_aliases)} plug(s) already configured"
            )
            self.radio_replace = QRadioButton("Replace the configured plugs with this selection")
            self.radio_add.setChecked(True)
            layout.addWidget(self.radio_add)
            layout.addWidget(self.radio_replace)
            self.radio_add.toggled.connect(self._mode_changed)

        self.table = QTableWidget(len(found), 4, self)
        self.table.setHorizontalHeaderLabels(["Use", "Device", "Model", "Name"])
        self.table.verticalHeader().setVisible(False)
        self._alias_edits = []
        suggested = set(existing_aliases)
        for row, d in enumerate(found):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, check)

            label = d["ip"] + (f'  "{d["nickname"]}"' if d["nickname"] else "")
            self.table.setItem(row, 1, QTableWidgetItem(label))
            model = d["model"]
            if not model.startswith(ENERGY_MODELS):
                model += "  (no energy meter?)"
            self.table.setItem(row, 2, QTableWidgetItem(model))

            base = sanitize_alias(d["nickname"]) if d["nickname"] else "plug" + d["ip"].rsplit(".", 1)[1]
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

        self._mode_changed()

    def mode(self) -> str:
        return "replace" if self.radio_replace and self.radio_replace.isChecked() else "add"

    def _mode_changed(self, *_):
        """In add mode, plugs whose IP is already configured are locked out."""
        add_mode = self.mode() == "add"
        for row, d in enumerate(self._found):
            known = d["ip"] in self._ip_to_alias
            item = self.table.item(row, 0)
            if add_mode and known:
                item.setCheckState(Qt.Unchecked)
                item.setFlags(Qt.ItemIsUserCheckable)  # disabled
                self.table.item(row, 1).setText(
                    f'{d["ip"]}  (already configured as "{self._ip_to_alias[d["ip"]]}")'
                )
            else:
                item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                if known:
                    item.setCheckState(Qt.Checked)
                    label = d["ip"] + (f'  "{d["nickname"]}"' if d["nickname"] else "")
                    self.table.item(row, 1).setText(label)

    def selection(self) -> tuple[str, list[tuple[str, str]]]:
        """Returns (mode, [(alias, ip), ...]) with aliases sanitized and unique."""
        mode = self.mode()
        taken = set(self._non_tapo_aliases)
        if mode == "add":
            taken |= set(self._tapo_aliases)
        accepted = []
        for row, d in enumerate(self._found):
            if self.table.item(row, 0).checkState() != Qt.Checked:
                continue
            base = sanitize_alias(self._alias_edits[row].text())
            alias = unique_alias(base, taken)
            taken.add(alias)
            accepted.append((alias, d["ip"]))
        return mode, accepted
