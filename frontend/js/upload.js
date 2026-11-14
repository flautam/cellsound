/* Upload page: check the video, then show pretend tracking progress. */
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

let chosenFile = null;
let pollTimer = null;
let lastMilestone = -1;

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

function isMp4(file) {
  const name = file.name.toLowerCase();
  return name.endsWith(".mp4") || file.type === "video/mp4";
}

function takeFile(file) {
  chosenFile = null;
  if (!file) {
    fileName.textContent = "No file chosen";
    showFileError("Choose an MP4 video first.");
    return;
  }
  fileName.textContent = file.name;
  if (!isMp4(file)) {
    showFileError("That file is not an MP4 video.");
    return;
  }
  if (file.size > MAX_BYTES) {
    showFileError("The video is larger than 100 MB.");
    return;
  }
  chosenFile = file;
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
  item.querySelector("strong").textContent = word;
  const dot = item.querySelector(".dot");
  dot.classList.remove("now", "done");
  if (kind) dot.classList.add(kind);
}

function say(text) {
  if (statusText.textContent !== text) {
    statusText.textContent = text;
  }
}

function showProgress(data) {
  const total = data.total_frames || 200;
  const frame = Math.min(data.frame || 0, total);
  const done = data.status === "done";
  const fraction = total === 0 ? 0 : frame / total;
  progress.max = total;
  progress.value = frame;
  progress.textContent = Math.round(fraction * 100) + "%";
  const fill = document.getElementById("bar-fill");
  if (fill) fill.style.transform = "scaleX(" + fraction + ")";
  progressLabel.textContent = "Finding and following nuclei: frame " + frame + " of " + total;
  setCheck("check-received", "Done", "done");
  setCheck("check-track", done ? "Done" : "In progress", done ? "done" : "now");
  document.getElementById("received-count").textContent = String(total);

  const percent = Math.round((frame / total) * 100);
  const milestone = done ? 100 : Math.floor(percent / 25) * 25;
  if (milestone !== lastMilestone && (milestone >= 25 || done)) {
    lastMilestone = milestone;
    say(done
      ? "Tracking finished. Your results are ready. Continue to Point of Audio and settings."
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
  takeFile(videoInput.files && videoInput.files[0]);
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
  const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
  takeFile(file);
});

startButton.addEventListener("click", function () {
  if (!chosenFile) {
    showFileError("Choose an MP4 video first.");
    return;
  }
  const calibration = readCalibration();
  if (!calibration) return;
  saveCalibration(calibration);
  const body = new FormData();
  body.append("video", chosenFile);
  if (calibration.pixel_size_um !== null) body.append("pixel_size_um", String(calibration.pixel_size_um));
  if (calibration.minutes_per_frame !== null) body.append("minutes_per_frame", String(calibration.minutes_per_frame));
  startButton.disabled = true;
  lastMilestone = -1;
  say("Tracking started. We will announce each quarter.");
  createJob(body).then(function (data) {
    setJobId(data.job_id);
    poll(data.job_id);
  }).catch(function (error) {
    startButton.disabled = false;
    showFileError(error.message);
  });
});

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
});
