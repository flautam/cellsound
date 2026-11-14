/* Every network call lives here. The FastAPI server in backend/app.py answers these. */
const USE_MOCK = false;

const mockRun = {
  frame: 0,
  total: 200,
  started: false,
  mode: "track"
};

function readBody(response) {
  return response.json().catch(function () {
    return {};
  }).then(function (data) {
    if (!response.ok) {
      const error = new Error(data.message || "The server could not finish that request.");
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
  return fetch("/api/jobs", { method: "POST", body: formData }).then(readBody);
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
  return fetch("/api/jobs/" + encodeURIComponent(jobId)).then(readBody);
}

function getFrame0(jobId) {
  if (USE_MOCK) {
    return mockFile("frame0");
  }
  return fetch("/api/jobs/" + encodeURIComponent(jobId) + "/frame0").then(readBody);
}

function getSummary(jobId) {
  if (USE_MOCK) return mockFile("summary");
  return fetch("/api/jobs/" + encodeURIComponent(jobId) + "/summary").then(readBody);
}

function getTraces(jobId) {
  if (USE_MOCK) return mockFile("traces");
  return fetch("/api/jobs/" + encodeURIComponent(jobId) + "/traces").then(readBody);
}

function downloadExport(jobId, include) {
  const query = include.join(",");
  if (USE_MOCK) return Promise.resolve(null);
  return fetch("/api/jobs/" + encodeURIComponent(jobId) + "/export.zip?include=" + encodeURIComponent(query)).then(function (response) {
    if (!response.ok) throw new Error("The download could not start.");
    return response.blob();
  });
}

function frameImageUrl(jobId) {
  return "/api/jobs/" + encodeURIComponent(jobId) + "/frame0.png";
}

function frameStepUrl(jobId, index, template) {
  const number = String(index);
  if (template) return String(template).replace("{index}", number);
  return "/api/jobs/" + encodeURIComponent(jobId) + "/frames/" + encodeURIComponent(number) + ".jpg";
}

function audioUrl(jobId, name) {
  const file = String(name || "").replace(/\.wav$/, "");
  return "/api/jobs/" + encodeURIComponent(jobId) + "/audio/" + encodeURIComponent(file) + ".wav";
}

function analyzeJob(jobId, body) {
  if (USE_MOCK) {
    mockRun.mode = "analyze";
    mockRun.frame = 0;
    mockRun.started = true;
    return mockFile("analyze");
  }
  return fetch("/api/jobs/" + encodeURIComponent(jobId) + "/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(readBody);
}
