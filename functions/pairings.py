"""Which skeleton an animation is really posed on, once someone has said.

Fit measures how well a bone table suits a model, and it is right about
three quarters of the time. The quarter it gets wrong is not random: it
cannot separate a character's costume variants (they are the same rig at
different sizes), and it will happily prefer a neighbour's table that
fits the mesh better than the character's own does. Nothing measurable
in the files distinguishes those cases - which is why the armadillo's
own table scores 0.79 against a stranger's 0.44 and is still correct.

So this module does not measure anything. It reads the judgements made
by eye through the ANMP viewer's Approve button and applies them.

WHY A NAME IS THE KEY, AND A TABLE'S CONTENT THE ANSWER

An area exists twice on this disc, cursed and purified, and the same
character appears in both with everything at a different address. What
does not change is what the thing is called, so the name is what a
judgement is filed under - "magic flower" posed on "magic flower",
whichever half of whichever area it was seen in.

What is stored against that name is the bone table itself, not where it
sits. The offset does not travel: the magic flower's table is at
0x41B20 in one area and 0x2A274 in another, and it is the same three
records in both. Keying on the offset got every magic flower on the
disc wrong; keying on the content gets them all right.

That is also why the key is the pair and not the animation alone. One
"NPC Animation" is shared by a dozen characters, and each of them wants
its own skeleton; the animation says nothing, the pairing says
everything.

WHAT IS STILL A GUESS

Anything nobody has judged. Those still go through game_rest.best_for,
so this narrows the guessing rather than replacing it.
"""
import os
import re
import sys

from functions import skeleton

FILE = "animation_pairings.txt"

# OK | <animation> | ANMP 0x.. | area N | model <name> @ 0x.. | skeleton
# Type X | <source> 0x.. | N bones | ...
_LINE = re.compile(
    r"^(OK|PROBLEM)\s*\|\s*(?P<anim>.+?)\s*\|\s*ANMP\s+0x[0-9A-Fa-f]+\s*\|"
    r"\s*area\s+(?P<area>\d+)\s*\|\s*model\s+(?P<model>.+?)\s+@\s+"
    r"0x[0-9A-Fa-f]+\s*\|\s*skeleton\s+(?P<skel>.*)$")

_TABLE = re.compile(r"(MAIN\.EXE|overlay)\s+0x([0-9A-Fa-f]+)\s*\|\s*(\d+)\s+bones")


def path():
    """Beside the program, the same place the viewer writes it."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, FILE)


def subject(text):
    """A row's name with its index prefix and extension taken off.

    "27-51234 Phams Daughter 01 Model.SMST" and
    "27-508D0 Phams Daughter 01 Model.SMST" are the same character in
    two halves of one area, so both have to key the same."""
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r"^[0-9A-Fa-f]+-[0-9A-Fa-f]+\s+", "", text)
    text = re.sub(r"^[0-9A-Fa-f]{6}-[0-9A-Fa-f]{6}\s+", "", text)
    text = re.sub(r"\.[A-Za-z]{3,4}$", "", text)
    for tail in (" Model", " Animation"):
        if text.endswith(tail):
            text = text[:-len(tail)]
    return " ".join(text.lower().split())


def load(where=None):
    """{(animation, model): (source, offset, bones, area)} from OK lines.

    PROBLEM lines are read too but deliberately not returned as answers -
    a pairing someone flagged as wrong is not one to repeat."""
    votes = {}
    where = {}
    try:
        with open(where or path(), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return {}
    for raw in lines:
        found = _LINE.match(raw.strip())
        if not found or found.group(1) != "OK":
            continue
        table = _TABLE.search(found.group("skel"))
        if not table:
            continue
        key = (subject(found.group("anim")), subject(found.group("model")))
        # The area is remembered so the offset can be read back out of
        # the right overlay later, but it is NOT part of what is voted
        # on: the same judgement made in the cursed and purified halves
        # is one answer twice over, not two answers once each.
        answer = (table.group(1), int(table.group(2), 16), int(table.group(3)))
        votes.setdefault(key, {})
        votes[key][answer] = votes[key].get(answer, 0) + 1
        where.setdefault(key, {}).setdefault(answer, int(found.group("area")))

    # The file is append-only and a pairing can be judged more than once
    # - the same one in the cursed and purified halves, or a second look
    # after changing the model. Taking the last would let one slip
    # overwrite a judgement made twice, so the most-repeated answer wins
    # and the newest breaks a tie.
    known = {}
    for key, answers in votes.items():
        best = max(answers, key=lambda a: answers[a])
        known[key] = best + (where[key][best],)
    return known


def resolve(known, sources_of):
    """Turn the recorded offsets into the tables they point at.

    An offset only means something in the overlay it was read from. The
    magic flower's table sits at 0x41B20 in one area and 0x2A274 in
    another, and the two hold exactly the same three records - so what
    travels between areas is the table itself, not where it is. This
    reads each judgement back out of the area it was made in and keeps
    the content, which is what the viewer then goes looking for.

    `sources_of(area)` is whatever load_sources gives for that area."""
    out = {}
    for key, (source, offset, bones, area) in known.items():
        for label, data in sources_of(area) or ():
            if label != source:
                continue
            try:
                table = skeleton.read_table(data, offset, bones)
            except Exception:
                break
            out[key] = (bones, tuple(tuple(int(v) for v in row)
                                     for row in table))
            break
    return out


def find(known, animation, model):
    """What was approved for this pairing, or None."""
    return known.get((subject(animation), subject(model)))
