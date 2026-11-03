// A menu of [label, icon, shown, run] under (or, without room, above) the element it opens from.
const menu = { from: null, redraw: null };
function showMenu(entries, from, redraw) {
  const m = $('menu');
  m.textContent = '';
  entries.forEach(([label, icon, on, run]) => {
    if (!on) return;
    const entry = document.createElement('div');
    entry.innerHTML = '<svg viewBox="0 0 24 24"><path d="' + icon + '"/></svg><span></span>';
    entry.lastChild.textContent = label;
    entry.onclick = () => { closeMenu(); run(); };
    m.appendChild(entry);
  });
  Object.assign(menu, { from, redraw });
  m.style.display = 'block'; $('shade').style.display = 'block';
  const r = from.getBoundingClientRect(), H = window.innerHeight;
  m.style.left = Math.max(8, Math.min(r.right, window.innerWidth - 8) - m.offsetWidth) + 'px';
  m.style.top = (r.bottom + m.offsetHeight + 8 <= H ? r.bottom : Math.max(8, r.top - m.offsetHeight)) + 'px';
}
function closeMenu() { menu.redraw = null; $('menu').style.display = $('shade').style.display = $('ask').style.display = 'none'; }
// Add to playlist: what to add waits here while MA lists the playlists.
const addTo = { what: null, from: null };
function addToPlaylist(what, from) { Object.assign(addTo, { what, from }); post('action', { action: 'playlists' }); }
const toPlaylist = (playlist, name) => post('action', { action: 'add_to_playlist', playlist, name, ...addTo.what });
function askName() {
  $('askName').value = '';
  $('ask').style.display = 'flex'; $('shade').style.display = 'block';
  $('askName').focus();
}

// The ⋯ menu: lyrics, MA's sleep timer and autoplay. It shows the last known settings at once, MA's answer redraws it.
const opts = { autoplay: false, crossfade: false };
function moreMenu() {
  const left = Math.ceil((state.sleepAt - Date.now()) / 60000);
  showMenu([[lyr.on ? 'Hide lyrics' : 'Lyrics', ICONS.lyrics, true, () => setLyrics(!lyr.on)],
            [viz.on ? 'Hide visualiser' : 'Visualiser', ICONS.viz, true, () => setViz(!viz.on)],
            [left > 0 ? 'Sleep timer · ' + left + ' min' : 'Sleep timer', ICONS.sleep, true, () => sleepMenu($('More'))],
            ['Autoplay ' + (opts.autoplay ? 'on' : 'off'), ICONS.autoplay, true,
             () => post('action', { action: 'autoplay', on: !opts.autoplay })],
            ['Browse & search', ICONS.search, true, openBrowse]], $('More'), moreMenu);
}
function sleepMenu(from) {
  showMenu([15, 30, 45, 60, 120].map(m => [m + ' min', ICONS.sleep, true, () => post('action', { action: 'sleep', minutes: m })])
    .concat([['Turn off', ICONS.off, state.sleepAt > Date.now(), () => post('action', { action: 'sleep', minutes: 0 })]]), from);
}
function speedMenu() {
  showMenu([1, 1.25, 1.5, 2, 3].map(s => [s + '×', ICONS.replay, true, () => post('action', { action: 'speed', speed: s })]), $('speed'));
}
// MA's sleep timer counts down at the top while it runs, as in MA's app.
function sleepTick() {
  const left = state.sleepAt - Date.now(), h = Math.floor(left / 3600000);
  $('sleep').classList.toggle('hidden', left <= 0);
  if (left > 0) $('sleepLeft').textContent = h ? h + ':' + time(left % 3600000).padStart(5, '0') : time(left);
}
function startMenu() {
  $('askOk').onclick = () => { const name = $('askName').value.trim(); if (name) { closeMenu(); toPlaylist('', name); } };
  $('askName').onkeydown = e => { if (e.key === 'Enter') $('askOk').onclick(); };
  $('shade').onclick = closeMenu;
  $('sleep').querySelector('path').setAttribute('d', ICONS.sleep);
  $('sleep').onclick = () => sleepMenu($('sleep'));
  $('speed').onclick = speedMenu;
  $('More').onclick = () => { post('action', { action: 'options' }); moreMenu(); };
}
