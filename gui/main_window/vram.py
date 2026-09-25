"""Which VRAM an area has loaded.

Every textured view needs the 1 MB of video memory its area would
have had. It is assembled from the area's own IMG chunk, plus - for
the character models - the one chunk every area shares.
"""
import numpy as np
import os

from formats.audio import voice
from psx.vram_viewer import vram_index_image
from gui.main_window.common import COMMON_VRAM_AREA


class VramMixin:
    """Which VRAM an area has loaded.

    Mixed into MainWindow; every method here reaches the rest of
    the window through self.
    """

    def overlay_name_for_area(self, chunk_index):
        """The BIN the game runs for an IDX area, including purified areas."""
        chunk = chunk_index or 0
        name = self.OVERLAY_NAMES.get(chunk + 6)
        if ((not name or not name.startswith("A0"))
                and chunk >= self.PURIFIED_OFFSET):
            # A purified area has no overlay of its own; it runs on the
            # one belonging to the area it is a copy of. Reached both
            # when chunk + 6 names nothing (AREA_1B) and when it names
            # something that is not an area's overlay at all: AREA_1A,
            # 1C and 1D land on SOP/OPN/CRD.BIN, which are the menu and
            # cutscene overlays, and were taking those areas' animated
            # palettes with them.
            name = self.OVERLAY_NAMES.get(chunk - self.PURIFIED_OFFSET + 6)
        return name

    def overlay_for_area(self, chunk_index):
        """The Axx.BIN belonging to an area, or None if there isn't one
        or the disc was opened somewhere without a BIN folder."""
        name = self.overlay_name_for_area(chunk_index)
        if not name or not self.dat_file:
            return None
        root = os.path.dirname(os.path.dirname(os.path.dirname(self.dat_file)))
        for folder in (os.path.join(root, "BIN"),
                       os.path.join(os.path.dirname(
                           os.path.dirname(self.dat_file)), "BIN"),
                       # An ISO opened directly (rather than an already-
                       # extracted folder) has TOMBA2.DAT sitting flat in
                       # its own temp dir, with BIN/ written beside it as
                       # a sibling - see ISOHandler.extract_iso.
                       os.path.join(os.path.dirname(self.dat_file), "BIN")):
            path = os.path.join(folder, name)
            if os.path.exists(path):
                return path
        # A disc opened as an image has no BIN folder on disk - the
        # overlays stay inside it - so take this one out and keep it.
        image = getattr(self, "current_iso_path", None)
        if image and self.iso_handler and self.iso_handler.get_temp_dir():
            cached = os.path.join(self.iso_handler.get_temp_dir(), name)
            if os.path.exists(cached):
                return cached
            data = voice.extract_file(image, name)
            if data:
                with open(cached, "wb") as f:
                    f.write(data)
                return cached
        return None

    @staticmethod
    def _area_chunk_index(item):
        """The AREA_NN number a file row sits under (its folder's parent,
        or the folder itself), or None if it isn't under one. The label
        carries a count - "AREA_04 (41)" - so only the first word of the
        number is parsed."""
        parent = item.parent()
        if parent is None:
            return None
        for candidate in (parent.parent(), parent):
            if candidate is not None and candidate.text().startswith("AREA_"):
                try:
                    return int(candidate.text().split("_")[1].split()[0], 16)
                except ValueError:
                    return None
        return None

    def _load_area_vram_bytes(self, chunk_index, merge_common=False):
        """This area's TOMBA2.IMG chunk, decompressed to raw VRAM - what
        SPRT pieces are cut out of. None (with a printed reason) if the
        area has no VRAM or it can't be read; callers are expected to
        carry on without it.

        `merge_common` fills in what AREA_01 holds wherever this area's
        own VRAM is empty. The character models sample texture pages
        that are only ever in AREA_01's chunk - it is loaded once and
        stays resident - and the two never overlap by a byte on the
        retail disc, so the merge adds their art without touching the
        area's own."""
        if chunk_index is None or not self.dat_file:
            return None
        cache = getattr(self, "_area_vram_cache", None)
        if cache is None:
            cache = self._area_vram_cache = {}
        key = (chunk_index, bool(merge_common))
        if key in cache:
            return cache[key]
        # _chunk_vram owns the decompression cache and is invalidated whenever
        # TOMBA2.IMG changes. Previously every Level/SMST/ANMP open inflated
        # the same IMG chunk again even when its exact bytes were in memory.
        vram = self._chunk_vram(chunk_index)

        if not merge_common or chunk_index == COMMON_VRAM_AREA:
            cache[key] = vram
            return vram
        common = self._load_area_vram_bytes(COMMON_VRAM_AREA)
        if common is None:
            cache[key] = vram
            return vram
        if vram is None:
            cache[key] = common
            return common
        own = np.frombuffer(bytes(vram), dtype=np.uint8).copy()
        shared = np.frombuffer(bytes(common), dtype=np.uint8)
        empty = own == 0
        own[empty] = shared[:own.size][empty]
        cache[key] = bytearray(own.tobytes())
        return cache[key]

    def _anmp_model_vram(self, address, current_area):
        """VRAM from an SMST's own area, even in another area's ANMP view.

        A trail model can be reachable from several areas; prefer the area
        being edited when it is one of them, otherwise use the first area
        that actually loads the model.  Falling back to the ANMP's area is
        only for old/custom indexes without membership metadata.
        """
        areas = sorted((getattr(self, "area_membership", None) or {}).get(
            address, ()))
        area = (current_area if current_area in areas else
                areas[0] if areas else current_area)
        if area is None:
            return None
        vram = self._load_area_vram_bytes(area, merge_common=True)
        return (vram, vram_index_image(vram) if vram else None, area)
