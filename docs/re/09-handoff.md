# Rd-03E Firmware RE — Agent Handoff

Nov 9, 2026 · @K

## Objective

Reverse engineer the Ai-Thinker Rd-03E 24 GHz FMCW radar module's MCU firmware in Ghidra, with the goal of documenting how the GD32 host MCU configures and reads the S3KM111L radar SoC. The end state is enough understanding to write custom firmware for the GD32E230, or to drive the S3KM111L from another MCU.

The radar SoC has no public datasheet, so the firmware is the best available spec. Vendor firmware images are downloadable; the module also exposes SWD for flash dumps.

## Hardware

The board is a two-chip design: an S3KM111L radar SoC on the antenna side and a GD32E230K8 Cortex-M23 on the back, identified from chip markings on K's unit.

| Part | Side | Role | Key facts |
| --- | --- | --- | --- |
| ICLegend Micro S3KM111L | Front (antenna side) | 24 GHz FMCW radar SoC | K-band, up to 1 GHz sweep bandwidth; no public datasheet |
| GigaDevice GD32E230K8 (QFN32) | Back | Host MCU running app firmware | Cortex-M23 (ARMv8-M Baseline), 72 MHz, 64 KB flash, 8 KB SRAM; marking "GD ARM / E230K8" |
| Patch antenna arrays | Front | TX/RX | Four-element columns on each side of the SoC |

**Headers**

- **J2 (5 pins, comms + power):** VCC (5 V), GND, OT1 = UART TX, RX = UART RX, OT2 = digital detection output.
- **J3 (SWD pads, back):** 3V3, CLK (SWCLK), DIO (SWDIO), GND. This is the GD32's programming port. J-Link V9+ or CMSIS-DAP per the vendor manual.
- Silkscreen on back: "Rd-03E G521".

## Firmware and SDK status

The shipping firmware is binary-only, and the radar signal processing is proprietary; only the GD32 application layer is likely available as source.

| Item | Availability | Notes |
| --- | --- | --- |
| Build 2268 (precision ranging) | Binary download | From Ai-Thinker's Rd-03E docs page |
| Build 2269 (gesture recognition) | Binary download | Same page; diffing against 2268 separates shared radar code from app code |
| Rd-03E SDK | Vendor package, Keil 5 project | Manual says customers can develop in the released SDK; contents unverified. Expect the radar chain as a precompiled lib |
| S3KM111L datasheet | Not public | Users report obtaining it only through unofficial channels |
| Rd-01 radar SDK (sister module) | GitHub | Depends on obsolete Bouffalo SDK plus a precompiled RISC-V radar library; useful only as a reference for the protocol |
| GD32E23x support | Open | GigaDevice firmware library + CMSIS pack; Rust PAC/HAL in gd32-rs; gcc templates |

Vendor toolchain per manual: Keil 5 with GigaDevice.GD32E23x\_DFP.1.0.1.pack and ARM.CMSIS 5.7.0. Assume the images were built with ARMCC/armclang.

## Ghidra setup

Load as `ARM:LE:32:Cortex` (Thumb); Ghidra's Cortex spec covers ARMv8-M Baseline instructions such as SDIV/UDIV, CBZ and MOVW/MOVT.

1. **Import.** `.hex` → Intel Hex loader places it automatically. `.bin` → raw binary at base `0x08000000`.
2. **Memory map.** Add the blocks below before auto-analysis.
3. **Vector table.** Word 0 = initial SP, word 1 = `Reset_Handler`, then exception and IRQ handlers. Mark them as Thumb functions (clear bit 0 of each pointer).
4. **SVD.** Import the GD32E230 SVD (from gd32-rs `svd/`) with the SVD-Loader script so peripheral accesses resolve to named registers.
5. **Function ID.** Build GigaDevice's GD32E23x standard peripheral library (Keil or arm-none-eabi-gcc), generate FID signatures, and apply them. Vendor modules nearly always use the stock SPL.
6. **Runtime helpers.** Label `__aeabi_*`, `__scatterload`, memcpy/memset by pattern.

| Region | Start | Size | Notes |
| --- | --- | --- | --- |
| Flash | 0x08000000 | 64 KB | Code + rodata |
| SRAM | 0x20000000 | 8 KB | Initial SP lands at its top |
| System memory / option bytes | 0x1FFFEC00 / 0x1FFFF800 | — | Bootloader ROM; option bytes hold read protection. Verify against the GD32E23x user manual |
| APB/AHB1 peripherals | 0x40000000 | — | SPI, I2C, USART, timers, ADC, DMA, RCU |
| AHB2 (GPIO) | 0x48000000 | — | GPIOA/B/C/F |

Take exact peripheral addresses from the SVD rather than this table.

## Analysis plan

The top priority is the GD32 → S3KM111L init sequence, because it is effectively the undocumented radar register map.

1. **Find the inter-chip bus.** Trace from `Reset_Handler` through clock and GPIO init. Identify which of SPI0/SPI1/I2C0/I2C1 is enabled and which pins it uses. Note any GPIO used as chip enable, reset or interrupt for the S3KM111L.
2. **Extract the radar init sequence.** Recover every register address/value written to the S3KM111L at boot, in order. Name fields where code or constants hint at meaning (start/stop frequency, ramp time, chirps per frame, gain, ADC rate).
3. **Map the data path.** How samples or results come back: polled reads, DMA, or an EXTI interrupt on a ready line. Record buffer sizes and formats.
4. **Locate signal processing.** Look for FFT (butterfly loops, twiddle tables in rodata), windowing tables, CFAR/thresholding, and tracking. Determine whether range FFT runs on the GD32 or arrives pre-processed from the S3KM111L.
5. **Diff 2268 vs 2269.** Functions identical in both are shared radar plumbing; differing ones are the ranging vs gesture application logic. BinDiff or Ghidra Version Tracking.
6. **Document the UART protocol.** Start at the USART IRQ handler: frame headers/tails, command IDs, config commands, report formats, baud rate.
7. **Cross-check on hardware (optional, needs K).** Logic-analyzer capture of the inter-chip bus during boot, compared against the recovered sequence. SWD flash dump if read protection is off, compared against the vendor image.

## Open questions, caveats, deliverables

The biggest unknown is how much of the radar processing lives on the GD32 versus inside the S3KM111L.

**Open questions**

- Does the S3KM111L run its own firmware (internal ROM/flash)? If so, that code is not in the downloads and only the GD32 side of the conversation is visible. The sister Rd-03 uses the S3KM1110, which has an integrated MCU, so the "L" variant pairing with an external GD32 suggests processing moved to the GD32. Unconfirmed.
- Are the vendor downloads `.hex` or `.bin`, and are they plain (not encrypted or compressed)? Expected plain Keil output.
- What does the Rd-03E SDK package actually contain: app source plus a radar `.lib`, or more?
- Is flash read protection set on shipped modules?

**Caveats**

- Peripheral addresses and option-byte locations in this doc are from memory; confirm against the SVD and GD32E23x user manual.
- The pin function of OT1/OT2 is from vendor docs; confirm on hardware.

**Deliverables**

- [x] Headless Ghidra analysis of 2268/2269 (`bazel run //ghidra:analyze_fw`);
      MMIO + decomp in `out/`. (SVD/FID labelling not needed — the constant
      resolver `tools/hexconst.py` covered readability.)
- [x] **S3KM111L register map:** every init write recovered — `regmap/boot-2268.csv`,
      [`01-s3km111l.md`](01-s3km111l.md). Field meanings partly inferred (control
      reg, channel gains, STC curve); full naming is the remaining interpretation.
- [x] **Inter-chip bus summary:** I2C0 @1 MHz PB6/7 (dev 0x40/0x44) control,
      SPI0 PA4/5/7 + DMA data; [`02-bus.md`](02-bus.md).
- [x] **Signal chain:** runs on the **S3KM111L SoC**, not the GD32 (thin host) —
      [`03-architecture.md`](03-architecture.md).
- [x] **UART protocol:** Ai-Thinker framing, [`03-architecture.md`](03-architecture.md).
- [x] **Diff 2268 vs 2269:** [`01-s3km111l.md`](01-s3km111l.md).
- [x] **From-source firmware:** `firmware/` — a compiling GD32E230 Rust firmware
      that brings up the buses, replays the real radar register table, and runs
      the poll/report loop. (Exceeds the "feasibility note" ask.)

## References

- [Ai-Thinker Rd-03E docs page](https://docs.ai-thinker.com/en/Rd-03E/): firmware builds 2268/2269, host tools, quick start
- [Rd-03E Gesture Recognition User Manual](https://docs.ai-thinker.com/_media/rd-03e_gesture_recognition_user_manual.pdf): J2/J3 pinout, Keil toolchain, SDK mention
- [Rd-03 series page](https://docs.ai-thinker.com/_export/xhtml/en/rd-03): Rd-03 (S3KM1110) SDK download and serial protocol, useful for comparison
- [Ai-Thinker-Radar GitHub](https://github.com/Ai-Thinker-Open/Ai-Thinker-Radar) and [issue #2](https://github.com/Ai-Thinker-Open/Ai-Thinker-Radar/issues/2): Rd-01 SDK, S3KM111L datasheet availability
- [GigaDevice GD32E230K8T6 product page](https://www.gigadevice.com/product/mcu/mcus-product-selector/gd32e230k8t6): datasheet, user manual, firmware library
- [gd32-rs](https://github.com/gd32-rust/gd32-rs): GD32E230 SVD and Rust PAC
