"""Readable names for the routines the decompiler left as FUN_<overlay>__<address>.

A name is made from what the routine does, as the editor already words it (game/native_decode.py): the
items it gives, the event it starts, the sound or animation it plays, the progress byte it sets, the
helper it spawns. FUN_A02__80122140 that loads a script and moves to phase 2 becomes
LoadScript_Phase2__80122140; the address stays on the end so it can still be found. Nothing here is
written back to the project's symbol table - it only changes what the code views print.
"""
import re

_FUN = re.compile(r'\bFUN_(A[0-9A-Z]{2})__([0-9a-fA-F]{8})\b')
_cache = {}


def _camel(text):
    words = re.findall(r'[A-Za-z0-9]+', text)
    return ''.join(w[:1].upper() + w[1:] for w in words)


_PLUMBING = re.compile(r'^(Queue|RunActorScript|AdvanceActor|UpdateActor|TestAndQueue|InitializeActor|InitializeMulti|SetActorPartModel|ReleaseActor|CalculateYaw|Step)')


def _engine(name):
    """A token for a call into the game's own routines - none for the plumbing every actor runs."""
    word = re.sub(r'^(Update|Handle|Set|Start)', '', _camel(name))
    if _PLUMBING.match(_camel(name)) or not word:
        return None
    return word if len(word) <= 20 else ''.join(re.findall(r'[A-Z][a-z0-9]*', word)[:3])


def _token(effect):
    """One short CamelCase token for an effect sentence, or None for one that says nothing."""
    e = effect.strip()
    rules = (
        (r'give item \d+ “([^”]+)”', lambda m: 'Give' + _camel(m.group(1))),
        (r'give item (\d+)', lambda m: 'GiveItem' + m.group(1)),
        (r'take item \d+ “([^”]+)”', lambda m: 'Take' + _camel(m.group(1))),
        (r'take item (\d+)', lambda m: 'TakeItem' + m.group(1)),
        (r'complete event \d+ “([^”]+)”', lambda m: 'Complete' + _camel(m.group(1))),
        (r'start event \d+ “([^”]+)”', lambda m: 'Start' + _camel(m.group(1))),
        (r'(?:complete|start) event (\d+)', lambda m: 'Event' + m.group(1)),
        (r'spawn a pickup of item \d+ “([^”]+)”', lambda m: 'SpawnPickup' + _camel(m.group(1))),
        (r'spawn a pickup', lambda m: 'SpawnPickup'),
        (r'create a helper actor', lambda m: 'SpawnHelper'),
        (r'show the message for item \d+ “([^”]+)”', lambda m: 'ItemMessage' + _camel(m.group(1))),
        (r'show system message (\d+)', lambda m: 'Message' + m.group(1)),
        (r'set progress (\d+) to', lambda m: 'SetProgress' + m.group(1)),
        (r'Tomba animation (\d+)', lambda m: 'TombaAnim' + m.group(1)),
        (r'play animation (\d+)', lambda m: 'Anim' + m.group(1)),
        (r'play sound 0x([0-9A-Fa-f]+)', lambda m: 'Sound' + m.group(1).upper()),
        (r'start conversation music', lambda m: 'Music'),
        (r'set the phase of ', lambda m: 'Phase'),
        (r'set the state of ', lambda m: 'State'),
        (r'set the behaviour of ', lambda m: 'Behaviour'),
        (r'set the (?:X|height|depth) of ', lambda m: 'Place'),
        (r'set the timer of ', lambda m: 'Timer'),
        (r'turn ', lambda m: 'Turn'),
        (r'run f_(\w+)', lambda m: _engine(m.group(1))),
        (r'run (FUN_\w+)', lambda m: None),
    )
    for pattern, make in rules:
        m = re.search(pattern, e)
        if m:
            return make(m)
    return None


def name_for(exe, overlay, tag, address):
    """A readable name for FUN_<tag>__<address>, or None when the routine says too little to name."""
    key = (tag, address, len(overlay))
    if key in _cache:
        return _cache[key]
    name = None
    try:
        from game import event_preview
        base = int(address, 16) if isinstance(address, str) else address
        lines = event_preview.native_plain(exe, overlay, base)
        tokens = []
        for line in lines:
            token = _token(line)
            if token and token not in tokens:
                tokens.append(token)
            if len(tokens) == 3:
                break
        if tokens:
            name = '_'.join(tokens)[:44]
    except Exception:
        name = None
    _cache[key] = name
    return name


def rename(text, exe, overlay):
    """`text` (decompiled or decoded C) with each FUN_<tag>__<address> replaced by its readable name where it has one."""
    if not text:
        return text
    def swap(match):
        tag, address = match.group(1), match.group(2)
        name = name_for(exe, overlay, tag, int(address, 16))
        return f'{name}__{address.lower()}' if name else match.group(0)
    return _FUN.sub(swap, text)
