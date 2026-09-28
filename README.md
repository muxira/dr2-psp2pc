<div align="center">

# 🐰 dr2-psp2pc

**Transferring Danganronpa 2: Goodbye Despair saves from PSP to PC (Steam)**

Story progress, flags, items, Monocoins, playtime — into a PC-version slot, as if you'd played it there. No PC save needed as a template.

![python](https://img.shields.io/badge/python-3.8%2B-blue) ![deps](https://img.shields.io/badge/dependencies-none-brightgreen) ![platform](https://img.shields.io/badge/PSP-NPJH50631-lightgrey) ![target](https://img.shields.io/badge/PC-Steam-black) ![license](https://img.shields.io/badge/license-MIT-green)

</div>

---

## ✨ What gets transferred

| | |
|---|---|
| 📖 Chapter and story point | ✅ |
| 🚩 Progress flags, unlocks | ✅ |
| 🎒 Items and game data | ✅ |
| 🪙 Monocoins | ✅ |
| ⏱️ Playtime and save count | ✅ |
| 🏝️ Island Mode state | ✅ |
| ⚙️ Settings (difficulty, sound) | ✅ from the PSP save |
| 💬 Backlog (dialogue history) | ⚙️ optional: from the PSP save or empty |

Every byte of the PC slot is either transferred from the PSP save through the
offset map in [docs/FORMAT.md](docs/FORMAT.md) or set to the value a fresh PC game
uses, so the converter needs no PC save at all. Checked against real saves: a
PSP prologue save converts to a slot identical to real PC prologue saves on
99.9 % of the transferred bytes (the rest is play time, camera and settings).
Chapter 2 and chapter 6 saves converted by the previous, template-based version
loaded in the game; the new output differs from it only in the bytes that
version took from the template.

## 📦 Requirements

- **Python 3.8+**, nothing else
- PSP save `NPJH50631DATKG000x/DATKG.BIN`:
  - encrypted (106,484 bytes) + the keys in `keys.txt`, see [Keys](#-keys), **or**
  - already decrypted (106,468 bytes), no keys needed
- Optional: an existing PC `savedata.vfs`; its other slots are kept. Without one, a new file is created.

## 🚀 Quick Start (Windows)

1. Close the game.
2. Set up `keys.txt` once, see [Keys](#-keys).
3. Run **`install.bat`**.
4. Drag the PSP save file into the window, specify the PC slot number. Repeat for other saves, empty Enter — done.
5. Decide whether to transfer the backlog (`Y`/`N`).

The script automatically:
- makes a backup in `backups\<date>\`;
- checks the checksums of the new slots;
- restores the original file on any error.

PC saves are located at `%USERPROFILE%\Documents\My Games\Danganronpa2\savedata.vfs`.

## 🖥️ Command line

```bash
# view slots
python dr2_save_bridge.py info savedata.vfs
python dr2_save_bridge.py info NPJH50631DATKG0000/DATKG.BIN   # decrypts it, shows the backlog

# PSP save -> PC slot 1 of a brand new savedata.vfs
python dr2_save_bridge.py convert --pc-vfs none.vfs \
    --map 1=NPJH50631DATKG0000/DATKG.BIN --out savedata.vfs

# two saves, with backlog, straight into the game folder (with backup)
python dr2_save_bridge.py convert \
    --map 1=NPJH50631DATKG0001/DATKG.BIN --map 2=NPJH50631DATKG0000/DATKG.BIN \
    --backlog keep --install

# check the map yourself: convert without a template and compare with a real
# PC slot saved at the same story point
python dr2_save_bridge.py verify NPJH50631DATKG0000/DATKG.BIN savedata.vfs --slot 1
```

| flag | |
|---|---|
| `--map SLOT=FILE` | PC slot number (starting from 1) = PSP save; can be used multiple times |
| `--backlog clear\|keep` | `clear` (default): empty history; `keep`: transfer from PSP |
| `--keys FILE` | keys file, default `keys.txt` next to the script (global option) |
| `--key HEX` | game key, overrides the one in the keys file (global option) |
| `--install` | overwrite the input file, keeping a `*.bak` copy alongside |
| `--force` | overwrite `--out` without `--install` |

`--install` with no `--pc-vfs` and no `--out` uses
`%USERPROFILE%\Documents\My Games\Danganronpa2\savedata.vfs` directly.

## 💬 About the backlog

The PSP log holds 0x200 lines of 28 chars in the original release; the Russian fan
translation keeps only the **last 9 lines** of 96 chars. The tool detects which one it is.
In `keep` mode the lines are transferred into the PC history in the correct order. Lines longer than 63 characters are wrapped by word.

> [!WARNING]
> The English PC version may not display Cyrillic or Japanese: the lines will appear empty or as squares.
> If you have a working font patch, keep `keep`. Otherwise choose `clear`: the backlog will fill in on its own as soon as you continue playing.

## 🔑 Keys

`DATKG.BIN` is encrypted by the console itself with the Kirk AES stream cipher
(mode 5, per-game key). The cipher is reimplemented here in pure Python, so **no
external tool is needed**, but **no key is shipped with it**: you provide five
16-byte keys in `keys.txt`, which git ignores.

| key | where to find it |
|---|---|
| `key19CC`, `key19DC` | PPSSPP source, `Core/HLE/sceChnnlsv.cpp` |
| `kirk_key_12`, `kirk_key_64` | PPSSPP source, `ext/libkirk/kirk_engine.c` (key vault slots 0x12 and 0x64) |
| `game_key` | PPSSPP log: run the game with logging on, load or save once, look for `Game key (hex):` |

The tool finds them for you in those files, by fingerprint (an old `gamekey.bin` works too):

```bash
python dr2_save_bridge.py keys --import path/to/ppsspp-source path/to/ppsspp.log
python dr2_save_bridge.py keys --game-key <32 hex digits>   # or set it by hand
python dr2_save_bridge.py keys                               # check what is set
```

Or copy `keys.example.txt` to `keys.txt` and fill it in. Every value is checked
against a SHA-256 fingerprint, so a wrong key is reported by name. The same
`keys.txt` works for dr1-save-bridge except for `game_key`.

## ↩️ Rollback

Copy `backups\<date>\savedata.vfs` back into the saves folder.

## 🔬 How it works

Both saves are memory images of the same structures: a header, two game states
(main game and Island Mode) and an extra block. The PC build enlarges a few
arrays: the text window (64 chars per row instead of 28), the message log and a
text line, adds a mini-game snapshot area and 16 header bytes. The converter
walks maps that cover every byte of the PC slot (`STATE_MAP`, `EXTRA_MAP`),
clears or rebuilds the language-specific parts, and computes both checksums.

Detailed offset map: **[docs/FORMAT.md](docs/FORMAT.md)**.

```bash
python -m unittest discover -s tests    # synthetic data only
```

## ⚠️ Limitations

- Tested on PSP **NPJH50631** (Japanese base, Russian fan translation) → Steam version **DR2_us.exe**.
- The reverse direction (PC → PSP) is not yet available.
- PC-only and language-specific data start empty: the text window, the current text line and a suspended mini-game snapshot (empty in every save examined on both builds).
- The project is not affiliated with Spike Chunsoft / NIS America. Saves, keys, and game files are not included in the repository (see `.gitignore`).

## 📄 License

[MIT](LICENSE) © 2026 muxira

---

<div align="center"><sub>Upupupu... Despair-free save transfer. 🐻</sub></div>
