/* Overview: selectable thumbnails after tracing + analysis; local direction sort. */
const statusText = document.getElementById("status-text");
const videoList = document.getElementById("videos");
const emptyBox = document.getElementById("empty");
const sortSection = document.getElementById("sort-section");
const sortForm = document.getElementById("sort-form");
const sortButton = document.getElementById("sort-videos");
const questionInput = document.getElementById("question");
const sortResult = document.getElementById("sort-result");
const nextButton = document.getElementById("next-link");
const selectAllButton = document.getElementById("select-all");
const deselectAllButton = document.getElementById("deselect-all");
const selectTools = document.getElementById("select-tools");

let videos = [];
let selected = {};
let matchIds = {};

const DIRECTIONS = ["left", "right", "up", "down"];
const DIRECTION_LINES = {
  left: "These cells mostly move left.",
  right: "These cells mostly move right.",
  up: "These cells mostly move up, toward the top of the image.",
  down: "These cells mostly move down, toward the bottom of the image."
};

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

function groupDirection(cluster) {
  if (cluster && DIRECTIONS.indexOf(cluster.main_type) >= 0) return cluster.main_type;
  const scores = (cluster && cluster.scores) || {};
  let best = DIRECTIONS[0];
  let bestScore = -1;
  DIRECTIONS.forEach(function (name) {
    let score = Number(scores[name]);
    if (!isFinite(score)) score = 0;
    if (score > bestScore) {
      best = name;
      bestScore = score;
    }
  });
  return best;
}

function behaviorText(motion) {
  const clusters = (motion && motion.clusters) || [];
  if (!clusters.length) return "";
  const ranked = clusters.slice().sort(function (a, b) {
    return (b.n_cells || 0) - (a.n_cells || 0);
  });
  const main = ranked[0];
  const label = groupDirection(main);
  const second = (main.search_types || []).filter(function (name) {
    return DIRECTIONS.indexOf(name) >= 0 && name !== label;
  })[0];
  let text = "Main behavior: " + label + ".";
  if (second) text += " Close second: " + second + ".";
  if (ranked.length > 1) text += " " + ranked.length + " groups were traced.";
  text += " " + DIRECTION_LINES[label];
  return text;
}

function briefSummary(summary) {
  const overview = (summary && summary.overview) || {};
  const pop = (summary && summary.population) || {};
  if (overview.description) {
    const text = String(overview.description);
    const cut = text.indexOf(". ");
    return cut > 40 && cut < 160 ? text.slice(0, cut + 1) : text.slice(0, 160) + (text.length > 160 ? "…" : "");
  }
  if (pop.stats_line) return pop.stats_line;
  return "";
}

function selectedCount() {
  return videos.filter(function (video) { return selected[video.job_id]; }).length;
}

function updateContinue() {
  const count = selectedCount();
  nextButton.disabled = count === 0;
  nextButton.textContent = count
    ? "Continue to detailed analysis (" + count + ")"
    : "Select videos to continue";
  document.getElementById("select-hint").textContent = count
    ? count + " video" + (count === 1 ? "" : "s") + " selected for detailed analysis."
    : "Select one or more thumbnails to shortlist for detailed analysis.";
}

function render() {
  videoList.replaceChildren();
  videos.forEach(function (video) {
    const item = document.createElement("li");
    item.className = "overview-card"
      + (selected[video.job_id] ? " is-selected" : "")
      + (matchIds[video.job_id] ? " is-match" : "");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "overview-select";
    button.setAttribute("aria-pressed", selected[video.job_id] ? "true" : "false");
    const head = document.createElement("div");
    head.className = "overview-head";
    const heading = document.createElement("h3");
    heading.textContent = video.name;
    const mark = document.createElement("span");
    mark.className = "overview-check";
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = selected[video.job_id] ? "✓" : "";
    head.appendChild(heading);
    head.appendChild(mark);
    const behavior = video.behavior || "Behavior labels are not ready for this video yet.";
    const matched = Boolean(matchIds[video.job_id]);
    const image = document.createElement("img");
    image.src = video.imageUrl;
    image.alt = "First frame of " + video.name + ". " + behavior;
    image.width = 512;
    image.height = 512;
    const summary = document.createElement("p");
    summary.textContent = behavior;
    button.appendChild(head);
    if (matched) {
      const tag = document.createElement("p");
      tag.className = "match-tag";
      tag.textContent = "Match";
      button.appendChild(tag);
    }
    button.appendChild(image);
    button.appendChild(summary);
    button.setAttribute(
      "aria-label",
      (selected[video.job_id] ? "Selected. " : "Not selected. ")
        + (matched ? "Match. " : "")
        + video.name + ". " + behavior + " Activate to toggle."
    );
    button.addEventListener("click", function () {
      selected[video.job_id] = !selected[video.job_id];
      render();
      say((selected[video.job_id] ? "Selected " : "Removed ") + video.name + ".");
      updateContinue();
    });
    item.appendChild(button);
    videoList.appendChild(item);
  });
  updateContinue();
}

function showEmpty(message) {
  emptyBox.hidden = false;
  document.getElementById("empty-text").textContent = message;
  videoList.hidden = true;
  sortSection.hidden = true;
  if (selectTools) selectTools.hidden = true;
  nextButton.hidden = true;
  say(message);
}

function loadCatalog() {
  const storedBatch = batchId();
  if (storedBatch) {
    return getBatch(storedBatch).then(function (data) {
      return (data.videos || []).map(function (video) {
        return { job_id: video.job_id, name: video.name || video.job_id };
      }).filter(function (video) { return video.job_id; });
    });
  }
  const job = currentJobId();
  if (!job) return Promise.resolve([]);
  return Promise.resolve([{ job_id: job, name: "This video" }]);
}

function attachLabels(catalog) {
  return Promise.all(catalog.map(function (video) {
    return getJob(video.job_id).then(function (job) {
      video.tracked = job.status === "done" || job.stage === "done";
      return getMotionTypes(video.job_id).then(function (motion) {
        video.behavior = behaviorText(motion);
        video.ready = Boolean(video.behavior);
        video.imageUrl = frameImageUrl(video.job_id);
        return video;
      }).catch(function () {
        return getSummary(video.job_id).then(function (summary) {
          video.behavior = briefSummary(summary);
          video.ready = Boolean(video.behavior);
          video.imageUrl = frameImageUrl(video.job_id);
          return video;
        }).catch(function () {
          video.behavior = "";
          video.ready = false;
          video.imageUrl = frameImageUrl(video.job_id);
          return video;
        });
      });
    }).catch(function () {
      video.tracked = false;
      video.behavior = "";
      video.ready = false;
      video.imageUrl = frameImageUrl(video.job_id);
      return video;
    });
  }));
}

sortForm.addEventListener("submit", function (event) {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) {
    say("Type a question before sorting. For example: show videos where cells move up.");
    questionInput.focus();
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
    sortButton.disabled = false;
    const order = (result && result.job_ids) || [];
    const hits = (result && result.matched_job_ids) || [];
    matchIds = {};
    hits.forEach(function (id) { matchIds[id] = true; });
    videos.forEach(function (video) {
      selected[video.job_id] = Boolean(matchIds[video.job_id]);
    });
    if (order.length) {
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
    }
    render();
    const matchCount = hits.length;
    const total = videos.length;
    if (sortResult) {
      sortResult.hidden = false;
      sortResult.textContent = matchCount + " of " + total + " videos match this search. Closest matches appear first.";
    }
    const base = (result && result.reason) || "Matching videos are marked Match.";
    say(base + " " + matchCount + " of " + total + " match. Closest matches appear first."
      + (matchCount
        ? " Selected " + matchCount + " matching video" + (matchCount === 1 ? "" : "s") + "."
        : " Cleared the selection because nothing matched."));
  }).catch(function (error) {
    sortButton.disabled = false;
    say(error.message || "Sorting could not finish. Videos stay in their current order.");
  });
});

selectAllButton.addEventListener("click", function () {
  videos.forEach(function (video) {
    selected[video.job_id] = true;
  });
  render();
  say("Selected all videos.");
});

deselectAllButton.addEventListener("click", function () {
  videos.forEach(function (video) {
    selected[video.job_id] = false;
  });
  render();
  say("Cleared the selection.");
});

nextButton.addEventListener("click", function () {
  const ids = videos.filter(function (video) { return selected[video.job_id]; }).map(function (video) { return video.job_id; });
  if (!ids.length) {
    say("Select at least one video thumbnail before continuing.");
    return;
  }
  saveShortlist(ids);
  setJobId(ids[0]);
  window.location.href = hrefWithJob("analysis.html");
});

document.getElementById("trace-link").href = hrefWithJob("setup.html");

loadCatalog().then(function (catalog) {
  if (!catalog.length) {
    showEmpty("Upload a video and finish cell tracing first, then open analysis settings and run analysis.");
    return null;
  }
  return attachLabels(catalog).then(function (ready) {
    const traced = ready.filter(function (video) { return video.ready; });
    if (!traced.length) {
      const trackedOnly = ready.some(function (video) { return video.tracked; });
      showEmpty(trackedOnly
        ? "Cell tracing finished, but summaries are not ready yet. Open analysis settings and run analysis first."
        : "Finish cell tracing first, then open analysis settings and run analysis.");
      return null;
    }
    videos = ready;
    const prior = readShortlist();
    ready.forEach(function (video) {
      selected[video.job_id] = prior.length ? prior.indexOf(video.job_id) >= 0 : true;
    });
    emptyBox.hidden = true;
    videoList.hidden = false;
    sortSection.hidden = false;
    if (selectTools) selectTools.hidden = false;
    nextButton.hidden = false;
    sortButton.disabled = false;
    render();
    say(traced.length + (traced.length === 1 ? " video has a behavior summary. Select thumbnails for detailed analysis." : " videos have behavior summaries. Select thumbnails for detailed analysis."));
  });
}).catch(function (error) {
  showEmpty(error.message || "Finish cell tracing and analysis settings first.");
});
