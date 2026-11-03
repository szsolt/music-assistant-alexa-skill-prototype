// The busy overlay and the toast.
// No answer from the skill in time: the overlay goes, the page stays usable.
const BUSY_MS = 10000;
function busy(on) {
  $('busy').style.display = on ? 'flex' : 'none';
  clearTimeout(busy.timer);
  if (on) busy.timer = setTimeout(() => busy(false), BUSY_MS);
}
function toast(text) {
  const t = $('toast'); t.textContent = text; t.style.display = 'block';
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.style.display = 'none'; }, 2500);
}

if (typeof module === 'object') module.exports = { busy, toast, BUSY_MS };   // for the Node tests
