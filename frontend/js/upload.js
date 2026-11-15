/* Upload page: check the video, then show tracking progress for one or many files. */
const MAX_BYTES = 100 * 1024 * 1024;
const videoInput = document.getElementById("video");
const fileName = document.getElementById("file-name");
const fileError = document.getElementById("video-error");
const drop = document.getElementById("drop");
const startButton = document.getElementById("start");
const pixelSize = document.getElementById("px");
const frameTime = document.getElementById("dt");
const calibError = document.getElementById("calib-error");
const progress = document.getElementById("prog");
const progressLabel = document.getElementById("prog-label");
const statusText = document.getElementById("status-text");
const continueLink = document.getElementById("continue");
const soundButton = document.getElementById("sound-check");
const pauseButton = document.querySelector("[data-pause-mirror]");

let chosenFiles = [];
let pollTimer = null;
let batchTimer = null;
let lastMilestone = -1;
const batchList = document.getElementById("batch-list");
const batchHeading = document.getElementById("batch-progress-h");
const overallProgress = document.getElementById("overall-progress");

function showFileError(message) {
  fileError.hidden = false;
  fileError.textContent = message;
  videoInput.setAttribute("aria-invalid", "true");
  videoInput.setAttribute("aria-describedby", "video-hint video-error");
  videoInput.focus();
}

function clearFileError() {
  fileError.hidden = true;
  fileError.textContent = "";
  videoInput.removeAttribute("aria-invalid");
  videoInput.setAttribute("aria-describedby", "video-hint");
}

function isVideo(file) {
  const name = file.name.toLowerCase();
  return name.endsWith(".mp4") || name.endsWith(".mov") || file.type === "video/mp4" || file.type === "video/quicktime";
}

function takeFiles(fileList) {
  chosenFiles = [];
  const list = Array.prototype.slice.call(fileList || []);
  if (!list.length) {
    fileName.textContent = "No files chosen";
    showFileError("Choose at least one MP4 or MOV video.");
    return;
  }
  if (list.length > 8) {
    fileName.textContent = list.length + " files chosen";
    showFileError("Choose at most 8 videos at a time.");
    return;
  }
  for (let index = 0; index < list.length; index += 1) {
    if (!isVideo(list[index])) {
      showFileError(list[index].name + " is not an MP4 or MOV video.");
      return;
    }
    if (list[index].size > MAX_BYTES) {
      showFileError(list[index].name + " is larger than 100 MB.");
      return;
    }
  }
  chosenFiles = list;
  fileName.textContent = list.map(function (file) { return file.name; }).join(", ");
  clearFileError();
}

function calibrationValue(input) {
  if (input.value.trim() === "") return null;
  const number = Number(input.value);
  if (!Number.isFinite(number) || number <= 0) return "bad";
  return number;
}

function readCalibration() {
  const pixel = calibrationValue(pixelSize);
  const minutes = calibrationValue(frameTime);
  if (pixel === "bad" || minutes === "bad") {
    calibError.hidden = false;
    calibError.textContent = "Calibration numbers need to be greater than zero, or left blank.";
    (pixel === "bad" ? pixelSize : frameTime).focus();
    return null;
  }
  calibError.hidden = true;
  calibError.textContent = "";
  return { pixel_size_um: pixel, minutes_per_frame: minutes };
}

function saveCalibration(values) {
  try {
    sessionStorage.setItem("cellsound-calibration", JSON.stringify(values));
  } catch (error) {
    /* The analysis can still run without saved calibration. */
  }
}

function setBar(fraction) {
  const clamped = Math.max(0, Math.min(1, fraction || 0));
  progress.max = 100;
  progress.value = Math.round(clamped * 100);
  progress.textContent = Math.round(clamped * 100) + "%";
  const fill = document.getElementById("bar-fill");
  if (fill) fill.style.transform = "scaleX(" + clamped + ")";
}

function say(text) {
  if (statusText.textContent !== text) {
    statusText.textContent = text;
  }
}

function trackedLabel(frame, total, videoIndex, videoCount) {
  const which = videoCount > 1 ? (" from video " + videoIndex) : "";
  return "Tracked cells from " + frame + "/" + total + " frames" + which;
}

function showProgress(data) {
  if (data.status === "error") {
    continueLink.hidden = true;
    say(data.message || "Tracking stopped because of an error.");
    return;
  }
  const total = data.total_frames || 0;
  const frame = Math.min(data.frame || 0, total || 0);
  const done = data.status === "done";
  const fraction = total === 0 ? (done ? 1 : 0) : frame / total;
  setBar(fraction);
  progressLabel.textContent = done
    ? "Tracked cells from " + (total || frame) + "/" + (total || frame) + " frames — ready for analysis."
    : (total
      ? trackedLabel(frame, total, 1, 1)
      : "Tracked cells: starting…");

  batchList.hidden = false;
  batchHeading.hidden = false;
  batchList.replaceChildren();
  const item = document.createElement("li");
  item.className = "batch-item";
  const title = document.createElement("div");
  title.className = "batch-item-title";
  const num = document.createElement("span");
  num.className = "dot" + (done ? " done" : " now");
  num.setAttribute("aria-hidden", "true");
  num.textContent = "1";
  const name = document.createElement("strong");
  name.textContent = (chosenFiles[0] && chosenFiles[0].name) || "Video 1";
  title.appendChild(num);
  title.appendChild(name);
  const steps = document.createElement("ol");
  steps.className = "batch-steps";
  const trackState = done
    ? "Done"
    : (total ? ("frame " + frame + " of " + total) : "starting");
  const readyState = done ? "Done" : "Waiting";
  [
    ["Tracking cells", trackState, done ? "done" : "now"],
    ["Ready for analysis", readyState, done ? "done" : ""]
  ].forEach(function (row, stepIndex) {
    const step = document.createElement("li");
    const kind = row[2] ? (" " + row[2]) : "";
    step.innerHTML = "<span class=\"dot" + kind + "\" aria-hidden=\"true\">" + (stepIndex + 1) + "</span><span>" + row[0] + " <strong>" + row[1] + "</strong></span>";
    steps.appendChild(step);
  });
  item.appendChild(title);
  item.appendChild(steps);
  batchList.appendChild(item);

  const percent = Math.round(fraction * 100);
  const milestone = done ? 100 : Math.floor(percent / 25) * 25;
  if (milestone !== lastMilestone && (milestone >= 25 || done)) {
    lastMilestone = milestone;
    say(done
      ? "Tracking finished. Continue to analysis settings — that Point of Audio will apply to every uploaded video."
      : "Tracking is " + milestone + " percent done. We will announce when it finishes.");
  }
  continueLink.hidden = !done;
  if (done) {
    continueLink.href = hrefWithJob("setup.html");
  }
}

function poll(jobId) {
  getJob(jobId).then(function (data) {
    showProgress(data);
    if (data.status === "done" || data.status === "error") {
      startButton.disabled = false;
      if (data.status === "error") {
        say(data.message || "Tracking stopped because of an error.");
      }
      return;
    }
    pollTimer = setTimeout(function () {
      poll(jobId);
    }, 500);
  }).catch(function (error) {
    say(error.message);
  });
}

videoInput.addEventListener("change", function () {
  takeFiles(videoInput.files);
});

["dragenter", "dragover"].forEach(function (name) {
  drop.addEventListener(name, function (event) {
    event.preventDefault();
    drop.classList.add("drag");
  });
});
["dragleave", "drop"].forEach(function (name) {
  drop.addEventListener(name, function (event) {
    event.preventDefault();
    drop.classList.remove("drag");
  });
});
drop.addEventListener("drop", function (event) {
  takeFiles(event.dataTransfer && event.dataTransfer.files);
});

function showSingleProgress(on) {
  overallProgress.hidden = false;
  if (on) {
    batchHeading.hidden = false;
    batchList.hidden = false;
  }
}

function drawBatch(data) {
  const videos = data.videos || [];
  batchList.hidden = false;
  batchHeading.hidden = false;
  batchList.replaceChildren();
  let frameSum = 0;
  let frameTotal = 0;
  let ready = 0;
  let activeIndex = 0;
  let activeFrame = 0;
  let activeTotal = 0;
  videos.forEach(function (video, index) {
    const item = document.createElement("li");
    item.className = "batch-item";
    const total = video.total_frames || 0;
    const frame = video.frame || 0;
    const running = video.status === "running" || (total > 0 && frame > 0 && video.status !== "done");
    if (video.status === "done") {
      ready += 1;
      frameSum += total || 1;
      frameTotal += total || 1;
    } else if (total > 0) {
      frameSum += frame;
      frameTotal += total;
      if (running && !activeIndex) {
        activeIndex = index + 1;
        activeFrame = frame;
        activeTotal = total;
      }
    } else {
      frameTotal += 1;
    }
    const title = document.createElement("div");
    title.className = "batch-item-title";
    const num = document.createElement("span");
    num.className = "dot" + (video.status === "done" ? " done" : running ? " now" : "");
    num.setAttribute("aria-hidden", "true");
    num.textContent = String(index + 1);
    const name = document.createElement("strong");
    name.textContent = video.name || ("Video " + (index + 1));
    title.appendChild(num);
    title.appendChild(name);
    const steps = document.createElement("ol");
    steps.className = "batch-steps";
    let trackState = "Waiting";
    let trackKind = "";
    if (video.status === "done") {
      trackState = "frame " + (total || frame) + " of " + (total || frame);
      trackKind = "done";
    } else if (video.status === "error") {
      trackState = "Stopped";
    } else if (running || frame > 0) {
      trackState = total ? ("frame " + frame + " of " + total) : "starting";
      trackKind = "now";
    }
    const readyState = video.status === "done" ? "Done" : "Waiting";
    const readyKind = video.status === "done" ? "done" : "";
    [
      ["Tracking cells", trackState, trackKind],
      ["Ready for analysis", readyState, readyKind]
    ].forEach(function (row, stepIndex) {
      const step = document.createElement("li");
      const kind = row[2] ? (" " + row[2]) : "";
      step.innerHTML = "<span class=\"dot" + kind + "\" aria-hidden=\"true\">" + (stepIndex + 1) + "</span><span>" + row[0] + " <strong>" + row[1] + "</strong></span>";
      steps.appendChild(step);
    });
    item.appendChild(title);
    item.appendChild(steps);
    batchList.appendChild(item);
  });

  const totalVideos = videos.length || 1;
  const overall = frameTotal > 0 ? frameSum / frameTotal : ready / totalVideos;
  setBar(data.status === "done" ? 1 : overall);
  if (data.status === "done") {
    progressLabel.textContent = totalVideos + " of " + totalVideos + " videos ready for analysis.";
  } else if (activeTotal > 0) {
    progressLabel.textContent = trackedLabel(activeFrame, activeTotal, activeIndex || 1, totalVideos);
  } else {
    progressLabel.textContent = "Tracked cells: waiting to start…";
  }

  if (data.status === "done") {
    say(ready + " of " + totalVideos + " videos are ready. The same Point of Audio will apply to all of them.");
    return;
  }
  if (data.status === "error") {
    say("Every video stopped because of an error.");
    return;
  }
  const current = videos.find(function (video) { return video.status === "running"; });
  say(current
    ? "Working on " + current.name + ". " + ready + " of " + totalVideos + " finished."
    : "Videos are queued.");
}

function pollBatch(batchId) {
  getBatch(batchId).then(function (data) {
    drawBatch(data);
    if (data.status === "done" || data.status === "error") {
      startButton.disabled = false;
      if (data.status === "done") {
        const first = (data.videos || []).find(function (video) { return video.job_id; });
        if (first) setJobId(first.job_id);
        continueLink.hidden = false;
        continueLink.href = hrefWithJob("setup.html");
      }
      batchHeading.focus();
      return;
    }
    batchTimer = setTimeout(function () { pollBatch(batchId); }, 500);
  }).catch(function (error) {
    startButton.disabled = false;
    say(error.message);
  });
}

function appendCalibration(body, calibration) {
  if (calibration.pixel_size_um !== null) body.append("pixel_size_um", String(calibration.pixel_size_um));
  if (calibration.minutes_per_frame !== null) body.append("minutes_per_frame", String(calibration.minutes_per_frame));
}

startButton.addEventListener("click", function () {
  if (!chosenFiles.length) {
    showFileError("Choose at least one MP4 or MOV video.");
    return;
  }
  const calibration = readCalibration();
  if (!calibration) return;
  saveCalibration(calibration);
  startButton.disabled = true;
  continueLink.hidden = true;
  setBar(0);
  progressLabel.textContent = "Tracked cells: starting…";
  if (chosenFiles.length === 1) {
    showSingleProgress(true);
    const body = new FormData();
    body.append("video", chosenFiles[0]);
    appendCalibration(body, calibration);
    lastMilestone = -1;
    say("Tracking started. We will announce each quarter.");
    createJob(body).then(function (data) {
      setJobId(data.job_id);
      poll(data.job_id);
    }).catch(function (error) {
      startButton.disabled = false;
      showFileError(error.message);
    });
    return;
  }
  showSingleProgress(false);
  const body = new FormData();
  chosenFiles.forEach(function (file) { body.append("videos", file); });
  appendCalibration(body, calibration);
  say("Upload started. Videos will track one at a time. Progress updates below.");
  batchHeading.hidden = false;
  batchList.hidden = false;
  batchHeading.focus();
  createBatch(body).then(function (data) {
    try { sessionStorage.setItem("cellsound-batch", data.batch_id); } catch (error) { /* later pages can still use the address */ }
    drawBatch(data);
    pollBatch(data.batch_id);
  }).catch(function (error) {
    startButton.disabled = false;
    showFileError(error.message);
  });
});

const getStarted = document.getElementById("get-started");
if (getStarted) {
  getStarted.addEventListener("click", function () {
    const target = document.getElementById("before-h");
    if (!target) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const paused = document.body.classList.contains("pause-motion");
    target.scrollIntoView({ behavior: (reduce || paused) ? "auto" : "smooth", block: "start" });
    target.focus({ preventScroll: true });
  });
}

soundButton.addEventListener("click", function () {
  playSoundCheck();
  say("Playing a short sound check. Left speaker, then right speaker.");
});

if (pauseButton) {
  pauseButton.addEventListener("click", function () {
    const paused = pauseButton.getAttribute("aria-pressed") === "true";
    setPauseAnimations(!paused);
  });
}

window.addEventListener("pagehide", function () {
  clearTimeout(pollTimer);
  clearTimeout(batchTimer);
});
