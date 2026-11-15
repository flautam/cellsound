/* Point of Audio: move a point with buttons or numbers, then run analysis. */
const FILLS = ["var(--sun)", "var(--butter)", "var(--on-header)", "var(--primary)"];

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

function paintMark() {
  const scale = markScale();
  crosshair.setAttribute("transform", "translate(" + poa.x + " " + poa.y + ") scale(" + scale + ")");
  const where = placeWords(poa.x, poa.y);
  previewTitle.textContent = "First video frame with " + frame.cells.length + " cell nuclei. The Point of Audio is at the " + where + ", x " + poa.x + ", y " + poa.y + ". Tap or drag to move it.";
  document.getElementById("preview").setAttribute("aria-label", previewTitle.textContent);
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
  paintMark();
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
  paintMark();
  if (!quiet) say(positionSentence(word));
}

function scheduleMove() {
  const x = Number(xInput.value);
  const y = Number(yInput.value);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return;
  poa.x = clamp(x, frame.width);
  poa.y = clamp(y, frame.height);
  paintMark();
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
  return {
    poa: { x: poa.x, y: poa.y },
    n_clusters: Number(clusterSlider.value)
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

function waitUntilDone(job) {
  return getJob(job).then(function poll(data) {
    if (data.status === "error") {
      throw new Error(data.message || "The analysis could not finish.");
    }
    if (data.status === "done") return data;
    return new Promise(function (resolve) {
      setTimeout(function () {
        resolve(getJob(job).then(poll));
      }, 500);
    });
  });
}

function jobsForThisPoint() {
  const job = currentJobId() || "a1b2c3";
  let stored = "";
  try {
    stored = sessionStorage.getItem("cellsound-batch") || "";
  } catch (error) {
    stored = "";
  }
  if (!stored) return Promise.resolve([job]);
  return getBatch(stored).then(function (data) {
    const ids = (data.videos || []).map(function (video) { return video.job_id; }).filter(Boolean);
    return ids.length ? ids : [job];
  }).catch(function () {
    return [job];
  });
}

document.getElementById("run").addEventListener("click", function () {
  const body = analysisBody();
  runError.hidden = true;
  document.getElementById("run").disabled = true;
  say("Running the analysis.");
  try {
    sessionStorage.setItem("cellsound-analysis", JSON.stringify(body));
  } catch (error) {
    /* The next page can still load the saved summary. */
  }
  jobsForThisPoint().then(function (ids) {
    let chain = Promise.resolve();
    ids.forEach(function (id, index) {
      chain = chain.then(function () {
        say("Running the analysis for video " + (index + 1) + " of " + ids.length + ". The same Point of Audio is used for each video.");
        return analyzeJob(id, body).then(function () {
          return waitUntilDone(id);
        });
      });
    });
    return chain.then(function () {
      return ids[0];
    });
  }).then(function (firstId) {
    setJobId(firstId);
    window.location.href = hrefWithJob("analysis.html");
  }).catch(function (error) {
    document.getElementById("run").disabled = false;
    runError.hidden = false;
    runError.textContent = error.message;
    say(error.message);
  });
});

const preview = document.getElementById("preview");
let dragging = false;

function pointFromEvent(event) {
  const matrix = preview.getScreenCTM();
  if (!matrix) return null;
  const point = preview.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const local = point.matrixTransform(matrix.inverse());
  return { x: local.x, y: local.y };
}

preview.addEventListener("pointerdown", function (event) {
  if (event.pointerType === "mouse" && event.button !== 0) return;
  const spot = pointFromEvent(event);
  if (!spot) return;
  dragging = true;
  preview.setPointerCapture(event.pointerId);
  event.preventDefault();
  moveTo(spot.x, spot.y, "", true);
});

preview.addEventListener("pointermove", function (event) {
  if (!dragging) return;
  const spot = pointFromEvent(event);
  if (!spot) return;
  moveTo(spot.x, spot.y, "", true);
});

function finishDrag() {
  if (!dragging) return;
  dragging = false;
  say(positionSentence(""));
}

preview.addEventListener("pointerup", finishDrag);
preview.addEventListener("pointercancel", finishDrag);

preview.addEventListener("keydown", function (event) {
  const moves = {
    ArrowUp: [0, -1, "up"],
    ArrowDown: [0, 1, "down"],
    ArrowLeft: [-1, 0, "left"],
    ArrowRight: [1, 0, "right"]
  };
  const move = moves[event.key];
  if (!move) return;
  event.preventDefault();
  moveTo(poa.x + move[0] * stepSize(), poa.y + move[1] * stepSize(), move[2]);
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
  draw();
  moveTo(frame.width / 2, frame.height / 2, "");
}).catch(function (error) {
  say(error.message);
});
refreshSliders();
