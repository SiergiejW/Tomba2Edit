"""Run the CPU-heavy texture allocator while keeping the dialog responsive."""
from PyQt6.QtCore import QThread, Qt
from PyQt6.QtWidgets import QProgressDialog


class ImportWorker(QThread):
    def __init__(self, task):
        super().__init__()
        self.task, self.result, self.error = task, None, None

    def run(self):
        try:
            self.result = self.task()
        except Exception as exc:
            self.error = exc


def run_import(task, parent):
    progress = QProgressDialog('Packing model textures into available game VRAM…', '', 0, 0, parent)
    progress.setWindowTitle('Preparing model import')
    progress.setWindowModality(Qt.WindowModality.ApplicationModal)
    progress.setCancelButton(None)
    progress.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    worker = ImportWorker(task)
    worker.finished.connect(progress.accept)
    worker.start()
    progress.exec()
    worker.wait()
    if worker.error:
        raise worker.error
    return worker.result
