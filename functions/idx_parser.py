import os
import struct
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from functions import format_detect


# A slot the IDX names but gives no bytes to: its offset is the same as
# the next entry's, so its size works out at zero. Nine of them on the
# retail disc - AREA_0C and AREA_22 one each, AREA_1B two, AREA_20 five
# - and they are why an area can seem to list the same file two or three
# times over. Without this they would each be typed, and named, from the
# blob that starts where they do, which belongs to the entry after them.
EMPTY_TYPE = "EMPTY"

# What an empty slot's row is drawn in, so it reads as structure rather
# than as a file. Fixed rather than from the palette: it has to stay
# quieter than ordinary rows under either theme.
EMPTY_ROW_COLOR = "#8a8a8a"


def _entry_type(dat_file, address, size, cache):
    """What the file at `address` is, read out of its own bytes.

    Every row in the tree is typed this way, whether the IDX gave it an
    id or not. The ids used to decide it, and they can't: they mean
    different things on different builds - the demo's id 6 is a level
    where retail's id 6 is an animation - so a table of them is a table
    per build, and wrong for any build nobody has written one for. The
    bytes say the same thing on every build.

    Cached on (address, size): the trail lists the same handful of
    files under nearly every area, so this is asked ~660 times for 53
    distinct blobs."""
    if size <= 0:
        return EMPTY_TYPE, (f"0x{address:X}, no bytes of its own\n"
                            "the next entry starts at this same offset")
    key = (address, size)
    if key not in cache:
        cache[key] = format_detect.entry_type(dat_file, address, size)
    return cache[key]


def _type_icon(main_window, filetype, default):
    """The tree icon for a file type, whether the type came from the
    IDX's id or was read out of the bytes."""
    icons = {
        "SPRT": main_window.sprt_icon,
        "TXTD": main_window.txtd_icon,
        "TXT1": main_window.txt2_icon,
        "TXT2": main_window.txt2_icon,
        "TANP": main_window.tanp_icon,
        "ANMP": main_window.tanp_icon,
        "SMST": main_window.smst_icon,
        "SCLD": main_window.scld_icon,
        "MDAT": main_window.mdat_icon,
        "DRWB": main_window.drwb_icon,
        "BGMP": main_window.bgmp_icon,
        "BETP": main_window.betp_icon,
        "ALFD": main_window.alfd_icon,
        # Names a labels file may use for the same animation container.
        "ALFP": main_window.alfd_icon,
        "MDAP": main_window.tanp_icon,
        "SPRP": main_window.sprt_icon,
    }
    return icons.get(filetype, default)


# Everything a file row needs to be re-labelled without rebuilding the
# tree: (stem, type, absolute DAT address, the row's own tooltip). Kept
# in UserRole + 3 so apply_labels() can rewrite the text from the stem
# each time rather than trying to strip the last name back off it.
_ROW_LABEL_DATA = Qt.ItemDataRole.UserRole + 3


# TANP, BETP, ALFD and the map's ALFP/MDAP are one container - see
# format_detect - so the bytes can only ever say ANMP. Which of the
# names a build uses for a given file is knowledge, not structure, so a
# labels file is allowed to say, and the row follows it.
_ANIM_FAMILY = frozenset(("ANMP", "TANP", "BETP", "ALFD", "ALFP", "MDAP"))


def apply_labels(main_window):
    """Rewrite every row in the tree from main_window.labels - the names
    someone worked out for this build of the disc (see
    functions/labels.py). Returns how many file rows got a name.

    Separate from building the tree so that loading a different labels
    file is a rename pass over what's already there, rather than a
    reparse that would drop the expanded folders and the edit colouring
    along with it."""
    model = main_window.tree_view.model()
    if model is None:
        return 0
    label_set = getattr(main_window, "labels", None)
    named = 0

    def relabel_file(child):
        nonlocal named
        data = child.data(_ROW_LABEL_DATA)
        if not data:
            return
        stem, filetype, address, detail = data
        label = label_set.get(address) if label_set else None
        name = label.name if label else ""
        shown = filetype
        tooltip = detail
        if label:
            named += bool(name)
            if label.kind and label.kind != filetype:
                if filetype in _ANIM_FAMILY and label.kind in _ANIM_FAMILY:
                    shown = label.kind
                else:
                    # Elsewhere the hand-written type is only a note. It
                    # is wrong twice on the retail disc, and the type on
                    # the row is the one the tool read out of the bytes.
                    tooltip += f"\nlabels file calls this {label.kind}"
        elif label_set:
            tooltip += "\nnot in the labels file"
        child.setText(f"{stem} {name}.{shown}" if name else f"{stem}.{shown}")
        child.setIcon(_type_icon(main_window, shown, child.icon()))
        child.setToolTip(tooltip)

    def relabel_area(area_item):
        """An AREA folder takes the name of the level inside it - which
        is its MDAT's name, so the folder says what the room is without
        anyone having to write the area numbers down separately."""
        index = _chunk_index_of(area_item)
        if index is None:
            return
        name = label_set.area_name(index) if label_set else ""
        if not name and label_set:
            name = _area_name_from_mdat(area_item, label_set)
        count = main_window.count_items(area_item)
        area_item.setText(f"AREA_{index:02X}" + (f" {name}" if name else "")
                          + (f" ({count})" if count else ""))

    def walk(item, depth=0):
        for row in range(item.rowCount()):
            child = item.child(row)
            if child.hasChildren() or depth == 0:
                if depth == 0:
                    relabel_area(child)
                walk(child, depth + 1)
            else:
                relabel_file(child)

    walk(model.invisibleRootItem())
    return named


def row_label_data(item):
    """(stem, type, address, tooltip) for a file row, or None if this
    item isn't one (a folder, a VRAM row)."""
    return item.data(_ROW_LABEL_DATA)


def area_index_of(item):
    """The AREA number this item is, or None if it isn't an AREA
    folder. Public counterpart of _chunk_index_of."""
    return _chunk_index_of(item) if item.parent() is None else None


class LabelNameDelegate(QStyledItemDelegate):
    """Renaming in the tree edits the NAME and nothing else.

    A row reads "8-1B724 Town of the Fishermen.MDAT", but only the
    middle of that is anybody's to change: the id and offset are where
    the file is, and the type is what the bytes say it is. So the editor
    opens on the name alone and hands the name alone back, and the row
    is rebuilt around it. `renamed(item, text)` is called with whatever
    was typed - it is up to the caller to put it somewhere."""

    def __init__(self, renamed, parent=None):
        super().__init__(parent)
        self._renamed = renamed

    @staticmethod
    def _current_name(item):
        data = row_label_data(item)
        if data:
            stem = data[0]
            # Split off the type from the right - the row may be showing
            # a name the labels file gave it rather than data[1].
            text = item.text().rsplit(".", 1)[0]
            if text.startswith(stem):
                text = text[len(stem):]
            return text.strip()
        index = area_index_of(item)
        if index is not None:
            text = item.text()[len("AREA_XX"):]
            return text.rsplit(" (", 1)[0].strip() if " (" in text else text.strip()
        return item.text()

    def setEditorData(self, editor, index):
        item = index.model().itemFromIndex(index)
        editor.setText(self._current_name(item) if item else "")

    def setModelData(self, editor, model, index):
        item = model.itemFromIndex(index)
        if item is not None:
            self._renamed(item, editor.text())


def _chunk_index_of(area_item):
    """The number out of an "AREA_04 Something (41)" folder label."""
    text = area_item.text()
    if not text.startswith("AREA_"):
        return None
    try:
        return int(text.split("_", 1)[1][:2], 16)
    except ValueError:
        return None


def _area_name_from_mdat(area_item, label_set):
    """The name of the first named MDAT under this area - the level the
    area holds. Areas that are nothing but a trail listing, or whose
    level nobody has named, come back empty and keep their number."""
    for row in range(area_item.rowCount()):
        folder = area_item.child(row)
        for k in range(folder.rowCount()):
            data = folder.child(k).data(_ROW_LABEL_DATA)
            if not data or data[1] != "MDAT":
                continue
            label = label_set.get(data[2])
            if label and label.name:
                return label.name
    return ""


def parse_idx_file(main_window, cd_folder):
    idx_path = os.path.join(cd_folder, "TOMBA2.IDX")
    dat_path = os.path.join(cd_folder, "TOMBA2.DAT")
    img_path = os.path.join(cd_folder, "TOMBA2.IMG")

    IDX = open(idx_path, "rb")
    DAT = open(dat_path, "rb")
    IMG = open(img_path, "rb")

    main_window.dat_file = dat_path

    chunk_size = 0x800
    trailer = 0x700

    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["Name"])
    root_item = model.invisibleRootItem()

    # (chunk_index, file_index) -> the QStandardItem for that TXTD/TXT2
    # file, so TXTDViewer/TXT2Viewer edits can be reflected here too (see
    # MainWindow._set_txtd_tree_item_state). Both file types share this
    # one dict - (chunk_index, file_index) is unique per SDAT slot
    # regardless of type, since `i` below comes from the same enumerated
    # sdat_pointers list either way. Reset on every (re)parse.
    main_window.txtd_item_lookup = {}

    # DAT address -> [(chunk_index, file_index), ...] for every TXTD/TXT2
    # location that reaches it. The DAT holds one copy of a shared asset;
    # an area's tree entry is a pointer to it, not the file itself, so
    # the same address can show up under several AREA_NN folders. This
    # is what lets an edit under one area be recognised as editing the
    # same file everywhere else it's used - see MainWindow.
    # on_txtd_content_changed and _set_txtd_tree_item_state.
    main_window.address_locations = {}

    # (address, size) -> (type, tooltip) for every file, so the trail's
    # repeated copies are only read once.
    entry_types = {}

    folder_icon = main_window.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
    file_icon = main_window.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

    for chunk_index in range(int(os.path.getsize(idx_path) / chunk_size)):
        IDX.seek(chunk_index * chunk_size)
        img_start, img_end, dat_start, dat_end, pointer_amount = struct.unpack("<5I", IDX.read(20))

        IMG.seek(img_start)
        imgdata = IMG.read(img_end - img_start)

        DAT.seek(dat_start)
        datdata = DAT.read(dat_end - dat_start)

        sdat_pointers = [main_window.tuplify(item) for item in
                         struct.unpack("<{:d}I".format(pointer_amount), IDX.read(pointer_amount * 4))]

        IDX.seek(chunk_index * chunk_size + (chunk_size - trailer))
        traildata = struct.unpack("<{:d}I".format(trailer >> 2), IDX.read(trailer))
        trail_list = []
        for t in range(0, len(traildata), 2):
            dat_trail_start, dat_trail_end = traildata[t], traildata[t + 1]
            dat_trail_size = dat_trail_end - dat_trail_start
            if dat_trail_size != 0:
                trail_list.append((dat_trail_start, dat_trail_end, dat_trail_size))

        area_item = QStandardItem(folder_icon, f"AREA_{chunk_index:02X}")
        # The only folder anyone may rename - the NN_DATA / NN_VRAM /
        # NN_TRAIL ones below are structure, not names.
        area_item.setFlags(area_item.flags() | Qt.ItemFlag.ItemIsEditable)
        root_item.appendRow(area_item)

        if datdata:
            sdat_item = QStandardItem(folder_icon, f"{chunk_index:02X}_DATA")
            sdat_item.setFlags(sdat_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            area_item.appendRow(sdat_item)
            for i in range(len(sdat_pointers)):
                id, offset = sdat_pointers[i]
                if i < len(sdat_pointers) - 1:
                    next_offset = sdat_pointers[i + 1][1]
                else:
                    next_offset = dat_end - dat_start

                size = next_offset - offset
                filetype, detail = _entry_type(DAT, dat_start + offset, size,
                                               entry_types)
                stem = f"{id}-{offset:04X}"
                file_item = QStandardItem(file_icon, f"{stem}.{filetype}")

                # ✅ Store basic data
                file_item.setData((id, dat_start, offset, size), Qt.ItemDataRole.UserRole)
                # ✅ Store AREA and file index
                file_item.setData((chunk_index, i), Qt.ItemDataRole.UserRole + 2)

                if filetype == EMPTY_TYPE:
                    # An empty slot shares its address with the entry
                    # after it, so leaving the label data off is what
                    # keeps apply_labels away from it - otherwise it
                    # takes that file's name and the area looks like it
                    # holds the same thing twice. Nothing to rename
                    # either, so the row isn't editable.
                    file_item.setFlags(file_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    file_item.setForeground(QBrush(QColor(EMPTY_ROW_COLOR)))
                    file_item.setToolTip(f"id {id}, {detail}")
                else:
                    file_item.setData(
                        (stem, filetype, dat_start + offset, f"id {id}, {detail}"),
                        _ROW_LABEL_DATA)
                    file_item.setFlags(file_item.flags() | Qt.ItemFlag.ItemIsEditable)
                    file_item.setIcon(_type_icon(main_window, filetype, file_icon))
                    if filetype in ("TXTD", "TXT1", "TXT2"):
                        # TXT1 and TXT2 are the same layout under different
                        # SDAT ids - separate labels, one viewer.
                        file_path = f"{dat_start + offset:08X}.{filetype.lower()}"
                        file_item.setData(file_path, Qt.ItemDataRole.UserRole + 1)
                        main_window.txtd_item_lookup[(chunk_index, i)] = file_item
                        main_window.address_locations.setdefault(
                            dat_start + offset, []).append((chunk_index, i))

                sdat_item.appendRow(file_item)

        if imgdata:
            vram_item = QStandardItem(folder_icon, f"{chunk_index:02X}_VRAM")
            vram_item.setFlags(vram_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            area_item.appendRow(vram_item)

            vram_c_item = QStandardItem(main_window.cvram_icon, f"{chunk_index:02X}.CVRAM")
            vram_c_item.setFlags(vram_c_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            vram_c_item.setData(("vram_compressed", img_start, img_end - img_start, img_path), Qt.ItemDataRole.UserRole)
            vram_item.appendRow(vram_c_item)

            vram_u_item = QStandardItem(main_window.vram_icon, f"{chunk_index:02X}.VRAM")
            vram_u_item.setFlags(vram_u_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            vram_u_item.setData(("vram_uncompressed", chunk_index), Qt.ItemDataRole.UserRole)
            vram_item.appendRow(vram_u_item)

        if traildata:
            trail_item = QStandardItem(folder_icon, f"{chunk_index:02X}_TRAIL")
            trail_item.setFlags(trail_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            area_item.appendRow(trail_item)
            for i in range(len(trail_list)):
                adr, end, sz = trail_list[i]
                # The trailer carries no type id, so the type is read out
                # of the blob itself (see _trail_type above).
                filetype, tooltip = _entry_type(DAT, adr, sz, entry_types)
                stem = f"{adr:04X}-{end:04X}"
                trail_file_item = QStandardItem(
                    _type_icon(main_window, filetype, file_icon),
                    f"{stem}.{filetype}")
                trail_file_item.setFlags(trail_file_item.flags() | Qt.ItemFlag.ItemIsEditable)
                trail_file_item.setData(("trail", adr, end - adr, dat_start), Qt.ItemDataRole.UserRole)
                trail_file_item.setData((chunk_index, i), Qt.ItemDataRole.UserRole + 2)  # ✅ NEW for trail files
                trail_file_item.setData((stem, filetype, adr, tooltip), _ROW_LABEL_DATA)
                trail_file_item.setToolTip(tooltip)
                trail_item.appendRow(trail_file_item)

        main_window.update_folder_name(area_item)
        if datdata:
            main_window.update_folder_name(sdat_item)
        if imgdata:
            main_window.update_folder_name(vram_item)
        if traildata:
            main_window.update_folder_name(trail_item)

    main_window.tree_view.setModel(model)
    main_window.tree_view.selectionModel().selectionChanged.connect(main_window.on_tree_selection_changed)

    # Names last, over the finished tree - see apply_labels above.
    main_window.load_labels_for_disc(idx_path)