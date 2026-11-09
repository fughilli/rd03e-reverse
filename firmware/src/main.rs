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

    // Radar control: replay the boot register table over I2C (TODO: fill it).
    let dual = false; // single-SoC board; set true to also drive dev 0x44
    s3km111l::init(dual);

    // Radar data: stream SPI0 into FRAME_BUF via DMA.
    // SAFETY: single-threaded bring-up; the DMA owns FRAME_BUF after this.
    let buf: &mut [u16] = unsafe { &mut *core::ptr::addr_of_mut!(FRAME_BUF) };
    datapath::init_rx(buf);

    // Main poll/report loop — the vendor's `do { collect; report } while(true)`
    // (FUN_08007e4c). The S3KM111L runs the radar DSP internally; the host polls
    // its result registers over I2C and emits Ai-Thinker report frames on USART1.
    loop {
        let status = s3km111l::result::status();
        let r04 = s3km111l::result::reg04();
        let r05 = s3km111l::result::reg05();

        // Report payload. TODO: lay this out per the documented Rd-03E ranging
        // report (target distance/energy); the register reads above are the
        // confirmed source. This is also the hook for a custom algorithm —
        // post-process here, or reconfigure the SoC via s3km111l::write_reg().
        let payload = [
            status as u8, (status >> 8) as u8,
            r04 as u8, (r04 >> 8) as u8,
            r05 as u8, (r05 >> 8) as u8,
        ];
        protocol::send_report(&payload);
    }
}
