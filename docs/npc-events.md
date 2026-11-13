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

## Why an NPC has many pages, and which one runs

A page is any script the entity's handler can start; the handler's native
code picks one. A shared handler serves every actor of its kind, every
state and every quest stage, so A06's Donglin inhabitants show 47 pages.
The **Conditions** box reads the branches and jump-table cases on the way
to each page's loader call (`event_script.page_conditions`), e.g. page 33
"event 33 is cleared AND actor step == 3". These are typically:
- which actor it is: actor +3, the scene record's slot byte (actor_sim.SLOT) - handlers switch on it with a jump table (Donglin's 6 inhabitants);
- the handler's state machine: actor +0x04 / +0x05 / +0x06;
- quest state: event states at 0x800BF8B4, area progress at 0x800BF9B4
  (event 0 = not started, 255 = cleared);
- Tomba's interaction: `f_TryPrepareTombaForInteraction()`.
Where code paths merge, only the tests every path makes are kept. "?" is
a value set on another path.

## Zippo, Tomba and the simulation

`src_CurrentScriptActor` is only ever Zippo (`f_InitializeAreaScriptActor`:
area file 16, 12 parts, animations at 0x8001B860). So every "script actor"
flag (0x8000, or bit 1 of Play animation) means Zippo, and conversation
pages (C tabs) run on him. A conversation request (13) settles him at
Tomba + 190 round Tomba's facing (+0/180/270/90 degrees by request flags
0-1, +45 for 0x20; flag 4 uses explicit offsets), 150 up, scale 0xC00,
facing Tomba (FUN_8007091c).

Simulation preview (on by default) leaves the viewport as the events up
to the selected row leave it. The entity loops its current animation, and
Zippo stands by Tomba and loops his. The camera moves only for camera rows,
or when skipping to a point with a different event camera; a manual pan
stays. When a page gives no Tomba position, he stands 250 units from the
entity on the side the viewport looks from. Closing the editor removes the
stand-ins and restores the entity; the camera stays.

## Messages and Ifs

Command 40 modes 0/1 show entry N of the resident System message table
(System chunk file 3: offsets at +16, text at +16 + 2 * first u16).
Rows read If <condition>: with the guarded block indented. The block runs
while the test fails; the If jumps to its end when it holds. Insert If
asks for the jump target; "Jump to row" re-points any branch.

## Text, gestures and conversation music

**Show text** (0) and **Show text + gesture** (1) are both one command.
Command 1 starts gesture `o[2]` with the line (blend 4) and returns the
actor to its rest animation (+0x7B) when the line closes. Flag 0x4000 skips
that return, 0x2000 returns mid-line once the gesture ends, and 0x8000
gestures the current script actor. **Separate text and animation** (and
**Separate all on page**) rewrite a command 1 as Play animation X ->
Show text -> Play animation -1, which is "back to rest" in
`f_RunScriptedActorAnimationCommand`. The split is refused for 0x2000,
0x8000 and branch forms. The text may appear one tick earlier.

**Conversation music** (11): operand 0 = 0 ends it (sound 0x7F, letterbox
off, area track restarts). Otherwise it starts (letterbox mode = operand
0) with cue operand 1 -> sound 0x72..0x78 (cue 0 = cue 1; 5 and 6 are both
0x76) -> SEQ slot 4..7 in TOMBA2.SND, or 10..12 when the area's overlay
fills them (A05, A0A-A0F, A0K, A0L). The preview renders the SEQ with
`formats/audio/seq.py`. Many NPCs start their music from native code
(`f_BeginScriptedInteractionAudio`) and only end it in the script. Area
background music is not in events: `f_PlayAreaBackgroundTrack` reads
the normal or purified per-area tables in MAIN.EXE.

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

## Commands read from the decomp (2026-11-05)

| Op | Callback | What it does |
|----|----------|--------------|
| 10 | f_WaitForTombaScriptedActionCommand | Waits until Tomba finishes his current move and is handed to the cutscene (0x800BF80E, set by his action states while the letterbox is on); gives up after 500 ticks. Bits 0/1 of +0x72 hold his X (+0x74) / height (+0x76) meanwhile. Keep it before anything that moves or animates Tomba. |
| 40 mode 10 | f_RunScriptedMessageCommand | Shows nothing; waits for the message box already open to close. |
| 18 / 19 | f_RunStartEventCommand / f_RunUpdateEventCommand | f_StartEvent / f_UpdateEvent on quest event +0x72 (the Events list: AP, notice). Waits for the notice unless +0x74 is non-zero. |
| 55 | f_RunScriptedTombaAnimationCommand | Tomba animation +0x74 (f_SetTombaAnimation IDs = the verified Tomba bank), blend +0x76; mode 0 waits, 2 uses held state 0x26; extended: sound +0x68 on frame +0x66. |
| 57 | f_StartSynchronizedActorAndTombaAnimations | Actor animation +0x74 and Tomba animation +0x76, blend 4. |
| 58 | f_RunScriptedActorFaceTombaSequence | Tomba turns to face the actor; the actor turns toward Tomba 0x100 a tick (yaw = ratan2(actor depth - Tomba depth, Tomba X - actor X)); when Tomba is ready, mode != 2 starts the 57 pair, mode 0 waits for the actor animation. |
| 22 | f_SetScriptedCameraFocusCommand | +0x72: 0 f_SetCameraMode(+0x74), 1 mode byte only, -1 restore. +0x76: 0 focus at (+0x64, +0x66, +0x68), 1 on Tomba 140 units up. |
| 23 / 24 | camera angles / focus on Tomba | pitch 0x800E8076, yaw 0x800E8078 (4096 = 360), distance 0x800E8074. |

Camera block 0x800E8008 (mode +0x64, focus +0x38); f_InitializeAreaCameraState 0x80078610,
f_UpdateCameraController 0x8006EC44; eye/focus each frame at scratchpad 0x1F8000D0 / 0x1F8000DC.
Tomba's position: 0x800E7EAC (X, height, depth, 16.16), scratchpad copy 0x1F800160.
The editor's camera preview (event_preview.camera_path) runs those callbacks and the
camera controller in the emulator; Tomba's position comes from the page (54, 49, the 16
interaction offset: the actor's anchor is Tomba minus that offset) or is assumed in front.

### Conversations (Request / Run conversation, ops 13 / 14)

f_ConfigureScriptActorConversation(handler, request, flags) (0x80070724; offset form
0x8007074C) stores the handler on the current script actor (+0xBF), the request in
0x800BF830, the flags in 0x800BF831. f_UpdateScriptActorConversationState dispatches:

- handler 0 (0x800713F8): loads script `table[request]` through the 0x80040CDC loader with
  animation table 0x8001B860; `table` is the word at 0x800A4BBC[area byte 0x800BF870] - only
  A00 has one (0x80149E50, nine slots; slot 1 is filled at run time). The editor shows these
  as pages `C<request>`; they are written back by relocating the script and rewriting that
  one table word (event_script._stage_table).
- handler 1 (0x8007156C): no dialogue. It runs on the dispatcher's actor - the area script
  actor, i.e. Zippo, not the NPC and not Tomba - and eases that actor toward the configured
  stand point with f_UpdateScriptedInteractionApproach (mode 1, walk/idle animation); the
  page itself shows the text. (Disassembled; the later states are not traced.)
  FUN_8007091c, run first, places the same actor round Tomba and grows it to scale 0xC00.
- handler >= 10: area code, `handler - 10` into A00 0x80149E74 or A07 0x80132E78.

Without flag 0x04 the stand point is a fixed default: 0xBE out at one of four angles
(flags & 3: 0, 0x800, 0xC00, 0x400; +0x200 with 0x20). With it, +0x64/+0x66/+0x68.
Area trigger code (f_UpdateFishermensTownProgressTriggers) also requests conversations on
the area script actor f_HandleAreaScriptActor (0x80071A3C), which is not placed: the Level
Editor's "Area scripts..." opens all of the area's conversation pages on it.

Presentation (op 1) resource bit 0x8000: the gesture plays on src_CurrentScriptActor
rather than the entity running the page.

### Zippo = the area script actor (2026-11-05, not simulated yet)

f_HandleAreaScriptActor (0x80071A3C) is Zippo: f_InitializeAreaScriptActor builds a 12-part
model from area file 16 (layout 0x800A4BC8), ANMP file 17 at +0x3C, animation table 0x8001B860,
scale 0x200, and makes itself src_CurrentScriptActor. Files 16/17 are Zippo in almost every
area. It is drawn only while a conversation is active (pickup_phase != 0); conversation state
1 places it beside Tomba (FUN_8007091C/FUN_80070BD0, default offset 0xBE / 0x50 at angle
flags & 3). So handler-1 conversations bring Zippo to Tomba, and presentation bit 0x8000
gestures are Zippo's. Each area's spawn table holds the handler (A00 word at 0x80147E20).
Next: allocate it via ALLOCATE_ACTOR with that handler in actor_sim, run its init, then pose
it like NPC previews (World._parts) and add it to the simulation preview.

## Editor aids (2026-11-09)

- Waits are labelled "Wait: ..."; command 18 with event -1 waits for every pending quest banner/popup (global counters), it names no event.
- "Runtime-selected entry": the handler stores constants into the actor dialogue selector (+0x7A); `native_selectors` lists them with their conditions.
- Progress rows (4, 6, 12, 43): `progress_writers` lists the script rows in the area that set the same byte (native writers are not covered).
- Animation lists come from the emulated actor table (`animation_catalogue`); a switch blends over the command ticks and the clip then plays that many ticks late.

## NPC-specific pages

NPCs of one handler share its pages. To give NPCs of one scene-record variant (actor +3) a page of their own, the editor copies the page into the event region and puts an eight-word stub in front of the page's loader call: `jal LOADER` becomes `jal stub`; the stub reads `lbu t0, 3(t0)`, swaps the script argument register for the copy's pointer when the variant matches, and tail-jumps to the real loader (or the next stub), so the return address stands and other variants load what they always did. Works for the loaders that take the actor in a0 (f_LoadActorScript, f_InitializeActorScript); f_LoadCurrentScriptActorScript is refused. `specialise` / `unspecialise` in game/event_script.py; discovery sees through the stubs (the copy is a Binding with .variant, the handler's own page lists .overridden). Tabs are marked with a star; right-click a tab to make or remove one.


The stub now carries up to eight tests (`stub_words`): Tomba has an item (inventory byte at 0x800BFAB4 + id: at least / at most / exactly), an event state (0x800BF8B4 + n), an area-progress byte (0x800BF9B4 + n), or the actor's scene-record variant (actor +3), all of which must hold for the copy to run; otherwise the original page runs (the Else). The editor's dialog (right-click a tab � Add a copy of this page that runs only if�) offers them with the game's item and event names.

### Which pages are this NPC's

A handler's code is shared by every NPC of its kind and tells them apart by actor +3 (the scene record's slot): it switches on it with a jump table and the per-NPC routines are called from there. `Flow` now follows those calls as edges, so a page loaded inside a per-NPC routine carries the case test (shown as `actor variant (scene record) == k`), and the editor hides the pages whose test fails for the NPC's own slot (Show hidden pages restores them). The two A00 house Fishermen (slots 96 and 97) go from 12 tabs to 2; a Donglin Inhabitant (slot 0) from 47 to 11. The tests are worked out in the background once per handler and kept for the session.

Better than reading the tests: `pages_for_slot` runs the handler itself in the emulator with the actor's slot (+3) and state 1, hooks every routine the handler calls directly (noting and skipping it), and reports the loader calls inside the routines the dispatch reached. For the A00 house Fishermen, slot 96 reaches one routine with page 3 and slot 97 another with page 4. The editor hides every page outside that set (and still applies the slot tests, hand-hiding and 'Show hidden pages').
