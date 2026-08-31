# Tomba2Edit
## Introduction
A tool for previewing, with the goal of becoming a Tomba! 2 repacker/exporter. Based on reverse-engineered research by the Tomba Club.<br>
Check out our [discord](https://discord.com/invite/WcAb5kf) and [Youtube](https://www.youtube.com/@TombaClub). Visit [Tomba Club Wiki](https://tomba.club/wiki/Tomba!_2:_The_Evil_Swine_Return/Technical_information) for more Tomba 2 technical information.<br>
The tool aims to view models, textures, sprites, text, background maps, collision data, animations from Tomba 2.<br>

Currently working:
- Data repacking<br>
- Text editing<br>
- Level geometry viewing<br>
- Level collision viewing<br>
- Level drawmap (DRWA and DRWB) viewing<br>

## Special Thanks

This reverse-engineering effort started in the **Tomba Club Discord**, founded in 2018 by **AtanoKSi** ([SoundCloud](https://soundcloud.com/atanok-si), [YT](https://www.youtube.com/c/atanoksi)).

Huge thanks to everyone who contributed, especially:

- **vervalkon** ([X](https://x.com/vervalkon), [YT](https://www.youtube.com/channel/UCgyrTxYpBaB1Dahpz94rOEg)) - HUGE inspiration to learn Python and amazing help with figuring out *big chunk* of the logic used in this project.

- **Dedok179** ([GitHub](https://github.com/Dedok179), [YT](https://www.youtube.com/c/Dedok179)) - for demonstrating that repacking is possible and providing help with text editing and translation work.

- **All Tomba Club members** - Thank you for your research, testing, documentation, and contributions that made this project possible.

## Installation
Download Tomba2Edit.exe from the [download section](https://github.com/SiergiejW/Tomba2Edit/releases)<br>
Project uses PyQt6, struct, numpy, OpenGL, Pillow Python libraries.

## How to use:<br>
1. Preferably, get Tomba US Retail in format of BIN/CUE (Track 1.bin, Track 2.bin, .cue)<br>
2. Open Track 1.BIN <br>
3. Make edits, overwrite Track 1.BIN.<br>
4. ISO supported, but not preffered. Also extracting all contents and opening folder works. Can also save DAT/IDX to swap if you have means to open BIN/ISO.
- MDAT - Level data
- TXTD - Text data
- SMST - 3D Assets

## Controls 
**Camera movement:**<br>
To enter free camera mode, click on any 3D space.<br>
WASD   - Move camera<br>
QE     - Move up/down<br>
Scroll - Camera speed<br>
Shift  - Faster camera movement

