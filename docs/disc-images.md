# Opening and saving disc images

The main Open command accepts CUE sheets, BIN/IMG tracks, ISO images and
Special Edition CAR files. A CUE selects its data file automatically;
keep all files named in the CUE together. Split-track and combined BIN
dumps are supported when the data track starts at `INDEX 01 00:00:00`.
A stored pregap before the data track is reported as unsupported.

Tomba! 2 Special Edition's `t2_data_us.car`, `t2_data_us_og.car` and
`t2_data_jp.car` contain raw 2352-byte Mode 2 disc sectors. They can be
opened directly. Their filename extension does not require extracting an
archive or converting the image.

Use **Build Disc (BIN/CUE/CAR)** after editing. Opening a CAR defaults to
saving an edited `.car` copy. Put that result into your own Special
Edition installation under the expected filename when testing; keep the
original image as a backup. The editor does not overwrite the open image.

BIN export copies the audio files named in the source CUE and retains
their track indices and pregaps. CAR output keeps the image's existing
audio sectors and does not create an auxiliary CUE. All unedited sectors
are preserved. Grown files are appended, their directory entries updated,
and the ISO volume length extended when necessary.

**Advanced > Save ISO** creates a 2048-byte data-only image. Use the raw
BIN/CUE or CAR export to preserve XA music and voice.

## Validation

All three local Special Edition images were opened and their required game
files, MAIN.EXE, SOP.BIN and 28 BIN overlays extracted. The US images use
the US retail executable profile; the Japanese image uses the Japanese
retail profile.

A grown A00 overlay was written into a temporary copy of each CAR, then
read back through ISO9660. The replacement bytes and new volume length
matched exactly. Only the original volume-descriptor and BIN-directory
sectors changed; every other original sector and the source image hashes
were unchanged.

The US startup overlay builds its resident file descriptors from
`CdSearchFile`, including the directory's current byte size
(`START.BIN`, `0x80106514` through `0x8010654C`). The area loader reads that
size into `0x80108F9C`. Therefore grown overlay files require updating the
ISO directory extent and size, rather than patching a fixed size in
MAIN.EXE. This verifies the loading/export path; gameplay still needs an
emulator or Special Edition test.
