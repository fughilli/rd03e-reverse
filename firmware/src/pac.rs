//! Minimal GD32E230 register map — only the peripherals recovered by the RE
//! (see `docs/re/02-bus.md`). Deliberately PAC-lite and dependency-free: these
//! are the same raw register addresses the vendor firmware writes, so the
//! bring-up stays a 1:1 mirror of what was reverse-engineered. Swap for the
//! `gd32e23x` PAC later if you want typed fields.

use core::ptr::{read_volatile, write_volatile};

#[inline(always)]
pub fn rd(addr: u32) -> u32 {
    unsafe { read_volatile(addr as *const u32) }
}

#[inline(always)]
pub fn wr(addr: u32, val: u32) {
    unsafe { write_volatile(addr as *mut u32, val) }
}

#[inline(always)]
pub fn modify(addr: u32, clear: u32, set: u32) {
    wr(addr, (rd(addr) & !clear) | set);
}

// Peripheral bases (confirmed via mmio_accesses.csv).
pub const RCU: u32 = 0x4002_1000;
pub const GPIOA: u32 = 0x4800_0000;
pub const GPIOB: u32 = 0x4800_0400;
pub const DMA: u32 = 0x4002_0000;
pub const I2C0: u32 = 0x4000_5400;
pub const SPI0: u32 = 0x4001_3000;
pub const USART1: u32 = 0x4000_4400;
pub const TIMER2: u32 = 0x4000_0400;

// RCU register offsets.
pub const RCU_CTL: u32 = 0x00;
pub const RCU_CFG0: u32 = 0x04;
pub const RCU_APB2EN: u32 = 0x18;
pub const RCU_APB1EN: u32 = 0x1C;
pub const RCU_AHBEN: u32 = 0x14;

// GPIO register offsets.
pub const GPIO_CTL: u32 = 0x00; // 2 bits/pin: 00 in,01 out,10 AF,11 analog
pub const GPIO_OSPD: u32 = 0x08; // 2 bits/pin output speed
pub const GPIO_PUD: u32 = 0x0C; // 2 bits/pin pull up/down
pub const GPIO_AFSEL0: u32 = 0x20; // pins 0..7, 4 bits/pin
pub const GPIO_AFSEL1: u32 = 0x24; // pins 8..15

// I2C register offsets (GD32E23x I2C).
pub const I2C_CTL0: u32 = 0x00;
pub const I2C_CTL1: u32 = 0x04;
pub const I2C_DATA: u32 = 0x10;
pub const I2C_STAT0: u32 = 0x14;
pub const I2C_STAT1: u32 = 0x18;
pub const I2C_CKCFG: u32 = 0x1C;
pub const I2C_RT: u32 = 0x20;

// SPI register offsets.
pub const SPI_CTL0: u32 = 0x00;
pub const SPI_CTL1: u32 = 0x04;
pub const SPI_STAT: u32 = 0x08;
pub const SPI_DATA: u32 = 0x0C;

// DMA register offsets (channel stride 0x14, channel 0 base at +0x08).
pub const DMA_INTF: u32 = 0x00;
pub const DMA_INTC: u32 = 0x04;
pub const DMA_CH0CTL: u32 = 0x08;
pub const DMA_CH0CNT: u32 = 0x0C;
pub const DMA_CH0PADDR: u32 = 0x10;
pub const DMA_CH0MADDR: u32 = 0x14;
