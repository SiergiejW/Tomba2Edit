"""Undo/redo for the Level Editor's edits.

A snapshot holds exactly what those edits change: which instances and
pickups exist, every instance's and record's fields, the model bindings.
Records and instances are restored in place, so every reference to them
(an instance's placement, a pickup's contents) stays valid. Restoring
rebuilds the scene the way an added pickup does."""
import copy
import time

LIMIT = 100
# Edits to one box within this long are one step, not one per keystroke.
COALESCE = 1.5


def _state(obj):
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {name: getattr(obj, name) for name in getattr(obj, "__slots__", ())}


def _put(obj, state):
    if hasattr(obj, "__dict__"):
        obj.__dict__.clear()
        obj.__dict__.update(state)
    else:
        for name, value in state.items():
            setattr(obj, name, value)


class LevelUndo:
    def __init__(self, panel):
        self.panel = panel
        self.undo_stack, self.redo_stack = [], []
        self._last = (None, 0.0)
        self.changed = None          # callable, told when the stacks change

    def clear(self):
        self.undo_stack.clear(); self.redo_stack.clear(); self._last = (None, 0.0)
        self._notify()

    def snapshot(self):
        scene = self.panel.scene
        return {
            "instances": list(scene.instances),
            "instance_state": [(i, _state(i)) for i in scene.instances],
            "pickups": list(scene.pickups),
            "pickup_state": [(r, _state(r)) for r in scene.pickups],
            "placement_state": [(r, _state(r)) for r in scene.placements],
            "bindings": copy.copy(scene.bindings),
            "selected": self.panel.viewer.selected,
        }

    def remember(self, key=None):
        """Call before an edit. `key` merges quick repeats (one box)."""
        if self.panel.scene is None:
            return
        now = time.monotonic()
        if key is not None and key == self._last[0] and now - self._last[1] < COALESCE:
            self._last = (key, now)
            return
        self._last = (key, now)
        self.undo_stack.append(self.snapshot())
        del self.undo_stack[:-LIMIT]
        self.redo_stack.clear()
        self._notify()

    def forget_last(self):
        """A drag that was cancelled changed nothing."""
        if self.undo_stack:
            self.undo_stack.pop()
        self._last = (None, 0.0)
        self._notify()

    def undo(self):
        self._step(self.undo_stack, self.redo_stack)

    def redo(self):
        self._step(self.redo_stack, self.undo_stack)

    def _step(self, source, target):
        if not source or self.panel.scene is None:
            return
        target.append(self.snapshot())
        self.restore(source.pop())
        self._last = (None, 0.0)
        self._notify()

    def restore(self, snap):
        panel = self.panel
        scene = panel.scene
        panel.stop_event_preview()
        scene.instances[:] = snap["instances"]
        for obj, state in snap["instance_state"]:
            _put(obj, state)
        scene.pickups[:] = snap["pickups"]
        for obj, state in snap["pickup_state"] + snap["placement_state"]:
            _put(obj, state)
        scene.bindings.clear()
        scene.bindings.update(snap["bindings"])
        # Staged files may hold the undone state: the next apply restages.
        panel._placement_dirty = True
        selected = snap["selected"]
        if selected is not None and not 0 <= selected < len(scene.instances):
            selected = None
        panel._refresh_edited_scene(selected)

    def _notify(self):
        if self.changed:
            self.changed()
