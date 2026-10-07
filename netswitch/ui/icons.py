from pathlib import Path
from PyQt6.QtGui import QIcon

ICON_PATH = Path(__file__).resolve().parents[1] / 'assets/netswitch.svg'

def app_icon():
    return QIcon(str(ICON_PATH))
