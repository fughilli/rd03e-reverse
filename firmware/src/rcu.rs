//! Clock / peripheral-enable bring-up.
//!
//! The vendor firmware encodes an RCU peripheral as `(reg_off << 6) | bit` and
//! sets that bit in the RCU register at `reg_off` (decompiled `FUN_08008170`).
//! We keep the same idea but spell the registers out for clarity.
//!
//! System clock (PLL) setup lives in the vendor's `FUN_08005984` (RCU
//! CTL/CFG0/CFG1/CTL1). This skeleton runs from the default clock; wiring the
//! PLL for the real 72 MHz SYSCLK is a TODO once the CFG word is decoded.

use crate::pac::*;

/// Enable the peripheral clocks the RE'd firmware uses.
pub fn init_clocks() {
    // AHB: GPIOA (bit 17), GPIOB (bit 18), DMA (bit 0).
    modify(RCU + RCU_AHBEN, 0, (1 << 17) | (1 << 18) | (1 << 0));
    // APB2: SPI0 (bit 12).
    modify(RCU + RCU_APB2EN, 0, 1 << 12);
    // APB1: I2C0 (bit 21), USART1 (bit 17), TIMER2 (bit 1).
    modify(RCU + RCU_APB1EN, 0, (1 << 21) | (1 << 17) | (1 << 1));

    // TODO: configure IRC8M/HXTAL -> PLL -> 72 MHz SYSCLK (vendor FUN_08005984,
    // RCU_CFG0/CFG1/CTL1). Peripheral clock dividers feed the I2C/SPI/USART
    // timing constants below, so this must land before trusting those.
}
