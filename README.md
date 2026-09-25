<div align="center">

# 🐰 dr2-psp2pc

**Transferring Danganronpa 2: Goodbye Despair saves from PSP to PC (Steam)**

Story progress, flags, items, Monocoins, playtime — into a PC-version slot, as if you'd played it there.

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
| 💬 Backlog (dialogue history) | ⚙️ optional: last 9 lines from PSP or empty |
| ⚙️ Settings (difficulty, sound) | taken from the PC save |

Tested in-game: PSP saves from Chapter 2 and Chapter 6 load on PC on the first try.

## 📦 Requirements

- **Python 3.8+**, nothing else
- A PC version with **its own** save: at least one valid slot, from which settings are taken
- PSP save `NPJH50631DATKG000x/DATKG.BIN`:
  - already decrypted (106,468 bytes), **or**
  - encrypted (106,484 bytes) + the game key `gamekey.bin` + [`psp-save`](https://github.com/vita8328/psp-save) — see [below](#-how-to-decrypt-a-psp-save)

## 🚀 Quick Start (Windows)

1. Close the game.
2. Run **`install.bat`**.
3. Drag the PSP save file into the window, specify the PC slot number. Repeat for other saves, empty Enter — done.
4. Decide whether to transfer the backlog (`Y`/`N`).

The script automatically:
- makes a backup in `backups\<date>\`;
- checks checksums before and after;
- restores the original file on any error.

PC saves are located at `%USERPROFILE%\Documents\My Games\Danganronpa2\savedata.vfs`.

## 🖥️ Command line

```bash
# view slots
python dr2_save_bridge.py info savedata.vfs
python dr2_save_bridge.py info DATKG_dec.bin        # + shows the PSP backlog

# PSP save -> PC slot 1, no backlog
python dr2_save_bridge.py convert --pc-vfs savedata.vfs \
    --map 1=DATKG_dec.bin --out savedata_new.vfs

# two saves, with backlog, encrypted input, straight into the game (with backup)
python dr2_save_bridge.py convert --pc-vfs savedata.vfs \
    --map 1=NPJH50631DATKG0001/DATKG.BIN --map 2=NPJH50631DATKG0000/DATKG.BIN \
    --key gamekey.bin --psp-save tools/psp-save.exe \
    --backlog keep --out savedata.vfs --install
```

| flag | |
|---|---|
| `--map SLOT=FILE` | PC slot number (starting from 1) = PSP save; can be used multiple times |
| `--backlog clear\|keep` | `clear` (default): empty history; `keep`: transfer from PSP |
| `--template-slot N` | which PC slot to use as the base (default: last complete one) |
| `--key`, `--psp-save` | only needed for an encrypted `DATKG.BIN` |
| `--install` | overwrite `--out`, keeping a `*.bak` copy alongside |

## 💬 About the backlog

The PSP save only stores the **last 9 lines** of dialogue, in the language of its version.
In `keep` mode, they're transferred into the PC history in the correct order. Lines longer than 63 characters are wrapped by word.

> [!WARNING]
> The English PC version may not display Cyrillic or Japanese: the lines will appear empty or as squares.
> If you have a working font patch, keep `keep`. Otherwise choose `clear`: the backlog will fill in on its own as soon as you continue playing.

## 🔑 How to decrypt a PSP save

1. Run the game in **PPSSPP** with logging set to Debug level and load the save.
2. On loading, the log will show a line `Game key` (16 bytes hex). Save it to a file:
   ```bash
   python -c "open('gamekey.bin','wb').write(bytes.fromhex('YOUR_KEY_HEX'))"
   ```
3. Build [`psp-save`](https://github.com/vita8328/psp-save) and place `psp-save.exe` into `tools\`.

After that, `install.bat` and `--key/--psp-save` will decrypt the save automatically (mode 5).

## ↩️ Rollback

Copy `backups\<date>\savedata.vfs` back into the saves folder.

## 🔬 How it works

The PC save is a PSP save with enlarged arrays: a 64-character text window instead of 42, a 512-line backlog instead of 9, and a header 16 bytes longer.
The converter transfers data block by block, following a map reconstructed from `DR2_us.exe`, and recalculates both checksums.
Anything language-dependent (the current line, the backlog) is either cleared or converted separately.

Detailed offset map: **[docs/FORMAT.md](docs/FORMAT.md)**.

```bash
python -m unittest discover -s tests    # tests on synthetic data, no third-party saves
```

## ⚠️ Limitations

- Tested on PSP **NPJH50631** (Japanese base, Russian fan translation) → Steam version **DR2_us.exe**.
- The reverse direction (PC → PSP) is not yet available.
- The project is not affiliated with Spike Chunsoft / NIS America. Saves, keys, and game files are not included in the repository (see `.gitignore`).

## 📄 License

[MIT](LICENSE) © 2026 muxira

---

<div align="center"><sub>Upupupu... Despair-free save transfer. 🐻</sub></div>
