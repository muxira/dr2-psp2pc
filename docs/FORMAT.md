# Save format notes

Reverse-engineered from `DR2_us.exe` (Steam build of *Danganronpa 2: Goodbye
Despair*) and from real saves of both versions: the PSP release `NPJH50631`
(Japanese base with the Russian fan translation) and the Steam build. All
numbers are hex, integers are little-endian, text is UTF-16LE.

Every offset below comes from one of two sources:

* **code**: field boundaries, array sizes and loop bounds read from the
  disassembly of `DR2_us.exe` (2643 accesses to the live game state, 1255 to the
  extra block, 278 to the header were collected);
* **data**: byte-for-byte agreement between a PSP save and PC saves made at the
  same story point (prologue), plus PSP saves from chapters 2 and 6.

## PSP savedata encryption

`DATKG.BIN` (0x19FF4 bytes) is encrypted by the console with mode 5: a 16 byte
IV XORed with the game key, then the ciphertext. The cipher is the Kirk
AES-128-CBC keystream generator of `sceChnnlsv` (PPSSPP
`Core/HLE/sceChnnlsv.cpp`), reimplemented in pure Python. No key is shipped: the
user supplies `game_key`, `key19CC`, `key19DC` (PPSSPP `sceChnnlsv.cpp`) and Kirk
key vault slots 0x12 and 0x64 (`kirk_key_12`, `kirk_key_64`, PPSSPP
`ext/libkirk/kirk_engine.c`) in `keys.txt`. The tool keeps only SHA-256
fingerprints to validate them.

## PC `savedata.vfs`

Container: `[u32 name_len][name][u32 size][data]...` + footer `EF BE AD DE`.
Entries `data%04d.bin` (slots, 0-based) and `icon%04d.png`.

Slot entry (`0x2E4 + 0x37358` bytes):

| offset | size | content |
|---|---|---|
| 0x000 | 0x40 | title `Danganronpa 2: Goodbye Despair` |
| 0x040 | 0x80 | chapter label (`PROLOGUE`, `CHAPTER1`...) + U+3000 |
| 0x0C0 | 0x200 | list text: `Monocoins N\nPlay Time H:MM:SS\nNumber of times saved: N` |
| 0x2C0 | 0x20 | timestamp `DD.MM.YYYY/HH.MM.SS` |
| 0x2E0 | 4 | core size `0x37358` |
| 0x2E4 | 0x37358 | core |

## Core

| | PSP (decrypted, 0x19FE4) | PC (0x37358) |
|---|---|---|
| header | 0x00..0x30 | 0x00..0x40 |
| header checksum | u32 @0x2C = byte sum of 0x00..0x2B | u32 @0x3C = byte sum of 0x00..0x3B |
| body magic `64 95 AE 16`, 4 zero bytes | 0x30 | 0x40 |
| state 0: main game | 0x38, 0xC044 bytes | 0x48, 0x199F4 bytes |
| state 1: Island Mode | 0xC07C | 0x19A3C |
| extra | 0x180C0, 0x1F20 bytes | 0x33430, 0x3F24 bytes |
| body checksum | last u32 = byte sum of 0x30..end-4 | last u32 = byte sum of 0x40..end-4 |

In the exe the header buffer is at `0x752D88` (0x40 bytes), the body buffer at
`0x752DC8` (0x37318 bytes), the two state slots at `0x752DD0` and `0x7747C4`,
the live game state at `0x78A0E0` and the extra block at `0x7861B8`. The function
at `0x431DF0` copies the live state into slot 0 or 1 and ORs bit `0x80` into the
slot's word at `+0x34`; the save routine at `0x4326A0` builds the core from the
header and body buffers.

## Header

Both builds use the same layout up to 0x2C; the PC header has 16 more bytes.

| offset | size | content | handling |
|---|---|---|---|
| 0x00 | 4 | magic | written |
| 0x04 | 4 | volumes (`3C 46 64 50` in a new game on both builds) | copied from PSP |
| 0x08 | 6 | difficulty and other options | copied from PSP |
| 0x14 | 4 | counter / seed | copied from PSP |
| 0x18 | 3 | play time: seconds, minutes, hours | copied from PSP |
| 0x1C | 4 | extra 24 h blocks | copied from PSP |
| 0x20 | 4 | number of times saved | copied from PSP |
| 0x24 | 4 | chapter-clear mask | copied from PSP |
| 0x28 | 4 | completion flags | copied from PSP |
| PC 0x2C | 0x10 | PC only: one byte at 0x2C set when a new game starts (`0x432230`), zero in every PC save | zero |

## State block map (PC <- PSP)

Used for both state slots. The rows tile all 0x199F4 bytes of the PC block;
this is `STATE_MAP` in `dr2_save_bridge.py`.

| PC | PSP | kind | content |
|---|---|---|---|
| 0x00000-0x00266 | 0x00000-0x00266 | copy | chapter u16, script state, flags, items |
| 0x00266-0x00566 | (0x00266-0x003B6) | zero | text window. PC: u16[0x100] text (4 rows of 64) at 0x266, u8[0x100] attributes at 0x466 (cleared with a 0x200-byte memset of the text). PSP: 0x150 bytes, i.e. u16[0x70] + u8[0x70] (4 rows of 28, from the size) |
| 0x00566-0x00D3C | 0x003B6-0x00B8C | copy | window state (row count u8 at PC 0x566), camera, cursor, game data |
| 0x00D3C-0x19342 | 0x00B8C-0x0B992 | log | message log, see below |
| 0x19342-0x199F4 | 0x0B992-0x0C044 | copy | game data |

Fix-ups after copying:

| field | handling |
|---|---|
| cursor s16 x, y at PC 0xBAC, 0xBAE | doubled: PSP 480x272 screen, PC 960x544 (the prologue saves hold 240,136 on PSP and 480,272 on PC at the same point) |
| state 0 word at +0x34 | bit `0x80` set, as the PC save routine does; bit `0x40` is set by script commands and is kept |

### Evidence

* The three copy rows are uniform shifts (0, +0x1B0, +0xD9B0). Against the two
  PC prologue saves A and B, every nonzero PSP byte of both states lands on an
  identical PC byte, except camera floats and the cursor. An inserted or removed
  field anywhere inside a row would shift all later anchors.
* The zero gaps inside the rows that could hide a resized array have the same
  size on both builds; e.g. the u16[0xEE] array at PC 0x592 (code: indexed
  accesses at 0x592, loop bound at 0x76E) is 0x1DC bytes on both.
* PC 0x19342 is a transient mode byte (compared with 0xA/0xB and reset by the
  code); PSP holds 0 there.

## Message log

A ring of lines. `meta` = 3 bytes per line: speaker, kind (1 single line, 2
first line of a multi-line message, 0 continuation), style.

| | PSP | PC |
|---|---|---|
| flag u8, 0xFF | 0xB8C | 0xD3C |
| head u16, write u16 | 0xB8E, 0xB90 | 0xD3E, 0xD40 |
| meta | 0xB92 (0x200 x 3) | 0xD42 (0x200 x 3) |
| text | 0x1192, 0x7000 bytes | 0x1342, 0x200 rows of 0x40 chars |
| attr u8 per char | 0x8192, 0x3800 bytes | 0x11342 |
| rows x chars | 0x200 x 28 (original), 9 x 96 (Russian fan translation) | 0x200 x 64 |

The array sizes give 0x200 rows of 28 chars for the original PSP build. The fan
translation writes 9 rows of 96 chars into the same arrays (head and write stay
below 9, text rows start every 0xC0 bytes); the tool detects the geometry.
`--backlog keep` splits lines longer than 63 chars at a space.

## Extra block map (PC <- PSP)

The rows tile all 0x3F24 bytes of the PC block; `EXTRA_MAP` in the code.

| PC | PSP | kind | content |
|---|---|---|---|
| 0x0000-0x135A | 0x0000-0x135A | copy | progress, report cards, flags |
| 0x135A-0x14DA | (0x135A-0x13DA) | zero | text line. PC: u16[0x80] text + u8[0x80] attr (code: loop bound 0x80). PSP: u16[42] + u8[42] + 2 bytes |
| 0x14DA-0x1784 | 0x13DA-0x1684 | copy | game data |
| 0x1784-0x3BEC | (0x1684-0x1BE8) | zero | suspended mini-game snapshot: PC copies 0x2468 bytes to and from an engine structure (`0x42EE90`, `0x42FAF0`); the valid flag lives at its end (PC 0x3BE8). PSP 0x564 bytes. Zero in every save of both builds |
| 0x3BEC-0x3F24 | 0x1BE8-0x1F20 | copy | game data, Monocoins u16 at PC 0x3E8A |

Against PC saves A and B all three copy rows are identical, including 614
nonzero bytes.

## Verification

`python dr2_save_bridge.py verify DATKG.BIN savedata.vfs --slot N` converts a PSP
save without any template and compares the result with a real PC slot, row by
row of both maps.

* PSP prologue save against PC prologue saves A and B: 15148 and 15151 of 15168
  transferred bytes identical. The rest are play time, difficulty and counter in
  the header, camera floats, the cursor (doubled on purpose) and flag bits.
* Compared with the template-based converter 1.1.0, which loaded chapter 2 and
  chapter 6 saves in the game, the output differs only in bytes 1.1.0 took from
  the template: text window attributes, two header option bytes and the log flag.
