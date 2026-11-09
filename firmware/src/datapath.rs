//! Radar data path — SPI0 + DMA channel 0 (`docs/re/02-bus.md`).
//!
//! The vendor binds DMA CH0's peripheral address to `SPI0_DATA` (0x4001300C)
//! and streams into a RAM buffer (vendor `FUN_08004ecc` sets up GPIO AF, DMA
//! CH0, SPI0, then enables SPI0). This is where raw radar frames land for your
//! own signal processing.
//!
//! Direction is still an open RE question (PA6/MISO is unmuxed in 2268): this
//! configures CH0 as peripheral->memory (receive). Flip `DIR` and the SPI0
//! RX/TX mode once the vendor's CH0CTL/SPI0 CTL0 config words are decoded.

use crate::pac::*;

// SPI0 CTL0 bits.
const SPE: u32 = 1 << 6; // SPI enable
const MSTMOD: u32 = 1 << 2; // master mode
const SPI_CTL1_DMAREN: u32 = 1 << 0; // RX DMA enable (CTL1)

// DMA CH0CTL bits.
const CHEN: u32 = 1 << 0;
const DIR_M2P: u32 = 1 << 4; // 0 = peripheral->memory (RX)
const MNAGA: u32 = 1 << 7; // memory address increment
const CMEN: u32 = 1 << 5; // circular mode

/// Configure SPI0 as master and DMA CH0 to receive SPI0 words into `buf`.
pub fn init_rx(buf: &mut [u16]) {
    // Minimal SPI0 config: master, enable. The vendor's exact CTL0 word
    // (baud/DFF/BIDIMODE) is TODO — see docs/re/02-bus.md.
    wr(SPI0 + SPI_CTL0, MSTMOD);
    modify(SPI0 + SPI_CTL1, 0, SPI_CTL1_DMAREN);

    // DMA CH0: peripheral = SPI0_DATA, memory = buf, count = len, circular RX.
    modify(DMA + DMA_CH0CTL, CHEN, 0); // disable while configuring
    wr(DMA + DMA_CH0PADDR, SPI0 + SPI_DATA);
    wr(DMA + DMA_CH0MADDR, buf.as_mut_ptr() as u32);
    wr(DMA + DMA_CH0CNT, buf.len() as u32);
    modify(DMA + DMA_CH0CTL, DIR_M2P, MNAGA | CMEN); // DIR=0 (periph->mem)
    modify(DMA + DMA_CH0CTL, 0, CHEN);

    modify(SPI0 + SPI_CTL0, 0, SPE);
}
