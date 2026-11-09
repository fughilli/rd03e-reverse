//! S3KM111L radar-SoC driver — the RE'd control model (`docs/re/01-s3km111l.md`).
//!
//! The vendor configures the radar by replaying tables of `{reg, value}` pairs
//! over I2C0 (vendor `FUN_08004cb8` boot table, `FUN_08001300` per-mode tables).
//! This module is the from-source equivalent: the primitives are confirmed; the
//! concrete register **values** are the remaining extraction (see the TODO on
//! `BOOT_TABLE`), which is where a new radar-algorithm firmware starts.

use crate::i2c;

/// Primary radar device (7-bit I2C address). A second SoC at `DEV1` is driven
/// in lock-step on dual-array boards.
pub const DEV0: u8 = 0x40;
pub const DEV1: u8 = 0x44;

/// One register-table entry (vendor layout is `[reg:u8][pad:u8][val:u16]`).
#[derive(Clone, Copy)]
pub struct RegVal {
    pub reg: u8,
    pub val: u16,
}

#[inline]
pub fn write_reg(dev: u8, reg: u8, val: u16) {
    i2c::write_reg(dev, reg, val);
}

#[inline]
pub fn read_reg(dev: u8, reg: u8) -> u16 {
    i2c::read_reg(dev, reg)
}

/// Replay a register table to one device (vendor `FUN_08004cb8` shape).
pub fn apply_table(dev: u8, table: &[RegVal]) {
    for e in table {
        write_reg(dev, e.reg, e.val);
    }
}

/// Replay to the primary (and secondary, if present) — vendor `FUN_08001300`.
pub fn apply_table_all(table: &[RegVal], dual: bool) {
    apply_table(DEV0, table);
    if dual {
        apply_table(DEV1, table);
    }
}

/// The S3KM111L boot register sequence, extracted from build 2268 (precision
/// ranging): the vendor's compressed `.data` at flash `0x08009838` unpacks to
/// SRAM `0x20000000`, and the table at `+0x10` is replayed to device 0x40 by
/// `FUN_08004cb8`. Recovered with `tools/unpack_data.py`; the full map and the
/// 2268-vs-2269 (ranging-vs-gesture) diff are in `docs/re/regmap/boot-2268.csv`
/// and `docs/re/01-s3km111l.md`. `reg 0x40` is written first (`0x4207`, config)
/// and last (`0x0207`, run) — the config-enable/start control register.
pub const BOOT_TABLE: &[RegVal] = &[
    RegVal { reg: 0x40, val: 0x4207 }, // control: config-enable set
    RegVal { reg: 0x41, val: 0x0004 },
    RegVal { reg: 0x09, val: 0xe901 },
    RegVal { reg: 0x01, val: 0x0000 },
    RegVal { reg: 0x67, val: 0x0000 },
    RegVal { reg: 0x72, val: 0x0650 },
    RegVal { reg: 0x42, val: 0x0001 },
    RegVal { reg: 0x43, val: 0xd4c0 }, // * mode-specific (ranging)
    RegVal { reg: 0x44, val: 0x0020 },
    RegVal { reg: 0x45, val: 0x0000 },
    RegVal { reg: 0x46, val: 0x7530 }, // 30000
    RegVal { reg: 0x47, val: 0x1000 },
    RegVal { reg: 0x48, val: 0x5078 }, // * mode-specific (ranging)
    RegVal { reg: 0x49, val: 0x2000 },
    RegVal { reg: 0x4a, val: 0x4e20 }, // 20000
    RegVal { reg: 0x4b, val: 0x0000 },
    RegVal { reg: 0x4c, val: 0xc0f8 }, // * mode-specific (ranging)
    RegVal { reg: 0x4d, val: 0x0000 },
    RegVal { reg: 0x4e, val: 0x0001 },
    RegVal { reg: 0x4f, val: 0x0000 },
    RegVal { reg: 0x50, val: 0x0fa0 }, // 4000
    RegVal { reg: 0x51, val: 0x005d }, // *
    RegVal { reg: 0x52, val: 0xeee0 }, // *
    RegVal { reg: 0x53, val: 0x0a01 },
    RegVal { reg: 0x54, val: 0x999a },
    RegVal { reg: 0x55, val: 0x0000 },
    RegVal { reg: 0x56, val: 0x004c }, // *
    RegVal { reg: 0x57, val: 0xffff },
    RegVal { reg: 0x58, val: 0xffb2 }, // *
    RegVal { reg: 0x59, val: 0x0000 },
    RegVal { reg: 0x5a, val: 0x0000 },
    RegVal { reg: 0x5b, val: 0x0022 },
    RegVal { reg: 0x5c, val: 0x0022 },
    RegVal { reg: 0x5d, val: 0x1919 },
    RegVal { reg: 0x5e, val: 0xff00 },
    RegVal { reg: 0x61, val: 0x0021 }, // RX channel 0 gain
    RegVal { reg: 0x62, val: 0x0021 }, // RX channel 1 gain
    RegVal { reg: 0x63, val: 0x0021 }, // RX channel 2 gain
    RegVal { reg: 0x64, val: 0x0021 }, // RX channel 3 gain
    RegVal { reg: 0x6e, val: 0x83fc },
    RegVal { reg: 0x66, val: 0x0a00 },
    RegVal { reg: 0x6c, val: 0x9990 },
    RegVal { reg: 0x6d, val: 0x9580 },
    RegVal { reg: 0x70, val: 0x2ea0 }, // * mode-specific (ranging)
    RegVal { reg: 0x76, val: 0x0021 },
    RegVal { reg: 0x02, val: 0x10f7 }, // *
    RegVal { reg: 0x04, val: 0x020c },
    RegVal { reg: 0x09, val: 0x6901 },
    RegVal { reg: 0x0a, val: 0x4200 }, // *
    RegVal { reg: 0x0b, val: 0xc0f7 }, // *
    RegVal { reg: 0x05, val: 0x0017 }, // *
    RegVal { reg: 0x06, val: 0x0122 },
    RegVal { reg: 0x07, val: 0x01b2 }, // ranging-only
    RegVal { reg: 0x08, val: 0x001c }, // ranging-only
    RegVal { reg: 0x0d, val: 0x2000 },
    RegVal { reg: 0x0e, val: 0x2000 },
    RegVal { reg: 0x14, val: 0x5a03 },
    RegVal { reg: 0x15, val: 0x1708 },
    RegVal { reg: 0x17, val: 0x0210 },
    // 0x20..0x2f: range-dependent threshold/STC curve (shared with gesture).
    RegVal { reg: 0x20, val: 0x0000 },
    RegVal { reg: 0x21, val: 0x0000 },
    RegVal { reg: 0x22, val: 0x0000 },
    RegVal { reg: 0x23, val: 0x1ddc },
    RegVal { reg: 0x24, val: 0x1d5e },
    RegVal { reg: 0x25, val: 0x1ce2 },
    RegVal { reg: 0x26, val: 0x1c64 },
    RegVal { reg: 0x27, val: 0x17d0 },
    RegVal { reg: 0x28, val: 0x16d4 },
    RegVal { reg: 0x29, val: 0x15dc },
    RegVal { reg: 0x2a, val: 0x15dc },
    RegVal { reg: 0x2b, val: 0x15dc },
    RegVal { reg: 0x2c, val: 0x15dc },
    RegVal { reg: 0x2d, val: 0x15dc },
    RegVal { reg: 0x2e, val: 0x15dc },
    RegVal { reg: 0x2f, val: 0x15dc },
    RegVal { reg: 0x72, val: 0x0653 },
    RegVal { reg: 0x67, val: 0x1840 },
    RegVal { reg: 0x01, val: 0x8e24 },
    RegVal { reg: 0x41, val: 0xc844 },
    RegVal { reg: 0x40, val: 0x0207 }, // control: config-enable cleared -> run
];

/// Bring the radar to a known state. Fill `BOOT_TABLE` (or build it from a
/// calibration routine) to make this real.
pub fn init(dual: bool) {
    apply_table_all(BOOT_TABLE, dual);
}

/// Result/status registers the host polls each loop iteration (vendor
/// `FUN_08004b7c`/`FUN_08004c1c`/`FUN_08004c36`/`FUN_08004c60`). The SoC runs
/// the radar DSP internally and exposes outputs here; the register *addresses*
/// are confirmed from the RE, the exact field semantics (distance encoding,
/// target count) are the remaining interpretation — see docs/re/03-architecture.md.
pub mod result {
    use super::{read_reg, DEV0};

    /// Status / mode register (vendor reads reg 0x01 in the loop).
    pub fn status() -> u16 {
        read_reg(DEV0, 0x01)
    }
    /// Result register 0x04 (vendor FUN_08004c60/FUN_08004bfc).
    pub fn reg04() -> u16 {
        read_reg(DEV0, 0x04)
    }
    /// Result register 0x05 (vendor FUN_08004c1c).
    pub fn reg05() -> u16 {
        read_reg(DEV0, 0x05)
    }
    /// Result register 0x44 (vendor FUN_08004c36).
    pub fn reg44() -> u16 {
        read_reg(DEV0, 0x44)
    }
}
