/* Overview: play the population sound, show traces, and read the summary. */
const CARDS = [
  { id: "pitch", title: "Pitch = direction", text: "A higher note means cells are moving toward the Point of Audio. A lower note means they are moving away. A still cell stays on the middle note.", example: "Hear pitch example", cue: "pitch", status: "Example: a tone rising as a cell moves toward the Point of Audio, then falling as it moves away." },
  { id: "volume", title: "Volume = closeness", text: "The closest cells are loudest. Middle cells are quieter. The farthest cells are soft, and their high notes drop away.", example: "Hear volume example", cue: "volume", status: "Example: a tone starting soft, then getting louder as the cell comes close." },
  { id: "beat", title: "Beat = speed", text: "A slow beat means a cell is barely moving. Faster cells tap more often.", example: "Hear beat example", cue: "beat", status: "Example: a slow pulse speeding up to a quick beat." },
  { id: "all", title: "All three together", text: "A fast cell moving toward a nearby Point of Audio sounds loud, high, and quick. Each cluster has its own instrument, so one group can be followed inside the mix.", example: "Hear them together", cue: "all", status: "Example: all three cues at once. A close, fast cell moving inward sounds loud, high, and quick.", wide: true, ink: true }
];

const playButton = document.getElementById("play");
const playLabel = document.getElementById("play-label");
const position = document.getElementById("pos");
const positionText = document.getElementById("pos-visible");
const volume = document.getElementById("vol");
const volumeHint = document.getElementById("vol-hint");
const statusText = document.getElementById("status-text");
const pitchFill = document.getElementById("pitch-fill");
const volumeFill = document.getElementById("volume-fill");
const beatDots = document.querySelectorAll("#beat-dots i");
const moveEnd = document.getElementById("move-end");
const rate = document.getElementById("rate");

let summary = null;
let traces = null;
let totalFrames = 200;
let totalMinutes = 1000;
let timed = false;
let frame = 0;
let playing = false;
let timer = null;
let tone = null;

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
  let pitch = 0.12 + mix * 0.88;
  let loud = (Number(volume.value) / 100) * (0.3 + 0.7 * closeness);
  let beat = Math.min(beatDots.length - 1, Math.floor(mix * beatDots.length));
  if (preview === "pitch") pitch = 0.92;
  if (preview === "volume") loud = 0.92;
  if (preview === "beat") beat = beatDots.length - 1;
  if (preview === "all") {
    pitch = 0.84;
    loud = 0.88;
    beat = beatDots.length - 2;
  }
  pitchFill.style.transform = "scaleX(" + pitch.toFixed(3) + ")";
  volumeFill.style.transform = "scaleX(" + Math.max(0.08, loud).toFixed(3) + ")";
  beatDots.forEach(function (dot, index) {
    dot.classList.toggle("on", index === beat);
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
  setPlaying(false);
  if (message) say(message);
}

function finish() {
  if (!playing) return;
  frame = totalFrames;
  showPosition();
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
}

function begin() {
  if (frame >= totalFrames) frame = 0;
  showPosition();
  tone = playRecording(audioUrl(jobId, "overview"), {
    rate: Number(rate.value || 1),
    ratio: totalFrames ? frame / totalFrames : 0,
    onEnded: finish,
    onError: function () {
      halt("The overview recording could not play.");
    }
  });
  setPlaying(true);
  say("Playing the overview recording from " + positionWords() + ".");
  clearInterval(timer);
  timer = setInterval(followRecording, 200);
}

function ns(name) {
  return document.createElementNS("http://www.w3.org/2000/svg", name);
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
  const imageUrl = traces.image_url || frameImageUrl(jobId);
  image.setAttribute("width", String(width));
  image.setAttribute("height", String(height));
  image.setAttribute("href", imageUrl);
  image.setAttributeNS("http://www.w3.org/1999/xlink", "href", imageUrl);
  paths.replaceChildren();
  traces.clusters.forEach(function (cluster) {
    const group = ns("g");
    const selected = show === "all" || show === String(cluster.cluster_id);
    group.setAttribute("opacity", selected ? "1" : "0.12");
    (cluster.paths || []).forEach(function (path) {
      const points = path.map(function (point) { return point[0] + "," + point[1]; }).join(" ");
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
  const words = show === "all" || !chosen
    ? traces.description
    : "Showing chunk " + chosen.cluster_id + ", " + chosen.name + ", only. Other chunks are faded.";
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
  const nFrames = Number(pop.n_frames || 0);
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
  const videoCard = document.getElementById("cluster-video-card");
  const video = document.getElementById("cluster-video");
  if (traces && traces.cluster_video_url && videoCard && video) {
    video.src = "/api/jobs/" + encodeURIComponent(jobId) + "/clusters_overlay.mp4";
    videoCard.hidden = false;
  } else if (videoCard) {
    videoCard.hidden = true;
  }
  showPosition();
}

function buildCards() {
  const wrap = document.getElementById("how-cards");
  CARDS.forEach(function (card) {
    const box = document.createElement("div");
    box.className = "listen-card" + (card.wide ? " wide" : "") + (card.ink ? " ink-card" : "");
    if (!card.ink) box.style.background = card.id === "pitch" ? "var(--sky)" : card.id === "volume" ? "var(--mint)" : "var(--peach)";
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
  setPlaying(false);
  say("All sound stopped.");
});

position.addEventListener("input", function () {
  frame = Number(position.value);
  showPosition();
  paintCues();
  if (tone && totalFrames) tone.seekRatio(frame / totalFrames);
});

volume.addEventListener("input", function () {
  setAppVolume(volume.value);
  volume.setAttribute("aria-valuetext", "Volume " + volume.value + " percent");
  volumeHint.textContent = "Volume " + volume.value + " percent. Separate from your screen reader.";
  if (tone) tone.setVolume();
  paintCues();
});

rate.addEventListener("change", function () {
  if (tone) tone.setRate(Number(rate.value || 1));
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
  stopAllSound(true);
});

setAppVolume(70);
buildCards();

const jobId = currentJobId() || "a1b2c3";
Promise.all([getSummary(jobId), getTraces(jobId)]).then(function (results) {
  summary = results[0];
  traces = results[1];
  fillSummary();
  fillLegend();
  drawTraces();
  paintCues();
}).catch(function (error) {
  say(error.message);
});
