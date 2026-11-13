//! Clock / peripheral-enable bring-up.
//!
//! Values recovered by reading the RUNNING vendor firmware's registers over SWD
//! (not decoded from the obfuscated decomp): the vendor drives IRC8M -> PLL ->
//! 72 MHz SYSCLK with RCU_CTL=0x03008483, RCU_CFG0=0x0804000a, and FMC 2
//! wait-states (FMC_WS=0x32). We replicate that exact register state.

use crate::pac::*;

// RCU_CTL bits.
const IRC8MEN: u32 = 1 << 0;
const IRC8MSTB: u32 = 1 << 1;
const PLLEN: u32 = 1 << 24;
const PLLSTB: u32 = 1 << 25;

/// Configure flash wait-states + PLL for 72 MHz, then enable peripheral clocks.
pub fn init_clocks() {
    // Flash: 2 wait-states + prefetch (vendor FMC_WS=0x32) BEFORE raising clock.
    wr(FMC + FMC_WS, 0x32);

    // IRC8M on + stable (reset default, but make sure).
    modify(RCU + RCU_CTL, 0, IRC8MEN);
    while rd(RCU + RCU_CTL) & IRC8MSTB == 0 {}

    // PLL config without switching sysclk yet: recovered CFG0 (0x0804000a =
    // PLLSEL=IRC8M/2, PLLMF -> x18, AHB/APB prescalers /1) with SCS bits clear.
    wr(RCU + RCU_CFG0, 0x0804_0008);

    // Enable PLL, wait for lock.
    modify(RCU + RCU_CTL, 0, PLLEN);
    while rd(RCU + RCU_CTL) & PLLSTB == 0 {}

    // Switch system clock to PLL (SCS[1:0]=0b10) and confirm (SCSS[3:2]=0b10).
    modify(RCU + RCU_CFG0, 0b11, 0b10);
    while (rd(RCU + RCU_CFG0) >> 2) & 0b11 != 0b10 {}

    // Peripheral clock enables — replicate the vendor's exact enable set read
    // live: AHBEN=0x00060015 (GPIOA/B, DMA, SRAM/FMC), APB1EN=0x00220002
    // (I2C0, USART1, TIMER2), APB2EN=0x00001001 (SPI0, AF/cfg).
    modify(RCU + RCU_AHBEN, 0, 0x0006_0015);
    modify(RCU + RCU_APB1EN, 0, 0x0022_0002);
    modify(RCU + RCU_APB2EN, 0, 0x0000_1001);
}
