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

**Tomba! 2, Tomba 2, The Evil Swine Return, PlayStation, PS1, PSX, ROM hacking, ROM hacking tools, game modding, game hacking, fan translation, localization, reverse engineering, game preservation, asset extraction, asset viewer, level editor, 3D model viewer, texture viewer, sprite editor, animation viewer, collision editor, DAT extractor, DAT repacker, IDX, BIN/CUE, ISO9660, glTF, GLB, Blender, Python, PyQt6.**
