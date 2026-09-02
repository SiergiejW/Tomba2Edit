import os
import struct
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from functions import format_detect
from functions.labels import content_key


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
    """What the file at `address` is, read out of its own bytes, and its
    content hash - see functions/labels.py's content_key(). Returns
    (filetype, detail, content_hash).

    Every row in the tree is typed this way, whether the IDX gave it an
    id or not. The ids used to decide it, and they can't: they mean
    different things on different builds - the demo's id 6 is a level
    where retail's id 6 is an animation - so a table of them is a table
    per build, and wrong for any build nobody has written one for. The
    bytes say the same thing on every build.

    The hash comes from the exact same read as the type detection - no
    second pass over the DAT to get it.

    Cached on (address, size): the trail lists the same handful of
    files under nearly every area, so this is asked ~660 times for 53
    distinct blobs. That caching is also the reason a genuinely
    duplicated SDAT entry - the same character's model, baked into
    several areas' own copies at different addresses - still gets
    hashed once per address rather than once per area: this cache is
    keyed on where the bytes are, not on what they are, and finding
    what they are is exactly what the hash is for."""
    if size <= 0:
        return EMPTY_TYPE, (f"0x{address:X}, no bytes of its own\n"
                            "the next entry starts at this same offset"), ""
    key = (address, size)
    if key not in cache:
        dat_file.seek(address)
        data = dat_file.read(size)
        filetype, detail = format_detect.entry_type(dat_file, address, size)
        cache[key] = (filetype, detail, content_key(data))
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
# format_detect - so the bytes can only ever say ANMP, and every one of
# these names describes the same layout. Older labels files hand out
# four or five of them between animations that are not different in any
# way a reader could act on, which made the tree look like it held
# several animation formats. They are all shown as ANMP now, and a
# labels file still carrying an old name is read as meaning that.
_ANIM_FAMILY = frozenset(("ANMP", "TANP", "BETP", "ALFD", "ALFP",
                          "MDAP", "TAND", "MDAD"))


def _relabel_file_item(main_window, child, label_set):
    """Rewrite one file row's text/icon/tooltip from the labels file.
    Returns True if the row has a name (in `label_set` or not is what
    apply_labels()/apply_labels_flat() count as "named" between them).

    Shared by the Indexed View's tree walk and the Data View's flat
    walk - a row means the same thing in both, so relabelling it does
    too."""
    data = child.data(_ROW_LABEL_DATA)
    if not data:
        return False
    stem, filetype, _address, detail, content = data
    label = label_set.get(content) if label_set else None
    name = label.name if label else ""
    shown = filetype
    tooltip = detail
    if label:
        if label.kind and label.kind != filetype:
            if filetype in _ANIM_FAMILY and label.kind in _ANIM_FAMILY:
                shown = "ANMP"
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
    return bool(name)


def apply_labels(main_window):
    """Rewrite every row in the Indexed View from main_window.labels -
    the names someone worked out for this build of the disc (see
    functions/labels.py). Returns how many file rows got a name.

    Separate from building the tree so that loading a different labels
    file is a rename pass over what's already there, rather than a
    reparse that would drop the expanded folders and the edit colouring
    along with it. apply_labels_flat() is the Data View's counterpart -
    kept separate because that view has no AREA folders to rename."""
    model = main_window.tree_view.model()
    if model is None:
        return 0
    label_set = getattr(main_window, "labels", None)
    named = 0

    def relabel_file(child):
        nonlocal named
        named += _relabel_file_item(main_window, child, label_set)

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


def apply_labels_flat(main_window):
    """apply_labels()'s counterpart for the Data View: the same rename
    pass, over a model that is one flat list of file rows with no AREA
    folders to also rename."""
    model = getattr(main_window, "dat_view_model", None)
    if model is None:
        return 0
    label_set = getattr(main_window, "labels", None)
    named = 0
    root = model.invisibleRootItem()
    for row in range(root.rowCount()):
        named += _relabel_file_item(main_window, root.child(row), label_set)
    return named


def build_dat_view(main_window):
    """One row per distinct FILE in TOMBA2.DAT, in first-seen-address
    order - what the disc actually holds, as opposed to the Indexed
    View's one row per area that reaches one. "Distinct" is by content,
    not address: an area's own SDAT can carry its own full copy of a
    character it reuses, at its own address, so two (or twenty-two)
    rows in the Indexed View can be the exact same file - see
    functions/labels.py's module docstring. Built from the Indexed
    View's own tree rather than by re-reading the IDX: parse_idx_file()
    has already typed, hashed and named every entry once, so this is a
    sort and a group-by, not a second parse.

    Returns the populated model; main_window.dat_view_model is also set,
    since apply_labels_flat() and the edit-highlighting in MainWindow
    both need to find it again later."""
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(["Name"])
    root = model.invisibleRootItem()

    # Sort by each file's first-seen address, not by its content hash -
    # a hash has no meaningful order, and address order is what the
    # Indexed View itself reads in.
    by_first_address = sorted(
        main_window.content_item.items(),
        key=lambda pair: pair[1].data(_ROW_LABEL_DATA)[2])

    # content hash -> this model's item for it, so an address can be
    # resolved to its row via address_content below.
    item_for_content = {}
    for content, source in by_first_address:
        data = source.data(_ROW_LABEL_DATA)
        if not data:
            continue                      # an EMPTY slot - nothing to list
        stem, filetype, _address, detail, _content = data
        item = QStandardItem(source.icon(), f"{stem}.{filetype}")
        item.setData(data, _ROW_LABEL_DATA)
        item.setData(source.data(Qt.ItemDataRole.UserRole), Qt.ItemDataRole.UserRole)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setToolTip(detail)
        root.appendRow(item)
        item_for_content[content] = item

    # Every address that shares a file's content maps to that file's one
    # row, not just the address that happened to be first - an edit made
    # through any one of them still has to be able to find where to show
    # it (see MainWindow._set_txtd_tree_item_state).
    main_window.dat_view_lookup = {
        address: item_for_content[content]
        for address, content in main_window.address_content.items()
        if content in item_for_content
    }

    main_window.dat_view_model = model
    return model


def content_hashes(main_window):
    """Every distinct file's content hash on this disc, for scoring a
    labels file against it - see functions/labels.py's LabelSet.score().
    Built from the tree's own per-row hashes rather than a second pass
    over the DAT."""
    return set(main_window.content_item)


def row_label_data(item):
    """(stem, type, address, tooltip, content hash) for a file row, or
    None if this item isn't one (a folder, a VRAM row). The content hash
    is what a rename actually keys on - see functions/labels.py."""
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
            label = label_set.get(data[4])
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

    # DAT address -> {chunk_index, ...} for every kind of entry, not just
    # TXTD/TXT2 - the same "one DAT file, many areas" fact applies to
    # SMST/ANMP just as much, and there it's what tells an SMST or a bone
    # tree candidate apart from an unrelated one that happens to be the
    # right size: whichever candidate this address is actually used from
    # the area being viewed is far more likely to be the right one than
    # whichever the tree walk happens to reach first. See
    # MainWindow._smst_candidates and gui/anmp/game_rest.py.
    main_window.area_membership = {}

    # DAT address -> the first QStandardItem the parse reaches for it, of
    # any type - what the Data View tab is built from (one row per
    # address instead of one per area that reaches it) and how a row
    # there navigates back to a real, previewable spot in the Indexed
    # View. Kept to the first rather than every occurrence: navigation
    # only needs somewhere to land, not the complete list.
    main_window.address_item = {}

    # DAT address -> its own content hash, and content hash -> the first
    # item with it - what actually names a file now (see
    # functions/labels.py's module docstring on why an address can't):
    # a level's own SDAT gets its own full copy of a character it
    # reuses, so the same asset sits at a different address in every
    # area that has it, and only the hash says two rows are the same
    # file. address_content is every address, even the ones that turn
    # out to duplicate another; content_item is one representative row
    # per distinct file - what the Data View is really listing.
    main_window.address_content = {}
    main_window.content_item = {}

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
                filetype, detail, content = _entry_type(
                    DAT, dat_start + offset, size, entry_types)
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
                        (stem, filetype, dat_start + offset,
                         f"id {id}, {detail}", content),
                        _ROW_LABEL_DATA)
                    file_item.setFlags(file_item.flags() | Qt.ItemFlag.ItemIsEditable)
                    file_item.setIcon(_type_icon(main_window, filetype, file_icon))
                    main_window.area_membership.setdefault(
                        dat_start + offset, set()).add(chunk_index)
                    main_window.address_item.setdefault(
                        dat_start + offset, file_item)
                    main_window.address_content[dat_start + offset] = content
                    main_window.content_item.setdefault(content, file_item)
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
                filetype, tooltip, content = _entry_type(DAT, adr, sz, entry_types)
                stem = f"{adr:04X}-{end:04X}"
                trail_file_item = QStandardItem(
                    _type_icon(main_window, filetype, file_icon),
                    f"{stem}.{filetype}")
                trail_file_item.setFlags(trail_file_item.flags() | Qt.ItemFlag.ItemIsEditable)
                trail_file_item.setData(("trail", adr, end - adr, dat_start), Qt.ItemDataRole.UserRole)
                trail_file_item.setData((chunk_index, i), Qt.ItemDataRole.UserRole + 2)  # ✅ NEW for trail files
                trail_file_item.setData((stem, filetype, adr, tooltip, content), _ROW_LABEL_DATA)
                trail_file_item.setToolTip(tooltip)
                main_window.area_membership.setdefault(adr, set()).add(chunk_index)
                main_window.address_item.setdefault(adr, trail_file_item)
                main_window.address_content[adr] = content
                main_window.content_item.setdefault(content, trail_file_item)
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

    # One row per address, built from the tree just finished above -
    # see its own docstring for why this is a sort rather than a
    # second parse.
    build_dat_view(main_window)
    main_window.dat_view.setModel(main_window.dat_view_model)

    # Names last, over the finished tree - see apply_labels above.
    main_window.load_labels_for_disc(idx_path)