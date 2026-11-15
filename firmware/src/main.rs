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

mod datapath;
mod dsp;
mod gpio;
mod i2c;
mod pac;
mod protocol;
mod rcu;
mod rt;
mod rtt;
mod s3km111l;
mod usart;

/// FNV-1a over a frame, to detect when a genuinely new SoC frame has landed
/// (the main loop runs far faster than the ~50 Hz frame rate, so without this
/// we'd push the same frame many times).
fn hash96(f: &[u8; rtt::FRAME_COPY]) -> u32 {
    let mut h: u32 = 0x811c_9dc5;
    for &b in f.iter() {
        h = (h ^ b as u32).wrapping_mul(0x0100_0193);
    }
    h
}

/// Circular DMA buffer that SPI0-slave/DMA-CH1 fills from the SoC stream.
/// 200 bytes = two ~100-byte frames, matching the vendor (datapath.rs).
static mut FRAME_BUF: [u8; datapath::BUF_LEN] = [0; datapath::BUF_LEN];

/// Debug mirror of the last report (readable over SWD): [type, value, bin, tick].
#[no_mangle]
static mut DBG_REPORT: [u16; 4] = [0; 4];

/// Firmware entry. `#![no_main]` means there's no Rust `main` lang item; this is
/// an ordinary function that `rt::Reset` tail-calls after RAM init.
fn main() -> ! {
    // Hardware bring-up, in the vendor's order (docs/re/02-bus.md call tree).
    rcu::init_clocks();
    gpio::init_pins();
    i2c::init(); // S3KM111L control bus (PB6/PB7, 1 MHz)
    usart::init(); // host UART (J2)

    // Let the S3KM111L power up after PA9 enable before configuring it (~20 ms).
    rt::delay(1_440_000);

    // Radar control: replay the 80-entry boot register table over I2C.
    let dual = false; // single-SoC board; set true to also drive dev 0x44
    s3km111l::init(dual);

    // Radar data path: SPI0 slave + DMA channel 1 receive the SoC's framed
    // stream into FRAME_BUF (circular), exactly as the vendor does — config
    // read live over SWD (datapath.rs). The SoC, now configured above, is the
    // SPI master and streams ~100-byte `AA 30 …` frames.
    let buf: &mut [u8] = unsafe { &mut *core::ptr::addr_of_mut!(FRAME_BUF) };
    datapath::init_rx(buf);

    // Streaming data path. The host bridge drains the RTT ring over SWD and
    // does all DSP (FFT / phase / waterfall) in the browser, so the firmware
    // just exfils raw frames and services config RPCs:
    //   * each distinct SoC frame -> pushed into the RTT up-ring, epoch-stamped
    //   * cmd mailbox -> I2C register writes (live FMCW tuning) + epoch bumps
    // The loop runs flat-out (no delay); `hash96` dedups so we push exactly the
    // new frames (the loop is far faster than the ~50 Hz frame cadence).
    //
    // DBG_REPORT is still refreshed periodically (our own DFT range) so a plain
    // SWD readback stays meaningful without the bridge — [type, dist, bin, mag].
    let base = core::ptr::addr_of!(FRAME_BUF) as *const u8;
    let rtt_p = rtt::ptr();
    let mut last_hash: u32 = 0;
    let mut frame = [0u8; rtt::FRAME_COPY];
    let mut samples = [0i16; dsp::N];
    let mut smooth_q4: u32 = 0;
    let mut have = false;
    let mut dbg_div: u32 = 0;
    let mut skip: u32 = 0;

    // Firmware-side carrier-hop state (see rtt.rs hop_* fields). When the host
    // sets `hop_enable`, the MCU itself cycles the VCO carrier (reg 0x70) across
    // the comb, discards `hop_settle` frames after each change, then pushes
    // `hop_dwell` frames *tagged with the carrier index*. The host thus gets
    // stepped-frequency data from the normal-rate stream — zero extra SWD load,
    // and no flaky host-driven carrier writes. `hop_ci` = current comb index,
    // `hop_cnt` = frames seen at this carrier, `hopping` = engaged last frame.
    let mut hopping = false;
    let mut hop_ci: usize = 0;
    let mut hop_cnt: u32 = 0;

    loop {
        if datapath::newest_frame(base, datapath::BUF_LEN, &mut frame) {
            let h = hash96(&frame);
            if h != last_hash {
                last_hash = h;

                if unsafe { rtt::hop_enabled(rtt_p) } {
                    // Entering hop mode (or re-armed): start the comb at index 0.
                    if !hopping {
                        hopping = true;
                        hop_ci = 0;
                        hop_cnt = 0;
                        i2c::write_reg(rtt::HOP_DEV, rtt::HOP_REG, unsafe {
                            rtt::hop_carrier(rtt_p, 0)
                        });
                    }
                    let settle = unsafe { rtt::hop_settle(rtt_p) };
                    let dwell = unsafe { rtt::hop_dwell(rtt_p) };
                    let n = unsafe { rtt::hop_n(rtt_p) };
                    hop_cnt += 1;
                    if hop_cnt > settle {
                        // Past the relock transient: push this frame tagged with
                        // the carrier it was captured under.
                        unsafe { rtt::push(rtt_p, &frame, hop_ci as u8) };
                    }
                    if hop_cnt >= settle + dwell {
                        // Advance to the next comb carrier.
                        hop_ci = (hop_ci + 1) % n;
                        hop_cnt = 0;
                        i2c::write_reg(rtt::HOP_DEV, rtt::HOP_REG, unsafe {
                            rtt::hop_carrier(rtt_p, hop_ci)
                        });
                    }
                    unsafe { rtt::poll_cmd(rtt_p) };
                    continue;
                }

                // Not hopping. If we just left hop mode, leave the carrier where
                // it is; the host restores its tracking carrier via the mailbox.
                hopping = false;

                // Decimate: push 1 of every `decim` distinct frames (tag 0).
                skip += 1;
                if skip < unsafe { rtt::decim(rtt_p) } {
                    continue;
                }
                skip = 0;
                unsafe { rtt::push(rtt_p, &frame, 0) };

                // Periodic from-source range into DBG_REPORT (1 in 16 frames).
                dbg_div = dbg_div.wrapping_add(1);
                if dbg_div & 0xF == 0
                    && datapath::extract_samples(base, datapath::BUF_LEN, &mut samples)
                {
                    let (q4, mag) = dsp::peak_bin_q4(&samples);
                    smooth_q4 = if have { (smooth_q4 * 3 + q4 as u32) / 4 } else { q4 as u32 };
                    have = true;
                    // Calibrated coarse range (HITL reflector fit at 0.699/1.283/
                    // 2.743 m, docs/re/05): higher bin = CLOSER, R[m] = 3.2908 −
                    // 0.1825·bin. smooth_q4 = bin<<4, so (1825·smooth_q4)/160 =
                    // 182.5·bin in mm. (The old `bin×18.2cm` was wrong in scale
                    // AND sign.) For a true absolute scale use the host stepped-
                    // frequency sweep; this is the single-frame coarse estimate.
                    let r_mm = 3291i32 - (1825i32 * smooth_q4 as i32) / 160;
                    let dist_cm = (r_mm.max(0) / 10) as u16;
                    let magh = (mag >> 20).min(65535) as u16;
                    unsafe {
                        let d = core::ptr::addr_of_mut!(DBG_REPORT);
                        (*d)[0] = 2;
                        (*d)[1] = dist_cm;
                        (*d)[2] = (smooth_q4 >> 4) as u16;
                        (*d)[3] = magh;
                    }
                }
            }
        }
        unsafe { rtt::poll_cmd(rtt_p) };
    }
}
