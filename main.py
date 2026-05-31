"""程序入口。"""
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from app.book_scheme import register_scheme
from app.main_window import MainWindow

def main() -> int:
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    register_scheme()
    app = QApplication(sys.argv)
    app.setApplicationName("PySave EPUB Editor")
    app.setOrganizationName("PySave")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
