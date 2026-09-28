"""Unit tests for dr2_save_bridge.  Synthetic data only, no real saves."""
import os
import re
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dr2_save_bridge as B  # noqa: E402


def psp_fix(c):
    struct.pack_into("<I", c, B.PSP_HDR_SUM, B.sum8(c[:B.PSP_HDR_SUM]))
    struct.pack_into("<I", c, B.PSP_LEN - 4, B.sum8(c[B.PSP_HDR:B.PSP_LEN - 4]))
    return c


def psp_core(chapter=3, coins=123, lines=(), head=0, write=None, width=96, rows=9, fill=None):
    c = bytearray(B.PSP_LEN)
    if fill is not None:
        for i in range(B.PSP_HDR + 8, B.PSP_LEN - 4):
            c[i] = fill(i)
    c[0:4] = c[B.PSP_HDR:B.PSP_HDR + 4] = B.MAGIC
    c[B.PSP_HDR + 4:B.PSP_HDR + 8] = bytes(4)
    c[4:0x2C] = bytes(range(4, 0x2C))
    c[B.HDR_TIME:B.HDR_TIME + 3] = bytes([5, 6, 7])
    struct.pack_into("<II", c, B.HDR_DAYS, 0, 42)
    for i in range(2):
        s = B.PSP_S0 + i * B.PSP_STATE
        struct.pack_into("<H", c, s, chapter)
        struct.pack_into("<hh", c, s + B.CURSOR[0] - 0x1B0, 100, 50)
        struct.pack_into("<HH", c, s + B.PSP_LOG["head"], 0, 0)
    s, L = B.PSP_S0, B.PSP_LOG
    for k, (kind, text) in enumerate(lines):
        i = (head + k) % rows
        c[s + L["meta"] + 3 * i:s + L["meta"] + 3 * i + 3] = bytes([7, kind, 1])
        struct.pack_into(f"<{len(text)}H", c, s + L["text"] + 2 * i * width, *map(ord, text))
        c[s + L["attr"] + i * width:s + L["attr"] + i * width + len(text)] = b"\x04" * len(text)
    if lines:
        write = (head + len(lines)) % rows if write is None else write
        struct.pack_into("<HH", c, s + L["head"], head, write)
    struct.pack_into("<H", c, B.PSP_EXTRA + B.PSP_COINS, coins)
    return psp_fix(c)


def gs(core, i=0):
    return core[B.PC_S0 + i * B.PC_STATE:B.PC_S0 + (i + 1) * B.PC_STATE]


class TestChecksums(unittest.TestCase):
    def test_converted_core_passes_both_pc_checksums(self):
        core = B.convert_core(psp_core())
        self.assertTrue(B.pc_ok(core))
        core[B.PC_LEN - 5] ^= 1
        self.assertFalse(B.pc_ok(core))

    def test_rejects_bad_input(self):
        bad = psp_core()
        bad[100] ^= 1
        with self.assertRaises(B.BridgeError):
            B.convert_core(bad)


class TestMaps(unittest.TestCase):
    def check_rows(self, rows, pc_len, psp_len):
        pos = prev = 0
        for lo, hi, kind, p in rows:
            self.assertEqual(lo, pos)
            self.assertGreater(hi, lo)
            self.assertIn(kind, ("copy", "zero", "log"))
            if kind == "copy":
                self.assertGreaterEqual(p, prev)
                self.assertLessEqual(p + hi - lo, psp_len)
                prev = p + hi - lo
            else:
                self.assertIsNone(p)
            pos = hi
        self.assertEqual(pos, pc_len)

    def test_rows_tile_the_whole_pc_blocks(self):
        self.check_rows(B.STATE_MAP, B.PC_STATE, B.PSP_STATE)
        self.check_rows(B.EXTRA_MAP, B.PC_EXTRA_LEN, B.PSP_EXTRA_LEN)

    def test_layout_sizes(self):
        self.assertEqual(B.PC_EXTRA_LEN, 0x3F24)
        self.assertEqual(B.PSP_EXTRA_LEN, 0x1F20)
        self.assertEqual(B.PC_EXTRA + B.PC_COINS, 0x372BA)

    def test_copy_rows_are_verbatim_and_the_rest_is_fresh(self):
        psp = psp_core(fill=lambda i: (i * 37 + 11) & 0xFF)
        core = B.convert_core(psp)
        self.assertEqual(core[4:0x2C], psp[4:0x2C])
        self.assertEqual(core[0x2C:B.PC_HDR_SUM], bytes(B.PC_HDR_SUM - 0x2C))
        for i in range(2):
            src, dst = psp[B.PSP_S0 + i * B.PSP_STATE:], gs(core, i)
            for lo, hi, kind, p in B.STATE_MAP:
                if kind == "copy":
                    want = bytearray(src[p:p + hi - lo])
                    for f in B.CURSOR:
                        if lo <= f < hi:
                            v, = struct.unpack_from("<h", want, f - lo)
                            struct.pack_into("<h", want, f - lo, max(-32768, min(32767, v * 2)))
                    if i == 0 and lo <= B.SAVE_FLAGS < hi:
                        want[B.SAVE_FLAGS - lo] |= 0x80
                    self.assertEqual(dst[lo:hi], want, (i, hex(lo)))
                elif kind == "zero":
                    self.assertEqual(dst[lo:hi], bytes(hi - lo), (i, hex(lo)))
        src, dst = psp[B.PSP_EXTRA:], core[B.PC_EXTRA:]
        for lo, hi, kind, p in B.EXTRA_MAP:
            want = src[p:p + hi - lo] if kind == "copy" else bytes(hi - lo)
            self.assertEqual(dst[lo:hi], want, hex(lo))

    def test_cursor_is_scaled_to_the_pc_screen(self):
        self.assertEqual(struct.unpack_from("<hh", gs(B.convert_core(psp_core())), B.CURSOR[0]),
                         (200, 100))

    def test_entry_fields(self):
        e, st = B.build_entry(psp_core(chapter=3, coins=123))
        self.assertTrue(B.entry_ok(e))
        self.assertEqual((st["chapter"], st["coins"], st["saves"]), (3, 123, 42))
        self.assertEqual((st["hours"], st["minutes"], st["seconds"]), (7, 6, 5))
        self.assertEqual(bytes(e[0x40:0x48]), b"CHAPTER3")


RING = [(2, "L3a"), (0, "L3b"), (1, "L5"), (1, "L6")]


class TestLog(unittest.TestCase):
    def test_clear(self):
        state = gs(B.convert_core(psp_core(lines=RING)))
        L = B.PC_LOG
        self.assertEqual((state[L["flag"]], state[L["flag"] + 1]), (0, 0xFF))
        self.assertFalse(any(state[L["head"]:L["end"]]))

    def test_keep_both_geometries_and_ring_order(self):
        for width, rows, head in ((96, 9, 7), (28, 0x200, 0x1FE)):
            psp = psp_core(lines=RING, head=head, width=width, rows=rows)
            self.assertEqual(B.psp_log_geometry(psp[B.PSP_S0:]), (width, rows))
            got = B.read_pc_log(gs(B.convert_core(psp, "keep")))
            self.assertEqual([("".join(map(chr, c)), m[1]) for m, c, _ in got],
                             [(t, k) for k, t in RING])
            self.assertEqual(got[0][2], [4, 4, 4])

    def test_leading_continuation_is_dropped(self):
        ring = [(0, "tail")] + RING
        got = B.read_psp_log(psp_core(lines=ring)[B.PSP_S0:])
        self.assertEqual("".join(map(chr, got[0][1])), "L3a")

    def test_long_line_is_split(self):
        text = ("word " * 20).strip()
        out = B.fit_lines([(bytes([1, 1, 0]), [ord(c) for c in text], [4] * len(text))])
        self.assertEqual([m[1] for m, _, _ in out], [2, 0])
        self.assertTrue(all(len(c) < B.PC_LOG["chars"] for _, c, _ in out))
        self.assertEqual("".join("".join(map(chr, c)) for _, c, _ in out), text)


FAKE_KEYS = {n: bytes([i + 1]) * 16 for i, n in enumerate(B.KEY_NAMES)}


class FakeFingerprints:
    def __enter__(self):
        self.saved = dict(B.KEY_FINGERPRINTS)
        B.KEY_FINGERPRINTS.update({n: B.fingerprint(k) for n, k in FAKE_KEYS.items()})

    def __exit__(self, *exc):
        B.KEY_FINGERPRINTS.clear()
        B.KEY_FINGERPRINTS.update(self.saved)


class Args:
    def __init__(self, keys, key=None):
        self.keys, self.key = keys, key


class TestKeys(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.dir.name, "keys.txt")

    def tearDown(self):
        self.dir.cleanup()

    def test_no_key_material_in_the_source(self):
        src = open(B.__file__, encoding="utf-8").read()
        self.assertEqual(re.findall(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])", src), [])
        self.assertEqual(re.findall(r"(0[xX][0-9A-Fa-f]{2}\s*,\s*){15}", src), [])
        for fp in B.KEY_FINGERPRINTS.values():
            self.assertRegex(fp, r"^[0-9a-f]{64}$")

    def test_missing_keys_are_named(self):
        with self.assertRaises(B.BridgeError) as cm:
            B.load_keys(Args(self.file))
        for n in B.KEY_NAMES:
            self.assertIn(n, str(cm.exception))

    def test_wrong_cipher_key_is_detected(self):
        with FakeFingerprints():
            B.write_keys_file(self.file, dict(FAKE_KEYS, key19CC=bytes(16)))
            with self.assertRaises(B.BridgeError) as cm:
                B.load_keys(Args(self.file))
            self.assertIn("key19CC", str(cm.exception))

    def test_import_and_override(self):
        d = self.dir.name
        arr = lambda k: "{" + ", ".join(f"0x{b:02X}" for b in k) + "}"
        with open(os.path.join(d, "sceChnnlsv.cpp"), "w") as f:
            f.write(f"u8 key19CC[16] = {arr(FAKE_KEYS['key19CC'])};\n"
                    f"u8 key19DC[16] = {arr(FAKE_KEYS['key19DC'])};\n"
                    f"u8 v12[16] = {arr(FAKE_KEYS['kirk_key_12'])};\n"
                    f"u8 v64[16] = {arr(FAKE_KEYS['kirk_key_64'])};\n")
        with open(os.path.join(d, "gamekey.bin"), "wb") as f:
            f.write(FAKE_KEYS[B.GAME_KEY])
        with FakeFingerprints():
            self.assertEqual(B.main(["--keys", self.file, "keys", "--import", d]), 0)
            self.assertEqual(B.load_keys(Args(self.file)), FAKE_KEYS)
            self.assertEqual(B.load_keys(Args(self.file, "ab" * 16))[B.GAME_KEY], b"\xab" * 16)

    def test_decrypt_is_keyed(self):
        raw = bytes(range(256)) * 8
        other = dict(FAKE_KEYS, game_key=bytes(16))
        self.assertNotEqual(B.psp_decrypt(raw, FAKE_KEYS), B.psp_decrypt(raw, other))


class TestCli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.psp = self.path("psp.bin")
        with open(self.psp, "wb") as f:
            f.write(psp_core(lines=RING))

    def tearDown(self):
        self.dir.cleanup()

    def path(self, name):
        return os.path.join(self.dir.name, name)

    def test_convert_without_any_pc_save(self):
        out = self.path("new.vfs")
        self.assertEqual(B.main(["convert", "--pc-vfs", self.path("none.vfs"),
                                 "--map", f"2={self.psp}", "--out", out]), 0)
        ents = B.vfs_parse(open(out, "rb").read())
        self.assertEqual([n for n, _ in ents], ["data0001.bin"])
        self.assertTrue(B.entry_ok(ents[0][1]))

    def test_existing_slots_are_kept(self):
        vfs = self.path("savedata.vfs")
        old, _ = B.build_entry(psp_core(chapter=5))
        with open(vfs, "wb") as f:
            f.write(B.vfs_pack([["data0000.bin", old], ["icon0000.png", bytearray(b"png")]]))
        self.assertEqual(B.main(["convert", "--pc-vfs", vfs, "--map", f"2={self.psp}",
                                 "--backlog", "keep", "--install"]), 0)
        ents = dict(B.vfs_parse(open(vfs, "rb").read()))
        self.assertEqual(sorted(ents), ["data0000.bin", "data0001.bin", "icon0000.png"])
        self.assertEqual(ents["data0000.bin"], old)
        self.assertTrue(any(n.endswith(".bak") for n in os.listdir(self.dir.name)))

    def test_encrypted_save_without_keys_is_refused(self):
        enc = self.path("enc.BIN")
        with open(enc, "wb") as f:
            f.write(bytes(B.PSP_LEN + B.PSP_ENC_HEAD))
        out = self.path("o.vfs")
        self.assertEqual(B.main(["--keys", self.path("none.txt"), "convert", "--pc-vfs",
                                 self.path("x.vfs"), "--map", f"1={enc}", "--out", out]), 1)
        self.assertFalse(os.path.exists(out))

    def test_verify_and_info(self):
        vfs = self.path("pc.vfs")
        e, _ = B.build_entry(psp_core(lines=RING))
        with open(vfs, "wb") as f:
            f.write(B.vfs_pack([["data0000.bin", e]]))
        self.assertEqual(B.main(["verify", self.psp, vfs, "--slot", "1"]), 0)
        rows = B.verify_core(psp_core(lines=RING), e[B.ENTRY_HDR:])
        self.assertTrue(all(not d for _, _, _, k, d in rows if k == "copy"))
        self.assertEqual(B.main(["info", vfs]), 0)
        self.assertEqual(B.main(["info", self.psp]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
