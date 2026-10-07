"""Read-only live routing view; counters are outbound, never invented downloads."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QGridLayout, QHeaderView, QHBoxLayout,
                            QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from ..apps import network_name
from .widgets import label, panel

def bytes_text(value):
    value = max(0, int(value))
    amount = float(value)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if amount < 1024 or unit == 'TB':
            return f'{int(amount)} B' if unit == 'B' else f'{amount:.1f} {unit}'
        amount /= 1024

class UsagePage(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        content = QWidget()
        self.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 26, 30, 24)
        layout.setSpacing(20)
        layout.addWidget(label('Live routing', 'title'))
        layout.addWidget(label('See which network your active applications are using.', 'muted'))
        self.default = label('', 'muted')
        layout.addWidget(self.default)
        self.connections = QGridLayout()
        self.connections.setSpacing(12)
        layout.addLayout(self.connections)
        heading = QHBoxLayout()
        heading.addWidget(label('Active applications', 'heading'), 1)
        self.count = label('0 active', 'muted')
        heading.addWidget(self.count)
        layout.addLayout(heading)
        self.empty = label('No routed apps are running. Open an app with an automatic rule, or launch one through Net Switch.', 'muted')
        self.empty_panel, empty_layout = panel()
        self.empty_panel.setMinimumHeight(140)
        empty_layout.addStretch()
        empty_layout.addWidget(label('No active routes', 'heading'))
        empty_layout.addWidget(self.empty)
        empty_layout.addStretch()
        layout.addWidget(self.empty_panel)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['App', 'Using now', 'Preferred', 'Backup', 'Sent', 'Routing'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self.table)
        note = label('Outgoing traffic only · Updates every 3 seconds · Other apps aren’t individually tracked', 'muted')
        note.setToolTip('Sent counters start again when routing rules change. They are not a billing total and do not include downloads.')
        layout.addWidget(note)
        layout.addStretch()

    def update_routes(self, sessions, devices, default_name, title_for, ready=True):
        by_name = {d['name']: d for d in devices}
        def friendly(name):
            return network_name(by_name[name]) if name in by_name else name or 'None'
        self.default.setText(f'System default  ·  {friendly(default_name)}' if default_name else 'No default connection')
        while self.connections.count():
            item = self.connections.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        names = [d['name'] for d in devices if d['kind'] == 'Wi-Fi' or d['name'].startswith(('en', 'eth'))]
        names += sorted({s['active'] for s in sessions if s.get('active')} - set(names))
        for index, name in enumerate(names):
            active = [s for s in sessions if s.get('active') == name]
            sent = sum(s.get('traffic', {}).get('routed', {}).get('bytes', 0) for s in active)
            card, card_layout = panel()
            header = QHBoxLayout()
            header.addWidget(label(friendly(name), 'heading'), 1)
            if name == default_name:
                header.addWidget(label('DEFAULT', 'badge'))
            card_layout.addLayout(header)
            state = by_name.get(name, {}).get('internet')
            subtitle = {'online': 'Internet available', 'offline': 'No internet', 'checking': 'Checking internet…', 'disconnected': 'Disconnected'}.get(state, 'Connected' if by_name.get(name, {}).get('connected') else 'Unavailable')
            card_layout.addWidget(label(subtitle, 'muted'))
            metric = QHBoxLayout()
            metric.addWidget(label(str(len(active)), 'metric'))
            metric.addWidget(label(f'active {"app" if len(active) == 1 else "apps"}', 'muted'), 1)
            metric.addWidget(label(f'{bytes_text(sent)} sent', 'muted'))
            card_layout.addLayout(metric)
            self.connections.addWidget(card, index // 2, index % 2)
        self.count.setText(f'{len(sessions)} active')
        self.empty.setVisible(not sessions)
        self.empty_panel.setVisible(not sessions)
        self.empty.setText('No routed apps are running. Open an app with an automatic rule, or launch one through Net Switch.' if ready else
                           'The routing service is unavailable. Start it to see current routes.')
        self.table.setVisible(bool(sessions))
        self.table.setRowCount(len(sessions))
        self.table.setMinimumHeight(min(400, 50 + len(sessions) * 58))
        for row, session in enumerate(sessions):
            traffic = session.get('traffic', {})
            blocked = traffic.get('blocked', {}).get('packets', 0)
            values = [title_for(session), friendly(session['active']) if session.get('active') else 'Waiting',
                      friendly(session.get('interface')), friendly(session.get('fallback')),
                      bytes_text(traffic.get('routed', {}).get('bytes', 0)),
                      'Automatic' if session.get('automatic') else 'Net Switch launch']
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 1:
                    item.setForeground(QColor('#a6e1c9' if session.get('active') else '#e9bd83'))
                item.setToolTip(f"Session: {session['id']}\nProcess: {session['pid']}\nBlocked packets: {blocked}\n" +
                                ('Waiting for an available selected connection.' if not session.get('active') else f"Adapter: {session['active']}"))
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 58)
