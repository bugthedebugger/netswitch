"""Semantic surface tokens and compact controls inspired by T3 Code."""
import os
from pathlib import Path
COLORS = {'canvas': '#111318', 'sidebar': '#17191f', 'surface': '#191c23',
          'text': '#eef0f6', 'muted': '#969cae', 'border': '#2c303b', 'accent': '#a99bff'}
STYLE = """
QWidget { background: #111318; color: #eef0f6; font-size: 13px; font-family: "Inter", "Noto Sans", sans-serif; }
QWidget#sidebar { background: #17191f; border-right: 1px solid #2b2e38; }
QWidget#sidebar QLabel { background: transparent; }
QWidget#topbar, QWidget#actionbar { background: #14161c; border-bottom: 1px solid #292d37; }
QWidget#actionbar { border-top: 1px solid #292d37; border-bottom: none; }
QLabel { background: transparent; }
QLabel#brand { font-size: 18px; font-weight: 650; }
QLabel#title { font-size: 25px; font-weight: 650; }
QLabel#heading { font-size: 14px; font-weight: 600; }
QLabel#metric { font-size: 24px; font-weight: 600; }
QLabel#muted { color: #969cae; font-size: 12px; }
QLabel#eyebrow { color: #747e94; font-size: 10px; font-weight: 600; }
QLabel#badge { color: #bdb0ff; background: #2c2642; border-radius: 5px; padding: 4px 8px; }
QFrame#panel { background: #191c23; border: 1px solid #2c303b; border-radius: 10px; }
QFrame#panel QLabel, QFrame#panel QCheckBox { background: transparent; }
QLineEdit, QComboBox { background: #1c2029; border: 1px solid #343b49; border-radius: 6px; padding: 9px 10px; }
QLineEdit:focus, QComboBox:focus { border-color: #a99bff; }
QPushButton { background: #242833; border: 1px solid #343b49; border-radius: 6px; padding: 9px 14px; font-weight: 500; }
QPushButton:hover { background: #303647; border-color: #545b70; }
QPushButton:disabled { color: #656b7b; background: #1d2028; border-color: #292d37; }
QPushButton#primary { background: #ad9fff; color: #191426; border: 1px solid #ad9fff; font-weight: 600; }
QPushButton#primary:hover { background: #beb3ff; border-color: #beb3ff; }
QPushButton#primary:disabled { background: #272631; color: #686375; border-color: #272631; }
QPushButton#ghost, QPushButton#nav { background: transparent; border: none; color: #a4abbd; text-align: left; padding: 10px 12px; }
QPushButton#ghost:hover, QPushButton#nav:hover { background: #242833; color: #eef0f6; }
QPushButton#nav:checked { background: #2b2740; color: #cbbfff; }
QPushButton#danger { color: #c7a1aa; background: transparent; border: none; }
QPushButton#danger:hover { background: #35242b; color: #f5b5c1; }
QPushButton#network { background: #1d2029; border: 1px solid #363c49; text-align: left; padding: 12px 16px; font-size: 13px; }
QPushButton#network:checked { background: #2a263c; border: 1px solid #a99bff; color: #dacfff; }
QPushButton#network:hover { border-color: #8a80b5; }
QCheckBox { spacing: 10px; color: #d1d5df; }
QCheckBox:disabled { color: #6c7281; }
QCheckBox::indicator { width: 18px; height: 18px; }
QCheckBox::indicator:unchecked { background: #20242e; border: 1px solid #4a5263; border-radius: 5px; }
QCheckBox::indicator:checked { background: #ad9fff; border: 1px solid #c4baff; border-radius: 5px; image: url(CHECK_ICON); }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 11px 8px; border-radius: 6px; color: #aeb5c5; }
QListWidget::item:selected { background: #292d39; color: #f2f3f8; }
QListWidget::item:hover { background: #222630; }
QScrollArea { border: none; }
QTableWidget { background: #191c23; border: 1px solid #2c303b; border-radius: 8px; }
QTableWidget::item { padding: 10px; border-bottom: 1px solid #2a2e38; }
QHeaderView::section { background: #1e222b; color: #8993a9; border: none; padding: 12px 10px; font-size: 11px; text-align: left; }
QScrollBar:vertical { background: transparent; width: 6px; }
QScrollBar::handle:vertical { background: #3c4353; border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal { background: transparent; height: 6px; }
QScrollBar::handle:horizontal { background: #3c4353; border-radius: 3px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
QToolTip { color: #eef0f6; background: #252a36; border: 1px solid #40495c; padding: 8px; }
"""

def apply_theme(app):
    from PyQt6.QtGui import QIcon
    app.setStyle('Fusion')
    check = str(Path(__file__).resolve().parent.parent / 'assets/check.svg')
    app.setStyleSheet(STYLE.replace('CHECK_ICON', '"' + check + '"'))
    # Qt's offscreen and minimal platform plugins omit Linux icon paths.
    roots = [str(Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'icons')]
    roots += [str(Path(p) / 'icons') for p in os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':')]
    QIcon.setThemeSearchPaths(list(dict.fromkeys(roots + QIcon.themeSearchPaths())))
    if not QIcon.themeName():
        QIcon.setThemeName('breeze-dark')
    QIcon.setFallbackThemeName('hicolor')
