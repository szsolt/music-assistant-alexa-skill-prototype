// The playlist: MA's queue, as the skill sends it ({queue: ...}, see ma/player.queue_window).
// The page keeps no order of its own: MA's answer to a move replaces what it shows.
const pl = { open: false, offset: -1, limit: 110, win: null, asked: false, again: false, dragging: false,
             scroll: false, drawnOffset: null, timer: 0, scrolledAt: 0, tap: false };
const rowHeight = () => ($('plList').querySelector('.pr') || {}).offsetHeight || 1;
const span = ms => { const m = Math.round(ms / 60000);
  return m >= 60 ? Math.floor(m / 60) + ' h ' + m % 60 + ' min' : m + ' min'; };
function askQueue() {
  if (!pl.open) return;
  if (pl.asked) { pl.again = true; return; }
  pl.asked = true;
  post('action', { action: 'queue', offset: pl.offset, limit: pl.limit }).then(() => {
    pl.asked = false;
    if (pl.again) { pl.again = false; askQueue(); }
  });
}
function openList() {
  Object.assign(pl, { open: true, offset: -1, limit: 110, scroll: true, drawnOffset: null });
  $('plList').textContent = ''; $('plHead').textContent = '';
  $('pl').style.display = 'flex'; drawOpts(); touched(); askQueue(); post('action', { action: 'options' });
}
// The chips over the playlist, as in MA's queue view.
function drawOpts() {
  $('plAuto').className = opts.autoplay ? 'on' : '';
  $('plFade').className = opts.crossfade ? 'on' : '';
  $('plClear').style.display = $('plMove').style.display = $('plSave').style.display = pl.win && pl.win.total ? '' : 'none';
}
// What happens after the queue, under Up next, as MA's queue view says it. A tap turns Autoplay on or off.
function modeBanner(w) {
  const [title, text] = w.dynamic ? ['Autoplay is active', 'Your queue is being dynamically filled based on your selection.']
    : state.repeat > 0 ? ['Autoplay is disabled', 'Autoplay has been automatically turned off because repeat is on']
    : opts.autoplay ? ['Autoplay is enabled', 'After your selection has played, Autoplay keeps the music going by automatically adding more content based on your preferences.']
    : ['Autoplay is disabled', 'Your music will stop once the queue has finished. Enable Autoplay to automatically keep it going with more content based on your preferences.'];
  const b = document.createElement('div');
  b.className = 'mode' + (w.dynamic || (opts.autoplay && !(state.repeat > 0)) ? ' on' : '');
  b.innerHTML = '<svg viewBox="0 0 24 24"><path d="' + ICONS.endless + '"/></svg><div><b></b><small></small></div>';
  b.querySelector('b').textContent = title; b.querySelector('small').textContent = text;
  if (!w.dynamic && !(state.repeat > 0)) b.onclick = () => post('action', { action: 'autoplay', on: !opts.autoplay });
  return b;
}
function closeList() { pl.open = false; closeMenu(); $('pl').style.display = 'none'; }
function plTick() {
  const w = pl.win; if (!w) return;
  const row = w.items[w.index - w.offset], now = $('plList').querySelector('.now .d');
  if (row && now && row.duration_ms) now.textContent = time(trackPosition()) + ' / ' + time(row.duration_ms);
  const left = w.after >= 0 && state.trackDuration > 0 ? w.after + trackLength() - trackPosition() : -1;
  $('plHead').textContent = (w.index + 1) + ' of ' + w.total + (left >= 0 ? ' · ' + span(left) + ' left' : '');
}
// The row at the top of the list, and how far it is scrolled past.
function topRow() {
  const list = $('plList'), rows = list.querySelectorAll('.pr');
  for (let k = 0; k < rows.length; k++)
    if (rows[k].offsetTop + rows[k].offsetHeight > list.scrollTop) return { i: pl.drawnOffset + k, dy: list.scrollTop - rows[k].offsetTop };
  return null;
}
function drawList() {
  const w = pl.win, list = $('plList');
  const anchor = pl.drawnOffset === null ? null : topRow();
  list.textContent = '';
  w.items.forEach((it, k) => {
    const i = w.offset + k, r = document.createElement('div');
    const label = i === w.index ? 'Now playing' : i === w.index + 1 ? 'Up next · ' + (w.total - w.index - 1)
                : i === w.offset && i < w.index ? 'Played' : '';
    if (label) { const l = document.createElement('div'); l.className = 'lab'; l.textContent = label; list.appendChild(l); }
    if (i === w.index + 1) list.appendChild(modeBanner(w));
    r.className = 'pr' + (i < w.index ? ' past' : i === w.index ? ' now' : '');
    r.innerHTML = '<span class="th"><img loading="lazy" alt="">' + (i === w.index ? EQ : '') + '</span><div class="tx"><div></div><small></small></div><span class="d"></span><i class="more">⋮</i><b>☰</b>';
    if (it.image) r.querySelector('img').src = it.image;
    r.querySelector('.tx div').textContent = it.title;
    r.querySelector('small').textContent = [it.secondary, it.album].filter(Boolean).join(' · ');
    r.querySelector('.d').textContent = it.duration_ms ? time(it.duration_ms) : '';
    const handle = r.querySelector('b');
    if (i > w.locked) handle.onpointerdown = e => drag(e, r, k); else handle.style.visibility = 'hidden';
    r.onclick = e => {
      if (e.target.closest('b') || !pl.tap) return;
      if (e.target.closest('.more')) openMenu(it, i, r); else pick(it, i);
    };
    list.appendChild(r);
    if (i === w.index && i === w.total - 1) list.appendChild(modeBanner(w));
  });
  const rows = list.querySelectorAll('.pr'), first = rows[Math.max(0, w.index - w.offset - 1)];
  if (pl.scroll && first) list.scrollTop = first.offsetTop;
  else if (anchor && rows[anchor.i - w.offset]) list.scrollTop = rows[anchor.i - w.offset].offsetTop + anchor.dy;
  pl.scroll = false; pl.drawnOffset = w.offset;
  plTick();
}
// As in MA's queue: what a song's ⋮ offers. MA can't change played or loaded songs.
function openMenu(it, i, row) {
  touched();
  const w = pl.win, free = i > w.locked;
  const edit = action => post('action', { action, item: it.item_id, offset: pl.offset, limit: pl.limit });
  showMenu([['Play now', ICONS.play, i !== w.index, () => pick(it, i)],
            ['Play next', ICONS.playNext, free && i !== w.locked + 1, () => edit('play_next')],
            ['Endless mix', ICONS.mix, true, () => { closeList(); waitFor({ action: 'mix', item: it.item_id }); }],
            ['Move to end', ICONS.toEnd, free && i < w.total - 1, () => edit('move_end')],
            ['Add to playlist', ICONS.playlist, true, () => addToPlaylist({ item: it.item_id }, row.querySelector('.more'))],
            ['Delete', ICONS.delete, free, () => edit('delete')]], row.querySelector('.more'));
}
function pick(it, i) {
  closeList();
  if (i !== pl.win.index) waitFor({ action: 'jump', item: it.item_id });
}
// The dragged row follows the finger; the rows in between slide aside to show where it lands.
function drag(e, row, from) {
  e.preventDefault(); touched();
  const list = $('plList'), rows = Array.from(list.querySelectorAll('.pr')), h = row.offsetHeight, y0 = e.clientY, s0 = list.scrollTop;
  const first = pl.win.locked + 1 - pl.win.offset, last = rows.length - 1;
  let to = from;
  pl.dragging = true; row.classList.add('drag');
  try { row.setPointerCapture(e.pointerId); } catch (err) {}
  const move = ev => {
    const dy = ev.clientY - y0 + list.scrollTop - s0;
    row.style.transform = 'translateY(' + dy + 'px)';
    to = Math.max(first, Math.min(last, from + Math.round(dy / h)));
    rows.forEach((r, k) => {
      if (k !== from) r.style.transform = k > from && k <= to ? 'translateY(-' + h + 'px)' : k < from && k >= to ? 'translateY(' + h + 'px)' : '';
    });
    const box = list.getBoundingClientRect();
    if (ev.clientY < box.top + h / 2) list.scrollTop -= 8; else if (ev.clientY > box.bottom - h / 2) list.scrollTop += 8;
  };
  const up = () => {
    ['pointermove', 'pointerup', 'pointercancel'].forEach((t, k) => row.removeEventListener(t, k ? up : move));
    pl.dragging = false; touched();
    const items = pl.win.items;
    if (to !== from && items[from]) {
      const item = items[from].item_id;
      items.splice(to, 0, items.splice(from, 1)[0]);
      post('action', { action: 'move', item, shift: to - from, offset: pl.offset, limit: pl.limit });
    }
    drawList();
  };
  row.addEventListener('pointermove', move); row.addEventListener('pointerup', up); row.addEventListener('pointercancel', up);
}
function startPlaylist() {
  [['plAuto', ICONS.autoplay], ['plClear', ICONS.delete], ['plMove', ICONS.speaker], ['plSave', ICONS.playlist]].forEach(([id, d]) =>
    $(id).insertAdjacentHTML('afterbegin', '<svg viewBox="0 0 24 24"><path d="' + d + '"/></svg>'));
  $('plAuto').onclick = () => post('action', { action: 'autoplay', on: !opts.autoplay });
  $('plFade').onclick = () => post('action', { action: 'crossfade', on: !opts.crossfade });
  $('plClear').onclick = () => showMenu([['Clear the queue', ICONS.delete, true,
    () => post('action', { action: 'clear', offset: -1, limit: pl.limit })]], $('plClear'));
  $('plMove').onclick = () => post('action', { action: 'players' });
  $('plSave').onclick = () => addToPlaylist({ queue: true }, $('plSave'));
  // More rows when scrolled near either end. shortcut: at most 500 rows at once, enough for any queue we play.
  // A touch that scrolls the list, or stops it scrolling, is not a tap on a song.
  $('plList').onpointerdown = () => { pl.tap = Date.now() - pl.scrolledAt > 300; };
  $('plList').onscroll = () => {
    touched(); pl.scrolledAt = Date.now(); pl.tap = false;
    const list = $('plList'), w = pl.win, near = 5 * rowHeight();
    if (!w || pl.dragging || pl.limit >= 500) return;
    if (list.scrollTop + list.clientHeight > list.scrollHeight - near && w.offset + w.items.length < w.total) {
      pl.limit += 100; askQueue();
    } else if (list.scrollTop < near && w.offset > 0) {
      const offset = Math.max(0, w.offset - 100);
      pl.limit += w.offset - offset; pl.offset = offset; askQueue();
    }
  };
  $('upnext').onclick = openList;
  $('Playlist').onclick = openList;
  $('plClose').onclick = closeList;
}
