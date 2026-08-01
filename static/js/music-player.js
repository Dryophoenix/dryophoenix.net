(function () {
  'use strict';

  function fmt(s) {
    if (!isFinite(s)) return '0:00';
    var m = Math.floor(s / 60);
    var sec = Math.floor(s % 60);
    return m + ':' + (sec < 10 ? '0' : '') + sec;
  }

  function setHidden(el, isHidden) {
    // el.hidden is a no-op here: the reflected IDL property only works on
    // HTML elements in this engine, and silently drops on these inline
    // <svg> icons instead of erroring -- toggle the attribute directly.
    if (isHidden) { el.setAttribute('hidden', ''); }
    else { el.removeAttribute('hidden'); }
  }

  function initPlayer(player) {
    var audio = new Audio(player.dataset.src);
    audio.preload = 'none';

    var toggle = player.querySelector('.player-toggle');
    var iconPlay = player.querySelector('.icon-play');
    var iconPause = player.querySelector('.icon-pause');
    var seek = player.querySelector('.player-seek');
    var current = player.querySelector('.player-current');
    var duration = player.querySelector('.player-duration');
    var seeking = false;

    function setPlaying(isPlaying) {
      setHidden(iconPlay, isPlaying);
      setHidden(iconPause, !isPlaying);
      toggle.setAttribute('aria-label', isPlaying ? 'Pause' : 'Play');
    }

    toggle.addEventListener('click', function () {
      if (audio.paused) { audio.play(); } else { audio.pause(); }
    });

    audio.addEventListener('play', function () {
      if (window.__dryoActiveAudio && window.__dryoActiveAudio !== audio) {
        window.__dryoActiveAudio.pause();
      }
      window.__dryoActiveAudio = audio;
      setPlaying(true);
    });
    audio.addEventListener('pause', function () { setPlaying(false); });
    audio.addEventListener('ended', function () {
      setPlaying(false);
      seek.value = 0;
      current.textContent = '0:00';
    });

    audio.addEventListener('loadedmetadata', function () {
      seek.max = audio.duration;
      duration.textContent = fmt(audio.duration);
    });

    audio.addEventListener('timeupdate', function () {
      if (seeking) return;
      seek.value = audio.currentTime;
      current.textContent = fmt(audio.currentTime);
    });

    seek.addEventListener('input', function () {
      seeking = true;
      current.textContent = fmt(seek.value);
    });
    seek.addEventListener('change', function () {
      audio.currentTime = seek.value;
      seeking = false;
    });
  }

  document.querySelectorAll('.player').forEach(initPlayer);
}());
