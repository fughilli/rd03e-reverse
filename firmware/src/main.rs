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

/// Circular DMA buffer that SPI0-slave/DMA-CH1 fills from the SoC stream.
/// 200 bytes = two ~100-byte frames, matching the vendor (datapath.rs).
static mut FRAME_BUF: [u8; datapath::BUF_LEN] = [0; datapath::BUF_LEN];

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

    // Radar data path: SPI0 slave + DMA channel 1 receive the SoC's framed
    // stream into FRAME_BUF (circular), exactly as the vendor does — config
    // read live over SWD (datapath.rs). The SoC, now configured above, is the
    // SPI master and streams ~100-byte `AA 30 …` frames.
    let buf: &mut [u8] = unsafe { &mut *core::ptr::addr_of_mut!(FRAME_BUF) };
    datapath::init_rx(buf);

    // Full from-source chain: parse the captured frames and report the range
    // field on USART1 in the vendor's `55 55 AA AA <type> <lo> <hi>` format.
    // type 2 = target (value = per-frame range field, XX>>3), type 0 = no frame
    // found this tick. This is the coarse pre-filter value; matching the
    // vendor's smoothed distance needs the tracking filter (docs/re/05). ~50 Hz.
    let base = core::ptr::addr_of!(FRAME_BUF) as *const u8;
    loop {
        match datapath::latest_range(base, datapath::BUF_LEN) {
            Some(v) => protocol::send_raw(2, v),
            None => protocol::send_raw(0, 0),
        }
        cortex_m::asm::delay(3_600_000); // ~50 ms @ 72 MHz
    }
}
