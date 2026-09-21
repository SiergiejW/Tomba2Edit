"""Qt bridge: process IO and snapshot deserialization never run in the UI."""
import json
import os
import pickle
import subprocess
import sys
import tempfile
import threading

from PyQt6.QtCore import QThread, pyqtSignal


class LevelLoad(QThread):
    stage = pyqtSignal(object)
    status = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, args, parent=None):
        super().__init__(parent)
        self.args = args
        self.cancelled = threading.Event()
        self.process = None

    def cancel(self):
        self.cancelled.set()
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()

    def run(self):
        try:
            with tempfile.TemporaryDirectory(prefix="tomba-level-load-") as directory:
                manifest = os.path.join(directory, "request.json")
                with open(manifest, "w", encoding="utf-8") as f:
                    json.dump(self.args, f)
                command = ([sys.executable, "--level-load-worker"] if getattr(sys, "frozen", False)
                           else [sys.executable, "-m", "gui.level.load_worker"])
                with open(os.path.join(directory, "worker.log"), "w+") as errors:
                    self.process = subprocess.Popen(command + [manifest, directory],
                        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                        stdout=subprocess.PIPE, stderr=errors, text=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if self.cancelled.is_set():
                        self.process.terminate()
                    for line in self.process.stdout:
                        if self.cancelled.is_set():
                            break
                        name = line.strip()
                        if name.startswith("status "):
                            self.status.emit(name[len("status "):])
                            continue
                        if not (name.startswith("stage-") and name.endswith(".pickle")
                                and os.path.basename(name) == name):
                            continue
                        path = os.path.join(directory, name)
                        with open(path, "rb") as f:
                            payload = pickle.load(f)
                        os.remove(path)
                        if not self.cancelled.is_set():
                            self.stage.emit(payload)
                    self.process.wait()
                    if self.process.returncode and not self.cancelled.is_set():
                        errors.seek(0)
                        self.failed.emit(errors.read()[-4000:])
        except Exception as error:
            if not self.cancelled.is_set():
                self.failed.emit(str(error))
        finally:
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.kill()
                self.process.wait()
