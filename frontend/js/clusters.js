/* Clusters: pick a group, hear it alone, and compare it with the population. */
const picks = document.getElementById("cluster-picks");
const statusText = document.getElementById("status");
const keysOn = document.getElementById("keys-on");
const instrumentSelect = document.getElementById("inst");
const playButton = document.getElementById("play");
const playLabel = document.getElementById("play-label");
const playIcon = document.getElementById("play-icon");

let summary = null;
let clusters = [];
let current = 0;
let playing = false;
let tone = null;
const chosenInstrument = {};

function say(text) {
  if (statusText.textContent !== text) statusText.textContent = text;
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

function instrumentOf(cluster) {
  return chosenInstrument[cluster.cluster_id] || String(cluster.instrument || "piano").toLowerCase();
}

function instrumentName(value) {
  const option = instrumentSelect.querySelector("option[value=\"" + value + "\"]");
  return option ? option.textContent : value;
}

function selected() {
  return clusters[current];
}

function setPlaying(on) {
  playing = on;
  if (!on) tone = null;
  document.getElementById("detail").classList.toggle("playing", on);
  const cluster = selected();
  playLabel.textContent = (on ? "Pause cluster " : "Play cluster ") + (cluster ? cluster.cluster_id : "");
  playIcon.setAttribute("d", on ? "M6 4h4v16H6zM14 4h4v16h-4z" : "M7 4v16l13-8z");
}

function stopPlayback() {
  stopAllSound(true);
  setPlaying(false);
}

function fillPick(cluster) {
  const text = document.getElementById("pick-text-" + cluster.cluster_id);
  text.replaceChildren();
  const name = document.createElement("strong");
  const selectedNow = cluster === selected();
  name.textContent = "Cluster " + cluster.cluster_id + ": " + cluster.name;
  const meta = document.createElement("span");
  meta.textContent = cluster.n_cells + " cells · " + instrumentName(instrumentOf(cluster)) + (selectedNow ? " · selected" : "");
  text.appendChild(name);
  text.appendChild(meta);
  if (cluster.unusual) {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = "Unusual: moves outward";
    text.appendChild(tag);
  }
  const button = document.getElementById("pick-" + cluster.cluster_id);
  if (selectedNow) button.setAttribute("aria-current", "true");
  else button.removeAttribute("aria-current");
}

function showDetail() {
  const cluster = selected();
  const pop = summary.population;
  const units = summary.units || { speed: "µm/min", angular: "rad/min" };
  const dot = document.getElementById("detail-dot");
  dot.textContent = String(cluster.cluster_id);
  dot.style.background = cluster.color;
  document.getElementById("detail-h").textContent = "Cluster " + cluster.cluster_id + ": " + cluster.name;
  document.getElementById("detail-meta").textContent = cluster.n_cells + " cells · rank " + cluster.rank + " of " + clusters.length;
  document.getElementById("detail-desc").textContent = cluster.description || "";
  document.getElementById("cmp-caption").textContent = "Cluster " + cluster.cluster_id + " compared with all cells";
  document.getElementById("cmp-speed").textContent = plain(cluster.mean_speed, 1) + " " + units.speed;
  document.getElementById("all-speed").textContent = plain(pop.mean_speed, 1) + " " + units.speed;
  document.getElementById("cmp-radial").textContent = signed(cluster.mean_radial_v, 1) + " " + units.speed;
  document.getElementById("all-radial").textContent = signed(pop.mean_radial_v, 1) + " " + units.speed;
  document.getElementById("cmp-angular").textContent = signed(cluster.mean_angular_v, 2) + " " + units.angular;
  document.getElementById("all-angular").textContent = signed(pop.mean_angular_v, 2) + " " + units.angular;
  instrumentSelect.value = instrumentOf(cluster);
  document.getElementById("prev").disabled = current === 0;
  document.getElementById("next").disabled = current === clusters.length - 1;
  const explore = document.getElementById("explore");
  explore.href = hrefWithJob("export.html");
  explore.textContent = "Go to export";
  playLabel.textContent = (playing ? "Pause cluster " : "Play cluster ") + cluster.cluster_id;
  clusters.forEach(fillPick);
}

function select(index, focusId) {
  const next = Math.max(0, Math.min(clusters.length - 1, index));
  if (playing) stopPlayback();
  current = next;
  showDetail();
  const cluster = selected();
  say("Cluster " + cluster.cluster_id + ", " + cluster.name + ", selected. " + cluster.n_cells + " cells, played on " + instrumentName(instrumentOf(cluster)).toLowerCase() + ".");
  if (focusId) {
    const target = document.getElementById(focusId);
    if (target) target.focus();
  }
}

function move(step) {
  const next = current + step;
  if (next < 0) {
    say("This is the first cluster.");
    return;
  }
  if (next >= clusters.length) {
    say("This is the last cluster.");
    return;
  }
  select(next, "detail-h");
}

function buildList() {
  picks.replaceChildren();
  clusters.forEach(function (cluster, index) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pick";
    button.id = "pick-" + cluster.cluster_id;
    button.addEventListener("click", function () {
      select(index, button.id);
    });
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
    picks.appendChild(item);
  });
}

playButton.addEventListener("click", function () {
  const cluster = selected();
  if (!cluster) return;
  if (playing) {
    stopPlayback();
    say("Paused.");
    return;
  }
  tone = playRecording(audioUrl(currentJobId(), cluster.audio), {
    onEnded: function () {
      stopPlayback();
      say("Cluster " + cluster.cluster_id + " finished.");
    },
    onError: function () {
      stopPlayback();
      say("That cluster recording could not play.");
    }
  });
  setPlaying(true);
  say("Playing the recording for cluster " + cluster.cluster_id + ". Other clusters are muted.");
});

document.getElementById("stop").addEventListener("click", function () {
  if (!playing) {
    say("All sound stopped.");
    return;
  }
  stopAllSound();
});

document.getElementById("prev").addEventListener("click", function () { move(-1); });
document.getElementById("next").addEventListener("click", function () { move(1); });

instrumentSelect.addEventListener("change", function () {
  const cluster = selected();
  if (!cluster) return;
  chosenInstrument[cluster.cluster_id] = instrumentSelect.value;
  fillPick(cluster);
  say("The saved recording still uses " + instrumentName(cluster.instrument || "piano").toLowerCase() + ". Run the analysis again to record this cluster on " + instrumentName(instrumentSelect.value).toLowerCase() + ".");
});

keysOn.addEventListener("change", function () {
  say(keysOn.checked ? "N and P shortcuts turned on." : "N and P shortcuts turned off.");
});

document.addEventListener("keydown", function (event) {
  if (!keysOn.checked) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const tag = event.target && event.target.tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
  if (event.key === "n" || event.key === "N") {
    event.preventDefault();
    move(1);
  }
  if (event.key === "p" || event.key === "P") {
    event.preventDefault();
    move(-1);
  }
});

document.addEventListener("cellsound-stopped", function () {
  if (!playing) return;
  setPlaying(false);
  say("All sound stopped.");
});

const pauseButton = document.querySelector("[data-pause-mirror]");
if (pauseButton) {
  pauseButton.addEventListener("click", function () {
    setPauseAnimations(pauseButton.getAttribute("aria-pressed") !== "true");
  });
}

window.addEventListener("pagehide", function () {
  stopAllSound(true);
});

getSummary(currentJobId() || "a1b2c3").then(function (data) {
  summary = data;
  clusters = (data.clusters || []).slice().sort(function (a, b) { return a.rank - b.rank; });
  document.getElementById("page-title").textContent = "Explore the " + clusters.length + " clusters";
  if (!clusters.length) {
    say("No cells were tracked, so there are no clusters to explore.");
    return;
  }
  buildList();
  showDetail();
  const first = selected();
  say("Cluster " + first.cluster_id + " selected. Press Play to hear it alone.");
}).catch(function (error) {
  say(error.message);
});
