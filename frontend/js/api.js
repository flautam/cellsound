/* Every network call lives here. The FastAPI server in backend/app.py answers these. */
const USE_MOCK = false;

const mockRun = {
  frame: 0,
  total: 200,
  started: false,
  mode: "track"
};

function readBody(response) {
  return response.text().then(function (text) {
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (error) {
      data = {};
    }
    if (!response.ok) {
      let message = data.message;
      if (!message && (response.status === 404 || response.status === 405)) {
        message = "This page can show the site, but the analysis program is not running here. Processing works at http://127.0.0.1:8000/ on the computer that starts the server.";
      }
      const error = new Error(message || "The server could not finish that request.");
      error.code = data.error || "";
      throw error;
    }
    return data;
  });
}

function mockFile(name) {
  return fetch("mock/" + name + ".json").then(readBody);
}

function createJob(formData) {
  if (USE_MOCK) {
    mockRun.frame = 0;
    mockRun.started = true;
    return mockFile("job");
  }
  return fetch(apiPath("/api/jobs"), { method: "POST", body: formData }).then(readBody);
}

function getJob(jobId) {
  if (USE_MOCK) {
    return mockFile("progress").then(function (example) {
      const step = mockRun.mode === "analyze" ? 50 : 10;
      if (mockRun.started) {
        mockRun.frame = Math.min(example.total_frames, mockRun.frame + step);
      }
      const done = mockRun.started && mockRun.frame >= example.total_frames;
      return {
        status: done ? "done" : (mockRun.started ? "running" : example.status),
        stage: done ? "done" : (mockRun.mode === "analyze" ? "measuring" : "tracking"),
        frame: mockRun.started ? mockRun.frame : example.frame,
        total_frames: example.total_frames,
        message: done ? "Analysis finished" : example.message
      };
    });
  }
  return fetch(apiPath("/api/jobs/" + encodeURIComponent(jobId))).then(readBody);
}

function getFrame0(jobId) {
  if (USE_MOCK) {
    return mockFile("frame0");
  }
  return fetch(apiPath("/api/jobs/" + encodeURIComponent(jobId) + "/frame0")).then(readBody);
}

function getSummary(jobId) {
  if (USE_MOCK) return mockFile("summary");
  return fetch(apiPath("/api/jobs/" + encodeURIComponent(jobId) + "/summary")).then(readBody);
}

function getTraces(jobId) {
  if (USE_MOCK) return mockFile("traces");
  return fetch(apiPath("/api/jobs/" + encodeURIComponent(jobId) + "/traces")).then(readBody);
}

function downloadExport(jobId, include) {
  const query = include.join(",");
  if (USE_MOCK) return Promise.resolve(null);
  return fetch(apiPath("/api/jobs/" + encodeURIComponent(jobId) + "/export.zip?include=" + encodeURIComponent(query))).then(function (response) {
    if (!response.ok) throw new Error("The download could not start.");
    return response.blob();
  });
}

function frameImageUrl(jobId) {
  return apiPath("/api/jobs/" + encodeURIComponent(jobId) + "/frame0.png");
}

function frameStepUrl(jobId, index, template) {
  const number = String(index);
  if (template) return String(template).replace("{index}", number);
  return apiPath("/api/jobs/" + encodeURIComponent(jobId) + "/frames/" + encodeURIComponent(number) + ".jpg");
}

function audioUrl(jobId, name) {
  const file = String(name || "").replace(/\.wav$/, "");
  return apiPath("/api/jobs/" + encodeURIComponent(jobId) + "/audio/" + encodeURIComponent(file) + ".wav");
}

function createBatch(formData) {
  if (USE_MOCK) return mockFile("batch");
  return fetch(apiPath("/api/batches"), { method: "POST", body: formData }).then(readBody);
}

function getBatch(batchId) {
  if (USE_MOCK) return mockFile("batch");
  return fetch(apiPath("/api/batches/" + encodeURIComponent(batchId))).then(readBody);
}

function analyzeJob(jobId, body) {
  if (USE_MOCK) {
    mockRun.mode = "analyze";
    mockRun.frame = 0;
    mockRun.started = true;
    return mockFile("analyze");
  }
  return fetch(apiPath("/api/jobs/" + encodeURIComponent(jobId) + "/analyze"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(readBody);
}

const CODER_APP_ORIGIN = "https://8000--main--cellsound--flautam.coder.c1.workshop.cdr.dev";

function apiPath(path) {
  const selected = window.cellsound && window.cellsound.select;
  let base = selected ? String(selected).replace(/\/$/, "") : "";
  if (!base && window.location) {
    const host = window.location.host;
    if (host === "cellsound.select" || host === "www.cellsound.select") {
      base = CODER_APP_ORIGIN;
    }
  }
  return base + path;
}

function getMotionTypes(jobId) {
  if (USE_MOCK) return mockFile("motion_types");
  return fetch(apiPath("/api/jobs/" + encodeURIComponent(jobId) + "/motion-types")).then(readBody);
}

function getSorter() {
  if (USE_MOCK) {
    return Promise.resolve({
      configured: true,
      message: "Ask a question to sort these videos by behavior. For example: show videos where cells move up."
    });
  }
  return fetch(apiPath("/api/sort")).then(readBody);
}

function sortVideos(body) {
  if (USE_MOCK) return mockFile("sort");
  return fetch(apiPath("/api/sort"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(readBody);
}
