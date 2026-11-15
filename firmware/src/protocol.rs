//! Ai-Thinker Rd-03E host UART protocol (USART1 / J2).
//!
//! The config-command delimiters are confirmed in the vendor `.data` image
//! (header `FD FC FB FA`, footer `04 03 02 01`). The report framing was
//! recovered on hardware: a fixed 7-byte `55 55 AA AA <type> <lo> <hi>`.

use crate::usart;

/// Config-command frame: `FD FC FB FA | len(2 LE) | cmd(2 LE) | data | 04 03 02 01`.
/// (These delimiters ARE confirmed in the vendor `.data` image at 0x08009838.)
pub const CONFIG_HDR: [u8; 4] = [0xFD, 0xFC, 0xFB, 0xFA];
pub const CONFIG_FTR: [u8; 4] = [0x04, 0x03, 0x02, 0x01];

/// Actual vendor report framing, recovered on hardware (HITL, 2026-11-11): a
/// fixed 7-byte frame `55 55 AA AA <type> <lo> <hi>`, value16 = lo|(hi<<8).
/// The old `F4 F3 F2 F1` guess from the vendor docs is NOT in the binary.
/// `type`: 0 = no target, 2 = target present (value = range), 1 = transition.
pub const REPORT_HDR: [u8; 4] = [0x55, 0x55, 0xAA, 0xAA];

/// Emit one `55 55 AA AA <type> <lo> <hi>` frame.
pub fn send_raw(ty: u8, value: u16) {
    usart::write(&REPORT_HDR);
    usart::write(&[ty, value as u8, (value >> 8) as u8]);
}

/// Emit a config-command acknowledgement/echo (host-tool handshake path).
pub fn send_config(cmd: u16, data: &[u8]) {
    usart::write(&CONFIG_HDR);
    let n = (data.len() + 2) as u16;
    usart::write(&[n as u8, (n >> 8) as u8]); // length, little-endian
    usart::write(&[cmd as u8, (cmd >> 8) as u8]);
    usart::write(data);
    usart::write(&CONFIG_FTR);
}
