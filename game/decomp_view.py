"""The decompiled C of a handler, from decomp/MAIN.EXE_US_DECOMP.c (US retail).

The file is Ghidra's export for MAIN.EXE and the area overlays: functions in
address order, overlay ones named FUN_Axx__<address> unless the project named
them. Only what the export covers can be shown - a routine missing from it is
simply not found."""
import re
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / 'decomp' / 'MAIN.EXE_US_DECOMP.c'
_index = None
_lines = None
_DEF = re.compile(r'^[A-Za-z_][^;=]*?\b([A-Za-z_]\w*)\s*\(')


def _load():
    global _index, _lines
    if _index is not None:
        return
    try:
        _lines = _PATH.read_text(encoding='utf-8', errors='replace').split('\n')
    except OSError:
        _lines, _index = [], {}
        return
    _index = {}
    for n, line in enumerate(_lines):
        if not line or line[0] in ' \t/}{#' or line.endswith(';'):
            continue
        m = _DEF.match(line)
        if m and any(_lines[k].strip() == '{' for k in range(n + 1, min(n + 4, len(_lines)))):
            _index.setdefault(m.group(1), n)


def function_text(tag, address, symbol_name=None):
    """The C of the routine at `address` in overlay `tag` ('A02'), or None. Tries the project's own
    name for it, then Ghidra's FUN_<tag>__<address>."""
    _load()
    for name in (symbol_name, f'FUN_{tag}__{address:08x}', f'FUN_{address:08x}'):
        if name and name in _index:
            start = _index[name]
            end = start
            while end < len(_lines) and _lines[end] != '}':
                end += 1
            first = start
            while first > 0 and (_lines[first - 1].startswith('//') or not _lines[first - 1].strip()) and start - first < 8:
                first -= 1
            return '\n'.join(_lines[first:end + 1])
    return None
