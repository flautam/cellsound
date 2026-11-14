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

function playCue(kind, spatial) {
  stopAllSound(true);
  const start = audioContext().currentTime + 0.05;
  const side = spatial ? -0.7 : 0;
  if (kind === "pitch") {
    scheduleTone(start, 330, side, 0.45);
    scheduleTone(start + 0.5, 660, spatial ? 0.7 : 0, 0.45);
    return;
  }
  if (kind === "volume") {
    scheduleTone(start, 440, side, 0.4);
    scheduleTone(start + 0.5, 440, spatial ? 0.4 : 0, 0.7);
    return;
  }
  if (kind === "beat") {
    scheduleTone(start, 220, side, 0.12);
    scheduleTone(start + 0.45, 220, side, 0.12);
    scheduleTone(start + 0.75, 220, side, 0.12);
    scheduleTone(start + 0.95, 220, side, 0.12);
    return;
  }
  scheduleTone(start, 392, side, 0.35);
  scheduleTone(start + 0.4, 523, spatial ? 0.5 : 0, 0.45);
}

function startOverviewTone(spatial) {
  stopAllSound(true);
  const ctx = audioContext();
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  const pan = ctx.createStereoPanner();
  osc.type = "sine";
  osc.frequency.value = 392;
  pan.pan.value = spatial ? -0.4 : 0;
  gain.gain.value = Math.max(0.0001, 0.08 * appVolume);
  osc.connect(gain);
  gain.connect(pan);
  pan.connect(speaker());
  osc.start();
  function stop() {
    try { osc.stop(); } catch (error) { /* already stopped */ }
  }
  activeSounds.add(stop);
  osc.onended = function () { activeSounds.delete(stop); };
  return {
    glide: function (frame, total) {
      const mix = total ? frame / total : 0;
      osc.frequency.setTargetAtTime(320 + mix * 280, ctx.currentTime, 0.08);
      if (spatial) pan.pan.setTargetAtTime(Math.sin(mix * 6) * 0.7, ctx.currentTime, 0.08);
      gain.gain.setTargetAtTime(Math.max(0.0001, 0.08 * appVolume), ctx.currentTime, 0.05);
    }
  };
}

function clusterVoice(name) {
  const key = String(name || "piano").toLowerCase();
  const voices = {
    piano: { type: "sine", frequency: 392, gain: 0.08 },
    marimba: { type: "triangle", frequency: 494, gain: 0.07 },
    cello: { type: "triangle", frequency: 196, gain: 0.08 },
    flute: { type: "sine", frequency: 587, gain: 0.06 },
    bell: { type: "sine", frequency: 784, gain: 0.05 },
    "soft synth": { type: "triangle", frequency: 330, gain: 0.06 }
  };
  return voices[key] || voices.piano;
}

function playClusterTone(name) {
  stopAllSound(true);
  const ctx = audioContext();
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  const voice = clusterVoice(name);
  osc.type = voice.type;
  osc.frequency.value = voice.frequency;
  gain.gain.value = Math.max(0.0001, voice.gain * appVolume);
  osc.connect(gain);
  gain.connect(speaker());
  osc.start();
  function stop() {
    try { osc.stop(); } catch (error) { /* already stopped */ }
  }
  activeSounds.add(stop);
  osc.onended = function () { activeSounds.delete(stop); };
  return {
    setInstrument: function (next) {
      const chosen = clusterVoice(next);
      osc.type = chosen.type;
      osc.frequency.setTargetAtTime(chosen.frequency, ctx.currentTime, 0.08);
      gain.gain.setTargetAtTime(Math.max(0.0001, chosen.gain * appVolume), ctx.currentTime, 0.08);
    }
  };
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
