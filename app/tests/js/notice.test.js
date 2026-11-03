const test = require('node:test');
const assert = require('node:assert');
const { busy, toast, BUSY_MS } = require('../../skill/page/html/notice.js');

// The page's elements, as far as the overlay and the toast use them.
const elements = { busy: { style: {} }, toast: { style: {}, textContent: '' } };
globalThis.$ = id => elements[id];

test.beforeEach(context => context.mock.timers.enable({ apis: ['setTimeout'] }));

test('the busy overlay clears by itself after 10 s', context => {
  assert.strictEqual(BUSY_MS, 10000);
  busy(true);
  context.mock.timers.tick(9999);
  assert.strictEqual(elements.busy.style.display, 'flex');
  context.mock.timers.tick(1);
  assert.strictEqual(elements.busy.style.display, 'none');
});

test('a new wait gives the overlay its full 10 s again', context => {
  busy(true);
  context.mock.timers.tick(6000);
  busy(true);
  context.mock.timers.tick(6000);
  assert.strictEqual(elements.busy.style.display, 'flex');
  context.mock.timers.tick(4000);
  assert.strictEqual(elements.busy.style.display, 'none');
});

test('an answer clears the overlay at once', () => {
  busy(true);
  busy(false);
  assert.strictEqual(elements.busy.style.display, 'none');
});

test('a toast shows for 2.5 s', context => {
  toast('Music Assistant is not reachable');
  assert.deepStrictEqual([elements.toast.textContent, elements.toast.style.display], ['Music Assistant is not reachable', 'block']);
  context.mock.timers.tick(2499);
  assert.strictEqual(elements.toast.style.display, 'block');
  context.mock.timers.tick(1);
  assert.strictEqual(elements.toast.style.display, 'none');
});
