# Open500 LA v2.9c

The LA add-ons for the **Akai MPC500** (OS 1.31), built on the [Open500](https://github.com/M45K/open500) platform: MPC3000-style timing correct and swing, S500 sampling based on the Akai S-series samplers, audio tracks, songs, a full program page, trim and zones, and print, master and input effects. They load from the memory card like samples; nothing on the MPC is changed for good.

## Download

| | |
|---|---|
| **[⬇ The add-ons (LA-v2.9c.zip)](../../releases/latest/download/LA-v2.9c.zip)** | The 14 add-on files, in the order the MPC must load them |
| **[⬇ Manual (PDF)](../../releases/latest/download/Open500-Manual-LA-v2.9c.pdf)** | Installing, and everything the add-ons do |
| **[⬇ Firmware patcher (HTML)](../../releases/latest/download/Open500-LA-Firmware-Patcher.html)** | Makes the LA firmware from Akai's own OS 1.31 file, in your browser |

Earlier versions are on the [Releases](../../releases) page.

## Install

1. Unzip **LA-v2.9c.zip** straight onto the root of the CF card, so the card holds the folder `LA v2.9c` with the 14 WAV files in it. Unzip it onto the card itself, not onto the computer first: the MPC loads the files in the order they were written, and `LA.WAV` must come first.
2. On the MPC500, press **MODE + PAD 2** (LOAD), select the `LA v2.9c` folder and press **DO IT**.

For switching off and straight back on, flash the LA firmware once: open the firmware patcher, drop Akai's `mpc500.bin` (from the OS 1.31 update) onto it, and flash the `MPC500.bin` it saves. The manual has every step, a Mac install and what to do if something goes wrong.

## What's in It

| File | Page | |
|---|---|---|
| `LA.WAV` | LA | The add-on manager (the Open500 HUB, renamed); loads first |
| `PROGRAM.WAV` | Program | Every pad parameter of the program on one page, and a safe Purge |
| `TRIM.WAV` | Trim | Trim with the Q-Link slider jumping between hits; extract to a new pad |
| `ZONE.WAV` | Zone | Split a sample into zones by pressing pads while it plays |
| `SONG.WAV` | Song | Build songs with the pads |
| `SEQEDIT.WAV` | Seq Edit | Sequence length (with duplicate) and bar copy |
| `TRACK.WAV` | Track | Track type, MIDI channel and track select on one page |
| `TC3000.WAV` | TC 3000 | MPC3000-style timing correct and swing; shift existing notes |
| `25LEVELS.WAV` | 25 Levels | 12 Levels from a MIDI keyboard over 25 semitones |
| `S500.WAV` | S500 | Record menu: Audio Track, S500 Input, S500 Convert, Resample |
| `AUDTRACK.WAV` | Audio Track | Record audio over the sequence from bar 1; Resample |
| `PADFX.WAV` | Pad FX | Effects menu |
| `PRINTFX.WAV` | Print FX | Render a pad through reverb, EQ or delay into a new sample |
| `MASTER.WAV` | Master | Master EQ and compressor; live input effects |

## Source

Each add-on is built from its Python source in `sdk/examples/` (for example `python sdk/examples/s500.py`), with the Open500 SDK in `sdk/`. `firmware/` holds the firmware patcher and `build_patcher.py`, which makes it. The original Open500 README is `OPEN500-README.md`. No Akai code is in this repository.

## Contact

By Lanny C (LA). Questions and bug reports: doctorbnetwork@gmail.com

Videos: [youtube.com/@DoctorbNetwork](https://www.youtube.com/@DoctorbNetwork)

## Licence and Credits

- Built on [Open500](https://github.com/M45K/open500) by M45K and contributors. Open500 and these add-ons are free software under the **GNU GPL v3** (`LICENSE`).
- MPC, MPC500, MPC3000 and the S-series are Akai Professional's. This project is not made or endorsed by Akai.
