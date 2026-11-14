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

## B — calibrated distance  ✅ SOLVED (better than the vendor)
**We compute range ourselves by DFT-ing the frame's range-profile samples** and
taking the peak bin, instead of using the SoC's noisy `+0x6a` field. On HW the
DFT bin is **dead-stable (std 0.00)** where the SoC field was std 4.8 + dropouts
— so we get a rock-steady reading with **no multi-target tracker** (`FUN_08004fa0`
is not needed). `dsp.rs`: fixed-point N=43 DFT, ~900 MACs/frame.
**Bin→range CALIBRATED on HW (2026‑11‑14) — overturns the earlier guess.**
A corner reflector measured at three marked ranges (0.699 m / 1.283 m / 2.743 m
= 27.5″ / 50.5″ / 9 ft) lands on bins 14 / 11 / 3. So **higher bin = CLOSER**,
and a linear fit gives:

> **R[m] ≈ 3.2908 − 0.1825·bin**   (coarse, single‑frame)

The old "bin 11 = 2 m ⇒ 18.2 cm/bin ascending" was wrong in **both scale and
sign**. The from‑source firmware readout (`main.rs` `DBG_REPORT[1]`) now uses this
formula; bin 11 reads **128 cm**, not 200 cm. (This coarse slope shifts with the
chirp config — re‑derive per C‑experiment — but the absolute sweep below is
config‑independent.)

Polish done:
- **Sub‑bin interpolation** (parabolic peak fit) is in `dsp.rs` — sub‑cm bin
  precision.
- **Presence**: `DBG_REPORT[3]` carries the peak DFT magnitude; threshold it for
  type‑0 (empty‑scene mag sets the cutoff).

This DFT path is also the enabler for C and D below: we now have the **full
range profile** in firmware, not just a single bin.

## B+ — stepped‑frequency ABSOLUTE range  ✅ SOLVED (config‑independent)
The coarse bin→range above is a *relative* scale that moves with the chirp. For
an **absolute**, chirp‑independent range we exploit reg **`0x70` (dev 0x20) =
the S3KM111L VCO fine‑tune DAC** (characterised on HW: **3583 Hz/count**; it
locks only on a discrete comb of codes ~500 wide every 1000 counts — good codes
`{12000,12250,13000,13250,14000,14250,15000,15250}`; off‑comb codes collapse SNR
or show a fixed bin‑13 TX→RX leakage tone).

At each lock the **beat phase at the target bin ramps linearly with the carrier
code**; the slope is proportional to absolute range with **no λ/2 ambiguity**:

> **R[m] = slope[rad/count] × 6658.2 + 0.0662**   (R_sys ≈ −6.6 cm cable/delay)

Calibrated against the same three reflector positions — all fell on
`slope = 1.502e‑4 · R` with **±3 mm** residual. Because the radar sees much
static room clutter brighter than a far reflector, the peak bin ≠ the target, so
a bin's stepped range is cross‑checked against its coarse `3.29 − 0.183·bin`
(±0.4 m) to reject ambiguous/multi‑scatterer bins; the operator can also pin a
bin. PLL relock after a `0x70` write is < 1 frame (~15 ms); fixed‑carrier phase
is coherence R = 1.0000, frame‑to‑frame jitter 0.014 rad ≈ 14 µm.

### Firmware carrier‑hop (`RADAR_RTT v2`) — continuous, dropout‑free
Driving the `0x70` hop from the **host** over SWD destabilised the (parasitic‑
power) CMSIS‑DAP link within seconds. The fix is to hop **in firmware**: the MCU
cycles the comb itself, discards a settle frame, and **tags each pushed record
with the carrier index** (record header is now epoch u16 + seq u8 + carrier u8;
the host reads the 16‑byte magic to pick the `buf` offset, `_v2` ⇒ 0x70). The
host then reads the *normal‑rate* stream, groups frames by carrier tag, and
re‑fits the stepped‑freq slope every frame — **absolute range with zero extra
SWD traffic**. Validated: **60 s soak, 0 link respawns, ~9.6 Hz range updates,
max gap 161 ms, std 9 mm** at 0.93 m. Config lives in the `hop_*` fields of the
`RADAR_RTT` control block; the web workspace exposes it as the "HW hop" button.

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
`detected_bin`'s gate (`5..28`) is a chirp‑config assumption — re‑derive per
experiment from the SDR + a sweep. The bin→range scale is now calibrated
(`R ≈ 3.29 − 0.183·bin`, see B) but still config‑dependent; the stepped‑freq
sweep (B+) gives the config‑independent absolute. **`0x70` is now fully
characterised** (VCO fine‑tune, 3583 Hz/count, discrete lock comb) — the first
of the chirp‑group registers nailed down on HW, and the basis of B+.

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

4. **HLK‑LD2450 — angle/aperture for free (new HW on the bench, 2026‑11‑14).**
   A Hi‑Link **LD2450** (24 GHz, 250 MHz BW, FMCW) is a sibling module built on
   the *same architecture family* as the Rd‑03E: an AIoT mmwave radar SoC + a
   GD32‑class host MCU (castellated pads label `PA9`/`TX`/`RX`, GD32 USART0
   naming; UART out at 256000 baud, plus USB `DM/DP`). Crucially it carries a
   **1 TX + 2 RX microstrip antenna** array (the user's "3 antenna ports") and
   already does 2‑D multi‑target tracking: it reports up to **3 targets' X/Y +
   speed**, FoV azimuth ±60° / pitch ±35°. The two RX give azimuth **angle‑of‑
   arrival** (phase/amplitude monopulse) — i.e. *receive digital beamforming /
   DoA*, not steerable phased‑array TX. That is exactly the angle dimension D
   wants, on one coherent board with a shared clock — so RE'ing the LD2450's
   per‑RX raw frames is a faster path to real‑aperture angle than the Rd‑03E's
   4‑RX capture, and a far easier stepping stone than two‑module bistatic.
   *Next step if pursued:* dump its firmware over SWD and map its SPI/UART frame
   the same way we did the Rd‑03E (`docs/re/01`–`05`); the control model is
   likely near‑identical.

**Recommended order:** finish B (sweep + light tracker) ✅ → C experiment #1–3 to
master the chirp (and get the SDR map) → **LD2450 angle path (above)** or D step 3
(4‑RX aperture, no new HW) → D step 1 (monostatic SAR on a track) → D step 2
(bistatic, only if the board exposes clock+trigger).

## Open items / to verify with the operator
- Distance sweep for B calibration and to pin bin→cm.
- SDR capture of the baseline chirp for the C register map.
- Whether the module board exposes a reference-clock / chirp-sync pad (D).
- The 12 ft range-extension test with lowered STC + widened chirp.
