// A full-screen sheet like the playlist, for the album.
const sheet = { kind: null };
function openSheet(kind, head, note) {
  sheet.kind = kind;
  $('sheet').className = kind + (audio.paused ? ' paused' : ''); $('sheetBack').style.display = 'none'; sheetFav(null); $('sheetMore').style.display = 'none';
  $('sheetHead').textContent = head;
  sheetNote(note);
  $('sheet').style.display = 'flex'; touched();
}
function sheetNote(text) {
  const n = document.createElement('div'); n.className = 'note'; n.textContent = text;
  $('sheetBody').textContent = ''; $('sheetBody').appendChild(n);
}
// A favourite heart: on or off; a tap flips it and sends send(now).
function heart(el, on, send) {
  el.classList.toggle('on', !!on);
  el.onclick = e => { e.stopPropagation(); el.classList.toggle('on'); send(el.classList.contains('on')); };
}
let HEART = '';   // the sheet header's heart, copied into rows at start
const favorite = (uri, it) => on => { it.favorite = on; post('action', { action: 'favorite', uri, on }); };
// The heart in the sheet header, hidden for null.
function sheetFav(on, send) { $('sheetFav').style.display = on == null ? 'none' : 'block'; heart($('sheetFav'), on, send); }
const lab = (box, text) => { const l = box.appendChild(document.createElement('div')); l.className = 'lab'; l.textContent = text; };
function closeSheet() { sheet.kind = null; $('sheet').style.display = 'none'; $('q').blur(); }
// Browse & search MA's music ({browse: ...}, see ma/library.browse). Albums, playlists and artists open; songs play.
// Like MA: search is a list; elsewhere albums and artists show as a cover grid, and an artist starts with 5 songs.
const br = { cur: null, back: [], opening: false, kind: '' };
const OPENS = ['artist', 'album', 'playlist', 'podcast'];
function openBrowse() { openSheet('browse', 'Favourites', 'Loading…'); browse('favorites'); }
// The cover opens the playing song's album, at the song.
function browse(kind, text, opening) {
  br.opening = !!opening; br.kind = kind;
  if (!opening) br.back = [];
  else if (br.cur) br.cur.top = $('sheetBody').scrollTop;   // back returns here
  $('bFav').classList.toggle('on', kind === 'favorites'); $('bLib').classList.toggle('on', kind === 'library'); $('bRecent').classList.toggle('on', kind === 'recent');
  sheetNote(kind === 'search' ? 'Searching…' : 'Loading…');
  post('action', { action: 'browse', kind, text: text || '', item: wave.item || '' });
}
function showBrowse(r) {
  if (br.opening && br.cur) br.back.push(br.cur);
  r.kind = br.kind; br.opening = false; br.cur = r;
  drawBrowse();
}
function drawBrowse(top = 0) {
  const r = br.cur;
  if (r.title) $('sheetHead').textContent = r.title;
  $('sheetBack').style.display = br.back.length ? 'inline' : 'none';
  // an album's heart and ⋮ sit in its header, an artist's or a playlist's in the top bar
  sheetFav(r.item && !r.header ? r.favorite : null, favorite(r.item, r));
  $('sheetMore').style.display = r.item && !r.header ? 'block' : 'none';
  if (r.item) itemMenu($('sheetMore'), r, { uri: r.item });
  if (!r.sections.length) { sheetNote(r.kind === 'playing' ? 'Nothing to open for this song' : 'Nothing found'); return; }
  const body = $('sheetBody'); body.textContent = '';
  if (r.header) body.appendChild(hero(r));
  r.sections.forEach(sec => {
    if (sec.name) lab(body, sec.name);
    const grid = r.kind !== 'search' && sec.rows.every(it => ['album', 'artist', 'podcast', 'audiobook'].includes(it.type));
    const box = grid ? body.appendChild(document.createElement('div')) : body;
    if (grid) box.className = 'grid';
    // an artist's songs and each library section show their first rows, as MA's pages
    const first = r.type === 'artist' && sec.name === 'Songs' ? 5 : r.kind === 'library' ? 12 : 0;
    const short = first && !sec.all && sec.rows.length > first + 1;
    const discs = r.type === 'album' && sec.rows.some(it => it.disc > 1);   // MA's "Disc N" headers
    let disc;
    (short ? sec.rows.slice(0, first) : sec.rows).forEach(it => {
      if (discs && it.disc !== disc) lab(box, 'Disc ' + (disc = it.disc));
      box.appendChild(browseItem(r, it, grid));
    });
    if (short) {
      const all = document.createElement('div'); all.className = 'pr all'; all.textContent = 'All ' + sec.rows.length + ' ' + sec.name.toLowerCase();
      all.onclick = () => { sec.all = true; drawBrowse(body.scrollTop); };
      body.appendChild(all);
    }
  });
  const now = !top && r.type !== 'artist' && body.querySelector('.now');   // an artist opens at its top
  if (now) top = now.offsetTop - body.offsetTop - body.clientHeight / 3;
  body.style.scrollBehavior = 'auto'; body.scrollTop = top; body.style.scrollBehavior = '';
}
// An album's cover, artist (opens the artist) and year, or an artist's photo, as MA's album and artist pages.
function hero(r) {
  const h = document.createElement('div'), artist = r.type === 'artist';
  const rows = name => (r.sections.find(sec => sec.name === name) || { rows: [] }).rows;
  const songs = artist ? rows('Songs') : r.sections[0] ? r.sections[0].rows : [];
  h.className = 'hero ' + r.type;
  h.innerHTML = '<div class="art"><img alt=""></div><div><b></b><a></a><small></small><span class="acts"><span class="heart">' + HEART + '</span><i class="more">⋮</i></span></div>';
  itemMenu(h.querySelector('.more'), r, { uri: r.item });
  // the playing album's heart is the player's, so both stay in step
  if (r.item === (r.now || [])[1] && state.albumFavorite >= 0)
    heart(h.querySelector('.heart'), state.albumFavorite === 1, () => toggle('albumFavorite', 'favorite_album'));
  else heart(h.querySelector('.heart'), r.favorite, favorite(r.item, r));
  if (r.header.image) h.querySelector('img').src = r.header.image; else h.querySelector('.art').textContent = initials(r.title);
  h.querySelector('b').textContent = r.title;
  h.querySelector('a').textContent = r.header.artist;
  if (!r.header.artist) h.querySelector('a').remove();
  if (r.header.artist_uri) h.querySelector('a').onclick = () => browse('open', r.header.artist_uri, true);
  else if (r.header.artist) h.querySelector('a').style.color = 'inherit';   // a podcast's publisher opens nothing
  const ms = songs.reduce((sum, it) => sum + it.duration_ms, 0);
  h.querySelector('small').textContent = (artist ? [rows('Albums').length + ' albums', songs.length + ' songs']
    : r.type === 'podcast' ? [songs.length + ' episodes']
    : [r.header.year || '', songs.length + ' songs', ms ? Math.round(ms / 60000) + ' min' : ''])
    .filter(Boolean).join(' · ');
  return h;
}
const initials = name => {  // as MA: first letters of two words, or two letters of one
  const w = name.trim().split(/\s+/).filter(Boolean);
  return (w.length > 1 ? w[0][0] + w[1][0] : (w[0] || '').slice(0, 2)).toUpperCase();
};
// The ⋮ menu of an item: play it, queue it, or view its artist or album.
function itemMenu(more, r, it) {
  more.onclick = e => {
    e.stopPropagation();
    const add = option => post('action', { action: 'play_media', uri: it.uri, option });
    showMenu([['Play now', ICONS.play, true, () => { closeSheet(); waitFor({ action: 'play_media', uri: it.uri }); }],
              ['Play next', ICONS.playNext, true, () => add('next')],
              ['Add to queue', ICONS.add, true, () => add('add')],
              ['Add to playlist', ICONS.playlist, /:\/\/(track|album|playlist)\//.test(it.uri), () => addToPlaylist({ uri: it.uri }, more)],
              ...(it.go || []).filter(go => go[2] !== r.item).map(([kind, name, uri]) => ['View ' + kind + ' ' + name, ICONS[kind], true,
                                                          () => browse('open', uri, true)])], more);
  };
}
function browseItem(r, it, grid) {
  const el = document.createElement('div'), opens = OPENS.includes(it.type);
  el.className = grid ? 'cell ' + it.type : 'pr';
  el.innerHTML = grid ? '<div class="art"><img alt="" loading="lazy"></div><div class="meta"><div class="tx"><div></div><small></small></div><i class="more">⋮</i></div>'
    : '<span class="th"><img alt="" loading="lazy"></span><div class="tx"><div></div><small></small></div><span class="d"></span><i class="heart">' + HEART + '</i><i class="more">⋮</i>';
  if (it.image) el.querySelector('img').src = it.image;
  else if (grid) el.querySelector('.art').textContent = initials(it.name);
  // MA's now-playing bars, as in the playlist: the playing song, its album and its artists
  if ((r.now || []).includes(it.uri))
    el.querySelector(grid ? '.art' : '.th').insertAdjacentHTML('beforeend', EQ);
  el.querySelector('.tx div').textContent = it.name;
  if (!grid) {
    // MA's played marks for episodes and audiobooks
    el.querySelector('.d').textContent = ({ done: '✓ ', part: '◔ ' }[it.played] || '') + (it.duration_ms && !opens ? time(it.duration_ms) : '');
    if (it.type === 'podcast_episode') el.querySelector('.heart').style.visibility = 'hidden';
    const playing = it.uri === (r.now || [])[0];
    el.classList.toggle('now', playing);
    heart(el.querySelector('.heart'), playing ? state.songFavorite === 1 : it.favorite,
          playing ? () => toggle('songFavorite', 'favorite_song') : favorite(it.uri, it));
  }
  el.querySelector('small').textContent = it.sub || (grid ? it.type[0].toUpperCase() + it.type.slice(1) : '');
  el.onclick = () => {
    if (opens) { $('q').blur(); browse('open', it.uri, true); }
    else { closeSheet(); waitFor(r.uri ? { action: 'play_media', uri: r.uri, item: it.uri } : { action: 'play_media', uri: it.uri }); }
  };
  itemMenu(el.querySelector('.more'), r, it);
  return el;
}
const search = () => { const t = $('q').value.trim(); if (t) { $('q').blur(); browse('search', t); } };
function startBrowse() {
  HEART = $('sheetFav').innerHTML;
  $('sheetClose').onclick = closeSheet;
  $('album').onclick = $('cover').onclick = () => { openSheet('browse', state.album, 'Loading…'); browse('playing'); };
  $('artist').onclick = () => { openSheet('browse', state.artist, 'Loading…'); browse('playing', 'artist'); };
  $('sheetBack').onclick = () => { if (br.back.length) { br.cur = br.back.pop(); drawBrowse(br.cur.top); } };
  $('bFav').onclick = () => browse('favorites');
  $('bLib').onclick = () => browse('library');
  $('bRecent').onclick = () => browse('recent');
  $('bGoIcon').setAttribute('d', ICONS.search);
  $('bGo').onclick = search;
  $('q').onkeydown = e => { if (e.key === 'Enter') search(); };
}
