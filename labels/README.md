# Labels

Nothing in `TOMBA2.DAT` carries a filename. The IDX gives each SDAT entry a
type id, and `functions/format_detect.py` reads the type of a trail file out
of its own bytes, so the tree can say what a file *is* — but "the MDAT at
0x1B724" is as close as either gets to saying *which* one it is.

The names are knowledge that only exists outside the disc, worked out by hand
by people opening files and looking at them. A **labels file** is where that
knowledge lives: a list of addresses in one build of `TOMBA2.DAT`, each with
the name someone gave it. Every `.json` in this folder is loaded at startup;
adding one is all it takes to support another build.

## Which one gets used

Not by checksum — this tool edits and repacks discs, so a checksum would stop
matching the moment it was used in anger. Each labels file is instead scored
against the disc in front of it: how many of the addresses it names are really
in that disc's IDX. The right file scores 1.00 and the wrong one 0.02, and
anything under 0.5 is treated as no match at all.

That score is also a health check on your own file. If you repack a disc so
far that things move, the score drops and the tool says so rather than
quietly hanging names on the wrong files.

**File → Load Labels…** loads one of your own, from anywhere. It stays in
force until **File → Use Built-in Labels**, so reopening the ISO doesn't
throw your work away.

## Format

```json
{
  "name": "Tomba! 2: The Evil Swine Return (USA)",
  "build": "us-retail",
  "source": "TOMBAMAP_us.txt",
  "dat_size": 9537536,
  "entries": [
    {"start": "053724", "end": "075FDB", "type": "MDAT",
     "name": "Town of the Fishermen"}
  ]
}
```

- `start`, `end` — hex offsets into `TOMBA2.DAT`, `end` inclusive, the way the
  hand-written `TOMBAMAP` txt files wrote them. Only `start` is used to match a
  file; `end` is kept so nothing from the original map is lost.
- `type` — what the person who mapped it recorded. The tool works the type out
  for itself (`functions/format_detect.py`) and does not take this as
  authority. It uses it for two things: to say in the tooltip when the two
  disagree — there are two such disagreements in `us-retail.json`, and in both
  the tool is right — and to pick a name inside the animation family, where
  TANP, BETP, ALFD, ALFP and MDAP are all one container and the bytes can only
  say "ANMP". There is deliberately no id table here: ids mean different things
  on different builds, so anything that leaned on them would be wrong for every
  build nobody had written a table for.
- `name` — what shows in the tree. Leave it out or `""` for an address that is
  recorded but not identified; those rows keep their address and nothing else.
- `dat_size`, `serial` — notes to whoever reads the file. Nothing matches on
  them; the disc is identified by the addresses (see above).

## Other sections

```json
  "bins": {"A0F.BIN": "Last Pig Boss", "SOP.BIN": "Intro"},
  "areas": {"0C": "Water Temple"}
```

- `bins` — what each overlay in the disc's `BIN/` folder is, shown beside it in
  the BINs tab. `A0F.BIN` says nothing on its own.
- `areas` — a name for an `AREA_nn` folder in the tree. Optional: an area is
  normally named after the level inside it, taken from its MDAT's own entry, so
  this is only for areas that have no MDAT or whose level wants a different
  name. Keys are the hex chunk number.

## Builds

`us-retail.json` is SCUS-94454 and `us-demo.json` the US standalone demo. Other
regions are different builds with different addresses and need their own file —
SLPS-02350 and the SCES discs are not covered by these.

## Converting an old TOMBAMAP

```
python -m functions.labels convert examples/TOMBAMAP_us.txt labels/us-retail.json \
    --name "Tomba! 2 (USA)" --build us-retail --dat-size 9537536
```

Both files here were made that way, from `examples/TOMBAMAP_us.txt` and
`examples/TOMBAMAPdemo_us.txt`.
