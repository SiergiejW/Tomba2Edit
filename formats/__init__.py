"""Tomba! 2's own file formats, one package per format.

Each package is a whole slice of the tool: the parser that reads the
bytes, the renderer that turns them into something to look at, and the
Qt viewer the main window drops into a tab. The format's four-letter
code stays in every filename, so `scld_parser.py` is still what you
search for - the folder just says what SCLD *is*.

    archive/     TOMBA2.DAT and its IDX index, and repacking them
    animation/   ANMP frames, skeletons, UV and CLUT animation
    audio/       SEQ/VAB music, XA streams, VAG samples, SFX, voice
    background/  BGMP background maps
    collision/   SCLD collision planes, and the town overlay's own
    drawmaps/    DRWA and DRWB drawmaps
    executable/  MAIN.EXE and the per-area overlay BINs (incl. SOP)
    geometry/    MDAT level geometry
    images/      IMG texture chunks and the image codecs
    models/      SMST 3D assets, and glTF/GLB export
    movie/       STR movies: MDEC video and CD-XA sound
    sprites/     SPRT sprite banks, ripped sprites, pickup art
    text/        TXTD/TXT2 dialogue, the font pages, translation I/O
"""
