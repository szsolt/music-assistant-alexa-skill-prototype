const test = require('node:test');
const assert = require('node:assert');
const { parseLyrics } = require('../../skill/page/html/lyrics.js');

test('synced lyrics become lines with their time in the song', () => {
  const lrc = '[ar:Kaláka]\n[00:12.50] Kicsi, gyere velem\n[01:02]rózsát szedni\n[01:05.25]';
  assert.deepStrictEqual(parseLyrics({ lrc, plain: 'Kicsi, gyere velem' }), {
    plain: false,
    lines: [{ at: 12500, text: 'Kicsi, gyere velem' }, { at: 62000, text: 'rózsát szedni' }, { at: 65250, text: '' }],
  });
});

test('plain lyrics show as they are, untimed', () => {
  assert.deepStrictEqual(parseLyrics({ lrc: '', plain: 'Kicsi, gyere velem\n\nrózsát szedni' }), {
    plain: true,
    lines: [{ at: -1, text: 'Kicsi, gyere velem' }, { at: -1, text: '' }, { at: -1, text: 'rózsát szedni' }],
  });
});

test('a song without lyrics has no line with text', () => {
  const { lines } = parseLyrics({});
  assert.ok(!lines.some(line => line.text));
});
