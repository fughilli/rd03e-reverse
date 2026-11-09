# Reverse-engineering targets

Ai-Thinker's shipping Rd-03E firmware, downloaded from the vendor docs page
([docs.ai-thinker.com/en/Rd-03E](https://docs.ai-thinker.com/en/Rd-03E/)),
plus any SWD flash dumps K pulls off a physical module.

**The vendor images are proprietary and gitignored (`/targets/rd03e/`) — never
committed here.** Re-fetch with `./fetch.sh`.

## Builds (verified on the vendor page, 2026-11-09)

| Build | Variant | Vendor file | Local name | Version |
| --- | --- | --- | --- | --- |
| 2268 | Precision ranging | `（2268）XenP102RM01.hex` | `2268_XenP102RM01.hex` | V1.0.0 |
| 2269 | Gesture recognition | `（2269）XenG102 ST.hex` | `2269_XenG102ST.hex` | V1.0.0 |

## Facts

- **Format:** Intel HEX (`.hex`) — Ghidra's Intel Hex loader places the bytes at
  their recorded addresses automatically. Base is flash at `0x08000000`; no
  manual base offset needed (unlike a raw `.bin`).
- **Target:** GigaDevice GD32E230K8 — ARM Cortex-M23 (ARMv8-M Baseline, Thumb),
  64 KB flash / 8 KB SRAM. Load in Ghidra as `ARM:LE:32:Cortex`.
- **Likely stripped:** vendor Keil/armclang output, so expect `FUN_xxxx` names —
  the register evidence (`out/*/mmio_accesses.csv`) and the SPL FID / SVD passes
  carry the analysis, not symbol names. (Contrast the esp32-reverse blobs, which
  are unstripped relocatable objects.)

## Why both builds

2268 (ranging) and 2269 (gesture) share the same radar front-end bring-up but
differ in application logic. Diffing them (BinDiff / Ghidra Version Tracking)
separates the **shared S3KM111L init + bus plumbing** (identical in both — the
undocumented radar register map we're after) from the per-application signal
processing. See the analysis plan in [`../docs/re/09-handoff.md`](../docs/re/09-handoff.md).

## Host tools (reference, not RE targets)

The vendor also ships Windows host tools on the same page — `xenp102rm01_tool.zip`
(ranging) and `xeng102sttool_v1.2.0.0_20230517_1_.zip` (gesture). Useful for
cross-checking the UART protocol against live hardware; not fetched by `fetch.sh`.
