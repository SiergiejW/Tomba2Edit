# Tomba2Edit
## Introduction
A tool for previewing, with the goal of becoming a Tomba! 2 repacker/exporter. Based on reverse-engineered research by the Tomba Club.<br>
Check out our [discord](discord.com/invite/WcAb5kf) and [Youtube](https://www.youtube.com/@TombaClub). Visit [Tomba Club Wiki](https://tomba.club/wiki/Tomba!_2:_The_Evil_Swine_Return/Technical_information) for more Tomba 2 technical information.<br>
The tool aims to view models, textures, sprites, text, background maps, collision data, animations from Tomba 2.

## Special thanks
This is a reverse engineering work that started in 2018 Tomba Club Discord server started by AtanoKSi.
Special thanks to: <br>
<li style="padding-left: 37px;">vervalkon for inspiring me to learn Python and figuring out MANY things here<br>
<li style="padding-left: 37px;">Dedok179 for demonstrating the repacking is possible and help with text editing/translation work<br>
<li style="padding-left: 37px;">and all Tomba Club members for their contributions to the project!<br>

## Installation
Download Tomba2Edit.exe from the [download section](https://github.com/SiergiejW/Tomba2Edit/releases)<br>
Project uses PyQt6, struct, numpy, OpenGL, Pillow Python libraries.

## How to use:<br>
Extract Tomba 2 iso and select folder, that contains BIN, CD, MOVIE<br>
The script will search for files named TOMBA2.DAT, TOMBA2.IDX, TOMBA2.IMG and display their contents.
MDAT - Level data
TXTD - Text data
SMST - 3D Assets

## Controls 
**Camera movement:**<br>
To enter free camera mode, click on any 3D space.<br>
WASD   - Move camera<br>
QE     - Move up/down<br>
Scroll - Camera speed<br>
Shift  - Faster camera movement

