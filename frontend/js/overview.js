/* Overview: play the population sound, show traces, and read the summary. */
const CARDS = [
  { id: "tone", title: "Each cluster has its own tone", text: "Cluster 1 is the lowest tone and higher cluster numbers sit higher, up to cluster 10. The note stays recognizable, so you can tell clusters apart immediately.", example: "Hear three tones", cue: "tone", status: "Example: three cluster tones in a row, each tapping slowly." },
  { id: "pulse", title: "Faster pulses mean faster motion", text: "Pulse rate follows speed. Fast clusters tap quickly. Slow clusters tap slowly. The base tone does not change to another cluster's note.", example: "Hear pulses speed up", cue: "pulse", status: "Example: one tone, tapping slowly at first, then faster as the cluster speeds up." },
  { id: "all", title: "Checked clusters together", text: "Overview plays each checked cluster on its own tone at the same time. A fast pulse is the faster-moving cluster.", example: "Hear them together", cue: "all", status: "Example: two tones at once. The higher one pulses faster because that cluster is moving faster.", wide: true, ink: true }
];

const playButton = document.getElementById("play");
const playLabel = document.getElementById("play-label");
const position = document.getElementById("pos");
const positionText = document.getElementById("pos-visible");
const volume = document.getElementById("vol");
const volumeHint = document.getElementById("vol-hint");
const statusText = document.getElementById("status-text");
const pitchFill = document.getElementById("pitch-fill");
const beatDots = document.querySelectorAll("#beat-dots i");
const moveEnd = document.getElementById("move-end");
const rate = document.getElementById("rate");
const clusterVideo = document.getElementById("cluster-video");
const clusterVideoWrap = document.getElementById("cluster-video-wrap");

let summary = null;
let traces = null;
let totalFrames = 200;
let totalMinutes = 1000;
let timed = false;
let frame = 0;
let playing = false;
let timer = null;
let tone = null;
let traceFollows = false;
const heard = {};

function playRatio() {
  return totalFrames ? Math.max(0, Math.min(1, frame / totalFrames)) : 0;
}

function syncClusterVideo(shouldPlay) {
  if (!clusterVideo || !clusterVideo.src) return;
  const ratio = playRatio();
  const duration = Number(clusterVideo.duration);
  if (Number.isFinite(duration) && duration > 0) {
    const target = Math.min(duration - 0.04, ratio * duration);
    if (!Number.isFinite(clusterVideo.currentTime) || Math.abs(clusterVideo.currentTime - target) > 0.08) {
      try { clusterVideo.currentTime = Math.max(0, target); } catch (error) { /* not seekable yet */ }
    }
  }
  clusterVideo.playbackRate = Number(rate.value || 1);
  clusterVideo.muted = true;
  if (shouldPlay) {
    const playPromise = clusterVideo.play();
    if (playPromise && playPromise.catch) playPromise.catch(function () { /* autoplay may wait for gesture */ });
  } else {
    clusterVideo.pause();
  }
}

function say(text) {
  if (statusText.textContent !== text) statusText.textContent = text;
}

function clock(minutes) {
  const whole = Math.max(0, Math.round(minutes));
  const hours = Math.floor(whole / 60);
  const mins = whole % 60;
  if (hours === 0) return mins + " minutes";
  if (mins === 0) return hours + " hours";
  return hours + " hours " + mins + " minutes";
}

function positionWords() {
  if (!timed) return "frame " + frame + " of " + totalFrames;
  const at = totalFrames ? (frame / totalFrames) * totalMinutes : 0;
  return clock(at) + " of " + clock(totalMinutes);
}

function showPosition() {
  position.value = String(frame);
  position.max = String(totalFrames);
  const words = positionWords();
  position.setAttribute("aria-valuetext", words);
  positionText.textContent = words;
}

function paintCues(preview) {
  const mix = totalFrames ? frame / totalFrames : 0;
  const closeness = 0.5 + 0.5 * Math.cos(mix * Math.PI);
  let beat = Math.min(beatDots.length - 1, Math.round(closeness * (beatDots.length - 1)));
  if (preview === "tone") beat = 1;
  if (preview === "pulse" || preview === "all") beat = beatDots.length - 1;
  if (pitchFill) pitchFill.style.transform = "scaleX(0.62)";
  beatDots.forEach(function (dot, index) {
    dot.classList.toggle("on", index <= beat);
  });
}

function setPlaying(on) {
  playing = on;
  playLabel.textContent = on ? "Pause" : "Play overview";
  const icon = playButton.querySelector("path");
  if (icon) icon.setAttribute("d", on ? "M6 4h4v16H6zM14 4h4v16h-4z" : "M7 4v16l13-8z");
  if (!on) tone = null;
  paintCues();
}

function halt(message) {
  clearInterval(timer);
  timer = null;
  stopAllSound(true);
  syncClusterVideo(false);
  setPlaying(false);
  if (message) say(message);
}

function finish() {
  if (!playing) return;
  frame = totalFrames;
  showPosition();
  syncClusterVideo(false);
  halt("Overview finished.");
  if (moveEnd.checked) {
    const heading = document.getElementById("desc-h");
    heading.focus();
  }
}

function followRecording() {
  if (!tone || !tone.ready()) return;
  frame = Math.round(Math.min(1, tone.currentRatio()) * totalFrames);
  showPosition();
  paintCues();
  drawTraces();
  syncClusterVideo(true);
}

function clusterRows() {
  return (summary && summary.clusters) || [];
}

function isHeard(id) {
  const key = Number(id);
  if (!Object.prototype.hasOwnProperty.call(heard, key)) return true;
  return Boolean(heard[key]);
}

function mixPlan() {
  const rows = clusterRows();
  return {
    urls: rows.map(function (cluster) {
      return audioUrl(jobId, cluster.audio || ("cluster_" + cluster.cluster_id));
    }),
    heard: rows.map(function (cluster) { return isHeard(cluster.cluster_id); })
  };
}

function heardSentence() {
  const chosen = clusterRows().filter(function (cluster) { return isHeard(cluster.cluster_id); });
  if (!chosen.length) return "No clusters are in the overview sound. Check a box to bring one back.";
  const names = chosen.map(function (cluster) { return "cluster " + cluster.cluster_id; });
  if (chosen.length === clusterRows().length) return "Hearing every cluster: " + names.join(", ") + ".";
  return "Hearing " + names.join(", ") + ". The others are quiet.";
}

function syncHeard() {
  const note = document.getElementById("hear-note");
  if (note) note.textContent = heardSentence();
  if (tone && tone.setHeard) tone.setHeard(mixPlan().heard);
  if (traces) drawTraces();
}

function buildHearBoxes() {
  const box = document.getElementById("hear-boxes");
  if (!box) return;
  box.replaceChildren();
  const rows = clusterRows();
  const many = rows.length > 2;
  rows.forEach(function (cluster) {
    const id = Number(cluster.cluster_id);
    if (!Object.prototype.hasOwnProperty.call(heard, id)) {
      heard[id] = many ? (id === 1 || id === 2) : true;
    }
    const label = document.createElement("label");
    label.className = "check-label";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = "hear-" + id;
    input.checked = heard[id];
    const text = document.createElement("span");
    text.textContent = "Cluster " + id + ", " + cluster.name + ", Tone " + id;
    input.addEventListener("change", function () {
      heard[id] = input.checked;
      syncHeard();
    });
    label.appendChild(input);
    label.appendChild(text);
    box.appendChild(label);
  });
  syncHeard();
}

function begin() {
  if (!clusterRows().length) {
    say("The cluster recordings are not ready yet.");
    return;
  }
  if (frame >= totalFrames) frame = 0;
  traceFollows = true;
  showPosition();
  syncClusterVideo(true);
  const plan = mixPlan();
  tone = playMix(plan.urls, {
    rate: Number(rate.value || 1),
    ratio: playRatio(),
    heard: plan.heard,
    onEnded: finish,
    onError: function () {
      halt("The overview recording could not play.");
    }
  });
  setPlaying(true);
  say("Playing with the cluster video from " + positionWords() + ". " + heardSentence());
  drawTraces();
  clearInterval(timer);
  timer = setInterval(followRecording, 100);
}

function ns(name) {
  return document.createElementNS("http://www.w3.org/2000/svg", name);
}

function clipPath(path, ratio) {
  if (ratio == null || !path.length) return path;
  const framed = path[0].length > 2;
  if (framed) {
    const maxFrame = traces.max_frame != null ? Number(traces.max_frame) : Number(path[path.length - 1][2]);
    const limit = ratio * maxFrame;
    const shown = path.filter(function (point) { return Number(point[2]) <= limit + 0.001; });
    return shown.length ? shown : [path[0]];
  }
  const keep = Math.max(1, Math.round(path.length * ratio));
  return path.slice(0, keep);
}

function drawTraces() {
  const svg = document.getElementById("traces");
  const paths = document.getElementById("trace-paths");
  const marker = document.getElementById("trace-poa");
  const title = document.getElementById("trace-title");
  const show = document.getElementById("trace-show").value;
  const width = traces.width || 512;
  const height = traces.height || 512;
  const scale = Math.min(width, height) / 512;
  svg.setAttribute("viewBox", "0 0 " + width + " " + height);
  svg.style.aspectRatio = width + " / " + height;
  const backdrop = document.getElementById("trace-backdrop");
  backdrop.setAttribute("width", String(width));
  backdrop.setAttribute("height", String(height));
  const image = document.getElementById("trace-image");
  const ratio = traceFollows && totalFrames ? Math.max(0, Math.min(1, frame / totalFrames)) : null;
  let imageUrl = traces.image_url || frameImageUrl(jobId);
  if (ratio != null && traces.frames_url && Number(traces.frame_count) > 0) {
    const last = Math.max(0, Number(traces.frame_count) - 1);
    imageUrl = frameStepUrl(jobId, Math.round(ratio * last), traces.frames_url);
  }
  image.setAttribute("width", String(width));
  image.setAttribute("height", String(height));
  if (image.getAttribute("href") !== imageUrl) {
    image.setAttribute("href", imageUrl);
    image.setAttributeNS("http://www.w3.org/1999/xlink", "href", imageUrl);
  }
  if (ratio != null) svg.setAttribute("data-step", String(frame));
  else svg.removeAttribute("data-step");
  paths.replaceChildren();
  const radius = Number(traces.max_radius || 28);
  traces.clusters.forEach(function (cluster) {
    const group = ns("g");
    const selected = show === "all" || show === String(cluster.cluster_id);
    const on = isHeard(cluster.cluster_id);
    group.setAttribute("opacity", selected && on ? "1" : "0.12");
    let head = null;
    (cluster.paths || []).forEach(function (path, pathIndex) {
      const shown = clipPath(path, ratio);
      if (pathIndex === 0 && shown.length) head = shown[shown.length - 1];
      if (shown.length === 1) {
        const dot = ns("circle");
        dot.setAttribute("cx", String(shown[0][0]));
        dot.setAttribute("cy", String(shown[0][1]));
        dot.setAttribute("r", String(5 * scale));
        dot.setAttribute("fill", cluster.color);
        group.appendChild(dot);
        return;
      }
      const points = shown.map(function (point) { return point[0] + "," + point[1]; }).join(" ");
      const halo = ns("polyline");
      halo.setAttribute("points", points);
      halo.setAttribute("fill", "none");
      halo.setAttribute("stroke", "var(--on-header)");
      halo.setAttribute("stroke-width", String(8 * scale));
      halo.setAttribute("stroke-linecap", "round");
      halo.setAttribute("stroke-linejoin", "round");
      const line = ns("polyline");
      line.setAttribute("points", points);
      line.setAttribute("fill", "none");
      line.setAttribute("stroke", cluster.color);
      line.setAttribute("stroke-width", String(4 * scale));
      line.setAttribute("stroke-linecap", "round");
      line.setAttribute("stroke-linejoin", "round");
      group.appendChild(halo);
      group.appendChild(line);
    });
    const center = (ratio != null && head)
      ? { x: head[0], y: head[1] }
      : (cluster.centroid || (head ? { x: head[0], y: head[1] } : null));
    if (center) {
      const ball = ns("circle");
      ball.setAttribute("cx", String(center.x));
      ball.setAttribute("cy", String(center.y));
      ball.setAttribute("r", String(radius));
      ball.setAttribute("fill", cluster.color);
      ball.setAttribute("fill-opacity", "0.12");
      ball.setAttribute("stroke", cluster.color);
      ball.setAttribute("stroke-width", String(2 * scale));
      group.appendChild(ball);
    }
    paths.appendChild(group);
  });
  const poa = traces.poa || { x: 256, y: 256 };
  marker.replaceChildren();
  marker.setAttribute("transform", "translate(" + poa.x + " " + poa.y + ") scale(" + scale + ")");
  [
    ["circle", { r: "22", stroke: "var(--on-header)", "stroke-width": "8" }],
    ["circle", { r: "22", stroke: "var(--danger)", "stroke-width": "4" }],
    ["path", { d: "M0 -36v16M0 20v16M-36 0h16M20 0h16", stroke: "var(--on-header)", "stroke-width": "8" }],
    ["path", { d: "M0 -36v16M0 20v16M-36 0h16M20 0h16", stroke: "var(--danger)", "stroke-width": "4" }]
  ].forEach(function (spec) {
    const part = ns(spec[0]);
    part.setAttribute("fill", "none");
    part.setAttribute("stroke-linecap", "round");
    Object.keys(spec[1]).forEach(function (key) { part.setAttribute(key, spec[1][key]); });
    marker.appendChild(part);
  });
  const chosen = summary.clusters.find(function (cluster) { return String(cluster.cluster_id) === show; });
  const heardIds = clusterRows().filter(function (cluster) { return isHeard(cluster.cluster_id); }).map(function (cluster) { return cluster.cluster_id; });
  let words = traces.description;
  if (!heardIds.length) words = "No clusters are in the overview sound. Chunk trails are faded.";
  else if (heardIds.length < clusterRows().length) words = "Showing cluster " + heardIds.join(" and cluster ") + ". Other chunks are faded.";
  if (show !== "all" && chosen) {
    words = isHeard(chosen.cluster_id)
      ? "Showing chunk " + chosen.cluster_id + ", " + chosen.name + ", only. Other chunks are faded."
      : "Chunk " + chosen.cluster_id + " is faded because it is not in the overview sound.";
  }
  title.textContent = words;
  svg.setAttribute("aria-label", words);
}

function fillLegend() {
  const list = document.getElementById("trace-legend");
  const show = document.getElementById("trace-show");
  list.replaceChildren();
  summary.clusters.forEach(function (cluster) {
    const option = document.createElement("option");
    option.value = String(cluster.cluster_id);
    option.textContent = "Chunk " + cluster.cluster_id + " only";
    show.appendChild(option);
    const item = document.createElement("li");
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.setAttribute("aria-hidden", "true");
    swatch.style.background = cluster.color;
    const text = document.createElement("span");
    const unusual = cluster.unusual ? " (unusual)" : "";
    text.innerHTML = "<strong>Chunk " + cluster.cluster_id + "</strong> · " + cluster.name + unusual + " · ~" + cluster.n_cells + " cells";
    item.appendChild(swatch);
    item.appendChild(text);
    list.appendChild(item);
  });
  const poaItem = document.createElement("li");
  poaItem.innerHTML = "<span class=\"poa-chip\" aria-hidden=\"true\"><svg width=\"26\" height=\"26\" viewBox=\"0 0 56 56\"><circle cx=\"28\" cy=\"28\" r=\"18\" fill=\"none\" stroke=\"var(--on-header)\" stroke-width=\"8\"></circle><circle cx=\"28\" cy=\"28\" r=\"18\" fill=\"none\" stroke=\"var(--danger)\" stroke-width=\"4\"></circle><path d=\"M28 4v12M28 40v12M4 28h12M40 28h12\" fill=\"none\" stroke=\"var(--on-header)\" stroke-width=\"8\" stroke-linecap=\"round\"></path><path d=\"M28 4v12M28 40v12M4 28h12M40 28h12\" fill=\"none\" stroke=\"var(--danger)\" stroke-width=\"4\" stroke-linecap=\"round\"></path></svg></span> Point of Audio";
  list.appendChild(poaItem);
}

function fillSummary() {
  const pop = summary.population || {};
  const overview = summary.overview || {};
  const units = summary.units || { speed: "", angular: "" };
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem("cellsound-calibration") || "null"); } catch (error) { saved = null; }
  const minutes = saved && saved.minutes_per_frame ? Number(saved.minutes_per_frame) : 0;
  let nFrames = Number(pop.n_frames || 0);
  if (!nFrames && !(minutes > 0 && pop.duration_min != null)) nFrames = Number(pop.duration_min || 0);
  timed = minutes > 0 && pop.duration_min != null;
  totalMinutes = timed ? Number(pop.duration_min) : nFrames;
  totalFrames = nFrames || (timed ? Math.max(1, Math.round(Number(pop.duration_min) / minutes)) : Math.max(1, Math.round(totalMinutes)));
  const hours = timed ? (Number(pop.duration_min) / 60) : 0;
  const span = timed
    ? ((hours >= 1 ? (Math.round(hours * 10) / 10) + " hours" : Math.round(Number(pop.duration_min)) + " minutes"))
    : "no time calibration, timeline in frames";
  document.getElementById("hero-lede").textContent =
    (pop.n_cells || 0) + " cells in " + (pop.n_clusters || 0) + " chunks over " + totalFrames +
    " frames (" + span + "). Read the overview, watch the cluster video, then open chunks.";
  document.getElementById("desc-text").textContent =
    overview.description || pop.description || "No overview is ready yet.";
  const mainCount = Math.round((pop.n_cells || 0) * (pop.pct_main_pattern || 0) / 100);
  const unusual = (summary.clusters || []).some(function (cluster) { return cluster.unusual; });
  const trend = pop.trend_compass ? ("Drift " + pop.trend_compass) : "Net population drift";
  const rows = [
    ["Average speed", (pop.mean_speed != null ? pop.mean_speed : "—") + " " + (units.speed || ""), "Typical chunk speed"],
    ["Main trend", trend + (pop.trend_speed != null ? (" · " + pop.trend_speed + " " + (units.speed || "")) : ""), "Chunks are ranked by how well they match this"],
    ["Toward or away", (pop.mean_radial_v != null ? pop.mean_radial_v : "—") + " " + (units.speed || ""), "Negative means toward the Point of Audio"],
    ["Following the main pattern", (pop.pct_main_pattern != null ? pop.pct_main_pattern : "—") + "%", mainCount + " of " + (pop.n_cells || 0) + " cells"],
    ["Chunks found", String(pop.n_clusters || 0), unusual ? "Some chunks move against the trend" : "Ranked for listening next"]
  ];
  const body = document.getElementById("stats-body");
  body.replaceChildren();
  rows.forEach(function (row) {
    const tr = document.createElement("tr");
    tr.innerHTML = "<th scope=\"row\">" + row[0] + "</th><td>" + row[1] + "</td><td>" + row[2] + "</td>";
    body.appendChild(tr);
  });
  const trust = summary.reliability || {};
  document.getElementById("trust-h").textContent = "Tracking reliability: " + (trust.level || "unknown") + (trust.note ? ", with one note" : "");
  document.getElementById("trust-note").textContent = trust.note ? trust.note + " Treat those cells' values with care." : "";
  const explore = document.getElementById("explore");
  explore.textContent = "Explore the " + (pop.n_clusters || 0) + " chunks";
  explore.href = hrefWithJob("clusters.html");
  if (traces && traces.cluster_video_url && clusterVideo && clusterVideoWrap) {
    clusterVideo.src = "/api/jobs/" + encodeURIComponent(jobId) + "/clusters_overlay.mp4?v=" + Date.now();
    clusterVideoWrap.hidden = false;
    clusterVideo.onloadedmetadata = function () {
      syncClusterVideo(false);
    };
  } else if (clusterVideoWrap) {
    clusterVideoWrap.hidden = true;
  }
  showPosition();
  syncClusterVideo(false);
}

function buildCards() {
  const wrap = document.getElementById("how-cards");
  CARDS.forEach(function (card) {
    const box = document.createElement("div");
    box.className = "listen-card" + (card.wide ? " wide" : "") + (card.ink ? " ink-card" : "");
    if (!card.ink) box.style.background = card.id === "tone" ? "var(--sky)" : "var(--mint)";
    const title = document.createElement("h3");
    title.textContent = card.title;
    const copy = document.createElement("p");
    copy.textContent = card.text;
    const hear = document.createElement("button");
    hear.type = "button";
    hear.className = "btn btn-quiet";
    hear.textContent = card.example;
    hear.addEventListener("click", function () {
      playCue(card.cue);
      say(card.status);
      paintCues(card.cue);
      window.setTimeout(function () { paintCues(); }, 1400);
    });
    box.appendChild(title);
    box.appendChild(copy);
    box.appendChild(hear);
    wrap.appendChild(box);
  });
}

playButton.addEventListener("click", function () {
  if (playing) {
    halt("Paused at " + positionWords() + ".");
    return;
  }
  begin();
});

document.getElementById("replay").addEventListener("click", function () {
  frame = 0;
  begin();
});

document.getElementById("stop").addEventListener("click", function () {
  halt("All sound stopped.");
});

document.addEventListener("cellsound-stopped", function () {
  if (!playing) return;
  clearInterval(timer);
  timer = null;
  syncClusterVideo(false);
  setPlaying(false);
  say("All sound stopped.");
});

position.addEventListener("input", function () {
  frame = Number(position.value);
  traceFollows = true;
  showPosition();
  paintCues();
  if (tone && totalFrames) tone.seekRatio(playRatio());
  if (traces) drawTraces();
  syncClusterVideo(playing);
});

volume.addEventListener("input", function () {
  setAppVolume(volume.value);
  volume.setAttribute("aria-valuetext", "Volume " + volume.value + " percent");
  volumeHint.textContent = "Volume " + volume.value + " percent. Separate from your screen reader.";
  if (tone) tone.setVolume();
  paintCues();
});

rate.addEventListener("change", function () {
  const next = Number(rate.value || 1);
  if (tone) tone.setRate(next);
  if (clusterVideo) clusterVideo.playbackRate = next;
});

document.getElementById("trace-show").addEventListener("change", drawTraces);

document.getElementById("how-btn").addEventListener("click", function () {
  const panel = document.getElementById("how-panel");
  const open = panel.hidden;
  panel.hidden = !open;
  this.setAttribute("aria-expanded", open ? "true" : "false");
  document.getElementById("how-state").textContent = open ? "Hide" : "Show";
});

const pauseButton = document.querySelector("[data-pause-mirror]");
if (pauseButton) {
  pauseButton.addEventListener("click", function () {
    setPauseAnimations(pauseButton.getAttribute("aria-pressed") !== "true");
  });
}

window.addEventListener("pagehide", function () {
  clearInterval(timer);
  syncClusterVideo(false);
  stopAllSound(true);
});

setAppVolume(70);
buildCards();

const jobId = currentJobId() || "a1b2c3";
Promise.all([getSummary(jobId), getTraces(jobId)]).then(function (results) {
  summary = results[0];
  traces = results[1];
  function byClusterId(a, b) { return Number(a.cluster_id) - Number(b.cluster_id); }
  if (summary.clusters) summary.clusters = summary.clusters.slice().sort(byClusterId);
  if (traces.clusters) traces.clusters = traces.clusters.slice().sort(byClusterId);
  fillSummary();
  buildHearBoxes();
  fillLegend();
  drawTraces();
  paintCues();
}).catch(function (error) {
  say(error.message);
});
