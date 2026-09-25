# Save format notes

Reverse-engineered from `DR2_us.exe` (Steam) and real saves of both versions.
All numbers are hex. Integers are little-endian. Text is UTF-16LE.

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

| | PSP (decrypted `DATKG.BIN`, 0x19FE4) | PC (0x37358) |
|---|---|---|
| header | 0x00..0x30 | 0x00..0x40 |
| small checksum | u32 @0x2C = byte sum of 0x00..0x2B | u32 @0x3C = byte sum of 0x00..0x3B |
| body magic | 0x30 | 0x40 |
| state[0] | 0x38, 0xC044 bytes | 0x48, 0x199F4 bytes |
| state[1] | 0xC07C | 0x19A3C |
| extra | 0x180C0, 0x1F20 bytes | 0x33430, 0x3F24 bytes |
| big checksum | last u32 = byte sum of 0x30..end-4 | last u32 = byte sum of 0x40..end-4 |

Header 0x00 magic `64 95 AE 16 3C 46 64 50`, 0x08 option bytes (kept from the PC template),
0x18 sec/min/hour u8, 0x1C days u32, 0x20 save count u32.

In the exe the live state lives at `0x78A0E0`, the slot buffer at `0x752D88`;
state+0 is the chapter (u16).

## State block map (PSP -> PC)

| PSP | PC | length | content |
|---|---|---|---|
| 0x0000 | 0x0000 | 0x266 | game state, chapter u16 at +0 (copied as is) |
| 0x0266 | 0x0266 | - | text window: PSP 4x42 chars, PC 4x64 + colour table (blanked) |
| 0x03B6 | 0x0566 | 0x7DC | game state (copied as is); cursor s16 @PC 0xBAC scaled x2 |
| 0x0B8C | 0x0D3C | - | backlog (see below) |
| 0xB992 | 0x19342 | 0x6B2 | game state (copied as is) |

## Extra block map

| PSP | PC | length | content |
|---|---|---|---|
| 0x0000 | 0x0000 | 0x135A | game data (copied as is) |
| 0x135A | 0x135A | - | text line: PSP 42 chars, PC 128 chars (blanked) |
| 0x13DA | 0x14DA | 0x2AA | game data (copied as is) |
| 0x1684 | 0x1784 | - | resource table, PSP 0x564 / PC 0x2468, always zero |
| 0x1BE8 | 0x3BEC | 0x338 | game data incl. Monocoins u16 (PC core 0x372BA) |

## Backlog

A ring of lines; `head` = oldest line that starts a message, `write` = next slot.
Meta = 3 bytes per line: speaker, kind (1 single line, 2 first line of a
multi-line message, 0 continuation), style.

| | PSP | PC |
|---|---|---|
| flag u8 / 0xFF | 0xB8C / 0xB8D | 0xD3C / 0xD3D |
| head u16, write u16 | 0xB8E, 0xB90 | 0xD3E, 0xD40 |
| meta | 0xB92 | 0xD42 (3 x 0x200) |
| text | 0x1192, 0x60 chars/line | 0x1342, 0x40 chars/line |
| attr u8 per char | 0x8192 | 0x11342 |
| ring size | 9 lines | 0x200 lines |

Lines longer than 63 chars are split at a space when moved to PC.
