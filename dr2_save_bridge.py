#!/usr/bin/env python3
"""dr2-save-bridge: Danganronpa 2 PSP (NPJH50631) -> Steam PC save converter.

Transfers story state, flags, items, Monocoins, play time and save count from a
decrypted PSP DATKG.BIN into a slot of the PC savedata.vfs. See docs/FORMAT.md.
"""
import argparse
import datetime
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from collections import Counter

__version__ = "1.1.0"

# ---------------------------------------------------------------- layout ----
PSP_LEN, PC_LEN = 0x19FE4, 0x37358
PSP_ENC_LEN = 0x19FF4
PSP_STATE, PC_STATE = 0xC044, 0x199F4
PSP_S0, PC_S0 = 0x38, 0x48
PSP_EXTRA, PC_EXTRA = PSP_S0 + 2 * PSP_STATE, PC_S0 + 2 * PC_STATE
PC_ENTRY_HDR = 0x2E4
MAGIC = bytes.fromhex("6495ae16")
COINS_PC = 0x372BA

# (psp_off, pc_off, length) inside one state block
STATE_MAP = [
    (0x0000, 0x0000, 0x266),
    (0x03B6, 0x0566, 0xB94 - 0x3B6 - 2),
    (0xB992, 0x19342, PSP_STATE - 0xB992),
]
DIALOG_PC = (0x266, 0x466)          # current text window, language-specific
CURSOR_PC = (0xBAC, 0xBAE)          # s16 x/y, PSP 480x272 -> PC 960x544
EXTRA_MAP = [
    (0x0000, 0x0000, 0x135A),
    (0x13DA, 0x14DA, 0x1684 - 0x13DA),
    (0x1BE8, 0x3BEC, 0x1F20 - 0x1BE8),
]
EXTRA_TEXT_PC = (0x135A, 0x14DA)
HDR_COPY = (0x14, 0x2C)              # seed, play time, save count, unlock masks

# Backlog ring buffers (state-relative). meta = 3 bytes/line: speaker, kind, style.
# kind: 1 = one-line message, 2 = first line of multi-line, 0 = continuation.
PSP_BL = dict(flag=0xB8C, head=0xB8E, write=0xB90, meta=0xB92, text=0x1192,
              attr=0x8192, ring=9, width=0x60)
PC_BL = dict(flag=0xD3C, head=0xD3E, write=0xD40, meta=0xD42, text=0x1342,
             attr=0x11342, ring=0x200, width=0x40, end=0x19342)
PC_LINE_MAX = PC_BL["width"] - 1

CHAPTER_NAMES = {0: "PROLOGUE", 7: "EPILOGUE"}


class BridgeError(Exception):
    pass


def sum8(b):
    return sum(b) & 0xFFFFFFFF


# ------------------------------------------------------------ validation ----
def psp_ok(core):
    return (len(core) == PSP_LEN and core[:4] == MAGIC and core[0x30:0x34] == MAGIC
            and struct.unpack_from("<I", core, 0x2C)[0] == sum8(core[:0x2C])
            and struct.unpack_from("<I", core, PSP_LEN - 4)[0] == sum8(core[0x30:PSP_LEN - 4]))


def pc_ok(core):
    return (len(core) == PC_LEN and core[:4] == MAGIC and core[0x40:0x44] == MAGIC
            and struct.unpack_from("<I", core, 0x3C)[0] == sum8(core[:0x3C])
            and struct.unpack_from("<I", core, PC_LEN - 4)[0] == sum8(core[0x40:PC_LEN - 4]))


def fix_pc_sums(core):
    struct.pack_into("<I", core, 0x3C, sum8(core[:0x3C]))
    struct.pack_into("<I", core, PC_LEN - 4, sum8(core[0x40:PC_LEN - 4]))


# --------------------------------------------------------------- backlog ----
def _u16s(buf, off, n):
    return list(struct.unpack_from(f"<{n}H", buf, off))


def read_psp_backlog(state):
    """Return lines oldest->newest as (meta3, chars, attrs)."""
    b = PSP_BL
    head, write = struct.unpack_from("<HH", state, b["head"])
    ring = b["ring"]
    if head >= ring or write >= ring:
        return []
    order, i = [], head
    while True:
        order.append(i)
        i = (i + 1) % ring
        if i == write or len(order) == ring:
            break
    lines = []
    for i in order:
        meta = bytes(state[b["meta"] + 3 * i:b["meta"] + 3 * i + 3])
        chars = _u16s(state, b["text"] + i * b["width"] * 2, b["width"])
        attrs = list(state[b["attr"] + i * b["width"]:b["attr"] + (i + 1) * b["width"]])
        n = chars.index(0) if 0 in chars else len(chars)
        if n == 0 and not any(meta):
            continue
        lines.append((meta, chars[:n], attrs[:n]))
    while lines and lines[0][0][1] == 0:     # oldest message was partly overwritten
        lines.pop(0)
    return lines


def _split(chars, limit):
    if len(chars) <= limit:
        return [chars]
    cut = max((k for k in range(1, limit + 1) if chars[k - 1] == 0x20), default=limit)
    return [chars[:cut]] + _split(chars[cut:], limit)


def fit_lines(lines, limit=PC_LINE_MAX):
    out = []
    for meta, chars, attrs in lines:
        parts, pos = _split(chars, limit), 0
        for k, part in enumerate(parts):
            kind = meta[1]
            if len(parts) > 1:
                kind = (2 if meta[1] in (1, 2) else 0) if k == 0 else 0
            out.append((bytes([meta[0], kind, meta[2]]), part, attrs[pos:pos + len(part)]))
            pos += len(part)
    return out


def write_pc_backlog(state, lines):
    b = PC_BL
    state[b["head"]:b["end"]] = bytes(b["end"] - b["head"])
    lines = lines[-b["ring"]:]
    for i, (meta, chars, attrs) in enumerate(lines):
        state[b["meta"] + 3 * i:b["meta"] + 3 * i + 3] = meta
        struct.pack_into(f"<{len(chars)}H", state, b["text"] + i * b["width"] * 2, *chars)
        a = b["attr"] + i * b["width"]
        state[a:a + len(attrs)] = bytes(attrs)
    struct.pack_into("<HH", state, b["head"], 0, len(lines) % b["ring"])
    state[b["flag"]] = 1 if lines else 0
    state[b["flag"] + 1] = 0xFF


# --------------------------------------------------------------- convert ----
def convert_core(psp, template, backlog="clear"):
    if not psp_ok(psp):
        raise BridgeError("PSP data is not a valid decrypted DR2 save (magic/checksum)")
    if not pc_ok(template):
        raise BridgeError("PC template slot is damaged (checksum)")
    out = bytearray(template)
    out[HDR_COPY[0]:HDR_COPY[1]] = psp[HDR_COPY[0]:HDR_COPY[1]]
    for i in range(2):
        ps, pc = PSP_S0 + i * PSP_STATE, PC_S0 + i * PC_STATE
        for po, co, n in STATE_MAP:
            out[pc + co:pc + co + n] = psp[ps + po:ps + po + n]
        out[pc + PC_BL["head"]:pc + PC_BL["end"]] = bytes(PC_BL["end"] - PC_BL["head"])
        out[pc + DIALOG_PC[0]:pc + DIALOG_PC[1]] = bytes(DIALOG_PC[1] - DIALOG_PC[0])
        for f in CURSOR_PC:
            v, = struct.unpack_from("<h", out, pc + f)
            struct.pack_into("<h", out, pc + f, max(-32768, min(32767, v * 2)))
        if backlog == "keep" and i == 0:
            state = out[pc:pc + PC_STATE]
            write_pc_backlog(state, fit_lines(read_psp_backlog(psp[ps:ps + PSP_STATE])))
            out[pc:pc + PC_STATE] = state
    for po, co, n in EXTRA_MAP:
        out[PC_EXTRA + co:PC_EXTRA + co + n] = psp[PSP_EXTRA + po:PSP_EXTRA + po + n]
    out[PC_EXTRA + EXTRA_TEXT_PC[0]:PC_EXTRA + EXTRA_TEXT_PC[1]] = \
        bytes(EXTRA_TEXT_PC[1] - EXTRA_TEXT_PC[0])
    fix_pc_sums(out)
    assert pc_ok(out)
    return out


def stats(core, pc=True):
    base = PC_S0 if pc else PSP_S0
    coins_off = COINS_PC if pc else PSP_EXTRA + 0x1E86
    day, = struct.unpack_from("<I", core, 0x1C)
    return dict(chapter=struct.unpack_from("<H", core, base)[0],
                hours=day * 24 + core[0x1A], minutes=core[0x19], seconds=core[0x18],
                saves=struct.unpack_from("<I", core, 0x20)[0],
                coins=struct.unpack_from("<H", core, coins_off)[0])


def _put_str(entry, off, size, s):
    b = s.encode("utf-8")[:size - 1]
    entry[off:off + size] = b + bytes(size - len(b))


def build_entry(psp, template_entry, backlog="clear", now=None):
    core = convert_core(psp, template_entry[PC_ENTRY_HDR:], backlog)
    e = bytearray(template_entry[:PC_ENTRY_HDR]) + core
    st = stats(core)
    ch = st["chapter"]
    _put_str(e, 0x40, 0x80, CHAPTER_NAMES.get(ch, f"CHAPTER{ch}") + "\u3000")
    _put_str(e, 0xC0, 0x200,
             f"Monocoins {st['coins']}\nPlay Time {st['hours']}:{st['minutes']:02d}:{st['seconds']:02d}\n"
             f"Number of times saved: {st['saves']}")
    _put_str(e, 0x2C0, 0x20, (now or datetime.datetime.now()).strftime("%d.%m.%Y/%H.%M.%S"))
    return e, st


# ------------------------------------------------------------- container ----
def vfs_parse(blob):
    o, ents = 0, []
    while o < len(blob) - 4:
        n, = struct.unpack_from("<I", blob, o)
        if not 0 < n <= 64:
            raise BridgeError(f"savedata.vfs: bad entry name length at {o:#x}")
        name = blob[o + 4:o + 4 + n].decode("ascii")
        o += 4 + n
        size, = struct.unpack_from("<I", blob, o)
        o += 4
        ents.append([name, bytearray(blob[o:o + size])])
        o += size
    if o != len(blob) - 4 or struct.unpack_from("<I", blob, o)[0] != 0xDEADBEEF:
        raise BridgeError("savedata.vfs: bad footer")
    return ents


def vfs_pack(ents):
    out = bytearray()
    for name, data in ents:
        nb = name.encode("ascii")
        out += struct.pack("<I", len(nb)) + nb + struct.pack("<I", len(data)) + data
    return bytes(out + struct.pack("<I", 0xDEADBEEF))


def slot_name(n):
    return f"data{n - 1:04d}.bin"


def entry_ok(data):
    return len(data) == PC_ENTRY_HDR + PC_LEN and pc_ok(data[PC_ENTRY_HDR:])


# ------------------------------------------------------------ PSP decrypt ----
def entropy(b):
    c, n = Counter(b), len(b)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def load_psp(path, key=None, psp_save=None):
    data = bytearray(open(path, "rb").read())
    if len(data) == PSP_LEN:
        return data
    if len(data) != PSP_ENC_LEN:
        raise BridgeError(f"{path}: unexpected size {len(data)}")
    if not key or not psp_save:
        raise BridgeError(f"{path} is encrypted: pass --key GAMEKEY.BIN and --psp-save psp-save.exe")
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "dec.bin")
        r = subprocess.run([psp_save, "-d", key, "5", path, out], capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(out):
            raise BridgeError(f"psp-save failed: {r.stderr.strip() or r.returncode}")
        data = bytearray(open(out, "rb").read())
    if entropy(data[:65536]) > 7.5 or not psp_ok(data):
        raise BridgeError("decryption produced garbage: wrong game key?")
    return data


# ------------------------------------------------------------------- CLI ----
def cmd_convert(a):
    ents = vfs_parse(open(a.pc_vfs, "rb").read())
    by_name = {n: d for n, d in ents}
    targets = {}
    for spec in a.map:
        slot, _, src = spec.partition("=")
        if not slot.isdigit() or not 1 <= int(slot) <= 99 or not src:
            raise BridgeError(f"bad --map {spec!r}, expected SLOT=FILE")
        targets[slot_name(int(slot))] = src
    if a.template_slot:
        tname = slot_name(a.template_slot)
        if tname not in by_name or not entry_ok(by_name[tname]):
            raise BridgeError(f"template slot {a.template_slot} missing or damaged")
    else:
        cands = [n for n, d in ents if n.startswith("data") and entry_ok(d) and n not in targets]
        cands = cands or [n for n, d in ents if n.startswith("data") and entry_ok(d)]
        if not cands:
            raise BridgeError("no valid PC slot to use as template: make one normal save on PC first")
        tname = cands[-1]
    template = by_name[tname]
    print(f"template: {tname}   backlog: {a.backlog}")
    for name, src in targets.items():
        entry, st = build_entry(load_psp(src, a.key, a.psp_save), template, a.backlog)
        for x in ents:
            if x[0] == name:
                x[1] = entry
                break
        else:
            ents.append([name, entry])
        slot = int(name[4:8]) + 1
        print(f"slot {slot} <- {os.path.basename(src)}: chapter {st['chapter']}, "
              f"{st['coins']} coins, {st['hours']}:{st['minutes']:02d}:{st['seconds']:02d}, "
              f"{st['saves']} saves")
    ents.sort(key=lambda x: (not x[0].startswith("data"), x[0]))
    blob = vfs_pack(ents)
    for name, data in vfs_parse(blob):
        if name in targets and not entry_ok(data):
            raise BridgeError(f"self-check failed for {name}")
    if a.install:
        if os.path.exists(a.out):
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = f"{a.out}.{ts}.bak"
            shutil.copy2(a.out, bak)
            print(f"backup: {bak}")
    elif os.path.exists(a.out) and not a.force:
        raise BridgeError(f"{a.out} exists (use --force or --install)")
    with open(a.out, "wb") as f:
        f.write(blob)
    print(f"wrote {a.out} ({len(blob)} bytes)")


def cmd_info(a):
    blob = open(a.file, "rb").read()
    if len(blob) in (PSP_LEN, PSP_ENC_LEN):
        if len(blob) == PSP_ENC_LEN:
            print("encrypted PSP DATKG.BIN (decrypt with --key/--psp-save)")
            return
        print(f"PSP decrypted core, valid={psp_ok(blob)}", stats(blob, pc=False))
        for meta, chars, _ in read_psp_backlog(blob[PSP_S0:PSP_S0 + PSP_STATE]):
            print(f"  [{meta.hex()}] {''.join(map(chr, chars))}")
        return
    for name, data in vfs_parse(blob):
        if not name.startswith("data"):
            print(f"{name}: {len(data)} bytes")
            continue
        ok = entry_ok(data)
        info = stats(data[PC_ENTRY_HDR:]) if ok else "DAMAGED / foreign layout"
        print(f"slot {int(name[4:8]) + 1} ({name}): {info}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dr2_save_bridge", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert", help="put PSP saves into PC savedata.vfs slots")
    c.add_argument("--pc-vfs", required=True, help="existing PC savedata.vfs (read)")
    c.add_argument("--map", action="append", required=True, metavar="SLOT=FILE",
                   help="PC slot number (1-based) = PSP DATKG.BIN (decrypted or encrypted)")
    c.add_argument("--backlog", choices=["clear", "keep"], default="clear",
                   help="clear (default) or keep the PSP dialogue history (last 9 lines, PSP language)")
    c.add_argument("--template-slot", type=int, help="valid PC slot used for PC-only data")
    c.add_argument("--key", help="16-byte PSP game key file (encrypted input only)")
    c.add_argument("--psp-save", help="path to psp-save executable (encrypted input only)")
    c.add_argument("--out", required=True)
    c.add_argument("--install", action="store_true", help="overwrite --out, keeping a timestamped backup")
    c.add_argument("--force", action="store_true")
    c.set_defaults(func=cmd_convert)
    i = sub.add_parser("info", help="show slots of a savedata.vfs or a PSP core")
    i.add_argument("file")
    i.set_defaults(func=cmd_info)
    a = ap.parse_args(argv)
    try:
        a.func(a)
    except (BridgeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
