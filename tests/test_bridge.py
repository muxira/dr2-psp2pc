import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import dr2_save_bridge as B  # noqa: E402


def psp_core(lines, head, write, chapter=3, coins=123):
    c = bytearray(B.PSP_LEN)
    c[0:4] = c[0x30:0x34] = B.MAGIC
    c[0x18:0x1B] = bytes([5, 6, 7])
    struct.pack_into("<I", c, 0x20, 42)
    for i in range(2):
        s = B.PSP_S0 + i * B.PSP_STATE
        struct.pack_into("<H", c, s, chapter)
        struct.pack_into("<hh", c, s + 0xBAC - 0x1B0, 100, 50)
    s, bl = B.PSP_S0, B.PSP_BL
    struct.pack_into("<HH", c, s + bl["head"], head, write)
    for i, (kind, text) in enumerate(lines):
        c[s + bl["meta"] + 3 * i:s + bl["meta"] + 3 * i + 3] = bytes([7, kind, 1])
        struct.pack_into(f"<{len(text)}H", c, s + bl["text"] + i * bl["width"] * 2, *map(ord, text))
        c[s + bl["attr"] + i * bl["width"]:s + bl["attr"] + i * bl["width"] + len(text)] = b"\x04" * len(text)
    struct.pack_into("<H", c, 0x19F46, coins)
    struct.pack_into("<I", c, 0x2C, B.sum8(c[:0x2C]))
    struct.pack_into("<I", c, B.PSP_LEN - 4, B.sum8(c[0x30:B.PSP_LEN - 4]))
    return c


def pc_entry():
    core = bytearray(B.PC_LEN)
    core[0:4] = core[0x40:0x44] = B.MAGIC
    s = B.PC_S0
    core[s + B.PC_BL["text"]:s + B.PC_BL["text"] + 4] = "Hi".encode("utf-16-le")
    B.fix_pc_sums(core)
    return bytearray(B.PC_ENTRY_HDR) + core


def pc_lines(entry):
    s = entry[B.PC_ENTRY_HDR + B.PC_S0:][:B.PC_STATE]
    bl = B.PC_BL
    head, write = struct.unpack_from("<HH", s, bl["head"])
    out = []
    for i in range(write):
        chars = struct.unpack_from("<64H", s, bl["text"] + i * 0x80)
        n = chars.index(0) if 0 in chars else 64
        out.append((s[bl["meta"] + 3 * i + 1], "".join(map(chr, chars[:n]))))
    return head, write, s[bl["flag"]], out


RING = [(1, "L0"), (1, "L1"), (0, "stale"), (2, "L3a"), (0, "L3b"),
        (1, "L5"), (1, "L6"), (1, "L7"), (1, "L8")]


class ConvertTest(unittest.TestCase):
    def test_clear_mode(self):
        e, st = B.build_entry(psp_core(RING, 3, 2), pc_entry(), "clear")
        self.assertTrue(B.entry_ok(e))
        self.assertEqual((st["chapter"], st["coins"], st["saves"]), (3, 123, 42))
        self.assertEqual((st["hours"], st["minutes"], st["seconds"]), (7, 6, 5))
        core = e[B.PC_ENTRY_HDR:]
        s = B.PC_S0
        self.assertFalse(any(core[s + B.PC_BL["head"]:s + B.PC_BL["end"]]))
        self.assertEqual(struct.unpack_from("<hh", core, s + 0xBAC), (200, 100))
        self.assertEqual(bytes(e[0x40:0x48]), b"CHAPTER3")

    def test_keep_mode_order(self):
        e, _ = B.build_entry(psp_core(RING, 3, 2), pc_entry(), "keep")
        self.assertTrue(B.entry_ok(e))
        head, write, flag, lines = pc_lines(e)
        self.assertEqual((head, flag), (0, 1))
        self.assertEqual([t for _, t in lines], ["L3a", "L3b", "L5", "L6", "L7", "L8", "L0", "L1"])
        self.assertEqual(write, 8)

    def test_keep_full_ring_drops_leading_continuation(self):
        ring = [(0, "tail")] + RING[1:2] + [(1, "x")] + RING[3:]
        _, _, _, lines = pc_lines(B.build_entry(psp_core(ring, 0, 0), pc_entry(), "keep")[0])
        self.assertEqual(lines[0][1], "L1")

    def test_long_line_is_split(self):
        text = ("word " * 20).strip()
        out = B.fit_lines([(bytes([1, 1, 0]), [ord(c) for c in text], [4] * len(text))])
        self.assertEqual([m[1] for m, _, _ in out], [2, 0])
        self.assertTrue(all(len(c) <= B.PC_LINE_MAX for _, c, _ in out))
        self.assertEqual("".join("".join(map(chr, c)) for _, c, _ in out), text)

    def test_rejects_bad_input(self):
        bad = psp_core(RING, 0, 0)
        bad[100] ^= 1
        with self.assertRaises(B.BridgeError):
            B.convert_core(bad, pc_entry()[B.PC_ENTRY_HDR:])


class CliTest(unittest.TestCase):
    def test_convert_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            vfs = os.path.join(td, "savedata.vfs")
            psp = os.path.join(td, "psp.bin")
            out = os.path.join(td, "out.vfs")
            open(vfs, "wb").write(B.vfs_pack([["data0000.bin", pc_entry()], ["icon0000.png", bytearray(b"png")]]))
            open(psp, "wb").write(psp_core(RING, 3, 2))
            self.assertEqual(B.main(["convert", "--pc-vfs", vfs, "--map", f"2={psp}",
                                     "--backlog", "keep", "--out", out]), 0)
            ents = dict(B.vfs_parse(open(out, "rb").read()))
            self.assertEqual(sorted(ents), ["data0000.bin", "data0001.bin", "icon0000.png"])
            self.assertTrue(B.entry_ok(ents["data0001.bin"]))
            self.assertEqual(ents["data0000.bin"], pc_entry())


if __name__ == "__main__":
    unittest.main()
