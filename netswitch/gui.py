"""Desktop entry point; presentation lives in ui/, app discovery in apps.py."""
import sys
from PyQt6.QtWidgets import QApplication
from .ui.theme import STYLE, apply_theme
from .ui.window import Window
from .ui.icons import app_icon

def main():
    app = QApplication(sys.argv)
    app.setDesktopFileName('netswitch')
    app.setApplicationName('NetSwitch')
    app.setApplicationDisplayName('Net Switch')
    app.setWindowIcon(app_icon())
    apply_theme(app)
    window = Window()
    window.show()
    return app.exec()
