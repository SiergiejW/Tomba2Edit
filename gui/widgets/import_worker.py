"""Run the CPU-heavy texture allocator while keeping the dialog responsive."""
from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QProgressDialog


class ImportWorker(QThread):
    message = pyqtSignal(str)

    def __init__(self, task, reporting=False):
        super().__init__()
        self.task, self.result, self.error = task, None, None
        self.reporting = reporting

    def run(self):
        try:
            self.result = (self.task(self.message.emit, self.isInterruptionRequested)
                           if self.reporting else self.task())
        except Exception as exc:
            self.error = exc


def run_import(task, parent, reporting=False):
    progress = QProgressDialog('Packing model textures into available game VRAM…', '', 0, 0, parent)
    progress.setWindowTitle('Preparing model import')
    progress.setWindowModality(Qt.WindowModality.ApplicationModal)
    if not reporting:
        progress.setCancelButton(None)
    else:
        progress.setCancelButtonText('Cancel import')
    progress.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    worker = ImportWorker(task, reporting)
    worker.message.connect(progress.setLabelText)
    progress.canceled.connect(worker.requestInterruption)
    worker.finished.connect(progress.accept)
    worker.start()
    progress.exec()
    worker.wait()
    if worker.error:
        raise worker.error
    return worker.result
