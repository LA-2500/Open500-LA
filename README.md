# Open500 LA v3.0

Add-on set for the **Akai MPC500** (OS 1.31), built on the [Open500](https://github.com/M45K/open500) platform. 14 native-code add-ons delivered as WAV files and loaded through the MPC500 sample loader. Replaced pages keep the original MPC500 page one entry away (ENTER DEFAULT). Flashing the factory OS removes Open500 completely; the bootloader is never modified.

## Download

| | |
|---|---|
| **[⬇ The add-ons (LA-v3.0.zip)](../../releases/latest/download/LA-v3.0.zip)** | The 14 add-on files, in the order the MPC must load them |
| **[⬇ Quick Start (PDF)](../../releases/latest/download/Open500-LA-Quick-Start.pdf)** | Installation and technical specifications, 3 pages |
| **[⬇ Manual (PDF)](../../releases/latest/download/Open500-Manual-LA-v3.0.pdf)** | Full reference for every add-on |
| **[⬇ Firmware patcher (HTML)](../../releases/latest/download/Open500-LA-Firmware-Patcher.html)** | Builds the LA firmware from Akai OS 1.31, client-side |

Earlier versions are on the [Releases](../../releases) page.

## Install

1. **Firmware (one time).** Open the firmware patcher, drop Akai's unmodified `mpc500.bin` (OS 1.31, 514,732 bytes) onto it, save the generated `MPC500.bin` to the card root, and flash it (SHIFT + power on, REC). Required: the add-ons run only on the LA firmware.
2. **Add-ons.** Extract **LA-v3.0.zip** directly to the CF card root. The MPC500 loads files in write order; the zip stores them in load order (`LA.WAV` first, `MASTER.WAV` last).
3. **Load.** MODE + PAD 2 (LOAD), select `LA v3.0`, DO IT. Add-ons run from RAM: reload after every power-on.

Full procedure, specifications and troubleshooting: the [Quick Start](../../releases/latest/download/Open500-LA-Quick-Start.pdf).

## Add-ons

| File | Page | Access | Function |
|---|---|---|---|
| `LA.WAV` | LA | loads first | Add-on manager (Open500 HUB); installs and dispatches the other add-ons |
| `PROGRAM.WAV` | Program | MODE + PAD 6 | All pad parameters of the current program on one page; safe Purge |
| `TRIM.WAV` | Trim | MODE + PAD 5 | Trim with Q-Link slider snapping to transients; extract region to a new pad |
| `ZONE.WAV` | Zone | Other / Trim | Zone a sample by pressing pads during playback |
| `SONG.WAV` | Song | MODE + PAD 12 | Song construction from the pads |
| `SEQEDIT.WAV` | Seq Edit | MODE + PAD 10 | Sequence length change (with duplicate); bar copy |
| `TRACK.WAV` | Track | TRACK | Track type, MIDI channel, track select |
| `TC3000.WAV` | TC 3000 | TIMING CORRECT | MPC3000 or SP-1200 timing correct and swing; shift existing notes |
| `25LEVELS.WAV` | 25 Levels | Other | 12 Levels over 25 semitones from a MIDI keyboard |
| `S500.WAV` | S500 | MODE + PAD 4 | Record menu: Audio Track, S500 Input, S500 Convert, Resample, MPC Record |
| `AUDTRACK.WAV` | Audio Track | Record / Other | Audio recording aligned to bar 1 of the sequence; Resample |
| `PADFX.WAV` | Pad FX | MODE + PAD 9 | Effects menu |
| `PRINTFX.WAV` | Print FX | Pad FX | Offline render of a pad through reverb, EQ or delay to a new sample |
| `MASTER.WAV` | Master | Pad FX | Master EQ and compressor on the output; Input FX (delay, modulation, reverb) |

Other = MODE + PAD 8. Every add-on is also listed there.

## Specifications

| | |
|---|---|
| Target | Akai MPC500, OS 1.31 |
| Firmware | Factory image 514,732 bytes. Patch: Open500 loader (1,295 bytes at 0x103EC, over an all-zero region) and 4-byte hook pointer at 0x2B328; OS CRC-32 at 0x10010 recomputed. Output verified by SHA-256. No Akai code is distributed. |
| Add-ons | 14 RIFF/WAVE containers, 229,564 bytes; executed from working RAM; load order = card write order |
| S500 | Anti-alias: 4th-order Butterworth low-pass at 0.45 × rate. Rate: 7.0–44.1 kHz in 0.5 kHz steps, plus 11.25, 26.04 (default) and 29.76 kHz; exact fractional sample-and-hold. Bits: 16/12/8. Optional normalise before quantisation. Pitch-up 0 to +24 semitones. Mono output. |
| TC 3000 | 96 PPQN. Swing type MPC3000 (swung 1/32 delayed 0–6 whole ticks, doubled per larger grid; 1/16 at 66 % = 8 ticks) or SP-1200 (24 PPQN grid with TC OFF; swing 50/54/58/63/67/71 %). Applied to notes as recorded and by the EDIT page; Note Repeat follows it. |
| Power cycling | LA firmware separates cold boot from folder reload; residual RAM is ignored at power-on |

## Source

Add-on sources: `sdk/examples/` (for example `python sdk/examples/s500.py` builds and tests `S500.WAV` in the emulator). SDK, assembler and emulator: `sdk/`. Firmware patcher and generator: `firmware/` (`build_patcher.py`). Quick Start source: `guide/quickstart.html`. Original Open500 README: `OPEN500-README.md`.

## Contact

Lanny C (LA). Questions and bug reports: doctorbnetwork@gmail.com

Video: [youtube.com/@DoctorbNetwork](https://www.youtube.com/@DoctorbNetwork)

## Licence and Credits

- Built on [Open500](https://github.com/M45K/open500) by M45K and contributors. Open500 and these add-ons: GNU GPL v3 (`LICENSE`).
- MPC, MPC500, MPC3000 and the S-series are trademarks of Akai Professional. This project is not affiliated with or endorsed by Akai.
