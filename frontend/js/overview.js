/* Overview: thumbnails and behavior labels, then an optional behavior sort. */
const statusText = document.getElementById("status-text");
const videoList = document.getElementById("videos");
const emptyBox = document.getElementById("empty");
const sortSection = document.getElementById("sort-section");
const sortForm = document.getElementById("sort-form");
const sortButton = document.getElementById("sort-videos");
const questionInput = document.getElementById("question");

let videos = [];
let sorterConfigured = false;

function say(text) {
  if (statusText.textContent !== text) statusText.textContent = text;
}

function batchId() {
  try {
    return sessionStorage.getItem("cellsound-batch") || "";
  } catch (error) {
    return "";
  }
}

function behaviorText(motion) {
  const clusters = (motion && motion.clusters) || [];
  if (!clusters.length) return "";
  const ranked = clusters.slice().sort(function (a, b) {
    return (b.n_cells || 0) - (a.n_cells || 0);
  });
  const main = ranked[0];
  const second = (main.search_types || []).filter(function (name) {
    return name && name !== main.main_type;
  })[0];
  let text = "Main behavior: " + main.main_type + ".";
  if (second) text += " Close second: " + second + ".";
  if (ranked.length > 1) text += " " + ranked.length + " groups were traced.";
  if (main.description) text += " " + main.description;
  return text;
}

function render() {
  videoList.replaceChildren();
  videos.forEach(function (video) {
    const item = document.createElement("li");
    item.className = "overview-card";
    const heading = document.createElement("h3");
    heading.textContent = video.name;
    const image = document.createElement("img");
    image.src = video.imageUrl;
    image.alt = video.behavior
      ? video.name + ". " + video.behavior
      : "First frame of " + video.name;
    image.width = 512;
    image.height = 512;
    const summary = document.createElement("p");
    summary.textContent = video.behavior || "Behavior labels are not ready for this video yet.";
    item.appendChild(heading);
    item.appendChild(image);
    item.appendChild(summary);
    videoList.appendChild(item);
  });
}

function showEmpty(message) {
  emptyBox.hidden = false;
  document.getElementById("empty-text").textContent = message;
  videoList.hidden = true;
  sortSection.hidden = true;
  say(message);
}

function loadCatalog() {
  const storedBatch = batchId();
  if (storedBatch) {
    return getBatch(storedBatch).then(function (data) {
      return (data.videos || []).map(function (video) {
        return { job_id: video.job_id, name: video.name || video.job_id };
      });
    });
  }
  const job = currentJobId();
  if (!job) return Promise.resolve([]);
  return Promise.resolve([{ job_id: job, name: "This video" }]);
}

function attachLabels(catalog) {
  return Promise.all(catalog.map(function (video) {
    return getMotionTypes(video.job_id).then(function (motion) {
      video.behavior = behaviorText(motion);
      video.ready = Boolean(video.behavior);
      video.imageUrl = frameImageUrl(video.job_id);
      return video;
    }).catch(function () {
      video.behavior = "";
      video.ready = false;
      video.imageUrl = frameImageUrl(video.job_id);
      return video;
    });
  }));
}

function applySorter(status) {
  sorterConfigured = Boolean(status && status.configured);
  sortButton.disabled = !sorterConfigured;
  if (!sorterConfigured) {
    say(status && status.message
      ? status.message
      : "Sorting is unavailable. Videos stay in their current order, with their behavior labels.");
  }
}

sortForm.addEventListener("submit", function (event) {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) {
    say("Type a question before sorting. For example: show videos where cells move up.");
    questionInput.focus();
    return;
  }
  if (!sorterConfigured) {
    say("Sorting is unavailable because the server has no API key. Videos stay in their current order.");
    return;
  }
  sortButton.disabled = true;
  say("Sorting the videos.");
  sortVideos({
    question: question,
    videos: videos.map(function (video) {
      return { job_id: video.job_id, name: video.name };
    })
  }).then(function (result) {
    sortButton.disabled = !sorterConfigured;
    if (!result || result.configured === false) {
      sorterConfigured = false;
      sortButton.disabled = true;
      say(result && result.reason
        ? result.reason
        : "Sorting is unavailable. Videos stay in their current order.");
      return;
    }
    const order = result.job_ids || [];
    const byId = {};
    videos.forEach(function (video) { byId[video.job_id] = video; });
    const next = [];
    order.forEach(function (id) {
      if (byId[id]) {
        next.push(byId[id]);
        delete byId[id];
      }
    });
    Object.keys(byId).forEach(function (id) { next.push(byId[id]); });
    videos = next;
    render();
    say(result.reason || "Videos are ordered to match your question.");
  }).catch(function (error) {
    sortButton.disabled = !sorterConfigured;
    say(error.message || "Sorting could not finish. Videos stay in their current order.");
  });
});

document.querySelectorAll("[data-pause-mirror]").forEach(function (button) {
  button.addEventListener("click", function () {
    setPauseAnimations(!(readDisplay().pause));
  });
});

document.getElementById("trace-link").href = hrefWithJob("analysis.html");
document.getElementById("next-link").href = hrefWithJob("analysis.html");

loadCatalog().then(function (catalog) {
  if (!catalog.length) {
    showEmpty("Finish cell tracing first. Open detailed analysis after you upload a video and place the Point of Audio.");
    return null;
  }
  return attachLabels(catalog).then(function (ready) {
    const traced = ready.filter(function (video) { return video.ready; });
    if (!traced.length) {
      showEmpty("Finish cell tracing first. Behavior labels appear here after detailed analysis has followed the cells.");
      return null;
    }
    videos = ready;
    emptyBox.hidden = true;
    videoList.hidden = false;
    sortSection.hidden = false;
    render();
    say(traced.length + (traced.length === 1 ? " video has a behavior summary." : " videos have behavior summaries."));
    return getSorter().then(applySorter).catch(function () {
      applySorter({
        configured: false,
        message: "Sorting is unavailable. Videos stay in their current order, with their behavior labels."
      });
    });
  });
}).catch(function (error) {
  showEmpty(error.message || "Finish cell tracing first.");
});
