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

def _entity_actor(scene,instance,animations=None):
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
    if not actor_sim._in_ram(table):raise ValueError('This entity has no native skeletal animation table.')
    return world,actor,table


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

def native_code_at(exe,overlay,callback,count=64):
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
    return f'Callback 0x{callback:08X}\n\n'+'\n'.join(lines)

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


def zippo_frames(scene, animation, max_ticks=180, previous=None, blend=4):
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
    a = ((tomba_yaw + turn) & 0xFFF) * 2 * math.pi / 4096
    x, h, d = tomba
    return (x + radius * math.cos(a), h - height - 0x46, d - radius * math.sin(a))

