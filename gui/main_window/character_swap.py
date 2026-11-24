"""Play as another character (game/character_swap.py) from the main window."""
from pathlib import Path

from PyQt6.QtWidgets import QMessageBox

from game import character_swap, game_build
from gui.main_window.npcs import remap_project_state


class CharacterSwapMixin:
    def _swap_paths(self):
        if not self.dat_file:
            raise ValueError("Open a game or project first.")
        exe = getattr(self.mainexe_viewer, "exe_path", None)
        if not exe:
            raise ValueError("Playing as another character needs MAIN.EXE beside the game files.")
        if game_build.use(exe).name != "us-retail":
            raise ValueError("Playing as another character currently requires the US retail game.")
        cd = Path(self.dat_file).parent
        a0f = self.overlay_for_area(0x13)
        return dict(exe=Path(exe), dat=cd / "TOMBA2.DAT", idx=cd / "TOMBA2.IDX",
                    img=cd / "TOMBA2.IMG", a0f=Path(a0f) if a0f else None)

    def open_character_swap(self):
        from gui.widgets.character_swap_dialog import CharacterSwapDialog
        try:
            self._swap_paths()
            # Work on the project's own files, never the source disc.
            if self._npc_materialize_session() is None:
                return
            paths = self._swap_paths()
            dat, idx = paths["dat"].read_bytes(), paths["idx"].read_bytes()
            exe = paths["exe"].read_bytes()
            current = character_swap.installed(exe, dat, idx, 0)
            current.name = "Tomba slot as installed"
            sources = [current]
            skeletons = [character_swap.tomba_skeleton(exe, dat, idx)]
            if paths["a0f"] is not None and paths["a0f"].name.upper() == "A0F.BIN":
                try:
                    tabby = character_swap.tabby(dat, idx, paths["a0f"].read_bytes())
                    sources.append(tabby)
                    skeletons.append(character_swap.skeleton_of(tabby))
                except character_swap.SwapError as error:
                    print(f"Tabby unavailable: {error}")
            if current.bones != skeletons[0].bones:     # a swapped body's own rig
                skeletons.append(character_swap.skeleton_of(current))
            dialog = CharacterSwapDialog(self, paths, sources, skeletons)
            found, failed = self._swap_catalogue()
            added = sum(dialog._add_recipes(recipes) for _chunk, recipes in found)
            if found or failed:
                note = f"{added} character(s) found in {len(found)} area(s)."
                if failed:
                    note += " Skipped: " + "; ".join(f"AREA_{c:02X}" for c, _e in failed) + "."
                dialog.source_note.setText(note)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Play as another character", str(error))
            return
        dialog.exec()

    def _swap_catalogue(self):
        """Every area's characters, searched once per opened game/project."""
        key = str(self.dat_file)
        cached = getattr(self, "_swap_catalogue_cache", None)
        if cached and cached[0] == key:
            return cached[1]
        chunks = [c for c in range(4, 26) if self.overlay_for_area(c)]
        try:
            result = self.npc_catalogue_all(chunks)
        except Exception as error:          # cancelled or unreadable: open without them
            print(f"Character search: {error}")
            return [], []
        self._swap_catalogue_cache = (key, result)
        return result

    def install_character_swap(self, prepared):
        """Write a prepared swap into the working project and rebase on it."""
        idx = next(p for p in prepared.files if p.name.upper() == "TOMBA2.IDX")
        state = self._translation_project_state()
        before_idx = prepared.originals[idx]
        active = getattr(self.level_panel, "chunk", None)
        root = self._npc_project_root()
        character_swap.commit(prepared)
        # Trail files moved; edit markers follow their files.
        state = remap_project_state(state, before_idx, idx.read_bytes())
        state["img_dirty"] = True
        manifest = self._npc_write_manifest(root, state)
        self._npc_reload_project(root, manifest, state, active)
        self._mark_swapped(idx, prepared)
        self._npc_write_manifest(root, self._translation_project_state())
        self.statusBar().showMessage(
            "Character swap installed in the working project. Save the project to keep it.", 15000)

    def _mark_swapped(self, idx, prepared):
        """Stage the new costume files as edits, so Build Disc ships this
        DAT/IDX, and flag the IMG - neither goes out otherwise."""
        from formats.archive import repacker
        trail = repacker.parse_idx_bytes(idx.read_bytes())[character_swap.SYSTEM_CHUNK]["trail_ranges"]
        for slot in prepared.models:
            start = trail[slot][0]
            item = self.address_item.get(start)
            entry = self._entry_of(item)
            if entry is None:
                continue
            label = f"{character_swap.COSTUMES[slot].name} (swapped body)"
            self.pending_file_edits[entry["key"]] = dict(entry, data=self._entry_bytes(item), label=label)
            self._colour_address(start, "edited")
        self.img_dirty = True
        self._refresh_edit_status()
