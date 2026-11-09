# GD32E230 peripheral & bus map (build 2268)

What the host MCU actually drives, read from `out/2268/mmio_accesses.csv` (223
accesses, 36 registers) plus the decompiled init functions. Peripheral bases are
GD32E230 (confirm exact offsets against the SVD). Pin assignments are from the
GPIO alternate-function masks in the init code.

## Peripherals touched

| Peripheral | Base | Role (inferred) | Init fn |
| --- | --- | --- | --- |
| **I2C0** | `0x40005400` | **S3KM111L control bus** (dev 0x40/0x44), 1 MHz | `FUN_08004758` |
| **SPI0** | `0x40013000` | **bulk data path** (+ DMA CH0), see below | `FUN_080081dc` / `FUN_08004ecc` |
| **DMA ch0** | `0x40020008` | moves SPI0 data (`CH0PADDR` = `SPI0_DATA 0x4001300c`) | `FUN_0800739c`/`FUN_080074a4` |
| **USART1** | `0x40004400` | **host UART** (J2 OT1/RX), report + command I/O | `FUN_08005c88` |
| USART0 | `0x40013800` | secondary UART (debug?) | `FUN_080088cc` |
| SPI1 | `0x40003800` | configured; role TBD (2nd device / unused) | `FUN_08008260` |
| TIMER2 | `0x40000400` | frame/sample timing (passed through init) | `FUN_08004b1c` |
| TIMER0/5/13/15/16 | various | timebases | `FUN_08003ea4` |
| RCU | `0x40021000` | clocks/PLL + per-peripheral enables | `FUN_08005984` (sysclk) |
| FMC | `0x40022000` | flash write — persist config/calibration | `FUN_08008408` |
| GPIOA/B | `0x48000000/400` | pin mux (see below) | — |

**Notably absent: the GD32's own ADC (`0x40012400`) is never touched.** The
radar samples do **not** come in through the MCU's ADC — they arrive over a
bus from the S3KM111L. Combined with the DMA binding, the sample stream is SPI0.

## Pin assignments (from GPIO AF masks)

| Signal | Port/pins | Evidence |
| --- | --- | --- |
| I2C0 SCL / SDA | **PB6 / PB7** | `FUN_08004758`: AF-mode on GPIOB mask `0xC0` (bits 6,7) |
| SPI0 NSS/SCK/MOSI | **PA4 / PA5 / PA7** | `FUN_08007970`: AF-mode on GPIOA mask `0xB0` (bits 4,5,7) |
| (SPI0 MISO PA6) | **not muxed** | `0xB0` omits bit 6 — see data-path note |

RCU clock-enable encoding (`FUN_08008170(periph)`, `periph=(reg_off<<6)|bit`)
was used to confirm: `0x511`->AHBEN.17 = GPIOA, `0x512`->AHBEN.18 = GPIOB,
`0x715`->APB1EN.21 = I2C0.

## Data path: SPI0 + DMA ch0

- `FUN_08004ecc` brings SPI0 up as a unit: GPIO AF (`FUN_08007970`), DMA ch0
  setup (`FUN_0800739c`, which touches `SPI0_DATA 0x4001300c`), SPI0 config
  (`FUN_080081dc`), SPI0 enable (`FUN_0800824c` sets CTL0 bit6 = SPE).
- DMA **CH0PADDR = `SPI0_DATA`**; `CH0MADDR`/`CH0CNT` set per transfer
  (`FUN_080074a4`). So a DMA channel shuttles SPI0 words to/from a RAM buffer.
- **Direction is not yet confirmed.** SPI0's MISO (PA6) is *not* muxed, only
  NSS/SCK/MOSI — consistent with either a receive-only bidirectional mode (data
  on MOSI) or a transmit path. Resolve by reading the `CH0CTL` DIR bit value and
  the SPI0 `CTL0` BIDIMODE/RXONLY bits (config word built in `FUN_080083e0`/
  `FUN_080081dc`). **Open.**

## Call tree (bring-up)

```
Reset (0x080000e0)
  ... SystemInit / clocks  (FUN_08005984: RCU CTL/CFG0/CFG1/CTL1 -> PLL)
  FUN_08004b1c   GPIOA.9 + I2C0 bring-up (-> FUN_08004758)         [control bus]
  FUN_08004ecc   SPI0 + DMA ch0 bring-up (-> FUN_0800739c/81dc)    [data bus]
  FUN_08005c88   USART1 host UART                                   [host I/O]
  FUN_08004cb8   S3KM111L boot register table -> dev 0x40          [radar init]
  FUN_08001300   per-mode reconfig tables -> dev 0x40 (+0x44)       [radar mode]
```

## Open

- SPI0/DMA direction + exact SPI0 `CTL0` config word (BIDIMODE/RXONLY, DFF, baud).
- USART1 baud (set in `FUN_080087e4`; needs the resolved APB1 clock).
- SPI1's purpose; USART0's purpose.
- EXTI/data-ready line from the S3KM111L (no EXTI hits seen — polled or DMA-only?).
