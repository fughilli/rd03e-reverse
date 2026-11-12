# Report framing, distance path, and range calibration (HITL-measured)

Everything here was measured on the physical module over the HITL rig (RP2350
SWD probe + ESP32‑C6 UART tap on J2), 2026‑11. It **corrects two claims** made
from static analysis alone in `03`/`04`:

1. The report frame is **`55 55 AA AA`**, not `F4 F3 F2 F1`. The `F4 F3 F2 F1`/
   `F8 F7 F6 F5` delimiters from the vendor user manual are **not present** in
   build 2268's binary (grep of the flash image finds neither).
2. The target distance is **not** read from an I2C result register. It is
   recovered from a framed byte stream the SoC pushes over **SPI0 + DMA**, parsed
   on the GD32. Regs `0x01/0x04/0x05/0x44` are **config** registers — reading
   them back returns exactly the values the boot table wrote (verified: our
   from‑source firmware reads `0x8E24/0x020C/0x0017/0x0020`, byte‑for‑byte the
   boot‑table writes). The I2C "result accessors" `FUN_08004c1c`/`FUN_08004c36`
   return the constants `0x40`/`0x20`; `FUN_08004b7c` returns `0`. They are not
   the distance source.

## Report frame (USART1 / J2, 256000 baud 8N1)

Fixed **7‑byte** frame, little‑endian 16‑bit value:

```
55 55 AA AA  <type>  <lo> <hi>          value16 = lo | (hi << 8)
```

| `type` | meaning                | `value16`                     |
| ------ | ---------------------- | ----------------------------- |
| `0`    | no target              | `0`                           |
| `1`    | transition / tentative | in‑flight range               |
| `2`    | target present         | **range** (calibrated below)  |

An earlier parser that synced only on `type == 2` dropped the `type == 1`
frames and injected zeros — do not do that; decode all three types.

## Distance path (SPI0 → DMA → GD32 frame parser)

The S3KM111L runs the radar DSP internally and streams results to the host:

```
S3KM111L ──SPI0 (0x40013000)──▶ DMA ──▶ SRAM ring (0x200013A4, 0xD0/channel)
                                          │
   FUN_0800207c  9‑state byte parser  ◀───┘   sync byte 0xAA, per‑channel
   (sub‑handlers FUN_080025e4/2764/27f8/…)    struct stride 0x72:
                                                 +0x68 state (0..8)
                                                 +0x6a range accumulator A
                                                 +0x6c range accumulator B
                                                 +0x6e write cursor
                                                 +0x70 remaining length
                     │
   FUN_08002450  range scaling ───────────────▶ FUN_08002f0c / FUN_08001f58
     local_24 = frame +0x6c (mode 1) or +0x6a (mode 0/3)
     local_28 = 1000 >> 3 (mode 1) | 1000 >> 6 (mode 2) | 1000 (mode 0/3)
     mode selector at *DAT_08002868 (SRAM 0x20001070)
                     │
                     ▼  USART1
            55 55 AA AA <type> <lo> <hi>
```

Consequence for from‑source firmware: to report distance we must bring up the
**SPI0 receive + DMA** path (`datapath.rs`) and port this parser — there is no
shortcut register to poll. The hardware bring‑up (clocks, GPIO, I2C config,
UART) is already from‑source and HW‑verified; this parser is the remaining
datapath work.

## Range calibration (corner reflector sweep, stock vendor firmware)

Trihedral corner reflector on boresight, `type == 2` `value16` recorded at each
range. Presence % = fraction of frames reporting the target.

| Range (cm) | reported `value16` | presence % | note                 |
| ---------- | ------------------ | ---------- | -------------------- |
| 48         | 61                 | 100        |                      |
| 122        | 128                | 100        |                      |
| 203        | 201                | 100        |                      |
| 244        | 235                | 97         |                      |
| 274        | 261                | 81         | SNR dropping         |
| 305        | ~330 (scattered)   | 50         | detection breaking up|
| 358        | 0                  | 0          | no target            |

**Linear fit (5 clean points, 48–274 cm):**

```
value16   = 0.886 · range_cm + 19.4      (R² = 0.99978, ~1.13 cm/LSB)
range_cm  = 1.129 · value16  − 21.9
```

- **Usable range (stock config):** ~2.5 m clean, ~3 m hard ceiling. Beyond ~3 m
  the target breaks up (presence < 50 %) and then drops to no‑target.
- The ceiling is a **config/threshold limit, not a hardware wall**: the CFAR/STC
  threshold curve (`0x20..0x2f`) and the chirp registers (`0x43..0x72`) govern
  it. Extending range means raising the far‑range thresholds and/or re‑profiling
  the chirp — bounded by SNR∝1/R⁴, ADC Nyquist, and the SoC's range‑gate count.

## Reproduce

C6 runs the listen‑only firmware (`//:esp32c6_listen`), which prints one line
per frame: `F <n> T <ms> Y <type> V <value16> O <otherbyte>`. Capture with
`hitl-monitor --port /dev/ttyACM0 --seconds N` (raw `cat`/`stty` resets the C6).
Plots: `range_calibration.svg`, `distance_fit.svg`, `radar_response.svg`.
