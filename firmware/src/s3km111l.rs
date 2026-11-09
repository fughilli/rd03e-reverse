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

/// TODO: the real boot register set. The vendor builds this in SRAM at
/// `0x20000010` from flash defaults + calibration, so it is not a single static
/// array in the image — recover it by locating the flash initializer (the
/// entry layout, device addr 0x40, and `val == 0` terminator are known). Until
/// then this is empty and the radar stays in its power-on state.
pub const BOOT_TABLE: &[RegVal] = &[
    // RegVal { reg: 0x??, val: 0x???? },
];

/// Bring the radar to a known state. Fill `BOOT_TABLE` (or build it from a
/// calibration routine) to make this real.
pub fn init(dual: bool) {
    apply_table_all(BOOT_TABLE, dual);
}
