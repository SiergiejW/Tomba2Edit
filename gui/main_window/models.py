"""Which model, and which skeleton, a row is offered.

An ANMP does not say which SMST it animates and an SMST does not
say which skeleton poses it. Both are worked out here - from the
handler code, from the approved pairings, and, failing those,
from how near the two files sit on the disc.
"""
import os
import re
import struct

from PyQt6.QtCore import Qt
from formats.animation import game_rest, pairings
from formats.archive.idx_parser import row_label_data
from game import handler_models


class ModelsMixin:
    """Which model, and which skeleton, a row is offered.

    Mixed into MainWindow; every method here reaches the rest of
    the window through self.
    """

    def _approved_pairings(self):
        """The judged pairings, resolved to the bone tables they name.

        Built once - it reads every area an approval was made in, which
        needs the overlays, and they do not change while a disc is."""
        if getattr(self, "_approvals", None) is None:
            self._approvals = pairings.resolve(
                pairings.load(), self._skeleton_sources)
            print(f"[ANMP] approved pairings on file: {len(self._approvals)}")
        return self._approvals

    def _skeleton_types(self):
        """The disc-wide shape catalogue, built once - see
        game_rest.catalogue. A second of work, and only when the first
        animation is opened rather than while the disc is loading."""
        if getattr(self, "_type_names", None) is None:
            everywhere = []
            exe = getattr(self.mainexe_viewer, "exe_path", None)
            if exe:
                everywhere.append(exe)
            for area in range(48):
                path = self.overlay_for_area(area)
                if path and path not in everywhere:
                    everywhere.append(path)
            if getattr(self, "_overlay_bytes", None) is None:
                self._overlay_bytes = {}
            sources = game_rest.load_sources(
                exe, None, everywhere, self._overlay_bytes)
            self._type_names = game_rest.catalogue(sources)
            print(f"[ANMP] skeleton shapes on this disc: "
                  f"{len(self._type_names)}")
        return self._type_names

    def _pending_smst_blob(self, address):
        """An SMST edit that is staged but not yet written to the disc.

        Handed to formats/models/smst_parser so every view that loads a model
        - the SMST tab, the ANMP tab, the skeleton search, the export -
        sees a pasted part rather than what is still on the disc. Read
        straight out of pending_file_edits, so undoing the edit there
        undoes this with it."""
        for info in self.pending_file_edits.values():
            if info.get("address") == address:
                return info.get("data")
        return None

    def _push_clut_choices(self, model, vram_bytes, name=None,
                           chunk_index=None):
        """Offer the VRAM view the palettes this model samples.

        Nothing in VRAM marks a palette as a palette, so a viewer left
        to itself can only guess where they are. A loaded model already
        knows: every face carries the address of the one it draws
        through. Each is listed with the texture pages that use it,
        since that is what identifies it by eye."""
        if not model:
            return
        pages = {}
        for page, clut, *_rest in model.get("texture_info") or ():
            pages.setdefault(clut, set()).add(page)
        choices = [(f"page {', '.join(str(p) for p in sorted(used))}", clut)
                   for clut, used in sorted(pages.items())]
        self.vram_viewer.set_clut_choices(choices)
        # For the VRAM view's Textured mode - see psx/vram_preview.
        if chunk_index is not None and self.dat_file:
            self.vram_viewer.set_area_source(
                os.path.join(os.path.dirname(self.dat_file), "TOMBA2.IDX"),
                self.dat_file, chunk_index)
        if vram_bytes is not None:
            self.vram_viewer.set_vram_bytes(
                vram_bytes, name or self.vram_viewer.source_name)

    def _bones_for_model(self, model, chunk_index, address=None):
        """The skeleton this model is built on, or None.

        Prefer bone tables passed with this exact SDAT model file by the
        game's actor initializer. Without that code association, try every
        plausible size and retain the older geometric fallback."""
        if not model or not model.get("groups"):
            return None
        bindings = self._model_skeletons(chunk_index).get(address, ())
        if bindings:
            choices = []
            blocks = game_rest.mesh_blocks(model)
            for binding in bindings:
                if binding.limb_count > len(model["groups"]):
                    continue
                bones = [tuple(row) for row in binding.bones]
                grade = game_rest.fit(bones, model, blocks)
                choices.append((grade if grade is not None else float("inf"),
                                binding.offset, binding.source, bones))
            if choices:
                grade, offset, source, bones = min(choices)
                print(f"[SMST] export skeleton: exact actor-code binding "
                      f"{source} 0x{offset:X}, fit {grade:.2f}")
                return bones
        counts = list(range(min(len(model["groups"]), 32), 1, -1))
        try:
            found = game_rest.best_for(
                self._skeleton_sources(chunk_index), model, counts)
        except Exception as e:
            print(f"[SMST] no skeleton for export: {e}")
            return None
        if found:
            print(f"[SMST] export skeleton: {found[0]} 0x{found[1]:X} "
                  f"at {found[3]} bones, fit {found[4]:.2f}")
        return found[2] if found else None

    def _skeleton_sources(self, chunk_index):
        """Where to look for bone trees when posing an area's animation:
        that area's own overlay, and MAIN.EXE for the player.

        Deliberately not the other areas' overlays. Reaching into them
        looked like it helped - it found a better-fitting table for the
        tiny mouse than its own area had - but every character since
        checked against a savestate keeps its skeleton in the overlay of
        the area it appears in: Mizuno in A06, the armadillo in A04,
        Pham's daughter in A05, the mole and the miner in A01, the pig
        in A00, Tomba alone in MAIN.EXE. Searching wider only adds
        another area's characters as competition, and they win often
        enough to matter - the armadillo's own table scores 0.79 where a
        stranger's from another overlay scores 0.44, so widening got it
        wrong where the local search gets it right.

        The files are kept, since this is asked again on every animation
        opened and they do not change while a disc is."""
        if getattr(self, "_overlay_bytes", None) is None:
            self._overlay_bytes = {}
        return game_rest.load_sources(
            getattr(self.mainexe_viewer, "exe_path", None),
            self.overlay_for_area(chunk_index),
            cache=self._overlay_bytes)

    @staticmethod
    def _row_subject(text):
        """A row's name with the stem and the word Model/Animation taken
        off - "23-474F0 Pig Enemy ... Animation.ALFP" and
        "22-432EC Pig Enemy ... Model.SMST" both come back as the same
        thing, which is what lets one be paired with the other."""
        body = text.rsplit(".", 1)[0]
        body = re.sub(r"^\S+\s*", "", body)
        body = re.sub(r"\b(anmiation|animations?|models?)\b\??", "",
                      body, flags=re.I)
        return " ".join(body.lower().split())

    def _approved_models(self):
        """{animation: {model, ...}} - every model judged right for an
        animation, so a later one can be offered them first.

        This is what makes a shared animation land on a character at
        all. "NPC Animation" names no model and matches none, so it
        used to fall through to packing order and take whatever the
        area happened to pack beside it - an anemone, an armadillo, an
        asset pack. All twelve of them defaulted to something that was
        not a person. The judgements say which models really wear it."""
        if getattr(self, "_approved_by_animation", None) is None:
            out = {}
            for animation, model in pairings.load():
                out.setdefault(animation, set()).add(model)
            self._approved_by_animation = out
        return self._approved_by_animation

    def _nearness(self, item):
        """Sort key that puts models this area actually loads first.

        Comparing tree folders is not enough. A character's model can be
        a trail file, which sits in its own folder while still belonging
        to the area - Ark and Win in the Town of the Fishermen are both
        like that - so ranking by folder sent them to the Ark and Win
        models packed with the OUTRO instead, whose texture pages are
        not in this area's VRAM and which therefore came out miscoloured.
        What settles it is which areas actually load a file, which the
        IDX already says."""
        here = self._area_chunk_index(item)
        parent = item.parent()
        membership = getattr(self, "area_membership", None) or {}

        def key(row_item):
            data = row_label_data(row_item)
            address = data[2] if data else 0
            areas = membership.get(address) or ()
            return (0 if here in areas else 1,
                    0 if row_item.parent() is parent else 1,
                    address)
        return key

    def _preferred_models(self, item):
        """The SMSTs to try first for the animation on `item`, best
        guess first - [(label, address, size, trusted), ...], where
        `trusted` marks a pairing that came from the names rather than
        from the packing order, and so should be taken as given.

        An animation does not name its model, so this is inference, but
        not guesswork: two things about how the disc is built say which
        model an animation belongs to far better than any property of
        the files themselves does.

        The order the area packs its own files in. A character's model
        and its animation are written next to each other, model first -
        the Town of the Fishermen pig is SDAT id 22 with its animation
        at 23, Kainen 24 and 25, the seagull 26 and 27. Taking the SMST
        nearest above the animation in its own area gets 87 of the 92
        pairs this disc's labels can be checked against; ranking by
        "the smallest model with enough groups", which is what this
        used to do, gets 27.

        The names, where a labels file has them. That is knowledge from
        outside the disc, so it is only as good as the file - but when
        it is there it is better than any structural guess, and it
        reaches models the packing order cannot: Tomba's own model is a
        trail file shared by every area rather than an entry in any
        one area's SDAT, so nothing sits above his animation to find.

        Names win over packing order where both have an opinion, and
        the two together got all 92 of those pairs right.
        """
        out = []
        seen_addresses = set()
        seen_contents = set()

        def add(row_item, trusted):
            data = row_label_data(row_item)
            if not data or data[1] != "SMST":
                return
            entry = row_item.data(Qt.ItemDataRole.UserRole) or ()
            if not entry:
                return
            size = entry[2] if isinstance(entry[0], str) else (
                entry[3] if len(entry) > 3 else 0)
            content = data[4]
            if (not size or data[2] in seen_addresses
                    or content in seen_contents):
                return
            # Named/preferred matches used to bypass the content-based
            # dedupe in _smst_candidates, producing many identical Zippo
            # rows from the same model copied through several areas.
            seen_addresses.add(data[2])
            seen_contents.add(content)
            out.append((row_item.text(), data[2], size, trusted))

        subject = self._row_subject(item.text())
        parent = item.parent()

        # 0. Anything already judged right for this animation. A
        # judgement beats every guess below it, and one made in another
        # area still applies - see formats/animation/pairings.py on why a name
        # is what travels.
        approved = self._approved_models().get(pairings.subject(item.text()))
        if approved:
            model = self.tree_view.model()
            wanted = []
            if model is not None:
                stack = [model.invisibleRootItem()]
                while stack:
                    node = stack.pop()
                    for row in range(node.rowCount()):
                        child = node.child(row, 0)
                        stack.append(child)
                        found = row_label_data(child)
                        if (found and found[1] == "SMST"
                                and pairings.subject(child.text()) in approved):
                            wanted.append(child)
            for row_item in sorted(wanted, key=self._nearness(item)):
                add(row_item, True)

        # 1. Named the same thing, this area first, then anywhere.
        #
        # Then the same thing with the tail of the name dropped, a word
        # at a time: "Tomba Trolley Animation" is Tomba riding a
        # trolley, and the model it wants is plain "Tomba Model" - the
        # trolley is separate scenery with four groups. Whole words
        # only, longest first, so this narrows from the most specific
        # name to the least and stops at the first that names anything.
        if subject and subject != "?":
            words = subject.split()
            models = []
            model = self.tree_view.model()
            if model is not None:
                stack = [model.invisibleRootItem()]
                while stack:
                    node = stack.pop()
                    for row in range(node.rowCount()):
                        child = node.child(row, 0)
                        stack.append(child)
                        found = row_label_data(child)
                        if found and found[1] == "SMST":
                            models.append((self._row_subject(child.text()), child))
            matched = False
            for length in range(len(words), 0, -1):
                wanted = " ".join(words[:length])
                named = [c for s, c in models if s == wanted]
                if named:
                    for row_item in sorted(named, key=self._nearness(item)):
                        add(row_item, True)
                    matched = True
                    break

            # Tomba's one TANP drives every costume archive. Keep the plain
            # model first, but expose the suits in the model chooser too.
            if subject == "tomba":
                tomba_models = [c for s, c in models
                                if "tomba" in s.split()]
                near = self._nearness(item)
                for row_item in sorted(
                        tomba_models,
                        key=lambda c: (
                            0 if self._row_subject(c.text()) == "tomba" else 1,
                            near(c))):
                    add(row_item, True)

            # Failing that, a model whose name CONTAINS the animation's:
            # "Sea Anemone Animation" belongs to the models called
            # "Pink Sea Anemone segments (mouth closed)" and its two
            # open-mouthed variants, and "Mizuno Animation" to "Mizuno
            # the Witch", none of which any amount of trimming the
            # animation's own name will ever reach. Shortest names
            # first, that being the one carrying the least the
            # animation did not ask for.
            #
            # The name has to be distinctive enough to be worth
            # matching on - two words, or one long enough not to be a
            # word half the disc shares. "Mizuno" earns it; "pig" would
            # match a dozen unrelated models and does not.
            if not matched and (len(words) >= 2 or len(subject) >= 5):
                inside = [c for s, c in models if subject in s]
                near = self._nearness(item)
                for row_item in sorted(inside,
                                       key=lambda c: (near(c)[0],
                                                      len(c.text()), near(c))):
                    add(row_item, True)

        # 2. The model packed just above it in this area, then just below.
        if parent is not None:
            start = item.row()
            for step, stop in ((-1, -1), (1, parent.rowCount())):
                for row in range(start + step, stop, step):
                    sibling = parent.child(row, 0)
                    found = row_label_data(sibling)
                    if found and found[1] == "SMST":
                        add(sibling, False)
                        break
        return out

    def _smst_candidates(self, current_area=None, limit=400):
        """Every SMST on the disc, as (label, address, size) - what the
        animation viewer offers as models to pose. Taken off the tree,
        which has already typed and named everything, deduped by
        content rather than address: an area's own SDAT can carry its
        own full copy of a character it reuses, so the same model can
        otherwise show up dozens of times over, once per area that has
        it, for no reason a person picking one from a list would want."""
        model = self.tree_view.model()
        if model is None:
            return []
        rows = []
        found = []
        seen = set()

        def walk(item):
            for row in range(item.rowCount()):
                child = item.child(row)
                if child.hasChildren():
                    walk(child)
                    continue
                data = row_label_data(child)
                if not data or data[1] != "SMST":
                    continue
                address, content = data[2], data[4]
                entry = child.data(Qt.ItemDataRole.UserRole) or ()
                size = entry[3] if len(entry) > 3 else 0
                if isinstance(entry[0], str):        # a trail row
                    size = entry[2]
                if not size:
                    continue
                rows.append((0 if self._area_chunk_index(child) == current_area else 1,
                             address, content, child.text(), size))

        walk(model.invisibleRootItem())
        for _near, address, content, label, size in sorted(rows):
            if content in seen:
                continue
            seen.add(content)
            found.append((label, address, size))
        return found[:limit]

    def _model_skeletons(self, chunk_index):
        """Exact code-derived skeleton choices keyed by SMST DAT address.

        An area's actor initialization calls put the model file id and bone
        table pointer in the same instruction sequence. Unlike geometric
        fitting, this only associates combinations the game itself uses.
        """
        overlay = self.overlay_for_area(chunk_index)
        exe = getattr(self.mainexe_viewer, "exe_path", None)
        if chunk_index is None or not overlay or not exe:
            return {}
        cache = getattr(self, "_model_skeleton_cache", None)
        if cache is None:
            cache = self._model_skeleton_cache = {}
        key = (exe, overlay)
        if key not in cache:
            try:
                cache[key] = handler_models.model_skeleton_bindings(exe, overlay)
            except (OSError, ValueError, struct.error) as error:
                print(f"[ANMP] could not read code model/skeleton pairs: {error}")
                cache[key] = {}
        by_id = cache[key]
        if not by_id:
            return {}

        out = {}
        model = self.tree_view.model()
        if model is None:
            return out

        def walk(node):
            for row in range(node.rowCount()):
                child = node.child(row)
                if child.hasChildren():
                    walk(child)
                    continue
                data = row_label_data(child)
                entry = child.data(Qt.ItemDataRole.UserRole) or ()
                if (not data or data[1] != "SMST"
                        or self._area_chunk_index(child) != chunk_index
                        or not entry or not isinstance(entry[0], int)):
                    continue
                bindings = by_id.get(entry[0])
                if bindings:
                    out[data[2]] = bindings

        walk(model.invisibleRootItem())
        return out
