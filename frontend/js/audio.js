/* One place that starts and stops app sound. Nothing plays by itself. */
const activeSounds = new Set();
let audioCtx = null;
let appVolume = 0.7;

function setAppVolume(percent) {
  appVolume = Math.max(0, Math.min(100, Number(percent) || 0)) / 100;
}

function audioContext() {
  if (!audioCtx) {
    audioCtx = new AudioContext();
  }
  if (audioCtx.state === "suspended") {
    audioCtx.resume();
  }
  return audioCtx;
}

function stopAllSound(silent) {
  activeSounds.forEach(function (stop) {
    stop();
  });
  activeSounds.clear();
  if (!silent) document.dispatchEvent(new CustomEvent("cellsound-stopped"));
}

let analyser = null;
let speakerNode = null;

function speaker() {
  const ctx = audioContext();
  if (!speakerNode) {
    speakerNode = ctx.createGain();
    analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.55;
    speakerNode.connect(analyser);
    analyser.connect(ctx.destination);
  }
  return speakerNode;
}

function readWave() {
  if (!analyser) return new Uint8Array(0);
  const data = new Uint8Array(analyser.fftSize);
  analyser.getByteTimeDomainData(data);
  return data;
}

function scheduleTone(when, frequency, panValue, duration) {
  const ctx = audioContext();
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  const pan = ctx.createStereoPanner();
  osc.type = "sine";
  osc.frequency.value = frequency;
  pan.pan.value = panValue;
  gain.gain.setValueAtTime(0.0001, when);
  gain.gain.exponentialRampToValueAtTime(Math.max(0.0001, 0.12 * appVolume), when + 0.03);
  gain.gain.exponentialRampToValueAtTime(0.0001, when + duration);
  osc.connect(gain);
  gain.connect(pan);
  pan.connect(speaker());
  osc.start(when);
  osc.stop(when + duration + 0.05);
  function stop() {
    try {
      osc.stop();
    } catch (error) {
      /* already stopped */
    }
  }
  activeSounds.add(stop);
  osc.onended = function () {
    activeSounds.delete(stop);
  };
}

function playRecording(url, options) {
  const settings = options || {};
  stopAllSound(true);
  const audio = new Audio(url);
  audio.preload = "auto";
  const ratio = settings.ratio || 0;

  function applyVolume() {
    audio.volume = Math.max(0, Math.min(1, appVolume));
  }

  audio.addEventListener("loadedmetadata", function () {
    applyVolume();
    audio.playbackRate = settings.rate || 1;
    if (ratio > 0 && audio.duration) {
      audio.currentTime = Math.min(audio.duration * ratio, Math.max(0, audio.duration - 0.05));
    }
  });
  audio.addEventListener("error", function () {
    if (settings.onError) settings.onError();
  });
  audio.addEventListener("ended", function () {
    activeSounds.delete(stop);
    if (settings.onEnded) settings.onEnded();
  });

  function stop() {
    audio.pause();
  }

  activeSounds.add(stop);
  const started = audio.play();
  if (started && started.catch) {
    started.catch(function () {
      if (settings.onError) settings.onError();
    });
  }
  return {
    setVolume: applyVolume,
    setRate: function (rate) {
      audio.playbackRate = rate || 1;
    },
    seekRatio: function (next) {
      if (!audio.duration) return;
      audio.currentTime = Math.max(0, Math.min(0.999, next)) * audio.duration;
    },
    ready: function () {
      return Boolean(audio.duration);
    },
    currentRatio: function () {
      if (!audio.duration) return 0;
      return audio.currentTime / audio.duration;
    }
  };
}

function playCue(kind) {
  const job = typeof currentJobId === "function" ? currentJobId() : "";
  if (!job) return;
  stopAllSound();
  playRecording(audioUrl(job, "example_" + kind));
}

function playSoundCheck() {
  stopAllSound(true);
  const start = audioContext().currentTime + 0.05;
  scheduleTone(start, 523, -0.8, 0.35);
  scheduleTone(start + 0.5, 659, 0.8, 0.35);
}

document.addEventListener("keydown", function (event) {
  if (event.key === "Escape") {
    stopAllSound();
  }
});
