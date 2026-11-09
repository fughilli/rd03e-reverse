//! Ai-Thinker Rd-03E host UART protocol (USART1 / J2).
//!
//! The firmware speaks the standard Ai-Thinker radar framing. The config-command
//! delimiters are confirmed in the vendor `.data` image (header `FD FC FB FA` at
//! `0x20000004`, footer `04 03 02 01` at `0x20000008`); the report-frame
//! delimiters are the documented `F4 F3 F2 F1` / `F8 F7 F6 F5`. See the Rd-03E
//! user manuals referenced in `docs/re/09-handoff.md` for the command IDs and
//! report payload layout.

use crate::usart;

/// Config-command frame: `FD FC FB FA | len(2 LE) | cmd(2 LE) | data | 04 03 02 01`.
pub const CONFIG_HDR: [u8; 4] = [0xFD, 0xFC, 0xFB, 0xFA];
pub const CONFIG_FTR: [u8; 4] = [0x04, 0x03, 0x02, 0x01];

/// Report/data frame: `F4 F3 F2 F1 | len(2 LE) | payload | F8 F7 F6 F5`.
pub const REPORT_HDR: [u8; 4] = [0xF4, 0xF3, 0xF2, 0xF1];
pub const REPORT_FTR: [u8; 4] = [0xF8, 0xF7, 0xF6, 0xF5];

fn send_len(n: u16) {
    usart::write(&[n as u8, (n >> 8) as u8]); // little-endian
}

/// Emit a report frame wrapping `payload`.
pub fn send_report(payload: &[u8]) {
    usart::write(&REPORT_HDR);
    send_len(payload.len() as u16);
    usart::write(payload);
    usart::write(&REPORT_FTR);
}

/// Emit a config-command acknowledgement/echo (host-tool handshake path).
pub fn send_config(cmd: u16, data: &[u8]) {
    usart::write(&CONFIG_HDR);
    send_len((data.len() + 2) as u16);
    usart::write(&[cmd as u8, (cmd >> 8) as u8]);
    usart::write(data);
    usart::write(&CONFIG_FTR);
}
