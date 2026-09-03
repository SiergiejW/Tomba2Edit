# Tomba2Edit

**Tomba! 2 modding, translation, extraction, editing, repacking and asset-export toolkit for PlayStation.**

Tomba2Edit is an open-source tool for exploring and modifying **Tomba! 2: The Evil Swine Return** game data. It provides a graphical interface for viewing and editing game assets and includes tools for working with the game's **DAT/IDX archives, ISO images, levels, 3D models, textures, sprites, animations, collision data, background maps, text and audio**.

The project is based on reverse-engineering research by the **Tomba Club** community.

> **Goal:** make Tomba! 2's internal game data accessible to modders, translators, researchers and preservationists.

## What can Tomba2Edit do?

### Game data & archives

* Read and analyze Tomba! 2 `DAT` / `IDX` game archives
* Extract and replace files inside game data
* Repack modified game data
* Batch multiple file modifications into a single repacking operation
* Work with the game's internal data structures and file offsets
* Handle sector alignment and pointer relocation when data changes size

### PlayStation disc images

* Read PlayStation CD/ISO data
* Work with BIN/CUE disc images
* Extract game data for editing
* Rebuild ISO9660 disc images with modified files
* Replace files while preserving the rest of the disc filesystem

### Level editing & visualization

Tomba2Edit can inspect several of Tomba! 2's level-related formats, including:

* **MDAT** - level geometry
* **SCLD** - collision data
* **DRWA / DRWB** - level drawmaps
* **BGMP** - background maps
* Level assets and related data

This makes the project useful for investigating how Tomba! 2 stores and renders its 3D environments.

### 3D models & animation

The tool can inspect Tomba! 2's 3D assets and export geometry for use outside the game.

Features include:

* 3D model viewing
* Texture/VRAM visualization
* Skeleton and animation handling
* Model export
* **glTF / GLB export**
* Embedded textures in GLB output
* Animation export
* Collision geometry export

Exported GLB files can be opened in applications such as **Blender** and other glTF-compatible software.

### Graphics & sprites

Tools are available for working with:

* Sprites
* Sprite sheets
* Textures
* PlayStation VRAM data
* Background graphics
* Level graphics
* Image formats used by Tomba! 2

### Text & translation

Tomba2Edit includes functionality for **editing Tomba! 2 text data**, making it useful for:

* Fan translations
* Text modifications
* Translation research
* Localization experiments
* Investigating the game's text format

### Audio

The project includes tools for working with Tomba! 2 audio data, including:

* BGM
* SFX
* Voice/audio data
* Audio extraction
* WAV output
* Optional MP3 export

## Supported / investigated formats

Some of the important Tomba! 2 data formats currently handled or investigated by the project include:

| Format              | Purpose                         |
| ------------------- | ------------------------------- |
| `DAT`               | Main game data archive          |
| `IDX`               | Game data index                 |
| `MDAT`              | Level geometry                  |
| `SMST`              | 3D assets                       |
| `TXTD`              | Text data                       |
| `SPRT`              | Sprite data                     |
| `SCLD`              | Collision data                  |
| `DRWA` / `DRWB`     | Level drawmaps                  |
| `BGMP`              | Background maps                 |
| Animation formats   | Character/object animation data |
| PlayStation ISO9660 | Disc filesystem                 |

See the `functions/`, `gui/` and `examples/` directories for implementation details and additional research tools.

## Screenshots

### Level viewer


### Translation / text editing


## Installation

### Windows

Download the latest **Tomba2Edit executable** from the repository's Releases page.

The application is currently distributed as a Windows executable.

### From source

Tomba2Edit is written in Python and uses technologies including:

* Python
* PyQt6
* NumPy
* Pillow
* OpenGL

Additional dependencies may be required depending on the functionality being used.

## Getting started

1. Obtain a legally dumped copy of **Tomba! 2: The Evil Swine Return**.
2. A **BIN/CUE** dump of the US retail PlayStation release is recommended.
3. Open `Track 1.BIN` in Tomba2Edit.
4. Explore the available game data.
5. Edit supported data.
6. Export or repack your changes.

ISO images and extracted game directories are also supported in relevant workflows.

### Important

Tomba2Edit does **not** provide copyrighted Tomba! 2 game data.

You must provide your own legally obtained game dump.

## Controls

### Free camera

Click inside a 3D viewport to enter free-camera mode.

| Key         | Action                 |
| ----------- | ---------------------- |
| `W A S D`   | Move camera            |
| `Q / E`     | Move up / down         |
| Mouse wheel | Camera speed           |
| `Shift`     | Faster camera movement |

## Project status

Tomba2Edit is an active reverse-engineering and modding project.

Some formats and editing operations are mature, while others are still being researched or developed.

### Currently working on

* Data repacking
* Text editing
* Level geometry viewing
* Level collision viewing
* Level drawmap viewing
* Game asset inspection
* 3D model and animation export
* ISO rebuilding and file replacement

See the repository history and Issues for current development.

## Reverse engineering & research

Tomba2Edit builds on reverse-engineering work carried out by the **Tomba Club** community.

For technical information about Tomba! 2's internal formats and ongoing research, see the **Tomba Club Wiki**.

The `examples/` directory also contains standalone research and format-analysis tools.

## Contributing

Contributions are welcome.

Useful areas include:

* Reverse engineering unknown formats
* Improving existing format parsers
* Testing game modifications
* Improving translation tools
* Adding exporters
* Documentation
* GUI improvements
* Creating examples and technical documentation

If you discover a bug or understand an undocumented Tomba! 2 format, please open an Issue or start a discussion.

## Credits

This project would not exist without the Tomba Club reverse-engineering community.

Special thanks to everyone who contributed research, testing, documentation and technical discoveries.

## Links

* **Tomba Club** - Tomba! 2 reverse-engineering research and technical documentation
* **Tomba Club Discord** - discussion, research and collaboration
* **Tomba2Edit source code** - this repository

## Keywords

**Tomba! 2, Tomba 2, Tombi 2, Tomba 2: The Evil Swine Return, Tombi 2: The Evil Swine Return, Tomba 2 Evil Swine Return, Tombi 2 Evil Swine Return, Tomba 2 PlayStation, Tomba 2 PS1, Tomba 2 PSX, Tomba 2 game, Tomba 2 tools, Tomba 2 editor, Tomba 2 game editor, Tomba 2 modding, Tomba 2 modding tool, Tomba 2 modding tools, Tomba 2 ROM hack, Tomba 2 ROM hacking, Tomba 2 ROM hacking tools, Tomba 2 hack, Tomba 2 fan translation, Tomba 2 translation, Tomba 2 translation tool, Tomba 2 localization, Tomba 2 text editor, Tomba 2 text editing, Tomba 2 repacker, Tomba 2 exporter, Tomba 2 extractor, Tomba 2 asset extractor, Tomba 2 asset extraction, Tomba 2 data extraction, Tomba 2 DAT extractor, Tomba 2 DAT repacker, Tomba 2 IDX, Tomba 2 DAT IDX, Tomba 2 ISO editor, Tomba 2 ISO builder, Tomba 2 BIN CUE, Tomba 2 disc image, Tomba 2 game files, Tomba 2 file formats, Tomba 2 reverse engineering, Tomba 2 research, Tomba 2 game data, Tomba 2 game assets, Tomba 2 resources, Tomba 2 archive editor, Tomba 2 archive extractor, Tomba 2 level editor, Tomba 2 level viewer, Tomba 2 level modding, Tomba 2 collision editor, Tomba 2 collision viewer, Tomba 2 map editor, Tomba 2 map viewer, Tomba 2 3D editor, Tomba 2 3D viewer, Tomba 2 3D models, Tomba 2 model viewer, Tomba 2 model extractor, Tomba 2 model exporter, Tomba 2 character models, Tomba 2 animation, Tomba 2 animation viewer, Tomba 2 animation exporter, Tomba 2 skeleton, Tomba 2 textures, Tomba 2 texture viewer, Tomba 2 texture extractor, Tomba 2 sprites, Tomba 2 sprite editor, Tomba 2 sprite extractor, Tomba 2 spritesheet, Tomba 2 VRAM, Tomba 2 graphics, Tomba 2 background graphics, Tomba 2 audio extraction, Tomba 2 sound effects, Tomba 2 BGM, Tomba 2 music extraction, Tomba 2 WAV, Tomba 2 MP3, Tomba 2 glTF, Tomba 2 GLB, Tomba 2 Blender, Tomba 2 3D export, Tomba 2 asset viewer, PlayStation modding tools, PS1 modding tools, PSX modding tools, PlayStation ROM hacking, PS1 ROM hacking, PSX ROM hacking, PlayStation reverse engineering, PS1 reverse engineering, PSX reverse engineering, PlayStation game editor, PS1 game editor, PS1 asset extraction, retro game modding, classic game modding, game preservation, video game preservation, fan localization, fan translation tools, ROM hacking tools, game reverse engineering, game asset extraction, DAT file editor, DAT file extractor, DAT repacker, IDX file, ISO9660, glTF exporter, GLB exporter, Blender game assets, Python, PyQt6.**
