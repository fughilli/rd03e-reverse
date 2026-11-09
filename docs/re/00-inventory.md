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

## Not yet established (needs evidence before it goes in the register map)

- [ ] **Inter-chip bus:** which of SPI0/SPI1/I2C0/I2C1 drives the S3KM111L, on
      which GPIO pins, plus any CE/reset/IRQ lines. Trace from `Reset_Handler`
      through RCU clock-enable + GPIO AF config.
- [ ] **S3KM111L init sequence:** every register address/value written at boot,
      in order — this is effectively the chip's undocumented register map.
- [ ] **Data path:** polled / DMA / EXTI-on-ready; buffer sizes and formats.
- [ ] **Signal chain location:** how much (range FFT, CFAR, tracking, gesture
      classifier) runs on the GD32 vs. arrives pre-processed from the S3KM111L.
- [ ] **UART protocol (J2):** frame headers/tails, command IDs, report formats,
      baud — start at the USART IRQ handler.
- [ ] **2268 vs 2269 diff:** shared functions = radar plumbing; differing =
      ranging vs gesture app logic (BinDiff / Ghidra Version Tracking).

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
