import sys
from PyQt6.QtWidgets import QApplication

version = "0.3.5"


def _install_crash_report():
    """Make an unhandled exception say something before it kills us.

    PyQt6 does not let an exception raised inside a slot propagate: it
    calls qFatal(), which calls abort(), and Windows reports that as
    0xC0000409 with nothing printed and every pending edit gone. A one
    line typo therefore looks exactly like a memory corruption bug.

    sys.excepthook still runs first, so this is the last chance to say
    what actually happened. The traceback also goes to a file beside the
    program, because the abort follows immediately and a dialog is no
    use to someone who was not watching."""
    import os
    import traceback

    log = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "crash.log")

    def hook(kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        sys.stderr.write(text)
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass
        try:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None, "Tomba2Edit hit a bug",
                f"{kind.__name__}: {value}\n\n"
                f"The full traceback is in:\n{log}\n\n"
                "The program has to close now. Anything already written "
                "to disc is fine; unsaved edits are not.")
        except Exception:
            pass

    sys.excepthook = hook


def main():
    print(f"Tomba2Edit ver{version}")
    _install_crash_report()
    from gui.main_window import MainWindow  # Move the import here to avoid circular import
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    app.exec()

if __name__ == "__main__":
    main()

