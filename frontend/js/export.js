/* Export: choose what goes in the zip, then download it. */
const boxes = Array.prototype.slice.call(document.querySelectorAll(".opt input"));
const label = document.getElementById("download-label");
const statusBox = document.getElementById("status");
const audioNote = document.getElementById("d-audio");
let summary = null;

function chosen() {
  return boxes.filter(function (box) { return box.checked; }).map(function (box) { return box.value; });
}

function say(html) {
  statusBox.innerHTML = html;
}

function updateCount() {
  const count = chosen().length;
  const noun = count === 1 ? "item" : "items";
  label.textContent = "Download zip (" + count + " " + noun + ")";
  if (statusBox.querySelector(".result-bad") && count > 0) say("");
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
    const names = ["overview.wav"].concat(clusters.map(function (cluster) { return cluster.audio || "cluster_" + cluster.cluster_id + ".wav"; }));
    files.push({ name: "sounds/README.txt", data: textFile("Sound files in the full export:\n" + names.join("\n") + "\n") });
  }
  if (include.indexOf("text") !== -1) {
    const lines = [population.description || "Population summary."];
    clusters.forEach(function (cluster) {
      lines.push("Cluster " + cluster.cluster_id + ": " + cluster.name + ". " + (cluster.description || ""));
    });
    files.push({ name: "descriptions/summary.txt", data: textFile(lines.join("\n\n") + "\n") });
  }
  if (include.indexOf("data") !== -1) {
    const rows = ["cluster_id,name,n_cells,mean_speed,mean_radial_v,mean_angular_v"];
    clusters.forEach(function (cluster) {
      rows.push([cluster.cluster_id, "\"" + cluster.name + "\"", cluster.n_cells, cluster.mean_speed, cluster.mean_radial_v, cluster.mean_angular_v].join(","));
    });
    files.push({ name: "data/clusters.csv", data: textFile(rows.join("\n") + "\n") });
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

function succeed(count) {
  const noun = count === 1 ? "item" : "items";
  say("<div class=\"result pop\"><svg aria-hidden=\"true\" width=\"40\" height=\"40\" viewBox=\"0 0 40 40\"><circle cx=\"20\" cy=\"20\" r=\"18\" fill=\"var(--ok)\"></circle><path d=\"m12 20 6 6 10-12\" fill=\"none\" stroke=\"var(--bg)\" stroke-width=\"3.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"></path></svg><p>Download started: cellsound-results.zip, " + count + " " + noun + ".</p></div>");
}

boxes.forEach(function (box) {
  box.addEventListener("change", updateCount);
});

document.getElementById("download").addEventListener("click", function () {
  const include = chosen();
  if (!include.length) {
    say("<p class=\"result-bad\">Choose at least one item to include, then download.</p>");
    return;
  }
  const jobId = currentJobId() || "a1b2c3";
  downloadExport(jobId, include).then(function (blob) {
    saveBlob(blob || zipFiles(mockFiles(include)), "cellsound-results.zip");
    succeed(include.length);
  }).catch(function (error) {
    say("<p class=\"result-bad\">" + error.message + "</p>");
  });
});

const pauseButton = document.querySelector("[data-pause-mirror]");
if (pauseButton) {
  pauseButton.addEventListener("click", function () {
    setPauseAnimations(pauseButton.getAttribute("aria-pressed") !== "true");
  });
}

getSummary(currentJobId() || "a1b2c3").then(function (data) {
  summary = data;
  if (summary.clusters) {
    summary.clusters = summary.clusters.slice().sort(function (a, b) {
      return Number(a.cluster_id) - Number(b.cluster_id);
    });
  }
  const count = data.population ? data.population.n_clusters : 0;
  if (count) audioNote.textContent = "Overview plus one file for each of the " + count + " clusters.";
}).catch(function () {
  /* The choices still work without the summary. */
});
