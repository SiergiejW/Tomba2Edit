# NPC event pages and action stacks

The Level Editor now has **Events / actions…** for selected entities.
The event window has its action stack and parameters on the left and an
embedded Preview pane on the right. This first implementation targets the
verified US retail layout.

## Implemented workflow

**Events / actions…** (Level Editor, any selected entity) opens one window:
the action stack on the left, previews on the right.

- **Pages** are the literal scripts the entity's handler starts. Discovery
  floods the handler's control flow (both branch paths, jump tables, tail
  jumps, overlay calls) and resolves the script register backwards at every
  call to the three loaders: `f_LoadActorScript` 0x80040D68 (a1),
  `f_InitializeActorScript` 0x80040CDC (a2, also gives the animation table)
  and `f_LoadCurrentScriptActorScript` 0x80070888 (a0). Paths that build
  different pointers give one page each. Dynamic tables stay unresolved.
- **Rows** read like RPG Maker: `◆ Wait 21 ticks (0.70 s)`,
  `◆ Spawn presentation: dialogue 3, entry 2 — "Whoooa!…"`,
  `◆ If progress[44] == 7 ⟶ #19 if true`. Rows inside a forward jump are
  indented, jump targets show `◁ #NN`, and colours mark the kind of action.
  Jumps into shared resident scripts stay external pointers.
- **Editing**: typed fields for decoded commands, raw fields behind a
  checkbox, insert before/after (extended 16-byte records for moves and
  camera focus), delete, move up/down and revert page. Conditional and
  end actions cannot be deleted. A swap that would change what a jump runs
  is refused.
- **Previews**: dialogue text (editable, staged with the IDX text editor's
  pending edits) and linked voice; resident voice IDs; sound effects from
  their VAB waveform; item names for item messages; skeletal animation
  played on the entity's own model (on request); a top-down **scene path**
  of set-position and move actions and camera focus points over the room's
  other entities, with known durations; disassembly of each callback and of
  the target of **Run native code** (command 62).
- **Applying** compiles the page into the event region, the top 8 KB of
  the free-RAM window (0x80155000..0x80157000), and redirects the call
  site's `lui`/`addiu` pair. Custom rewards and grown pickup tables stay
  below it (`pickup_edit.TABLES_LIMIT`). Re-editing a page reuses its slot
  when nothing else references it. A `lui` in a scratch register is
  redirected only if the scratch register is dead after the `addiu`. A
  `lui` shared by several pages is redirected only if the new high half
  matches.

Command semantics come from the US decomp. Callback results: 0 waits,
1 done (a branch form then takes its pointer), 2 continues and 3 jumps.
Only 42/43/44 (and native/area code) return 2/3. 6 and 9 are waits, not
conditions. Conditions jump when they hold (mode -1 always, 0 ==, 1 <=,
2 >=, 3 any bit). 49–61 are Tomba/interior commands and 62 calls native
code at operands 1|2<<16.

Verified on retail: Windmill (9 pages), Kainen (9), Mining Town miners
(3, with animation 6 playing on the miner's model). Edited pages execute
through the retail interpreter in tests. Emulator playthrough of an edited
conversation is still outstanding.

## What the game already supports

`f_RunActorScriptInterpreter` at US address `0x80041098` dispatches the low
11 bits of a command header through the callback table at `0x800A3B78`.
Callbacks can wait for another tick, advance, or choose a branch.
`f_LoadActorScriptCommand` copies three signed 16-bit operands; header
bit `0x2000` adds four more operands. Commands therefore cannot all be
treated as fixed-size records.

`f_AdvanceActorScriptCursor` distinguishes ordinary next records, extended
records, conditional pointer edges, and terminal records. It advances by
8, 16, or 24 bytes depending on the header and branch form. Inserting an
action requires rebuilding pointer edges, not inserting bytes in place.

Named callbacks in the annotated US decomp cover messages, voice playback,
skeletal animation, axis/interpolated movement, camera transforms/focus,
timer waits, animation/camera completion waits, event changes, and conditions.
Preserve unknown commands and raw flags verbatim until their semantics are
verified. The symbol-name cache predates some decomp renames; callback names
must be checked against actual native addresses before exposing parameters.

## Evidence from real NPCs

The Windmill interior NPC selects dialogue resource `0x25` or `0x26`
according to Static Explosion event state, starts interaction audio, and
loads A00 script `0x80148884`. Its handler waits for script completion and
then restores the idle interaction state.

Starving Charles runs separate scripts for item use, Baked Banana ownership,
and response variants. The native handler also grants Banana item `0x85`
after the final script finishes. That grant belongs to the surrounding NPC
state machine; displaying just its script would omit a gameplay action.

The trolley NPC similarly chooses script pointers from its behavior and
quest progress. One flat list of every observed call would combine mutually
exclusive conversations and misrepresent execution order.

## Further editor work

Selecting an entity opens an Events inspector with condition pages, trigger,
and an ordered list with nested If/Else blocks. Each page preserves the
native entry point and distinguishes decoded script commands from external
NPC logic. Reused scripts show their other users.

Show Text opens the resolved text entry and its existing editor. Voice can
be a separate command or come from the text entry's TXTD metadata: the low
byte selects the first clip and successive END segments use following clips.
Display a play button only after resolving the actual clip table/channel.
Do not assume a text ID is also a voice ID.

Known commands receive typed fields and previews. Unknown or unresolved
commands remain visible as native actions. Wait distinguishes a timer from
waiting for dialogue, animation, camera movement, or audio completion.

Editing a shared script defaults to creating an entity-specific copy.
Adding/reordering/removing commands compiles a new native script, relocates
its branch pointers, and redirects the verified script reference. The
surrounding handler's quest checks and completion behavior remain explicit.
Per-entity overrides require a verified distinct binding or handler wrapper;
changing a shared code immediate can otherwise affect several NPCs.

## Completion checks for implementation

First support one small Windmill conversation end to end: decode and encode
unchanged bytes exactly, resolve displayed text and voice, edit a parameter,
insert a timer wait and animation command, then run the native interpreter
and test the resulting conversation in an emulator. Expand to Charles and
trolley pages after branch selection and external handler actions are
represented correctly. A chronological runtime trace supplements this
analysis; it cannot establish that unobserved quest branches do not exist.
