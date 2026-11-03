// The playing song: its fields, its loudness graph and its now-playing bars, as the stream plays on.
let seeking = false, queueEnded = false;
function play() { audio.play().catch(e => console.log('play: ' + e)); }
function setImage(img, src) { if (src && img.getAttribute('src') !== src) img.src = src; }

function render() {
  $('title').textContent = state.title; $('title').className = state.titleSize ? 's' + state.titleSize : '';
  $('artist').textContent = state.artist; $('album').textContent = state.album;
  $('lyTitle').textContent = state.artist ? state.title + ' · ' + state.artist : state.title;
  setImage($('cover'), state.cover);
  $('bg').style.backgroundImage = state.background ? `url("${state.background}")` : '';
  $('quality').textContent = state.quality;
  $('nextTitle').textContent = state.nextTitle; $('nextArtist').textContent = state.nextArtist;
  setImage($('nextImage'), state.nextImage);
  $('upnext').classList.toggle('hidden', !(state.nextDuration >= 0 && state.nextTitle));
  const radio = !state.trackDuration;
  $('seek').style.display = radio ? 'none' : ''; $('bar').style.display = radio ? 'block' : 'none';
  $('seek').classList.toggle('wave', !radio && !!wave.bars);
  $('total').textContent = radio ? '' : time(trackLength());
  $('slider').max = trackLength();
  if (!seeking) { $('slider').value = trackPosition(); $('elapsed').textContent = time(trackPosition()); }
  for (const [id, key] of [['FavSong', 'songFavorite'], ['FavAlbum', 'albumFavorite'], ['Shuffle', 'shuffle'], ['Repeat', 'repeat']]) {
    $(id).classList.toggle('hidden', state[key] < 0); $(id).classList.toggle('on', state[key] > 0);
  }
  // Audiobooks and podcasts: skip back and forward and the speed in place of shuffle and repeat, as in MA's app.
  const spoken = state.speed > 0;
  for (const id of ['SkipBack', 'SkipForward']) $(id).style.display = spoken ? '' : 'none';
  for (const id of ['Shuffle', 'Repeat']) $(id).style.display = spoken ? 'none' : '';
  $('speed').classList.toggle('hidden', !spoken); $('speed').textContent = speed() + '×';
  $('repeatIcon').setAttribute('d', [ICONS.repeatOff, ICONS.repeat, ICONS.repeatOne][state.repeat] || ICONS.repeat);
  $('shuffleIcon').setAttribute('d', state.shuffle > 0 ? ICONS.shuffle : ICONS.shuffleOff);
  $('playIcon').setAttribute('d', audio.paused ? ICONS.play : ICONS.pause);
  $('pl').classList.toggle('paused', audio.paused); $('sheet').classList.toggle('paused', audio.paused);
  drawWave();
}

// MA's loudness graph of the playing song stands in for the slider; the slider stays on top, unseen, for seeking.
const wave = { item: null, points: null, bars: null };
function askWave(item) {
  Object.assign(wave, { item: item || '', points: null, bars: null });
  post('action', { action: 'wave', item: item || '' });
  if (lyr.on) askLyrics();
  if (sheet.kind === 'browse') post('action', { action: 'now', item: item || '' });
}
// MA's loudness points as 200 bars, each the loudest point of its stretch.
function waveBars(p) {
  const size = p.length / 200;
  return Array.from({ length: 200 }, (_, n) => Math.max(0, ...p.slice(Math.floor(n * size), Math.floor((n + 1) * size))));
}
// The next track starts in the same stream: once the stream at `at` ms is past the track's end, it takes over.
function advance(s, at) {
  if (!(s.nextDuration >= 0 && s.trackDuration > 0 && at - s.trackOffset >= s.trackDuration)) return false;
  Object.assign(s, {
    trackOffset: s.trackOffset + s.trackDuration, trackDuration: s.nextDuration, item: s.nextItem,
    title: s.nextTitle, titleSize: s.nextTitleSize, artist: s.nextArtist,
    album: s.nextAlbum, quality: s.nextQuality, nextDuration: -1, nextItem: '',
  });
  if (s.nextImage) s.cover = s.background = s.nextImage;
  return true;
}
// MA's track (from the skill) shows once the stream at `at` ms reaches its start; new details of the shown one at once.
function due(s, track, at) {
  return track.trackOffset === undefined || track.trackOffset <= at || track.item === s.item;
}
let ahead = null;   // MA's track, held until the stream reaches it
function take(track) {
  if (!due(state, track, position())) { ahead = track; return; }
  ahead = null;
  const item = state.item;
  Object.assign(state, track);
  if (track.item !== item) { askWave(track.item); askQueue(); }
}
function drawWave() {
  const c = $('wave'), bars = wave.bars, W = c.clientWidth, H = c.clientHeight;
  if (!bars || !state.trackDuration || !W) return;
  if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
  const g = c.getContext('2d'), step = W / bars.length;
  const done = (seeking ? Number($('slider').value) : trackPosition()) / trackLength();
  g.clearRect(0, 0, W, H);
  bars.forEach((v, n) => {
    const h = Math.max(2, v * H);
    g.fillStyle = n / bars.length < done ? '#03a9f4' : 'rgba(255,255,255,.35)';
    g.fillRect(n * step, (H - h) / 2, Math.max(1, step * .6), h);
  });
}

// MA's now-playing bars, for any cover; the loop below moves them all.
const EQ = '<span class="eq"><i></i><i></i><i></i><i></i></span>';
function startPlayer() {
  // As MA's queue: the now-playing bars (playlist and browse) show the loudness around the play position, 10 times a second.
  setInterval(() => {
    const eqs = document.querySelectorAll('#plList .now .eq, #sheetBody .eq'), p = wave.points;
    eqs.forEach(eq => eq.classList.toggle('live', !!p));
    if (!eqs.length || !p || audio.paused || !state.trackDuration) return;
    const at = Math.floor(Math.min(1, trackPosition() / trackLength()) * (p.length - 1));
    eqs.forEach(eq => eq.querySelectorAll('i').forEach((bar, k) =>
      bar.style.transform = 'scaleY(' + Math.max(.25, p[Math.max(0, Math.min(p.length - 1, at - 2 + k))]) + ')'));
  }, 100);

  audio.addEventListener('timeupdate', () => {
    if (ahead && due(state, ahead, position())) take(ahead);
    // The next track shows without asking the skill.
    const item = state.nextItem;
    if (advance(state, position())) {
      askWave(item);
      // MA may not have the next song's stream details yet: ask again for whatever plays then.
      setTimeout(() => { if (!wave.bars && wave.item === item) askWave(); }, 5000);
      askQueue();
    }
    // MA's queue ends here: stop, or the stream plays on into old music.
    if (state.queueEnd > 0 && position() >= state.queueEnd - 1000) {
      state.queueEnd = 0; queueEnded = true; audio.pause();
    }
    render();
  });
  audio.addEventListener('playing', () => { busy(false); render(); });
  audio.addEventListener('pause', () => {
    if (queueEnded) { queueEnded = false; post('action', { action: 'queue_ended' }); }
    render();
  });
}

if (typeof module === 'object') module.exports = { waveBars, advance, due };   // for the Node tests
