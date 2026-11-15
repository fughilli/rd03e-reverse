# Rd-03E capabilities & roadmap (from-source)

Synthesis of the from-source bring-up + the overnight exploration (2026-11-13).
Scopes the three goals: **B** calibrated distance, **C** chirp-parameter
experiments for range/precision, **D** distributed coherent aperture — a
self-organizing, infrastructure-free radar network (updated 2026-11-15).

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

## D — distributed coherent aperture (self-organizing radar network)
**Vision (2026‑11‑15 design pass).** The end goal is not a fixed two‑module rig
but a **self‑organizing network**: throw N nodes into a scene and have them
self‑localize, self‑synchronize, and cooperatively measure scatterers (people)
with **no central processor, no infrastructure anchors, and no placed
reference target** — robust to the nodes themselves being mobile. Each node is
both a 24 GHz FMCW radar *and* a radio (UWB + WiFi + BLE it controls to the
PHY), and that dual role is the key enabler: the nodes range and phase‑reference
to **each other**, so the network is self‑referential.

### D.0 — the three sync requirements (they differ by ~1000×)
"Time sync" is really three separate problems at f_c ≈ 24.1 GHz (λ ≈ 12.4 mm,
B ≈ 250 MHz → range bin ΔR = c/2B ≈ 0.6 m):

| Leg | Buys | Requirement | Met by |
|---|---|---|---|
| **Timing** (chirp‑start / ADC trigger) | range‑bin registration | ~1/10 bin → **~0.4 ns** | UWB TWR (0.1–1 ns), WiFi FTM (~1 ns) |
| **Frequency** (syntonization) | no phase drift over the CPI | ε < **0.2 ppb**/10 ms (≈8 ppb per 250 µs chirp) | disciplined‑oscillator loop (D.2) |
| **Phase** (carrier coherence) | **the aperture gain itself** | **< λ/20 ≈ 0.31 rad ≈ 2.1 ps ≈ 0.62 mm** | two‑way phase transfer + self‑cal (D.3) |

The gating leg is **carrier phase**: timing good enough for range (sub‑ns) is
~5 whole carrier cycles of phase error. Two caveats soften the λ/20 wall:
(1) it is a budget on the *residual jitter/drift of the relative phase* over one
coherent interval — a **static** offset (cable, PLL start phase, fixed geometry)
calibrates out once; (2) coherent gain rolls off as **exp(−σ_φ²)**, not a cliff —
0.31 rad costs ~0.4 dB, 0.5 rad ~1 dB, 1 rad loses half the array.
**Geometry = phase:** a node‑position error is indistinguishable from a phase
error, so relative positions must be known to the **same λ/20 ≈ 0.6 mm**. UWB
ranging gives *cm* (≈16 λ) — enough to *initialize* but not to image coherently;
the sub‑mm closes only via phase self‑calibration (D.3).

### D.1 — self-localization (relative geometry), no anchors
Nodes measure pairwise range (UWB two‑way ranging) and ideally angle (the radar's
own 2‑RX AoA, or UWB PDoA). Solve the constellation with **cooperative
localization** — belief‑propagation / factor‑graph message passing (SPAWN‑style,
Wymeersch et al.), which is inherently **distributed**: each node computes its own
position belief from neighbor messages, no central solver. Recovered up to a
rigid‑transform gauge (fine — self‑defined frame); range‑only flip ambiguity is
killed by adding angle. Output accuracy is **cm**; this is the init, not the
coherent geometry.

### D.2 — frequency discipline / real-time oscillator trim (master-less)
**Trim the reference, not the VCO.** Carrier, chirp slope, and ADC clock all
derive from the 24 MHz crystal; disciplining the 24 MHz keeps all three coherent
together. (The SoC VCO fine‑tune `0x70` is comb‑locked and only fixes the
carrier — use it for *coarse* carrier co‑location, not fine discipline.)
Actuators, best first: **VCTCXO swap** (±1–5 ppm pull, sub‑ppb via DAC; TCXO base
keeps temp drift small so a small‑pull part also gives fine resolution) →
**varactor‑pull the existing crystal** (lightest mod) → **DCXO** (I²C) →
**DSP‑only** numerical NCO/resample correction (free given we already hold the raw
frames). Steer from a ≥16‑bit DAC on the host MCU.

**Coarse‑analog + fine‑digital split (the move that makes it easy):** don't ask
the analog trim for 0.2 ppb. Its only job is to keep |Δf| small enough that the
carrier phase doesn't wrap **within one chirp** → Δf·T_chirp < 0.5 → for 250 µs,
**Δf < ~2 kHz ≈ 0.08 ppm** — a loose target any varactor/VCTCXO + slow PI loop
holds through temperature. The DSP/self‑cal then removes the residual down to
λ/20. Loop BW ~0.1–10 Hz (tracks temp/aging; the within‑CPI residual is
autofocus's job). **Master‑less:** drive the setpoint by **consensus
syntonization** (average‑consensus / pulse‑coupled‑oscillator coupling over the
pairwise two‑way exchange) so there is no single master to lose when a node moves
or drops — self‑healing.

### D.3 — phase closure WITHOUT a placed reference (self-cal)
The corner reflector is **not required** in the deployed system. Borrow
**self‑calibration** from radio interferometry: with **≥3 nodes** the **closure
phase** (sum of baseline phases around a triangle) is *independent of every
per‑node phase/position error* — a calibration‑free observable. Iterate between a
scene estimate and the per‑node phase/geometry (the VLBI "selfcal" loop); the
radar analogs are **PGA / entropy‑ or sharpness‑minimizing autofocus** on
**targets of opportunity** (static clutter — a wall corner, metal furniture). The
solver *discovers* its reference instead of us placing one. ≥4 nodes →
overdetermined → robust. **People are poor references** (extended, moving, low
RCS): anchor self‑cal on **static clutter** and use **Doppler to separate movers
(tracked) from the static set (calibrates phase)** — conveniently, the thing
that's hard to image is the thing you don't need for calibration. Honest walls:
the cm→0.6 mm gap closes only here; it's a **nonconvex** solve that can stall in a
**sparse/featureless** scene, and it needs good init (UWB geometry + the two‑way
pilot) — don't run it cold.

### D.4 — mobility = tracking, not one-shot calibration
Mobile nodes → time‑varying geometry and phase → run the whole estimator as a
recursive **cooperative SLAM** (factor graph over time). Mobility **helps**
(a moving node is itself a synthetic aperture; more baselines over time =
structure‑from‑motion for the constellation) and **hurts** (motion must be known
to λ/20 over the dwell, which ranging can't give). **Put an IMU on each node:**
it supplies the sub‑mm *short‑term* motion within a CPI (drifts long‑term;
radar/UWB corrects the drift). Net: mobility **bounds the coherent dwell**, so
lean on short coherent CPIs + non‑coherent integration across them.

### D.5 — distributed computation & the Tier‑1/Tier‑2 fork
Every estimator above has a distributed form (BP localization, consensus sync,
consensus/ADMM/information‑filter fusion). The real decision is the fusion tier:
- **Tier 1 — cooperative, *non‑coherent* (robust, buildable now).** Self‑localize
  (cm), each node images/SARs its own sector, share **detections/tracks** (tiny
  bandwidth), fuse by consensus Kalman/information filter. **No inter‑node carrier
  phase, no self‑cal, no reflector.** Robust to mobility and dropout, trivially
  distributed. Gives multi‑view coverage, occlusion robustness, better *people
  localization* — but **no coherent super‑aperture** (angular resolution = each
  node's own aperture + motion‑SAR, not the big inter‑node baseline).
- **Tier 2 — coherent distributed aperture (stretch).** Add D.2+D.3 so cross‑node
  baselines combine coherently → fine cross‑range from the large baseline.
  Nonconvex joint solve (scene × per‑node phase × sub‑mm position) and
  **bandwidth‑heavy** (coherent combination wants raw complex data shared, not
  just tracks).

**Strategy:** ship Tier 1 as the robust baseline; make Tier 2 opt‑in where scene
structure, node count (≥3–4), and link budget allow. They share the same
self‑localization + sync front‑end.

### D.6 — near-term stepping stones (single-board, no network sync)
These validate the sample→image pipeline *before* any inter‑node coherence:
1. **4‑RX real aperture for free.** The S3KM111L has 4 RX (gains `0x61–0x64`,
   per‑channel frame stride 0x72). Capturing all 4 channels' samples gives a
   small real aperture → angle/azimuth on **one** board.
2. **HLK‑LD2450 — angle for free (on the bench).** Sibling 24 GHz / 250 MHz FMCW
   module, same architecture family (AIoT mmwave SoC + GD32‑class host; `PA9`/
   `TX`/`RX`/USB `DM`‑`DP` pads; 256000‑baud UART). **1 TX + 2 RX** microstrip
   array, already does 2‑D multi‑target tracking (up to **3 targets' X/Y + speed**,
   azimuth ±60° / pitch ±35°). The 2 RX give azimuth **AoA (receive digital
   beamforming / DoA)** on one coherent, shared‑clock board — the easiest route to
   real‑aperture angle. *SWD tap:* no debug pads are broken out; the exposed
   `DM/DP` (USB) and `PA9` (USART0) are the host's **bootloader** interfaces — try
   **USB DFU** (dump without soldering) or serial ISP first; true SWD = solder to
   the host MCU's **PA13 (SWDIO)/PA14 (SWCLK)** or a via on those nets. A back‑side
   **u.FL fed by a lumped matching network off a host‑IC pad** is present; lumped
   discretes rule out 24 GHz, and two on‑board `24.000 MHz` crystals make it most
   likely a **buffered ~24 MHz reference/sync port** (verify by scoping for a CW
   tone) — i.e. a candidate **wired shared‑clock bootstrap** for D.2.
3. **Monostatic SAR.** One module on a known track (rail/robot) + positions;
   back‑project the raw BE‑int16 samples offline → a synthetic‑aperture image.
   Proven, sync‑free, validates the pipeline.

### D.7 — RF-only (UWB+WiFi+BLE) person tracking, WITHOUT the FMCW chips
Device‑free (no tag on the person) sensing off the sync/backhaul radios — nearly
free as **joint comms‑and‑sensing** (ISAC; 802.11bf is standardizing WiFi
sensing). Feasibility is very modality‑dependent (range res = c/2B):
- **UWB — the device‑free workhorse.** 500 MHz–1 GHz BW → **0.3–0.1 m** range
  res. A person perturbs each link's **CIR**; background‑subtract the static
  channel and multilaterate the moving reflection across a node mesh →
  **~0.3–1 m** localization of 1–2 *moving* people. Some UWB parts do impulse
  radar / respiration directly.
- **WiFi CSI — augmentation.** 20/40/160 MHz → 7.5/3.75/~1 m res; good for
  **presence, motion, counting, coarse zones, breathing** via ML on CSI, but
  low‑res, environment/training‑dependent, poor multi‑person.
- **BLE — presence only.** 2 MHz → ~75 m res (useless for device‑free ranging);
  RSSI fluctuation detects gross motion. (BLE Channel Sounding is for *tagged*
  device‑to‑device ranging, not device‑free.)

**Verdict:** **Yes** for presence + counting + coarse (0.3–1 m) tracking of a
*small number of moving* people, device‑free, led by the **UWB mesh** with WiFi
CSI support. **No** for the fine, multi‑person, micro‑motion/vitals‑rich
measurement the FMCW array gives — multi‑person separation and still‑person
sensing are exactly where RF‑only degrades and FMCW wins. Best used as an
**always‑on, low‑power cueing/coarse layer** that wakes and steers the FMCW nodes
for fine measurement. **Tagged** tracking (person carries a UWB tag) is a
different, far easier problem — 10–30 cm, trivially multi‑person by ID — if that
assumption is ever acceptable.

**Reflector's remaining role:** demoted to a **bring‑up/validation tool** — place
it at a known spot to confirm the blind self‑cal (D.3) converges to the right
answer, then remove it and verify the scene‑driven solve lands in the same place.

**Recommended order:** B ✅ → C exp #1–3 (master the chirp + SDR map) → D.6
single‑board angle (4‑RX or **LD2450**) → D.6 monostatic SAR → stand up D.7 RF‑only
coarse tracking (cheap, uses radios already present) → D.1 cooperative
localization → D.2 disciplined‑oscillator loop (bootstrap wired via the LD2450
u.FL if it's the 24 MHz ref) → **Tier 1** non‑coherent network → **Tier 2**
coherent self‑cal only where scene/nodes/link allow.

## Open items / to verify with the operator
- SDR capture of the baseline chirp for the C register map.
- The 12 ft range-extension test with lowered STC + widened chirp.
- **LD2450 u.FL:** scope the center pin for a CW tone to confirm it's the
  ~24 MHz reference (D.2 wired bootstrap) vs an IF/other; trace the net to the
  host‑IC pad / crystal.
- **LD2450 SWD/dump:** try USB DFU on `DM/DP` first; else serial ISP on `PA9`;
  else solder SWD to host `PA13`/`PA14`. Confirm read‑protection (RDP) state.
- **D.2 actuator choice:** VCTCXO swap vs varactor‑pull; which radio carries the
  two‑way phase pilot; DAC resolution vs pull range.
- Node count / scene assumptions for Tier‑2 self‑cal (≥3–4 nodes, static‑clutter
  structure); IMU availability per node for mobility (D.4).
