// MA's visualiser in place of the cover: the skill relays MA's waveform, each frame due `ahead` ms after it arrives.
// MilkDrop draws it, as in MA's app; a plain line where the Echo can't run MilkDrop.
// The choice outlives the page, as the lyrics one.
const viz = { on: false, frames: [], gotAt: 0, askedAt: 0, mode: '', milk: null, presets: null, preset: '', presetAt: 0,
  timing: null, slow: [] };
const PRESET_MS = 30000;
const LEAD_MS = 150;    // the picture lagged the Echo's sound by about this much
const MIN_FPS = 25;     // a preset slower than this on this Echo is skipped from then on
try { viz.slow = JSON.parse(localStorage.getItem('vizSlow')) || []; } catch (e) {}
function setViz(on) {
  Object.assign(viz, { on, frames: [] });
  try { localStorage.setItem('viz', on ? '1' : ''); } catch (e) {}
  $('cover').style.display = on ? 'none' : '';
  $('viz').style.display = on ? 'block' : 'none';
  askViz();
  if (!on) return;
  if (!viz.mode) loadMilkdrop();
  requestAnimationFrame(drawViz);
}
function askViz() { viz.askedAt = Date.now(); post('action', { action: 'viz', on: viz.on }); }
function takeViz(v) {
  if (v.clear) { viz.frames = []; return; }
  const now = performance.now(); viz.gotAt = Date.now();
  v.frames.forEach(([ahead, wave]) =>
    viz.frames.push({ due: now + ahead - LEAD_MS, wave: Uint8Array.from(atob(wave), ch => ch.charCodeAt(0)) }));
}
async function loadMilkdrop() {
  viz.mode = 'loading';
  try {
    if (!document.createElement('canvas').getContext('webgl2')) throw new Error('no WebGL2');
    const butterchurn = (await import('/bell/html/lib/butterchurn.min.js')).default;
    await new Promise((ok, fail) => document.head.appendChild(Object.assign(document.createElement('script'),
      { src: '/bell/html/lib/butterchurnPresets.min.js', onload: ok, onerror: fail })));
    const pack = window.butterchurnPresets;
    viz.presets = (pack.getPresets ? pack : pack.default).getPresets();
    // MA's "low" quality: half size, stretched by CSS.
    const c = $('viz'); c.width = c.clientWidth / 2; c.height = c.clientHeight / 2;
    viz.milk = butterchurn.createVisualizer(new AudioContext(), c,
      { width: c.width, height: c.height, pixelRatio: 1, meshWidth: 32, meshHeight: 24, outputFXAA: false });
    nextPreset(0);
    viz.mode = 'milk';
    if (viz.slow.length) post('action', { action: 'viz_slow', presets: viz.slow });
  } catch (e) { viz.mode = 'line'; }
}
function nextPreset(blend) {
  const all = Object.keys(viz.presets), fast = all.filter(n => !viz.slow.includes(n));
  const names = fast.length ? fast : all;
  viz.preset = names[Math.floor(Math.random() * names.length)];
  viz.milk.loadPreset(viz.presets[viz.preset], blend);
  Object.assign(viz, { presetAt: Date.now(), timing: null });
}
// Frames per 3 s of drawing, from 3 s after a preset loads (its blend and shader build).
function timePreset(t) {
  if (Date.now() - viz.presetAt < 3000) return;
  const tm = viz.timing = viz.timing || { from: t, n: 0 };
  if (t - tm.from < 3000) { tm.n++; return; }
  if (tm.n * 1000 / (t - tm.from) >= MIN_FPS) { viz.timing = null; return; }
  viz.slow.push(viz.preset);
  try { localStorage.setItem('vizSlow', JSON.stringify(viz.slow)); } catch (e) {}
  post('action', { action: 'viz_slow', presets: [viz.preset] });
  nextPreset(1);
}
function drawViz(t) {
  if (!viz.on) return;
  requestAnimationFrame(drawViz);
  // The skill restarted or lost MA: ask again.
  if (!audio.paused && Date.now() - Math.max(viz.gotAt, viz.askedAt) > 3000) askViz();
  const now = performance.now();
  let k = 0;
  while (k + 1 < viz.frames.length && viz.frames[k + 1].due <= now) k++;
  viz.frames.splice(0, k);
  const f = viz.frames[0], wave = !audio.paused && f && f.due <= now ? f.wave : null;
  if (viz.mode === 'milk') {
    if (!wave) { viz.timing = null; return; }    // paused: the picture stays
    if (Date.now() - viz.presetAt > PRESET_MS) nextPreset(2.7);
    viz.milk.render({ audioLevels: { timeByteArray: wave, timeByteArrayL: wave, timeByteArrayR: wave } });
    timePreset(t);
  } else if (viz.mode === 'line') drawLine(wave);
}
function drawLine(wave) {
  const c = $('viz'), w = c.clientWidth, h = c.clientHeight;
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  if (!wave) return;
  ctx.beginPath();
  for (let i = 0; i < wave.length; i++) {
    const y = h / 2 + (wave[i] - 128) / 128 * h * 0.45;
    if (i) ctx.lineTo(i * w / (wave.length - 1), y); else ctx.moveTo(0, y);
  }
  ctx.strokeStyle = '#fff'; ctx.lineWidth = Math.max(2, h / 150); ctx.stroke();
}
// A tap on MilkDrop picks another preset.
function startViz() { $('viz').onclick = () => { if (viz.mode === 'milk') nextPreset(1); }; }
