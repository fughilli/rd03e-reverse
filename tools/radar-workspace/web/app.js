"use strict";
// Rd-03E radar workspace client. All DSP runs here so processing is tunable
// live without reflashing. Frames arrive as [epoch, seq, hex96]; samples are
// 43 BE int16 at byte offset 10 of each frame.

const N = 43, HALF = 21;
const COS = new Float32Array(N * N), SIN = new Float32Array(N * N);
for (let k = 0; k < N; k++) for (let n = 0; n < N; n++) {
  const a = -2 * Math.PI * k * n / N;
  COS[k * N + n] = Math.cos(a); SIN[k * N + n] = Math.sin(a);
}
// Fast-track lives in the agent: it stays on the fixed carrier, integrates the
// tracked bin's phase at full rate, and interleaves single-carrier excursions
// to keep the absolute anchor fresh (no 0.88 s dropout). The browser just plots
// the 'range' messages it streams back.
const FAST_WIN_S = 8;             // tracker time window (s)
const fastHist = [];              // {t, r} range samples from agent 'range' msgs
let trackBin = null;              // bin the agent fast-tracker is following

// ---- state ----
let epoch = -1, paused = false, nframes = 0;
const COLS = 320;
const waterfall = [];               // ring of Float32Array(HALF) log-mags
const slow = [];                    // {t, ph} unwrapped phase samples
let slowPrev = 0, slowAccum = 0;    // phase unwrap accumulator
let domBin = 10;
let latest = null;                  // latest {samples, spectrum, peak, phase, mag}

// ---- DSP ----
function decodeFrame(hex) {
  const b = new Uint8Array(48);
  for (let i = 0; i < 48; i++) b[i] = parseInt(hex.substr(i * 2, 2), 16);
  // samples at offset 10, 43 BE int16
  const s = new Float32Array(N);
  let mean = 0;
  for (let k = 0; k < N; k++) {
    let v = (b[10 + 2 * k] << 8) | b[11 + 2 * k];
    if (v >= 32768) v -= 65536;
    s[k] = v; mean += v;
  }
  mean /= N;
  for (let k = 0; k < N; k++) s[k] -= mean;
  return s;
}

function analyze(s) {
  const spec = new Float32Array(HALF + 1);
  let peak = 1, peakMag = -1, peakRe = 0, peakIm = 0;
  for (let k = 1; k <= HALF; k++) {
    let re = 0, im = 0;
    for (let n = 0; n < N; n++) { re += s[n] * COS[k * N + n]; im += s[n] * SIN[k * N + n]; }
    const m = re * re + im * im;
    spec[k] = m;
    if (m > peakMag) { peakMag = m; peak = k; peakRe = re; peakIm = im; }
  }
  // parabolic sub-bin
  let sub = peak;
  if (peak > 1 && peak < HALF) {
    const a = spec[peak - 1], b2 = spec[peak], c = spec[peak + 1];
    const den = a - 2 * b2 + c;
    if (den !== 0) sub = peak + 0.5 * (a - c) / den;
  }
  return { spec, peak, sub, mag: Math.sqrt(peakMag), phase: Math.atan2(peakIm, peakRe) };
}

function onFrame(rec) {
  const [ep, seq, hex] = rec;
  if (ep !== epoch) { epoch = ep; onEpochChange(); }
  const s = decodeFrame(hex);
  const a = analyze(s);
  latest = { s, a, seq };
  nframes++;
  domBin = a.peak;

  // waterfall column (log magnitude, normalized later)
  const col = new Float32Array(HALF);
  for (let k = 1; k <= HALF; k++) col[k - 1] = Math.log10(1 + a.spec[k]);
  waterfall.push(col);
  while (waterfall.length > COLS) waterfall.shift();

  // slow-time unwrapped phase at the peak bin
  let d = a.phase - slowPrev;
  while (d > Math.PI) d -= 2 * Math.PI;
  while (d < -Math.PI) d += 2 * Math.PI;
  slowAccum += d; slowPrev = a.phase;
  slow.push({ t: performance.now(), ph: slowAccum });
  while (slow.length > 600) slow.shift();
}

function onEpochChange() {
  waterfall.length = 0; slow.length = 0; slowAccum = 0; slowPrev = 0;
  const dot = document.getElementById('edot');
  dot.style.background = '#ff5c7a';
  setTimeout(() => dot.style.background = 'var(--accent2)', 400);
}

// breathing estimate via autocorrelation of detrended slow-time phase
function breathingBPM() {
  const n = slow.length;
  if (n < 128) return null;
  const t0 = slow[0].t, dur = (slow[n - 1].t - t0) / 1000;
  if (dur < 4) return null;
  // resample to uniform grid
  const M = 256, y = new Float32Array(M);
  for (let i = 0; i < M; i++) {
    const tt = t0 + (i / (M - 1)) * (slow[n - 1].t - t0);
    // linear search (n small)
    let j = 0; while (j < n - 1 && slow[j + 1].t < tt) j++;
    const a = slow[j], b = slow[Math.min(j + 1, n - 1)];
    const f = b.t > a.t ? (tt - a.t) / (b.t - a.t) : 0;
    y[i] = a.ph + f * (b.ph - a.ph);
  }
  // detrend (remove linear fit)
  let sx = 0, sy = 0, sxx = 0, sxy = 0;
  for (let i = 0; i < M; i++) { sx += i; sy += y[i]; sxx += i * i; sxy += i * y[i]; }
  const slope = (M * sxy - sx * sy) / (M * sxx - sx * sx), inter = (sy - slope * sx) / M;
  for (let i = 0; i < M; i++) y[i] -= inter + slope * i;
  // autocorrelation, find peak in 0.1..0.7 Hz band
  const fs = M / dur;
  const lagMin = Math.floor(fs / 0.7), lagMax = Math.ceil(fs / 0.1);
  let best = 0, bestv = 0;
  for (let lag = lagMin; lag <= Math.min(lagMax, M - 1); lag++) {
    let c = 0; for (let i = 0; i + lag < M; i++) c += y[i] * y[i + lag];
    if (c > bestv) { bestv = c; best = lag; }
  }
  if (!best) return null;
  return 60 * fs / best;
}

// ---- rendering ----
function fit(cv) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight || cv.height;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  }
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}
function cmap(v) { // 0..1 -> rgb (blue->cyan->yellow->red)
  v = Math.max(0, Math.min(1, v));
  const r = Math.max(0, Math.min(1, 1.5 * v - 0.4)) * 255;
  const g = Math.max(0, Math.min(1, 1.4 * Math.sin(Math.PI * v))) * 255;
  const b = Math.max(0, Math.min(1, 1.1 - 1.6 * v)) * 255;
  return [r | 0, g | 0, b | 0];
}

function drawScope() {
  const { ctx, w, h } = fit(document.getElementById('scope'));
  ctx.clearRect(0, 0, w, h);
  if (!latest) return;
  const s = latest.s; let mx = 1;
  for (let k = 0; k < N; k++) mx = Math.max(mx, Math.abs(s[k]));
  ctx.strokeStyle = '#2a3644'; ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke();
  ctx.strokeStyle = '#39d0d8'; ctx.lineWidth = 1.5; ctx.beginPath();
  for (let k = 0; k < N; k++) {
    const x = k / (N - 1) * w, y = h / 2 - s[k] / mx * (h / 2 - 6);
    k ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  }
  ctx.stroke();
}

function drawProfile() {
  const { ctx, w, h } = fit(document.getElementById('prof'));
  ctx.clearRect(0, 0, w, h);
  if (!latest) return;
  const spec = latest.a.spec; let mx = 1;
  for (let k = 1; k <= HALF; k++) mx = Math.max(mx, Math.log10(1 + spec[k]));
  const bw = w / HALF;
  for (let k = 1; k <= HALF; k++) {
    const v = Math.log10(1 + spec[k]) / mx, bh = v * (h - 10);
    ctx.fillStyle = (k === latest.a.peak) ? '#f5a742' : '#2f6b70';
    ctx.fillRect((k - 1) * bw + 1, h - bh, bw - 2, bh);
  }
  ctx.fillStyle = '#8aa0b6'; ctx.font = '10px monospace';
  ctx.fillText('bin ' + latest.a.peak + '  ~' + (latest.a.sub * 18.2).toFixed(0) + ' cm', 6, 12);
}

function drawFall() {
  const cv = document.getElementById('fall'); const { ctx, w, h } = fit(cv);
  ctx.clearRect(0, 0, w, h);
  const n = waterfall.length; if (!n) return;
  const img = ctx.createImageData(COLS, HALF);
  let gmax = 0.001;
  for (const c of waterfall) for (let k = 0; k < HALF; k++) gmax = Math.max(gmax, c[k]);
  for (let x = 0; x < n; x++) {
    const c = waterfall[x];
    for (let k = 0; k < HALF; k++) {
      const [r, g, b] = cmap(c[k] / gmax);
      const px = ((HALF - 1 - k) * COLS + x) * 4;
      img.data[px] = r; img.data[px + 1] = g; img.data[px + 2] = b; img.data[px + 3] = 255;
    }
  }
  const off = document.createElement('canvas'); off.width = COLS; off.height = HALF;
  off.getContext('2d').putImageData(img, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(off, 0, 0, n, HALF, 0, 0, w * n / COLS, h);
  ctx.fillStyle = '#8aa0b6'; ctx.font = '10px monospace';
  ctx.fillText('near', 2, h - 3); ctx.fillText('far', 2, 11);
}

function drawSlow() {
  const { ctx, w, h } = fit(document.getElementById('slow'));
  ctx.clearRect(0, 0, w, h);
  const n = slow.length; if (n < 2) return;
  let mn = 1e9, mx = -1e9;
  for (const p of slow) { mn = Math.min(mn, p.ph); mx = Math.max(mx, p.ph); }
  const rng = (mx - mn) || 1;
  ctx.strokeStyle = '#54d98c'; ctx.lineWidth = 1.5; ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const x = i / (n - 1) * w, y = h - 6 - (slow[i].ph - mn) / rng * (h - 12);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  }
  ctx.stroke();
  ctx.fillStyle = '#8aa0b6'; ctx.font = '10px monospace';
  ctx.fillText('phase span ' + (rng).toFixed(2) + ' rad  (' + (rng * 12.4 / (4 * Math.PI)).toFixed(2) + ' mm)', 6, 12);
}

function render() {
  if (!paused) { drawScope(); drawProfile(); drawFall(); drawSlow(); }
  drawTracker();   // sweep-driven; always refresh so it updates while paused too
  // readouts
  if (latest) {
    document.getElementById('pbin').textContent = latest.a.peak;
    document.getElementById('pcm').textContent = (latest.a.sub * 18.2).toFixed(0);
    document.getElementById('pmag').textContent = latest.a.mag.toFixed(0);
    document.getElementById('phase').textContent = latest.a.phase.toFixed(3) + ' rad';
    document.getElementById('nframes').textContent = nframes;
    const bpm = breathingBPM();
    document.getElementById('breath').textContent = bpm ? bpm.toFixed(1) + ' / min' : '—';
  }
  requestAnimationFrame(render);
}

// ---- websocket ----
let ws;
function connect() {
  ws = new WebSocket('ws://' + location.host + '/');
  const c = document.getElementById('conn');
  ws.onopen = () => { c.textContent = 'live'; c.className = 'pill ok'; };
  ws.onclose = () => { c.textContent = 'disconnected'; c.className = 'pill bad'; setTimeout(connect, 1500); };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.t === 'frames') {
      if (!paused) for (const r of m.recs) onFrame(r);
      document.getElementById('drops').textContent = m.drops;
    } else if (m.t === 'status') {
      document.getElementById('fps').textContent = m.fps;
      document.getElementById('epoch').textContent = m.epoch;
      document.getElementById('decimv').textContent = m.decim;
      document.getElementById('upwr').textContent = m.up_wr;
    } else if (m.t === 'ready') {
      document.getElementById('decimv').textContent = m.decim;
    } else if (m.t === 'sweeping') {
      sweeping = true;
      document.getElementById('absbig').textContent = 'measuring…';
      document.getElementById('abssub').innerHTML = '<small>cycling carrier comb</small>';
      const st = document.getElementById('trk_state'); if (st) st.textContent = '… cycling carrier comb';
    } else if (m.t === 'absrange') {
      showAbs(m);
    } else if (m.t === 'range') {
      trackBin = m.bin;
      const now = performance.now();
      fastHist.push({ t: now, r: m.r, anc: !!m.anc });
      const cut = now - FAST_WIN_S * 1000;
      while (fastHist.length && fastHist[0].t < cut) fastHist.shift();
      document.getElementById('absr').textContent = m.r.toFixed(2) + ' m';
    } else if (m.t === 'ftstatus') {
      if (!m.ok) {
        const st = document.getElementById('trk_state');
        if (st) st.textContent = '— ' + (m.reason || 'no anchor');
      }
    } else if (m.t === 'bridge') {
      c.textContent = m.state; c.className = 'pill ' + (m.state === 'agent_up' ? 'ok' : 'bad');
      // the agent restarts in normal mode after a watchdog respawn; re-arm tracking
      if (m.state === 'agent_up' && tracking) {
        setTimeout(() => { if (tracking) send({ t: 'fasttrack', on: true, bin: pinnedBin }); }, 1500);
      }
      if (m.state === 'agent_up' && hopping) {
        setTimeout(() => { if (hopping) send({ t: 'hop', on: true, bin: pinnedBin }); }, 1500);
      }
    }
  };
}
function send(o) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }

// ---- stepped-frequency absolute range ----
let sweeping = false, tracking = false, hopping = false, pinnedBin = null;
const absHist = [];                 // sweep history for the tracker panel
const ABS_MAX = 160;
function doSweep() { if (!sweeping) send({ t: 'sweep' }); }
// the target we follow in each sweep: pinned bin if set & present, else strongest
function trackedOf(entry) {
  if (pinnedBin !== null && entry.all) {
    const d = entry.all.find((x) => x.bin === pinnedBin);
    if (d) return d;
  }
  return entry.best || null;
}
function drawTracker() {
  const { ctx, w, h } = fit(document.getElementById('trk'));
  ctx.clearRect(0, 0, w, h);
  const RMAX = 3.6, x0 = 34, pad = 12;
  const now = performance.now(), WIN = FAST_WIN_S * 1000;
  const yOf = (r) => h - pad - (Math.min(Math.max(r, 0), RMAX) / RMAX) * (h - 2 * pad);
  const xOf = (tt) => x0 + ((tt - (now - WIN)) / WIN) * (w - x0 - 4);
  ctx.font = '9px monospace';
  for (let r = 0; r <= 3.5; r += 0.5) {               // range grid + labels
    const y = yOf(r);
    ctx.strokeStyle = '#1e2834'; ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(w, y); ctx.stroke();
    ctx.fillStyle = '#8aa0b6'; ctx.fillText(r.toFixed(1), 2, y + 3);
  }
  if (!absHist.length && !fastHist.length) {
    ctx.fillStyle = '#8aa0b6'; ctx.font = '11px monospace';
    ctx.fillText('no data — Sweep (one shot) or Track (live 65 Hz)', x0 + 10, h / 2);
    return;
  }
  for (const e of absHist) {                          // faint: every clean target (scene)
    if (e.t < now - WIN) continue;
    const x = xOf(e.t);
    for (const d of e.targets) { ctx.fillStyle = 'rgba(84,217,140,0.33)'; ctx.fillRect(x - 1, yOf(d.range) - 1, 2, 2); }
  }
  for (const e of absHist) {                          // sweep anchors: tracked range (orange ◻)
    if (e.t < now - WIN) continue;
    const d = trackedOf(e); if (!d) continue;
    ctx.fillStyle = '#f5a742'; ctx.fillRect(xOf(e.t) - 2, yOf(d.range) - 2, 4, 4);
  }
  if (fastHist.length > 1) {                          // full-rate track line
    ctx.strokeStyle = pinnedBin !== null ? '#f5a742' : '#39d0d8'; ctx.lineWidth = 1.3; ctx.beginPath();
    let started = false;
    for (const p of fastHist) {
      if (p.t < now - WIN) continue;
      const x = xOf(p.t), y = yOf(p.r);
      started ? ctx.lineTo(x, y) : ctx.moveTo(x, y); started = true;
    }
    ctx.stroke();
  }
  let cur = null, src = '', rate = 0;
  if (fastHist.length) {
    cur = fastHist[fastHist.length - 1].r; src = 'fixed-carrier';
    const span = fastHist.length > 1 ? (fastHist[fastHist.length - 1].t - fastHist[0].t) / 1000 : 0;
    rate = span > 0 ? (fastHist.length - 1) / span : 0;
  } else if (absHist.length) { const d = trackedOf(absHist[absHist.length - 1]); if (d) { cur = d.range; src = 'sweep'; } }
  if (cur !== null) {
    ctx.fillStyle = pinnedBin !== null ? '#f5a742' : '#39d0d8'; ctx.font = 'bold 22px monospace';
    ctx.fillText(cur.toFixed(3) + ' m', x0 + 10, 26);
    ctx.fillStyle = '#8aa0b6'; ctx.font = '10px monospace';
    ctx.fillText((cur * 39.37).toFixed(1) + ' in · ' + src + (rate ? ' · ' + rate.toFixed(0) + ' Hz' : '')
      + (trackBin !== null ? ' · bin ' + trackBin : ''), x0 + 10, 40);
  }
  ctx.fillStyle = '#8aa0b6'; ctx.font = '9px monospace';
  ctx.fillText('-' + FAST_WIN_S + 's', x0 + 2, h - 3); ctx.fillText('now', w - 20, h - 3);
}
function showAbs(m) {                 // called once per sweep result: record + render
  sweeping = false;
  const st = document.getElementById('trk_state');
  if (!m.ok) {
    document.getElementById('absbig').textContent = '—';
    document.getElementById('absr').textContent = '—';
    document.getElementById('abssub').innerHTML = '<small>' + (m.reason || 'no carrier lock') + '</small>';
    if (st) st.textContent = '— no lock';
    return;
  }
  absHist.push({ t: performance.now(), targets: m.targets || [], all: m.all || [], best: m.best });
  while (absHist.length > ABS_MAX) absHist.shift();
  if (st) st.textContent = (hopping ? '● HW carrier-hop · ' : tracking ? '● tracking 65 Hz · ' : '✓ ')
    + (m.targets ? m.targets.length : 0) + ' clean targets';
  renderAbsPanel(m);
}
function renderAbsPanel(m) {          // DOM render only (also re-run on pin toggle)
  const big = document.getElementById('absbig'), sub = document.getElementById('abssub');
  const tl = document.getElementById('abstargets'), pill = document.getElementById('absr');
  let primary = null;
  if (pinnedBin !== null && m.all) primary = m.all.find((d) => d.bin === pinnedBin) || null;
  if (!primary) primary = m.best;
  if (primary) {
    big.textContent = primary.range.toFixed(3) + ' m';
    pill.textContent = primary.range.toFixed(2) + ' m';
    sub.innerHTML = '<small>' + (pinnedBin === primary.bin ? '📌 ' : '') + 'bin ' + primary.bin
      + ' · R²=' + primary.r2.toFixed(3) + ' · ' + (primary.range * 39.37).toFixed(1) + ' in'
      + (primary.ok ? '' : ' · <span style="color:var(--hot)">unverified</span>') + '</small>';
  } else {
    big.textContent = 'no clean target'; pill.textContent = '—';
    sub.innerHTML = '<small>' + (m.targets ? m.targets.length : 0) + ' candidates</small>';
  }
  tl.innerHTML = '';                  // clean targets, near→far, clickable to pin
  for (const d of (m.targets || [])) {
    const row = document.createElement('div');
    const on = d.bin === pinnedBin;
    row.style.cssText = 'font-size:10px;padding:2px 4px;border-bottom:1px dotted var(--grid);cursor:pointer;border-radius:3px'
      + (on ? ';background:#143' : '');
    row.innerHTML = (on ? '📌 ' : '') + 'bin ' + d.bin + ' · <b style="color:var(--ok)">'
      + d.range.toFixed(3) + ' m</b> · ' + (d.range * 39.37).toFixed(0) + '" · R²' + d.r2.toFixed(2);
    row.onclick = () => {
      pinnedBin = (pinnedBin === d.bin) ? null : d.bin;
      renderAbsPanel(m);
      if (tracking) send({ t: 'fasttrack', on: true, bin: pinnedBin });   // retarget agent
      if (hopping) send({ t: 'hop', on: true, bin: pinnedBin });          // retarget HW-hop
    };
    tl.appendChild(row);
  }
}

// ---- controls ----
document.getElementById('dec').oninput = (e) => {
  document.getElementById('decLbl').textContent = e.target.value;
  send({ t: 'decim', n: +e.target.value });
};
document.getElementById('flush').onclick = () => send({ t: 'flush' });
document.getElementById('sweep').onclick = () => doSweep();
const hopBtn = () => document.getElementById('hop');
const trkBtn = () => document.getElementById('track');
function setTrack(on) {
  tracking = on;
  trkBtn().textContent = on ? 'Tracking…' : 'Track 65Hz';
  trkBtn().className = on ? 'go' : '';
}
function setHop(on) {
  hopping = on;
  hopBtn().textContent = on ? 'HW hopping…' : 'HW hop (firmware)';
  hopBtn().className = on ? 'go' : '';
}
document.getElementById('track').onclick = () => {
  if (!tracking && hopping) { send({ t: 'hop', on: false }); setHop(false); }  // mutually exclusive
  setTrack(!tracking);
  if (tracking) { fastHist.length = 0; send({ t: 'fasttrack', on: true, bin: pinnedBin }); }
  else { send({ t: 'fasttrack', on: false }); trackBin = null; }
};
document.getElementById('hop').onclick = () => {
  if (!hopping && tracking) { send({ t: 'fasttrack', on: false }); setTrack(false); }  // mutually exclusive
  setHop(!hopping);
  if (hopping) { fastHist.length = 0; send({ t: 'hop', on: true, bin: pinnedBin }); }
  else { send({ t: 'hop', on: false }); trackBin = null; }
};
document.getElementById('pause').onclick = (e) => {
  paused = !paused; e.target.textContent = paused ? 'Resume' : 'Pause';
};
document.getElementById('write').onclick = () => {
  const dev = +document.getElementById('dev').value;
  const reg = parseInt(document.getElementById('reg').value, 16);
  const val = parseInt(document.getElementById('val').value, 16);
  if (isNaN(reg) || isNaN(val)) return;
  send({ t: 'reg_write', dev, reg, val });
};

// preset register quick-writes (from docs/re). Click fills the form.
const PRESETS = [
  ['#bins 0x44', 0x44, 0x0020],
  ['STC near 0x23', 0x23, 0x1ddc],
  ['STC far 0x2f', 0x2f, 0x15dc],
  ['STC far 0x2f LOW (sensitive)', 0x2f, 0x0800],
  ['RX gain0 0x61', 0x61, 0x0021],
  ['chirp 0x43', 0x43, 0xd4c0],
  ['chirp 0x48', 0x48, 0x5078],
  ['chirp 0x4c', 0x4c, 0xc0f8],
  ['chirp 0x70', 0x70, 0x2ea0],
];
const pl = document.getElementById('presets');
for (const [name, reg, val] of PRESETS) {
  const b = document.createElement('button');
  b.textContent = name;
  b.onclick = () => {
    document.getElementById('reg').value = reg.toString(16);
    document.getElementById('val').value = val.toString(16).padStart(4, '0');
  };
  pl.appendChild(b);
}

connect();
requestAnimationFrame(render);
