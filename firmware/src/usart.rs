//! USART1 host UART — the J2 header (OT1 = TX on PA2, RX on PA3), where the
//! module reports results and accepts commands.
//!
//! GD32E230 uses the modern (STM32F0-style) USART register layout, NOT the old
//! GD32F1 layout the first draft assumed. Verified by reading the running vendor
//! firmware over SWD: CTL0@0x00 = 0x2d (UEN|REN|TEN), BAUD@0x0C = 0x119 (281) ->
//! 72 MHz / 281 = 256000 baud, STAT@0x1C, TDATA@0x28.

use crate::pac::*;

// GD32E230 (F0-style) USART register offsets.
const USART_CTL0: u32 = 0x00;
const USART_BAUD: u32 = 0x0C;
const USART_STAT: u32 = 0x1C;
const USART_TDATA: u32 = 0x28;

// CTL0 bits.
const UEN: u32 = 1 << 0;
const REN: u32 = 1 << 2;
const TEN: u32 = 1 << 3;
// STAT bits.
const TBE: u32 = 1 << 7; // TDATA register empty (TXE)

/// Bring up USART1 as 256000 8N1 (BAUD divisor read from the vendor: 281).
pub fn init() {
    wr(USART1 + USART_BAUD, 281); // 72 MHz / 281 = 256000
    wr(USART1 + USART_CTL0, UEN | TEN | REN);
}

pub fn write_byte(b: u8) {
    while rd(USART1 + USART_STAT) & TBE == 0 {}
    wr(USART1 + USART_TDATA, b as u32);
}

pub fn write(bytes: &[u8]) {
    for &b in bytes {
        write_byte(b);
    }
}
