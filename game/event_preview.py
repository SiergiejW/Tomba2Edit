"""On-demand previews driven by the selected entity's native resources."""
import struct
from pathlib import Path
import numpy as np
from game import actor_sim, actor_assembly, event_script

def animation_frames(scene,instance,command,max_ticks=120):
    """Initialise just the selected scene and sample one requested animation.

    No level-wide recording or guessed model/animation pairing is used.
    Returned parts can be posed with the level viewer's existing assembly.
    """
    if command.opcode not in (1,30):raise ValueError('This action has no decoded skeletal animation preview.')
    values=command.operands
    if command.opcode==30:
        if values[0]&1:raise ValueError('This action targets the current script actor, which is not resolved to the selected entity.')
        animation,mode=values[1],values[2]
    else:
        if values[0]&0x8000:raise ValueError('This presentation animates another script actor; its target is unresolved.')
        animation,mode=values[2],4
    if animation<0:raise ValueError('The animation is selected by NPC code at runtime.')
    if animation>255:raise ValueError('Animation ID is outside the native actor range.')
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
    if not actor_sim._in_ram(table):raise ValueError('This entity has no native skeletal animation table.')
    pointer=world.mem.read(table+animation*4,4)
    from formats.animation.sequences import read_clip
    clip=read_clip(bytes(world.mem.ram),actor_sim.RAM_BASE,pointer,actor_sim._ANY_POSE)
    transform=world._transform_of(actor.handler)
    if transform is None:raise ValueError('The entity’s part-transform routine is unresolved.')
    world.cpu.call(0x80041718,(actor.address,animation,mode),sp=actor_sim.STACK)
    models,_=world._maps();frames=[]
    for tick in range(min(max(1,clip.duration),max_ticks)):
        world.cpu.call(transform,(actor.address,),budget=200000,sp=actor_sim.STACK)
        parts=world._parts(actor,models)
        if not parts:raise ValueError('The native animation produced no model parts.')
        frames.append(parts)
        world.cpu.call(actor_sim.ADVANCE_SKELETAL,(actor.address,),budget=200000,sp=actor_sim.STACK)
    return frames,f'Animation {animation}: {len(frames)} native ticks at 30 Hz'+(' (preview capped)' if clip.duration>max_ticks else '')

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
