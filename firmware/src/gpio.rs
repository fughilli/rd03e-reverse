//! GPIO alternate-function mux, matching the pin assignments recovered from the
//! vendor GPIO masks (`docs/re/02-bus.md`):
//!   I2C0  SCL/SDA        = PB6 / PB7
//!   SPI0  NSS/SCK/MOSI   = PA4 / PA5 / PA7   (MISO/PA6 left unmuxed in 2268)

use crate::pac::*;

const MODE_AF: u32 = 0b10;

/// Put `pin` (0..15) of `port` into alternate-function mode `af` (0..15),
/// high speed. Mirrors the vendor's mode+AFSEL pair-writes.
pub fn set_af(port: u32, pin: u32, af: u32) {
    // CTL: 2 bits/pin -> AF.
    modify(port + GPIO_CTL, 0b11 << (pin * 2), MODE_AF << (pin * 2));
    // Output speed: max (0b11).
    modify(port + GPIO_OSPD, 0b11 << (pin * 2), 0b11 << (pin * 2));
    // AFSEL0 for pins 0..7, AFSEL1 for 8..15 (4 bits/pin).
    let (afreg, shift) = if pin < 8 {
        (GPIO_AFSEL0, pin * 4)
    } else {
        (GPIO_AFSEL1, (pin - 8) * 4)
    };
    modify(port + afreg, 0xF << shift, (af & 0xF) << shift);
}

/// Mux the pins the RE'd firmware uses. AF numbers are the GD32E230 defaults
/// for these functions (AF1 for I2C0 on PB6/7, AF0 for SPI0 on PA4/5/7);
/// confirm against the datasheet's AF table.
pub fn init_pins() {
    // I2C0 on GPIOB: PB6 SCL, PB7 SDA (AF1).
    set_af(GPIOB, 6, 1);
    set_af(GPIOB, 7, 1);
    // Open-drain for I2C (OMODE bit = 1).
    modify(GPIOB + 0x04, 0, (1 << 6) | (1 << 7));
    // Pull-ups (PUD = 01) on SCL/SDA.
    modify(GPIOB + GPIO_PUD, 0b11 << (6 * 2) | 0b11 << (7 * 2), 0b01 << (6 * 2) | 0b01 << (7 * 2));

    // SPI0 on GPIOA: PA4 NSS, PA5 SCK, PA7 MOSI (AF0).
    set_af(GPIOA, 4, 0);
    set_af(GPIOA, 5, 0);
    set_af(GPIOA, 7, 0);
    // NOTE: PA6/MISO intentionally left unmuxed, as in build 2268 — see the
    // SPI0/DMA direction open question in docs/re/02-bus.md.
}
