#!/usr/bin/env python3
"""HLK-LD2450 reader -> line-delimited JSON on stdout.

Transport-agnostic so it can run wherever the module is wired:
  --port /dev/ttyUSB0        local USB-serial adapter (needs pyserial)
  --tcp  rig-radar:9600      a TCP UART tunnel (e.g. the Pi or an ESP32 bridge)
  --replay capture.bin       replay a raw-UART capture (dev/offline, no hardware)

Default link is 256000-8N1. Optionally set the tracking mode first
(--mode single|multi) and/or read the firmware version (--fw).

Output (newline JSON):
  {"t":"ready","module":"ld2450","transport":...,"mode":...}
  {"t":"targets","ts":<unix>,"n":<#present>,"targets":[{slot,present,x,y,speed,res,r,angle},...]}
  {"t":"status","fps":...,"frames":...,"present":...}
  {"t":"log"/"error", ...}

Coordinates: x mm (+ to module's right), y mm (down-range), speed cm/s,
r mm (range), angle deg from boresight. See ld2450.py for the wire format.
"""
import argparse
import json
import sys
import time

import ld2450

BAUD = 256000


def emit(o):
    sys.stdout.write(json.dumps(o, separators=(",", ":")) + "\n")
    sys.stdout.flush()


# ---- transports -------------------------------------------------------------
class SerialTransport:
    def __init__(self, port, baud):
        import serial  # lazy: only needed for a real port
        self.s = serial.Serial(port, baud, timeout=0.1)
        self.name = "serial:%s@%d" % (port, baud)

    def read(self, n):
        return self.s.read(n)

    def write(self, b):
        self.s.write(b)

    def close(self):
        self.s.close()


class TcpTransport:
    def __init__(self, hostport):
        import socket
        host, _, port = hostport.rpartition(":")
        self.s = socket.create_connection((host, int(port)), timeout=5)
        self.s.settimeout(0.1)
        self.name = "tcp:%s" % hostport

    def read(self, n):
        import socket
        try:
            return self.s.recv(n)
        except socket.timeout:
            return b""

    def write(self, b):
        self.s.sendall(b)

    def close(self):
        self.s.close()


class ReplayTransport:
    """Replay a raw-UART capture file as if it were a live link."""
    def __init__(self, path, rate=0.0):
        self.f = open(path, "rb")
        self.rate = rate            # bytes/s throttle; 0 = as fast as possible
        self.name = "replay:%s" % path

    def read(self, n):
        data = self.f.read(n)
        if self.rate and data:
            time.sleep(len(data) / self.rate)
        return data

    def write(self, b):
        pass                        # replay is read-only

    def close(self):
        self.f.close()


# ---- config (optional, before streaming) ------------------------------------
def do_config(tr, mode=None, read_fw=False):
    """Enter config, optionally set tracking mode / read firmware, exit config.
    Best-effort: ACK timeouts are logged, not fatal."""
    acks = ld2450.AckParser()

    def exchange(frame, label, wait=0.4):
        tr.write(frame)
        t0 = time.time()
        got = []
        while time.time() - t0 < wait:
            got += acks.feed(tr.read(64))
            if got:
                break
        if got:
            cmd, status, payload = got[0]
            emit({"t": "cmdresult", "cmd": label, "status": status,
                  "payload": payload.hex()})
            return payload
        emit({"t": "log", "msg": "no ACK for %s" % label})
        return None

    exchange(ld2450.enable_config(), "enable_config")
    if read_fw:
        exchange(ld2450.build_command(ld2450.CMD_READ_FIRMWARE), "read_firmware")
    if mode in ("single", "multi"):
        exchange(ld2450.set_target_mode(mode == "multi"), "set_mode_%s" % mode)
    exchange(ld2450.end_config(), "end_config")


# ---- main stream loop -------------------------------------------------------
def run(tr, mode=None, read_fw=False, status_period=1.0):
    if mode or read_fw:
        do_config(tr, mode=mode, read_fw=read_fw)
    emit({"t": "ready", "module": "ld2450", "transport": tr.name, "mode": mode})
    parser = ld2450.ReportParser()
    frames = 0
    present_acc = 0
    last_status = time.time()
    while True:
        chunk = tr.read(256)
        if chunk == b"" and isinstance(tr, ReplayTransport):
            break                   # EOF on a capture
        for ts in parser.feed(chunk):
            n = sum(1 for t in ts if t["present"])
            present_acc += n
            frames += 1
            out = [dict(slot=i, **t) for i, t in enumerate(ts)]
            emit({"t": "targets", "ts": round(time.time(), 3), "n": n, "targets": out})
        now = time.time()
        if now - last_status >= status_period:
            dt = now - last_status
            emit({"t": "status", "fps": round(frames / dt, 1) if dt else 0,
                  "frames": frames, "present": present_acc})
            frames = 0
            present_acc = 0
            last_status = now


def main():
    ap = argparse.ArgumentParser(description="HLK-LD2450 reader -> JSON")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--port", help="serial device, e.g. /dev/ttyUSB0")
    g.add_argument("--tcp", help="host:port of a UART tunnel")
    g.add_argument("--replay", help="raw-UART capture file to replay")
    ap.add_argument("--baud", type=int, default=BAUD)
    ap.add_argument("--rate", type=float, default=0.0,
                    help="replay throttle in bytes/s (0 = max)")
    ap.add_argument("--mode", choices=["single", "multi"],
                    help="set tracking mode before streaming")
    ap.add_argument("--fw", action="store_true", help="read firmware version first")
    args = ap.parse_args()

    if args.port:
        tr = SerialTransport(args.port, args.baud)
    elif args.tcp:
        tr = TcpTransport(args.tcp)
    else:
        tr = ReplayTransport(args.replay, rate=args.rate)
    try:
        run(tr, mode=args.mode, read_fw=args.fw)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        emit({"t": "error", "msg": repr(e)})
    finally:
        tr.close()


if __name__ == "__main__":
    main()
