"""Editing one sprite piece: paint it, or replace it from a PNG.

A piece is the editable unit rather than the whole sprite, and that is a
decision rather than a shortcut. A sprite is several quads pinned at
their own offsets, drawn back to front, sometimes overlapping and
sometimes mirrored; painting the composed picture would leave nowhere
sensible to put a texel two pieces both cover. A piece is one rectangle,
in one page, through one palette - so a click on it means exactly one
texel, and a PNG imported over it maps one to one.

Whole-sprite PNG export lives in the viewer beside this and is for
looking at, not for importing back.

Saving goes the same way a migrated texture does: the changed halfwords
become IMG shards and functions/img_writer.py appends them to the area's
chunk. Nothing is written until Save, and the tool's own VRAM is updated
with it so the sprite redraws immediately.
"""
import os

from PIL import Image
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QColorDialog, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from gui.pixel_canvas import PaintCanvas, fit_zoom
from gui.sprt import sprt_edit

# The palette strip's swatches.
SWATCH = 22


class PaletteStrip(QWidget):
    """The piece's colours, one clickable square each."""

    chosen = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.colours = []
        self.index = 1
        self.setFixedHeight(SWATCH + 18)

    def set_palette(self, colours, keep=True):
        self.colours = list(colours)
        if not keep or self.index >= len(self.colours):
            self.index = 1 if len(self.colours) > 1 else 0
        self.setMinimumWidth(SWATCH * max(len(self.colours), 1))
        self.update()

    def mousePressEvent(self, event):
        if not self.colours:
            return
        index = int(event.position().x()) // SWATCH
        if 0 <= index < len(self.colours):
            self.index = index
            self.chosen.emit(index)
            self.update()

    def paintEvent(self, _event):
        from PyQt6.QtGui import QPainter, QPen
        painter = QPainter(self)
        for i, colour in enumerate(self.colours):
            box = (i * SWATCH, 0, SWATCH - 2, SWATCH - 2)
            if not colour[3]:
                # The transparent entry is a hole, not a black square -
                # drawn as a checker so it cannot be mistaken for one.
                painter.fillRect(*box, QColor(70, 70, 70))
                painter.fillRect(box[0], 0, (SWATCH - 2) // 2,
                                 (SWATCH - 2) // 2, QColor(105, 105, 105))
                painter.fillRect(box[0] + (SWATCH - 2) // 2,
                                 (SWATCH - 2) // 2, (SWATCH - 2) // 2,
                                 (SWATCH - 2) // 2, QColor(105, 105, 105))
            else:
                painter.fillRect(*box, QColor(*colour[:3]))
            painter.setPen(QPen(QColor(255, 255, 255)
                                if i == self.index else QColor(40, 40, 40), 2))
            painter.drawRect(*box)
            painter.setPen(QColor(190, 190, 190))
            painter.drawText(i * SWATCH + 3, SWATCH + 12, f"{i:X}")


class SpriteEditPanel(QGroupBox):
    """Paint the selected piece, or swap its art for a PNG."""

    edited = pyqtSignal()               # the VRAM in hand has changed

    def __init__(self, parent=None):
        super().__init__("Edit piece", parent)
        self.piece = None
        self.vram = None                # the working VRAM (mutable)
        self.original = None            # what it was when loaded
        self.blob = None                # the SPRT bank's own bytes
        self.pool = []                  # (address, how many pieces use it)
        self.sprt = None                # the parsed bank, for user counts
        self._previewing = None         # a CLUT chosen but not applied
        self.indices = []
        self.colours = []
        self._history = []
        self._stroke = []

        self.canvas = PaintCanvas()
        self.canvas.painted.connect(self._paint)
        self.canvas.picked.connect(self._pick)
        self.canvas.stroke_ended.connect(self._end_stroke)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(False)
        scroll.setWidget(self.canvas)
        scroll.setMinimumHeight(220)
        self._scroll = scroll

        self.palette = PaletteStrip(self)

        # Which palette the piece draws through. Choosing one previews
        # it straight away; it only reaches the file on Apply, because
        # that is an edit to the SPRT blob rather than to VRAM.
        self.clut_box = QComboBox(self)
        self.clut_box.setEditable(True)
        self.clut_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.clut_box.setMinimumWidth(240)
        self.clut_box.setToolTip(
            "The palettes this bank's own pieces use, commonest first - "
            "those are the ones the area is known to have loaded, which "
            "is most of what makes a recolour work. Any other 16-halfword "
            "aligned address can be typed instead.\n\nChoosing one only "
            "previews it; Apply writes it into the piece.")
        self.clut_box.activated.connect(self._clut_chosen)
        self.clut_box.lineEdit().returnPressed.connect(self._clut_typed)
        self.copy_clut = QPushButton("From another area...", self)
        self.copy_clut.setToolTip(
            "Take a palette out of another area, copy it somewhere every "
            "area can reach, and point this piece at the copy.\n\nTyping "
            "the other area's address alone does not work: the address "
            "means whatever THIS area loaded there, which is usually "
            "nothing.")
        self.copy_clut.clicked.connect(self._copy_clut_from_area)
        self.apply_clut = QPushButton("Apply palette", self)
        self.apply_clut.setToolTip(
            "Point this piece at the previewed palette. Two bytes of the "
            "SPRT record change and no texel moves.")
        self.apply_clut.clicked.connect(self._commit_clut)

        clut_row = QHBoxLayout()
        clut_row.setContentsMargins(0, 0, 0, 0)
        clut_row.addWidget(QLabel("CLUT:", self))
        clut_row.addWidget(self.clut_box, 1)
        clut_row.addWidget(self.copy_clut)
        clut_row.addWidget(self.apply_clut)

        # Editing a colour changes it for every piece sharing the
        # palette, so how many that is has to be on screen before the
        # edit, not discovered after it.
        self.users_label = QLabel(self)
        self.users_label.setWordWrap(True)
        self.edit_colour = QPushButton("Edit colour...", self)
        self.edit_colour.setToolTip(
            "Change the selected swatch. This edits the PALETTE, so every "
            "piece drawing through it changes too - the count beside this "
            "says how many.")
        self.edit_colour.clicked.connect(self._edit_colour)
        self.make_hole = QPushButton("Make transparent", self)
        self.make_hole.setToolTip(
            "Write the hardware's nothing-at-all value into the selected "
            "swatch. That is a plain zero halfword - the PSX has no alpha "
            "- and it is how every hole in every sprite is made.")
        self.make_hole.clicked.connect(self._make_transparent)
        self.fork_clut = QPushButton("Duplicate palette", self)
        self.fork_clut.setToolTip(
            "Copy these sixteen colours to a free slot and point this "
            "piece at the copy, so editing them stops affecting the other "
            "pieces that share the original.")
        self.fork_clut.clicked.connect(self._fork_clut)

        colour_row = QHBoxLayout()
        colour_row.setContentsMargins(0, 0, 0, 0)
        colour_row.addWidget(self.edit_colour)
        colour_row.addWidget(self.make_hole)
        colour_row.addWidget(self.fork_clut)
        colour_row.addWidget(self.users_label, 1)

        self.info = QLabel("Pick a piece to edit it.", self)
        self.info.setWordWrap(True)

        self.undo_button = QPushButton("Undo", self)
        self.undo_button.setToolTip("Take back the last stroke or import.")
        self.undo_button.clicked.connect(self.undo)
        self.import_button = QPushButton("Import PNG...", self)
        self.import_button.setToolTip(
            "Replace this piece's art with a PNG, matched to its own 16 "
            "colours. Anything transparent becomes the palette's "
            "transparent entry.")
        self.import_button.clicked.connect(self.import_png)
        self.export_button = QPushButton("Export PNG...", self)
        self.export_button.setToolTip(
            "Write this piece out at 1:1 with its palette, ready to be "
            "drawn on and imported back.")
        self.export_button.clicked.connect(self.export_png)
        self.save_button = QPushButton("Save to IMG", self)
        self.save_button.setToolTip(
            "Write the changed texels into this area's TOMBA2.IMG chunk. "
            "Only the halfwords that actually differ are written.")
        self.save_button.clicked.connect(self.save)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        for button in (self.undo_button, self.import_button,
                       self.export_button, self.save_button):
            buttons.addWidget(button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.info)
        layout.addLayout(clut_row)
        layout.addWidget(self.palette)
        layout.addLayout(colour_row)
        layout.addWidget(scroll, 1)
        layout.addLayout(buttons)
        self._enable(False)

        # Set by the viewer so Save knows where to write.
        self.cd_folder = None
        self.chunk_index = None

    def _enable(self, on):
        for button in (self.undo_button, self.import_button,
                       self.export_button, self.save_button,
                       self.apply_clut, self.copy_clut,
                       self.edit_colour, self.make_hole, self.fork_clut):
            button.setEnabled(on)
        self.clut_box.setEnabled(on)

    # --- what is being edited -----------------------------------------

    def set_pool(self, pool, blob, sprt=None):
        """The palettes this bank uses, and its own bytes.

        Commonest first: a palette 51 pieces already draw through is far
        likelier to be the one wanted than one used once, and the list
        runs to a couple of hundred entries on a big bank."""
        self.pool = list(pool)
        self.blob = blob
        self.sprt = sprt
        self.clut_box.blockSignals(True)
        self.clut_box.clear()
        for address, count in sorted(pool, key=lambda p: (-p[1], p[0])):
            self.clut_box.addItem(
                f"0x{address:X}  ({count} piece{'s' if count != 1 else ''})",
                address)
        self.clut_box.blockSignals(False)

    def set_piece(self, piece, vram, original=None):
        """Edit `piece` against `vram`, which is edited in place."""
        self.piece = piece
        self.vram = vram
        self._previewing = None
        if original is not None:
            self.original = original
        if piece is None or vram is None or piece.ww == 0 or piece.hh == 0:
            self.piece = None
            self.canvas.clear()
            self.palette.set_palette([])
            self.info.setText("Pick a piece to edit it.")
            self._enable(False)
            return
        if piece.is_8bpp:
            self.piece = None
            self.canvas.clear()
            self.info.setText(
                "This piece is 8bpp. Nothing on the retail disc is, and "
                "painting it is not supported.")
            self._enable(False)
            return
        self.colours = sprt_edit.palette(vram, piece)
        self.indices = sprt_edit.piece_indices(vram, piece)
        self._show_clut(piece.clut_address)
        self._history = []
        self._stroke = []
        self.palette.set_palette(self.colours)
        self._show_users()
        self._redraw(fit=True)
        self.info.setText(
            f"piece {piece.index}: {piece.ww}x{piece.hh} texels, page "
            f"{piece.texpage}, CLUT 0x{piece.clut_address:X}"
            + ("  (mirrored)" if piece.hflip or piece.vflip else "")
            + "  -  left button paints, right button picks a colour.")
        self._enable(True)

    # --- palette ---------------------------------------------------

    def _show_clut(self, address):
        """Put `address` in the box without treating it as a choice."""
        self.clut_box.blockSignals(True)
        at = self.clut_box.findData(address)
        if at >= 0:
            self.clut_box.setCurrentIndex(at)
        else:
            self.clut_box.setEditText(f"0x{address:X}")
        self.clut_box.blockSignals(False)

    def _clut_chosen(self, index):
        address = self.clut_box.itemData(index)
        if address is not None:
            self._preview_clut(address)

    def _clut_typed(self):
        words = self.clut_box.currentText().split()
        try:
            self._preview_clut(int(words[0], 16) if words else 0)
        except ValueError:
            self.info.setText(f"'{self.clut_box.currentText()}' is not a hex "
                              f"address")

    def _preview_clut(self, address):
        """Draw the piece through another palette, without changing it.

        The texels do not move - only which sixteen colours they mean -
        so this is exactly what the piece would look like once Apply
        writes the address into it."""
        if self.piece is None or self.vram is None:
            return
        try:
            sprt_edit.clut_attribute(address, self.piece.clut)
        except ValueError as e:
            self.info.setText(str(e))
            return
        self._previewing = address
        self.colours = sprt_edit.read_palette_at(self.vram, address,
                                                 self.piece.is_8bpp)
        self.palette.set_palette(self.colours)
        self._show_users()
        self._redraw()
        same = address == self.piece.clut_address
        self.info.setText(
            f"previewing CLUT 0x{address:X}"
            + ("  (this piece's own)" if same else
               "  -  Apply to point the piece at it"))

    # --- editing the palette itself ---------------------------------

    def _clut_address(self):
        """Whichever palette the piece is drawing through right now -
        the previewed one if there is one, else its own."""
        if self._previewing is not None:
            return self._previewing
        return self.piece.clut_address if self.piece else None

    def _show_users(self):
        """Say how many pieces share this palette."""
        address = self._clut_address()
        if address is None or self.sprt is None:
            self.users_label.setText("")
            return
        users = sprt_edit.clut_users(self.sprt, address)
        n = len(users)
        if n <= 1:
            self.users_label.setText(
                f"0x{address:X} is used by this piece only - editing its "
                f"colours affects nothing else.")
        else:
            where = ", ".join(f"{s}/{p}" for s, p in users[:6])
            self.users_label.setText(
                f"<b>0x{address:X} is shared by {n} pieces</b> "
                f"(sprite/piece {where}{'...' if n > 6 else ''}) - editing "
                f"a colour changes all of them. Duplicate it first to "
                f"change only this one.")

    def _edit_colour(self):
        address = self._clut_address()
        if address is None or not self.colours:
            return
        index = self.palette.index
        _value, r, g, b, stp = sprt_edit.palette_entry(self.vram, address,
                                                       index)
        chosen = QColorDialog.getColor(
            QColor(r, g, b), self,
            f"Colour {index:X} of palette 0x{address:X}")
        if not chosen.isValid():
            return
        was = sprt_edit.palette_entry(self.vram, address, index)[0]
        now = sprt_edit.set_palette_entry(
            self.vram, address, index,
            (chosen.red(), chosen.green(), chosen.blue()), stp=stp)
        self._after_palette_change(index, was, now, address)

    def _make_transparent(self):
        address = self._clut_address()
        if address is None or not self.colours:
            return
        index = self.palette.index
        was = sprt_edit.palette_entry(self.vram, address, index)[0]
        now = sprt_edit.set_palette_entry(self.vram, address, index,
                                          (0, 0, 0), transparent=True)
        self._after_palette_change(index, was, now, address)

    def _after_palette_change(self, index, was, now, address):
        """Redraw through the changed palette and say what it cost."""
        self.colours = sprt_edit.read_palette_at(
            self.vram, address, self.piece.is_8bpp)
        self.palette.set_palette(self.colours)
        self._redraw()
        n = len(sprt_edit.clut_users(self.sprt, address)) if self.sprt else 1
        self.info.setText(
            f"colour {index:X} of 0x{address:X}: 0x{was:04X} -> 0x{now:04X}"
            + (f"  -  {n} pieces draw through it" if n > 1 else "")
            + "  -  Save to IMG to keep it.")
        print(f"palette 0x{address:X} colour {index:X}: "
              f"0x{was:04X} -> 0x{now:04X} ({n} user(s))")
        self.edited.emit()

    def _fork_clut(self):
        """Copy this palette somewhere free and use the copy."""
        from functions import psx_vram, vram_map
        from gui.img.img_viewer import chunk_bounds
        address = self._clut_address()
        if address is None or not self.cd_folder:
            return
        idx = os.path.join(self.cd_folder, "TOMBA2.IDX")
        img = os.path.join(self.cd_folder, "TOMBA2.IMG")
        shards = vram_map.chunk_shards(idx, img)
        free = vram_map.free_for(shards, sorted(shards))
        spots = vram_map.free_rects(free, 16, 1, pages=vram_map.USABLE_PAGES,
                                    limit=1, align=16)
        if not spots:
            QMessageBox.information(
                self, "Nowhere to put it",
                "There is no free 16-halfword slot that every area leaves "
                "alone.")
            return
        x, y, _page = spots[0]
        destination = x * 2 + y * psx_vram.VRAM_STRIDE
        sprt_edit.copy_palette(self.vram, address, destination)
        self._preview_clut(destination)
        self._commit_clut()
        self.info.setText(
            f"palette duplicated to 0x{destination:X} - this piece now has "
            f"its own copy. Save to IMG to write the colours.")
        print(f"palette 0x{address:X} duplicated to 0x{destination:X}")
        self.edited.emit()

    def _copy_clut_from_area(self):
        """Bring a palette over from another area and use it."""
        from gui.sprt.palette_dialog import PaletteImportDialog
        if self.piece is None or not self.cd_folder:
            return
        dialog = PaletteImportDialog(self.cd_folder,
                                     self.chunk_index or 1, self)
        if not dialog.exec() or dialog.address is None:
            return
        # The IMG on disc has the palette now, so the VRAM in hand needs
        # it too or the preview would still show the old colours.
        for i, byte in enumerate(dialog._data):
            self.vram[dialog.address + i] = byte
        self.original = bytes(self.vram)
        self._preview_clut(dialog.address)
        self._commit_clut()
        self.saved_to_img()

    def _commit_clut(self):
        """Write the previewed palette into the piece's record."""
        if self.piece is None or self.blob is None:
            return
        address = self._previewing
        if address is None or address == self.piece.clut_address:
            self.info.setText("That is already this piece's palette.")
            return
        try:
            blob = sprt_edit.set_piece_clut(self.blob, self.piece, address)
        except ValueError as e:
            QMessageBox.critical(self, "Can't use that palette", str(e))
            return
        self.blob = blob
        was = self.piece.clut_address
        self.piece.clut = sprt_edit.clut_attribute(address, self.piece.clut)
        self._previewing = None
        self.info.setText(
            f"piece {self.piece.index} now draws through 0x{address:X} "
            f"(was 0x{was:X}) - staged, save the ISO or files to keep it.")
        print(f"sprite piece {self.piece.index}: CLUT 0x{was:X} -> "
              f"0x{address:X}")
        self.clut_committed(blob)

    def clut_committed(self, blob):
        """Overridden by the viewer so the edit is staged and redrawn."""

    def _redraw(self, fit=False):
        image = sprt_edit.image_from_indices(self.indices, self.colours)
        qimage = QImage(image.tobytes(), image.width, image.height,
                        image.width * 4, QImage.Format.Format_RGBA8888).copy()
        self.canvas.set_image(qimage)
        if fit:
            self.canvas.set_zoom(fit_zoom((image.width, image.height),
                                          self._scroll.viewport().size(), 16))

    # --- painting ------------------------------------------------------

    def _paint(self, col, row):
        if self.piece is None:
            return
        if not (0 <= row < len(self.indices)
                and 0 <= col < len(self.indices[0])):
            return
        was = self.indices[row][col]
        if was == self.palette.index:
            return
        self._stroke.append((col, row, was))
        self.indices[row][col] = self.palette.index
        sprt_edit.apply_indices(self.vram, self.piece, self.indices)
        self._redraw()
        self.edited.emit()

    def _pick(self, col, row):
        if self.piece is None:
            return
        if 0 <= row < len(self.indices) and 0 <= col < len(self.indices[0]):
            self.palette.index = self.indices[row][col]
            self.palette.update()

    def _end_stroke(self, name="drawing"):
        if self._stroke:
            self._history.append((name, self._stroke))
            self._stroke = []

    def undo(self):
        self._end_stroke()
        if not self._history:
            self.info.setText("Nothing to undo")
            return
        name, texels = self._history.pop()
        # Newest first, so a texel painted twice in one stroke ends up
        # with what it held before the stroke started.
        for col, row, was in reversed(texels):
            self.indices[row][col] = was
        sprt_edit.apply_indices(self.vram, self.piece, self.indices)
        self._redraw()
        self.info.setText(f"Undid {name} ({len(texels)} texel(s)); "
                          f"{len(self._history)} left")
        self.edited.emit()

    # --- PNG -----------------------------------------------------------

    def _stem(self):
        p = self.piece
        return (f"piece{p.index}_page{p.texpage}_clut{p.clut_address:06X}"
                f"_{p.ww}x{p.hh}")

    def export_png(self):
        if self.piece is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export this piece", self._stem() + ".png", "PNG (*.png)")
        if not path:
            return
        sprt_edit.image_from_indices(self.indices, self.colours).save(path)
        print(f"wrote {path}")

    def import_png(self):
        if self.piece is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import a PNG over this piece", "", "PNG (*.png)")
        if not path:
            return
        try:
            image = Image.open(path)
        except Exception as e:
            QMessageBox.critical(self, "Couldn't read it", str(e))
            return
        if image.size != (self.piece.ww, self.piece.hh):
            answer = QMessageBox.question(
                self, "Different size",
                f"{os.path.basename(path)} is {image.size[0]}x"
                f"{image.size[1]} and this piece is {self.piece.ww}x"
                f"{self.piece.hh}.\n\nScale it to fit?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._end_stroke()
        before = [row[:] for row in self.indices]
        self.indices = sprt_edit.indices_from_image(
            image, self.colours, self.piece.ww, self.piece.hh)
        # One undoable action, recorded as every texel that changed.
        self._history.append(("import", [
            (x, y, before[y][x])
            for y in range(len(before)) for x in range(len(before[0]))
            if before[y][x] != self.indices[y][x]]))
        sprt_edit.apply_indices(self.vram, self.piece, self.indices)
        self._redraw()
        changed = len(self._history[-1][1])
        self.info.setText(f"Imported {os.path.basename(path)} - "
                          f"{changed} texel(s) changed.")
        print(f"imported {path} over piece {self.piece.index}: "
              f"{changed} texel(s) changed")
        self.edited.emit()

    # --- saving ---------------------------------------------------------

    def save(self):
        from functions import img_writer
        from gui.img.img_viewer import chunk_bounds
        if self.vram is None or self.original is None:
            return
        if self.cd_folder is None or self.chunk_index is None:
            QMessageBox.information(
                self, "Nowhere to write it",
                "This bank wasn't opened from a disc, so there is no IMG "
                "chunk to write into.")
            return
        rects = sprt_edit.changed_rects(self.original, self.vram)
        if not rects:
            self.info.setText("Nothing has changed.")
            return
        shards = sprt_edit.shards_for(self.vram, rects)
        idx = os.path.join(self.cd_folder, "TOMBA2.IDX")
        img = os.path.join(self.cd_folder, "TOMBA2.IMG")
        answer = QMessageBox.question(
            self, "Write to TOMBA2.IMG?",
            f"{len(shards)} shard(s), "
            f"{sum(w * h * 2 for _x, _y, w, h, _p in shards)} bytes, into "
            f"AREA_{self.chunk_index:02X}'s chunk.\n\nThe file gets longer, "
            f"so every chunk after it moves and TOMBA2.IDX is rewritten to "
            f"match.\n\nGo ahead?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            start, end = chunk_bounds(idx, self.chunk_index)
            with open(img, "rb") as f:
                f.seek(start)
                chunk = f.read(end - start)
            info = img_writer.rebuild(
                idx, img, {self.chunk_index: img_writer.add_shards(chunk,
                                                                  shards)},
                idx, img)
        except Exception as e:
            QMessageBox.critical(self, "Couldn't write it",
                                 f"TOMBA2.IMG was not changed:\n\n{e}")
            return
        self.original = bytes(self.vram)
        self.info.setText(
            f"Wrote {len(shards)} shard(s); TOMBA2.IMG grew by "
            f"{info['grew_by']} bytes.")
        print(f"sprite edit written: {len(shards)} shard(s), {info}")
        self.saved_to_img()

    def saved_to_img(self):
        """Overridden by the viewer so MainWindow learns the IMG is
        dirty - without it an export ships the original IMG beside the
        rewritten IDX. See MainWindow.img_dirty."""
