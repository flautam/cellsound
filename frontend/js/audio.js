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

function playMix(urls, options) {
  const settings = options || {};
  stopAllSound(true);
  const ctx = audioContext();
  const master = ctx.createGain();
  master.gain.value = 0.0001;
  master.connect(ctx.destination);
  let buffers = [];
  let gains = [];
  let sources = [];
  let masks = (settings.heard || []).slice();
  let rate = settings.rate || 1;
  let offset = 0;
  let startedAt = 0;
  let alive = true;
  let boost = 1;
  let duration = 0;

  function applyVolume() {
    const now = ctx.currentTime;
    master.gain.cancelScheduledValues(now);
    master.gain.setValueAtTime(master.gain.value, now);
    master.gain.linearRampToValueAtTime(Math.max(0.0001, appVolume * boost), now + 0.03);
    let count = 0;
    masks.forEach(function (on) { if (on) count += 1; });
    const each = count ? 1 / Math.sqrt(count) : 0;
    gains.forEach(function (gain, index) {
      const level = masks[index] ? each : 0;
      gain.gain.cancelScheduledValues(now);
      if (!level) {
        gain.gain.setValueAtTime(0, now);
        return;
      }
      gain.gain.setValueAtTime(gain.gain.value, now);
      gain.gain.linearRampToValueAtTime(level, now + 0.02);
    });
  }

  function begin(at) {
    if (!buffers.length || !alive) return;
    sources.forEach(function (source) {
      source.onended = null;
      try { source.stop(); } catch (error) { /* already stopped */ }
    });
    sources = [];
    offset = Math.max(0, Math.min(Math.max(0, duration - 0.05), at || 0));
    const when = ctx.currentTime;
    let pending = 0;
    buffers.forEach(function (buffer, index) {
      if (!buffer || !gains[index] || offset >= buffer.duration - 0.02) return;
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.playbackRate.value = rate;
      source.connect(gains[index]);
      pending += 1;
      source.onended = function () {
        pending -= 1;
        if (pending > 0 || !alive) return;
        alive = false;
        activeSounds.delete(stop);
        if (settings.onEnded) settings.onEnded();
      };
      source.start(when, Math.min(offset, buffer.duration - 0.02));
      sources.push(source);
    });
    if (!pending) {
      alive = false;
      activeSounds.delete(stop);
      if (settings.onEnded) settings.onEnded();
      return;
    }
    startedAt = when;
  }

  function stop() {
    alive = false;
    sources.forEach(function (source) {
      source.onended = null;
      try { source.stop(); } catch (error) { /* already stopped */ }
    });
  }

  activeSounds.add(stop);
  Promise.all((urls || []).map(function (url) {
    return fetch(url).then(function (response) {
      if (!response.ok) return null;
      return response.arrayBuffer();
    }).then(function (bytes) {
      if (!bytes) return null;
      return ctx.decodeAudioData(bytes).catch(function () { return null; });
    }).catch(function () { return null; });
  })).then(function (decoded) {
    if (!alive) return;
    buffers = decoded;
    if (!buffers.some(Boolean)) {
      if (settings.onError) settings.onError();
      return;
    }
    if (settings.heard && settings.heard.length === buffers.length) {
      masks = settings.heard.slice();
    } else {
      masks = buffers.map(function () { return true; });
    }
    duration = 0;
    let peak = 0;
    buffers.forEach(function (buffer) {
      const gain = ctx.createGain();
      gain.gain.value = 0;
      gain.connect(master);
      gains.push(gain);
      if (!buffer) return;
      duration = Math.max(duration, buffer.duration);
      const samples = buffer.getChannelData(0);
      for (let i = 0; i < samples.length; i += 32) {
        peak = Math.max(peak, Math.abs(samples[i]));
      }
    });
    boost = peak > 0.01 ? Math.min(4, 0.85 / peak) : 1;
    applyVolume();
    begin((settings.ratio || 0) * duration);
  });

  return {
    setVolume: applyVolume,
    setRate: function (next) {
      const now = ctx.currentTime;
      const played = Math.max(0, (now - startedAt) * rate);
      offset = duration ? Math.min(duration, offset + played) : offset + played;
      startedAt = now;
      rate = next || 1;
      sources.forEach(function (source) { source.playbackRate.value = rate; });
    },
    setHeard: function (next) {
      masks = (next || []).slice();
      settings.heard = masks;
      applyVolume();
    },
    seekRatio: function (next) {
      if (!duration) return;
      const place = Math.max(0, Math.min(0.999, next)) * duration;
      if (alive) begin(place);
      else offset = place;
    },
    ready: function () { return duration > 0; },
    currentRatio: function () {
      if (!duration) return 0;
      const played = sources.length ? (ctx.currentTime - startedAt) * rate : 0;
      return Math.min(1, (offset + Math.max(0, played)) / duration);
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
