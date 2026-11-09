//! USART1 host UART — the J2 header (OT1 = TX, RX), where the module reports
//! results and accepts commands (vendor `FUN_08005c88`/`FUN_080087e4`).
//!
//! Baud is set by the vendor in `FUN_080087e4` (USART_BAUD) from the APB1
//! clock; the Rd-03E ships at 256000 8N1. The divisor below is a placeholder
//! pending the decoded clock tree.

use crate::pac::*;

const USART_STAT: u32 = 0x00;
const USART_DATA: u32 = 0x04;
const USART_BAUD: u32 = 0x08;
const USART_CTL0: u32 = 0x0C;

const UEN: u32 = 1 << 13;
const TEN: u32 = 1 << 3;
const REN: u32 = 1 << 2;
const TBE: u32 = 1 << 7; // STAT: transmit buffer empty

/// Bring up USART1 as 8N1. TODO: real BAUD divisor from the decoded PCLK1.
pub fn init() {
    // BAUD = fck / baudrate. Placeholder for 256000 @ assumed 54 MHz PCLK1.
    wr(USART1 + USART_BAUD, 54_000_000 / 256_000);
    wr(USART1 + USART_CTL0, UEN | TEN | REN);
}

pub fn write_byte(b: u8) {
    while rd(USART1 + USART_STAT) & TBE == 0 {}
    wr(USART1 + USART_DATA, b as u32);
}

pub fn write(bytes: &[u8]) {
    for &b in bytes {
        write_byte(b);
    }
}
