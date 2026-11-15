#!/usr/bin/env python3
"""HLK-LD2450 24 GHz multi-target tracking radar — serial protocol (pure stdlib).

The LD2450 is a Hi-Link 1TX+2RX 24 GHz FMCW module that does on-board multi-
target tracking and reports up to 3 targets over UART (default 256000-8N1,
10 frames/s). This module decodes that vendor stream and builds its config
command frames. It is the *non-invasive baseline* harness for the LD2450,
mirroring how the Rd-03E work began by reading the SoC's vendor stream before
any from-source firmware RE (see ../radar-workspace, docs/re).

Report frame (fixed 30 bytes, little-endian):
    AA FF 03 00 | T1[8] | T2[8] | T3[8] | 55 CC
Each 8-byte target: X(i16 mm) Y(i16 mm) SPEED(i16 cm/s) RES(u16 mm).
Coordinates/speed use a SIGN-MAGNITUDE encoding: bit15 set => POSITIVE
(value = raw-0x8000), bit15 clear => NEGATIVE (value = -raw). An all-zero
8-byte record means "no target in this slot". Verified against the manual's
worked example (see tests).

Config frame (host->module), little-endian:
    FD FC FB FA | len(u16) | cmd(u16) | value... | 04 03 02 01
ACK  (module->host): same framing, cmd word is (cmd | 0x0100), payload starts
with a u16 status (0 = success). Config words must be bracketed by
enable-config (0x00FF, value 0x0001) ... end-config (0x00FE).
"""
import math
import struct

# ---- data (report) stream ---------------------------------------------------
REPORT_HEADER = b"\xAA\xFF\x03\x00"
REPORT_TAIL = b"\x55\xCC"
REPORT_LEN = 30
N_TARGETS = 3
TARGET_LEN = 8


def decode_signed(raw):
    """LD2450 sign-magnitude i16: bit15 set => positive, clear => negative."""
    raw &= 0xFFFF
    return (raw - 0x8000) if (raw & 0x8000) else -raw


def decode_target(b8):
    """Decode one 8-byte target record -> dict. `present` is False for an
    all-zero slot (no target)."""
    if len(b8) != TARGET_LEN:
        raise ValueError("target record must be 8 bytes, got %d" % len(b8))
    xr, yr, sr, res = struct.unpack("<HHHH", b8)
    present = any(b8)
    x = decode_signed(xr)      # mm, +x to the module's right
    y = decode_signed(yr)      # mm, +y away from the module (down-range)
    v = decode_signed(sr)      # cm/s, + approaching? (sign = radial, verify on HW)
    r = math.hypot(x, y)       # mm, range to target
    # bearing from boresight (+y). +angle to the right (+x).
    ang = math.degrees(math.atan2(x, y)) if present else 0.0
    return {"present": present, "x": x, "y": y, "speed": v,
            "res": res, "r": round(r, 1), "angle": round(ang, 2)}


def parse_report(frame):
    """Validate a 30-byte report frame and return the 3 target dicts.
    Raises ValueError on a malformed frame."""
    if len(frame) != REPORT_LEN:
        raise ValueError("report frame must be %d bytes, got %d" % (REPORT_LEN, len(frame)))
    if frame[:4] != REPORT_HEADER:
        raise ValueError("bad report header %r" % frame[:4])
    if frame[-2:] != REPORT_TAIL:
        raise ValueError("bad report tail %r" % frame[-2:])
    return [decode_target(frame[4 + i * TARGET_LEN: 4 + (i + 1) * TARGET_LEN])
            for i in range(N_TARGETS)]


class ReportParser:
    """Byte-stream resync parser. Feed arbitrary chunks; yields complete,
    validated 30-byte report frames, recovering from partial/garbled data."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, chunk):
        """Append bytes; yield each complete valid report frame found."""
        self._buf += chunk
        out = []
        while True:
            i = self._buf.find(REPORT_HEADER)
            if i < 0:
                # keep only a possible partial header at the tail
                if len(self._buf) > len(REPORT_HEADER):
                    del self._buf[:-len(REPORT_HEADER)]
                break
            if i > 0:
                del self._buf[:i]            # drop pre-header garbage
            if len(self._buf) < REPORT_LEN:
                break                        # need more bytes
            frame = bytes(self._buf[:REPORT_LEN])
            if frame[-2:] == REPORT_TAIL:
                out.append(parse_report(frame))
                del self._buf[:REPORT_LEN]
            else:
                del self._buf[:1]            # false header; resync one byte on
        return out


# ---- config command protocol ------------------------------------------------
CFG_HEADER = b"\xFD\xFC\xFB\xFA"
CFG_TAIL = b"\x04\x03\x02\x01"

CMD_ENABLE_CONFIG = 0x00FF      # value 0x0001
CMD_END_CONFIG = 0x00FE
CMD_SINGLE_TARGET = 0x0080      # single-target tracking
CMD_MULTI_TARGET = 0x0090       # multi-target tracking
CMD_QUERY_TARGET_MODE = 0x0091
CMD_READ_FIRMWARE = 0x00A0
CMD_SET_BAUD = 0x00A1
CMD_FACTORY_RESET = 0x00A2
CMD_RESTART = 0x00A3
CMD_BLUETOOTH = 0x00A4
CMD_QUERY_MAC = 0x00A5


def build_command(cmd, value=b""):
    """Build a host->module config frame for command word `cmd` with payload
    `value` (bytes). Length counts the 2-byte cmd word + value."""
    if isinstance(value, int):
        value = struct.pack("<H", value & 0xFFFF)
    body = struct.pack("<H", cmd & 0xFFFF) + value
    return CFG_HEADER + struct.pack("<H", len(body)) + body + CFG_TAIL


def enable_config():
    return build_command(CMD_ENABLE_CONFIG, struct.pack("<H", 0x0001))


def end_config():
    return build_command(CMD_END_CONFIG)


def set_target_mode(multi):
    return build_command(CMD_MULTI_TARGET if multi else CMD_SINGLE_TARGET)


def parse_ack(frame):
    """Parse a module->host ACK frame -> (cmd, status, payload).
    cmd is the command word (ACK flag 0x0100 stripped); status 0 = success."""
    if frame[:4] != CFG_HEADER or frame[-4:] != CFG_TAIL:
        raise ValueError("bad config ACK framing")
    (ln,) = struct.unpack("<H", frame[4:6])
    body = frame[6:6 + ln]
    (ack_cmd,) = struct.unpack("<H", body[:2])
    status = struct.unpack("<H", body[2:4])[0] if ln >= 4 else None
    return ack_cmd & 0x00FF, status, body[4:]


class AckParser:
    """Resync parser for module->host config ACK frames."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, chunk):
        self._buf += chunk
        out = []
        while True:
            i = self._buf.find(CFG_HEADER)
            if i < 0:
                if len(self._buf) > len(CFG_HEADER):
                    del self._buf[:-len(CFG_HEADER)]
                break
            if i > 0:
                del self._buf[:i]
            if len(self._buf) < 6:
                break
            (ln,) = struct.unpack("<H", self._buf[4:6])
            total = 6 + ln + 4
            if len(self._buf) < total:
                break
            frame = bytes(self._buf[:total])
            if frame[-4:] == CFG_TAIL:
                out.append(parse_ack(frame))
                del self._buf[:total]
            else:
                del self._buf[:1]
        return out
