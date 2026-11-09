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

Result values come from **polling S3KM111L registers over I2C** — the host
accessors read regs `0x01` (status), `0x04`, `0x05`, `0x44`
(`FUN_08004b7c/4c60/4c1c/4c36`). The `0x20..0x2f` CFAR/threshold curve and the
chirp configuration all live *in the SoC* (they are I2C register writes), so
detection/thresholding is the SoC's job.

## Host UART protocol (USART1 / J2)

Standard Ai-Thinker Rd-03E framing (delimiters confirmed in the `.data` image):

| Frame | Header | Footer | Body |
| --- | --- | --- | --- |
| Config command | `FD FC FB FA` | `04 03 02 01` | `len(2 LE) | cmd(2 LE) | data` |
| Report / data | `F4 F3 F2 F1` | `F8 F7 F6 F5` | `len(2 LE) | payload` |

Command IDs and the ranging report payload are in the vendor user manuals
(`09-handoff.md`). Implemented in `firmware/src/protocol.rs`.

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

- Field semantics of the result registers (distance encoding, target count) and
  the differing chirp registers — map against radar physics + the Rd-01 notes.
- SPI0 frame format/direction (`02-bus.md`).
- The per-mode reconfig tables (`FUN_08001300`, scatter entry 2).
