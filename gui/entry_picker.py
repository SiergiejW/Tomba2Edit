"""Pick one file out of the disc, by typing at it.

A list of every entry of one type is hundreds of rows long - the disc
holds 316 SMSTs - so the dialog that offers them has the same search
box the tree does, and matches the same way: plain substring against
the row's own displayed text, which carries both the name and the
offset ("18-4268 Charles Model.SMST"). So a search for a name or for a
raw address both work, and keep working after a rename.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QVBoxLayout,
)

ROLE = Qt.ItemDataRole.UserRole


class EntryPicker(QDialog):
    """A searchable list of (label, value) pairs, one to be chosen."""

    def __init__(self, title, prompt, entries, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 480)
        self._chosen = None

        self.search = QLineEdit(self)
        self.search.setPlaceholderText(
            "Search by name or offset (e.g. Tomba, 55F54, 19-11Da4)...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)

        self.list = QListWidget(self)
        for label, value in entries:
            item = QListWidgetItem(label, self.list)
            item.setData(ROLE, value)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        if self.list.count():
            self.list.setCurrentRow(0)

        self.count = QLabel(self)
        self._update_count()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(prompt, self))
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.count)
        layout.addWidget(buttons)
        # Typing goes to the search box; the list is arrowed into from
        # there, which is what makes this a search rather than a list
        # with a box above it.
        self.search.setFocus()
        self.search.installEventFilter(self)

    def eventFilter(self, watched, event):
        """Down from the search box steps into the list, and Return in
        it takes the row the search has left highlighted."""
        if watched is self.search and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self.list.setFocus()
                return True
        return super().eventFilter(watched, event)

    def _filter(self, text):
        needle = text.strip().lower()
        first = None
        for row in range(self.list.count()):
            item = self.list.item(row)
            hidden = bool(needle) and needle not in item.text().lower()
            item.setHidden(hidden)
            if not hidden and first is None:
                first = row
        if first is not None and (self.list.currentRow() < 0
                                  or self.list.currentItem().isHidden()):
            self.list.setCurrentRow(first)
        self._update_count()

    def _update_count(self):
        shown = sum(1 for row in range(self.list.count())
                    if not self.list.item(row).isHidden())
        self.count.setText(f"{shown} of {self.list.count()} shown")

    def chosen(self):
        """The selected value, or None."""
        item = self.list.currentItem()
        if item is None or item.isHidden():
            return None
        return item.data(ROLE)

    @classmethod
    def ask(cls, parent, title, prompt, entries):
        """Show the dialog and return what was picked, or None."""
        dialog = cls(title, prompt, entries, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.chosen()
