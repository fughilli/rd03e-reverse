#!/usr/bin/env python3
"""Rd-03E SWD streaming agent — runs INSIDE the HITL reservation on rig-radar.

Owns the RP2350 CMSIS-DAP probe via pyocd's Python API and bridges the GD32
RADAR_RTT control block to a line-delimited JSON protocol on stdin/stdout. The
container-side `bridge_server.py` spawns this over ssh and relays to browsers.

Robustness: the SWD link (parasitically-powered board + CMSIS-DAP over USB)
periodically hangs. So every SWD cycle runs in a worker thread with a timeout;
a stall triggers a fast in-process reconnect (abandoning the hung worker) rather
than a full process respawn. Reads are always SMALL and bounded (at most
MAX_READ records per cycle; a backlog is dropped to the newest frames) so a
large block read never becomes the thing that hangs.

Protocol (newline-delimited JSON; frame bytes hex):
  agent -> bridge: ready / frames / status / cmdresult / log / fatal
  bridge -> agent: cmd / reg_write / flush / decim / ping
"""
import sys
import site
import functools

# --- nix pyocd site-packages (from the hitl-swd wrapper) ---------------------
_PYOCD_SITES = [
    "/nix/store/sm1fsi9r9py6f4y3sy0r2c191l1bw9xz-python3.12-pyocd-0.36.0/lib/python3.12/site-packages",
    "/nix/store/fk1z5nbqpalwfrljzsaxzrmhprrrw2qx-python3.12-capstone-5.0.6/lib/python3.12/site-packages",
    "/nix/store/cym2ydrg1ii95snb7d25ngibldm8q6pw-python3.12-setuptools-78.1.1/lib/python3.12/site-packages",
    "/nix/store/rv5bdmgylrb5wfjsqbs3svgj961n43h4-python3.12-cmsis-pack-manager-0.5.2/lib/python3.12/site-packages",
    "/nix/store/kj7jlx7xcmsa38iks0fi2iwp6ac52ag5-python3.12-appdirs-1.4.4/lib/python3.12/site-packages",
    "/nix/store/6k7gpp58wk7fbmc6y2wqss8wa3pvrmk2-python3.12-pyyaml-6.0.2/lib/python3.12/site-packages",
    "/nix/store/l7w3xda98shvc2zrxzd25q10j06hx3p4-python3.12-colorama-0.4.6/lib/python3.12/site-packages",
    "/nix/store/80a1vj90rchlnwr9vdyr3ckikbr007id-python3.12-importlib-metadata-8.6.1/lib/python3.12/site-packages",
    "/nix/store/n0mzcq2jhk3qyai0i46sdrk6mqvpkpsi-python3.12-toml-0.10.2/lib/python3.12/site-packages",
    "/nix/store/4h9l87415cnhl9l05x1wvl8a1i6fb62x-python3.12-zipp-3.21.0/lib/python3.12/site-packages",
    "/nix/store/341clli96km86virkg1gb2873kg5ddly-python3.12-importlib-resources-6.5.2/lib/python3.12/site-packages",
    "/nix/store/zn74q7kfivdbs9xj31r778jlp609h7w3-python3.12-intelhex-2.3.0/lib/python3.12/site-packages",
    "/nix/store/3422k81rlx3qyg5qf8da120fr56psszw-python3.12-intervaltree-3.1.0/lib/python3.12/site-packages",
    "/nix/store/ar9cjwrnkyfq3b86mdvncg3ivq8dg0gf-python3.12-sortedcontainers-2.4.0/lib/python3.12/site-packages",
    "/nix/store/dpcf4gvl17vwc0lcnd64bjvy3ccvxlwl-python3.12-lark-1.2.2/lib/python3.12/site-packages",
    "/nix/store/zslpiiv9hzk7rp01vczd23r7vz0cq0w3-python3.12-regex-2024.11.6/lib/python3.12/site-packages",
    "/nix/store/nx90h0j5n3x48y86abnvylh2as7vhxpc-python3.12-natsort-8.4.0/lib/python3.12/site-packages",
    "/nix/store/mz8vl0dvl5hkv5d83g38d637amdq69hk-python3.12-fastnumbers-5.1.0/lib/python3.12/site-packages",
    "/nix/store/nbnppjiavifw1xjb6n0gr9yln415iz3s-python3.12-typing-extensions-4.13.2/lib/python3.12/site-packages",
    "/nix/store/b9a6ax40v8csmxlm5891bx829ymd3akc-python3.12-pyicu-2.15/lib/python3.12/site-packages",
    "/nix/store/cybycx62c3w774xflhvfihjdar31a57l-python3.12-prettytable-3.16.0/lib/python3.12/site-packages",
    "/nix/store/7x1g2lw0g46mdg688sm0lkpzz9vspirq-python3.12-wcwidth-0.2.13/lib/python3.12/site-packages",
    "/nix/store/g0ldngcyc7rsw19rf63l34vhg8fx78i8-python3.12-pyelftools-0.32/lib/python3.12/site-packages",
    "/nix/store/lg4a10lcax36ikbn5v4qa10hmg0h7jp3-python3.12-pylink-square-1.4.0/lib/python3.12/site-packages",
    "/nix/store/nryb87aj77mid3951gcgh9irczrffxjd-python3.12-psutil-7.0.0/lib/python3.12/site-packages",
    "/nix/store/pmg78n047rahrk97z0dz5k9ks43h2vd3-python3.12-six-1.17.0/lib/python3.12/site-packages",
    "/nix/store/m4nhjkjf389r0ib7l47im2265ch1xji5-python3.12-pyusb-1.3.1/lib/python3.12/site-packages",
]
functools.reduce(lambda k, p: site.addsitedir(p, k), _PYOCD_SITES, site._init_pathinfo())

import os
import json
import time
import select
import logging
import threading
import concurrent.futures as cf

logging.basicConfig(stream=sys.stderr, level=logging.ERROR)
from pyocd.core.helpers import ConnectHelper  # noqa: E402

PACK = "/nix/store/vb9c40fc6wqyskrjlq1c9lm51qw8iyc4-GigaDevice.GD32E23x_DFP.1.0.2.pack"
UID = os.environ.get("RADAR_PROBE_UID", "ECD6FE919F95A523")
TARGET = "gd32e230k8"
FREQ = int(os.environ.get("RADAR_SWD_HZ", "2000000"))

BASE = 0x20000000
O_REC_SIZE, O_REC_COUNT = 0x10, 0x14
O_UP_WR, O_UP_RD, O_EPOCH = 0x18, 0x1C, 0x20
O_CMD_SEQ, O_CMD_ACK, O_CMD_OP, O_CMD_A0, O_CMD_A1, O_CMD_STATUS = 0x24, 0x28, 0x2C, 0x30, 0x34, 0x38
O_DECIM = 0x3C
# Firmware carrier-hop config block (RADAR_RTT v2; see firmware/src/rtt.rs). The
# host writes these, then sets O_HOP_EN=1, and the MCU cycles reg 0x70 itself and
# tags each record's byte[3] with the carrier index — stepped-frequency data from
# the normal-rate stream, zero extra SWD traffic.
O_HOP_EN, O_HOP_N, O_HOP_SETTLE, O_HOP_DWELL = 0x40, 0x44, 0x48, 0x4C
O_HOP_CARR = 0x50               # [HOP_MAX] u32 carrier comb
HOP_MAX = 8
BUF_V2, BUF_V1 = 0x70, 0x40     # `buf` offset: v2 has the hop block, v1 doesn't
O_BUF = BUF_V2                  # set from the on-chip id in main()
HOP_CAPABLE = True             # false if attached to an old v1 image
MASK32 = 0xFFFFFFFF

MAX_READ = 6        # cap records read per cycle (bounded small SWD reads)
POLL = 0.05         # ~20 drain cycles/s
CYCLE_TIMEOUT = 3.0 # a cycle taking longer than this = a hung link

# --- stepped-frequency absolute-range calibration (see docs/re) --------------
# Carrier register 0x70 (dev 0x20) is the SoC VCO fine-tune. It locks only at a
# discrete comb of codes; at each lock the beat phase ramps linearly with the
# code. Calibrated against a corner reflector at 3 ranges (0.699/1.283/2.743 m),
# all 3 fell on slope = 1.502e-4 * R with <3 mm residual:
#   R[m] = phase_ramp_slope[rad/count] * CAL_SCALE + CAL_OFFSET
import math  # noqa: E402
import cmath  # noqa: E402
CAL_SCALE = 6658.2          # m per (rad/count)
CAL_OFFSET = 0.0662         # m  (-R_sys; system/cable delay ~ -6.6 cm)
CAL_K_HZ = 3583.1           # Hz per 0x70 count (for reference)
# Coarse range directly from FFT bin (higher bin = closer): R ~ C0 + CK*bin.
# Fit from the 3 reflector points (bin 3/11/14). Used as an independent cross-
# check: a bin whose stepped-freq range disagrees with its coarse range holds
# multiple/ambiguous scatterers and its phase ramp is not trustworthy.
COARSE_C0 = 3.2908
COARSE_CK = -0.1825
COARSE_TOL = 0.40           # m, max |stepped - coarse| to accept a bin
MIN_R2 = 0.85               # phase-ramp linearity floor (consistency is the real gate)
SWEEP_CARRIERS = [12000, 12250, 13000, 13250, 14000, 14250, 15000, 15250]
SWEEP_DEFAULT = 11936       # restore this carrier after a sweep
CARRIER_REG = (0x20, 0x70)  # (i2c dev, reg)
# Fast-track: stay on SWEEP_DEFAULT, integrate the tracked bin's phase at the
# full frame rate (dR = λ·dφ/4π, λ at 24 GHz), and every FT_STRIDE frames dart
# to ONE comb carrier for a single sample. A rolling slope fit over those
# samples refreshes the ABSOLUTE anchor continuously -> no 0.88 s dropout.
FT_CARRIERS = [12000, 13000, 14000, 15000]   # 4 well-separated locks for the slope
# Continuous carrier-hopping hangs this flaky parasitic-power SWD link within a
# few seconds, so DON'T interleave per-frame. Instead track continuously on the
# fixed carrier (rock-stable, 14 µm/frame) and re-anchor the absolute range with
# a brief carrier cycle only every FT_REANCHOR_S. Fine-phase drift over that
# window is < 1 mm, so absolute accuracy is kept. (Truly dropout-free interleave
# needs firmware-side carrier-hopping, which adds zero host SWD load.)
FT_REANCHOR_S = 30.0        # seconds between brief absolute re-anchors
FT_WAIT_TIMEOUT = 0.6       # s to wait for a frame before aborting an excursion
FT_LAMBDA = 299792458.0 / 24.0e9
FT_SCALE = FT_LAMBDA / (4 * math.pi)          # m per rad of fixed-carrier phase
# Firmware carrier-hop ("HW hop") tuning. The MCU hops across HOP_COMB, discards
# HOP_SETTLE frames after each change, pushes HOP_DWELL frames per carrier. The
# host reads the SAME normal-rate stream, groups frames by their carrier tag, and
# re-fits the stepped-freq slope on every captured frame -> continuous absolute
# range with ZERO extra SWD load (this is the dropout-free path the flaky SWD
# link can't do with host-driven hopping). EMA keeps a rolling phasor per carrier.
HOP_COMB = [12000, 13000, 14000, 15000]       # 4 well-separated calibrated locks
HOP_SETTLE = 1                                # frames to skip after a carrier change
HOP_DWELL = 2                                # frames captured per carrier
HOP_EMA = 0.5                                # per-carrier phasor smoothing (0..1)
HOP_FRESH = 1.0                              # s; a carrier older than this is stale
NS = 43                     # real beat samples per frame
SAMP_OFF = 10               # byte offset of first sample inside the 96B frame
NBINS = 22                  # usable one-sided bins (0..21)
_COSB = [[math.cos(-2 * math.pi * k * n / NS) for n in range(NS)] for k in range(NBINS)]
_SINB = [[math.sin(-2 * math.pi * k * n / NS) for n in range(NS)] for k in range(NBINS)]


def frame_phasors(frame96):
    """DC-removed per-bin DFT phasors (list[NBINS] complex) for one AA30 frame."""
    x = []
    for kk in range(NS):
        v = (frame96[SAMP_OFF + 2 * kk] << 8) | frame96[SAMP_OFF + 2 * kk + 1]
        if v >= 32768:
            v -= 65536
        x.append(v)
    mn = sum(x) / NS
    x = [v - mn for v in x]
    return [complex(sum(x[n] * _COSB[k][n] for n in range(NS)),
                    -sum(x[n] * _SINB[k][n] for n in range(NS))) for k in range(NBINS)]


def phase_one(frame96, k):
    """Phase of a single DFT bin (cheap: used in the per-frame fast-track loop)."""
    x = []
    for kk in range(NS):
        v = (frame96[SAMP_OFF + 2 * kk] << 8) | frame96[SAMP_OFF + 2 * kk + 1]
        if v >= 32768:
            v -= 65536
        x.append(v)
    mn = sum(x) / NS
    x = [v - mn for v in x]
    re = sum(x[n] * _COSB[k][n] for n in range(NS))
    im = -sum(x[n] * _SINB[k][n] for n in range(NS))
    return math.atan2(im, re)


def _fit_slope(xs, phs):
    """Least-squares slope of unwrapped phase vs carrier code (rad/count) + R^2."""
    uy = [phs[0]]
    for i in range(1, len(phs)):
        d = phs[i] - uy[-1]
        while d > math.pi:
            d -= 2 * math.pi
        while d < -math.pi:
            d += 2 * math.pi
        uy.append(uy[-1] + d)
    n = len(xs)
    mx = sum(xs) / n
    my = sum(uy) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((xs[i] - mx) * (uy[i] - my) for i in range(n))
    if sxx == 0:
        return 0.0, 0.0
    slope = sxy / sxx
    syy = sum((y - my) ** 2 for y in uy)
    r2 = (sxy * sxy / (sxx * syy)) if syy > 0 else 1.0
    return slope, r2


def fit_targets(carriers, means_list):
    """means_list: one per-bin phasor list per carrier. Returns (all, good, best):
    each bin's stepped-freq range, gated by linearity + coarse-range consistency."""
    allt = []
    for k in range(2, NBINS):
        phs = [cmath.phase(m[k]) for m in means_list]
        slope, r2 = _fit_slope(carriers, phs)
        mag = sum(abs(m[k]) for m in means_list) / len(means_list)
        rng = slope * CAL_SCALE + CAL_OFFSET
        coarse = COARSE_C0 + COARSE_CK * k
        allt.append({"bin": k, "range": round(rng, 3), "coarse": round(coarse, 2),
                     "mag": round(mag), "r2": round(r2, 3),
                     "ok": (r2 > MIN_R2 and 0.05 < rng < 3.5
                            and abs(rng - coarse) < COARSE_TOL and mag > 8000)})
    good = [d for d in allt if d["ok"]]
    good.sort(key=lambda d: d["range"])
    best = max(good, key=lambda d: d["mag"]) if good else None
    return allt, good, best


def emit(obj):
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def log(msg):
    emit({"t": "log", "msg": str(msg)})


def connect():
    s = ConnectHelper.session_with_chosen_probe(
        unique_id=UID,
        options={"target_override": TARGET, "connect_mode": "attach",
                 "pack": PACK, "frequency": FREQ, "logging": {"level": "error"}},
        blocking=False,
    )
    s.open()
    return s, s.target


def main():
    global O_BUF, HOP_CAPABLE
    session, t = connect()
    rec_size = t.read32(BASE + O_REC_SIZE)
    ring = t.read32(BASE + O_REC_COUNT)
    if not (0 < rec_size <= 256 and 0 < ring <= 256):
        log("bad control block (rec=%d ring=%d); RADAR_RTT firmware flashed?" % (rec_size, ring))
        rec_size, ring = 100, 32
    # Detect the firmware layout from the on-chip magic. v2 carries the hop_*
    # block, so `buf` sits at 0x70; v1 has it at 0x40 and no firmware hopping.
    magic = bytes(t.read_memory_block8(BASE, 16)).split(b"\0", 1)[0]
    if magic.endswith(b"_v2"):
        O_BUF, HOP_CAPABLE = BUF_V2, True
    elif magic.startswith(b"RADAR_RTT"):
        O_BUF, HOP_CAPABLE = BUF_V1, False
        log("firmware is %r (pre-hop); reflash for firmware carrier-hop" % magic.decode("latin1"))
    up_rd = t.read32(BASE + O_UP_WR)  # skip stale backlog
    emit({"t": "ready", "base": BASE, "ring": ring, "rec": rec_size,
          "decim": t.read32(BASE + O_DECIM), "hop": HOP_CAPABLE,
          "fw": magic.decode("latin1")})

    prog = [time.time()]

    def watchdog():
        while True:
            time.sleep(1.0)
            if time.time() - prog[0] > 8.0:
                sys.stderr.write("watchdog: stalled >8s; exit for respawn\n")
                sys.stderr.flush()
                os._exit(2)
    threading.Thread(target=watchdog, daemon=True).start()

    def do_cmd(tt, m):
        ty = m.get("t")
        if ty == "decim":
            n = max(1, int(m.get("n", 20)))
            tt.write32(BASE + O_DECIM, n)
            return {"t": "cmdresult", "op": "decim", "status": n}
        if ty == "ping":
            return {"t": "pong"}
        if ty == "reg_write":
            op = 1
            a0 = ((int(m.get("dev", 0)) & 0xFF) << 8) | (int(m.get("reg", 0)) & 0xFF)
            a1 = int(m.get("val", 0)) & 0xFFFF
        elif ty == "flush":
            op, a0, a1 = 2, 0, 0
        elif ty == "cmd":
            op, a0, a1 = int(m.get("op", 0)), int(m.get("a0", 0)), int(m.get("a1", 0))
        else:
            return None
        seq = (tt.read32(BASE + O_CMD_SEQ) + 1) & MASK32
        tt.write32(BASE + O_CMD_OP, op & MASK32)
        tt.write32(BASE + O_CMD_A0, a0 & MASK32)
        tt.write32(BASE + O_CMD_A1, a1 & MASK32)
        tt.write32(BASE + O_CMD_SEQ, seq)
        status = 0xDEAD
        for _ in range(60):
            if tt.read32(BASE + O_CMD_ACK) == seq:
                status = tt.read32(BASE + O_CMD_STATUS)
                break
            time.sleep(0.003)
        return {"t": "cmdresult", "op": op, "status": status}

    def cycle(tt, rd, cmds, want_decim):
        msgs = []
        for m in cmds:
            r = do_cmd(tt, m)
            if r:
                msgs.append(r)
        up_wr = tt.read32(BASE + O_UP_WR)
        epoch = tt.read32(BASE + O_EPOCH)
        avail = (up_wr - rd) & MASK32
        drops = 0
        if avail > MAX_READ:          # bound every read: drop stale backlog
            drops = avail - MAX_READ
            rd = (up_wr - MAX_READ) & MASK32
            avail = MAX_READ
        recs = []
        if avail > 0:
            first = rd % ring
            runs = ([(first, avail)] if first + avail <= ring
                    else [(first, ring - first), (0, avail - (ring - first))])
            for slot0, cnt in runs:
                data = bytes(tt.read_memory_block8(BASE + O_BUF + slot0 * rec_size, cnt * rec_size))
                for j in range(cnt):
                    off = j * rec_size
                    recs.append([data[off] | (data[off + 1] << 8),  # epoch
                                 data[off + 2],                      # seq (u8)
                                 data[off + 4:off + 4 + 96].hex(),
                                 data[off + 3]])                     # carrier idx
            rd = up_wr
            tt.write32(BASE + O_UP_RD, rd)
        decim = tt.read32(BASE + O_DECIM) if want_decim else None
        return {"msgs": msgs, "up_wr": up_wr, "up_rd": rd, "epoch": epoch,
                "drops": drops, "recs": recs, "decim": decim}

    def read_phasors(tt, rd, dur):
        """Drain the ring for `dur`s, return coherent mean phasor per bin."""
        acc = [0j] * NBINS
        nfr = 0
        t0 = time.time()
        while time.time() - t0 < dur:
            w = tt.read32(BASE + O_UP_WR)
            avail = (w - rd) & MASK32
            if avail == 0:
                time.sleep(0.02)
                continue
            if avail > MAX_READ:
                rd = (w - MAX_READ) & MASK32
                avail = MAX_READ
            first = rd % ring
            runs = ([(first, avail)] if first + avail <= ring
                    else [(first, ring - first), (0, avail - (ring - first))])
            for slot0, cnt in runs:
                data = bytes(tt.read_memory_block8(BASE + O_BUF + slot0 * rec_size, cnt * rec_size))
                for j in range(cnt):
                    off = j * rec_size
                    ph = frame_phasors(data[off + 4:off + 4 + 96])
                    for k in range(NBINS):
                        acc[k] += ph[k]
                    nfr += 1
            rd = w
            tt.write32(BASE + O_UP_RD, rd)
            prog[0] = time.time()
        means = [a / nfr for a in acc] if nfr else None
        return means, rd, nfr

    def do_sweep(tt, rd, settle=0.03, dwell=0.06):
        # Measured: the S3KM111L VCO relocks in < 1 frame (~15 ms) after a 0x70
        # write, and fixed-carrier phase is R=1.0 coherent. So 1-frame settle +
        # ~4-frame dwell is plenty; the whole 8-carrier sweep is now ~0.7 s.
        """Stepped-frequency sweep: cycle the carrier comb, fit each bin's phase
        ramp vs carrier, convert slope -> absolute range. Emits {t:absrange}."""
        emit({"t": "sweeping"})
        per = []
        for v in SWEEP_CARRIERS:
            do_cmd(tt, {"t": "reg_write", "dev": CARRIER_REG[0], "reg": CARRIER_REG[1], "val": v})
            time.sleep(settle)
            prog[0] = time.time()
            means, rd, nfr = read_phasors(tt, rd, dwell)
            if means:
                per.append((v, means))
        do_cmd(tt, {"t": "reg_write", "dev": CARRIER_REG[0], "reg": CARRIER_REG[1], "val": SWEEP_DEFAULT})
        rd = tt.read32(BASE + O_UP_WR)   # skip frames captured mid-restore
        if len(per) < 3:
            emit({"t": "absrange", "ok": False, "reason": "only %d carriers locked" % len(per)})
            return rd
        carriers = [v for v, _ in per]
        allt, good, best = fit_targets(carriers, [m for _, m in per])
        emit({"t": "absrange", "ok": True, "best": best,
              "targets": good, "all": allt, "carriers": carriers, "k_hz": CAL_K_HZ})
        return rd

    # --- fast-track: full-rate fixed-carrier range + interleaved anchors -------
    ft = {"on": False, "pinned": None, "bin": None, "need_init": True,
          "cum": 0.0, "prev": 0.0, "phi0": 0.0, "R0": 0.0, "have": False,
          "anchor_t": 0.0, "samples": {}}      # samples[carrier] = (phasors, ts)

    def ft_wait_new(tt, rd):
        t0 = time.time()
        while True:
            w = tt.read32(BASE + O_UP_WR)
            if (w - rd) & MASK32:
                return w
            prog[0] = time.time()          # alive: don't let the watchdog fire while waiting
            if time.time() - t0 > FT_WAIT_TIMEOUT:
                return None                # stream paused: abort gracefully, no hang
            time.sleep(0.001)

    def ft_grab(tt, rd):
        """Wait for a fresh frame; return (phasors|None, epoch, seq, new_rd)."""
        w = ft_wait_new(tt, rd)
        if w is None:
            return None, 0, 0, rd
        slot = (w - 1) % ring
        data = bytes(tt.read_memory_block8(BASE + O_BUF + slot * rec_size, rec_size))
        fr = data[4:4 + 96]
        return frame_phasors(fr), data[0] | (data[1] << 8), data[2] | (data[3] << 8), w

    def ft_set_carrier(tt, c):
        """Fire-and-forget carrier write (no ACK poll): we wait for a fresh frame
        next anyway, so skipping the ~60-read ACK loop cuts SWD traffic a lot."""
        a0 = (CARRIER_REG[0] << 8) | CARRIER_REG[1]
        seq = (tt.read32(BASE + O_CMD_SEQ) + 1) & MASK32
        tt.write32(BASE + O_CMD_OP, 1)
        tt.write32(BASE + O_CMD_A0, a0)
        tt.write32(BASE + O_CMD_A1, c & 0xFFFF)
        tt.write32(BASE + O_CMD_SEQ, seq)

    def ft_sample(tt, rd, c):
        """Dart to comb carrier c, grab one settled sample (skip 1 transition)."""
        ft_set_carrier(tt, c)
        w = ft_wait_new(tt, rd)            # transition frame, discard
        if w is None:
            return rd                      # stream paused; skip this sample
        rd = w
        ph, _e, _s, rd = ft_grab(tt, rd)   # settled
        if ph is not None:
            ft["samples"][c] = (ph, time.time())
        return rd

    def ft_fit():
        now = time.time()
        cs = [c for c in FT_CARRIERS if c in ft["samples"] and now - ft["samples"][c][1] < 3.0]
        if len(cs) < 3:
            return None
        return (cs,) + fit_targets(cs, [ft["samples"][c][0] for c in cs])

    def ft_restore(tt, rd):
        ft_set_carrier(tt, SWEEP_DEFAULT)
        rd = tt.read32(BASE + O_UP_WR)       # resume fixed-carrier from now
        # bridge the integrator across the excursion gap (<λ/2 motion assumed)
        ph, _e, _s, rd = ft_grab(tt, rd)
        if ph is not None and ft["have"] and ft["bin"] is not None:
            p = cmath.phase(ph[ft["bin"]])
            d = p - ft["prev"]
            while d > math.pi:
                d -= 2 * math.pi
            while d < -math.pi:
                d += 2 * math.pi
            ft["cum"] += d
            ft["prev"] = p
        return rd

    def ft_emit_range(anc=False):
        R = ft["R0"] + FT_SCALE * (ft["cum"] - ft["phi0"])
        emit({"t": "range", "r": round(R, 4), "bin": ft["bin"], "ts": time.time(), "anc": anc})

    def ft_init(tt, rd):
        ft["samples"] = {}
        for c in FT_CARRIERS:
            rd = ft_sample(tt, rd, c)
        fit = ft_fit()
        if fit:
            cs, allt, good, best = fit
            emit({"t": "absrange", "ok": True, "best": best, "targets": good,
                  "all": allt, "carriers": cs, "k_hz": CAL_K_HZ})
        else:
            allt, best = [], None
        bin_ = ft["pinned"] if ft["pinned"] is not None else (best["bin"] if best else None)
        d = next((x for x in allt if x["bin"] == bin_), None) if bin_ is not None else None
        ft_set_carrier(tt, SWEEP_DEFAULT)
        rd = tt.read32(BASE + O_UP_WR)       # back on fixed carrier
        if not d or not (0.05 < d["range"] < 3.6):
            emit({"t": "ftstatus", "ok": False, "reason": "no clean target to anchor"})
            ft["have"] = False
            ft["need_init"] = False
            return rd
        ph, _e, _s, rd = ft_grab(tt, rd)    # fixed-carrier phase anchor
        if ph is None:
            ft["have"] = False              # stream paused; retry init next tick
            return rd
        p0 = cmath.phase(ph[bin_])
        ft.update(bin=bin_, prev=p0, cum=p0, phi0=p0, R0=d["range"],
                  have=True, anchor_t=time.time(), need_init=False)
        ft_emit_range(anc=True)
        return rd

    def ft_reanchor():
        fit = ft_fit()
        if not fit:
            return
        cs, allt, good, best = fit
        d = next((x for x in allt if x["bin"] == ft["bin"]), None) if ft["bin"] is not None else None
        if d and 0.05 < d["range"] < 3.6:
            cur = ft["R0"] + FT_SCALE * (ft["cum"] - ft["phi0"])
            err = d["range"] - cur
            if abs(err) > 0.5:               # unwrap error / bin jump -> hard re-anchor
                ft["R0"] = d["range"]
                ft["phi0"] = ft["cum"]
            else:                            # gentle absolute discipline; keep the 14 µm fine motion
                ft["R0"] += 0.2 * err
            ft_emit_range(anc=True)
        emit({"t": "absrange", "ok": True, "best": best, "targets": good,
              "all": allt, "carriers": cs, "k_hz": CAL_K_HZ})

    def ft_step(tt, rd):
        w = tt.read32(BASE + O_UP_WR)
        avail = (w - rd) & MASK32
        if avail == 0:
            time.sleep(0.006)
            return rd
        if avail > MAX_READ:
            rd = (w - MAX_READ) & MASK32
            avail = MAX_READ
        first = rd % ring
        runs = ([(first, avail)] if first + avail <= ring
                else [(first, ring - first), (0, avail - (ring - first))])
        recs = []
        for slot0, cnt in runs:
            data = bytes(tt.read_memory_block8(BASE + O_BUF + slot0 * rec_size, cnt * rec_size))
            for j in range(cnt):
                off = j * rec_size
                fr = data[off + 4:off + 4 + 96]
                recs.append([data[off] | (data[off + 1] << 8), data[off + 2], fr.hex(), data[off + 3]])
                if ft["have"] and ft["bin"] is not None:
                    p = phase_one(fr, ft["bin"])          # integrate EVERY frame (unwrap continuity)
                    d = p - ft["prev"]
                    while d > math.pi:
                        d -= 2 * math.pi
                    while d < -math.pi:
                        d += 2 * math.pi
                    ft["cum"] += d
                    ft["prev"] = p
        rd = w
        tt.write32(BASE + O_UP_RD, rd)
        if recs:
            emit({"t": "frames", "epoch": recs[-1][0], "drops": 0, "recs": recs})
            if ft["have"] and ft["bin"] is not None:
                ft_emit_range()                           # but emit range once per batch (~20 Hz)
        prog[0] = time.time()
        if time.time() - ft["anchor_t"] >= FT_REANCHOR_S:   # brief periodic re-anchor
            for c in FT_CARRIERS:
                rd = ft_sample(tt, rd, c)
            rd = ft_restore(tt, rd)
            ft_reanchor()
            ft["anchor_t"] = time.time()
            prog[0] = time.time()
        return rd

    # --- firmware carrier-hop ("HW hop"): MCU hops 0x70 & tags each record -----
    # We just read the normal-rate stream, group frames by carrier tag, keep a
    # rolling (EMA) phasor per carrier, and re-fit the stepped-freq slope on every
    # captured frame -> continuous absolute range, zero extra SWD load.
    hop = {"on": False, "pinned": None, "bin": None, "ph": {}, "started": False,
           "last_abs": 0.0}        # ph[carrier_idx] = (phasor_list, ts)

    def hop_config(tt):
        n = min(len(HOP_COMB), HOP_MAX)
        for i, c in enumerate(HOP_COMB[:n]):
            tt.write32(BASE + O_HOP_CARR + 4 * i, c & 0xFFFF)
        tt.write32(BASE + O_HOP_N, n)
        tt.write32(BASE + O_HOP_SETTLE, HOP_SETTLE)
        tt.write32(BASE + O_HOP_DWELL, HOP_DWELL)
        tt.write32(BASE + O_HOP_EN, 1)          # engage firmware hopping

    def hop_disable(tt):
        tt.write32(BASE + O_HOP_EN, 0)
        do_cmd(tt, {"t": "reg_write", "dev": CARRIER_REG[0],
                    "reg": CARRIER_REG[1], "val": SWEEP_DEFAULT})

    def hop_fit_emit():
        """Fit across fresh carriers, pick the tracked bin, emit its range.
        Returns (all, good, best, carriers) for a throttled absrange, else None."""
        now = time.time()
        idxs = [i for i in range(len(HOP_COMB))
                if i in hop["ph"] and now - hop["ph"][i][1] < HOP_FRESH]
        if len(idxs) < 3:
            return None
        carriers = [HOP_COMB[i] for i in idxs]
        allt, good, best = fit_targets(carriers, [hop["ph"][i][0] for i in idxs])
        bin_ = (hop["pinned"] if hop["pinned"] is not None
                else hop["bin"] if hop["bin"] is not None
                else best["bin"] if best else None)
        if bin_ is None:
            return allt, good, best, carriers
        hop["bin"] = bin_
        d = next((x for x in allt if x["bin"] == bin_), None)
        if d and 0.05 < d["range"] < 3.6:
            emit({"t": "range", "r": round(d["range"], 4), "bin": bin_,
                  "ts": now, "anc": True, "hw": True})
        return allt, good, best, carriers

    def hop_step(tt, rd):
        w = tt.read32(BASE + O_UP_WR)
        avail = (w - rd) & MASK32
        if avail == 0:
            time.sleep(0.006)
            return rd
        if avail > MAX_READ:
            rd = (w - MAX_READ) & MASK32
            avail = MAX_READ
        first = rd % ring
        runs = ([(first, avail)] if first + avail <= ring
                else [(first, ring - first), (0, avail - (ring - first))])
        recs = []
        updated = False
        for slot0, cnt in runs:
            data = bytes(tt.read_memory_block8(BASE + O_BUF + slot0 * rec_size, cnt * rec_size))
            for j in range(cnt):
                off = j * rec_size
                fr = data[off + 4:off + 4 + 96]
                ci = data[off + 3]
                recs.append([data[off] | (data[off + 1] << 8), data[off + 2], fr.hex(), ci])
                if ci < len(HOP_COMB):
                    ph = frame_phasors(fr)
                    prev = hop["ph"].get(ci)
                    if prev is None:
                        hop["ph"][ci] = (ph, time.time())
                    else:
                        a = HOP_EMA
                        hop["ph"][ci] = ([a * ph[k] + (1 - a) * prev[0][k]
                                          for k in range(NBINS)], time.time())
                    updated = True
        rd = w
        tt.write32(BASE + O_UP_RD, rd)
        if recs:
            emit({"t": "frames", "epoch": recs[-1][0], "drops": 0, "recs": recs})
        if updated:
            res = hop_fit_emit()
            if res and time.time() - hop["last_abs"] >= 0.5:
                allt, good, best, carriers = res
                emit({"t": "absrange", "ok": True, "best": best, "targets": good,
                      "all": allt, "carriers": carriers, "k_hz": CAL_K_HZ})
                hop["last_abs"] = time.time()
        prog[0] = time.time()
        return rd

    # SWD runs in the MAIN thread. An in-process reconnect cannot work: a hung
    # USB transfer keeps the pyusb handle claimed, so a fresh session on the same
    # probe blocks forever. Empirically a fresh *process* always reclaims the
    # probe cleanly, so recovery = exit and let bridge_server respawn us. A real
    # link hang blocks inside pyusb; the watchdog thread (no progress >8s) then
    # os._exit(2)'s the whole process. Any SWD exception exits here. No executor,
    # no timeout-and-abandon, no spurious 3s-timeout churn.
    # Defensive: start every (re)spawn on the known-good fixed carrier, in case a
    # previously crashed fast-track excursion left a bad 0x70.
    try:
        do_cmd(t, {"t": "reg_write", "dev": CARRIER_REG[0], "reg": CARRIER_REG[1], "val": SWEEP_DEFAULT})
    except Exception:
        pass

    last_status = 0.0
    frames_since = 0
    while True:
        cmds = read_cmds()
        if cmds is None:
            break
        for c in cmds:                       # fast-track / HW-hop toggles
            if c.get("t") == "fasttrack":
                if c.get("on"):
                    ft["on"] = True
                    ft["pinned"] = c.get("bin", None)
                    ft["need_init"] = True
                    hop["on"] = False        # mutually exclusive
                else:
                    ft["on"] = False
            elif c.get("t") == "hop":
                if c.get("on") and not HOP_CAPABLE:
                    emit({"t": "ftstatus", "ok": False,
                          "reason": "firmware predates carrier-hop; reflash v2"})
                elif c.get("on"):
                    hop["on"] = True
                    hop["pinned"] = c.get("bin", None)
                    hop["bin"] = None
                    hop["ph"] = {}
                    hop["started"] = False
                    ft["on"] = False         # mutually exclusive
                else:
                    hop["on"] = False
        sweep_req = any(c.get("t") == "sweep" for c in cmds)
        other = [c for c in cmds if c.get("t") not in ("sweep", "fasttrack", "hop")]
        want_decim = (time.time() - last_status) >= 1.0
        try:
            if hop["on"] and HOP_CAPABLE:
                for c in other:              # decim/flush/ping still honoured
                    do_cmd(t, c)
                if not hop["started"]:
                    hop_config(t)
                    up_rd = t.read32(BASE + O_UP_WR)   # skip pre-hop frames
                    hop["started"] = True
                    hop["last_abs"] = 0.0
                    emit({"t": "ftstatus", "ok": True, "hw": True,
                          "reason": "firmware carrier-hop engaged"})
                up_rd = hop_step(t, up_rd)
                now = time.time()
                if want_decim:
                    emit({"t": "status", "up_wr": t.read32(BASE + O_UP_WR), "up_rd": up_rd,
                          "epoch": t.read32(BASE + O_EPOCH), "decim": t.read32(BASE + O_DECIM),
                          "fps": None, "ft": True, "hw": True})
                    last_status = now
                time.sleep(POLL)
                continue
            # leaving HW-hop: turn firmware hopping off and restore the carrier
            if hop["started"] and not hop["on"]:
                hop_disable(t)
                hop["started"] = False
                hop["bin"] = None
                up_rd = t.read32(BASE + O_UP_WR)
            if ft["on"]:
                for c in other:              # decim/flush/ping still honoured
                    do_cmd(t, c)
                up_rd = ft_init(t, up_rd) if ft["need_init"] else ft_step(t, up_rd)
                now = time.time()
                if want_decim:
                    emit({"t": "status", "up_wr": t.read32(BASE + O_UP_WR), "up_rd": up_rd,
                          "epoch": t.read32(BASE + O_EPOCH), "decim": t.read32(BASE + O_DECIM),
                          "fps": None, "ft": True})
                    last_status = now
                time.sleep(POLL)             # read at the normal-streaming SWD rate
                continue
            # leaving fast-track: make sure we're parked on the fixed carrier
            if not ft["on"] and ft["bin"] is not None:
                do_cmd(t, {"t": "reg_write", "dev": CARRIER_REG[0], "reg": CARRIER_REG[1], "val": SWEEP_DEFAULT})
                ft["bin"] = None
            r = cycle(t, up_rd, other, want_decim)
            up_rd = r["up_rd"]
            if sweep_req:
                up_rd = do_sweep(t, up_rd)
        except Exception as e:
            sys.stderr.write("swd error: %r; exit for respawn\n" % e)
            sys.stderr.flush()
            os._exit(4)
        prog[0] = time.time()
        for msg in r["msgs"]:
            emit(msg)
        if r["recs"] or r["drops"]:
            frames_since += len(r["recs"])
            emit({"t": "frames", "epoch": r["epoch"], "drops": r["drops"], "recs": r["recs"]})
        now = time.time()
        if want_decim:
            fps = frames_since / (now - last_status) if last_status else 0
            emit({"t": "status", "up_wr": r["up_wr"], "up_rd": r["up_rd"],
                  "epoch": r["epoch"], "decim": r["decim"], "fps": round(fps, 1)})
            frames_since = 0
            last_status = now
        time.sleep(POLL)

    _safe_close(session)


def _safe_close(s):
    try:
        s.close()
    except Exception:
        pass


def read_cmds():
    """Non-blocking: collect queued stdin command lines; None on EOF."""
    cmds = []
    while select.select([sys.stdin], [], [], 0)[0]:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            continue
        try:
            cmds.append(json.loads(line))
        except Exception as e:
            log("bad cmd json: %s" % e)
    return cmds


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        emit({"t": "fatal", "msg": repr(e)})
        sys.exit(1)
