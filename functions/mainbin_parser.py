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


def decode_bytes(raw):
    """Raw entry bytes (no terminator) -> displayed text."""
    out = []
    for b in raw:
        if b == 0x0A:
            out.append("\n")
        elif 0x20 <= b < 0x7F:
            out.append(chr(b))
        elif b in _INLINE_CONTROL_BYTES:
            out.append(_INLINE_CONTROL_BYTES[b])
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


def encode_bytes(text):
    """
    Displayed text -> raw bytes, inverse of decode_bytes(). Raises
    MainBinParseError instead of silently producing wrong bytes.
    """
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
        raise MainBinParseError(
            "Can't encode character {!r} at position {} of text {!r}: "
            "not plain ASCII and not a valid {{$XX}} byte escape.".format(ch, i, text)
        )
    return bytes(out)
