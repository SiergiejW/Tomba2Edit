"""On-demand previews driven by the selected entity's native resources."""
import struct
from pathlib import Path
import numpy as np
from game import actor_sim, actor_assembly, event_script

def _hold(world,actor,table,animation,step,max_ticks=120):
    """Play `animation` unblended on the emulated actor to its end, so the
    actor holds the pose it would have when the next one starts (the game
    eases out of whatever the skeleton currently is)."""
    from formats.animation.sequences import read_clip
    pointer=world.mem.read(table+(animation&0xFF)*4,4)
    if not actor_sim._in_ram(pointer):return
    clip=read_clip(bytes(world.mem.ram),actor_sim.RAM_BASE,pointer,actor_sim._ANY_POSE)
    world.cpu.call(0x80041718,(actor.address,animation&0xFF,0),sp=actor_sim.STACK)
    for _ in range(min(max(1,clip.duration),max_ticks)):
        step()
        world.cpu.call(actor_sim.ADVANCE_SKELETAL,(actor.address,),budget=200000,sp=actor_sim.STACK)

def _entity_actor(scene,instance,animations=None,need_table=True):
    """(world, actor, animation table) of the selected entity, initialised by its own code."""
    handler=event_script.handler_for(scene,instance)
    if not handler:raise ValueError('No verified native handler is associated with this entity.')
    idx=getattr(scene,'idx_path',str(Path(scene.dat_path).with_name('TOMBA2.IDX')))
    world=actor_sim.World(scene.exe_path,scene.overlay_path,scene.dat_path,idx,
                         scene.chunk_index,scene.area_index)
    world.static_edit=True;world.start_workers()
    if instance.scene is not None:
        spawner=scene._scene_spawner()
        if not spawner:raise ValueError('The interior spawner is unresolved.')
        world.enter_scene(spawner,scene.area_index,instance.scene)
    elif instance.placement is not None:
        world.place(instance.placement,actor_assembly.degrees_to_units)
    else:
        spawner=scene._scene_spawner()
        if not spawner:raise ValueError('The entity’s native scene entry point is unresolved.')
        world.enter_scene(spawner,scene.area_index,0)
    world.run(12,workers=False)
    actors=[a for a in world.actors if a.handler==handler and a.parts and not a.dead]
    if not actors:
        # A controller that spawns the real body (a boss): the model is the nearest actor that has parts.
        here=np.array(instance.game_position,dtype=float)
        near=[a for a in world.actors if a.parts and not a.dead and a.position is not None
              and float(np.linalg.norm(np.asarray(a.position,dtype=float)-here))<=800]
        if len(near)==1:actors=near
    if not actors:raise ValueError('The selected entity did not initialise a model for preview.')
    actors.sort(key=lambda a:float(np.linalg.norm(a.position-np.array(instance.game_position))))
    if (len(actors)>1 and abs(np.linalg.norm(actors[0].position-instance.game_position)-np.linalg.norm(actors[1].position-instance.game_position))<1
            and world.mem.read(actors[0].address+0x7c,4)!=world.mem.read(actors[1].address+0x7c,4)):
        # Identical copies sharing one animation table preview the same.
        raise ValueError('Several identical native actors match this selection; preview target is ambiguous.')
    actor=actors[0];table=world.mem.read(actor.address+0x7c,4)
    if not actor_sim._in_ram(table) and animations and actor_sim._in_ram(animations):
        # Attached when the page starts (f_InitializeActorScript's literal).
        table=animations;world.mem.write(actor.address+0x7c,4,table)
    if need_table and not actor_sim._in_ram(table):raise ValueError('This entity has no native skeletal animation table.')
    return world,actor,table


def probe_pages(scene,instance,animations=None,ticks=60):
    """The scripts this entity's own code starts when Tomba is ready to talk to
    it: its handler runs in the emulator beside the actor, with
    f_TryPrepareTombaForInteraction answering yes, and every script load made
    for that actor is noted. Returns the sorted script addresses. This is the
    state the world starts in; pages that only run after other quest events
    are not reached."""
    from psx.cpu import PASS
    world,actor,_table=_entity_actor(scene,instance,animations,need_table=False)
    cpu=world.cpu;loads=[]
    def watch(reg):
        def hook(c):
            loads.append((c.r[4],c.r[reg]));return PASS
        return hook
    cpu.hooks[event_script.LOAD_SCRIPT]=watch(5);cpu.hooks[0x80040CDC]=watch(6)
    asked=[0]
    def ready(c):
        asked[0]+=1;return 1
    cpu.hooks[0x8005308C]=ready
    world.run(ticks,workers=False)
    probe_pages.asked=asked[0];probe_pages.all_loads=list(loads)
    return sorted({script for who,script in loads if who==actor.address})

def animation_catalogue(scene,instance=None,animations=None,who='self'):
    """[(id, ticks, loops)] for every animation in the table of the entity
    (or Zippo, who='zippo'), read from the emulated actor's own table."""
    from formats.animation.sequences import read_clip
    if who=='zippo':
        world,actor=_zippo_actor(scene);table=world.mem.read(actor.address+0x7C,4)
    else:
        world,actor,table=_entity_actor(scene,instance,animations)
    if not actor_sim._in_ram(table):raise ValueError('No animation table.')
    ram=bytes(world.mem.ram);out=[]
    for n in range(256):
        pointer=world.mem.read(table+n*4,4)
        if not actor_sim._in_ram(pointer):break
        try:clip=read_clip(ram,actor_sim.RAM_BASE,pointer,actor_sim._ANY_POSE)
        except Exception:break
        out.append((n,clip.duration,clip.loop_start is not None))
    return out


def animation_frames(scene,instance,command,max_ticks=120,animations=None,previous=None,blend=None):
    """Initialise just the selected scene and sample one requested animation.

    No level-wide recording or guessed model/animation pairing is used.
    Returned parts can be posed with the level viewer's existing assembly.
    """
    if command.opcode not in (1,30,57,58):raise ValueError('This action has no decoded skeletal animation preview.')
    values=command.operands
    if command.opcode==30:
        if values[0]&1:raise ValueError('This action targets the current script actor, which is not resolved to the selected entity.')
        animation,mode=values[1],values[2]
    elif command.opcode in (57,58):
        # f_StartSynchronizedActorAndTombaAnimations: actor +0x74, blend 4.
        if values[0]&0x8000:raise ValueError('This action animates the current script actor, which is not resolved to the selected entity.')
        if command.opcode==58 and values[0]&0x7fff==2:raise ValueError('Turn-only form: no animation is started.')
        animation,mode=values[1],4
    else:
        if values[0]&0x8000:raise ValueError('This presentation animates another script actor; its target is unresolved.')
        animation,mode=values[2],4
    if animation<0:raise ValueError('The animation is selected by NPC code at runtime.')
    if animation>255:raise ValueError('Animation ID is outside the native actor range.')
    world,actor,table=_entity_actor(scene,instance,animations)
    pointer=world.mem.read(table+animation*4,4)
    from formats.animation.sequences import read_clip
    clip=read_clip(bytes(world.mem.ram),actor_sim.RAM_BASE,pointer,actor_sim._ANY_POSE)
    transform=world._transform_of(actor.handler)
    if transform is None:raise ValueError('The entity’s part-transform routine is unresolved.')
    # Unblended (mode 0): a non-zero mode is f_StartActorSkeletalAnimationBlend,
    # which eases over that many ticks out of whatever pose the actor holds -
    # here an arbitrary idle tick, so those first frames were noise.
    # With the animation that was playing before (`previous`), start from its
    # end pose and ease in over `blend` ticks as the game does.
    ease=0
    if previous is not None and previous!=animation and previous>=0:
        _hold(world,actor,table,previous,lambda:world.cpu.call(transform,(actor.address,),budget=200000,sp=actor_sim.STACK),max_ticks)
        ease=mode if blend is None else blend
    world.cpu.call(0x80041718,(actor.address,animation,ease),sp=actor_sim.STACK)
    models,_=world._maps();frames=[]
    # The ease uses up `ease` ticks of the clip's time: the clip itself then plays on, that much later.
    for tick in range(min(max(1,clip.duration),max_ticks)+ease):
        world.cpu.call(transform,(actor.address,),budget=200000,sp=actor_sim.STACK)
        parts=world._parts(actor,models)
        if not parts:raise ValueError('The native animation produced no model parts.')
        frames.append(parts)
        world.cpu.call(actor_sim.ADVANCE_SKELETAL,(actor.address,),budget=200000,sp=actor_sim.STACK)
    eased=f'; in game it eases in over {mode} ticks from the previous pose' if mode else ''
    loop=clip.starts[clip.loop_start]+ease if clip.loop_start is not None and clip.duration<=max_ticks else None
    looping=f'; loops from tick {loop}' if loop is not None else ''
    return frames,f'Animation {animation}: {len(frames)} native ticks at 30 Hz'+(' (preview capped)' if clip.duration>max_ticks else '')+looping+eased,loop

TOMBA_BONES = 0x800A3FA8   # 17-bone tree (parent, x, y, z)

def tomba_catalogue(scene):
    """[(id, ticks, loops, name)] for every animation Tomba's verified US table holds."""
    from game import game_build
    if game_build.current().name!='us-retail':raise ValueError('Tomba animation list requires the verified US table.')
    from formats.animation import anmp_parser,sequences
    exe=Path(scene.exe_path).read_bytes()
    where=getattr(scene,'resident',{}).get(4)
    if not where:raise ValueError('Tomba’s animation file (resident 4) is not in this scene.')
    start,(offset,size)=where
    anmp=anmp_parser.load_anmp(scene.dat_path,start+offset,size)
    bank=sequences.tomba_bank([('MAIN.EXE',exe)],anmp,4)
    if bank is None:raise ValueError('Unverified executable or ANMP; Tomba’s animations are not guessed.')
    return sorted((c.id,c.duration,c.loop_start is not None,c.name or '') for c in bank.clips)

def tomba_frames(scene,animation,max_ticks=240):
    """Tomba's model and one scripted animation, posed from the verified
    US table (f_SetTombaAnimation's IDs). Returns a dict for SMSTViewer:
    model (standing), pivots, frames (transforms per tick), hidden groups,
    spares, loop tick, note."""
    from game import game_build
    if game_build.current().name!='us-retail':raise ValueError('Tomba animation previews require the verified US table.')
    from formats.animation import anmp_parser,sequences,skeleton_tree,game_rest
    from formats.animation.anmp_skeleton import pose_transforms,SPARES
    from formats.models.smst_parser import load_smst
    exe=Path(scene.exe_path).read_bytes();base=struct.unpack_from('<I',exe,24)[0]
    where=getattr(scene,'resident',{}).get(4)
    if not where:raise ValueError('Tomba’s animation file (resident 4) is not in this scene.')
    start,(offset,size)=where
    anmp=anmp_parser.load_anmp(scene.dat_path,start+offset,size)
    bank=sequences.tomba_bank([('MAIN.EXE',exe)],anmp,4)
    if bank is None:raise ValueError('Unverified executable or ANMP; Tomba’s animations are not guessed.')
    clip=next((c for c in bank.clips if c.id==animation&0x7fff),None)
    if clip is None:raise ValueError(f'Tomba has no animation {animation&0x7fff}.')
    idx=getattr(scene,'idx_path',str(Path(scene.dat_path).with_name('TOMBA2.IDX')))
    trail=actor_sim.World._trail(idx,1)
    model=load_smst(scene.dat_path,trail[0],trail[1]-trail[0])
    bones=skeleton_tree.read_table(exe,TOMBA_BONES-base+0x800,17)
    hierarchy=game_rest.hierarchy(bones)
    spares=dict(SPARES.get(17,{}))
    packed=model['vertices']
    vertices,pivots=game_rest.rest_pose(model,bones,spares)
    model=dict(model,vertices=vertices.tolist())
    poses={f.index:f for f in anmp.frames}
    scales=sequences.scale_states(clip,poses)
    frames=[]
    for tick in range(min(clip.duration,max_ticks)):
        row,nxt,amount=clip.sample(tick)
        rotations,translation,_=anmp_parser.blend(poses[clip.steps[row].pose],
            poses[clip.steps[nxt].pose] if nxt is not None else None,amount)
        frames.append(pose_transforms(rotations,translation,hierarchy,pivots,scales=scales[row]))
    # Each bone draws one group; the clip picks the alternate head/hands.
    drawn={clip.parts.get(b,b) for b in range(17)}
    hidden=set(range(len(model['groups'])))-drawn
    shown={g:b for g,b in spares.items() if g in drawn}
    loop=clip.starts[clip.loop_start] if clip.loop_start is not None and clip.duration<=max_ticks else None
    name=f' ({clip.name})' if clip.name else ''
    note=(f'Tomba animation 0x{clip.id:02X}{name}: {len(frames)} ticks at 30 Hz'
          +(' (preview capped)' if clip.duration>max_ticks else '')
          +(f'; loops from tick {loop}' if loop is not None else '; ends on its last pose'))
    by_bone=[clip.parts.get(b,b) for b in range(17)]
    raw=np.array(packed,dtype=np.float64)
    groups=model['groups']
    def lowest(pose):
        return min(float(((raw[g.first_vertex:g.first_vertex+g.vertex_count]@pose[b][0].T)+pose[b][1])[:,1].min())
                   for b,g in ((b,groups[i]) for b,i in enumerate(by_bone)) if g.vertex_count)
    return dict(model=model,pivots=pivots,frames=frames,hidden=hidden,spares=shown,loop=loop,note=note,
                by_bone=by_bone,feet=lowest(frames[0]))

# The camera, run by the game's own code.
CAMERA_BLOCK = 0x800E8008
CAMERA_MODE = CAMERA_BLOCK+0x64
CAMERA_INIT, CAMERA_UPDATE = 0x80078610, 0x8006EC44
CAMERA_EYE, CAMERA_FOCUS = 0x1F8000D0, 0x1F8000DC
TOMBA_POS, TOMBA_SCRATCH = actor_sim.PLAYER+0x2C, 0x1F800160
COMMAND_BLOCK = 0x80150000          # free RAM (0x8014E944..0x80157002)
CAMERA_OPS = frozenset((21,22,23,24,25))

def _s32(v):return v-(1<<32) if v&0x80000000 else v

def camera_path(scene,commands,upto,tomba,ticks=90):
    """[(eye, focus), ...] per tick in game axes, from running the game's
    camera: rows before `upto` that touch the camera run through their own
    callbacks and settle, then row `upto` runs and is recorded. `tomba` is
    where Tomba is taken to stand (x, height, depth)."""
    idx=getattr(scene,'idx_path',str(Path(scene.dat_path).with_name('TOMBA2.IDX')))
    world=actor_sim.World(scene.exe_path,scene.overlay_path,scene.dat_path,idx,scene.chunk_index,scene.area_index)
    world.static_edit=True;world.start_workers()
    mem,cpu=world.mem,world.cpu
    exe=Path(scene.exe_path).read_bytes();base=struct.unpack_from('<I',exe,24)[0]
    for k,v in enumerate(tomba):
        mem.write(TOMBA_POS+4*k,4,(int(v)<<16)&0xffffffff)
        mem.write(TOMBA_SCRATCH+2*k,2,int(v)&0xffff)
    for i in range(0,0x98,4):mem.write(CAMERA_BLOCK+i,4,0)
    cpu.call(CAMERA_INIT,(),sp=actor_sim.STACK)
    def tick():
        cpu.call(CAMERA_UPDATE,(),sp=actor_sim.STACK)
        read=lambda at:tuple(_s32(mem.read(at+4*k,4))/65536 for k in range(3))
        return read(CAMERA_EYE),read(CAMERA_FOCUS)
    def run(c):
        """Call the command's callback the way the script runner does."""
        for i in range(0,0x100,4):mem.write(COMMAND_BLOCK+i,4,0)
        for n,b in enumerate(c.raw):mem.write(COMMAND_BLOCK+0xC0+n,1,b)
        mem.write(COMMAND_BLOCK+0x6C,4,COMMAND_BLOCK+0xC0)
        o=c.operands
        for k,v in enumerate(o[:3]):mem.write(COMMAND_BLOCK+0x72+2*k,2,v&0xffff)
        for k,v in enumerate(o[3:]):mem.write(COMMAND_BLOCK+0x64+2*k,2,v&0xffff)
        callback=struct.unpack_from('<I',exe,event_script.CALLBACKS-base+0x800+c.opcode*4)[0]
        cpu.call(callback,(COMMAND_BLOCK,),sp=actor_sim.STACK)
        if c.opcode==54:      # Tomba moved: keep the scratchpad copy in step
            for k in range(3):mem.write(TOMBA_SCRATCH+2*k,2,mem.read(TOMBA_POS+4*k+2,2))
        return cpu.r[2]
    for _ in range(3):tick()
    for c in commands[:upto]:
        if c.opcode in CAMERA_OPS or c.opcode==54:
            run(c)
            for _ in range(30):tick()
    selected=commands[upto]
    path=[]
    done=run(selected) if selected.opcode in CAMERA_OPS or selected.opcode==54 else True
    for n in range(ticks):
        path.append(tick())
        if not done and selected.opcode==21:done=run(selected)   # transform: polled until finished
    mode=mem.read(CAMERA_MODE,1)
    if path and not any(path[-1][1]) and any(tomba):
        # The camera chased a target the simulation never set (an actor it
        # does not run), so it ended on the world origin: not a view to show.
        return [],(f'camera mode {mode&0x3f} follows a target this simulation does not have, '
                   'so the viewport keeps its own camera.')
    return path,f'Camera mode {mode&0x3f} after this row; {ticks} ticks of the game’s own camera code.'

def native_code(exe,overlay,command,count=64):
    read=event_script.reader(exe,overlay)
    callback=struct.unpack('<I',read(event_script.CALLBACKS+command.opcode*4,4))[0]
    return f'Command bytes: {command.raw.hex(" ")}\n'+native_code_at(exe,overlay,callback,count)

FIELD_NAMES={0x05:'phase',0x2E:'X',0x32:'height',0x36:'depth',0x5E:'behaviour',0x60:'item it hands back',0x40:'timer',0x2B:'item contact'}
GLOBAL_WORDS={0x800BF844:'the spawned pickup'}
_LOAD_WIDTH=('lw','lh','lhu','lb','lbu')

def _sym_text(sym):
    """A tracked value in words: the actor in a0, a game global, or a field of one of them."""
    if sym is None:return '?'
    if sym[0]=='arg':return 'this actor'
    if sym[0]=='ret':return 'the actor it just created'
    if sym[0]=='g':return GLOBAL_WORDS.get(sym[1]) or f'the value in {event_script._global(sym[1])}'
    if sym[0]=='f':
        base=_sym_text(sym[1]);name=FIELD_NAMES.get(sym[2])
        return f'{name or "field +0x%02X"%sym[2]} of {base}' if sym[1][0]=='arg' or True else base
    return '?'

def native_steps(exe,overlay,callback,count=200):
    """What a straight-line native routine visibly does: the functions it
    calls with their arguments and the memory it writes - fixed globals, and
    fields of the actor in a0 or of an actor a global points to (the pickup
    a script spawned). Branches are not followed (the first path through is
    read), so this is a sketch, not a proof.
    [('set', label, value) | ('call', name, [args], [argument words]) | ('field', owner words, field, value words)]"""
    from psx.mips import Image
    data,base=(overlay,event_script.BASE) if callback>=event_script.BASE else (exe[0x800:],struct.unpack_from('<I',exe,24)[0])
    image=Image(data,base)
    if callback not in image:return []
    regs={0:0};sym={4:('arg',)};lines=[]
    def const(r):return regs.get(r)
    def drop(r):regs.pop(r,None);sym.pop(r,None)
    widths={'sb':0xff,'sh':0xffff,'sw':0xffffffff}
    pending=None;leaving=False
    for ins in _path(image,callback,count):
        n=ins.name
        if pending is not None or leaving:      # the delay slot: it runs before the call / return takes effect
            pass
        if n in _LOAD_WIDTH:
            owner=sym.get(ins.rs);fixed=const(ins.rs)
            drop(ins.rt)
            if fixed is not None and fixed>=0x80000000:sym[ins.rt]=('g',(fixed+ins.imm)&0xffffffff)
            elif owner is not None:sym[ins.rt]=('f',owner,ins.imm&0xffff)
        elif n in widths and const(ins.rs) is None and ins.rs in sym:
            value=const(ins.rt)
            lines.append(('field',_sym_text(sym[ins.rs]),ins.imm&0xffff,str(_signed(value)) if value is not None else _sym_text(sym.get(ins.rt))))
        elif n=='lui':regs[ins.rt]=(ins.unsigned<<16)&0xffffffff;sym.pop(ins.rt,None)
        elif n in ('addiu','ori') and const(ins.rs) is not None:
            regs[ins.rt]=((const(ins.rs)+ins.imm) if n=='addiu' else (const(ins.rs)|ins.unsigned))&0xffffffff;sym.pop(ins.rt,None)
        elif n in ('addu','or') and ins.rt==0 and const(ins.rs) is not None:regs[ins.rd]=const(ins.rs);sym.pop(ins.rd,None)
        elif n in ('addu','or') and ins.rt==0 and ins.rs in sym:sym[ins.rd]=sym[ins.rs];regs.pop(ins.rd,None)
        elif n in widths and const(ins.rs) is not None and const(ins.rt) is not None and const(ins.rs)>=0x80000000:
            lines.append(('set',event_script._global((const(ins.rs)+ins.imm)&0xffffffff),const(ins.rt)&widths[n]))
        elif n=='jal':pending=ins.target;continue
        elif n=='jr' and ins.rs==31:leaving=True;continue
        elif _dest_reg(ins) is not None and n not in widths:drop(_dest_reg(ins))
        if pending is not None:
            lines.append(('call',event_script._symbol(pending),[const(r) for r in (4,5,6,7)],[_sym_text(sym.get(r)) if const(r) is None and r in sym else None for r in (4,5,6,7)]))
            for r in range(2,26):drop(r)
            sym[2]=('ret',)
            pending=None
        if leaving:break
    return lines

def _path(image,start,count):
    """The instructions of the path a routine most likely takes: straight on, except that a branch
    guarding an early return (`if (x == 0) return;`) is taken, so the real work after it is read."""
    out=[];at=start
    while len(out)<count and at in image:
        i=image.at(at);out.append(i)
        if i.name in ('beq','bne','blez','bgtz','bltz','bgez') or i.name in ('j','jr'):
            if at+4 in image:out.append(image.at(at+4))
            if i.name=='jr':break
            if i.name=='j':at=i.target;continue
            fall=at+8
            guard=any(fall+4*k in image and image.at(fall+4*k).name=='jr' and image.at(fall+4*k).rs==31 for k in range(3))
            at=i.target if guard and i.target in image else fall
            continue
        at+=4
    return out

def _dest_reg(ins):
    d=event_script._dest(ins)
    return d if d not in (None,0) else None

def native_gist(exe,overlay,callback,count=200):
    """native_steps as short technical lines."""
    out=[]
    for step in native_steps(exe,overlay,callback,count):
        if step[0]=='set':out.append(f'sets {step[1]} = {step[2]}')
        elif step[0]=='field':out.append(f'sets {FIELD_NAMES.get(step[2],"+0x%02X"%step[2])} of {step[1]} = {step[3]}')
        else:
            shown=', '.join('?' if v is None else (f'0x{v:X}' if v>=0x8000 else str(v)) for v in step[2])
            out.append(f'calls {step[1]}({shown})')
    return out

def _signed(v):
    return v-0x100000000 if v is not None and v>=0x80000000 else v

# Known game routines in plain words: name -> (arguments) -> phrase.
PLAIN={
 'f_AddItemWithMessage':lambda a:f'give {event_script.item_label(a[0])} (with its message)' if a[0] is not None else 'give an item (with its message)',
 'f_AddItemWithoutMessage':lambda a:f'give {event_script.item_label(a[0])}' if a[0] is not None else 'give an item',
 'f_RemoveItemWithMessage':lambda a:f'take {event_script.item_label(a[0])} (with its message)' if a[0] is not None else 'take an item',
 'f_RemoveItemWithoutMessage':lambda a:f'take {event_script.item_label(a[0])}' if a[0] is not None else 'take an item',
 'f_RemoveItemWithAltMessage':lambda a:f'take {event_script.item_label(a[0])} (other message)' if a[0] is not None else 'take an item',
 'f_PlaySoundEffect':lambda a:f'play sound 0x{a[0]:02X}' if a[0] is not None else 'play a sound',
 'f_PlaySoundEffectWithPitchOffset':lambda a:f'play sound 0x{a[0]:02X}' if a[0] is not None else 'play a sound',
 'f_SpawnTransientEffectAtFixedPosition':lambda a:f'spawn effect {a[0]} at a fixed spot' if a[0] is not None else 'spawn an effect',
 'f_SpawnFlaggedTransientEffectAtFixedPosition':lambda a:f'spawn effect {a[0]} at a fixed spot' if a[0] is not None else 'spawn an effect',
 'f_AllocateActor':lambda a:'create a helper actor',
 'f_SetCameraMode':lambda a:'set the camera mode',
 'f_ApproachFixedPosition3D':lambda a:'move toward a fixed point',
 'f_ApproachCameraPositionAndRebuildView':lambda a:'move the camera',
 'f_StartEvent':lambda a:f'start {event_script.event_label(_signed(a[0]))}' if a[0] is not None else 'start an event',
 'f_UpdateEvent':lambda a:f'complete {event_script.event_label(_signed(a[0]))}' if a[0] is not None else 'complete an event',
 'f_QueueResourceMessage':lambda a:f'show system message {a[0]}' if a[0] is not None else 'show a system message',
 'f_QueueItemMessage':lambda a:f'show the message for {event_script.item_label(a[0])}' if a[0] is not None else 'show an item message',
 'f_QueuePopupMessage':lambda a:'show a popup message',
 'f_SetTombaAnimationIfChanged':lambda a:(f'Tomba animation {a[1] if a[0] is not None and a[0]>=0x80000000 else a[0]}' if a[0] is not None else 'change Tomba’s animation'),
 'f_SetActorAnimationIfChanged':lambda a:f'play animation {a[1]} on the actor' if a[1] is not None else 'change the actor’s animation',
 'f_SpawnGoldFishhookPickup':lambda a:'spawn the gold fishhook pickup',
 'f_SetCameraFocusPosition':lambda a:'set where the camera looks',
 'f_StartCameraTransformInterpolation':lambda a:'start a camera move',
 'f_UpdateCameraTransformInterpolation':lambda a:'continue the camera move',
 'f_RestorePreviousCameraMode':lambda a:'restore the previous camera mode',
 'f_CalculateYawFromPositionToPoint':lambda a:'work out which way to face',
 'f_StepActorYawTowardAngle':lambda a:'turn the actor toward an angle',
 'f_StepAngleTowardTarget':lambda a:'turn toward an angle',
 'f_StartTombaScriptedAnimation':lambda a:f'Tomba animation {a[1]&0x7fff}' if a[1] is not None else 'change Tomba’s animation',
 'f_SetTombaAnimation':lambda a:'change Tomba’s animation',
 'f_SetActorAnimation':lambda a:f'play animation {a[1]} on the actor' if a[1] is not None else 'change the actor’s animation',
 'f_StartActorSkeletalAnimation':lambda a:f'play animation {a[2]} on the actor' if a[2] is not None else 'change the actor’s animation',
 'f_SetScriptActorInteractionOffset':lambda a:'set where Tomba stands for the talk',
 'f_SetCurrentScriptActorMode':lambda a:f'put Zippo in mode {a[0]}' if a[0] is not None else 'change Zippo’s mode',
 'f_SetCameraTransform':lambda a:'set the camera transform',
 'f_DrawFullscreenOverlay':lambda a:'draw a full-screen overlay (fade)',
 'f_SpawnMovingTransientEffectAtFixedPosition':lambda a:f'spawn moving effect {a[0]}' if a[0] is not None else 'spawn a moving effect',
 'f_SpawnAreaLinkedItemPickupActor':lambda a:'spawn this area’s linked item pickup',
 'f_SpawnLinkedItemPickupActor':lambda a:'spawn the linked item pickup',
 'f_StartControllerVibration':lambda a:'vibrate the controller',
 'f_SetActorPartModel':lambda a:'change a model part',
 'f_ReleaseActorModelParts':lambda a:'release the model parts',
 'f_InitializeMultiPartActorModel':lambda a:'set up a multi-part model',
 'f_BeginScriptedInteractionAudio':lambda a:f'start conversation music cue {a[1]}' if a[1] is not None else 'start conversation music',
 'f_StartActorTimedFrameSequence':lambda a:'start a timed frame sequence',
}
OMIT_CALLS=frozenset(('rand','rsin','rcos','ratan2','SquareRoot0'))   # arithmetic helpers: noise in a summary

def _plain_call(name,consts):
    """A call in words, '' for arithmetic noise, None when the routine is not known."""
    if name in OMIT_CALLS:return ''
    if name in ('f_SpawnAreaLinkedItemPickupActor','f_SpawnLinkedItemPickupActor'):
        return f'spawn a pickup of {event_script.item_label(consts[1])}' if consts[1] is not None else 'spawn a pickup of the item it hands back'
    if name in PLAIN:
        try:return PLAIN[name](consts)
        except Exception:return name
    return None

def native_paths(exe,overlay,callback):
    """Every path through a native routine as guarded effects (game/native_decode.py)."""
    from game import native_decode
    try:return native_decode.decode(exe,overlay,callback,_plain_call,event_script._symbol,event_script._global)
    except (ValueError,IndexError,struct.error,KeyError):return []

def native_c(exe,overlay,callback):
    """The routine, and the overlay routines it runs on its actor, as C (game/native_decode.py)."""
    from game import native_decode
    try:return native_decode.c_text(exe,overlay,callback,event_script._symbol,event_script._global)
    except (ValueError,IndexError,struct.error,KeyError):return ''

def native_plain(exe,overlay,callback):
    """The routine in plain words, for a row: the distinct things it does, in order.
    [] when it does nothing it can name."""
    from game import native_decode
    return native_decode.summary(native_paths(exe,overlay,callback))

def native_code_at(exe,overlay,callback,count=64,tag=None):
    from psx.mips import Image
    data,base=(overlay,event_script.BASE) if callback>=event_script.BASE else (exe[0x800:],struct.unpack_from('<I',exe,24)[0])
    image=Image(data,base)
    if callback not in image:raise ValueError('Callback code is not present in the current area.')
    lines=[]
    for ins in image.walk(callback,count):
        lines.append(f'{ins.address:08X}  {ins.word:08X}  {ins.text()}')
        if ins.name=='jr' and ins.rs==31:
            if ins.address+4 in image:lines.extend(image.text(ins.address+4,1))
            break
    c,source='',''
    if tag:
        from game import decomp_view
        c=decomp_view.function_with_callees(tag,callback) or ''
        source='the project'+chr(8217)+'s Ghidra export'
    if not c:
        c=native_c(exe,overlay,callback);source='decoded from the machine code (this routine is not in the Ghidra export)'
        if c.count(chr(10))>500:c=chr(10).join(c.split(chr(10))[:500])+chr(10)+'/* ... cut: the routine branches too much to print in full ... */'
    head=(f'As C, {source}; a routine like this runs every tick until it returns 1:'+chr(10)+c+chr(10)+chr(10)) if c else ''
    return f'Callback 0x{callback:08X}\n\n'+head+'\n'.join(lines)

def sound_samples(exe,overlay,snd,area,sound_id,pitch_offset=0):
    from game import game_build
    if game_build.current().name!='us-retail':raise ValueError('Sound-command previews require the verified US layout.')
    from formats.audio import sfx
    base=struct.unpack_from('<I',exe,24)[0]
    number=sound_id&255
    if (number&0x80 and number&0x7f>=sfx.AREA_SOUNDS) or (not number&0x80 and number>=sfx.RESIDENT_SOUNDS):
        raise ValueError('Sound ID is outside the native sound-definition range.')
    if number&0x80:
        pointer=struct.unpack_from('<I',exe,sfx.AREA_TABLES-base+0x800+area*4)[0]
        at=pointer-event_script.BASE+(number&0x7f)*sfx.DEFINITION_SIZE;blob=overlay;bank=sfx.AREA_BANK_BASE+area
    else:
        at=sfx.DEFINITIONS-base+0x800+number*sfx.DEFINITION_SIZE;blob=exe;bank=sfx.EFFECTS_BANK
    entry=blob[at:at+sfx.DEFINITION_SIZE]
    if len(entry)!=8:raise ValueError('Sound definition is outside its native table.')
    _,_,program,tone,note,fine,_,spare=entry
    if spare:raise ValueError('Sound definition is not verified.')
    if tone&sfx.AREA_TONE:bank=sfx.AREA_BANK_BASE+area;tone&=~sfx.AREA_TONE
    banks=sfx.find_banks(snd)
    if bank>=len(banks):raise ValueError('The required sound bank is unavailable.')
    programs=banks[bank]['offset']+sfx.HEADER
    used=[p for p in range(128) if snd[programs+p*16]]
    if program not in used or tone>=snd[programs+program*16]:raise ValueError('Sound program/tone is unavailable.')
    at=programs+sfx.PROGRAM_TABLE+used.index(program)*sfx.TONE_TABLE+tone*32
    center,shift=snd[at+4],snd[at+5];vag=struct.unpack_from('<h',snd,at+22)[0]
    sample=next(((o,n) for b,i,o,n in sfx.samples(snd) if b==bank and i==vag),None)
    if sample is None:raise ValueError('The sound’s waveform is unresolved.')
    rate=min(sfx.SPU_MAX_RATE,round(sfx.SPU_RATE*2**((note-center+fine/128-shift/100+pitch_offset)/12)))
    return sfx.decode(snd,*sample),rate,f'Sound {number}: bank {bank}, waveform {vag}; SPU envelope and game mixing are not simulated.'

def resident_voice_samples(exe,image,voice_id):
    from formats.audio import voice,xa
    from game import game_build
    if game_build.current().name!='us-retail':raise ValueError('Resident voice previews require the verified US layout.')
    if voice_id<0:return None,0,'This command stops voice playback.'
    base=struct.unpack_from('<I',exe,24)[0]
    at=0x8009d110-base+0x800+voice_id*6
    if at<0 or at+6>len(exe):raise ValueError('Resident voice descriptor is outside the executable.')
    channel,flags,start,length=struct.unpack_from('<BBHH',exe,at)
    lba,sectors=voice.find_track(image)
    if channel>=32 or flags>1 or not length or (start+length)*8>sectors:
        raise ValueError('This voice ID does not resolve to a verified resident XA range.')
    selected=voice.clip_sectors((start,length),channel,sectors)
    with open(image,'rb') as source:
        samples,rate=xa.decode_channel(source,lba,selected,frame=xa.framing(image) or xa.RAW)
    return samples,rate,f'Resident voice {voice_id}, channel {channel}, {len(samples)/rate:.2f}s'

def duration(c):
    """Ticks an action is known to take on its own, or None."""
    o=c.operands
    if c.opcode==5:return o[0]+1
    if c.opcode==47 and o[0] in (1,2):return max(0,o[2])
    return None

def trace(commands,upto,start):
    """Where the actor goes, in list order through row `upto`.

    Steps: (row, kind, from, to, ticks). Positions are game (x, height,
    depth). Branches are not chosen; this follows the rows as listed."""
    pos=np.array(start,dtype=float);steps=[]
    for row,c in enumerate(commands[:upto+1]):
        o=c.operands;op=c.opcode
        if op==17:
            new=np.array(o[:3],dtype=float);steps.append((row,'set',pos.copy(),new,0));pos=new
        elif op==35 and 0<=o[0]<=2:
            new=pos.copy();new[o[0]]=o[2]
            speed=abs(o[3])/256 if c.extended and o[3] else None
            ticks=round(abs(new[o[0]]-pos[o[0]])/speed) if speed else None
            steps.append((row,'move',pos.copy(),new,ticks));pos=new
        elif op==36:
            new=np.array(o[:3],dtype=float)
            ticks=round(float(np.hypot(*(new-pos)[[0,2]]))/abs(event_script.move_speed(c))) if c.extended else None
            steps.append((row,'move',pos.copy(),new,ticks));pos=new
        elif op==22 and o[2]==0 and c.extended:
            steps.append((row,'camera',pos.copy(),np.array(o[3:6],dtype=float),0))
        elif op==22 and o[2]==1 or op==24:
            steps.append((row,'camera-tomba',pos.copy(),pos.copy(),0))
        else:
            ticks=duration(c)
            if ticks:steps.append((row,'wait',pos.copy(),pos.copy(),ticks))
    return steps


def music_cue(snd, overlay_name, overlay, cue):
    """(wav bytes, note) for a conversation-music cue, as this area plays it.

    The cue picks a sound ID (f_BeginScriptedInteractionAudio); sounds
    0x72-0x75 open resident SEQ slots 4-7 in TOMBA2.SND, 0x76-0x78 slots
    10-12, which an area fills with SEQs of its own overlay."""
    from formats.audio import seq
    from formats.audio.xa import wav_bytes_raw
    sound = event_script.music_sound(cue)
    if sound is None:
        raise ValueError(f'Cue {cue} is outside the game\'s cue table (1-8).')
    slot = event_script.music_slot(sound)
    if slot is not None and slot < 10:
        at = dict(seq.resident(snd)).get(slot)
        data, bank, where = snd, seq.MUSIC_BANK, 'TOMBA2.SND'
    else:
        at = dict((k, v) for k, v in seq.overlay(overlay_name, overlay) if k is not None).get(slot)
        data, bank, where = overlay, seq.bank_for(overlay_name), overlay_name
    if at is None:
        raise ValueError(f'Sound 0x{sound:02X} opens SEQ slot {slot}, which {overlay_name} does not fill - '
                         'in this area the cue plays nothing new.')
    stereo, _used = seq.render(data, at, snd, bank)
    if not len(stereo):
        raise ValueError('The SEQ rendered no notes.')
    return (wav_bytes_raw(seq.pcm(stereo), seq.RATE, 2),
            f'Cue {cue}: sound 0x{sound:02X}, SEQ slot {slot} from {where} '
            f'({len(stereo) / seq.RATE:.1f} s, loops played {seq.LOOP_PASSES}x).')



def tomba_pieces(result, frame, position, yaw, lift=0.0):
    """Per bone (m, t) in game axes, the way a Posed assembly hands them to
    LevelScene.positions: Tomba at `position` (game axes) turned to `yaw`
    (4096 = 360), feet `lift` view units up. A packed group vertex v lands
    at (v @ R.T + offset) turned and moved - the pose's own convention."""
    import math
    angle = math.radians(yaw * 360 / 4096)
    cos, sin = math.cos(angle), math.sin(angle)
    turn = np.array([[cos, 0.0, -sin], [0.0, 1.0, 0.0], [sin, 0.0, cos]])
    axes = np.array([[0.0, 0.0, 1.0], [0.0, -1.0, 0.0], [1.0, 0.0, 0.0]])   # VIEW_AXES
    x, h, d = position
    at = np.array((d, -h + lift, x))                                        # view_point
    pieces = []
    for rotation, offset in result['frames'][frame][:17]:
        pieces.append((axes @ (turn @ rotation) @ axes, axes @ (turn @ offset + at)))
    return pieces


ZIPPO_HANDLER = 0x80071A3C       # f_HandleAreaScriptActor (src_CurrentScriptActor)
ZIPPO_PARTS = 12                 # f_InitializeMultiPartActorModel(actor, 0xC, area slot 16, ...)
ZIPPO_SCALE = 0xC00              # his scale once settled in a conversation
_zippo_worlds = {}


def _zippo_actor(scene):
    """The area's Zippo, stood up by its own initialiser in an emulated
    world of this area (cached per area): (world, actor)."""
    key = (scene.dat_path, scene.chunk_index, scene.overlay_path)
    if key in _zippo_worlds:
        return _zippo_worlds[key]
    idx = getattr(scene, 'idx_path', str(Path(scene.dat_path).with_name('TOMBA2.IDX')))
    world = actor_sim.World(scene.exe_path, scene.overlay_path, scene.dat_path, idx,
                            scene.chunk_index, scene.area_index)
    world.static_edit = True; world.start_workers()
    address = world._alloc(actor_sim.ACTOR_SIZE + actor_sim.MAX_PARTS * 4)
    world.mem.load(address, bytes(actor_sim.ACTOR_SIZE + actor_sim.MAX_PARTS * 4))
    world.mem.write(address + actor_sim.CALLBACK, 4, ZIPPO_HANDLER)
    actor = actor_sim.Actor(address, ZIPPO_HANDLER)
    world.actors.append(actor); world.by_address[address] = actor
    world.run(4, only=lambda a: a is actor, workers=False)
    if not actor.parts:
        raise ValueError("Zippo's model (area file 16) did not initialise in this area.")
    _zippo_worlds[key] = (world, actor)
    return world, actor


import threading
_ZIPPO_LOCK = threading.Lock()      # one emulated Zippo, shared: two jobs at once corrupted each other's frames


def zippo_frames(scene, animation, max_ticks=180, previous=None, blend=4):
    """See _zippo_frames; jobs run one at a time because they share one emulated actor."""
    with _ZIPPO_LOCK:
        return _zippo_frames(scene, animation, max_ticks, previous, blend)


def _zippo_frames(scene, animation, max_ticks=180, previous=None, blend=4):
    """(frames, loop tick, origin, yaw) of Zippo playing `animation` from
    the conversation animation table (0x8001B860), as actor_sim Parts. With
    `previous` he starts from that animation's end pose and eases in over
    `blend` ticks, as the game does."""
    world, actor = _zippo_actor(scene)
    base = world.snapshot()
    try:
        # FUN_8007091c grows him from 0x200 to 0xC00 as he settles by Tomba.
        for field in (0xB8, 0xBA, 0xBC):
            world.mem.write(actor.address + field, 2, ZIPPO_SCALE)
        table = world.mem.read(actor.address + 0x7C, 4)
        if not actor_sim._in_ram(table): raise ValueError("Zippo has no animation table.")
        pointer = world.mem.read(table + (animation & 0xFF) * 4, 4)
        from formats.animation.sequences import read_clip
        clip = read_clip(bytes(world.mem.ram), actor_sim.RAM_BASE, pointer, actor_sim._ANY_POSE)
        transform = world._transform_of(ZIPPO_HANDLER) or 0
        def one():
            if transform:
                world.cpu.call(transform, (actor.address,), budget=200000, sp=actor_sim.STACK)
            else:
                world.run(1, only=lambda a: a is actor, workers=False)
        ease = 0
        if previous is not None and previous >= 0 and previous != animation:
            _hold(world, actor, table, previous, one)
            ease = blend
        world.cpu.call(0x80041718, (actor.address, animation & 0xFF, ease), sp=actor_sim.STACK)
        models, _ = world._maps(); frames = []
        for _tick in range(min(max(1, clip.duration), max_ticks) + ease):
            if transform:
                world.cpu.call(transform, (actor.address,), budget=200000, sp=actor_sim.STACK)
            else:
                world.run(1, only=lambda a: a is actor, workers=False)
            parts = world._parts(actor, models)
            if not parts: raise ValueError("Zippo's animation produced no model parts.")
            frames.append(parts)
            world.cpu.call(actor_sim.ADVANCE_SKELETAL, (actor.address,), budget=200000, sp=actor_sim.STACK)
        loop = clip.starts[clip.loop_start] + ease if clip.loop_start is not None and clip.duration <= max_ticks else None
        return frames, loop, np.array(actor.position if actor.position is not None else (0, 0, 0), dtype=float), 0
    finally:
        world.restore(base)


def zippo_position(tomba, tomba_yaw, flags=0, offset=None):
    """Where f_UpdateScriptActorConversationState / FUN_8007091c settle
    Zippo for a conversation: at Tomba plus 190 (or the request's own
    offsets) round Tomba's facing, 80 + 70 above. `flags` are the request
    flags (Request conversation, operand 2): bits 0-1 pick the side, 0x20
    turns 45 degrees more, 4 uses `offset` (x, height, angle) instead."""
    import math
    if flags & 4 and offset is not None:
        radius, height, turn = offset
    else:
        radius, height = 0xBE, 0x50
        turn = (0, 0x800, 0xC00, 0x400)[flags & 3] + (0x200 if flags & 0x20 else 0)
    # Flag 0x10 (f_UpdateScriptActorConversationState): the angle is measured
    # from the camera, src_CameraRotX + 0x800, not from Tomba's facing. The
    # conversation camera stands behind Tomba, looking at the one he talks
    # to, so that base is Tomba's back - never the entity in front of him.
    base = tomba_yaw + (0x800 if flags & 0x10 else 0)
    a = ((base + turn) & 0xFFF) * 2 * math.pi / 4096
    x, h, d = tomba
    return (x + radius * math.cos(a), h - height - 0x46, d - radius * math.sin(a))

