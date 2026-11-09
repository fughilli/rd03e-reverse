# Rd-03E firmware — RE inventory

Grounding for reversing the GD32E230 host firmware. Verified facts are separated
from what still needs Ghidra/hardware evidence. The objective, hardware detail,
and full analysis plan are in [`09-handoff.md`](09-handoff.md).

## Targets (see `../targets/README.md`)

| Build | Variant | Local file | Format |
| --- | --- | --- | --- |
| 2268 | Precision ranging | `targets/rd03e/2268_XenP102RM01.hex` | Intel HEX |
| 2269 | Gesture recognition | `targets/rd03e/2269_XenG102ST.hex` | Intel HEX |

## Verified

- **Host MCU:** GD32E230K8 — Cortex-M23 (ARMv8-M Baseline, Thumb), 72 MHz,
  64 KB flash @ `0x08000000`, 8 KB SRAM @ `0x20000000`. Ghidra language
  `ARM:LE:32:Cortex`.
- **Firmware delivery:** Intel HEX on the vendor docs page; builds 2268/2269 are
  separate images (ranging vs gesture). URLs verified 2026-11-09.
- **Load flow (harness):** `bazel run //ghidra:analyze_fw` imports the image,
  lays down SRAM / peripheral / sysmem blocks + the reset vector
  (`ghidra/scripts/SetupMemoryMap.py`), auto-analyzes, and writes
  `out/<build>/mmio_accesses.csv` + `out/<build>/decomp/`.

## Established (build 2268) — see 01/02

- [x] **Inter-chip bus:** **I2C0 @1 MHz on PB6/PB7** = S3KM111L control (dev
      **0x40**, +0x44 for a 2nd SoC); **SPI0 on PA4/5/7 + DMA ch0** = bulk data
      path. The GD32 ADC is unused. Details + function addrs: [`02-bus.md`](02-bus.md).
- [x] **Register access model:** I2C, 8-bit reg addr, **16-bit big-endian**
      value; write=`FUN_08004814`, read=`FUN_080047d8`; table-driven init
      (`FUN_08004cb8` boot, `FUN_08001300` per-mode). [`01-s3km111l.md`](01-s3km111l.md).

## Not yet established

- [ ] **Concrete S3KM111L register defaults:** addresses/values are assembled in
      SRAM at boot from flash; extract the flash source (entry layout + dev addr
      are known). Next big step.
- [ ] **SPI0/DMA direction + format:** CH0CTL DIR bit, SPI0 CTL0 config word.
- [ ] **Data-ready mechanism:** no EXTI hits — polled or DMA-complete driven?
- [ ] **Signal chain location:** how much (range FFT, CFAR, tracking, gesture
      classifier) runs on the GD32 vs. arrives pre-processed from the S3KM111L.
- [ ] **UART protocol (J2):** USART1 frame headers/tails, command IDs, reports,
      baud (`FUN_080087e4`).
- [ ] **2268 vs 2269 diff:** shared = radar plumbing; differing = ranging vs
      gesture app logic.

## Faithfulness policy

Register *offsets and semantics* stay out of the reconstructed map until backed
by `out/*/mmio_accesses.csv` cross-checked against the GD32E230 SVD / user
manual — and, where possible, a logic-analyzer capture of the real inter-chip
bus at boot. Peripheral addresses in `09-handoff.md` are from memory and must be
confirmed against the SVD before use.

## Open follow-ups for the harness

- `flake.lock` is not yet committed (needs one `nix flake lock` after the Nix
  overlay is live) — until then the Nix inputs float on their branch tips.
- SVD-Loader + SPL FID signatures (handoff §"Ghidra setup" steps 4–5) are still
  manual; candidates to fold into `SetupMemoryMap.py` / a second preScript once
  the GD32E230 SVD and an SPL build are in hand.
