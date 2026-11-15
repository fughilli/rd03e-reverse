# Firmware architecture — the GD32E230 is a thin host

The complete control-flow model recovered from build 2268. The decisive fact:
**all radar signal processing runs on the S3KM111L SoC; the GD32 only
configures it, polls its result registers, and reports over UART.** This is
forced by the hardware — the GD32E230 has **8 KB of SRAM**, far too little to
buffer or FFT raw radar frames — and confirmed by the code below.

## Main application (`FUN_08007e4c`)

```
FUN_08004b1c()          # bring-up: GPIOA + I2C0 control bus          (02-bus.md)
FUN_080016b8()          # app state init
FUN_08002948(10)        # mode / profile select
FUN_08004cb8()          # S3KM111L boot register table -> I2C dev 0x40 (01-s3km111l.md)
FUN_08002294()          # SPI0 + DMA data path bring-up
FUN_080012f0()          # buffers / report state
FUN_08008b24(...)       # start timers
do {                    # --- infinite poll/report loop ---
    FUN_080023a8();     #   collect radar results from the SoC
    FUN_08001418();     #   emit ready report frames over UART
} while (true);
```

## The loop

- **Collect — `FUN_080023a8`:** iterates over the device count (`*0x200001c8`)
  and two sub-channels, filling per-device buffers (SRAM `0x200013a4`, stride
  **0xD0 = 208 bytes**). The data itself is read from the S3KM111L — the GD32
  does not compute it.
- **Report — `FUN_08001418`:** when a "frame ready" flag (`buf+0x82`) is set,
  sends a **0x40 = 64-byte** report frame (`FUN_08001450`) and clears the flag.

**Correction (HITL‑measured, see `05-calibration.md`).** Result values do *not*
come from polling I2C registers. Regs `0x01/0x04/0x05/0x44` are **config**
registers — reading them back returns exactly the boot‑table write values
(verified on HW), and the "result accessors" `FUN_08004c1c`/`FUN_08004c36`
return the constants `0x40`/`0x20`. The actual target data arrives as a framed
byte stream over **SPI0 + DMA** (`FUN_08002294`→`FUN_08004ecc`), parsed on the
GD32 by the 9‑state machine `FUN_0800207c`; `FUN_08002450` scales the parsed
range and emits it. The `0x20..0x2f` CFAR/threshold curve and the chirp
configuration still live *in the SoC* (they are I2C register writes), so
detection/thresholding is the SoC's job.

## Host UART protocol (USART1 / J2)

Standard Ai-Thinker Rd-03E framing (delimiters confirmed in the `.data` image):

| Frame | Header | Footer | Body |
| --- | --- | --- | --- |
| Config command | `FD FC FB FA` | `04 03 02 01` | `len(2 LE) | cmd(2 LE) | data` |
| Report / data | `55 55 AA AA` | *(none)* | `type(1) | value16(2 LE)` — 7 bytes |

The config delimiters are confirmed in the `.data` image. The **report framing
`55 55 AA AA` was recovered on hardware** (`05-calibration.md`); the manuals'
`F4 F3 F2 F1`/`F8 F7 F6 F5` delimiters are **not** in build 2268's binary.
Implemented in `firmware/src/protocol.rs`.

## What this means for new radar algorithms

Two surfaces, both now open from source:

1. **Reconfigure the SoC's processing** — the register map
   (`firmware/src/s3km111l.rs::BOOT_TABLE`, `regmap/boot-2268.csv`) controls
   chirp parameters, gains (`0x61..0x64`), and the detection threshold/STC curve
   (`0x20..0x2f`). Changing these changes range, resolution, and sensitivity.
2. **Post-process on the GD32** — the poll/report loop in `firmware/src/main.rs`
   is where host-side logic (filtering, tracking, custom reporting) goes. For
   richer data than the result registers expose, the SPI0+DMA path streams SoC
   output into RAM (`datapath.rs`; frame format is the remaining open item).

## Remaining interpretation (not structural)

- SPI0 frame **byte protocol** decoded in `05-calibration.md` (sync `0xAA`,
  per‑channel struct stride `0x72`, range at `+0x6a`/`+0x6c`); the exact
  nibble‑coding of the sub‑modes (`*DAT_080028e4`) is ported but not yet
  HW‑validated against a live capture.
- SPI0 electrical direction/role (master vs slave) — to read live via SWD.
- The differing chirp registers (`0x43..0x72`) — map against radar physics.
- The per-mode reconfig tables (`FUN_08001300`, scatter entry 2).
