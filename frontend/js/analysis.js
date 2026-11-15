/* Detailed analysis: shortlisted video tabs, muted overview, split chunk panel. */
const statusText = document.getElementById("status-text");
const playButton = document.getElementById("play");
const playLabel = document.getElementById("play-label");
const playIcon = document.getElementById("play-icon");
const overviewVideo = document.getElementById("overview-video");
const chunkVideo = document.getElementById("chunk-video");
const chunkDetail = document.getElementById("chunk-detail");
const chunkPicks = document.getElementById("chunk-picks");
const videoTabs = document.getElementById("video-tabs");
const pos = document.getElementById("pos");
const posVisible = document.getElementById("pos-visible");

let shortlist = [];
let activeJob = "";
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

function briefChunk(cluster, index) {
  const path = (cluster.start_region || "?") + " → " + (cluster.end_region || "?");
  if (index === 0) return "Top trend match · " + path;
  if (cluster.unusual) return "Against trend · " + path;
  return "~" + (cluster.n_cells || 0) + " cells · " + path;
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
    if (chunkVideo && !chunkVideo.paused) chunkVideo.pause();
  }
}

function halt(message) {
  stopAllSound(true);
  setPlaying(false);
  if (message) say(message);
}

function syncChunkVideo(shouldPlay, forceSeek) {
  if (!chunkVideo || !chunkVideo.src) return;
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
    syncChunkVideo(true, "drift");
    if (ratio >= 0.999) {
      halt("Finished this chunk.");
    }
  }, 200);
}

function playSelected() {
  const cluster = selected();
  if (!cluster || !activeJob) return;
  halt();
  setAppVolume(document.getElementById("vol").value);
  const ratio = Number(pos.value) / Number(pos.max);
  tone = playRecording(audioUrl(activeJob, audioName(cluster)), {
    rate: 1,
    ratio: ratio,
    onEnded: function () { halt("Finished this chunk."); },
    onError: function () { halt("This chunk’s sound could not play."); }
  });
  setPlaying(true);
  if (chunkVideo) chunkVideo.playbackRate = 1;
  syncChunkVideo(true, true);
  startCueLoop();
  say("Playing chunk " + cluster.cluster_id + ".");
}

function fillPick(cluster, index) {
  const button = document.getElementById("pick-" + cluster.cluster_id);
  if (!button) return;
  const text = document.getElementById("pick-text-" + cluster.cluster_id);
  text.replaceChildren();
  const name = document.createElement("strong");
  name.textContent = "Chunk " + cluster.cluster_id;
  const meta = document.createElement("span");
  meta.textContent = briefChunk(cluster, index);
  text.appendChild(name);
  text.appendChild(meta);
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
  document.getElementById("detail-h").textContent = "Chunk " + cluster.cluster_id + ": " + (cluster.name || "");
  document.getElementById("detail-meta").textContent =
    "~" + cluster.n_cells + " cells · rank " + cluster.rank + " of " + clusters.length;
  document.getElementById("detail-desc").textContent = cluster.description || "";
  document.getElementById("cmp-caption").textContent = "Chunk " + cluster.cluster_id + " compared with the whole video";
  document.getElementById("cmp-speed").textContent = plain(cluster.mean_speed, 1) + " " + units.speed;
  document.getElementById("all-speed").textContent = plain(pop.mean_speed, 1) + " " + units.speed;
  document.getElementById("cmp-along").textContent = signed(cluster.along_trend != null ? cluster.along_trend : 0, 2) + " " + units.speed;
  document.getElementById("all-along").textContent = signed(pop.trend_speed != null ? pop.trend_speed : 0, 2) + " " + units.speed + (pop.trend_compass ? (" " + pop.trend_compass) : "");
  document.getElementById("cmp-radial").textContent = signed(cluster.mean_radial_v, 1) + " " + units.speed;
  document.getElementById("all-radial").textContent = signed(pop.mean_radial_v, 1) + " " + units.speed;
  document.getElementById("cmp-path").textContent = (cluster.start_region || "?") + " → " + (cluster.end_region || "?");
  playLabel.textContent = "Play this chunk " + cluster.cluster_id;
  clusters.forEach(fillPick);

  chunkVideo.playbackRate = 1;
  chunkVideo.defaultPlaybackRate = 1;
  chunkVideo.src = apiPath("/api/jobs/" + encodeURIComponent(activeJob) + "/chunks/" + encodeURIComponent(cluster.cluster_id) + "/video.mp4?v=" + Date.now());
  chunkVideo.load();
  updatePosLabel(0);
}

function select(index) {
  const next = Math.max(0, Math.min(clusters.length - 1, index));
  if (playing) halt();
  current = next;
  showDetail();
  const cluster = selected();
  say("Chunk " + cluster.cluster_id + " selected.");
}

function buildList() {
  chunkPicks.replaceChildren();
  clusters.forEach(function (cluster, index) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pick";
    button.id = "pick-" + cluster.cluster_id;
    button.addEventListener("click", function () { select(index); });
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.setAttribute("aria-hidden", "true");
    dot.style.background = cluster.color;
    dot.textContent = String(cluster.cluster_id);
    const text = document.createElement("span");
    text.className = "pick-copy";
    text.id = "pick-text-" + cluster.cluster_id;
    button.appendChild(dot);
    button.appendChild(text);
    item.appendChild(button);
    chunkPicks.appendChild(item);
    fillPick(cluster, index);
  });
  document.getElementById("important-hint").textContent =
    clusters.length
      ? "Ranked by match to the main trend. Pick a chunk on the left."
      : "No chunks were found for this video.";
}

function fillOverview() {
  const pop = summary.population || {};
  const overview = summary.overview || {};
  const units = summary.units || { speed: "µm/min", length: "µm", time: "min" };
  const active = shortlist.find(function (video) { return video.job_id === activeJob; });
  document.getElementById("hero-lede").textContent =
    (active ? active.name + " · " : "") + (pop.n_cells || 0) + " cells in " + (pop.n_clusters || 0) + " chunks.";
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
  overviewVideo.src = apiPath("/api/jobs/" + encodeURIComponent(activeJob) + "/clusters_overlay.mp4?v=" + Date.now());
  overviewVideo.load();
  document.getElementById("export-link").href = hrefWithJob("export.html");
  const overviewLink = document.getElementById("overview-link");
  if (overviewLink) overviewLink.href = hrefWithJob("overview.html");
}

function paintTabs() {
  videoTabs.replaceChildren();
  if (shortlist.length <= 1) {
    videoTabs.hidden = shortlist.length === 0;
  } else {
    videoTabs.hidden = false;
  }
  shortlist.forEach(function (video, index) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "video-tab";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", video.job_id === activeJob ? "true" : "false");
    button.id = "video-tab-" + video.job_id;
    button.textContent = video.name || ("Video " + (index + 1));
    button.addEventListener("click", function () {
      if (video.job_id === activeJob) return;
      loadJob(video.job_id);
    });
    videoTabs.appendChild(button);
  });
}

function loadJob(jobId) {
  halt();
  activeJob = jobId;
  setJobId(jobId);
  current = -1;
  chunkDetail.hidden = true;
  chunkPicks.replaceChildren();
  paintTabs();
  say("Loading detailed analysis for this video.");
  return getSummary(jobId).then(function (data) {
    summary = data;
    clusters = (summary.clusters || []).slice().sort(byRank);
    fillOverview();
    buildList();
    if (clusters.length) {
      select(0);
      say(clusters.length + " chunks ready. Pick one on the left or press Play.");
    } else {
      say("No chunks were found for this video.");
    }
  }).catch(function (error) {
    document.getElementById("hero-lede").textContent = "This video could not load.";
    document.getElementById("desc-text").textContent = error.message || "Run Point of Audio analysis first.";
    say(error.message || "Analysis is not ready yet.");
  });
}

function resolveShortlist() {
  const ids = readShortlist();
  if (ids.length) {
    return Promise.all(ids.map(function (id, index) {
      return Promise.resolve({ job_id: id, name: "Video " + (index + 1) }).then(function (video) {
        return getBatchNames().then(function (names) {
          if (names[id]) video.name = names[id];
          return video;
        });
      });
    }));
  }
  const job = currentJobId();
  if (!job) return Promise.resolve([]);
  return getBatchNames().then(function (names) {
    return [{ job_id: job, name: names[job] || "This video" }];
  });
}

function getBatchNames() {
  let stored = "";
  try {
    stored = sessionStorage.getItem("cellsound-batch") || "";
  } catch (error) {
    stored = "";
  }
  if (!stored) return Promise.resolve({});
  return getBatch(stored).then(function (data) {
    const map = {};
    (data.videos || []).forEach(function (video) {
      if (video.job_id) map[video.job_id] = video.name || video.job_id;
    });
    return map;
  }).catch(function () {
    return {};
  });
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

document.addEventListener("cellsound-stopped", function () {
  setPlaying(false);
  say("Sound stopped.");
});

document.getElementById("vol").addEventListener("input", function () {
  const value = document.getElementById("vol").value;
  document.getElementById("vol").setAttribute("aria-valuetext", "Volume " + value + " percent");
  document.getElementById("vol-hint").textContent = "Volume " + value + " percent.";
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

document.querySelectorAll("[data-pause-mirror]").forEach(function (button) {
  button.addEventListener("click", function () {
    setPauseAnimations(!(readDisplay().pause));
  });
});

resolveShortlist().then(function (list) {
  shortlist = list;
  if (!shortlist.length) {
    document.getElementById("hero-lede").textContent = "No videos were shortlisted.";
    document.getElementById("desc-text").textContent = "Go back to Overview, select video thumbnails, then continue here.";
    say("Select videos on the Overview page first.");
    return;
  }
  saveShortlist(shortlist.map(function (video) { return video.job_id; }));
  return loadJob(shortlist[0].job_id);
}).catch(function (error) {
  say(error.message || "Detailed analysis could not load.");
});
