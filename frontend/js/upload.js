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

function setCheck(id, word, kind) {
  const item = document.getElementById(id);
  if (!item) return;
  const strong = item.querySelector("strong");
  if (strong) strong.textContent = word;
  const dot = item.querySelector(".dot");
  if (!dot) return;
  dot.classList.remove("now", "done");
  if (kind) dot.classList.add(kind);
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

function showProgress(data) {
  if (data.status === "error") {
    setCheck("check-received", "Done", "done");
    setCheck("check-track", "Stopped", "");
    setCheck("check-ready", "Waiting", "");
    continueLink.hidden = true;
    say(data.message || "Tracking stopped because of an error.");
    return;
  }
  const total = data.total_frames || 200;
  const frame = Math.min(data.frame || 0, total);
  const done = data.status === "done";
  const fraction = total === 0 ? 0 : frame / total;
  setBar(fraction);
  progressLabel.textContent = done
    ? "Tracking finished for this video."
    : "Finding and following nuclei: frame " + frame + " of " + total;
  document.getElementById("check-received-text").innerHTML =
    "Video received, " + total + " frames: <strong></strong>";
  document.getElementById("check-track-text").innerHTML =
    "Detecting and tracking nuclei: <strong></strong>";
  document.getElementById("check-ready-text").innerHTML =
    "Ready for Point of Audio: <strong></strong>";
  setCheck("check-received", "Done", "done");
  setCheck("check-track", done ? "Done" : "In progress", done ? "done" : "now");
  setCheck("check-ready", done ? "Done" : "Waiting", done ? "done" : "");

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
  batchHeading.hidden = on;
  batchList.hidden = on;
}

function videoStepLabel(video) {
  if (video.status === "done") return "3. Ready";
  if (video.status === "error") return "Stopped";
  if (video.status === "running" || (video.total_frames && video.frame > 0)) {
    const total = video.total_frames || 0;
    const frame = video.frame || 0;
    return total ? "2. Tracking frame " + frame + " of " + total : "2. Tracking";
  }
  if (video.status === "queued" || video.status === "received") return "1. Received — waiting";
  return "1. Waiting";
}

function drawBatch(data) {
  const videos = data.videos || [];
  batchList.replaceChildren();
  let frameSum = 0;
  let frameTotal = 0;
  let ready = 0;
  videos.forEach(function (video, index) {
    const item = document.createElement("li");
    item.className = "batch-item";
    const total = video.total_frames || 0;
    const frame = video.frame || 0;
    if (video.status === "done") {
      ready += 1;
      frameSum += total || 1;
      frameTotal += total || 1;
    } else if (total > 0) {
      frameSum += frame;
      frameTotal += total;
    } else {
      frameTotal += 1;
    }
    const title = document.createElement("div");
    title.className = "batch-item-title";
    const num = document.createElement("span");
    num.className = "dot" + (video.status === "done" ? " done" : video.status === "running" ? " now" : "");
    num.setAttribute("aria-hidden", "true");
    num.textContent = String(index + 1);
    const name = document.createElement("strong");
    name.textContent = video.name || ("Video " + (index + 1));
    title.appendChild(num);
    title.appendChild(name);
    const steps = document.createElement("ol");
    steps.className = "batch-steps";
    ["Received", "Tracking nuclei", "Ready for Point of Audio"].forEach(function (label, stepIndex) {
      const step = document.createElement("li");
      let state = "Waiting";
      let kind = "";
      if (stepIndex === 0) {
        state = "Done";
        kind = "done";
      } else if (stepIndex === 1) {
        if (video.status === "done") {
          state = "Done";
          kind = "done";
        } else if (video.status === "error") {
          state = "Stopped";
        } else if (video.status === "running" || frame > 0) {
          state = total ? ("Frame " + frame + " of " + total) : "In progress";
          kind = "now";
        }
      } else if (stepIndex === 2) {
        if (video.status === "done") {
          state = "Done";
          kind = "done";
        }
      }
      step.innerHTML = "<span class=\"dot" + (kind ? " " + kind : "") + "\" aria-hidden=\"true\">" + (stepIndex + 1) + "</span><span>" + label + ": <strong>" + state + "</strong></span>";
      steps.appendChild(step);
    });
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = videoStepLabel(video) + (video.message ? " — " + video.message : "");
    item.appendChild(title);
    item.appendChild(steps);
    item.appendChild(note);
    batchList.appendChild(item);
  });

  const totalVideos = videos.length || 1;
  const overall = frameTotal > 0 ? frameSum / frameTotal : ready / totalVideos;
  setBar(data.status === "done" ? 1 : overall);
  progressLabel.textContent = data.status === "done"
    ? totalVideos + " of " + totalVideos + " videos ready."
    : "Overall tracking progress across " + totalVideos + " videos (" + ready + " ready).";

  document.getElementById("check-received-text").innerHTML =
    totalVideos + " videos received: <strong></strong>";
  document.getElementById("check-track-text").innerHTML =
    "Detecting and tracking nuclei: <strong></strong>";
  document.getElementById("check-ready-text").innerHTML =
    "Ready for Point of Audio: <strong></strong>";
  setCheck("check-received", "Done", "done");
  setCheck(
    "check-track",
    data.status === "done" ? "Done" : (ready + " of " + totalVideos + " finished"),
    data.status === "done" ? "done" : "now"
  );
  setCheck(
    "check-ready",
    data.status === "done" ? "Done" : "Waiting",
    data.status === "done" ? "done" : ""
  );

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
  setCheck("check-ready", "Waiting", "");
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
