# NPC event pages and action stacks

The Level Editor now has **Events / actions…** for selected entities.
The event window has its action stack and parameters on the left and an
embedded Preview pane on the right. This first implementation targets the
verified US retail layout.

## Implemented workflow

The inspector discovers literal native script bindings reachable from an
entity's handler. Each resolved entry point is a page; it does not claim
that every quest condition or dynamic table has been recovered. Known
commands have editable parameters. Timer waits, dialogue presentations,
animation, resource messages, sound, voice, position and camera-angle
commands can be inserted before a selected action.

Applying compiles a copied script into the verified extension window,
relocates its conditional pointers and redirects the selected native call
site. The original script bytes remain intact. Ambiguous or unsupported
bindings and invalid control flow are refused. A shared-edit checkbox is
required: per-entity handler isolation is not implemented, so actors using
that same call site receive the same edit. Project/disc builds include the
changed overlay through the existing level-file staging path.

Every decoded command offers its native instructions and raw operands in
Preview, including actions whose semantics remain unknown. Presentation
commands resolve the area's TXTD bank and dialogue entry. Runtime-selected
entries are explicitly listed as candidates. Dialogue edits join the same
pending edits and cache as the IDX text editor. Linked voice uses the
existing master/channel/clip resolver; explicit resident voice commands
use their native XA descriptors. Sound previews resolve native sound
definitions to VAB waveforms; SPU envelopes and the gameplay mixer are not
simulated.

Animation playback is requested explicitly. It initialises the selected
native entity in its own area/interior, uses its animation table and model
parts, then samples the requested skeletal animation. It works for modelled
entities generally, not just NPCs. It refuses unresolved runtime targets
instead of choosing another character's model. The preview is capped at
120 ticks and can show the selected entity's static model or native sprite
when the action itself has no decoded visual preview. Movement and camera
commands currently offer native-code inspection and a static model view;
their full scene effects are not simulated in the preview.

Edit mode initialises NPCs and all listed interiors without recording pose,
particle or line clips and without probing speculative event actors. Cold
initialisation still executes native setup; a measured A00 load took about
9.4 seconds and a cached reopen about 0.1 seconds on the development PC.
Selected writable entities have a native-axis translation gizmo. Authored
NPC scene-table positions are writable when the binding is unambiguous;
script-controlled or unresolved positions remain immovable.

Tests cover unchanged native round trips, branch relocation, insertion,
executing inserted waits through the retail MIPS interpreter, event-window
layout, dialogue staging, draft isolation, no-recording fast mode, gizmo
axes, commit and cancellation. A retail Windmill animation preview resolved
17 model parts and animation 2's 24 native ticks. Emulator playthrough is
still required for edited conversations and save/load behavior.

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
