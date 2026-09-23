"""The file tree down the left, and its context menu.
"""
from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtWidgets import QAbstractItemView, QMenu
from formats.archive.idx_parser import LabelNameDelegate, row_label_data


class TreeMixin:
    """The file tree down the left, and its context menu.

    Mixed into MainWindow; every method here reaches the rest of
    the window through self.
    """

    def _filter_tree(self, text):
        """Hide every row whose own text doesn't contain `text`, folders
        included whenever something under them matches.

        Matches by plain substring against the row's displayed text,
        case-insensitive - which is deliberately the same text a rename
        writes, offset and all ("82F000-832F2C Tomba model.SMST" keeps
        its address after being named), so a search for either a name or
        a raw offset works today and keeps working after a rename,
        without needing two different matching rules.

        Rows are hidden in place with setRowHidden rather than through a
        filter proxy model: several other places read tree_view.model()
        expecting the real QStandardItemModel and its own row/column
        indices directly (_smst_candidates, on_tree_selection_changed,
        the labels code...), and a proxy would put a second index space
        between them and it for no benefit this search needs."""
        needle = text.strip().lower()
        model = self.tree_view.model()
        if model is None:
            return
        root = model.invisibleRootItem()

        def visit(item, parent_index, ancestor_matched):
            any_visible = False
            for row in range(item.rowCount()):
                child = item.child(row)
                child_index = model.index(row, 0, parent_index)
                self_matched = ancestor_matched or not needle or \
                    needle in child.text().lower()
                if child.hasChildren():
                    # A matching folder (an area, or an id-named group
                    # like AREA_0C's own name) shows everything under
                    # it, same as a matching leaf shows its own row.
                    matched = visit(child, child_index, self_matched) or self_matched
                else:
                    matched = self_matched
                self.tree_view.setRowHidden(row, parent_index, not matched)
                any_visible = any_visible or matched
            return any_visible

        visit(root, self.tree_view.rootIndex(), False)
        if needle:
            self.tree_view.expandAll()

    def _filter_dat_view(self, text):
        """The Data View's search: same matching rule as _filter_tree,
        simpler to apply since it has no folders - every row is a
        top-level one."""
        needle = text.strip().lower()
        model = self.dat_view.model()
        if model is None:
            return
        root = model.invisibleRootItem()
        for row in range(root.rowCount()):
            matched = not needle or needle in root.child(row).text().lower()
            self.dat_view.setRowHidden(row, self.dat_view.rootIndex(), not matched)

    def _open_from_dat_view(self, index):
        """A Data View row was double-clicked: show that same file in
        the Indexed View, which is what actually knows how to preview
        and edit each type - the Data View is a way to find a file by
        address, not a second copy of the viewer machinery."""
        item = self.dat_view.model().itemFromIndex(index)
        data = row_label_data(item) if item else None
        if not data:
            return
        address = data[2]
        target = self.address_item.get(address)
        tree_model = self.tree_view.model()
        if target is None or tree_model is None:
            return

        target_index = tree_model.indexFromItem(target)
        if not target_index.isValid():
            return

        self.tree_search.clear()      # fires _filter_tree("") - unhides everything
        walked = target_index.parent()
        while walked.isValid():
            self.tree_view.expand(walked)
            walked = walked.parent()

        self.main_tabs.setCurrentIndex(0)
        self.tree_view.selectionModel().setCurrentIndex(
            target_index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows)
        self.tree_view.scrollTo(target_index)

    def count_items(self, item):
        count = 0
        for i in range(item.rowCount()):
            child = item.child(i)
            if child.hasChildren():
                count += self.count_items(child)
            else:
                count += 1
        return count

    def update_folder_name(self, item):
        if item.hasChildren():
            count = self.count_items(item)
            item.setText(f"{item.text()} ({count})")

    def tuplify(self, item):
        dat_id = item >> 24
        dat_ptr = item & 0x00FFFFFF
        return (dat_id, dat_ptr)

    def setup_tree_view(self):
        self.tree_view.setModel(QStandardItemModel())
        self.tree_view.setHeaderHidden(False)
        # Renaming a row edits only the name part of it - the address and
        # the type aren't anyone's to change (see LabelNameDelegate).
        self.tree_view.setItemDelegate(LabelNameDelegate(self.rename_row, self))
        # F2 only. Qt's default triggers include DoubleClicked and
        # SelectedClicked, and this tree is browsed by clicking - double
        # click is how a folder opens and how a file gets looked at, so
        # either of those would put a text box where the user wanted the
        # thing they clicked on. Right-click offers Rename as well, since
        # a keyboard shortcut on its own is undiscoverable.
        self.tree_view.setEditTriggers(
            QAbstractItemView.EditTrigger.EditKeyPressed)
        self.install_file_menu(self.tree_view)

    def install_file_menu(self, view):
        """Give a view the right-click menu for a file row.

        Both views get the same one. They are two ways of listing the
        same disc - one row per area that reaches a file, or one row per
        file - and a file's bytes are the file's bytes whichever way you
        arrived at them."""
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        view.customContextMenuRequested.connect(
            lambda position, which=view: self._file_context_menu(which, position))

    def _file_context_menu(self, view, position):
        """Rename, export, replace and swap, for whatever was clicked."""
        index = view.indexAt(position)
        if not index.isValid():
            return
        item = view.model().itemFromIndex(index)
        if item is None:
            return

        menu = QMenu(self)
        renameable = bool(item.flags() & Qt.ItemFlag.ItemIsEditable)
        rename = menu.addAction("Rename\tF2")
        rename.setEnabled(renameable)
        rename.triggered.connect(lambda _checked=False: view.edit(index))
        if not renameable:
            rename.setToolTip(
                "Only AREA folders and the files in them carry names")

        if item.data(Qt.ItemDataRole.UserRole):
            menu.addSeparator()
            export = menu.addAction("Export File...")
            export.triggered.connect(
                lambda _checked=False: self.export_selected_bytes(item))
            entry = self._entry_of(item)
            if entry is not None:
                replace = menu.addAction("Replace File...")
                replace.setToolTip(
                    "Put the bytes of a file from disk here. Staged like a "
                    "text edit - exporting rebuilds the DAT and IDX around "
                    "whatever size it is.")
                replace.triggered.connect(
                    lambda _checked=False: self.import_selected_bytes(item))
                swap = menu.addAction("Swap With...")
                swap.setToolTip(
                    "Put another entry of the same type here - one model "
                    "where another was.")
                swap.triggered.connect(
                    lambda _checked=False: self.swap_selected_bytes(item))
                if entry["key"] in self.pending_file_edits:
                    revert = menu.addAction("Undo Replacement")
                    revert.triggered.connect(
                        lambda _checked=False: self._revert_file_edit(item))

        menu.exec(view.viewport().mapToGlobal(position))
