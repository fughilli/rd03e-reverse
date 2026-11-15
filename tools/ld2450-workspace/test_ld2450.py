#!/usr/bin/env python3
"""Unit tests for the LD2450 protocol decode (stdlib unittest, no hardware)."""
import unittest
import struct
import ld2450


# Manual's worked example: T1 bytes 0E 03 | B1 86 | 10 00 | 40 01
#   X = -782 mm, Y = 1713 mm, speed = -16 cm/s, res = 320 mm
T1 = bytes.fromhex("0E03B1861000 4001".replace(" ", ""))
EMPTY = bytes(8)
FRAME_1T = ld2450.REPORT_HEADER + T1 + EMPTY + EMPTY + ld2450.REPORT_TAIL


class TestDecode(unittest.TestCase):
    def test_signed_magnitude(self):
        # bit15 set => positive (raw-0x8000); clear => negative (-raw)
        self.assertEqual(ld2450.decode_signed(0x030E), -782)
        self.assertEqual(ld2450.decode_signed(0x86B1), 1713)
        self.assertEqual(ld2450.decode_signed(0x0010), -16)
        self.assertEqual(ld2450.decode_signed(0x8000), 0)     # +0
        self.assertEqual(ld2450.decode_signed(0x0000), 0)     # -0
        self.assertEqual(ld2450.decode_signed(0xFFFF), 32767)
        self.assertEqual(ld2450.decode_signed(0x7FFF), -32767)

    def test_decode_target_manual(self):
        t = ld2450.decode_target(T1)
        self.assertTrue(t["present"])
        self.assertEqual(t["x"], -782)
        self.assertEqual(t["y"], 1713)
        self.assertEqual(t["speed"], -16)
        self.assertEqual(t["res"], 320)
        # r = hypot(-782, 1713) ~= 1883.0 mm
        self.assertAlmostEqual(t["r"], 1883.0, delta=0.5)
        # bearing atan2(x=-782, y=1713) ~= -24.5 deg (to the left)
        self.assertAlmostEqual(t["angle"], -24.55, delta=0.2)

    def test_empty_slot(self):
        t = ld2450.decode_target(EMPTY)
        self.assertFalse(t["present"])
        self.assertEqual((t["x"], t["y"], t["speed"], t["res"]), (0, 0, 0, 0))

    def test_parse_report(self):
        ts = ld2450.parse_report(FRAME_1T)
        self.assertEqual(len(ts), 3)
        self.assertTrue(ts[0]["present"])
        self.assertFalse(ts[1]["present"])
        self.assertFalse(ts[2]["present"])
        self.assertEqual(ts[0]["y"], 1713)

    def test_parse_report_bad(self):
        with self.assertRaises(ValueError):
            ld2450.parse_report(FRAME_1T[:-1])            # short
        with self.assertRaises(ValueError):
            ld2450.parse_report(b"\x00" + FRAME_1T[1:])   # bad header
        with self.assertRaises(ValueError):
            ld2450.parse_report(FRAME_1T[:-2] + b"\x00\x00")  # bad tail


class TestStreamResync(unittest.TestCase):
    def test_clean_stream(self):
        p = ld2450.ReportParser()
        frames = p.feed(FRAME_1T + FRAME_1T)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0][0]["y"], 1713)

    def test_garbage_prefix_and_split(self):
        p = ld2450.ReportParser()
        stream = b"\x11\x22\x33" + FRAME_1T + b"\xAA\xFF" + FRAME_1T[2:]
        out = []
        # feed one byte at a time to stress the resync/partial logic
        for i in range(len(stream)):
            out += p.feed(stream[i:i + 1])
        self.assertEqual(len(out), 2)
        for f in out:
            self.assertEqual(f[0]["x"], -782)

    def test_false_header_recovers(self):
        # a header pattern with a wrong tail must not desync the next real frame
        fake = ld2450.REPORT_HEADER + bytes(24) + b"\x00\x00"  # wrong tail
        p = ld2450.ReportParser()
        out = p.feed(fake + FRAME_1T)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0]["y"], 1713)

    def test_three_targets(self):
        f = ld2450.REPORT_HEADER + T1 + T1 + T1 + ld2450.REPORT_TAIL
        ts = ld2450.ReportParser().feed(f)[0]
        self.assertTrue(all(t["present"] for t in ts))


class TestConfig(unittest.TestCase):
    def test_enable_config_frame(self):
        f = ld2450.enable_config()
        self.assertTrue(f.startswith(ld2450.CFG_HEADER))
        self.assertTrue(f.endswith(ld2450.CFG_TAIL))
        (ln,) = struct.unpack("<H", f[4:6])
        self.assertEqual(ln, 4)                       # cmd(2) + value(2)
        self.assertEqual(f[6:8], struct.pack("<H", ld2450.CMD_ENABLE_CONFIG))
        self.assertEqual(f[8:10], struct.pack("<H", 0x0001))

    def test_target_mode_frames(self):
        self.assertIn(struct.pack("<H", ld2450.CMD_MULTI_TARGET),
                      ld2450.set_target_mode(True))
        self.assertIn(struct.pack("<H", ld2450.CMD_SINGLE_TARGET),
                      ld2450.set_target_mode(False))

    def test_ack_roundtrip(self):
        # synthesize a success ACK for read-firmware: cmd|0x0100, status 0
        body = struct.pack("<H", ld2450.CMD_READ_FIRMWARE | 0x0100) + \
            struct.pack("<H", 0) + b"\x01\x02\x03\x04"
        frame = ld2450.CFG_HEADER + struct.pack("<H", len(body)) + body + ld2450.CFG_TAIL
        cmd, status, payload = ld2450.parse_ack(frame)
        self.assertEqual(cmd, ld2450.CMD_READ_FIRMWARE & 0xFF)
        self.assertEqual(status, 0)
        self.assertEqual(payload, b"\x01\x02\x03\x04")

    def test_ack_parser_stream(self):
        body = struct.pack("<H", ld2450.CMD_ENABLE_CONFIG | 0x0100) + struct.pack("<H", 0)
        frame = ld2450.CFG_HEADER + struct.pack("<H", len(body)) + body + ld2450.CFG_TAIL
        out = ld2450.AckParser().feed(b"\x99" + frame + frame)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0][1], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
