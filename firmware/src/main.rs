//! From-source GD32E230 firmware for the Ai-Thinker Rd-03E.
//!
//! Reverse-engineered bring-up of the host MCU so the S3KM111L radar can be
//! driven by our own code — the foundation for developing custom radar
//! algorithms. See `docs/re/` for the analysis this mirrors:
//!   - 01-s3km111l.md : I2C control model (dev 0x40/0x44, 16-bit BE registers)
//!   - 02-bus.md      : peripheral/pin map, SPI0+DMA data path
//!
//! Status: hardware access layer (clocks, pins, I2C0, SPI0+DMA, USART1) is
//! RE-faithful; the radar register tables and signal processing are TODO.

#![no_std]
#![no_main]

use cortex_m_rt::entry;
use panic_halt as _;

mod datapath;
mod gpio;
mod i2c;
mod pac;
mod protocol;
mod rcu;
mod s3km111l;
mod usart;

/// Radar sample ring buffer that DMA fills from SPI0 (size is a placeholder
/// until the real frame geometry is known).
static mut FRAME_BUF: [u16; 256] = [0; 256];

#[entry]
fn main() -> ! {
    // Hardware bring-up, in the vendor's order (docs/re/02-bus.md call tree).
    rcu::init_clocks();
    gpio::init_pins();
    i2c::init(); // S3KM111L control bus (PB6/PB7, 1 MHz)
    usart::init(); // host UART (J2)

    // Let the S3KM111L power up after PA9 enable before configuring it (~20 ms).
    cortex_m::asm::delay(1_440_000);

    // Radar control: replay the 80-entry boot register table over I2C.
    let dual = false; // single-SoC board; set true to also drive dev 0x44
    s3km111l::init(dual);

    // NOTE: SPI0/DMA raw-sample path (datapath::init_rx) is intentionally NOT
    // brought up here — the target range/presence come from the I2C result
    // registers below, and leaving SPI down shrinks the bring-up failure surface.
    let _ = core::ptr::addr_of_mut!(FRAME_BUF);

    // Bring-up diagnostic loop. Streams back four I2C registers as tagged
    // frames (0x10=reg01, 0x11=reg04, 0x12=reg05, 0x13=reg44). HITL result
    // (2026-11-12): these read back EXACTLY the boot-table write values
    // (0x8E24/0x020C/0x0017/0x0020) — they are *config* registers, which proves
    // the I2C link + SoC config store work, but they are NOT the distance
    // source. Distance arrives as a framed SPI0+DMA byte stream parsed on the
    // host (see docs/re/05-calibration.md, FUN_0800207c/FUN_08002450); bringing
    // up `datapath` is the next step. This loop stays as a link-health probe.
    loop {
        protocol::send_raw(0x10, s3km111l::result::status());
        protocol::send_raw(0x11, s3km111l::result::reg04());
        protocol::send_raw(0x12, s3km111l::result::reg05());
        protocol::send_raw(0x13, s3km111l::result::reg44());
        cortex_m::asm::delay(3_600_000); // ~50 ms @ 72 MHz
    }
}
