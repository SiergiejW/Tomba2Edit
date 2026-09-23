"""Odds and ends the window and its mixins share."""

# Colors used to flag a file's row in the main file tree,
# mirroring the entry-level coloring in TXTDViewer/TXT2Viewer: orange
# while it has pending (unexported) text edits, green once those edits
# have been exported.
EDITED_TXTD_ITEM_COLOR = "orange"
EXPORTED_TXTD_ITEM_COLOR = "green"


# The area whose VRAM chunk holds the art every area shares - the
# character models' texture pages live only here. See
# _load_area_vram_bytes(merge_common=True).
COMMON_VRAM_AREA = 1


def _panel_player(panel):
    """The QMediaPlayer a tab makes sound through.

    Dialogues, Music and SFX share formats/audio/audio_transport and reach it
    through that; Movies has its own, since what it is keeping time
    against is a picture rather than a playlist."""
    transport = getattr(panel, "transport", None)
    return transport.player if transport is not None else panel.player


def _export_name(item):
    """A tree row's name, made safe to hand a Save dialog as a filename.

    The row already reads well - "23-45964 Giant Ice Pig Model.SMST" -
    so it is the name to offer, minus the extension it came with and
    anything Windows will not take in a filename."""
    text = (item.text() if item is not None else "") or "model"
    for kind in (".SMST", ".ANMP", ".TANP", ".MDAP", ".ALFP", ".MDAT",
                 ".SCLD", ".SPRT", ".BGMP", ".TXTD", ".IDX", ".BIN"):
        if text.upper().endswith(kind):
            text = text[:-len(kind)]
            break
    for bad in '<>:"/\\|?*':
        text = text.replace(bad, "-")
    return text.strip().strip(".") or "model"
