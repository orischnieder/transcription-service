"use strict";

// Single-page UI: choose a file -> upload (XHR, for upload progress) -> poll the job -> show it.
// All server text is inserted with textContent, never innerHTML.

(() => {
  const POLL_INTERVAL_MS = 1500;
  const MAX_POLL_FAILURES = 3; // consecutive network errors while polling before giving up

  // Errors where the file itself is the problem: "Try again" asks for a different file.
  const FILE_ERRORS = new Set([
    "unsupported_file_type", "empty_file", "file_too_large", "invalid_media", "media_too_long",
  ]);

  // Every error code the API can return, plus "network" for when the server cannot be reached.
  const ERROR_MESSAGES = {
    unsupported_file_type: (l) => `This file type isn't supported. Choose an audio or video file (${formatList(l)}).`,
    empty_file: () => "This file is empty. Choose a file that has audio in it.",
    file_too_large: (l) => `This file is larger than ${formatSizeLimit(l)}. Try a shorter or smaller file.`,
    invalid_media: () => "We couldn't find any sound in this file. It may be damaged, or a video without an audio track.",
    media_too_long: (l) => `This recording is longer than ${formatDurationLimit(l)}. Try a shorter clip.`,
    invalid_language: () => "That language isn't supported. Choose \"Detect automatically\" and try again.",
    invalid_request: () => "Something about the request wasn't right. Please choose the file again.",
    queue_unavailable: () => "The service is busy or temporarily unavailable. Please try again in a moment.",
    job_not_found: () => "We couldn't find this transcription. It may have expired.",
    job_not_ready: () => "The transcript isn't ready yet.",
    transcription_failed: () => "Something went wrong while transcribing this file.",
    transcription_timeout: () => "This file took too long to transcribe and was stopped. Try a shorter clip.",
    worker_restarted: () => "The transcription was interrupted because the server restarted. Please try again.",
    internal_error: () => "Something went wrong on our side. Please try again.",
    network: () => "We couldn't reach the server. Check your connection and try again.",
  };

  const STAGES = ["uploading", "queued", "processing", "done"];
  const STAGE_TEXT = {
    uploading: { label: "Uploading", hint: "Sending your file. Keep this page open." },
    queued: { label: "Queued", hint: "Waiting for the transcriber to pick up your file." },
    processing: { label: "Transcribing", hint: "Listening to your file. Longer recordings take longer." },
    done: { label: "Done", hint: "" },
  };

  const $ = (id) => document.getElementById(id);

  const state = {
    limits: null, // from /api/limits; null if it could not be loaded
    file: null, // the File chosen by the user
    transcriptText: "", // what "Copy text" copies: one segment per line, like the .txt download
    lastError: null,
    elapsedTimer: null,
  };

  class AppError extends Error {
    constructor(code, serverMessage) {
      super(code);
      this.code = code;
      this.serverMessage = serverMessage || "";
    }
  }

  // ---------- Formatting ----------

  function formatBytes(bytes) {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  }

  function formatSizeLimit(limits) {
    if (!limits) return "the size limit";
    const mb = limits.max_upload_mb;
    return mb % 1024 === 0 ? `${mb / 1024} GB` : `${mb} MB`;
  }

  function formatDurationLimit(limits) {
    if (!limits) return "the length limit";
    const minutes = Math.round(limits.max_duration_sec / 60);
    return minutes >= 120 && minutes % 60 === 0 ? `${minutes / 60} hours` : `${minutes} minutes`;
  }

  function formatList(limits) {
    return limits ? limits.allowed_extensions.join(", ") : "mp3, wav, mp4, …";
  }

  // 75.4 -> "1:15", 3725 -> "1:02:05"
  function formatClock(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = String(total % 60).padStart(2, "0");
    return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
  }

  function languageName(code) {
    // Whisper uses "jw" for Javanese; the standard code is "jv".
    const standard = code === "jw" ? "jv" : code;
    try {
      return new Intl.DisplayNames(["en"], { type: "language" }).of(standard) || code;
    } catch {
      return code;
    }
  }

  function messageFor(error) {
    const build = ERROR_MESSAGES[error.code];
    if (build) return build(state.limits);
    return error.serverMessage || ERROR_MESSAGES.internal_error();
  }

  // ---------- Views ----------

  function showView(name) {
    for (const view of ["select", "progress", "result", "error"]) {
      $(`view-${view}`).hidden = view !== name;
    }
    // Move focus to the new view's heading so keyboard and screen-reader users follow along.
    const heading = document.querySelector(`#view-${name} h2`);
    if (heading) heading.focus({ preventScroll: false });
  }

  // ---------- Limits and language list ----------

  async function loadLimits() {
    try {
      const response = await fetch("/api/limits");
      if (!response.ok) throw new Error(String(response.status));
      state.limits = await response.json();
    } catch {
      // The server still validates everything; the page just can't pre-check.
      $("limit-size").textContent = "unknown";
      $("limit-duration").textContent = "unknown";
      return;
    }
    const limits = state.limits;
    $("limit-formats").textContent = `Audio or video: ${limits.allowed_extensions.join(", ")}`;
    $("limit-size").textContent = formatSizeLimit(limits);
    $("limit-duration").textContent = formatDurationLimit(limits);
    $("file-input").accept = limits.allowed_extensions.map((ext) => `.${ext}`).join(",");
    fillLanguages(limits.languages);
  }

  function fillLanguages(codes) {
    const select = $("language");
    const options = codes
      .map((code) => ({ code, name: languageName(code) }))
      .sort((a, b) => a.name.localeCompare(b.name, "en"));
    for (const { code, name } of options) {
      select.append(new Option(name, code));
    }
  }

  // ---------- Choosing a file ----------

  function checkFile(file) {
    const limits = state.limits;
    if (!limits) return null; // limits unavailable: let the server decide
    const extension = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
    if (!limits.allowed_extensions.includes(extension)) return new AppError("unsupported_file_type");
    if (file.size === 0) return new AppError("empty_file");
    if (file.size > limits.max_upload_mb * 1024 * 1024) return new AppError("file_too_large");
    return null;
  }

  function selectFile(file) {
    if (!file) return;
    const problem = checkFile(file);
    if (problem) {
      showError(problem);
      return;
    }
    state.file = file;
    $("selected-name").textContent = file.name;
    $("selected-size").textContent = formatBytes(file.size);
    $("selected").hidden = false;
    $("start").focus();
  }

  function clearFile() {
    state.file = null;
    $("file-input").value = "";
    $("selected").hidden = true;
  }

  // ---------- Upload and polling ----------

  function uploadFile(file, language) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/jobs");
      xhr.responseType = "json";
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) setPercent((event.loaded / event.total) * 100);
      };
      xhr.onload = () => {
        if (xhr.status === 202 && xhr.response) resolve(xhr.response);
        else reject(errorFromResponse(xhr.status, xhr.response));
      };
      xhr.onerror = () => reject(new AppError("network"));
      const form = new FormData();
      form.append("file", file);
      if (language) form.append("language", language);
      xhr.send(form);
    });
  }

  function errorFromResponse(status, body) {
    if (body && body.error && body.error.code) return new AppError(body.error.code, body.error.message);
    if (status === 413) return new AppError("file_too_large"); // e.g. from a proxy, without our JSON
    if (status === 503) return new AppError("queue_unavailable");
    return new AppError("internal_error");
  }

  async function fetchJob(jobId) {
    let response;
    try {
      response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { cache: "no-store" });
    } catch {
      throw new AppError("network");
    }
    const body = await response.json().catch(() => null);
    if (!response.ok) throw errorFromResponse(response.status, body);
    return body;
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function waitForJob(jobId) {
    let failures = 0;
    for (;;) {
      let job;
      try {
        job = await fetchJob(jobId);
        failures = 0;
      } catch (error) {
        failures += 1;
        if (error.code !== "network" || failures >= MAX_POLL_FAILURES) throw error;
        await sleep(POLL_INTERVAL_MS);
        continue;
      }
      if (job.status === "done") return job;
      if (job.status === "failed") throw new AppError(job.error_code, job.error_message);
      setStage(job.status);
      if (job.status === "processing") setPercent(job.progress);
      await sleep(POLL_INTERVAL_MS);
    }
  }

  async function transcribe() {
    const file = state.file;
    if (!file) return;
    $("progress-title").textContent = file.name;
    delete $("stage-label").dataset.stage; // a previous run may have ended on any stage
    setStage("uploading");
    setPercent(0);
    showView("progress");
    try {
      const job = await uploadFile(file, $("language").value);
      const finished = await waitForJob(job.id);
      stopElapsed();
      showResult(finished);
    } catch (error) {
      stopElapsed();
      showError(error instanceof AppError ? error : new AppError("internal_error"));
    }
  }

  // ---------- Progress display ----------

  function setStage(stage) {
    const current = STAGES.indexOf(stage);
    document.querySelectorAll(".stages li").forEach((item, index) => {
      item.classList.toggle("is-done", index < current);
      item.classList.toggle("is-current", index === current);
      if (index === current) item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    });

    const label = $("stage-label");
    if (label.dataset.stage === stage) return; // already showing this stage
    label.dataset.stage = stage;
    label.textContent = STAGE_TEXT[stage].label;
    $("stage-hint").textContent = STAGE_TEXT[stage].hint;
    $("status-live").textContent = STAGE_TEXT[stage].label;

    const bar = $("meter-bar");
    const waiting = stage === "queued";
    bar.classList.toggle("is-indeterminate", waiting);
    $("percent").hidden = waiting;
    if (waiting) {
      bar.removeAttribute("aria-valuenow");
      bar.style.width = "";
    } else {
      setPercent(0);
    }

    if (stage === "processing") startElapsed();
    else $("elapsed").textContent = "";
  }

  function setPercent(value) {
    const percent = Math.max(0, Math.min(100, Math.round(value)));
    $("percent").textContent = `${percent}%`;
    const bar = $("meter-bar");
    bar.style.width = `${percent}%`;
    bar.setAttribute("aria-valuenow", String(percent));
  }

  function startElapsed() {
    stopElapsed();
    const startedAt = Date.now();
    const render = () => {
      $("elapsed").textContent = `${formatClock((Date.now() - startedAt) / 1000)} elapsed`;
    };
    render();
    state.elapsedTimer = setInterval(render, 1000);
  }

  function stopElapsed() {
    clearInterval(state.elapsedTimer);
    state.elapsedTimer = null;
  }

  // ---------- Result ----------

  function showResult(job) {
    const result = job.result;
    const segments = result ? result.segments : [];

    $("result-title").textContent = job.filename;
    const parts = [formatClock(result ? result.duration_sec : job.media_duration_sec)];
    if (result && result.language) parts.push(languageName(result.language));
    parts.push(`${segments.length} ${segments.length === 1 ? "segment" : "segments"}`);
    $("result-meta").textContent = parts.join(" · ");

    const list = $("transcript");
    list.replaceChildren(...segments.map(renderSegment));
    $("empty-transcript").hidden = segments.length > 0;
    $("copy").disabled = segments.length === 0;
    $("copy-status").textContent = "";

    const base = `/api/jobs/${encodeURIComponent(job.id)}`;
    $("download-txt").href = `${base}/transcript.txt`;
    $("download-srt").href = `${base}/transcript.srt`;

    state.transcriptText = segments.map((segment) => segment.text).join("\n");
    showView("result");
  }

  function renderSegment(segment) {
    const item = document.createElement("li");
    const time = document.createElement("time");
    time.dateTime = `PT${segment.start.toFixed(1)}S`;
    time.textContent = formatClock(segment.start);
    const text = document.createElement("p");
    text.dir = "auto"; // per segment, so Hebrew/Arabic lines read right-to-left
    text.textContent = segment.text;
    item.append(time, text);
    return item;
  }

  async function copyTranscript() {
    const status = $("copy-status");
    try {
      await writeClipboard(state.transcriptText);
      status.textContent = "Copied to clipboard.";
    } catch {
      status.textContent = "Couldn't copy automatically. Select the text and copy it instead.";
    }
  }

  async function writeClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch {
        // Permission denied (e.g. embedded browsers, iframes): fall through to the old method.
      }
    }
    // Fallback for plain-HTTP deployments (no Clipboard API) and denied permissions.
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.className = "visually-hidden";
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    if (!ok) throw new Error("copy failed");
  }

  // ---------- Errors ----------

  function showError(error) {
    state.lastError = error;
    $("error-message").textContent = messageFor(error);
    showView("error");
  }

  function retry() {
    // If the file was the problem, ask for another one; otherwise keep it ready to resend.
    if (!state.lastError || FILE_ERRORS.has(state.lastError.code)) clearFile();
    showView("select");
    (state.file ? $("start") : $("choose")).focus();
  }

  function startOver() {
    clearFile();
    showView("select");
    $("choose").focus();
  }

  // ---------- Wiring ----------

  function wireDropzone() {
    const zone = $("dropzone");
    // Counter, because dragenter/dragleave also fire when moving over child elements.
    let depth = 0;
    zone.addEventListener("dragenter", (event) => {
      event.preventDefault();
      depth += 1;
      zone.classList.add("is-dragging");
    });
    zone.addEventListener("dragover", (event) => event.preventDefault());
    zone.addEventListener("dragleave", () => {
      depth = Math.max(0, depth - 1);
      if (depth === 0) zone.classList.remove("is-dragging");
    });
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      depth = 0;
      zone.classList.remove("is-dragging");
      selectFile(event.dataTransfer.files[0]);
    });
    // A file dropped outside the zone would otherwise make the browser navigate away to it.
    window.addEventListener("dragover", (event) => event.preventDefault());
    window.addEventListener("drop", (event) => event.preventDefault());
  }

  function init() {
    $("choose").addEventListener("click", () => $("file-input").click());
    $("file-input").addEventListener("change", (event) => selectFile(event.target.files[0]));
    $("start").addEventListener("click", transcribe);
    $("copy").addEventListener("click", copyTranscript);
    $("another").addEventListener("click", startOver);
    $("retry").addEventListener("click", retry);
    wireDropzone();
    loadLimits();
  }

  init();
})();
