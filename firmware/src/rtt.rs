//! SEGGER-RTT-style target-RAM ring for lossless radar-frame exfil, plus a
//! host->device command mailbox — both drained/driven over SWD by the host
//! bridge (see docs/re/07-streaming.md).
//!
//! Why a ring (not snapshotting FRAME_BUF): the SoC's DMA overwrites the
//! 200-byte FRAME_BUF circularly at ~50 Hz. A host that snapshots it races the
//! DMA and silently drops/duplicates frames. Instead the firmware pushes each
//! *new* frame into this ring (`up_wr` advances); the host reads `up_wr`, copies
//! the records it hasn't seen, and advances `up_rd` over SWD — lossless, and
//! decoupled from the host's (DERP-jittery) poll cadence.
//!
//! Regime tagging: every record carries the current `epoch`. Any config change
//! (a `WRITE_REG` RPC) or an explicit `FLUSH`/`BUMP_EPOCH` increments `epoch`,
//! so the host can label every frame with its regime and never mix samples
//! across a live FMCW tuning change.
//!
//! Layout (all multi-byte fields little-endian; GD32 is LE). The host finds the
//! block either by the ELF symbol `RADAR_RTT` or by scanning SRAM for `id`.

use core::ptr::{addr_of, addr_of_mut, read_volatile, write_volatile};
use core::sync::atomic::{compiler_fence, Ordering};

/// Bytes copied from each SoC frame (header + 43 BE-int16 samples + checksum
/// start): `AA 30 XX 18 80 00 | ref | 7FFF | 43*2 samples | cksum..`.
pub const FRAME_COPY: usize = 96;
/// Per-record size: 4-byte record header + frame bytes.
///   byte 0..2 : epoch u16  (regime id)
///   byte 2    : seq   u8   (low 8 bits of up_wr — gap detection within the ring)
///   byte 3    : carrier u8 (firmware carrier-hop index, 0 when not hopping)
/// Stealing the seq high byte for the carrier tag keeps REC_SIZE and every host
/// offset stable; 8 bits of seq is ample to detect drops in a 32-deep ring.
pub const REC_HDR: usize = 4;
pub const REC_SIZE: usize = REC_HDR + FRAME_COPY; // 100
/// Ring depth. 32 * 100 B = 3200 B of SRAM; ~640 ms of buffer at 50 Hz, which
/// absorbs DERP poll jitter without overrunning. (8 KB SRAM total — fits with
/// FRAME_BUF + stack.)
pub const RING_RECS: usize = 32;

/// Max carriers in the firmware-side hop comb (sizes `hop_carriers`).
pub const HOP_MAX: usize = 8;

/// S3KM111L VCO fine-tune register for carrier hopping: I2C dev 0x20, reg 0x70.
/// (Characterised on HW: 3583 Hz/count, locks on a discrete comb — see
/// docs/re and the host `swd_agent.py` calibration constants.)
pub const HOP_DEV: u8 = 0x20;
pub const HOP_REG: u8 = 0x70;

#[repr(C)]
pub struct RadarRtt {
    /// Magic for host discovery: "RADAR_RTT_v1\0\0\0\0".
    pub id: [u8; 16],
    pub rec_size: u32,  // REC_SIZE
    pub rec_count: u32, // RING_RECS
    pub up_wr: u32,     // firmware-incremented monotonic record index
    pub up_rd: u32,     // host-incremented (firmware never writes it)
    pub epoch: u32,     // current regime id (>=1)
    pub cmd_seq: u32,   // host increments to submit a command
    pub cmd_ack: u32,   // firmware sets == cmd_seq when the command is done
    pub cmd_op: u32,    // opcode (see OP_*)
    pub cmd_a0: u32,    // arg0
    pub cmd_a1: u32,    // arg1
    pub cmd_status: u32, // 0 = ok, 0xffffffff = bad opcode
    /// Host-settable stream decimation: the firmware pushes 1 of every N
    /// distinct SoC frames (N>=1). The SoC streams ~1 kHz; the SWD drain can't
    /// keep up losslessly, so the host throttles here (e.g. N=20 -> ~50 Hz).
    /// Written directly by the host over SWD (no RPC needed).
    pub stream_decim: u32,
    // --- firmware-side carrier hopping (all host-written over SWD) ------------
    /// 1 = the firmware itself cycles the VCO carrier (reg 0x70) across the comb
    /// below and tags each pushed record with the active carrier index, so the
    /// host gets stepped-frequency data from the *normal-rate* stream with zero
    /// extra SWD traffic (no host carrier writes, no 5x read rate). 0 = off.
    pub hop_enable: u32,
    /// Number of active comb entries (clamped to 1..=HOP_MAX).
    pub hop_n: u32,
    /// Frames to discard after each carrier change (PLL relock settle; 1 is
    /// enough — relock is < 1 frame on HW).
    pub hop_settle: u32,
    /// Frames to capture (push, tagged) per carrier before advancing.
    pub hop_dwell: u32,
    /// The carrier comb: `hop_carriers[0..hop_n]` are 0x70 codes (u16 range).
    pub hop_carriers: [u32; HOP_MAX],
    pub buf: [u8; RING_RECS * REC_SIZE],
}

/// Offset of `buf` within the control block (host ring base = BASE + this).
/// id(16) + 12 u32 fields (0x40) + 4 hop scalars (0x10) + HOP_MAX u32 comb.
pub const BUF_OFFSET: usize = 16 + 12 * 4 + 4 * 4 + HOP_MAX * 4; // = 0x70

// Command opcodes (host writes cmd_op).
pub const OP_NOP: u32 = 0;
/// Write an S3KM111L register over I2C. a0 = (dev8 << 8) | reg8 (dev 0 => DEV0);
/// a1 = 16-bit value. Bumps epoch (new regime).
pub const OP_WRITE_REG: u32 = 1;
/// Force a regime boundary with no register change (bumps epoch).
pub const OP_FLUSH: u32 = 2;
pub const OP_BUMP_EPOCH: u32 = 3;

#[no_mangle]
pub static mut RADAR_RTT: RadarRtt = RadarRtt {
    // "v2" = this build carries the hop_* fields + per-record carrier tag, so
    // the host knows `buf` is at 0x70 (not 0x40) and firmware hopping exists.
    id: *b"RADAR_RTT_v2\0\0\0\0",
    rec_size: REC_SIZE as u32,
    rec_count: RING_RECS as u32,
    up_wr: 0,
    up_rd: 0,
    epoch: 1,
    cmd_seq: 0,
    cmd_ack: 0,
    cmd_op: 0,
    cmd_a0: 0,
    cmd_a1: 0,
    cmd_status: 0,
    stream_decim: 20,
    hop_enable: 0,
    hop_n: 4,
    hop_settle: 1,
    hop_dwell: 2,
    // Four well-separated locks from the calibrated comb (host may overwrite).
    hop_carriers: [12000, 13000, 14000, 15000, 0, 0, 0, 0],
    buf: [0; RING_RECS * REC_SIZE],
};

/// Carrier-hop config snapshot read from the control block (host-written).
#[inline]
pub unsafe fn hop_enabled(p: *mut RadarRtt) -> bool {
    read_volatile(addr_of!((*p).hop_enable)) != 0
}
#[inline]
pub unsafe fn hop_n(p: *mut RadarRtt) -> usize {
    (read_volatile(addr_of!((*p).hop_n)) as usize).clamp(1, HOP_MAX)
}
#[inline]
pub unsafe fn hop_settle(p: *mut RadarRtt) -> u32 {
    read_volatile(addr_of!((*p).hop_settle))
}
#[inline]
pub unsafe fn hop_dwell(p: *mut RadarRtt) -> u32 {
    read_volatile(addr_of!((*p).hop_dwell)).max(1)
}
/// Carrier code for comb index `i` (wrapped into the valid range).
#[inline]
pub unsafe fn hop_carrier(p: *mut RadarRtt, i: usize) -> u16 {
    let n = hop_n(p);
    let idx = if n == 0 { 0 } else { i % n };
    read_volatile(addr_of!((*p).hop_carriers[idx])) as u16
}

/// Read the host-set decimation (clamped to >= 1).
#[inline]
pub unsafe fn decim(p: *mut RadarRtt) -> u32 {
    read_volatile(addr_of!((*p).stream_decim)).max(1)
}

/// Pointer to the control block (stable; `RADAR_RTT` is a fixed static).
#[inline]
pub fn ptr() -> *mut RadarRtt {
    addr_of_mut!(RADAR_RTT)
}

/// Push one frame into the up-ring, stamped with the current epoch, an 8-bit
/// sequence (low byte of `up_wr`) and the `carrier` hop index (0 when not
/// hopping). `up_wr` is advanced last (release ordering) so the host never
/// reads a half-written record.
///
/// # Safety
/// Single producer (the main loop). The host only *reads* `buf`/`up_wr` and
/// writes `up_rd`, so there is no producer/producer race.
pub unsafe fn push(p: *mut RadarRtt, frame: &[u8; FRAME_COPY], carrier: u8) {
    let up_wr = read_volatile(addr_of!((*p).up_wr));
    let epoch = read_volatile(addr_of!((*p).epoch));
    let slot = (up_wr as usize) % RING_RECS;
    let base = slot * REC_SIZE;
    let bufp = addr_of_mut!((*p).buf) as *mut u8;
    write_volatile(bufp.add(base), epoch as u8);
    write_volatile(bufp.add(base + 1), (epoch >> 8) as u8);
    write_volatile(bufp.add(base + 2), up_wr as u8); // seq (low 8 bits)
    write_volatile(bufp.add(base + 3), carrier); // carrier-hop index (0 = none)
    let mut k = 0;
    while k < FRAME_COPY {
        write_volatile(bufp.add(base + REC_HDR + k), frame[k]);
        k += 1;
    }
    compiler_fence(Ordering::Release);
    write_volatile(addr_of_mut!((*p).up_wr), up_wr.wrapping_add(1));
}

/// Service the command mailbox: if `cmd_seq != cmd_ack`, execute the op, set
/// `cmd_status`, bump `epoch` on config changes, then ack. Called every loop.
///
/// # Safety
/// Issues I2C transactions (shared bus); only the main loop calls it.
pub unsafe fn poll_cmd(p: *mut RadarRtt) {
    let seq = read_volatile(addr_of!((*p).cmd_seq));
    let ack = read_volatile(addr_of!((*p).cmd_ack));
    if seq == ack {
        return;
    }
    let op = read_volatile(addr_of!((*p).cmd_op));
    let a0 = read_volatile(addr_of!((*p).cmd_a0));
    let a1 = read_volatile(addr_of!((*p).cmd_a1));
    let mut status: u32 = 0;
    let mut bump = false;
    match op {
        OP_WRITE_REG => {
            let dev = ((a0 >> 8) & 0xff) as u8;
            let dev = if dev == 0 { crate::s3km111l::DEV0 } else { dev };
            let reg = (a0 & 0xff) as u8;
            crate::i2c::write_reg(dev, reg, a1 as u16);
            bump = true;
        }
        OP_FLUSH | OP_BUMP_EPOCH => bump = true,
        OP_NOP => {}
        _ => status = 0xffff_ffff,
    }
    if bump {
        let ep = read_volatile(addr_of!((*p).epoch));
        write_volatile(addr_of_mut!((*p).epoch), ep.wrapping_add(1));
    }
    write_volatile(addr_of_mut!((*p).cmd_status), status);
    compiler_fence(Ordering::Release);
    write_volatile(addr_of_mut!((*p).cmd_ack), seq);
}
