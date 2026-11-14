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
  const ctx = audioContext();
  const gain = ctx.createGain();
  gain.gain.value = Math.max(0.0001, appVolume);
  gain.connect(ctx.destination);
  let buffer = null;
  let source = null;
  let rate = settings.rate || 1;
  let offset = 0;
  let startedAt = 0;
  let alive = true;
  let boost = 1;

  function applyVolume() {
    gain.gain.value = Math.max(0.0001, appVolume * boost);
  }

  function begin(at) {
    if (!buffer || !alive) return;
    if (source) {
      source.onended = null;
      try { source.stop(); } catch (error) { /* already stopped */ }
    }
    offset = Math.max(0, Math.min(buffer.duration - 0.05, at || 0));
    source = ctx.createBufferSource();
    source.buffer = buffer;
    source.playbackRate.value = rate;
    source.connect(gain);
    source.onended = function () {
      if (!alive) return;
      alive = false;
      activeSounds.delete(stop);
      if (settings.onEnded) settings.onEnded();
    };
    source.start(0, offset);
    startedAt = ctx.currentTime;
  }

  function stop() {
    alive = false;
    if (!source) return;
    source.onended = null;
    try { source.stop(); } catch (error) { /* already stopped */ }
  }

  activeSounds.add(stop);
  fetch(url).then(function (response) {
    if (!response.ok) throw new Error("missing");
    return response.arrayBuffer();
  }).then(function (bytes) {
    return ctx.decodeAudioData(bytes);
  }).then(function (decoded) {
    const samples = decoded.getChannelData(0);
    let peak = 0;
    for (let index = 0; index < samples.length; index += 16) {
      peak = Math.max(peak, Math.abs(samples[index]));
    }
    boost = peak > 0.01 ? Math.min(6, 0.85 / peak) : 1;
    applyVolume();
    buffer = decoded;
    begin((settings.ratio || 0) * buffer.duration);
  }).catch(function () {
    if (settings.onError) settings.onError();
  });

  return {
    setVolume: applyVolume,
    setRate: function (next) {
      rate = next || 1;
      if (source) source.playbackRate.value = rate;
    },
    seekRatio: function (next) {
      if (!buffer) return;
      const place = Math.max(0, Math.min(0.999, next)) * buffer.duration;
      if (alive) begin(place);
      else offset = place;
    },
    ready: function () {
      return Boolean(buffer);
    },
    currentRatio: function () {
      if (!buffer) return 0;
      const played = source ? (ctx.currentTime - startedAt) * rate : 0;
      return Math.min(1, (offset + Math.max(0, played)) / buffer.duration);
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
