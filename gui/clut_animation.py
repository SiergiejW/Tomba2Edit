"""Playing an area's animated palettes in a 3D view.

The flowing water, the waterfalls, the fires. None of the artwork moves:
the game swaps a fresh 16-colour palette into VRAM each frame, and every
face pointing at that palette changes colour together. Where the tables
come from and how they were read is in functions/clut_anim.py; this is
only the playing of them.

Shared by the MDAT and the SMST viewer because it is the same
animation - one area's table covers everything that area draws, so the
harbour's water animates whether it is being looked at as part of the
room or as parts 72-77 of the Fishermen's Town asset pack. Each viewer
supplies the two things that differ: which palettes its loaded model
uses, and how a palette is put on screen.
"""
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QStyle

from functions import clut_anim

# How many of the game's animation ticks to play per second. The routine
# that walks the tables counts calls, and how often it is called is in
# code functions/clut_anim.py has not followed, so nothing on the disc
# says what this should be - it is set by eye against the game. It is
# the one number here that is a guess: how long each step of an
# animation holds, in ticks, is read off the area's overlay.
TICK_HZ = 30

ANIMATE_TOOLTIP = (
    "Play this room's animated palettes - the flowing water, the "
    "waterfalls, the fires. The artwork does not move: the game swaps a "
    "new 16-colour palette into VRAM each frame and every face using it "
    "changes together.\n\n"
    f"Played at {TICK_HZ} ticks a second. How long each step holds, in "
    "ticks, comes from the area's overlay; the rate those ticks are "
    "counted at is not on the disc, and is set here by eye against the "
    "game.")


class ClutAnimationMixin:
    """Animated palettes for a viewer that draws grouped by palette.

    Mixed in ahead of the widget class. The viewer must call
    init_clut_animation() from its __init__, add make_animate_action()
    to its toolbar, and implement the two hooks at the bottom.
    """

    def init_clut_animation(self):
        self.clut_animations = {}   # VRAM address -> ClutAnimation
        self.anim_tick = 0
        self.anim_shown = {}        # VRAM address -> frame last put on screen
        # Whether animation should start on its own when a model that
        # has some is opened. Turned off by unticking the toolbar
        # button, so a deliberately still view stays still from file to
        # file.
        self.animate_wanted = True
        self.animate_action = None
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._advance_animation)

    def make_animate_action(self):
        """The toolbar toggle - off, and disabled, until something with
        animated palettes has been opened."""
        action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
            "Animate", self)
        action.setCheckable(True)
        action.setEnabled(False)
        action.setToolTip(ANIMATE_TOOLTIP)
        action.toggled.connect(self.toggle_animation)
        self.animate_action = action
        return action

    def load_clut_animations(self, overlay_path):
        """Bind the animated palettes this model uses, out of the area's
        overlay. Returns how many were bound.

        Call this AFTER the model is loaded and its palette groups are
        built: an overlay's table covers a whole area - its rooms, its
        assets, its sprites - and only the animations whose CLUT this
        model actually points at are kept. AREA_04's overlay holds 13;
        its room MDAT uses 7 of them.

        Safe to call with None, or with a path that has no table: an
        area with no overlay, or a disc opened somewhere without a BIN
        folder, simply gets no animation."""
        self.clear_clut_animations()
        found = []
        if overlay_path:
            try:
                _base, found = clut_anim.load_animations(overlay_path)
            except OSError:
                found = []
            except Exception as e:
                print(f"Could not read palette animations from "
                      f"{overlay_path}: {e}")
        used = set(self.animated_clut_addresses())
        # Where two records drive the same CLUT - which happens, and the
        # game runs both - the last one written is the one on screen.
        for animation in found:
            if animation.address in used:
                self.clut_animations[animation.address] = animation

        if self.animate_action is not None:
            self.animate_action.setEnabled(bool(self.clut_animations))
        if self.clut_animations and self.animate_wanted:
            if self.animate_action is None or self.animate_action.isChecked():
                self.start_animation()
            else:
                self.animate_action.setChecked(True)   # starts it
        return len(self.clut_animations)

    def clear_clut_animations(self):
        """Drop everything bound - for a viewer about to rebuild the
        palette textures these are attached to."""
        self.stop_animation()
        self.clut_animations = {}
        self.anim_tick = 0
        if self.animate_action is not None:
            self.animate_action.setEnabled(False)

    def toggle_animation(self, checked):
        self.animate_wanted = checked
        if checked and self.clut_animations:
            self.start_animation()
        else:
            self.stop_animation()

    def start_animation(self):
        self._apply_animation(force=True)
        self.anim_timer.start(max(1000 // TICK_HZ, 1))

    def stop_animation(self):
        """Stop, and put back what the area's own VRAM holds - the one
        frame of each animation that was on screen before any of this."""
        self.anim_timer.stop()
        if not self.anim_shown:
            return
        shown, self.anim_shown = self.anim_shown, {}
        self.apply_clut_palettes([(address, None) for address in shown])

    def _advance_animation(self):
        self.anim_tick += 1
        self._apply_animation()

    def _apply_animation(self, force=False):
        """Hand over the palettes that have moved on to a new frame.

        Most ticks change nothing - the shortest step on the disc holds
        for two ticks and the longest for twenty - so the frame each
        palette is showing is remembered, and only the ones that have
        actually moved are touched."""
        if not self.clut_animations:
            return
        changed = []
        for address, animation in self.clut_animations.items():
            frame = animation.frame_at(self.anim_tick)
            if not force and self.anim_shown.get(address) == frame:
                continue
            self.anim_shown[address] = frame
            changed.append((address, animation.frames[frame]))
        if changed:
            self.apply_clut_palettes(changed)

    # --- what each viewer supplies -----------------------------------

    def animated_clut_addresses(self):
        """Every VRAM CLUT address the loaded model draws through."""
        raise NotImplementedError

    def apply_clut_palettes(self, palettes):
        """Put palettes on screen. `palettes` is [(address, raw), ...],
        where `raw` is 32 bytes of BGR555 straight out of the overlay,
        or None meaning "back to whatever the area's VRAM holds"."""
        raise NotImplementedError
