"""Small, subtle caption label placed above a tree or editor panel, so
each pane in a multi-column layout (disc tree / master-entry tree /
text editor) is visibly labeled without competing with the actual
content below it - and the status line along the bottom of a viewer,
which needs holding back from the layout."""

from PyQt6.QtWidgets import QLabel, QSizePolicy


def make_panel_title(text):
    # Styled by gui/theme.py under PANEL_TITLE, so a theme switch reaches it.
    label = QLabel(text)
    label.setObjectName("panelTitle")
    return label


def make_info_label(text=""):
    """The status line at the bottom of a viewer.

    A QLabel's minimum width is the width of its text, so a status line
    describing whatever file is open becomes the minimum width of its
    whole viewer - and, through the QStackedWidget every viewer shares,
    the minimum width of the pane they all sit in. That is what makes
    the disc tree jump narrower when a DRWA is opened and narrower again
    with each longer line set: the tree is being squeezed out by a
    sentence. Ignored horizontally takes the label out of that sum
    entirely; a line too long for the window is clipped instead, and
    set_info() keeps the whole of it in the tooltip."""
    label = QLabel(text)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.setToolTip(text)
    return label


def set_info(label, text):
    """Set a make_info_label()'s text, full version in the tooltip."""
    label.setText(text)
    label.setToolTip(text)
