from PyQt6.QtWidgets import QLabel, QFrame, QVBoxLayout

def label(text, role=None):
    widget = QLabel(text)
    widget.setWordWrap(True)
    if role:
        widget.setObjectName(role)
    return widget

def panel():
    frame = QFrame()
    frame.setObjectName('panel')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)
    layout.setSpacing(12)
    return frame, layout
