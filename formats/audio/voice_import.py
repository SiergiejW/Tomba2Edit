"""Shared prompts for importing a replacement voice clip.

A clip's length on disc is fixed by the overlay's own table (or, for a
whole channel, by how many sectors it already occupies) - nothing here
can grow or shrink that without rewriting the overlay's code, which is
out of scope. So a replacement that doesn't match exactly has to be
padded with silence or cut to fit, and the user gets asked which before
either happens.
"""
from PyQt6.QtWidgets import QMessageBox


_BUTTONS = (QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
           | QMessageBox.StandardButton.Cancel)


def confirm_length(parent, have_samples, need_samples, rate=18900):
    """True if the caller should go ahead - staging pads/cuts to fit
    once this says so, in formats.audio.voice_edit.VoiceEditStore.stage_clip.

    No and Cancel do the same thing - the caller aborts the import
    either way - but Cancel carries QMessageBox's actual Cancel role,
    which is what makes pressing Escape or closing the dialog with its
    own X button behave the same way instead of being left ambiguous."""
    if have_samples == need_samples:
        return True
    have_s, need_s = have_samples / rate, need_samples / rate
    if have_samples < need_samples:
        return QMessageBox.question(
            parent, "Shorter than the original",
            f"The replacement is {have_s:.2f}s; the space on disc is "
            f"{need_s:.2f}s. Fill the rest with silence?",
            _BUTTONS, QMessageBox.StandardButton.Cancel
        ) == QMessageBox.StandardButton.Yes
    return QMessageBox.question(
        parent, "Longer than the original",
        f"The replacement is {have_s:.2f}s; the space on disc only holds "
        f"{need_s:.2f}s. Cut it down to fit?",
        _BUTTONS, QMessageBox.StandardButton.Cancel
    ) == QMessageBox.StandardButton.Yes
