# Register/field interpretation + HITL validation plan

What the recovered values *mean*, separated by confidence. The structural RE is
done (`01`–`03`); this is physical interpretation, where a HITL rig with a
debugger is worth more than static analysis — so the low-confidence items below
are framed as experiments to run on the rig, not guesses to ship.

## High confidence (from code structure)

| Reg(s) | Meaning | Evidence |
| --- | --- | --- |
| `0x40` | **Control**: `0x4207` config-enable set (first write), `0x0207` run (last). Bit `0x4000` = config gate. | written first & last in boot table |
| `0x61..0x64` | **4 RX-channel gains** (all `0x0021`) | four identical consecutive regs; module has 4-element RX arrays |
| `0x20..0x2f` | **Range-dependent threshold / STC curve** (`0x1ddc,0x1d5e,0x1ce2,0x1c64,0x17d0,0x16d4,0x15dc×7`) | monotone-decreasing-then-flat; identical in ranging & gesture |
| `0x01`,`0x04`,`0x05`,`0x44` | **Config registers**, not results (HW read‑back = boot‑table writes; accessors `FUN_08004c1c/4c36` return constants `0x40/0x20`). Distance comes from the SPI frame stream — see `05`. | `FUN_08004b7c/4c60/4c1c/4c36` |

## UART protocol (confirmed in code)

- **Command RX parser** (`FUN_08001450`): matches header **`FD FC FB FA`** (bytes
  at `.data` `0x20000006`), then a **2-byte little-endian length** bounded `< 55`,
  then the command body. Footer `04 03 02 01`.
- **Report TX**: **`55 55 AA AA | type(1) | value16(2 LE)`**, 7 bytes, recovered
  on hardware (`05-calibration.md`). The manual's `F4 F3 F2 F1` framing is not in
  the binary.
- The output value in `FUN_08002450` derives from a per-mode base `1000`
  right-shifted by 3 or 6 depending on the detection mode at `0x20001070` —
  i.e. three output scalings selected by mode — combined with the range parsed
  from the SPI frame (`+0x6c`/`+0x6a`). HW calibration: `value16 ≈ 0.886·cm +
  19.4` (`05-calibration.md`).

## Hypotheses — resolve on the HITL rig

The chirp/frame registers differ between ranging (2268) and gesture (2269):

| Reg | 2268 (ranging) | 2269 (gesture) | round-decimal hint |
| --- | --- | --- | --- |
| `0x46` | `0x7530` = 30000 | 30000 (same) | 30000 — chirp/timing param |
| `0x4a` | `0x4e20` = 20000 | 20000 (same) | 20000 |
| `0x50` | `0x0fa0` = 4000 | 4000 (same) | 4000 |
| `0x43` | `0xd4c0` = 54464 | `0xc520` = 50464 | Δ = 4000 |
| `0x48` | `0x5078` = 20600 | `0x6d60` = 28000 | — |
| `0x4c` | `0xc0f8` = 49400 | `0x9470` = 38000 | — |
| `0x52` | `0xeee0` = 61152 | `0xe2e0` = 58080 | — |
| `0x70` | `0x2ea0` = 11936 | `0x26a0` = 9888 | — |

For a 24 GHz FMCW SoC (≤1 GHz sweep), these are almost certainly **start/stop
frequency (VCO/PLL codes), ramp time, ADC sample rate, and #samples/#chirps** —
but which is which needs measurement, not inference.

### Experiments (rig + SWD debugger + logic analyzer)

1. **Register read-back / live map.** Halt via SWD (J3), dump the S3KM111L regs
   through the GD32's I2C (or probe I2C0 PB6/7 with the LA during boot) to
   confirm the written table and catch any calibration-time modifications.
2. **Poke-and-observe.** Sweep one differing register (e.g. `0x48`, `0x4c`) and
   watch the UART report / the on-air chirp (HackRF or the c6-sdr bench) — maps
   each register to range, resolution, or frame rate directly.
3. **Result decode.** ✅ Done (`05-calibration.md`): known-distance sweep gave
   `value16 ≈ 0.886·cm + 19.4`. Distance is parsed from the SPI frame, not the
   I2C regs.
4. **SPI0 capture.** Bring up SPI0+DMA RX from source and stream the raw frames
   out over USART1 to validate the ported parser (`FUN_0800207c`) against live
   bytes; confirm SPI role/direction via SWD read of SPI0 CTL0 on the running
   vendor firmware (the one open bus question, `02-bus.md`).

With those, the hypotheses table above becomes a named register map and the
firmware's report path can be finalized.
