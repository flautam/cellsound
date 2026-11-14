/* Point of Audio: move a point with buttons or numbers, then run analysis. */
const FILLS = ["var(--sun)", "var(--butter)", "var(--on-header)", "var(--primary)"];
const INSTRUMENTS = ["piano", "marimba", "cello", "flute", "bell"];

const statusText = document.getElementById("status-text");
const previewTitle = document.getElementById("preview-title");
const cellDots = document.getElementById("cell-dots");
const crosshair = document.getElementById("crosshair");
const legend = document.getElementById("frame-legend");
const stepSelect = document.getElementById("step");
const xInput = document.getElementById("px-x");
const yInput = document.getElementById("px-y");
const clusterSlider = document.getElementById("clusters");
const runError = document.getElementById("run-error");

let frame = { width: 512, height: 512, cells: [], imageUrl: "" };
let poa = { x: 256, y: 256 };
let announceTimer = null;

function say(text) {
  if (statusText.textContent !== text) statusText.textContent = text;
}

function stepSize() {
  return Number(stepSelect.value) || 32;
}

function countNear(x, y) {
  return frame.cells.filter(function (cell) {
    const dx = cell.x - x;
    const dy = cell.y - y;
    return Math.sqrt(dx * dx + dy * dy) <= 50;
  }).length;
}

function placeWords(x, y) {
  const hx = x < frame.width / 3 ? "left" : (x > frame.width * 2 / 3 ? "right" : "center");
  const hy = y < frame.height / 3 ? "top" : (y > frame.height * 2 / 3 ? "bottom" : "middle");
  return hy + " " + hx;
}

function clamp(value, max) {
  return Math.max(0, Math.min(max, Math.round(value)));
}

function markScale() {
  return Math.min(frame.width, frame.height) / 512;
}

function fitPreview() {
  const svg = document.getElementById("preview");
  svg.setAttribute("viewBox", "0 0 " + frame.width + " " + frame.height);
  svg.style.aspectRatio = frame.width + " / " + frame.height;
  const backdrop = document.getElementById("frame-backdrop");
  backdrop.setAttribute("width", String(frame.width));
  backdrop.setAttribute("height", String(frame.height));
  const image = document.getElementById("frame-image");
  image.setAttribute("width", String(frame.width));
  image.setAttribute("height", String(frame.height));
  if (frame.imageUrl) {
    image.setAttribute("href", frame.imageUrl);
    image.setAttributeNS("http://www.w3.org/1999/xlink", "href", frame.imageUrl);
  }
}

function draw() {
  fitPreview();
  const scale = markScale();
  cellDots.replaceChildren();
  frame.cells.forEach(function (cell, index) {
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", String(cell.x));
    dot.setAttribute("cy", String(cell.y));
    dot.setAttribute("r", String(8 * scale));
    dot.setAttribute("fill", FILLS[index % FILLS.length]);
    dot.setAttribute("stroke", "var(--ink)");
    dot.setAttribute("stroke-width", String(2 * scale));
    cellDots.appendChild(dot);
  });
  crosshair.setAttribute("transform", "translate(" + poa.x + " " + poa.y + ") scale(" + scale + ")");
  const where = placeWords(poa.x, poa.y);
  previewTitle.textContent = "First video frame with " + frame.cells.length + " cell nuclei. The Point of Audio is at the " + where + ", x " + poa.x + ", y " + poa.y + ".";
  document.getElementById("preview").setAttribute("aria-label", previewTitle.textContent);
  legend.textContent = "First frame, " + frame.width + " × " + frame.height + " pixels";
}

function positionSentence(word) {
  const near = countNear(poa.x, poa.y);
  const center = poa.x === Math.round(frame.width / 2) && poa.y === Math.round(frame.height / 2);
  let line = center
    ? "Point of Audio set to the image center, x " + poa.x + ", y " + poa.y + "."
    : (word ? "Moved " + word + " to" : "Point of Audio at") + " x " + poa.x + ", y " + poa.y + ".";
  line += " " + near + " cells within 50 pixels.";
  if (poa.x === 0 || poa.y === 0 || poa.x === frame.width || poa.y === frame.height) {
    line += " At the edge of the image.";
  }
  return line;
}

function moveTo(x, y, word, quiet) {
  poa.x = clamp(x, frame.width);
  poa.y = clamp(y, frame.height);
  xInput.value = String(poa.x);
  yInput.value = String(poa.y);
  draw();
  if (!quiet) say(positionSentence(word));
}

function scheduleMove() {
  const x = Number(xInput.value);
  const y = Number(yInput.value);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return;
  poa.x = clamp(x, frame.width);
  poa.y = clamp(y, frame.height);
  draw();
  clearTimeout(announceTimer);
  announceTimer = setTimeout(function () {
    say(positionSentence(""));
  }, 250);
}

function refreshSliders() {
  clusterSlider.setAttribute("aria-valuetext", clusterSlider.value + " clusters");
  document.getElementById("cluster-num").textContent = clusterSlider.value;
}

function analysisBody() {
  const count = Number(clusterSlider.value);
  const instruments = {};
  for (let index = 1; index <= count; index += 1) {
    instruments[String(index)] = INSTRUMENTS[(index - 1) % INSTRUMENTS.length];
  }
  return {
    poa: { x: poa.x, y: poa.y },
    n_clusters: count,
    instruments: instruments
  };
}

function showCalibration() {
  const slot = document.querySelector("#calib-kept span");
  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem("cellsound-calibration") || "null");
  } catch (error) {
    saved = null;
  }
  if (saved && saved.pixel_size_um && saved.minutes_per_frame) {
    slot.textContent = "Calibration from upload kept: " + saved.pixel_size_um + " µm per pixel, " + saved.minutes_per_frame + " minutes per frame.";
  } else {
    slot.textContent = "No calibration was saved. Results will use pixels per frame.";
  }
}

document.getElementById("up").addEventListener("click", function () {
  moveTo(poa.x, poa.y - stepSize(), "up");
});
document.getElementById("down").addEventListener("click", function () {
  moveTo(poa.x, poa.y + stepSize(), "down");
});
document.getElementById("left").addEventListener("click", function () {
  moveTo(poa.x - stepSize(), poa.y, "left");
});
document.getElementById("right").addEventListener("click", function () {
  moveTo(poa.x + stepSize(), poa.y, "right");
});
document.getElementById("center").addEventListener("click", function () {
  moveTo(frame.width / 2, frame.height / 2, "");
});

stepSelect.addEventListener("change", function () {
  const size = String(stepSize());
  xInput.step = size;
  yInput.step = size;
});

xInput.addEventListener("input", scheduleMove);
yInput.addEventListener("input", scheduleMove);
[clusterSlider].forEach(function (slider) {
  slider.addEventListener("input", refreshSliders);
});

document.getElementById("reset").addEventListener("click", function () {
  clusterSlider.value = "8";
  stepSelect.value = "32";
  xInput.step = "32";
  yInput.step = "32";
  refreshSliders();
  moveTo(frame.width / 2, frame.height / 2, "", true);
  say("Settings reset to defaults. " + positionSentence(""));
});

document.getElementById("run").addEventListener("click", function () {
  const job = currentJobId() || "a1b2c3";
  const body = analysisBody();
  runError.hidden = true;
  document.getElementById("run").disabled = true;
  say("Running the analysis.");
  try {
    sessionStorage.setItem("cellsound-analysis", JSON.stringify(body));
  } catch (error) {
    /* The next page can still load the mock summary. */
  }
  analyzeJob(job, body).then(function () {
    setJobId(job);
    return getJob(job);
  }).then(function poll(data) {
    if (data.status === "error") {
      document.getElementById("run").disabled = false;
      runError.hidden = false;
      runError.textContent = data.message || "The analysis could not finish.";
      say(runError.textContent);
      return null;
    }
    if (data.status !== "done") {
      return new Promise(function (resolve) {
        setTimeout(function () {
          resolve(getJob(job).then(poll));
        }, 500);
      });
    }
    window.location.href = hrefWithJob("overview.html");
    return null;
  }).catch(function (error) {
    document.getElementById("run").disabled = false;
    runError.hidden = false;
    runError.textContent = error.message;
    say(error.message);
  });
});

const pauseButton = document.querySelector("[data-pause-mirror]");
if (pauseButton) {
  pauseButton.addEventListener("click", function () {
    setPauseAnimations(pauseButton.getAttribute("aria-pressed") !== "true");
  });
}

showCalibration();
const jobId = currentJobId() || "a1b2c3";
getFrame0(jobId).then(function (data) {
  frame = {
    width: data.width || 512,
    height: data.height || 512,
    cells: data.cells || [],
    imageUrl: data.image_url || frameImageUrl(jobId)
  };
  xInput.max = String(frame.width);
  yInput.max = String(frame.height);
  xInput.previousElementSibling.textContent = "X (0–" + frame.width + ")";
  yInput.previousElementSibling.textContent = "Y (0–" + frame.height + ")";
  moveTo(frame.width / 2, frame.height / 2, "");
}).catch(function (error) {
  say(error.message);
});
refreshSliders();
