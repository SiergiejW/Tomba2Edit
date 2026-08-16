import sys
from PyQt6.QtWidgets import QApplication

version = "0.2.1"

def main():
    print(f"Tomba2Edit ver{version}")
    from gui.main_window import MainWindow  # Move the import here to avoid circular import
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    app.exec()

if __name__ == "__main__":
    main()


