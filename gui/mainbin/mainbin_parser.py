"""
Parser for the plain-ASCII UI/system string pool found in main.bin (the
PS1 executable/overlay, not the TXT1/TXT2/TXTD DAT-based dialogue
system).
"""

TEXT_REGION_START = 0x680

# The one confirmed non-ASCII byte found inside the pool - rendered the
# same way tombadict-based formats render an unmapped byte, so it's at
# least visible and round-trippable instead of silently eaten.
_INLINE_CONTROL_BYTES = {0x01: "{$01}"}


class MainBinParseError(Exception):
    pass


# THIS POOL IS LATIN-1, NOT THE GAME'S OWN TABLE
#
# These are the memory-card and boot messages, which the console draws
# with its own font rather than out of the font page - so a byte here
# means what Latin-1 says it means, not what tombadict says. The German
# and Spanish discs prove it both ways: 0xFC reads "ü" and gives
# "uberpruft" -> "überprüft", while the game table calls 0xFC {$PAUSE};
# 0xF1 reads "ñ" for "dañado" and 0xBF "¿" for "¿Continúas?".
#
# Only 0xA0-0xFF is taken as text. 0x80-0x9F is the C1 control block,
# which is not printable in Latin-1 and does not appear inside real
# strings, so it stays an escape rather than becoming an invisible
# character an editor could not see or retype.
_LATIN1_FIRST = 0xA0

# GLYPH-BYTE MODE
#
# Off, a byte from 0xA0 up reads as the Latin-1 character of the same
# value, which is right for the stock discs (see above). On, it reads as
# a {$XX} escape instead - the byte itself, spelled out.
#
# That is what a translation needs. The letters a Polish build wants are
# not in Latin-1 and never will be; what it does instead is draw them
# into spare cells of the font page, and on this font a cell number IS
# the byte that selects it. So a glyph put in cell 160 is typed as
# {$A0}, and gui/txtd/font_preview.split_runs(raw_cells=True) draws it.
# Latin-1 would show that same byte as an invisible no-break space,
# which is why it could not be used before.
#
# Encoding is unaffected: encode_bytes() already accepts both spellings,
# so turning this on changes what you SEE, never what gets written.
#
# A cell a translation has claimed shows its letter rather than its
# number, and that letter types back to the same byte - so once "a" is
# drawn in cell 0xA0 and named in the Translation tab, "a" is what you
# write here. Only claimed cells, and only in this mode: a byte that is
# Latin-1 to a stock disc has to keep meaning that, and 0x41 stays "A"
# whatever the game table calls cell 0x41.
_GLYPH_BYTES = False


def _claimed():
    """{byte: letter} for the cells this translation has claimed, or
    empty when nothing has been."""
    if not _GLYPH_BYTES:
        return {}
    from gui.txtd import translation
    return {code: char for code, char in translation.active().chars.items()
            if char and len(char) == 1}


def glyph_bytes():
    return _GLYPH_BYTES


def set_glyph_bytes(on):
    global _GLYPH_BYTES
    _GLYPH_BYTES = bool(on)


def _japanese():
    """Whether the disc open right now is the Japanese one.

    Its pool is Shift-JIS rather than Latin-1, and starts 0x94 further
    in than every Latin build's - which heuristic_pool_bounds finds on
    its own once the entries decode as text instead of as escapes."""
    from gui.txtd import dicts
    return dicts.japanese_disc()


def decode_bytes(raw):
    """Raw entry bytes (no terminator) -> displayed text."""
    if _japanese():
        from gui.txtd import jptext
        return jptext.decode_pool(raw)
    claimed = _claimed()
    out = []
    for b in raw:
        if b == 0x0A:
            out.append("\n")
        elif 0x20 <= b < 0x7F:
            out.append(chr(b))
        elif b in claimed:
            out.append(claimed[b])
        elif b in _INLINE_CONTROL_BYTES:
            out.append(_INLINE_CONTROL_BYTES[b])
        elif b >= _LATIN1_FIRST and not _GLYPH_BYTES:
            out.append(chr(b))          # Latin-1 is code point == byte
        else:
            out.append(f"{{${b:02X}}}")
    return "".join(out)


def scan_entries(path, region_start=TEXT_REGION_START, region_end=None):
    """Scan the string pool, return entries in file order:
    [{"offset", "length", "text"}, ...] (length excludes the 0x00
    terminator). region_end defaults to EOF, correct for a pre-cut
    main.bin but wrong for the full exe - callers scanning the exe must
    pass the pool's real end, or code past it produces spurious entries."""
    with open(path, "rb") as f:
        data = f.read()

    entries = []
    i = region_start
    n = region_end if region_end is not None else len(data)
    while i < n:
        if data[i] == 0:
            i += 1
            continue
        start = i
        while i < n and data[i] != 0:
            i += 1
        raw = data[start:i]
        entries.append({
            "offset": start,
            "length": len(raw),
            "text": decode_bytes(raw),
        })
        # leave i pointing at the terminating 0x00 (or EOF); the loop's
        # own "if data[i]==0: i+=1; continue" advances past it next pass

    return entries


def _noise(entries):
    """How much of a run of entries is escaped bytes rather than
    readable text - the tell that the scanner has walked off the string
    pool and is reading code."""
    total = sum(len(e["text"]) for e in entries) or 1
    return sum(e["text"].count("{$") * 6 for e in entries) / total


def heuristic_pool_bounds(path, region_start, probe_len,
                          window=12, noise_threshold=0.5):
    """(start, end) of the string pool in `path`, for a build whose
    layout nobody has mapped. Shared by the MAIN.EXE and SOP.BIN
    editors, which face the same problem with different constants.

    Two things it has to get right, and each was got wrong before:

    START. The pool does not always begin where a known build puts it -
    the 28-09-99 prototype's MAIN.EXE has it 0x88 bytes further in, so
    scanning from the usual place lands mid-code and, the scanner being
    NUL-delimited, yields one enormous unreadable entry. So walk forward
    to the first sustained run of readable entries instead of assuming.

    END. A window is called noisy once it is HALF noise, which happens
    half a window BEFORE the pool really ends - that alone was costing
    the prototypes six entries off MAIN.EXE and, worse, seven of
    SOP.BIN's twelve story lines. So once a noisy window is found,
    step through it for the first entry that is noisy on its own and cut
    there.

    Taking the first noisy run rather than the last still matters: code
    further out holds scattered debug strings that would otherwise drag
    the boundary well past the real pool."""
    entries = scan_entries(path, region_start=region_start,
                           region_end=region_start + probe_len)
    if len(entries) < window:
        return region_start, region_start

    first = 0
    while (first + window <= len(entries)
           and _noise(entries[first:first + window]) > noise_threshold):
        first += 1
    if first + window > len(entries):
        return region_start, region_start

    boundary = len(entries)
    for i in range(first + 1, len(entries) - window + 1):
        if _noise(entries[i:i + window]) > noise_threshold:
            boundary = next((j for j in range(i, len(entries))
                             if _noise([entries[j]]) > noise_threshold), i)
            break

    last = entries[max(boundary, first + 1) - 1]
    return entries[first]["offset"], last["offset"] + last["length"] + 1


def encode_bytes(text):
    """
    Displayed text -> raw bytes, inverse of decode_bytes(). Raises
    MainBinParseError instead of silently producing wrong bytes.
    """
    if _japanese():
        from gui.txtd import jptext
        try:
            return jptext.encode_pool(text)
        except jptext.JapaneseTextError as exc:
            raise MainBinParseError(str(exc)) from exc
    claimed = {char: code for code, char in _claimed().items()}
    out = bytearray()
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            out.append(0x0A)
            i += 1
            continue
        if text[i:i + 2] == "{$" and i + 5 <= n and text[i + 4] == "}":
            hex_part = text[i + 2:i + 4]
            if all(c in "0123456789ABCDEFabcdef" for c in hex_part):
                out.append(int(hex_part, 16))
                i += 5
                continue
        if 0x20 <= ord(ch) < 0x7F:
            out.append(ord(ch))
            i += 1
            continue
        byte = claimed.get(ch)
        if byte is not None:
            out.append(byte)
            i += 1
            continue
        if _LATIN1_FIRST <= ord(ch) <= 0xFF:
            out.append(ord(ch))
            i += 1
            continue
        raise MainBinParseError(
            "Can't encode character {!r} at position {} of text {!r}: no "
            "cell in the Translation tab is named {!r}, and it is not "
            "Latin-1 or a {{$XX}} byte escape.".format(ch, i, text, ch)
        )
    return bytes(out)
