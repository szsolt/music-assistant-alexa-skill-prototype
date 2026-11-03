// Amazon closes the page after 30 min without a touch or voice: the skill
// sends a new one when asked. The page asks just before the last track change
// that comes in time, naming the next track, so the swap falls between tracks.
const RENEW_BY_MS = 28 * 60 * 1000, RENEW_LEAD_MS = 1500;
const loadedAt = Date.now();
let alexaClient = null, renewAskedAt = 0;
function renew(item, gapMs = 60000) {
  if (!alexaClient || Date.now() - renewAskedAt < gapMs) return;
  renewAskedAt = Date.now();
  alexaClient.skill.sendMessage(Object.assign({ renew: location.pathname.split('/').pop() }, item ? { item } : {}));
}
// With `left` ms to the page's limit and the stream at `at`: null (not yet), '' (now) or the next track's item (now, naming it).
function renewDue(left, s, at, playing) {
  if (left <= 0) return '';
  const toEnd = s.trackOffset + s.trackDuration - at;
  return playing && s.trackDuration > 0 && s.nextItem && toEnd > 0 && toEnd <= RENEW_LEAD_MS
      && toEnd + s.nextDuration >= left ? s.nextItem : null;
}
function renewWhenDue() {
  const item = renewDue(loadedAt + RENEW_BY_MS - Date.now(), state, position(), !audio.paused);
  if (item !== null) renew(item);
}
function connectAlexa() {
  try {
    Alexa.create({ version: '1.1' }).then(({ alexa }) => { alexaClient = alexa; })
      .catch(e => console.log('alexa: ' + e));
  } catch (e) { console.log('no alexa sdk: ' + e); }
}

if (typeof module === 'object') module.exports = { renewDue, RENEW_LEAD_MS };   // for the Node tests
