/* Export: choose videos and what goes in the zip, then download it. */
const boxes = Array.prototype.slice.call(document.querySelectorAll("#help .opt input"));
const label = document.getElementById("download-label");
const statusBox = document.getElementById("status");
const audioNote = document.getElementById("d-audio");
const videoPick = document.getElementById("video-pick");
const videoOptions = document.getElementById("video-options");
const videoPickHint = document.getElementById("video-pick-hint");
let summary = null;
let catalog = [];

function chosen() {
  return boxes.filter(function (box) { return box.checked; }).map(function (box) { return box.value; });
}

function chosenVideos() {
  return Array.prototype.slice
    .call(document.querySelectorAll("#video-options input[type='checkbox']"))
    .filter(function (box) { return box.checked; })
    .map(function (box) { return box.value; });
}

function say(html) {
  statusBox.innerHTML = html;
}

function updateCount() {
  const count = chosen().length;
  const videos = chosenVideos().length;
  const noun = count === 1 ? "item" : "items";
  const videoNoun = videos === 1 ? "video" : "videos";
  label.textContent = videos
    ? "Download zip (" + count + " " + noun + " · " + videos + " " + videoNoun + ")"
    : "Download zip (" + count + " " + noun + ")";
  if (statusBox.querySelector(".result-bad") && count > 0 && videos > 0) say("");
}

function textFile(value) {
  return new TextEncoder().encode(value);
}

function crc32(data) {
  if (!crc32.table) {
    const table = new Uint32Array(256);
    for (let index = 0; index < 256; index += 1) {
      let value = index;
      for (let bit = 0; bit < 8; bit += 1) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
      table[index] = value >>> 0;
    }
    crc32.table = table;
  }
  let crc = 0xffffffff;
  for (let index = 0; index < data.length; index += 1) crc = crc32.table[(crc ^ data[index]) & 255] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function zipFiles(files) {
  const now = new Date();
  const time = ((now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1)) & 0xffff;
  const date = (((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate()) & 0xffff;
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  files.forEach(function (file) {
    const name = textFile(file.name);
    const data = file.data;
    const crc = crc32(data);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(10, time, true);
    local.setUint16(12, date, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, data.length, true);
    local.setUint32(22, data.length, true);
    local.setUint16(26, name.length, true);
    const central = new DataView(new ArrayBuffer(46));
    central.setUint32(0, 0x02014b50, true);
    central.setUint16(4, 20, true);
    central.setUint16(6, 20, true);
    central.setUint16(12, time, true);
    central.setUint16(14, date, true);
    central.setUint32(16, crc, true);
    central.setUint32(20, data.length, true);
    central.setUint32(24, data.length, true);
    central.setUint16(28, name.length, true);
    central.setUint32(42, offset, true);
    localParts.push(new Uint8Array(local.buffer), name, data);
    centralParts.push(new Uint8Array(central.buffer), name);
    offset += 30 + name.length + data.length;
  });
  const centralSize = centralParts.reduce(function (sum, part) { return sum + part.length; }, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, files.length, true);
  end.setUint16(10, files.length, true);
  end.setUint32(12, centralSize, true);
  end.setUint32(16, offset, true);
  const blobParts = localParts.concat(centralParts, [new Uint8Array(end.buffer)]);
  return new Blob(blobParts, { type: "application/zip" });
}

function savedJson(key) {
  try {
    return JSON.parse(sessionStorage.getItem(key) || "null");
  } catch (error) {
    return null;
  }
}

function mockFiles(include) {
  const files = [];
  const clusters = summary && summary.clusters ? summary.clusters : [];
  const population = summary && summary.population ? summary.population : {};
  if (include.indexOf("audio") !== -1) {
    const names = clusters.map(function (cluster) { return cluster.audio || "cluster_" + cluster.cluster_id + ".wav"; });
    files.push({ name: "sounds/README.txt", data: textFile("Sound files in the full export:\n" + names.join("\n") + "\n") });
  }
  if (include.indexOf("text") !== -1) {
    const folder = "this_video/descriptions/";
    const lines = ["Video overview", population.description || "Population summary.", "", "Chunks"];
    clusters.forEach(function (cluster) {
      const header = "Chunk " + cluster.cluster_id + ": " + (cluster.name || "");
      const body = cluster.description || "";
      lines.push(header, body, "");
      files.push({
        name: folder + "chunk_" + cluster.cluster_id + ".txt",
        data: textFile(header + "\n\n" + body + "\n")
      });
    });
    files.push({ name: folder + "overview.txt", data: textFile((population.description || "Population summary.") + "\n") });
    files.push({ name: folder + "summary.txt", data: textFile(lines.join("\n").replace(/\n+$/, "\n")) });
  }
  if (include.indexOf("tracks") !== -1 || include.indexOf("data") !== -1) {
    files.push({ name: "this_video/data/tracks.csv", data: textFile("cell_id,frame,x,y\n") });
    files.push({ name: "this_video/data/motion.csv", data: textFile("cell_id,frame,vx,vy,speed\n") });
  }
  if (include.indexOf("chunks_csv") !== -1 || include.indexOf("data") !== -1) {
    const rows = ["cluster_id,name,n_cells,mean_speed,mean_radial_v,mean_angular_v"];
    clusters.forEach(function (cluster) {
      rows.push([cluster.cluster_id, "\"" + cluster.name + "\"", cluster.n_cells, cluster.mean_speed, cluster.mean_radial_v, cluster.mean_angular_v].join(","));
    });
    files.push({ name: "this_video/data/clusters.csv", data: textFile(rows.join("\n") + "\n") });
  }
  if (include.indexOf("tracking_video") !== -1 || include.indexOf("data") !== -1) {
    files.push({ name: "this_video/videos/README.txt", data: textFile("Full export includes videos/tracked.mp4.\n") });
  }
  if (include.indexOf("overview_video") !== -1 || include.indexOf("data") !== -1) {
    files.push({ name: "this_video/videos/overview_README.txt", data: textFile("Full export includes videos/overview_clusters.mp4 and chunk overlays.\n") });
  }
  if (include.indexOf("map") !== -1 || include.indexOf("data") !== -1) {
    files.push({ name: "this_video/maps/README.txt", data: textFile("Full export includes maps/clusters_map.png.\n") });
  }
  if (include.indexOf("settings") !== -1) {
    const settings = {
      calibration: savedJson("cellsound-calibration"),
      analysis: savedJson("cellsound-analysis")
    };
    files.push({ name: "settings.json", data: textFile(JSON.stringify(settings, null, 2) + "\n") });
  }
  return files;
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
}

function succeed(itemCount, videoCount) {
  const noun = itemCount === 1 ? "item" : "items";
  const videoNoun = videoCount === 1 ? "video" : "videos";
  say("<div class=\"result pop\"><svg aria-hidden=\"true\" width=\"40\" height=\"40\" viewBox=\"0 0 40 40\"><circle cx=\"20\" cy=\"20\" r=\"18\" fill=\"var(--ok)\"></circle><path d=\"m12 20 6 6 10-12\" fill=\"none\" stroke=\"var(--bg)\" stroke-width=\"3.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"></path></svg><p>Download started: cellsound-results.zip, " + itemCount + " " + noun + " from " + videoCount + " " + videoNoun + ".</p></div>");
}

function batchId() {
  try {
    return sessionStorage.getItem("cellsound-batch") || "";
  } catch (error) {
    return "";
  }
}

function loadCatalog() {
  const shortlist = readShortlist().filter(Boolean);
  const job = currentJobId();
  const storedBatch = batchId();

  function withNames(ids, nameMap) {
    return ids.map(function (id, index) {
      return {
        job_id: id,
        name: (nameMap && nameMap[id]) || ("Video " + (index + 1))
      };
    });
  }

  function namesFromBatch(data) {
    const map = {};
    (data.videos || []).forEach(function (video) {
      if (video.job_id) map[video.job_id] = video.name || video.job_id;
    });
    return map;
  }

  if (storedBatch) {
    return getBatch(storedBatch).then(function (data) {
      const map = namesFromBatch(data);
      const done = (data.videos || [])
        .filter(function (video) { return video.status === "done" && video.job_id; })
        .map(function (video) { return video.job_id; });
      // Prefer every finished video in the batch so both uploads show up.
      let ids = done.slice();
      shortlist.forEach(function (id) {
        if (ids.indexOf(id) < 0) ids.push(id);
      });
      if (!ids.length && job && job !== "a1b2c3") ids = [job];
      return withNames(ids, map).map(function (video) {
        video.prechecked = !shortlist.length || shortlist.indexOf(video.job_id) >= 0;
        return video;
      });
    }).catch(function () {
      if (shortlist.length) return withNames(shortlist, null);
      if (job && job !== "a1b2c3") return withNames([job], null);
      return [];
    });
  }

  if (shortlist.length) return Promise.resolve(withNames(shortlist, null));
  if (job && job !== "a1b2c3") return Promise.resolve(withNames([job], null));
  return Promise.resolve([]);
}

function paintVideos(list) {
  catalog = list || [];
  videoOptions.replaceChildren();
  if (!catalog.length) {
    videoPick.hidden = true;
    updateCount();
    return;
  }
  videoPick.hidden = false;
  videoPickHint.textContent = catalog.length === 1
    ? "This finished video will be included in the zip."
    : "Choose which finished videos go in the zip. Each selected video gets its own folder.";
  catalog.forEach(function (video, index) {
    const tint = ["var(--mint)", "var(--sky)", "var(--lilac)", "var(--butter)", "var(--peach)"][index % 5];
    const labelEl = document.createElement("label");
    labelEl.className = "opt";
    labelEl.style.background = tint;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = video.job_id;
    input.checked = video.prechecked !== false;
    input.setAttribute("aria-label", "Include " + video.name);
    input.addEventListener("change", updateCount);
    const span = document.createElement("span");
    const strong = document.createElement("strong");
    strong.textContent = video.name;
    const note = document.createElement("p");
    note.textContent = "Include this video’s selected files in the zip.";
    span.appendChild(strong);
    span.appendChild(note);
    labelEl.appendChild(input);
    labelEl.appendChild(span);
    videoOptions.appendChild(labelEl);
  });
  updateCount();
}

boxes.forEach(function (box) {
  box.addEventListener("change", updateCount);
});

document.getElementById("videos-all").addEventListener("click", function () {
  Array.prototype.forEach.call(document.querySelectorAll("#video-options input[type='checkbox']"), function (box) {
    box.checked = true;
  });
  updateCount();
  say("Selected all videos for download.");
});

document.getElementById("videos-none").addEventListener("click", function () {
  Array.prototype.forEach.call(document.querySelectorAll("#video-options input[type='checkbox']"), function (box) {
    box.checked = false;
  });
  updateCount();
  say("Cleared the video selection.");
});

document.getElementById("download").addEventListener("click", function () {
  const include = chosen();
  if (!include.length) {
    say("<p class=\"result-bad\">Choose at least one item to include, then download.</p>");
    return;
  }
  const jobIds = chosenVideos();
  if (!jobIds.length) {
    say("<p class=\"result-bad\">Choose at least one video to download.</p>");
    return;
  }
  say("Preparing the zip for " + jobIds.length + " video" + (jobIds.length === 1 ? "" : "s") + "…");
  setJobId(jobIds[0]);
  downloadExport(jobIds[0], include, jobIds).then(function (blob) {
    saveBlob(blob || zipFiles(mockFiles(include)), "cellsound-results.zip");
    succeed(include.length, jobIds.length);
  }).catch(function (error) {
    say("<p class=\"result-bad\">" + (error.message || "The download could not start.") + "</p>");
  });
});

const pauseButton = document.querySelector("[data-pause-mirror]");
if (pauseButton) {
  pauseButton.addEventListener("click", function () {
    setPauseAnimations(pauseButton.getAttribute("aria-pressed") !== "true");
  });
}

loadCatalog().then(function (list) {
  paintVideos(list);
  if (!list.length) {
    say("<p class=\"result-bad\">No finished videos are linked yet. Go back to Overview, select videos, continue through detailed analysis, then export.</p>");
    return;
  }
  setJobId(list[0].job_id);
  return getSummary(list[0].job_id).then(function (data) {
    summary = data;
    if (summary.clusters) {
      summary.clusters = summary.clusters.slice().sort(function (a, b) {
        return Number(a.cluster_id) - Number(b.cluster_id);
      });
    }
    const count = data.population ? data.population.n_clusters : 0;
    if (count) audioNote.textContent = "One sound file for each of the " + count + " chunks.";
  });
}).catch(function () {
  /* The choices still work without the summary. */
});
