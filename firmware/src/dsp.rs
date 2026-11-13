//! Minimal fixed-point DFT range-finder for the S3KM111L range profile.
//!
//! The SoC's own range field (`+0x6a` = frame byte `XX>>3`) is noisy and drops
//! out intermittently. But the frame also carries the raw **range-profile
//! samples** (43 BE int16, bytes [10:95]); a DFT of those peaks dead-stable on
//! the target bin (HW: std 0.0 vs the SoC field's 4.8). So we compute range
//! ourselves — no multi-target tracker needed. See docs/re/06.
//!
//! N = 43 is prime, so this is a direct DFT (not a radix FFT): ~21·43 ≈ 900
//! MACs/frame, trivial at 72 MHz / ~20 Hz frames.

/// Range-profile sample count per frame.
pub const N: usize = 43;

// Q15 tables: COS[m] = round(cos(2π·m/43)·32767), SIN likewise.
static COS: [i32; N] = [
    32767, 32418, 31378, 29669, 27328, 24404, 20960, 17070, 12815, 8288, 3584,
    -1197, -5952, -10580, -14982, -19066, -22743, -25935, -28574, -30605,
    -31983, -32680, -32680, -31983, -30605, -28574, -25935, -22743, -19066,
    -14982, -10580, -5952, -1197, 3584, 8288, 12815, 17070, 20960, 24404, 27328,
    29669, 31378, 32418,
];
static SIN: [i32; N] = [
    0, 4771, 9440, 13908, 18080, 21866, 25186, 27970, 30157, 31702, 32570,
    32745, 32222, 31012, 29141, 26649, 23589, 20026, 16037, 11705, 7125, 2392,
    -2392, -7125, -11705, -16037, -20026, -23589, -26649, -29141, -31012,
    -32222, -32745, -32570, -31702, -30157, -27970, -25186, -21866, -18080,
    -13908, -9440, -4771,
];

const HALF: usize = N / 2; // 21 usable bins

/// All DFT magnitude² values for bins `1..=HALF` of the DC-removed samples.
fn spectrum(x: &[i16; N]) -> [i64; HALF + 1] {
    let mean: i32 = x.iter().map(|&v| v as i32).sum::<i32>() / N as i32;
    let mut mags = [0i64; HALF + 1];
    for k in 1..=HALF {
        let mut re: i64 = 0;
        let mut im: i64 = 0;
        for n in 0..N {
            let xn = (x[n] as i32 - mean) as i64;
            let idx = (k * n) % N;
            re += xn * COS[idx] as i64;
            im -= xn * SIN[idx] as i64;
        }
        let r = re >> 15;
        let i = im >> 15;
        mags[k] = r * r + i * i;
    }
    mags
}

/// Peak DFT bin (`1..=N/2`) of the DC-removed samples — the target range bin —
/// and its magnitude² (for a presence/strength threshold).
pub fn peak_bin(x: &[i16; N]) -> (u8, i64) {
    let mags = spectrum(x);
    let mut best_k = 1;
    for k in 1..=HALF {
        if mags[k] > mags[best_k] {
            best_k = k;
        }
    }
    (best_k as u8, mags[best_k])
}

/// Peak bin refined to sub-bin precision (Q4: bin × 16) via parabolic
/// interpolation of the peak and its two neighbours, plus the peak magnitude².
/// δ = 0.5·(a−c)/(a−2b+c), clamped to ±0.5 bin. Sharpens range below the
/// ~18 cm bin spacing without a bigger transform.
pub fn peak_bin_q4(x: &[i16; N]) -> (u16, i64) {
    let mags = spectrum(x);
    let mut best_k = 1;
    for k in 1..=HALF {
        if mags[k] > mags[best_k] {
            best_k = k;
        }
    }
    let mut q4 = (best_k as i32) << 4;
    if best_k > 1 && best_k < HALF {
        let a = mags[best_k - 1];
        let b = mags[best_k];
        let c = mags[best_k + 1];
        let denom = a - 2 * b + c; // < 0 at a peak
        if denom != 0 {
            let delta_q4 = (8 * (a - c) / denom) as i32; // 16·0.5·(a−c)/denom
            q4 += delta_q4.clamp(-8, 8);
        }
    }
    (q4 as u16, mags[best_k])
}
