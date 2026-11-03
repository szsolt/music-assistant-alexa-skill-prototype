// Lyrics from MA in place of the title panel: synced (LRC) lines follow the song, plain ones just show.
// The choice outlives the page, which renews itself every ~28 min.
const lyr = { on: false, item: null, lines: [], at: -1, touchedAt: 0 };
function setLyrics(on) {
  lyr.on = on;
  try { localStorage.setItem('lyrics', on ? '1' : ''); } catch (e) {}
  if (on) askLyrics(); else { $('right').classList.remove('ly'); $('lyNote').textContent = ''; }
}
function askLyrics() {
  Object.assign(lyr, { item: wave.item, lines: [], at: -1 });
  $('right').classList.remove('ly'); $('lyNote').textContent = 'Looking for lyrics…';
  post('action', { action: 'lyrics', item: wave.item || '' });
}
// MA's lyrics as lines: synced ones with their time in ms, else the plain ones with at -1.
function parseLyrics(l) {
  const lines = [];
  (l.lrc || '').split('\n').forEach(line => {
    const m = line.match(/^\s*\[(\d+):(\d+(?:\.\d+)?)\](.*)/);
    if (m) lines.push({ at: (Number(m[1]) * 60 + Number(m[2])) * 1000, text: m[3].trim() });
  });
  const plain = !lines.length;
  if (plain) (l.plain || '').split('\n').forEach(text => lines.push({ at: -1, text }));
  return { lines, plain };
}
function showLyrics(l) {
  const { lines, plain } = parseLyrics(l);
  if (!lines.some(x => x.text)) { $('lyNote').textContent = 'No lyrics for this song'; return; }
  const body = $('lyBody'); body.textContent = ''; body.className = plain ? 'plain' : '';
  lines.forEach(x => {
    const d = document.createElement('div'); d.className = 'ly'; d.textContent = x.text || ' ';
    body.appendChild(d);
  });
  body.scrollTop = 0;
  Object.assign(lyr, { lines: plain ? [] : lines, at: -1 });
  $('lyNote').textContent = ''; $('right').classList.add('ly');
}
function lyricsTick() {
  if (!lyr.on || !lyr.lines.length) return;
  const t = trackPosition();
  let at = -1; lyr.lines.forEach((x, k) => { if (x.at <= t) at = k; });
  if (at === lyr.at) return;
  const rows = $('lyBody').children;
  if (rows[lyr.at]) rows[lyr.at].classList.remove('on');
  lyr.at = at;
  if (!rows[at]) return;
  rows[at].classList.add('on');
  // Leave the lines alone for a while after a touch.
  if (Date.now() - lyr.touchedAt > 4000) {
    const body = $('lyBody');
    body.scrollTop = rows[at].offsetTop - (body.clientHeight - rows[at].offsetHeight) / 2;
  }
}
function startLyrics() {
  $('lyBody').onpointerdown = () => { lyr.touchedAt = Date.now(); };
  $('lyBody').onscroll = () => { if (Date.now() - lyr.touchedAt < 1000) lyr.touchedAt = Date.now(); };
}

if (typeof module === 'object') module.exports = { parseLyrics };   // for the Node tests
