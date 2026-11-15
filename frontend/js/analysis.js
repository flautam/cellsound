/* Analysis: muted overview of all chunks, then listen to one highlighted chunk. */
const statusText = document.getElementById("status-text");
const playButton = document.getElementById("play");
const playLabel = document.getElementById("play-label");
const playIcon = document.getElementById("play-icon");
const overviewVideo = document.getElementById("overview-video");
const chunkVideo = document.getElementById("chunk-video");
const chunkDetail = document.getElementById("chunk-detail");
const chunkPicks = document.getElementById("chunk-picks");
const pos = document.getElementById("pos");
const posVisible = document.getElementById("pos-visible");
const beatDots = Array.from(document.querySelectorAll("#beat-dots i"));
const pitchFill = document.getElementById("pitch-fill");

let summary = null;
let clusters = [];
let current = -1;
let playing = false;
let tone = null;
let scrubbing = false;
let cueTimer = null;

function say(text) {
  if (statusText && statusText.textContent !== text) statusText.textContent = text;
}

function plain(value, places) {
  return Number(value).toFixed(places).replace(/0+$/, "").replace(/\.$/, "");
}

function signed(value, places) {
  const number = Number(value);
  const text = plain(Math.abs(number), places);
  if (number > 0) return "+" + text;
  if (number < 0) return "−" + text;
  return "0";
}

function toneLabel() {
  return "one tone for this video";
}

function selected() {
  return current >= 0 ? clusters[current] : null;
}

function byRank(a, b) {
  return (a.rank || 999) - (b.rank || 999);
}

function audioName(cluster) {
  const name = cluster.audio || ("cluster_" + cluster.cluster_id + ".wav");
  return String(name).replace(/\.wav$/i, "");
}

function setPlaying(on) {
  playing = on;
  if (!on) tone = null;
  chunkDetail.classList.toggle("playing", on);
  const cluster = selected();
  const id = cluster ? cluster.cluster_id : "";
  playLabel.textContent = (on ? "Pause chunk " : "Play this chunk") + (id ? " " + id : "");
  playIcon.setAttribute("d", on ? "M6 4h4v16H6zM14 4h4v16h-4z" : "M7 4v16l13-8z");
  if (!on) {
    clearInterval(cueTimer);
    cueTimer = null;
    paintCues(false);
    if (chunkVideo && !chunkVideo.paused) chunkVideo.pause();
  }
}

function halt(message) {
  stopAllSound(true);
  setPlaying(false);
  if (message) say(message);
}

function paintCues(on) {
  document.getElementById("wave").setAttribute("aria-hidden", on ? "false" : "true");
  if (!on) {
    pitchFill.style.width = "0%";
    beatDots.forEach(function (dot) { dot.className = ""; });
    return;
  }
  const cluster = selected();
  const radial = cluster && cluster.mean_radial_v != null ? Number(cluster.mean_radial_v) : 0;
  const toward = Math.max(0, Math.min(1, 0.5 - radial / 8));
  pitchFill.style.width = Math.round(toward * 100) + "%";
  const speed = cluster && cluster.mean_speed != null ? Number(cluster.mean_speed) : 0;
  const level = Math.max(0, Math.min(beatDots.length - 1, Math.round(speed)));
  beatDots.forEach(function (dot, index) {
    dot.className = index <= level ? "on" : "";
  });
}

function syncChunkVideo(shouldPlay, forceSeek) {
  if (!chunkVideo || !chunkVideo.src) return;
  // Seeking every cue tick made playback stutter. Only jump when starting,
  // scrubbing, or when audio and video drift apart.
  if (forceSeek && tone && tone.ready && tone.ready() && chunkVideo.duration) {
    const want = tone.currentRatio() * chunkVideo.duration;
    if (forceSeek === true || Math.abs((chunkVideo.currentTime || 0) - want) > 0.3) {
      try { chunkVideo.currentTime = want; } catch (error) { /* seek */ }
    }
  }
  if (shouldPlay) {
    if (chunkVideo.paused) {
      const play = chunkVideo.play();
      if (play && play.catch) play.catch(function () { /* autoplay blocked */ });
    }
  } else if (!chunkVideo.paused) {
    chunkVideo.pause();
  }
}

function updatePosLabel(ratio) {
  const place = Math.max(0, Math.min(1, ratio || 0));
  pos.value = String(Math.round(place * Number(pos.max)));
  const words = place <= 0.01 ? "Start of the clip" : (place >= 0.99 ? "End of the clip" : Math.round(place * 100) + " percent through the clip");
  pos.setAttribute("aria-valuetext", words);
  posVisible.textContent = words;
}

function startCueLoop() {
  clearInterval(cueTimer);
  cueTimer = setInterval(function () {
    if (!playing || !tone || !tone.ready()) return;
    const ratio = tone.currentRatio();
    if (!scrubbing) updatePosLabel(ratio);
    // Keep video running; only correct large drift (do not seek every tick).
    syncChunkVideo(true, "drift");
    if (ratio >= 0.999) {
      halt("Finished this chunk.");
    }
  }, 200);
}

function playSelected() {
  const cluster = selected();
  const job = currentJobId();
  if (!cluster || !job) return;
  halt();
  setAppVolume(document.getElementById("vol").value);
  const ratio = Number(pos.value) / Number(pos.max);
  // Rate is always 1× — half-speed is baked into the chunk MP4 + WAV length.
  tone = playRecording(audioUrl(job, audioName(cluster)), {
    rate: 1,
    ratio: ratio,
    onEnded: function () { halt("Finished this chunk."); },
    onError: function () { halt("This chunk’s sound could not play."); }
  });
  setPlaying(true);
  paintCues(true);
  if (chunkVideo) chunkVideo.playbackRate = 1;
  syncChunkVideo(true, true);
  startCueLoop();
  say("Playing chunk " + cluster.cluster_id + ", " + toneLabel(cluster) + ".");
}

function importanceNote(cluster, index) {
  if (index === 0) return "Strongest match to the main trend — start here.";
  if (cluster.unusual) return "Moves against the main trend — worth a look.";
  if ((cluster.rank || 99) <= 3) return "Strong trend support — high priority.";
  return "Lower priority relative to the main trend.";
}

function fillPick(cluster, index) {
  const button = document.getElementById("pick-" + cluster.cluster_id);
  if (!button) return;
  const text = document.getElementById("pick-text-" + cluster.cluster_id);
  text.replaceChildren();
  const name = document.createElement("strong");
  name.textContent = "Chunk " + cluster.cluster_id + ": " + cluster.name;
  const meta = document.createElement("span");
  meta.textContent = "~" + cluster.n_cells + " cells · " + toneLabel() + " · rank " + cluster.rank;
  const why = document.createElement("span");
  why.textContent = importanceNote(cluster, index);
  text.appendChild(name);
  text.appendChild(meta);
  text.appendChild(why);
  if (cluster.unusual) {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = "Unusual";
    text.appendChild(tag);
  }
  if (cluster === selected()) button.setAttribute("aria-current", "true");
  else button.removeAttribute("aria-current");
}

function showDetail() {
  const cluster = selected();
  if (!cluster || !summary) return;
  chunkDetail.hidden = false;
  const pop = summary.population || {};
  const units = summary.units || { speed: "µm/min" };
  const dot = document.getElementById("detail-dot");
  dot.textContent = String(cluster.cluster_id);
  dot.style.background = cluster.color;
  document.getElementById("detail-h").textContent = "Chunk " + cluster.cluster_id + ": " + cluster.name;
  document.getElementById("detail-meta").textContent =
    "~" + cluster.n_cells + " cells · rank " + cluster.rank + " of " + clusters.length + " · " + importanceNote(cluster, clusters.indexOf(cluster));
  document.getElementById("detail-desc").textContent = cluster.description || "";
  document.getElementById("cmp-caption").textContent = "Chunk " + cluster.cluster_id + " compared with the whole video";
  document.getElementById("cmp-speed").textContent = plain(cluster.mean_speed, 1) + " " + units.speed;
  document.getElementById("all-speed").textContent = plain(pop.mean_speed, 1) + " " + units.speed;
  document.getElementById("cmp-along").textContent = signed(cluster.along_trend != null ? cluster.along_trend : 0, 2) + " " + units.speed;
  document.getElementById("all-along").textContent = signed(pop.trend_speed != null ? pop.trend_speed : 0, 2) + " " + units.speed + (pop.trend_compass ? (" " + pop.trend_compass) : "");
  document.getElementById("cmp-radial").textContent = signed(cluster.mean_radial_v, 1) + " " + units.speed;
  document.getElementById("all-radial").textContent = signed(pop.mean_radial_v, 1) + " " + units.speed;
  document.getElementById("cmp-path").textContent = (cluster.start_region || "?") + " → " + (cluster.end_region || "?");
  document.getElementById("tone-note").textContent =
    "Each video uses one tone, not a different tone for each cluster. The pulse is the beat. Pitch rises when something moves toward the Point of Audio, and falls when it moves away. Volume tells how far away something is. Left and right computer speakers tell direction. Surround-sound headphones give a two-dimensional sense of where the motion is.";
  playLabel.textContent = "Play this chunk " + cluster.cluster_id;
  clusters.forEach(fillPick);

  const job = currentJobId();
  chunkVideo.playbackRate = 0.5;
  chunkVideo.defaultPlaybackRate = 0.5;
  chunkVideo.src = apiPath("/api/jobs/" + encodeURIComponent(job) + "/chunks/" + encodeURIComponent(cluster.cluster_id) + "/video.mp4?v=" + Date.now());
  chunkVideo.load();
  updatePosLabel(0);
}

function select(index, focusDetail) {
  const next = Math.max(0, Math.min(clusters.length - 1, index));
  if (playing) halt();
  current = next;
  showDetail();
  const cluster = selected();
  say("Chunk " + cluster.cluster_id + " selected. " + importanceNote(cluster, next));
  if (focusDetail) document.getElementById("detail-h").focus();
  chunkDetail.scrollIntoView({ behavior: "smooth", block: "start" });
}

function buildList() {
  chunkPicks.replaceChildren();
  clusters.forEach(function (cluster, index) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pick";
    button.id = "pick-" + cluster.cluster_id;
    button.addEventListener("click", function () { select(index, true); });
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.setAttribute("aria-hidden", "true");
    dot.style.background = cluster.color;
    dot.textContent = String(cluster.cluster_id);
    const text = document.createElement("span");
    text.id = "pick-text-" + cluster.cluster_id;
    button.appendChild(dot);
    button.appendChild(text);
    item.appendChild(button);
    chunkPicks.appendChild(item);
    fillPick(cluster, index);
  });
  document.getElementById("important-hint").textContent =
    clusters.length
      ? "Ranked by match to the main trend. Open a chunk to hear it with only that group highlighted."
      : "No chunks were found for this video.";
}

function fillOverview() {
  const pop = summary.population || {};
  const overview = summary.overview || {};
  const units = summary.units || { speed: "µm/min", length: "µm", time: "min" };
  document.getElementById("hero-lede").textContent =
    (pop.n_cells || 0) + " cells in " + (pop.n_clusters || 0) + " chunks. Read the overview, then open the chunks that matter most.";
  document.getElementById("desc-text").textContent =
    overview.description || pop.description || "No overview is ready yet.";
  const body = document.getElementById("stats-body");
  body.replaceChildren();
  const rows = [
    ["Cells tracked", String(pop.n_cells || 0), "Nuclei followed across the clip"],
    ["Chunks", String(pop.n_clusters || 0), "Spatial groups to inspect"],
    ["Mean speed", plain(pop.mean_speed || 0, 2) + " " + units.speed, "Typical motion across the field"],
    ["Main trend", (pop.trend_compass || "—") + " · " + plain(pop.trend_speed || 0, 2) + " " + units.speed, "Direction the population drifts"],
    ["Toward or away", signed(pop.mean_radial_v || 0, 2) + " " + units.speed, "Negative means toward the Point of Audio"]
  ];
  rows.forEach(function (row) {
    const tr = document.createElement("tr");
    row.forEach(function (cell, index) {
      const el = document.createElement(index === 0 ? "th" : "td");
      if (index === 0) el.scope = "row";
      el.textContent = cell;
      tr.appendChild(el);
    });
    body.appendChild(tr);
  });
  const trust = summary.reliability || {};
  document.getElementById("trust-note").textContent = trust.note || "Tracking finished.";
  const job = currentJobId();
  overviewVideo.src = apiPath("/api/jobs/" + encodeURIComponent(job) + "/clusters_overlay.mp4?v=" + Date.now());
  overviewVideo.load();
  document.getElementById("export-link").href = hrefWithJob("export.html");
  const overviewLink = document.getElementById("overview-link");
  if (overviewLink) overviewLink.href = hrefWithJob("overview.html");
}

playButton.addEventListener("click", function () {
  if (!selected()) {
    say("Choose a chunk from the list first.");
    return;
  }
  if (playing) {
    halt("Paused.");
    return;
  }
  playSelected();
});

document.getElementById("replay").addEventListener("click", function () {
  if (!selected()) {
    say("Choose a chunk from the list first.");
    return;
  }
  updatePosLabel(0);
  playSelected();
});

document.getElementById("stop").addEventListener("click", function () {
  halt("All sound stopped.");
});

document.addEventListener("cellsound-stopped", function () {
  setPlaying(false);
  say("All sound stopped.");
});

document.getElementById("vol").addEventListener("input", function () {
  const value = document.getElementById("vol").value;
  document.getElementById("vol").setAttribute("aria-valuetext", "Volume " + value + " percent");
  document.getElementById("vol-hint").textContent = "Volume " + value + " percent. Separate from your screen reader.";
  setAppVolume(value);
  if (tone && tone.setVolume) tone.setVolume();
});

pos.addEventListener("pointerdown", function () { scrubbing = true; });
pos.addEventListener("pointerup", function () { scrubbing = false; });
pos.addEventListener("input", function () {
  const ratio = Number(pos.value) / Number(pos.max);
  updatePosLabel(ratio);
  if (tone && tone.seekRatio) tone.seekRatio(ratio);
  if (chunkVideo && chunkVideo.duration) {
    try { chunkVideo.currentTime = ratio * chunkVideo.duration; } catch (error) { /* seek */ }
  }
});

document.getElementById("how-btn").addEventListener("click", function () {
  const open = document.getElementById("how-btn").getAttribute("aria-expanded") !== "true";
  document.getElementById("how-btn").setAttribute("aria-expanded", open ? "true" : "false");
  document.getElementById("how-panel").hidden = !open;
  document.getElementById("how-state").textContent = open ? "Hide" : "Show";
});

document.querySelectorAll("[data-pause-mirror]").forEach(function (button) {
  button.addEventListener("click", function () {
    setPauseAnimations(!(readDisplay().pause));
  });
});

Promise.all([getSummary(currentJobId())]).then(function (parts) {
  summary = parts[0];
  clusters = (summary.clusters || []).slice().sort(byRank);
  fillOverview();
  buildList();
  if (clusters.length) {
    say("Overview ready. " + clusters.length + " chunks ranked. Open one to listen.");
  } else {
    say("Overview ready, but no chunks were found.");
  }
}).catch(function (error) {
  document.getElementById("hero-lede").textContent = "The analysis could not load.";
  document.getElementById("desc-text").textContent = error.message || "Run Point of Audio and analyze first.";
  say(error.message || "Analysis is not ready yet.");
});
