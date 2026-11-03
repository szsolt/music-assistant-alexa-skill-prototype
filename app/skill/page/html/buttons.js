// Buttons that wait for MA's new stream pause the page meanwhile.
function waitFor(action) { audio.pause(); busy(true); post('action', action); }
function toggle(key, action) { state[key] = state[key] === 1 ? 0 : 1; post('action', { action, on: state[key] === 1 }); render(); }
const SKIP_BACK_MS = 10000, SKIP_FORWARD_MS = 30000;   // MA's app's
function startButtons() {
  $('Previous').onclick = () => waitFor({ action: 'previous' });
  $('Next').onclick = () => waitFor({ action: 'next' });
  $('PlayPause').onclick = () => {
    if (audio.paused) { busy(true); post('action', { action: 'play', position: trackPosition() }); }
    else { post('action', { action: 'pause', position: trackPosition() }); audio.pause(); }
  };
  $('FavSong').onclick = () => toggle('songFavorite', 'favorite_song');
  $('FavAlbum').onclick = () => toggle('albumFavorite', 'favorite_album');
  $('Shuffle').onclick = () => toggle('shuffle', 'shuffle');
  $('SkipBack').onclick = () => waitFor({ action: 'seek', position: Math.max(0, trackPosition() - SKIP_BACK_MS) });
  $('SkipForward').onclick = () => waitFor({ action: 'seek', position: Math.min(trackLength() - 1000, trackPosition() + SKIP_FORWARD_MS) });
  $('Repeat').onclick = () => post('action', { action: 'repeat', mode: (state.repeat + 1) % 3 });
  $('slider').oninput = () => { seeking = true; $('elapsed').textContent = time($('slider').value); drawWave(); };
  $('slider').onchange = () => { seeking = false; waitFor({ action: 'seek', position: Number($('slider').value) }); };
}
