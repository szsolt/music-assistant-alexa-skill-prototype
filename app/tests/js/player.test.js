const test = require('node:test');
const assert = require('node:assert');
const { waveBars, advance, due } = require('../../skill/page/html/player.js');

test('the loudness graph has 200 bars, each the loudest point of its stretch', () => {
  const points = Array.from({ length: 400 }, (_, n) => (n % 2 ? 0.5 : 0.25) + (n >= 200 ? 0.25 : 0));
  const bars = waveBars(points);
  assert.strictEqual(bars.length, 200);
  assert.deepStrictEqual([bars[0], bars[99], bars[100], bars[199]], [0.5, 0.5, 0.75, 0.75]);
});

const playing = () => ({
  title: 'Kicsi, gyere velem', titleSize: 1, artist: 'Kaláka', album: 'Volt egyszer egy...', quality: 'FLAC 16/44.1',
  cover: 'https://ma.example/cover.jpg', background: 'https://ma.example/cover.jpg',
  trackOffset: 60000, trackDuration: 180000, nextTitle: 'Altató', nextTitleSize: 0, nextArtist: 'Kaláka',
  nextAlbum: 'Volt egyszer egy...', nextQuality: 'FLAC 16/44.1', nextImage: 'https://ma.example/next.jpg',
  nextDuration: 150000, nextItem: '42',
});

test('at the end of the track the next one takes its place in the same stream', () => {
  const s = playing();
  assert.strictEqual(advance(s, 240000), true);
  assert.deepStrictEqual(s, Object.assign(playing(), {
    title: 'Altató', titleSize: 0, trackOffset: 240000, trackDuration: 150000, item: '42',
    cover: 'https://ma.example/next.jpg', background: 'https://ma.example/next.jpg', nextDuration: -1, nextItem: '',
  }));
});

test('before the end of the track nothing changes', () => {
  const s = playing();
  assert.strictEqual(advance(s, 239999), false);
  assert.deepStrictEqual(s, playing());
});

test('the track stays until the next one is known', () => {
  const s = Object.assign(playing(), { nextDuration: -1 });
  assert.strictEqual(advance(s, 300000), false);
  assert.strictEqual(s.title, 'Kicsi, gyere velem');
});

test("MA's next track shows once the stream reaches its start", () => {
  const s = Object.assign(playing(), { item: '41' });
  const next = { item: '42', title: 'Altató', trackOffset: 240000 };
  assert.strictEqual(due(s, next, 239000), false);
  assert.strictEqual(due(s, next, 240000), true);
});

test("new details of the shown track apply at once", () => {
  const s = Object.assign(playing(), { item: '41' });
  assert.strictEqual(due(s, { item: '41', trackOffset: 61000 }, 1000), true);
  assert.strictEqual(due(s, { item: '43', title: 'Radio' }, 1000), true);
});
