"""Builds one area's events-done scene in a process of its own.

"Both" needs the area run twice - a fresh game and one with every event done
- and the two share nothing. This is the second run: started by
LevelScene.load, it builds the scene and leaves it in game/scene_cache.py,
where the load that started it picks it up. Nothing is handed back, so there
is nothing to go wrong between the two: if this never finishes, the load
simply builds it itself.

usage: python -m gui.level.done_worker <dat> <idx> <chunk> <overlay> <exe>
"""
import os
import sys


def main(argv):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from gui.level.level_scene import EVENTS_DONE, LevelScene
    dat_path, idx_path, chunk, overlay_path, exe_path = argv
    LevelScene().load(dat_path, idx_path, int(chunk), overlay_path, exe_path, EVENTS_DONE)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
