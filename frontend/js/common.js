/* Shared header behavior: text size, contrast, job id, and the step links. */
const DISPLAY_KEY = "cellsound-display";
const JOB_KEY = "cellsound-job";

const STEPS = [
  { file: "index.html", label: "Upload" },
  { file: "setup.html", label: "Point of Audio" },
  { file: "overview.html", label: "Overview" },
  { file: "clusters.html", label: "Clusters" },
  { file: "export.html", label: "Export" }
];

function readDisplay() {
  const fallback = { size: "1", theme: "light", pause: false };
  try {
    const saved = JSON.parse(localStorage.getItem(DISPLAY_KEY) || "null");
    if (!saved) return fallback;
    return {
      size: ["1", "2", "3"].includes(String(saved.size)) ? String(saved.size) : "1",
      theme: ["light", "dark", "hc"].includes(saved.theme) ? saved.theme : "light",
      pause: Boolean(saved.pause)
    };
  } catch (error) {
    return fallback;
  }
}

function saveDisplay(settings) {
  try {
    localStorage.setItem(DISPLAY_KEY, JSON.stringify(settings));
  } catch (error) {
    /* Private browsing can block storage. The choice still applies this visit. */
  }
}

function applyDisplay(settings) {
  document.body.className = "app theme-" + settings.theme + " size-" + settings.size;
  if (settings.pause) {
    document.body.classList.add("pause-motion");
  }
  refreshPauseButtons(settings.pause);
}

function refreshPauseButtons(paused) {
  document.querySelectorAll("[data-pause-mirror]").forEach(function (button) {
    button.setAttribute("aria-pressed", paused ? "true" : "false");
    const label = button.querySelector("[data-pause-label]");
    if (label) {
      label.textContent = paused ? "Play animations" : "Pause animations";
    }
    const pauseIcon = button.querySelector("[data-icon='pause']");
    const playIcon = button.querySelector("[data-icon='play']");
    if (pauseIcon) pauseIcon.hidden = paused;
    if (playIcon) playIcon.hidden = !paused;
  });
}

function setPauseAnimations(paused) {
  const next = readDisplay();
  next.pause = paused;
  applyDisplay(next);
  saveDisplay(next);
  const box = document.getElementById("pause-animations");
  if (box) box.checked = paused;
}

function setJobId(id) {
  try {
    sessionStorage.setItem(JOB_KEY, id);
  } catch (error) {
    /* ignore */
  }
  const url = new URL(location.href);
  url.searchParams.set("job", id);
  history.replaceState(null, "", url);
  setupSteps();
}

function currentJobId() {
  const fromUrl = new URLSearchParams(location.search).get("job");
  if (fromUrl) {
    try {
      sessionStorage.setItem(JOB_KEY, fromUrl);
    } catch (error) {
      /* ignore */
    }
    return fromUrl;
  }
  try {
    return sessionStorage.getItem(JOB_KEY) || "";
  } catch (error) {
    return "";
  }
}

function hrefWithJob(file) {
  const job = currentJobId();
  return job ? file + "?job=" + encodeURIComponent(job) : file;
}

function setupDisplayMenu(settings) {
  const button = document.getElementById("display-button");
  const panel = document.getElementById("display-panel");
  const size = document.getElementById("text-size");
  const contrast = document.getElementById("contrast");
  const pause = document.getElementById("pause-animations");
  const done = document.getElementById("display-done");
  if (!button || !panel) return;

  size.value = settings.size;
  contrast.value = settings.theme;
  pause.checked = settings.pause;

  function openPanel(open) {
    button.setAttribute("aria-expanded", open ? "true" : "false");
    panel.hidden = !open;
  }

  button.addEventListener("click", function () {
    openPanel(panel.hidden);
  });

  function remember() {
    const next = {
      size: size.value,
      theme: contrast.value,
      pause: pause.checked
    };
    applyDisplay(next);
    saveDisplay(next);
    refreshPauseButtons(next.pause);
  }

  size.addEventListener("change", remember);
  contrast.addEventListener("change", remember);
  pause.addEventListener("change", remember);

  done.addEventListener("click", function () {
    openPanel(false);
    button.focus();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      openPanel(false);
    }
  });

  document.addEventListener("click", function (event) {
    if (!panel.hidden && !button.contains(event.target) && !panel.contains(event.target)) {
      openPanel(false);
    }
  });
}

function setupSteps() {
  const current = document.body.getAttribute("data-step");
  const brand = document.querySelector(".brand");
  if (brand) brand.href = hrefWithJob("index.html");
  document.querySelectorAll("[data-step-link]").forEach(function (link) {
    const number = link.getAttribute("data-step-link");
    link.href = hrefWithJob(STEPS[Number(number) - 1].file);
    if (number === current) {
      link.setAttribute("aria-current", "step");
    } else {
      link.removeAttribute("aria-current");
    }
  });
}

const settings = readDisplay();
applyDisplay(settings);
currentJobId();
setupSteps();
setupDisplayMenu(settings);
