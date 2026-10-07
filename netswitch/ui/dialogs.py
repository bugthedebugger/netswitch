from pathlib import Path
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QLineEdit,
                            QListWidget, QListWidgetItem, QPushButton, QVBoxLayout)
from ..apps import installed_apps
from .widgets import label

class AppPicker(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Choose an app')
        self.resize(520, 560)
        self.selected_app = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(label('Add application', 'title'))
        layout.addWidget(label('Choose an installed app or game to set its network.', 'muted'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search apps and games…')
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setIconSize(QSize(28, 28))
        for app in installed_apps():
            item = QListWidgetItem(QIcon.fromTheme(app['icon']), app['name'])
            item.setData(Qt.ItemDataRole.UserRole, app)
            self.list.addItem(item)
        layout.addWidget(self.list, 1)
        self.empty = label('No matching apps. Choose an app file below.', 'muted')
        self.empty.hide()
        layout.addWidget(self.empty)
        self.search.textChanged.connect(self.filter)
        self.list.itemDoubleClicked.connect(lambda _: self.choose())
        browse = QPushButton('Choose executable file…')
        browse.clicked.connect(self.browse)
        layout.addWidget(browse)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Choose app')
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName('primary')
        buttons.accepted.connect(self.choose)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def filter(self, text):
        visible = 0
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(text.casefold() not in item.text().casefold())
            visible += not item.isHidden()
        self.empty.setVisible(not visible)

    def choose(self):
        item = self.list.currentItem()
        if item and not item.isHidden():
            self.selected_app = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose an app', str(Path.home()), 'Apps (*)')
        if path:
            self.selected_app = {'name': Path(path).stem, 'icon': '', 'command': [path], 'steam_id': None}
            self.accept()
