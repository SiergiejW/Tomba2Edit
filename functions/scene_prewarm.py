"""Build exact Level Editor caches outside the UI process.

Invoked by LevelEditorPanel's ``Preload levels`` button. Each worker runs
the same LevelScene.load path as an interactive open; no reduced frame count
or alternate parser is involved. Once Fresh and Events done exist, building
Both is a cheap exact merge of those cached scenes.
"""
import concurrent.futures
import contextlib
import json
import os
import sys

from functions import scene_cache
from gui.level.level_scene import BOTH, EVENTS_DONE, FRESH, LevelScene


def _build(job):
    dat, idx, chunk, overlay, exe = job
    # Interactive loads explain each simulation stage. A bulk preload would
    # turn that into thousands of lines, so workers stay quiet and the parent
    # emits exactly one useful completion line per area.
    with open(os.devnull, "w") as quiet:
        with (contextlib.redirect_stdout(quiet),
              contextlib.redirect_stderr(quiet)):
            fresh = LevelScene().load(dat, idx, chunk, overlay, exe, FRESH)
            done = LevelScene().load(dat, idx, chunk, overlay, exe,
                                     EVENTS_DONE)
    # This is exactly LevelScene.load(BOTH)'s merge, but both expensive actor
    # runs are already in hand. Avoid simulating Fresh a third time merely to
    # create the merged cache entry.
    fresh._merge(done)
    fresh.progress = BOTH
    name = scene_cache.key(dat, idx, chunk, overlay, exe, BOTH)
    scene_cache.write(name, {k: v for k, v in fresh.__dict__.items()
                             if k != "world"})
    return chunk


def main(path):
    with open(path, encoding="utf-8") as source:
        jobs = json.load(source)
    # Scene worlds are large. Two processes use separate CPU cores without
    # multiplying peak RAM as aggressively as a worker per logical core.
    workers = min(2, max(1, os.cpu_count() or 1), len(jobs))
    failed = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_build, tuple(job)): job[2] for job in jobs}
        for future in concurrent.futures.as_completed(futures):
            chunk = futures[future]
            try:
                future.result()
                result = f"Preloaded level AREA_{chunk:02X}"
            except Exception as error:
                result = f"Failed to preload level AREA_{chunk:02X}: {error}"
                failed += 1
            print(result, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
