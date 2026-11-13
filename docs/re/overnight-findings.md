# Rd-03E module exploration — overnight autonomous session (2026-11-13)

Goal: figure out the module's capabilities and how to achieve: (B) calibrated
distance from our firmware, (C) chirp-config experiments for range/precision,
(D) two-module bistatic setup for synthetic aperture.

Fixed constraint tonight: target (retroreflector) is static at ~2 m; no distance
sweep possible (user asleep). So: decomp analysis, live register instrumentation,
frame-data analysis (FFT), register mapping, and roadmap scoping.

## Key open question
The header field I extracted (`+0x6a = XX>>3`, XX = 3rd frame byte) does NOT track
distance: XX≈0xf0 at both 2 m and 12 ft. So the real range is elsewhere. Round 1
instruments the running vendor to read its live parsed struct + output and find
the true range source.

## Findings log

### Round 1 — range source found (2026-11-13 night)
Resolved vendor pointers from flash constant pool:
- struct base DAT_08002944 = DAT_080028e8 = 0x20000FFE (ch0; +0x6a=0x20001068, +0x6c=0x2000106A)
- mode byte DAT_08002868 = DAT_080028e4 = 0x20001070 (ONE byte selects both frame-parse sub-mode AND output scaling)
- filter coeffs DAT_08002078 = 0x200012D4
- SPI buffer DAT_0800244c = 0x200013A4 (matches DMA MADDR)

Live reads @2m target:
- mode @0x20001070 = **0** → FUN_08002450 case 0: local_24 = +0x6a, scale local_28 = 1000.
- +0x6a (range field) samples: 11,31,31,31,14,31,7,31 → **31 = no-detection default; 7/11/14 = real target bin** (intermittent detection). +0x6c = 0 (unused in mode 0).
- KEY: +0x6a DOES encode range; earlier "doesn't track distance" was from averaging in the 31 no-detection defaults. At 12ft (out of range) it was always 31.
- Filter coeffs @0x200012d4: ints 717,30,425,30,20,2,8,5,10,0 then floats 0.5,0.5,0.85,0.15,40.0,6.0,40.0,9.0 (0.85/0.15 = IIR smoother).

So the from-source distance recipe: extract +0x6a=XX>>3, drop 31 (no-detect), smooth
the real bins, scale bin->distance. Need the bin->distance scale (FUN_08004fa0).

### Round 2 — FFT confirms range bin (KEY WIN)
Captured 6 full 100-byte frames from vendor buffer @0x200013a4, 2m target.
Frame layout (100 B): [0]AA [1]30 [2]XX(range) [3]18 [4-5]80 00 [6-7]val [8-9]7FFF(marker)
[10..95] samples (BE int16) [96-99] trailer ..55.
- FFT (BE int16, drop DC+marker), averaged over 12 frames -> clean peak at **bin 11**
  (both real-FFT and complex-IQ). Matches +0x6a detections (7-14, centered 11).
- So +0x6a IS the range bin. 31 = no-detection default. bin 11 = 2m.
- Scale: ~18.2 cm/bin (bin11=2m). => range resolution ~18cm => FMCW BW ~824 MHz. Plausible.
- 45-pt FFT fits easily on GD32 (future: sub-bin precision / our own peak-find).

### From-source distance recipe (implemented R3)
1. bin = XX>>3 per frame; 31 => no detection.
2. scan both frames in the ring; if any bin<31, that's a detection.
3. IIR-smooth detected bins; report type2 distance_cm = round(18.2*bin), type0 if
   no detection for a while.
Calibration (18.2 cm/bin) anchored on the single bin11=2m point — refine with a
sweep later (bin at 1m/1.5m/2.5m).

### Round 3 — from-source distance readout (works, noisy)
Implemented detected_bin() + IIR smoothing + coasting + bin->cm (18.2 cm/bin) in
firmware; DBG_REPORT @0x200000c8 mirrors [type,val,bin,tick] for SWD readback.
@2m target: type-2 detections at bin 7 and bin 14-18 (bimodal), mean ~247cm vs
true 200cm. Gating 5<=bin<28 removes close clutter (bin 0-4) + no-detect (30-31).
VERDICT: our firmware reports a real from-source distance in the right ballpark,
but it's noisy because the SoC's per-frame detection of this reflector is
bimodal (multipath). Clean output needs the vendor's multi-target tracker
(FUN_08004fa0, 0x5c-stride track structs) OR a cleaner target + a distance sweep
to calibrate properly. Both are follow-ups (sweep needs the user).

### Round 4 — STC/threshold registers mapped (experiment)
Lowered boot-table STC curve 0x23-0x2f to 60% of stock, flashed, measured @2m:
- stock: 6/12 type-2, bins {7,14,16,18}
- STC@60%: 10/20 type-2, bins {7×6,11,14,18,21}
=> 0x20-0x2f IS the per-range-bin detection threshold (STC) curve. Lowering it
makes the strong target return (bin ~7) detected more consistently, and admits
more far-bin noise. Detection RATE (~50%) is limited by SoC frame cadence, not
threshold. Lever confirmed for range/sensitivity tuning (C). Reverted to stock.
NOTE: target's consistent bin is ~7 (not 11 from the noisy FFT avg); true range
calibration still needs a clean distance sweep.

### Round 5 — FFT of frame samples >> the SoC's noisy bin (BIG WIN)
Frame layout refined: [0-5] header, [6-7] DC/ref (~0xA5xx), [8-9] 0x7FFF marker,
[10-95] 43 BE-int16 range-profile samples, [96-99] checksum..55. 45 samples not
/4 => stock stream is ONE RX channel.
Per-frame DFT peak of samples[10:95]: **bin 11 on ALL 12 frames, std=0.0**.
vs +0x6a (XX>>3): std 4.8 + 30/31 dropouts. => compute range ourselves by DFT
of the samples -> stable peak, no tracker needed. 43-pt DFT fits on GD32.
Implementing fixed-point DFT peak-finder in firmware (R6).
