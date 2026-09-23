"""Offering to move a model's art where every area can reach it.

A packet names a texture page, not a texture: a model whose art
lives in one level's IMG chunk draws wrong everywhere else. See
game/texture_migrate.py for the measuring and placing; this is
the part that notices and asks.
"""
import os

from PyQt6.QtWidgets import QMessageBox
from formats.images.img_viewer import chunk_bounds as img_browser_bounds
from psx import vram as psx_vram
from psx.vram_viewer import decode_vram_bytes


class TextureMigrationMixin:
    """Offering to move a model's art where every area can reach it.

    Mixed into MainWindow; every method here reaches the rest of
    the window through self.
    """

    def _chunk_vram(self, area):
        """One area's IMG chunk, decompressed. Cached - a check walks
        several areas and each decompress is not cheap."""
        cache = getattr(self, "_chunk_vram_cache", None)
        if cache is None:
            cache = self._chunk_vram_cache = {}
        if area not in cache:
            cd = os.path.dirname(self.dat_file)
            try:
                start, end = img_browser_bounds(
                    os.path.join(cd, "TOMBA2.IDX"), area)
                if end <= start:
                    cache[area] = None
                else:
                    with open(os.path.join(cd, "TOMBA2.IMG"), "rb") as f:
                        f.seek(start)
                        cache[area] = decode_vram_bytes(f.read(end - start))
            except Exception:
                cache[area] = None
        return cache[area]

    def _offer_texture_migration(self, item, data, source_item):
        """After an SMST is swapped in, say whether its art came with it.

        A swap copies bytes, and a model's bytes do not contain its
        textures - they contain page numbers and palette addresses,
        which mean whatever the destination area happens to have loaded
        there. So the swap can succeed and the model still draw wrong,
        which is exactly what happens putting Tuxedo Tomba where the
        standard model was. This is the moment to notice."""
        from game import texture_migrate
        from psx import vram_map
        from formats.archive.format_detect import FormatError, smst_groups
        try:
            smst_groups(data)
        except (FormatError, ValueError):
            return                      # not a model; nothing to check
        entry = self._entry_of(item)
        source_entry = self._entry_of(source_item)
        if entry is None or source_entry is None or not self.dat_file:
            return
        destinations = self._areas_reaching(entry)
        sources = self._areas_reaching(source_entry)
        if not destinations or not sources:
            print("[textures] couldn't tell which areas these files belong "
                  "to, so the swap was not checked.")
            return
        try:
            cd = os.path.dirname(self.dat_file)
            shards = vram_map.chunk_shards(
                os.path.join(cd, "TOMBA2.IDX"), os.path.join(cd, "TOMBA2.IMG"))
            boxes, cluts = texture_migrate.survey(data)
            # Where the model's art really is. A trail file is reached
            # from many areas and its textures sit in only one of them,
            # so the source is the area that has the most of what it
            # samples rather than whichever is listed first.
            came_from, source_vram, best = None, None, -1
            for area in sources:
                vram = vram_map.loaded_vram(shards, self._chunk_vram, area)
                present = sum(1 for page in boxes
                              if any(vram[at] for at
                                     in range(*self._page_span(page))))
                if present > best:
                    came_from, source_vram, best = area, vram, present
            # Missing from ANY area that reaches the destination: the
            # model has to draw correctly in all of them, not just one.
            keep_pages, keep_cluts = texture_migrate.needed_for(
                data, source_vram,
                [vram_map.loaded_vram(shards, self._chunk_vram, a)
                 for a in destinations])
        except Exception as e:
            print(f"[textures] couldn't check the swap: {e}")
            return

        missing_pages = sorted(set(boxes) - keep_pages)
        missing_cluts = sorted(set(cluts) - keep_cluts)
        where = (f"all {len(destinations)} areas"
                 if len(destinations) > 8 else
                 "AREA_" + ", AREA_".join(f"{a:02X}" for a in destinations))
        if not missing_pages and not missing_cluts:
            print(f"[textures] {item.text()}: every page and palette it "
                  f"samples is already loaded in all {len(destinations)} "
                  f"area(s) that use it ({where}) - no migration needed.")
            return

        answer = QMessageBox.question(
            self, "The textures did not come with it",
            f"{item.text()} draws from "
            + (f"texture page(s) {', '.join(str(p) for p in missing_pages)}"
               if missing_pages else "")
            + (" and " if missing_pages and missing_cluts else "")
            + (f"{len(missing_cluts)} palette(s)" if missing_cluts else "")
            + f" that at least one of the {len(destinations)} area(s) using "
            f"this file does not have loaded.\n\nThis file is reached from "
            f"{where}, and the art is in AREA_{came_from:02X}.\n\n"
            f"Until it is copied somewhere all of them can reach, the model "
            f"will draw with whatever those areas happen to keep at those "
            f"addresses.\n\nSet that up now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes:
            print(f"[textures] {item.text()} still points at page(s) "
                  f"{missing_pages} and {len(missing_cluts)} palette(s) that "
                  f"not every area using it loads.")
            return
        self.open_texture_migration(item, data, source_vram,
                                    preselect=destinations)

    @staticmethod
    def _page_span(page):
        """(first byte, last byte) of a texture page's first row - enough
        to tell whether an area has anything there at all."""
        column = (page % 16) * psx_vram.PAGE_BYTES
        row = (page // 16) * psx_vram.PAGE_ROWS
        at = row * psx_vram.VRAM_STRIDE + column
        return at, at + psx_vram.PAGE_BYTES

    def _areas_reaching(self, entry):
        """Which areas can reach a file, lowest first.

        An SDAT file belongs to one area. A TRAIL file - which is where
        Tomba's own models live - sits past every chunk and is reached
        from all the areas that point at it, so there is no single
        answer and area_membership is what knows."""
        if entry.get("area") is not None:
            return [entry["area"]]
        membership = getattr(self, "area_membership", None) or {}
        return sorted(membership.get(entry["address"], ()))

    def open_texture_migration(self, item, data, source_vram, preselect=()):
        """The placement dialog, staging whatever it decides on `item`."""
        from formats.models.migrate_dialog import MigrateDialog
        dialog = MigrateDialog(data, source_vram,
                               os.path.dirname(self.dat_file),
                               item.text(), self)
        # Every area that reaches this file starts ticked: those are the
        # ones it has to draw correctly in, and for a trail model that
        # is most of the disc.
        dialog.tick_areas(preselect if isinstance(preselect,
                                                  (list, tuple, set))
                          else [preselect])
        if dialog.exec() and dialog.new_blob is not None:
            self._stage_file_edit(item, dialog.new_blob,
                                  f"{item.text()} with migrated textures")
            self._chunk_vram_cache = {}
            # The dialog wrote TOMBA2.IMG and TOMBA2.IDX on disc. Without
            # this an export ships the ORIGINAL IMG beside the rewritten
            # IDX, which points every chunk at the wrong offset - see
            # img_dirty.
            self.img_dirty = True
            self._refresh_edit_status()
            print(f"[textures] {item.text()}: UVs rewritten and staged; "
                  f"TOMBA2.IMG and TOMBA2.IDX will be shipped with it.")
