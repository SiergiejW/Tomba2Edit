"""Shared prompts for importing a replacement voice clip.

A clip's length on disc is fixed by the overlay's own table (or, for a
whole channel, by how many sectors it already occupies) - nothing here
can grow or shrink that without rewriting the overlay's code, which is
out of scope. So a replacement that doesn't match exactly has to be
padded with silence or cut to fit, and the user gets asked which before
either happens.
"""
from PyQt6.QtWidgets import QMessageBox


def confirm_length(parent, have_samples, need_samples, rate=18900):
    """True if the caller should go ahead - staging pads/cuts to fit
    once this says so, in functions.voice_edit.VoiceEditStore.stage_clip."""
    if have_samples == need_samples:
        return True
    have_s, need_s = have_samples / rate, need_samples / rate
    if have_samples < need_samples:
        return QMessageBox.question(
            parent, "Shorter than the original",
            f"The replacement is {have_s:.2f}s; the space on disc is "
            f"{need_s:.2f}s. Fill the rest with silence?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes
    return QMessageBox.question(
        parent, "Longer than the original",
        f"The replacement is {have_s:.2f}s; the space on disc only holds "
        f"{need_s:.2f}s. Cut it down to fit?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    ) == QMessageBox.StandardButton.Yes
