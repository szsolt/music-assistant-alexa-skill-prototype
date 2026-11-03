// The page's whole script, as one <script>, run in a stub browser: it starts, sets up its timers and follows a track change.
// PAGE_SCRIPT names the script as the skill serves it (the Python suite sets it); without it the parts are inlined here.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const DIR = path.join(__dirname, '../../skill/page/html');

function script() {
  if (process.env.PAGE_SCRIPT) return fs.readFileSync(process.env.PAGE_SCRIPT, 'utf8');
  const html = fs.readFileSync(path.join(DIR, 'page.html'), 'utf8')
    .replace(/<!--include:([\w.]+)-->/g, (_, name) => fs.readFileSync(path.join(DIR, name), 'utf8'));
  return html.match(/<script>([\s\S]*?)<\/script>/)[1];
}

// Just enough of a browser for the page's own code.
function browser() {
  const elements = {}, intervals = [], timeouts = [], posts = [];
  const element = () => {
    const listeners = {}, classes = new Set(), found = {};
    return {
      style: {}, textContent: '', innerHTML: '<svg></svg>', value: '', children: [], offsetTop: 0, offsetHeight: 0,
      clientWidth: 0, clientHeight: 0, scrollTop: 0, paused: true, currentTime: 0, listeners,
      classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c),
                   toggle: (c, on) => ((on === undefined ? !classes.has(c) : on) ? classes.add(c) : classes.delete(c)) },
      setAttribute() {}, getAttribute() { return null; }, insertAdjacentHTML() {}, remove() {}, closest() { return null; },
      appendChild(child) { this.children.push(child); return child; },
      // One stub per selector, so the code that fills and wires found elements runs.
      querySelector(sel) { return found[sel] || (found[sel] = element()); },
      querySelectorAll(sel) { return [this.querySelector(sel)]; }, focus() {}, blur() {},
      getBoundingClientRect: () => ({ top: 0, bottom: 0, right: 0 }),
      addEventListener(type, run) { listeners[type] = run; },
      play() { this.paused = false; return Promise.resolve(); }, pause() { this.paused = true; },
    };
  };
  const context = {
    console, window: { innerWidth: 1280, innerHeight: 800 }, location: { pathname: '/bell/html/p1' },
    document: {
      getElementById: id => elements[id] || (elements[id] = element()), createElement: element,
      querySelectorAll: () => [element()], addEventListener() {}, activeElement: { id: '', value: '' },
    },
    EventSource: function (url) { context.events = this; this.url = url; },
    fetch: (url, init) => { posts.push([url, JSON.parse(init.body)]); return Promise.resolve({ status: 204 }); },
    localStorage: { getItem: () => null, setItem() {} },
    Alexa: { create: () => Promise.resolve({ alexa: { skill: { sendMessage() {} } } }) },
    setInterval: (run, ms) => intervals.push({ run, ms }), clearInterval() {},
    setTimeout: (run, ms) => timeouts.push({ run, ms }), clearTimeout() {},
  };
  return { context, elements, intervals, timeouts, posts };
}

test('the page starts, sets up its timers and follows a track change in its stream', async () => {
  const b = browser();
  vm.runInNewContext(script(), b.context);
  assert.deepStrictEqual(b.intervals.map(t => t.ms), [100, 1000, 250]);
  assert.strictEqual(b.context.events.url, '/bell/html/p1/events');
  assert.deepStrictEqual(b.posts.map(([url, body]) => [url, body.action]),
                         [['/bell/html/p1/state', undefined], ['/bell/html/p1/action', 'wave']]);

  b.elements.Playlist.onclick();
  b.context.events.onmessage({ data: JSON.stringify({ set: {
    title: 'Kicsi, gyere velem', trackOffset: 0, trackDuration: 180000,
    nextTitle: 'Altató', nextArtist: 'Kaláka', nextDuration: 150000, nextItem: '42' } }) });
  await new Promise(resolve => setImmediate(resolve));   // the playlist's first ask is answered
  b.posts.length = 0;
  Object.assign(b.elements.audio, { currentTime: 180, paused: false });
  b.elements.audio.listeners.timeupdate();
  assert.strictEqual(b.elements.title.textContent, 'Altató');
  assert.deepStrictEqual(b.posts.map(([, body]) => body), [
    { action: 'wave', item: '42' }, { action: 'queue', offset: -1, limit: 110 }]);
  assert.ok(b.timeouts.some(t => t.ms === 5000));

  // The loudness and the queue arrive; then every timer runs once, and a played song is tapped.
  b.context.events.onmessage({ data: JSON.stringify({ wave: { item: '42', points: [0.2, 0.5, 0.9, 0.4] }, queue: {
    offset: 0, index: 1, total: 3, locked: 1, after: 150000, items: [
      { item_id: 'q1', title: 'Csillagszemű', duration_ms: 200000 },
      { item_id: 'q2', title: 'Altató', duration_ms: 150000 },
      { item_id: 'q3', title: 'Esti dal', duration_ms: 120000 }] } }) });
  for (const t of [...b.intervals, ...b.timeouts]) t.run();
  const rows = b.elements.plList.children.filter(r => r.className.startsWith('pr'));
  assert.strictEqual(rows.length, 3);
  b.posts.length = 0;
  b.elements.plList.onpointerdown();
  rows[0].onclick({ target: rows[0] });
  assert.deepStrictEqual(b.posts.map(([, body]) => body), [{ action: 'jump', item: 'q1' }]);
});

test('with the playlist open, news asks for the queue again and a toast does not', () => {
  const b = browser();
  vm.runInNewContext(script(), b.context);
  b.elements.Playlist.onclick();
  const asks = () => b.timeouts.filter(t => t.ms === 1000).length;
  b.context.events.onmessage({ data: JSON.stringify({ toast: 'Music Assistant did not do it' }) });
  assert.strictEqual(asks(), 0);
  b.context.events.onmessage({ data: JSON.stringify({ set: { nextTitle: 'Altató' } }) });
  assert.strictEqual(asks(), 1);
});
