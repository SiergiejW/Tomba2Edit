import os
import struct
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QStyle

from functions import format_detect


def _trail_type(dat_path, address, size, cache):
    """What the trail file at `address` is, read out of its own bytes -
    the trailer gives no type id, so this is the only thing there is to
    go on. Returns (type, tooltip); the type is "bin" when nothing
    reads, which on the retail disc never happens (all 53 come back
    SMST or MDAT, and all 53 agree with TOMBAMAP_us.txt).

    The same blob is listed by most of the 48 areas, so the answer is
    cached on address and size rather than worked out ~660 times."""
    key = (address, size)
    if key in cache:
        return cache[key]
    try:
        matches = format_detect.identify_at(dat_path, address, size)
    except OSError as e:
        matches = []
        print(f"Could not read trail file at 0x{address:X}: {e}")
    if matches:
        tooltip = "\n".join(str(m) for m in matches)
        result = (matches[0].kind, f"0x{address:X}, {size} bytes\n{tooltip}")
    else:
        result = ("bin", f"0x{address:X}, {size} bytes\nreads as no known format")
    cache[key] = result
    return result


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
    }
    return icons.get(filetype, default)


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

    # (address, size) -> (type, tooltip) for the trail files, which most
    # of the 48 areas list the same copies of.
    trail_types = {}

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
        area_item.setFlags(area_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
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
                filetype = main_window.id_convert(DAT, id, hex(dat_start + offset))
                file_item = QStandardItem(file_icon, f"{id}-{offset:04X}.{filetype}")

                # ✅ Store basic data
                file_item.setData((id, dat_start, offset, size), Qt.ItemDataRole.UserRole)
                # ✅ Store AREA and file index
                file_item.setData((chunk_index, i), Qt.ItemDataRole.UserRole + 2)

                file_item.setFlags(file_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                file_item.setIcon(_type_icon(main_window, filetype, file_icon))
                if filetype in ("TXTD", "TXT1", "TXT2"):
                    # TXT1 and TXT2 are the same layout under different
                    # SDAT ids - separate labels, one viewer.
                    file_path = f"{dat_start + offset:08X}.{filetype.lower()}"
                    file_item.setData(file_path, Qt.ItemDataRole.UserRole + 1)
                    main_window.txtd_item_lookup[(chunk_index, i)] = file_item

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
                filetype, tooltip = _trail_type(dat_path, adr, sz, trail_types)
                trail_file_item = QStandardItem(
                    _type_icon(main_window, filetype, file_icon),
                    f"{adr:04X}-{end:04X}.{filetype}")
                trail_file_item.setFlags(trail_file_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                trail_file_item.setData(("trail", adr, end - adr, dat_start), Qt.ItemDataRole.UserRole)
                trail_file_item.setData((chunk_index, i), Qt.ItemDataRole.UserRole + 2)  # ✅ NEW for trail files
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