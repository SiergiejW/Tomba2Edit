"""Small, subtle caption label placed above a tree or editor panel, so
each pane in a multi-column layout (disc tree / master-entry tree /
text editor) is visibly labeled without competing with the actual
content below it."""

from PyQt6.QtWidgets import QLabel


def make_panel_title(text):
    label = QLabel(text)
    label.setStyleSheet("color: #b8b8b8; font-size: 11px; padding: 2px 4px;")
    return label
