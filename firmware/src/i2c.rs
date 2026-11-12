//! I2C0 master — the S3KM111L control bus (PB6/PB7, 1 MHz).
//!
//! The transaction flow is a direct port of the vendor's `FUN_08004410`
//! (write) and `FUN_0800426c` (read): status-polled START, addr, register
//! pointer, data bytes, STOP. S3KM111L registers are 8-bit addressed with
//! 16-bit big-endian values (`docs/re/01-s3km111l.md`).
//!
//! The clock/timing constants below are placeholders: the vendor computes
//! CKCFG from the APB1 clock and selects Fast-mode-Plus (1 MHz). Verify them
//! against the RE'd `FUN_08007b3c` once the SYSCLK/PLL config is decoded.

use crate::pac::*;

// STAT0 flags.
const SBSEND: u32 = 1 << 0;
const ADDSEND: u32 = 1 << 1;
const BTC: u32 = 1 << 2;
const TBE: u32 = 1 << 7;
const RBNE: u32 = 1 << 6;

// CTL0 bits.
const I2CEN: u32 = 1 << 0;
const START: u32 = 1 << 8;
const STOP: u32 = 1 << 9;
const ACKEN: u32 = 1 << 10;

#[inline]
fn wait_stat0(flag: u32) {
    // Bounded wait: on a bus fault we return rather than hang, so the host loop
    // keeps reporting (with stale/zero values) and stays debuggable over SWD.
    let mut n = 0u32;
    while rd(I2C0 + I2C_STAT0) & flag == 0 {
        n += 1;
        if n > 200_000 {
            break;
        }
    }
}

/// Bring up I2C0 as a master. Timing values read live from the running vendor
/// firmware over SWD: CTL1=0x48 (I2CCLK=72 MHz), CKCFG=0x8018 (Fast mode,
/// CLKC=24), RT=0x09.
pub fn init() {
    wr(I2C0 + I2C_CTL0, 0); // disable while configuring
    wr(I2C0 + I2C_CTL1, 72); // I2CCLK = 72 MHz (peripheral clock)
    wr(I2C0 + I2C_CKCFG, 0x8018); // FAST=1, CLKC=0x18 -> ~1 MHz
    wr(I2C0 + I2C_RT, 0x09);
    modify(I2C0 + I2C_CTL0, 0, I2CEN | ACKEN);
}

fn start(addr7: u8, read: bool) {
    modify(I2C0 + I2C_CTL0, 0, START);
    wait_stat0(SBSEND);
    let byte = ((addr7 as u32) << 1) | (read as u32);
    wr(I2C0 + I2C_DATA, byte);
    wait_stat0(ADDSEND);
    // Clear ADDSEND: read STAT0 then STAT1.
    let _ = rd(I2C0 + I2C_STAT0);
    let _ = rd(I2C0 + I2C_STAT1);
}

fn stop() {
    modify(I2C0 + I2C_CTL0, 0, STOP);
}

fn write_byte(b: u8) {
    wait_stat0(TBE);
    wr(I2C0 + I2C_DATA, b as u32);
}

/// Write a 16-bit radar register (value sent big-endian).
pub fn write_reg(dev: u8, reg: u8, val: u16) {
    start(dev, false);
    write_byte(reg);
    write_byte((val >> 8) as u8); // MSB first
    write_byte(val as u8);
    wait_stat0(BTC);
    stop();
}

/// Read a 16-bit radar register (big-endian on the wire).
pub fn read_reg(dev: u8, reg: u8) -> u16 {
    // Phase 1: write the register pointer.
    start(dev, false);
    write_byte(reg);
    wait_stat0(BTC);
    // Phase 2: repeated-START, read 2 bytes.
    start(dev, true);
    wait_stat0(RBNE);
    let hi = rd(I2C0 + I2C_DATA) as u16;
    // NACK the last byte then STOP.
    modify(I2C0 + I2C_CTL0, ACKEN, 0);
    wait_stat0(RBNE);
    let lo = rd(I2C0 + I2C_DATA) as u16;
    stop();
    modify(I2C0 + I2C_CTL0, 0, ACKEN);
    (hi << 8) | lo
}
