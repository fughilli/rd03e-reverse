//! Radar data path — SPI0 **slave** receive + DMA channel 1, from the SoC.
//!
//! Confirmed by live SWD reads of the running vendor firmware (2026-11-13):
//! the S3KM111L is the SPI **master** and streams framed target data to the
//! GD32, which receives as SPI0 **slave** and DMAs the bytes into a circular
//! SRAM buffer on **DMA channel 1**. Exact live register values:
//!
//!   SPI0 CTL0 = 0x0040  -> SPE only: MSTMOD=0 (slave), mode 0, 8-bit, MSB-first
//!   SPI0 CTL1 = 0x0001  -> DMAREN (RX DMA request enable)
//!   DMA CH1 CTL   = 0x20a7 -> P->M, circular, 8-bit/8-bit, mem-incr, prio high,
//!                             HTF+FTF interrupt enables, CHEN
//!   DMA CH1 PADDR = 0x4001300c (SPI0_DATA)
//!   DMA CH1 MADDR = 0x200013a4 (vendor's buffer; ours is FRAME_BUF)
//!   DMA CH1 CNT   = 200
//!
//! The raw stream looks like `AA 30 F0 18 80 00 80 00 <~92 sample bytes> …`,
//! one ~100-byte frame per `0xAA` sync — the input to the `FUN_0800207c`
//! parser (docs/re/05-calibration.md). NSS is PA4 (AF0), muxed in `gpio.rs`.

use crate::pac::*;

// SPI0 CTL0 / CTL1 / STAT bits (GD32E230).
const SPE: u32 = 1 << 6; // SPI enable
const CTL1_DMAREN: u32 = 1 << 0; // CTL1: RX DMA request enable
const STAT_RBNE: u32 = 1 << 0; // STAT: receive buffer not empty

// DMA CH1 CTL bits.
const CHEN: u32 = 1 << 0;

/// Vendor's live CH1 control word (circular P->M, 8-bit, mem-incr, prio high,
/// HTF+FTF IE, CHEN). We replay it verbatim; the interrupt-enable bits are
/// harmless with the DMA IRQ masked in the NVIC (we poll CNT instead).
const CH1_CTL: u32 = 0x20a7;

/// Circular DMA buffer length (vendor uses 200 bytes = two ~100-byte frames).
pub const BUF_LEN: usize = 200;

/// Bring up SPI0 as an 8-bit mode-0 slave and DMA channel 1 to receive the
/// SoC's SPI stream into `buf` circularly. `buf.len()` sets the DMA count.
pub fn init_rx(buf: &mut [u8]) {
    // SPI0 off while configuring. MSTMOD=0 (slave), CPOL/CPHA=0, FF16=0 (8-bit),
    // LF=0 (MSB first) are all the reset defaults -> the config word is just SPE.
    wr(SPI0 + SPI_CTL0, 0);
    wr(SPI0 + SPI_CTL1, CTL1_DMAREN); // request a DMA read per received byte

    // DMA CH1: SPI0_DATA -> buf, circular, 8-bit, CNT = buf length.
    modify(DMA + DMA_CH1CTL, CHEN, 0); // disable while configuring
    wr(DMA + DMA_CH1PADDR, SPI0 + SPI_DATA); // 0x4001300c
    wr(DMA + DMA_CH1MADDR, buf.as_mut_ptr() as u32);
    wr(DMA + DMA_CH1CNT, buf.len() as u32);
    wr(DMA + DMA_CH1CTL, CH1_CTL & !CHEN); // config, channel disabled
    modify(DMA + DMA_CH1CTL, 0, CHEN); // enable channel

    modify(SPI0 + SPI_CTL0, 0, SPE); // enable SPI0 slave (CTL0 = 0x40)
}

/// Current DMA write cursor as a byte offset into the circular buffer.
/// CH1CNT counts *down* from `BUF_LEN`, so the next write lands at
/// `BUF_LEN - CH1CNT`.
#[inline]
pub fn dma_pos(buf_len: usize) -> usize {
    let remaining = rd(DMA + DMA_CH1CNT) as usize;
    buf_len.wrapping_sub(remaining) % buf_len.max(1)
}

/// Parse the most recent frame header in the circular buffer and return the
/// per-frame range field the vendor's parser extracts.
///
/// Frame: `AA 30 XX 18 80 00 <samples>`. The vendor walks the mode-0 path
/// (`FUN_080025e4` case 0 on the `0x30`, then `FUN_08002764` case 0 on `XX`):
/// `+0x6a = ((0x30<<28)>>23) = 0`, then `+0x6a += XX>>3`. So the extracted
/// range field is `XX >> 3` — the coarse, pre-tracking-filter value the vendor
/// feeds to `FUN_08002450`. The vendor then smooths it across frames
/// (`FUN_08004fa0`) for its final report; that filter is not ported here
/// (see docs/re/05-calibration.md). Reads are volatile: DMA writes concurrently.
pub fn latest_range(buf_ptr: *const u8, len: usize) -> Option<u16> {
    let rd8 = |i: usize| unsafe { core::ptr::read_volatile(buf_ptr.add(i % len)) };
    for i in 0..len {
        if rd8(i) == 0xAA && rd8(i + 1) == 0x30 {
            return Some((rd8(i + 2) >> 3) as u16);
        }
    }
    None
}

/// Scan the ring for `AA 30` frames and return the **detected range bin**
/// (`XX>>3`, 0..30) averaged over the frames that detected a target, or `None`
/// when no frame detected one. The SoC writes bin **31** (`XX≈0xF8`) as its
/// no-detection default, so we drop 31. Confirmed on HW + by FFT of the sample
/// payload: a 2 m retroreflector lands on bin ~11. Volatile reads (DMA writes).
pub fn detected_bin(buf_ptr: *const u8, len: usize) -> Option<u8> {
    let rd8 = |i: usize| unsafe { core::ptr::read_volatile(buf_ptr.add(i % len)) };
    let mut sum = 0u16;
    let mut count = 0u16;
    for i in 0..len {
        if rd8(i) == 0xAA && rd8(i + 1) == 0x30 {
            let bin = rd8(i + 2) >> 3;
            // Gate to the real target band: reject >= 28 (bin 30/31 = the SoC's
            // no-detection default, past the ~3 m stock range) AND < 5 (close-in
            // clutter / antenna coupling at ~0.5 m that the SoC's own tracker
            // also discards). Confirmed noisy/bimodal on HW for the 2 m reflector.
            if (5..28).contains(&bin) {
                sum += bin as u16;
                count += 1;
            }
        }
    }
    if count > 0 {
        Some((sum / count) as u8)
    } else {
        None
    }
}

/// Polled-slave fallback (no DMA): forward each received SPI byte out USART1.
/// Kept for bring-up bisection; the DMA path above is the real one.
#[allow(dead_code)]
pub fn capture_forward() -> ! {
    wr(SPI0 + SPI_CTL0, 0);
    modify(SPI0 + SPI_CTL0, 0, SPE);
    loop {
        if rd(SPI0 + SPI_STAT) & STAT_RBNE != 0 {
            let b = rd(SPI0 + SPI_DATA) as u8;
            crate::usart::write(&[b]);
        }
    }
}
