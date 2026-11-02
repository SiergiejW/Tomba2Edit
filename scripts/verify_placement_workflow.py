"""Exercise real panel/default/edit/apply/reopen using an isolated retail copy.

No source game file is modified. Rendering is covered by the live UI smoke;
this check runs offscreen and verifies native bytes and all area selectors.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from formats.sprites import pickup_art
from game import game_build, placement
from gui.level.level_panel import LevelEditorPanel
from gui.level.level_scene import LevelScene, area_files, view_offset, view_point


def sparse_scene(root, dat, idx, area=4):
    scene = LevelScene()
    scene.chunk_index = area + 4             # A04 / actual IDX chunk 8
    scene.overlay_path = str(root / "BIN" / f"A0{'0123456789ABCDEFGHIJKL'[area]}.BIN")
    scene.exe_path = str(root / "MAIN.EXE")
    scene.dat_path = str(dat)
    scene.overlay_data = Path(scene.overlay_path).read_bytes()
    scene.pickups = placement.load_pickups(scene.overlay_path, scene.exe_path)
    scene.planes = scene._load_planes(str(idx), str(dat), scene.chunk_index)
    scene.reward_art = pickup_art.reward_art(
        scene.exe_path, overlay=scene.overlay_data, area=scene.area_index)
    scene.chest_models = pickup_art.chest_models(scene.exe_path)
    scene.chest_offsets = tuple(view_offset(o) for o in pickup_art.chest_offsets(scene.exe_path))
    scene.dat_start, files = area_files(str(idx), scene.chunk_index)
    scene.by_id = {file: (offset, size) for _, file, offset, size in files}
    start, resident = area_files(str(idx), 1)
    scene.resident = {file: (start, (offset, size)) for _, file, offset, size in resident}
    return scene


def main():
    source = ROOT / "tomba2"
    app = QApplication.instance() or QApplication([])
    dat, idx = source / "CD" / "TOMBA2.DAT", source / "CD" / "TOMBA2.IDX"
    native_names = [f"A0{c}.BIN" for c in "0123456789ABCDEFGHIJKL"]
    before_hash = {name: (source / "BIN" / name).read_bytes() for name in native_names}
    original_exe = (source / "MAIN.EXE").read_bytes()
    with tempfile.TemporaryDirectory(prefix="tomba2-placement-") as temporary:
        root = Path(temporary)
        (root / "BIN").mkdir()
        shutil.copy2(source / "MAIN.EXE", root / "MAIN.EXE")
        for name in native_names:
            shutil.copy2(source / "BIN" / name, root / "BIN" / name)
        game_build.use(str(root / "MAIN.EXE"))
        panel = LevelEditorPanel()
        panel.dat_path, panel.idx_path = str(dat), str(idx)
        panel.exe_path = str(root / "MAIN.EXE")
        panel.overlay_for_area = lambda chunk: (
            str(root / "BIN" / native_names[chunk - 4]) if 4 <= chunk < 26 else None)
        panel.scene = sparse_scene(root, dat, idx)
        panel.viewer.scene = panel.scene
        panel.view_box.addItem("Area", None)

        def refreshed(selected):
            panel.viewer.model_data = {"groups": panel.scene.instances}
            panel.viewer.selected = selected

        panel._refresh_edited_scene = refreshed
        scene = panel.scene
        before_counts = {name: [(table[0].table, len(table)) for table in
                               placement.find_pickups(before_hash[name], panel.exe_path)]
                         for name in native_names}
        original = len(scene.pickups)
        anchor = next(r for r in scene.pickups if r.chest and not r.type & 0x80)
        added = []
        errors = []
        with patch.object(QMessageBox, "information", side_effect=lambda *a: errors.append(a[-1])), \
                patch.object(QMessageBox, "critical", side_effect=lambda *a: errors.append(a[-1])):
            for index, category in enumerate(("chest", "crystal", "item", "apple", "weapon")):
                count = len(scene.pickups)
                panel._add_pickup(category, view_point((anchor.x + index * 64,
                                                       anchor.y - 200, anchor.z)))
                assert len(scene.pickups) == count + 1, (category, errors)
                added.append(scene.pickups[-1])
            # Choose another chest type and its contents through actual controls.
            chest_instance = next(i for i in scene.instances if i.pickup is added[0])
            panel.viewer.selected = chest_instance.index
            panel._filling = True
            panel._fill_pickup_options(chest_instance)
            panel._filling = False
            panel.pickup_kind.setCurrentIndex(panel.pickup_kind.findData(2))
            red_key = next(c for c in panel._catalog() if c.item == 29 and c.reward < 128)
            panel.pickup_reward.setCurrentIndex(panel.pickup_reward.findData(red_key.reward))
            assert added[0].reward == 2
            assert added[0].contents == red_key.reward
            # Finish a vertical move, preserving native X/Z and yaw.
            scene.move_pickup(chest_instance, (chest_instance.x, chest_instance.y + 50,
                                               chest_instance.z), axis="vertical")
            panel._placement_dirty = True
            assert panel.apply_changes(), errors
            first = (root / "BIN" / "A04.BIN").read_bytes(), (root / "MAIN.EXE").read_bytes()
            assert panel.apply_changes(), errors
            assert first == ((root / "BIN" / "A04.BIN").read_bytes(),
                             (root / "MAIN.EXE").read_bytes())
        assert not errors, errors
        reopened = sparse_scene(root, dat, idx)
        assert len(reopened.pickups) == original + 5
        by_bit = {(r.apple, r.bit): r for r in reopened.pickups}
        details = []
        for category, saved in zip(("chest", "crystal", "item", "apple", "weapon"), added):
            loaded = by_bit[saved.apple, saved.bit]
            assert placement.PICKUP.pack(*placement.PICKUP.unpack_from(
                (root / "BIN" / "A04.BIN").read_bytes(), loaded.offset)) == \
                placement.PICKUP.pack(saved.type, saved.alloc, saved.persist, saved.reward,
                                      saved.x, saved.y, saved.z, saved.bit, saved.plane,
                                      saved.behaviour, saved.config)
            art = reopened.reward_art[loaded.contents if loaded.chest else loaded.art_reward]
            details.append(dict(category=category, reward=loaded.contents if loaded.chest else loaded.art_reward,
                                item_id=art.item, grants=art.grants, position=loaded.position,
                                plane=loaded.plane, bit=loaded.bit, table=loaded.table))
        # Relocation must not invalidate any other area's selector.
        for name in native_names:
            after = [(table[0].table, len(table)) for table in
                     placement.find_pickups((root / "BIN" / name).read_bytes(), panel.exe_path)]
            if name != "A04.BIN":
                assert after == before_counts[name], (name, before_counts[name], after)
        assert next(d for d in details if d["category"] == "item")["item_id"] == 29
        assert next(d for d in details if d["category"] == "weapon")["item_id"] == 3
        assert next(d for d in details if d["category"] == "apple")["reward"] == 0
        assert next(d for d in details if d["category"] == "chest")["item_id"] == 29
        model = scene.build()
        assert next(i for i in scene.instances if i.pickup is added[0]).face_count
        # The native town convention has free X/Z and authored 8-bit yaw.
        town = sparse_scene(root, dat, idx, area=2)
        panel.scene = town
        panel.viewer.scene = town
        town_rows = []
        with patch.object(QMessageBox, "information", side_effect=lambda *a: errors.append(a[-1])), \
                patch.object(QMessageBox, "critical", side_effect=lambda *a: errors.append(a[-1])):
            for room in (None, 3):            # streets, then native interior 2
                native = next(r for r in town.pickups
                              if bool(r.type & 0x80) == (room is not None)
                              and (room is None or r.persist == room - 1))
                panel.view_box.blockSignals(True)
                panel.view_box.clear()
                panel.view_box.addItem("Selected view", room)
                panel.view_box.blockSignals(False)
                count = len(town.pickups)
                panel._add_pickup("chest", view_point(native.position))
                assert len(town.pickups) == count + 1, errors
                record = town.pickups[-1]
                instance = next(i for i in town.instances if i.pickup is record)
                assert record.behaviour == 4
                town.rotate_pickup(instance, 90)
                assert record.plane == 64
                town_rows.append(record)
            panel._placement_dirty = True
            assert panel.apply_changes(), errors
        town_reopened = sparse_scene(root, dat, idx, area=2)
        for saved in town_rows:
            row = next(r for r in town_reopened.pickups if r.bit == saved.bit)
            assert row.position == saved.position
            assert row.type == saved.type and row.persist == saved.persist
            assert row.plane == 64 and town_reopened.chest_heading(row) == 90
        assert not errors, errors
        panel._phase_timer.stop()
        panel._sprite_timer.stop()
        panel.close()
        print(json.dumps(dict(status="passed", additions=details,
                              other_overlays_preserved=21,
                              town_chests_reopened=len(town_rows),
                              built_vertices=len(model["vertices"])), indent=2))
    assert (source / "MAIN.EXE").read_bytes() == original_exe
    assert all((source / "BIN" / name).read_bytes() == data for name, data in before_hash.items())


if __name__ == "__main__":
    main()
