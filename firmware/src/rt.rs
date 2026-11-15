//! Minimal Cortex-M23 (ARMv8-M Baseline) runtime — the little bit of startup
//! that `cortex-m-rt` used to provide, written in-crate so the firmware builds
//! from **prebuilt `core` alone** (no external crates, no crate_universe). It
//! supplies four things cortex-m-rt / cortex-m / panic-halt gave us:
//!   * the **vector table** (initial SP + reset vector + default handlers),
//!   * the **reset handler** (zero `.bss`, copy `.data` from flash, call `main`),
//!   * a **`#[panic_handler]`** (halt), and
//!   * a **busy `delay`** (replaces `cortex_m::asm::delay`).
//!
//! The vector table is placed at the flash origin by `link.x`
//! (`.vector_table.*` sections). We never unmask an NVIC IRQ (the data path
//! polls DMA `CH1CNT`), so every exception/IRQ slot points at `DefaultHandler`.

use core::panic::PanicInfo;

extern "C" {
    // Provided by link.x.
    static mut _sbss: u32;
    static mut _ebss: u32;
    static mut _sdata: u32;
    static mut _edata: u32;
    static _sidata: u32; // .data load address (LMA) in flash
}

/// Reset handler: initialise RAM, then enter `main`. `link.x` sets `ENTRY(Reset)`
/// and stores its (Thumb-bit-set) address as vector[1].
#[no_mangle]
pub unsafe extern "C" fn Reset() -> ! {
    // Zero .bss.
    let mut dst = core::ptr::addr_of_mut!(_sbss);
    let bss_end = core::ptr::addr_of_mut!(_ebss);
    while dst < bss_end {
        core::ptr::write_volatile(dst, 0);
        dst = dst.add(1);
    }
    // Copy .data from its flash LMA to its RAM VMA.
    let mut dst = core::ptr::addr_of_mut!(_sdata);
    let data_end = core::ptr::addr_of_mut!(_edata);
    let mut src = core::ptr::addr_of!(_sidata);
    while dst < data_end {
        core::ptr::write_volatile(dst, core::ptr::read(src));
        dst = dst.add(1);
        src = src.add(1);
    }
    crate::main()
}

/// Catch-all for every exception/IRQ. Nothing should fire one (IRQs stay masked
/// in the NVIC), so if we land here something is wrong — halt for the debugger.
#[no_mangle]
pub extern "C" fn DefaultHandler() -> ! {
    loop {
        core::hint::spin_loop();
    }
}

/// vector[1]: the reset vector. `#[used]` + `KEEP` in link.x stop it being
/// garbage-collected since nothing in Rust references it.
#[used]
#[link_section = ".vector_table.reset_vector"]
#[no_mangle]
pub static RESET_VECTOR: unsafe extern "C" fn() -> ! = Reset;

/// vectors[2..]: the 14 ARMv8-M system-exception slots followed by 32 external
/// IRQ slots (GD32E230 has fewer; the surplus is harmless), all = DefaultHandler.
#[used]
#[link_section = ".vector_table.exceptions"]
#[no_mangle]
pub static EXCEPTIONS: [extern "C" fn() -> !; 46] = [DefaultHandler; 46];

/// Halt on panic (replaces `panic-halt`). `panic=abort` means this is the end
/// of the line; loop so the debugger can inspect state.
#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {
        core::hint::spin_loop();
    }
}

/// Busy-wait ~`cycles` iterations (replaces `cortex_m::asm::delay`). Used once,
/// for the S3KM111L power-up settle, so approximate timing is fine. The
/// `subs`/`bne` pair is ~1 cycle each on Cortex-M23; `volatile`-ish via the asm
/// barrier so the optimiser can't drop it.
#[inline(always)]
pub fn delay(cycles: u32) {
    let mut n = cycles;
    // SAFETY: a self-contained countdown loop touching only its own register.
    unsafe {
        core::arch::asm!(
            "1:",
            "subs {n}, {n}, #1",
            "bne 1b",
            n = inout(reg) n,
            options(nomem, nostack),
        );
    }
    let _ = n;
}
