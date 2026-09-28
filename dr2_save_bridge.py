#!/usr/bin/env python3
"""dr2-save-bridge: Danganronpa 2 PSP (NPJH50631) -> Steam PC save converter.

Builds a complete PC save slot from a PSP `DATKG.BIN`.  Every byte of the PC slot
is either transferred from the PSP save through the offset maps below or set to
the value a fresh PC game uses, so no PC save is needed as a template.  Pure
standard library, no dependencies.  docs/FORMAT.md explains how every offset was
established.
"""
import argparse
import datetime
import hashlib
import math
import os
import shutil
import struct
import sys
from collections import Counter

__version__ = "2.0.0"

# --------------------------------------------------------------- container ----
VFS_FOOTER = 0xDEADBEEF
ENTRY_HDR = 0x2E4                  # slot header in front of the core
TITLE = "Danganronpa 2: Goodbye Despair"
MAGIC = bytes.fromhex("6495ae16")

# ------------------------------------------------------------------ layout ----
# core = header | body magic, 4 zero bytes, state 0, state 1, extra | byte sum
PSP_ENC_HEAD = 0x10                # savedata IV prefix in front of the ciphertext
PSP_LEN, PC_LEN = 0x19FE4, 0x37358
PSP_HDR, PC_HDR = 0x30, 0x40       # body magic offset
PSP_HDR_SUM, PC_HDR_SUM = 0x2C, 0x3C
PSP_STATE, PC_STATE = 0xC044, 0x199F4
PSP_S0, PC_S0 = PSP_HDR + 8, PC_HDR + 8
PSP_EXTRA, PC_EXTRA = PSP_S0 + 2 * PSP_STATE, PC_S0 + 2 * PC_STATE
PSP_EXTRA_LEN, PC_EXTRA_LEN = PSP_LEN - 4 - PSP_EXTRA, PC_LEN - 4 - PC_EXTRA
HDR_COPY = (0x04, 0x2C)            # options, play time, save count, clear masks
HDR_TIME, HDR_DAYS, HDR_SAVES = 0x18, 0x1C, 0x20
PSP_COINS, PC_COINS = 0x1E86, 0x3E8A   # u16 Monocoins in the extra block

# Complete maps of the PC state block (0x199F4 bytes, used for both state slots)
# and of the PC extra block (0x3F24 bytes) onto the PSP ones.
# (pc_lo, pc_hi, kind, psp_lo); the rows tile the whole PC range.
#   copy   bytes copied verbatim from psp_lo
#   log    message log, rebuilt (language specific)
#   zero   language specific or PC-only bytes, zero in a fresh PC game
# docs/FORMAT.md explains how every row was established.
STATE_MAP = (
    (0x00000, 0x00266, "copy", 0x00000),   # chapter, script state, flags, items
    (0x00266, 0x00566, "zero", None),      # text window: u16[0x100] text, u8[0x100] attr
    (0x00566, 0x00D3C, "copy", 0x003B6),   # window state, camera, cursor, game data
    (0x00D3C, 0x19342, "log", None),       # message log
    (0x19342, 0x199F4, "copy", 0x0B992),   # game data
)
EXTRA_MAP = (
    (0x0000, 0x135A, "copy", 0x0000),      # progress, report cards, flags
    (0x135A, 0x14DA, "zero", None),        # text line: u16[0x80] text, u8[0x80] attr
    (0x14DA, 0x1784, "copy", 0x13DA),      # game data
    (0x1784, 0x3BEC, "zero", None),        # suspended mini-game snapshot (FORMAT.md)
    (0x3BEC, 0x3F24, "copy", 0x1BE8),      # game data, Monocoins
)
CURSOR = (0xBAC, 0xBAE)            # s16 x, y in the state: PSP 480x272, PC 960x544
SAVE_FLAGS = 0x34                  # the PC save routine sets bit 0x80 here (state 0)

# Message logs inside a state block.  The original PSP build keeps 0x200 rows of
# 28 chars; the Russian fan translation keeps 9 rows of 96 chars in the same arrays.
PSP_LOG = dict(flag=0xB8C, head=0xB8E, write=0xB90, meta=0xB92, text=0x1192,
               attr=0x8192, end=0xB992, geometry=((28, 0x200), (96, 9)))
PC_LOG = dict(flag=0xD3C, head=0xD3E, write=0xD40, meta=0xD42, text=0x1342,
              attr=0x11342, end=0x19342, rows=0x200, chars=0x40)

CHAPTER_NAMES = {0: "PROLOGUE", 7: "EPILOGUE"}

# ------------------------------------------------------------------- keys ----
# No decryption key is shipped with this tool.  The user provides them in
# keys.txt (see keys.example.txt, or run `keys --import`); only SHA-256
# fingerprints are kept here, to tell the user which key is missing or wrong.
KEYS_FILE = "keys.txt"
GAME_KEY = "game_key"
CIPHER_KEYS = ("key19CC", "key19DC", "kirk_key_12", "kirk_key_64")
KEY_NAMES = (GAME_KEY,) + CIPHER_KEYS
KEY_FINGERPRINTS = {
    "game_key": "12630d87b98f4229a5282cc3ac203b7ebc09713aa6ac1b821cc5d9e6f0cd8d13",
    "key19CC": "b58242561b16a8925126dbae5558dda53d6c275d712ee69f5bebce37c577af2d",
    "key19DC": "a0bf478c1471e4115011e5e0f70d5e2d43c95e5ef2645d164b76ec3cd118ec10",
    "kirk_key_12": "22e830fa0ac9b99cfd0706c7d907429f88f1de4b3605088d09e76c2ece69133f",
    "kirk_key_64": "5855111a412d0aa6cf441adc1a80ea8db8a3fd8ba876e409b2dc10745775eeee",
}
GAME_KEY_RELEASE = "NPJH50631"         # release whose game key is fingerprinted


class BridgeError(Exception):
    pass


def sum8(b):
    return sum(b) & 0xFFFFFFFF


# ------------------------------------------------------- PSP savedata crypt ----
# The console encrypts every savedata block with a Kirk AES stream cipher keyed
# by a per-game key.  Reimplemented here in pure Python as a port of PPSSPP's
# Core/HLE/sceChnnlsv.cpp; byte identical to the reference implementation for
# every mode 5 block.
def _make_sbox():
    """The standard AES S-box: GF(2^8) inverse followed by the affine map."""
    box = bytearray(256)
    p = q = 1
    while True:
        p ^= ((p << 1) ^ (0x1B if p & 0x80 else 0)) & 0xFF      # p *= 3
        q ^= q << 1                                               # q /= 3
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ (q << 1 | q >> 7) ^ (q << 2 | q >> 6) ^ (q << 3 | q >> 5) ^ (q << 4 | q >> 4)
        box[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    box[0] = 0x63
    return bytes(box)


_SBOX = _make_sbox()
_INV_SBOX = bytes(_SBOX.index(bytes([i])) for i in range(256))
_MUL = {}
for _c in (9, 11, 13, 14):
    _t = []
    for _i in range(256):
        _r, _a, _b = 0, _i, _c
        while _b:
            if _b & 1:
                _r ^= _a
            _a = ((_a << 1) ^ 0x1B) & 0xFF if _a & 0x80 else _a << 1
            _b >>= 1
        _t.append(_r)
    _MUL[_c] = _t


def _xt(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _key_expand(key):
    w = [int.from_bytes(key[4 * i:4 * i + 4], "big") for i in range(4)]
    rcon = 1
    for i in range(4, 44):
        t = w[i - 1]
        if i % 4 == 0:
            t = ((t << 8) | (t >> 24)) & 0xFFFFFFFF
            t = (_SBOX[(t >> 24) & 0xFF] << 24) | (_SBOX[(t >> 16) & 0xFF] << 16) \
                | (_SBOX[(t >> 8) & 0xFF] << 8) | _SBOX[t & 0xFF]
            t ^= rcon << 24
            rcon = _xt(rcon)
        w.append(w[i - 4] ^ t)
    return w


def _add_round_key(s, w, rnd):
    for c in range(4):
        v = w[4 * rnd + c]
        for r in range(4):
            s[4 * c + r] ^= (v >> (24 - 8 * r)) & 0xFF


def _aes_decrypt_block(w, blk):
    s = bytearray(blk)
    _add_round_key(s, w, 10)
    for rnd in range(9, 0, -1):
        t = bytearray(s[4 * ((c - r) % 4) + r] for c in range(4) for r in range(4))
        for i in range(16):
            t[i] = _INV_SBOX[t[i]]
        _add_round_key(t, w, rnd)
        m9, m11, m13, m14 = _MUL[9], _MUL[11], _MUL[13], _MUL[14]
        for c in range(4):
            a0, a1, a2, a3 = t[4 * c:4 * c + 4]
            t[4 * c] = m14[a0] ^ m11[a1] ^ m13[a2] ^ m9[a3]
            t[4 * c + 1] = m9[a0] ^ m14[a1] ^ m11[a2] ^ m13[a3]
            t[4 * c + 2] = m13[a0] ^ m9[a1] ^ m14[a2] ^ m11[a3]
            t[4 * c + 3] = m11[a0] ^ m13[a1] ^ m9[a2] ^ m14[a3]
        s = t
    t = bytearray(s[4 * ((c - r) % 4) + r] for c in range(4) for r in range(4))
    for i in range(16):
        t[i] = _INV_SBOX[t[i]]
    _add_round_key(t, w, 0)
    return bytes(t)


def _kirk_cbc_decrypt(key, data):
    """Kirk AES-CBC: the first block is decrypted unchained, then CBC."""
    w = _key_expand(key)
    out = bytearray()
    prev = None
    for off in range(0, len(data) - 15, 16):
        blk = data[off:off + 16]
        d = _aes_decrypt_block(w, blk)
        out += d if prev is None else bytes(a ^ b for a, b in zip(d, prev))
        prev = blk
    return bytes(out)


def _xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def _kirk_member(data, off, length, iv, unkn, keys):
    """Decrypt one 0x800 byte (or shorter) member of a savedata block."""
    buf = bytearray(20 + length)
    buf[20:36] = _xor(iv, keys["key19DC"])
    buf[0:16] = _kirk_cbc_decrypt(keys["kirk_key_12"], bytes(buf[20:36]))
    buf[0:16] = _xor(buf[0:16], keys["key19CC"])
    seed = bytes(buf[0:16])
    pad = bytes(16) if unkn == 1 else seed[:12] + (unkn - 1).to_bytes(4, "little")
    for i in range(20, length + 20, 16):
        buf[i:i + 12] = seed[:12]
        buf[i + 12:i + 16] = unkn.to_bytes(4, "little")
        unkn += 1
    buf[0:length] = _kirk_cbc_decrypt(keys["kirk_key_64"], bytes(buf[20:20 + length]))
    buf[0:16] = _xor(buf[0:16], pad)
    for i in range(length):
        data[off + i] ^= buf[i]
    return unkn


def psp_decrypt(raw, keys):
    """Decrypt a mode 5 `DATKG.BIN` with the KEY_NAMES dict and return the plain core."""
    d = bytearray(raw)
    d += bytes(-len(d) % 16)
    iv = _xor(bytes(d[:16]), keys[GAME_KEY])
    unkn, i, n = 1, 0, len(d) - PSP_ENC_HEAD
    while n - i >= 2048:
        unkn = _kirk_member(d, PSP_ENC_HEAD + i, 2048, iv, unkn, keys)
        i += 2048
    if n - i:
        _kirk_member(d, PSP_ENC_HEAD + i, n - i, iv, unkn, keys)
    return bytes(d[PSP_ENC_HEAD:PSP_ENC_HEAD + len(raw) - PSP_ENC_HEAD])


# ------------------------------------------------------------- validation ----
def psp_ok(core):
    return (len(core) == PSP_LEN and core[:4] == MAGIC
            and core[PSP_HDR:PSP_HDR + 4] == MAGIC
            and struct.unpack_from("<I", core, PSP_HDR_SUM)[0] == sum8(core[:PSP_HDR_SUM])
            and struct.unpack_from("<I", core, PSP_LEN - 4)[0]
            == sum8(core[PSP_HDR:PSP_LEN - 4]))


def pc_ok(core):
    """Both checksums the PC build verifies."""
    return (len(core) == PC_LEN and core[:4] == MAGIC
            and core[PC_HDR:PC_HDR + 4] == MAGIC
            and struct.unpack_from("<I", core, PC_HDR_SUM)[0] == sum8(core[:PC_HDR_SUM])
            and struct.unpack_from("<I", core, PC_LEN - 4)[0]
            == sum8(core[PC_HDR:PC_LEN - 4]))


def fix_pc_sums(core):
    struct.pack_into("<I", core, PC_HDR_SUM, sum8(core[:PC_HDR_SUM]))
    struct.pack_into("<I", core, PC_LEN - 4, sum8(core[PC_HDR:PC_LEN - 4]))


def entry_ok(data):
    return (len(data) == ENTRY_HDR + PC_LEN
            and struct.unpack_from("<I", data, ENTRY_HDR - 4)[0] == PC_LEN
            and pc_ok(data[ENTRY_HDR:]))


# ---------------------------------------------------------------- loading ----
def entropy(b):
    c, n = Counter(b), len(b)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def _hex16(text, what):
    try:
        k = bytes.fromhex(text.strip())
    except ValueError:
        raise BridgeError(f"{what}: not a hex string") from None
    if len(k) != 16:
        raise BridgeError(f"{what}: must be 16 bytes (32 hex digits), got {len(k)}")
    return k


def fingerprint(k):
    return hashlib.sha256(k).hexdigest()


def keys_path(a=None):
    return getattr(a, "keys", None) or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), KEYS_FILE)


def read_keys_file(path):
    """`name = hex` lines; `#` starts a comment.  Empty values are skipped."""
    keys = {}
    if not os.path.exists(path):
        return keys
    with open(path, encoding="utf-8") as f:
        for no, line in enumerate(f, 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            name, sep, value = (x.strip() for x in line.partition("="))
            if not sep or name not in KEY_NAMES:
                raise BridgeError(f"{path}:{no}: expected one of {', '.join(KEY_NAMES)} = <hex>")
            if value:
                keys[name] = _hex16(value, f"{path}:{no} {name}")
    return keys


def write_keys_file(path, keys):
    lines = ["# PSP savedata keys for dr2_save_bridge.py.  Private: never commit or share.\n"]
    lines += [f"{n} = {keys[n].hex() if n in keys else ''}\n" for n in KEY_NAMES]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def load_keys(a):
    """All keys needed to decrypt a DATKG.BIN, from keys.txt and --key."""
    path = keys_path(a)
    keys = read_keys_file(path)
    if getattr(a, "key", None):
        keys[GAME_KEY] = _hex16(a.key, "--key")
    missing = [n for n in KEY_NAMES if n not in keys]
    if missing:
        raise BridgeError(
            f"missing key(s) {', '.join(missing)} in {path}. An encrypted DATKG.BIN needs "
            f"them; run `python dr2_save_bridge.py keys --import <files>` or fill in "
            f"keys.example.txt and save it as {KEYS_FILE} (see README, Keys)")
    for n in CIPHER_KEYS:
        if fingerprint(keys[n]) != KEY_FINGERPRINTS[n]:
            raise BridgeError(f"{n} in {path} is wrong (fingerprint mismatch)")
    return keys


def looks_like_vfs(raw):
    """A savedata.vfs starts with [u32 len][name]; slot names begin with 'data'."""
    return len(raw) > 8 and raw[4:8] == b"data"


def load_psp(path, get_keys):
    """Read a decrypted core, or decrypt a DATKG.BIN; `get_keys` is called only then."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) == PSP_LEN:
        if not psp_ok(data):
            raise BridgeError(f"{path}: not a valid decrypted DR2 save (checksum)")
        return bytearray(data)
    if len(data) != PSP_LEN + PSP_ENC_HEAD:
        raise BridgeError(f"{path}: unexpected size {len(data)}")
    keys = get_keys()
    core = psp_decrypt(data, keys)
    if not psp_ok(core):
        known = fingerprint(keys[GAME_KEY]) == KEY_FINGERPRINTS[GAME_KEY]
        hint = (f"the game key is the one of {GAME_KEY_RELEASE}; is this save from "
                f"another release?" if known else "wrong game_key for this save?")
        raise BridgeError(f"{path}: decryption failed, {hint} "
                          f"(entropy {entropy(core[:4096]):.2f})")
    return bytearray(core)


# ---------------------------------------------------------- PC storage ------
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
    if o != len(blob) - 4 or struct.unpack_from("<I", blob, o)[0] != VFS_FOOTER:
        raise BridgeError("savedata.vfs: bad footer")
    return ents


def vfs_pack(ents):
    out = bytearray()
    for name, data in ents:
        nb = name.encode("ascii")
        out += struct.pack("<I", len(nb)) + nb + struct.pack("<I", len(data)) + data
    return bytes(out + struct.pack("<I", VFS_FOOTER))


def slot_name(n):
    return f"data{n - 1:04d}.bin"


# ----------------------------------------------------------- message log ----
def psp_log_geometry(state):
    """(chars per row, rows) of the PSP message log in this state block."""
    L = PSP_LOG
    head, write = struct.unpack_from("<HH", state, L["head"])
    best, score = L["geometry"][0], None
    for w, rows in L["geometry"]:
        if head >= rows or write >= rows:
            continue
        s = 0
        for r in range(1, min(rows, 33)):
            o = L["text"] + 2 * r * w
            if o + 2 > L["attr"]:
                break
            if state[o - 2:o] != b"\0\0":
                s -= 1000              # text runs across a row boundary: wrong width
            elif state[o:o + 2] != b"\0\0":
                s += 1
        if score is None or s > score:
            best, score = (w, rows), s
    return best


def read_log(state, L, width, rows):
    """Message log lines, oldest first, as (meta, chars, attrs).

    `meta` is 3 bytes per line: speaker, kind (1 single line, 2 first line of a
    multi-line message, 0 continuation), style.
    """
    head, write = struct.unpack_from("<HH", state, L["head"])
    if head >= rows or write >= rows:
        return []
    lines, i = [], head
    while True:
        t, a = L["text"] + 2 * i * width, L["attr"] + i * width
        if t + 2 * width > L["attr"] or a + width > L["end"]:
            break
        chars = list(struct.unpack_from(f"<{width}H", state, t))
        n = chars.index(0) if 0 in chars else width
        m = L["meta"] + 3 * i
        meta = bytes(state[m:m + 3])
        if n or any(meta):
            lines.append((meta, chars[:n], list(state[a:a + n])))
        i = (i + 1) % rows
        if i == write or len(lines) >= rows:
            break
    while lines and lines[0][0][1] == 0:   # oldest message was partly overwritten
        lines.pop(0)
    return lines


def read_psp_log(state):
    return read_log(state, PSP_LOG, *psp_log_geometry(state))


def read_pc_log(state):
    return read_log(state, PC_LOG, PC_LOG["chars"], PC_LOG["rows"])


def _split(chars, limit):
    if len(chars) <= limit:
        return [chars]
    cut = max((k for k in range(1, limit + 1) if chars[k - 1] == 0x20), default=limit)
    return [chars[:cut]] + _split(chars[cut:], limit)


def fit_lines(lines, limit=PC_LOG["chars"] - 1):
    """Split lines longer than `limit` chars at a space, keeping the meta kinds valid."""
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


def write_pc_log(state, lines):
    """Replace the PC message log with `lines` (an empty list clears it)."""
    L = PC_LOG
    w = L["chars"]
    state[L["flag"]:L["end"]] = bytes(L["end"] - L["flag"])
    lines = fit_lines(lines)[-(L["rows"] - 1):]
    for i, (meta, chars, attrs) in enumerate(lines):
        state[L["meta"] + 3 * i:L["meta"] + 3 * i + 3] = meta
        struct.pack_into(f"<{len(chars)}H", state, L["text"] + 2 * i * w, *chars)
        state[L["attr"] + i * w:L["attr"] + i * w + len(attrs)] = bytes(attrs)
    struct.pack_into("<HH", state, L["head"], 0, len(lines))
    state[L["flag"]] = 1 if lines else 0
    state[L["flag"] + 1] = 0xFF
    return len(lines)


# --------------------------------------------------------------- convert ----
def _apply(rows, src, size):
    out = bytearray(size)
    for lo, hi, kind, p in rows:
        if kind == "copy":
            out[lo:hi] = src[p:p + hi - lo]
    return out


def convert_state(src, backlog="clear", main=True):
    """Map one PSP state block onto a PC state block, row by row of STATE_MAP."""
    out = _apply(STATE_MAP, src, PC_STATE)
    write_pc_log(out, read_psp_log(src) if backlog == "keep" else [])
    for f in CURSOR:
        v, = struct.unpack_from("<h", out, f)
        struct.pack_into("<h", out, f, max(-32768, min(32767, v * 2)))
    if main:
        out[SAVE_FLAGS] |= 0x80
    return out


def convert_extra(src):
    return _apply(EXTRA_MAP, src, PC_EXTRA_LEN)


def convert_core(psp, backlog="clear"):
    """Build a complete PC core from a decrypted PSP core."""
    if not psp_ok(psp):
        raise BridgeError("PSP data is not a valid decrypted DR2 save (magic/checksum)")
    core = bytearray(PC_LEN)
    core[0:4] = MAGIC
    core[HDR_COPY[0]:HDR_COPY[1]] = psp[HDR_COPY[0]:HDR_COPY[1]]
    core[PC_HDR:PC_HDR + 4] = MAGIC
    for i in range(2):
        ps, pc = PSP_S0 + i * PSP_STATE, PC_S0 + i * PC_STATE
        core[pc:pc + PC_STATE] = convert_state(psp[ps:ps + PSP_STATE],
                                               backlog if i == 0 else "clear", i == 0)
    core[PC_EXTRA:PC_EXTRA + PC_EXTRA_LEN] = convert_extra(
        psp[PSP_EXTRA:PSP_EXTRA + PSP_EXTRA_LEN])
    fix_pc_sums(core)
    if not pc_ok(core):
        raise BridgeError("internal error: converted core failed its own checksums")
    return core


def stats(core, pc=True):
    s0, extra, coins = (PC_S0, PC_EXTRA, PC_COINS) if pc else (PSP_S0, PSP_EXTRA, PSP_COINS)
    days, = struct.unpack_from("<I", core, HDR_DAYS)
    return dict(chapter=struct.unpack_from("<H", core, s0)[0],
                hours=days * 24 + core[HDR_TIME + 2], minutes=core[HDR_TIME + 1],
                seconds=core[HDR_TIME],
                saves=struct.unpack_from("<I", core, HDR_SAVES)[0],
                coins=struct.unpack_from("<H", core, extra + coins)[0])


def _put_str(entry, off, size, s):
    b = s.encode("utf-8")[:size - 1]
    entry[off:off + size] = b + bytes(size - len(b))


def build_entry(psp, backlog="clear", now=None):
    core = convert_core(psp, backlog)
    e = bytearray(ENTRY_HDR) + core
    e[0:0x40] = TITLE.encode("ascii").ljust(0x40, b"\x00")
    st = stats(core)
    ch = st["chapter"]
    _put_str(e, 0x40, 0x80, CHAPTER_NAMES.get(ch, f"CHAPTER{ch}") + "\u3000")
    _put_str(e, 0xC0, 0x200,
             f"Monocoins {st['coins']}\nPlay Time {st['hours']}:{st['minutes']:02d}:"
             f"{st['seconds']:02d}\nNumber of times saved: {st['saves']}")
    _put_str(e, 0x2C0, 0x20, (now or datetime.datetime.now()).strftime("%d.%m.%Y/%H.%M.%S"))
    struct.pack_into("<I", e, ENTRY_HDR - 4, PC_LEN)
    return e, st


def default_game_dir():
    return os.path.join(os.path.expanduser("~"), "Documents", "My Games", "Danganronpa2")


def _fmt(st):
    return (f"chapter {st['chapter']}, {st['coins']} Monocoins, "
            f"{st['hours']}:{st['minutes']:02d}:{st['seconds']:02d}, {st['saves']} saves")


# -------------------------------------------------------------------- CLI ----
def cmd_convert(a):
    if os.path.exists(a.pc_vfs):
        with open(a.pc_vfs, "rb") as f:
            ents = vfs_parse(f.read())
    else:
        print(f"{a.pc_vfs} does not exist, a new savedata.vfs is created")
        ents = []
    targets = {}
    for spec in a.map:
        slot, _, src = spec.partition("=")
        if not slot.isdigit() or not 1 <= int(slot) <= 99 or not src:
            raise BridgeError(f"bad --map {spec!r}, expected SLOT=FILE")
        targets[slot_name(int(slot))] = src
    print(f"backlog: {a.backlog}")
    loaded = [(name, src, load_psp(src, lambda: load_keys(a))) for name, src in targets.items()]
    for name, src, psp in loaded:
        entry, st = build_entry(psp, a.backlog)
        for x in ents:
            if x[0] == name:
                x[1] = entry
                break
        else:
            ents.append([name, entry])
        print(f"  slot {int(name[4:8]) + 1} <- {os.path.basename(src)}: {_fmt(st)}")
    ents.sort(key=lambda x: (not x[0].startswith("data"), x[0]))
    blob = vfs_pack(ents)
    for name, data in vfs_parse(blob):
        if name in targets and not entry_ok(data):
            raise BridgeError(f"self-check failed for {name}")
    if a.install:
        if os.path.exists(a.out):
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = os.path.join(os.path.dirname(os.path.abspath(a.out)) or ".",
                               f"savedata.vfs.{ts}.bak")
            shutil.copy2(a.out, bak)
            print(f"backup: {bak}")
        else:
            os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    elif os.path.exists(a.out) and not a.force:
        raise BridgeError(f"{a.out} exists (use --force or --install)")
    with open(a.out, "wb") as f:
        f.write(blob)
    print(f"wrote {a.out} ({len(blob)} bytes)")


def verify_core(psp, pc_core):
    """Compare a template-free conversion with a real PC slot, per map row.

    Returns [(block, pc_lo, pc_hi, kind, differing byte offsets)].  Only `copy`
    rows carry PSP data; the rest are expected to differ.
    """
    got = convert_core(psp, "clear")
    out = [("header", HDR_COPY[0], HDR_COPY[1], "copy",
            [i for i in range(*HDR_COPY) if got[i] != pc_core[i]])]
    blocks = [(f"state{i}", PC_S0 + i * PC_STATE, STATE_MAP) for i in range(2)]
    blocks.append(("extra", PC_EXTRA, EXTRA_MAP))
    for name, base, rows in blocks:
        for lo, hi, kind, _ in rows:
            diff = [i for i in range(lo, hi) if got[base + i] != pc_core[base + i]]
            out.append((name, lo, hi, kind, diff))
    return out


def cmd_verify(a):
    psp = load_psp(a.psp, lambda: load_keys(a))
    with open(a.pc_vfs, "rb") as f:
        ents = dict((n, d) for n, d in vfs_parse(f.read()))
    data = ents.get(slot_name(a.slot))
    if data is None or not entry_ok(data):
        raise BridgeError(f"slot {a.slot} missing or damaged")
    mapped = same = 0
    print("block   row              kind   differing bytes")
    for name, lo, hi, kind, diff in verify_core(psp, data[ENTRY_HDR:]):
        if kind == "copy":
            mapped += hi - lo
            same += hi - lo - len(diff)
        shown = " ".join(f"{i:#x}" for i in diff[:6]) + (" ..." if len(diff) > 6 else "")
        print(f"{name:7} {lo:#07x}-{hi:#07x} {kind:5} {len(diff):6}  {shown}")
    print(f"transferred bytes identical to the PC slot: {same}/{mapped} "
          f"({100 * same / mapped:.2f} %)")


def _key_candidates(path):
    """(label, 16 bytes) candidates in a file: C byte arrays, hex strings, PPSSPP
    log lines and 16-byte binary files."""
    import re
    with open(path, "rb") as f:
        data = f.read(8 << 20)
    if len(data) == 16:
        yield "binary", data
        return
    text = data.decode("latin-1")
    for m in re.finditer(r"Game key \(hex\):\s*([0-9A-Fa-f]{32})", text):
        yield GAME_KEY, bytes.fromhex(m.group(1))
    for m in re.finditer(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{32})(?![0-9A-Fa-f])", text):
        yield "hex", bytes.fromhex(m.group(1))
    tokens = [int(t, 16) for t in re.findall(r"0[xX]([0-9A-Fa-f]{2})\b", text)]
    for i in range(len(tokens) - 15):
        yield "array", bytes(tokens[i:i + 16])


def import_keys(paths):
    """Find the keys in the user's files by fingerprint.  Returns (keys, other
    game keys found in PPSSPP logs)."""
    by_fp = {v: n for n, v in KEY_FINGERPRINTS.items()}
    found, others = {}, set()
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in names
                          if n.lower().endswith((".c", ".cpp", ".h", ".txt", ".log", ".bin",
                                                 ".ini", ".py"))]
        else:
            files.append(p)
    for path in files:
        try:
            for label, k in _key_candidates(path):
                name = by_fp.get(fingerprint(k))
                if name:
                    found[name] = k
                elif label == GAME_KEY:
                    others.add(k)
        except OSError:
            continue
    return found, others


def cmd_keys(a):
    path = keys_path(a)
    keys = read_keys_file(path)
    if a.game_key:
        keys[GAME_KEY] = _hex16(a.game_key, "--game-key")
    if a.import_paths:
        found, others = import_keys(a.import_paths)
        keys.update(found)
        print(f"found: {', '.join(sorted(found)) or 'nothing'}")
        if others and GAME_KEY not in found:
            print(f"{len(others)} game key(s) of other releases found in PPSSPP logs; "
                  f"pass the right one with --game-key")
    if a.game_key or a.import_paths:
        write_keys_file(path, keys)
        print(f"wrote {path}")
    print(f"keys file: {path}")
    for n in KEY_NAMES:
        if n not in keys:
            state = "missing"
        elif fingerprint(keys[n]) == KEY_FINGERPRINTS[n]:
            state = "ok"
        elif n == GAME_KEY:
            state = f"set (not the {GAME_KEY_RELEASE} key)"
        else:
            state = "WRONG"
        print(f"  {n:12} {state}")


def cmd_info(a):
    with open(a.file, "rb") as f:
        raw = f.read()
    if looks_like_vfs(raw):
        for name, data in vfs_parse(raw):
            if not name.startswith("data"):
                print(f"{name}: {len(data)} bytes")
                continue
            info = _fmt(stats(data[ENTRY_HDR:])) if entry_ok(data) else "DAMAGED / foreign layout"
            print(f"slot {int(name[4:8]) + 1} ({name}): {info}")
        return
    if len(raw) in (PSP_LEN, PSP_LEN + PSP_ENC_HEAD):
        core = load_psp(a.file, lambda: load_keys(a))
        print(f"PSP save ok, {_fmt(stats(core, pc=False))}")
        state = core[PSP_S0:PSP_S0 + PSP_STATE]
        w, rows = psp_log_geometry(state)
        lines = read_psp_log(state)
        print(f"  message log: {rows} rows of {w} chars, {len(lines)} lines")
        for meta, chars, _ in lines[-9:]:
            print(f"  [{meta.hex()}] {''.join(map(chr, chars))}")
        return
    print(f"unknown file ({len(raw)} bytes)")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="dr2_save_bridge", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--keys", metavar="FILE",
                    help=f"keys file (default: {KEYS_FILE} next to this script)")
    ap.add_argument("--key", help="16-byte PSP game key in hex, overrides the keys file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert", help="put PSP saves into PC savedata.vfs slots")
    c.add_argument("--pc-vfs", help="existing PC savedata.vfs; created if missing")
    c.add_argument("--map", action="append", required=True, metavar="SLOT=FILE",
                   help="PC slot number (1-based) = PSP DATKG.BIN (decrypted or encrypted)")
    c.add_argument("--backlog", choices=["clear", "keep"], default="clear",
                   help="clear (default) or keep the PSP dialogue history")
    c.add_argument("--out", help="output file, defaults to --pc-vfs with --install")
    c.add_argument("--install", action="store_true",
                   help="overwrite --out, keeping a timestamped .bak next to it")
    c.add_argument("--force", action="store_true")
    c.set_defaults(func=cmd_convert)
    i = sub.add_parser("info", help="show the slots of a savedata.vfs or a PSP save")
    i.add_argument("file")
    i.set_defaults(func=cmd_info)
    v = sub.add_parser("verify", help="compare a conversion with a real PC slot "
                                      "made at the same story point")
    v.add_argument("psp", help="PSP DATKG.BIN")
    v.add_argument("pc_vfs", help="PC savedata.vfs")
    v.add_argument("--slot", type=int, required=True, help="PC slot number (1-based)")
    v.set_defaults(func=cmd_verify)
    k = sub.add_parser("keys", help="show, import or set the decryption keys")
    k.add_argument("--import", dest="import_paths", nargs="+", metavar="PATH",
                   help="files or folders to search: PPSSPP sources (sceChnnlsv.cpp, "
                        "kirk_engine.c), PPSSPP logs, gamekey.bin")
    k.add_argument("--game-key", metavar="HEX", help="store this game key")
    k.set_defaults(func=cmd_keys)
    a = ap.parse_args(argv)
    if a.cmd == "convert" and not a.pc_vfs:
        a.pc_vfs = os.path.join(default_game_dir(), "savedata.vfs")
    if a.cmd == "convert" and a.install and not a.out:
        a.out = a.pc_vfs
    if a.cmd == "convert" and not a.out:
        ap.error("convert needs --out, or --install to write over the input file")
    try:
        a.func(a)
    except (BridgeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
