"""Playing a room's animated textures in a 3D view.

Two different things move, and this plays both:

  PALETTES  the game swaps a fresh 16-colour CLUT into VRAM each frame
            and every face using it changes colour together - the
            harbour's water, the fires. Tables in the area's overlay,
            read by functions/clut_anim.py.

  UVS       the artwork itself is a strip of frames side by side on one
            texture page, and the game steps the UVs across it - the
            waterfalls, the lava. Read off the page by
            functions/uv_anim.py.

Shared by the MDAT and the SMST viewer because it is the same animation
either way: one area's tables cover everything that area draws, so the
harbour's water animates whether it is being looked at as part of the
room or as parts 72-77 of the Fishermen's Town asset pack. Each viewer
supplies what differs - which palettes its model uses, and how a palette
or a UV offset is put on screen.
"""
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QStyle

from functions import clut_anim, uv_anim

# How many of the game's animation ticks to play per second. The routine
# that walks the tables counts calls, and how often it is called is in
# code functions/clut_anim.py has not followed, so nothing on the disc
# says what this should be - it is set by eye against the game. It is
# the one number here that is a guess: how long each step of an
# animation holds, in ticks, is read off the area's overlay.
TICK_HZ = 30

# Ticks each UV frame is held for. Unlike a palette animation, whose step
# lengths are in the overlay, a UV animation's rate is runtime state - the
# frame index is a byte in the drawing object's own record - so there is
# nothing on the disc to read. Set by eye, same as TICK_HZ.
UV_TICKS_PER_FRAME = 3

ANIMATE_TOOLTIP = (
    "Play this room's animated textures - the flowing water, the "
    "waterfalls, the lava, the fires.\n\n"
    "Two things move. A palette animation swaps a new 16-colour palette "
    "into VRAM and every face using it changes together. A UV animation "
    "steps the UVs across a strip of frames drawn side by side on the "
    "texture page.\n\n"
    f"Played at {TICK_HZ} ticks a second. A palette animation's step "
    "lengths come from the area's overlay; the rate they are counted at, "
    "and the whole of a UV animation's timing, are not on the disc and "
    "are set here by eye against the game.")


class ClutAnimationMixin:
    """Animated palettes for a viewer that draws grouped by palette.

    Mixed in ahead of the widget class. The viewer must call
    init_clut_animation() from its __init__, add make_animate_action()
    to its toolbar, and implement the two hooks at the bottom.
    """

    def init_clut_animation(self):
        self.clut_animations = {}   # VRAM address -> ClutAnimation
        self.uv_animations = {}     # VRAM address -> UVAnimation
        self.anim_tick = 0
        self.anim_shown = {}        # VRAM address -> frame last put on screen
        self.uv_shown = {}          # VRAM address -> UV frame last put on screen
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

    def load_animations(self, overlay_path):
        """Bind everything this model animates. Returns how many.

        Call this AFTER the model is loaded and its palette groups are
        built. An overlay's palette table covers a whole area - its
        rooms, its assets, its sprites - and only the animations whose
        CLUT this model actually points at are kept: AREA_04's overlay
        holds 13 and its room MDAT uses 7. The UV animations are read
        off the model's own texture pages instead, so they need no
        overlay at all.

        Safe with None, or with an overlay that has no table: an area
        without one, or a disc opened somewhere with no BIN folder,
        just gets no palette animation."""
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

        vram, model = self.animation_source()
        try:
            self.uv_animations = {clut: a for clut, a
                                  in uv_anim.find_animations(vram, model).items()
                                  if clut in used}
        except Exception as e:
            print(f"Could not look for UV animations: {e}")
            self.uv_animations = {}

        total = len(self.clut_animations) + len(self.uv_animations)
        if self.animate_action is not None:
            self.animate_action.setEnabled(bool(total))
        if total and self.animate_wanted:
            if self.animate_action is None or self.animate_action.isChecked():
                self.start_animation()
            else:
                self.animate_action.setChecked(True)   # starts it
        return total

    def clear_clut_animations(self):
        """Drop everything bound - for a viewer about to rebuild the
        palette textures these are attached to."""
        self.stop_animation()
        self.clut_animations = {}
        self.uv_animations = {}
        self.anim_tick = 0
        if self.animate_action is not None:
            self.animate_action.setEnabled(False)

    def toggle_animation(self, checked):
        self.animate_wanted = checked
        if checked and (self.clut_animations or self.uv_animations):
            self.start_animation()
        else:
            self.stop_animation()

    def start_animation(self):
        self._apply_animation(force=True)
        self.anim_timer.start(max(1000 // TICK_HZ, 1))

    def stop_animation(self):
        """Stop, and put everything back the way the area's own VRAM has
        it - the still frame that was on screen before any of this."""
        self.anim_timer.stop()
        if self.anim_shown:
            shown, self.anim_shown = self.anim_shown, {}
            self.apply_clut_palettes([(address, None) for address in shown])
        if self.uv_shown:
            shown, self.uv_shown = self.uv_shown, {}
            self.apply_uv_offsets({address: (0.0, 0.0) for address in shown})

    def _advance_animation(self):
        self.anim_tick += 1
        self._apply_animation()

    def _apply_animation(self, force=False):
        """Hand over whatever has moved on to a new frame.

        Most ticks change nothing - the shortest palette step on the disc
        holds for two ticks and the longest for twenty - so the frame
        each animation is showing is remembered, and only the ones that
        have actually moved are touched."""
        changed = []
        for address, animation in self.clut_animations.items():
            frame = animation.frame_at(self.anim_tick)
            if not force and self.anim_shown.get(address) == frame:
                continue
            self.anim_shown[address] = frame
            changed.append((address, animation.frames[frame]))
        if changed:
            self.apply_clut_palettes(changed)

        moved = {}
        for address, animation in self.uv_animations.items():
            frame = (self.anim_tick // UV_TICKS_PER_FRAME) % len(animation)
            if not force and self.uv_shown.get(address) == frame:
                continue
            self.uv_shown[address] = frame
            moved[address] = animation.atlas_offset_at(frame)
        if moved:
            self.apply_uv_offsets(moved)

    # --- what each viewer supplies -----------------------------------

    def animated_clut_addresses(self):
        """Every VRAM CLUT address the loaded model draws through."""
        raise NotImplementedError

    def animation_source(self):
        """(vram bytes, model_data) to look for UV animations in."""
        raise NotImplementedError

    def apply_clut_palettes(self, palettes):
        """Put palettes on screen. `palettes` is [(address, raw), ...],
        where `raw` is 32 bytes of BGR555 straight out of the overlay,
        or None meaning "back to whatever the area's VRAM holds"."""
        raise NotImplementedError

    def apply_uv_offsets(self, offsets):
        """Shift UVs on screen. `offsets` is {CLUT address: (du, dv)} as
        a fraction of the atlas; (0, 0) is the model's own UVs."""
        raise NotImplementedError
