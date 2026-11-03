const test = require('node:test');
const assert = require('node:assert');
const { renewDue, RENEW_LEAD_MS } = require('../../skill/page/html/renew.js');

const MIN = 60000;
// 3 min into a 4 min track that starts 10 min into the stream; the next track lasts 3 min.
const track = { trackOffset: 10 * MIN, trackDuration: 4 * MIN, nextItem: '42', nextDuration: 3 * MIN };
const at = toEnd => track.trackOffset + track.trackDuration - toEnd;

test('at its time limit the page asks for a new one', () => {
  assert.strictEqual(renewDue(0, track, at(MIN), true), '');
});

test('just before the last track change in time the page asks, naming the next track', () => {
  assert.strictEqual(renewDue(2 * MIN, track, at(RENEW_LEAD_MS), true), '42');
});

test('the page does not ask before the last moments of the track', () => {
  assert.strictEqual(renewDue(2 * MIN, track, at(2 * RENEW_LEAD_MS), true), null);
});

test('the page waits for a later track change that still comes in time', () => {
  assert.strictEqual(renewDue(4 * MIN, track, at(RENEW_LEAD_MS), true), null);
});

test('a paused page does not ask before its time limit', () => {
  assert.strictEqual(renewDue(2 * MIN, track, at(RENEW_LEAD_MS), false), null);
});
