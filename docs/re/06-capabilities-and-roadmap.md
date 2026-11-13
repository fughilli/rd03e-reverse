# Rd-03E capabilities & roadmap (from-source)

Synthesis of the from-source bring-up + the overnight exploration (2026-11-13).
Scopes the three goals: **B** calibrated distance, **C** chirp-parameter
experiments for range/precision, **D** two-module bistatic → synthetic aperture.

## What works from source today
Our GD32 firmware (branch `firmware/from-source-bringup`, PR #1) is a complete
radar host built entirely from RE'd code:
- Boots the GD32E230 @72 MHz; brings up I²C0, USART1, SPI0, DMA.
- Configures the S3KM111L over I²C with the 80-entry boot table (SoC ACKs).
- Receives the SoC's framed stream as **SPI0 slave + DMA ch1** into a ring.
- Parses frames, extracts the **range bin**, smooths it, and reports distance
  on USART1 in the vendor's `55 55 AA AA <type> <lo> <hi>` format.
- `DBG_REPORT @0x200000C8` mirrors `[type, value, bin, tick]` for SWD readback.

## The signal chain (how range actually flows) — KEY RE RESULT
The S3KM111L does the radar DSP internally and streams **~100-byte frames** over
SPI (sync `AA`, header `30 XX 18 80 00`, then BE-int16 samples, trailer `..55`):
- **`XX` (3rd byte) >> 3 = the range bin (0..31)**. `31` (and `30`) = the SoC's
  *no-detection* default. A real in-range target is a low bin (e.g. bin ~7–11
  for the ~2 m retroreflector). Confirmed two ways: (1) the vendor's live parsed
  field `+0x6a` = `XX>>3`; (2) an FFT of the frame's sample payload peaks at the
  same bin.
- The vendor converts bin→distance with a scale (`local_28`, mode-dependent) and
  a **multi-target tracking/smoothing filter** (`FUN_08004fa0`, 0x5c-stride track
  structs, IIR coeffs incl. 0.85/0.15 — read live at `0x200012D4`). We do *not*
  port that filter; a light IIR + gating is enough for a usable readout.
- The **mode byte @`0x20001070`** (= `DAT_08002868` = `DAT_080028e4`) selects
  both the frame sub-parse and the output scaling. Observed mode = 0.

## B — calibrated distance
**Status:** from-source distance works and is in the right ballpark (~2–2.5 m for
the 2 m target), but noisy: the SoC's per-frame detection of a lone retroreflector
is intermittent (~50 %) and bimodal (strong bin ~7 plus multipath bins 14–18).
That intermittency/multipath is exactly what the vendor's tracker cleans up.

**To finish B well (needs the bench operator):**
1. **Distance sweep** — place a clean target at marked ranges (0.5/1.0/1.5/2.0/
   2.5 m), log `DBG_REPORT.bin` at each. Fit `distance = a·bin + b`. One point
   tonight (bin ~7–11 ≈ 2 m) isn't enough to pin the slope (and the stock calib
   `value ≈ 0.886·cm + 19.4` is for the vendor's *filtered* output, not the bin).
2. **Light tracker** — replace the single IIR with a tiny per-bin histogram /
   mode-tracker over a sliding window to reject the multipath outliers without
   porting the full `FUN_08004fa0`. ~50 lines of firmware.
3. Optional: **host-side sub-bin FFT** — the frame already carries the raw
   samples; a 45-pt FFT fits on the GD32 (<1 KB) for sub-bin interpolation if we
   want finer than ~18 cm resolution.

## C — chirp / FMCW parameter experiments (range & precision)
The S3KM111L is undocumented, so register meanings are physics-grounded
hypotheses; **the on-air chirp must be measured to confirm them** (use the
c6-sdr / HackRF bench — see `amd-rig-sdr-ble-phy-re`).

**Confirmed tonight:**
- `0x20–0x2f` = **STC / per-range-bin detection threshold curve** (lowering it
  made the strong return detect more consistently; admits more far-bin noise).
  → primary lever for **sensitivity / max detection range**.
- `0x44 = 0x0020` (32) = **# range bins** (matches the 5-bit bin field).
- `0x61–0x64 = 0x0021` = the 4 RX-channel gains.

**Hypotheses (confirm by SDR):** the mode-specific regs that differ ranging↔
gesture — `0x43, 0x48, 0x4c, 0x52, 0x70` — are the **VCO/PLL start/stop codes,
ramp slope, and ADC timing**; the shared round numbers `0x46=30000, 0x4a=20000,
0x50=4000` are chirp/sample **timing**. Range resolution = c/(2·BW); so widening
the frequency sweep (the `0x43/0x48/0x4c` group) improves **precision**, and
more bins/longer chirp + lower STC improves **range** (bounded by SNR∝1/R⁴ and
ADC Nyquist).

**Experiment plan (each: change one reg in `BOOT_TABLE`, flash, observe):**
1. Point a HackRF/c6-sdr at the module; record the baseline chirp (start f, BW,
   ramp time, PRF). This calibrates the register→physics map directly.
2. Sweep `0x44` (bins): does the frame length / bin resolution change? → confirms
   #bins and the range-per-bin.
3. Sweep the `0x43/0x48/0x4c` group one at a time; watch the chirp start/stop/
   slope on the SDR → identify start-freq vs bandwidth vs slope.
4. With a target at a fixed distance, widen BW and measure whether two close
   targets resolve (precision) and whether a far target appears (range).
5. Lower the far STC thresholds (`0x2a–0x2f`) + verify the original 12 ft target
   now detects — the original range-extension goal.
`detected_bin`'s gate (`5..28`) and the 18.2 cm/bin scale are assumptions that
change with the chirp config — re-derive per experiment from the SDR + a sweep.

## D — two-module bistatic → synthetic aperture
**Hard truths first.** The S3KM111L is an *integrated* FMCW TX+RX SoC; true
coherent bistatic reception of module A's chirp by module B needs: (a) B in
RX-only mode locked to A's ramp, (b) a **shared reference clock** (the two
modules have independent crystals → ppm frequency offset → phase decorrelation
in µs), and (c) a **trigger line** so B's ramp starts with A's. Without a shared
10/40 MHz reference + trigger, coherent bistatic SAR is not achievable, and we
only get the SoC's processed range bins, not coherent raw I/Q across modules.

**Realistic staged path:**
1. **Monostatic SAR first (no two-module sync).** One module moved along a known
   track (rail/robot), collecting frames + positions. The frames already carry
   the **raw BE-int16 samples** (the a5.. payload) — phase-bearing. Back-project
   offline → a synthetic-aperture image. This is a proven technique and sidesteps
   the sync problem entirely; it validates the sample→image pipeline.
2. **Shared-clock bistatic.** If the board exposes the SoC reference clock and a
   chirp-trigger/sync pin, feed A's reference + trigger to B (RX-only). Then B's
   frames are coherent with A → bistatic range/Doppler. Needs hardware probing
   of the module (find REF_CLK / SYNC pads) — a board-level investigation.
3. **MIMO/aperture via the 4 RX channels.** The SoC already has 4 RX channels
   (gains `0x61–0x64`); the frame is per-channel (stride 0x72 in the struct).
   Capturing all 4 channels' samples gives a small real aperture *for free* (no
   second module) — angle/azimuth estimation, a stepping stone to SAR.

**Recommended order:** finish B (sweep + light tracker) → C experiment #1–3 to
master the chirp (and get the SDR map) → D step 3 (4-RX aperture, no new HW) →
D step 1 (monostatic SAR on a track) → D step 2 (bistatic, only if the board
exposes clock+trigger).

## Open items / to verify with the operator
- Distance sweep for B calibration and to pin bin→cm.
- SDR capture of the baseline chirp for the C register map.
- Whether the module board exposes a reference-clock / chirp-sync pad (D).
- The 12 ft range-extension test with lowered STC + widened chirp.
