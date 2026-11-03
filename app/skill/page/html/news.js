// From the skill: {set: {...}, track: {...}, src: new stream, media: 'play'|'pause', ready: true}
let events = null;
function listen() {
  openEvents();
  setInterval(report, 1000);
}
function openEvents() {
  if (events) events.close();
  const es = events = new EventSource(BASE + 'events');
  // EventSource gives up on an error status (the proxy's 502 while the skill restarts).
  es.onerror = () => { if (es.readyState === EventSource.CLOSED) setTimeout(() => es === events && openEvents(), 2000); };
  events.onmessage = e => {
    const msg = JSON.parse(e.data);
    Object.assign(state, msg.set);
    if (msg.ready || msg.src) busy(false);
    if (msg.src) { audio.src = msg.src; ahead = null; state.trackDuration = state.queueEnd = 0; }   // no time until MA says where its track is; a new track asks for its own loudness graph
    if (msg.track) take(msg.track);
    if (msg.wave && msg.wave.item === wave.item && msg.wave.points) {
      wave.points = msg.wave.points;
      wave.bars = waveBars(wave.points);
    }
    if (msg.lyrics && lyr.on && msg.lyrics.item === lyr.item) showLyrics(msg.lyrics);
    if (msg.browse && sheet.kind === 'browse') showBrowse(msg.browse);
    if (msg.now && msg.now.item === wave.item && br.cur) {   // a new song: move the now-playing marks
      [br.cur, ...br.back].forEach(v => v.now = msg.now.uris);
      if (sheet.kind === 'browse') drawBrowse($('sheetBody').scrollTop);
    }
    if (msg.viz && viz.on) takeViz(msg.viz);
    if (msg.toast) toast(msg.toast);
    if (msg.options) {
      Object.assign(opts, msg.options);
      drawOpts();
      if (pl.open && !pl.dragging) drawList();
      if (menu.redraw && $('menu').style.display === 'block') menu.redraw();
    }
    if (msg.media === 'play') play(); else if (msg.media === 'pause') audio.pause();
    if (msg.players) showMenu(msg.players.map(([target, name]) => [name, ICONS.speaker, true,
      () => post('action', { action: 'transfer', target, name })]), $('plMove'));
    if (msg.playlists && addTo.what) showMenu([['New playlist…', ICONS.add, true, askName],
      ...msg.playlists.map(([id, name]) => [name, ICONS.playlist, true, () => toPlaylist(id, name)])], addTo.from);
    if (msg.queue) { pl.win = msg.queue; pl.offset = msg.queue.offset; drawOpts(); if (pl.open && !pl.dragging) drawList(); }
    else if (pl.open && !msg.toast) { clearTimeout(pl.timer); pl.timer = setTimeout(askQueue, 1000); }
    render();
  };
}
// Where the page is, every second: the skill sends what it hasn't seen.
// A 404: the skill restarted and forgot this page, so ask for a new one.
// reconnect: the page missed a hand-off, so its SSE stream may be dead.
const report = () => post('state', { seen: state.seen, at: position(), playing: !audio.paused })
  .then(r => r && (r.status === 404 ? renew(null, 10000) : r.status === 200 && r.json()))
  .then(a => { if (a && a.reconnect) openEvents(); });
