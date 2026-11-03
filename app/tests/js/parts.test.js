// The page's parts load in Node without a browser: their DOM work waits for the page to start them.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PARTS = path.join(__dirname, '../../skill/page/html');

test('every part of the page loads without a browser', () => {
  const parts = fs.readdirSync(PARTS).filter(name => name.endsWith('.js'));
  assert.ok(parts.length > 1);
  for (const name of parts) assert.doesNotThrow(() => require(path.join(PARTS, name)), name);
});
