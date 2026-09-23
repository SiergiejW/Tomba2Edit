"""Zippo, standing next to whatever the panel is telling you.

He is a 34x42 sprite lifted off the disc, so he is scaled by whole
numbers with nearest-neighbour and never smoothed - a half pixel of
blur is the difference between a PlayStation sprite and a smudge.

One module rather than the same dozen lines in six panels: he turns up
beside the status line on Music, SFX, Movies, MAIN.EXE and TXTD, and at
the bottom of the sequence editor, and they should all get the same
Zippo at the same size.
"""
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

SPRITE = (34, 42)
SCALE = 2               # what the sequence editor settled on

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "icons", "tomba", "zippo.png")
# Loaded once and shared: the same few kilobytes in six panels.
_cache = {}


def pixmap(scale=SCALE):
    """Zippo at a whole-number scale, or a null pixmap if he is missing."""
    if scale in _cache:
        return _cache[scale]
    picture = QPixmap()
    if os.path.isfile(_PATH):
        loaded = QPixmap(_PATH)
        if not loaded.isNull():
            picture = loaded.scaled(
                SPRITE[0] * scale, SPRITE[1] * scale,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation)
    _cache[scale] = picture
    return picture


def label(scale=SCALE, align=None):
    """Zippo as a QLabel, sized to himself so he never stretches.

    Missing artwork gives an empty label rather than nothing at all:
    the layout it sits in should not change shape depending on whether
    the icons folder came along.
    """
    holder = QLabel()
    picture = pixmap(scale)
    if not picture.isNull():
        holder.setPixmap(picture)
        holder.setFixedSize(picture.size())
    holder.setAlignment(align or (Qt.AlignmentFlag.AlignBottom
                                  | Qt.AlignmentFlag.AlignLeft))
    return holder


def beside(widget, scale=SCALE, spacing=8, stretch=True):
    """`widget` with Zippo standing to its left, as one widget.

    For the common case - a panel whose status line should have him
    next to it - so a caller swaps one addWidget for another instead of
    unpicking its layout.
    """
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    row.addWidget(label(scale))
    row.addWidget(widget, 1 if stretch else 0)
    if not stretch:
        row.addStretch(1)
    return holder
