const state = {
  tasks: [],
  selectedTaskId: null,
  detail: null,
};

const taskListEl = document.getElementById("task-list");
const uploadForm = document.getElementById("upload-form");
const uploadMessageEl = document.getElementById("upload-message");
const detailEmptyEl = document.getElementById("detail-empty");
const detailViewEl = document.getElementById("detail-view");
const detailTitleEl = document.getElementById("detail-title");
const detailMetaEl = document.getElementById("detail-meta");
const previewVideoEl = document.getElementById("preview-video");
const transcriptTextEl = document.getElementById("transcript-text");
const cueEditorEl = document.getElementById("cue-editor");
const editorMessageEl = document.getElementById("editor-message");
const queueSizeEl = document.getElementById("queue-size");
const workerCountEl = document.getElementById("worker-count");
const ffmpegStatusEl = document.getElementById("ffmpeg-status");
const whisperStatusEl = document.getElementById("whisper-status");
const retryButton = document.getElementById("retry-button");
const refreshDetailButton = document.getElementById("refresh-detail-button");
const addCueButton = document.getElementById("add-cue-button");
const saveCuesButton = document.getElementById("save-cues-button");
const sourceLink = document.getElementById("source-link");
const renderedLink = document.getElementById("rendered-link");
const transcriptLink = document.getElementById("transcript-link");
const srtLink = document.getElementById("srt-link");

function setMessage(element, message, tone = "") {
  element.textContent = message || "";
  element.className = `inline-message ${tone}`.trim();
}

function statusClass(status) {
  return `status-badge ${status || "queued"}`;
}

function formatPercent(progress) {
  return `${Math.round((progress || 0) * 100)}%`;
}

function prettyStatus(status) {
  const labels = {
    queued: "Queued",
    processing: "Processing",
    rendering: "Rendering",
    completed: "Completed",
    failed: "Failed",
    deleting: "Deleting",
  };
  return labels[status] || status;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch (error) {
      // ignore json parse failures
    }
    throw new Error(detail);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

function renderTasks() {
  if (!state.tasks.length) {
    taskListEl.innerHTML = `<div class="detail-empty">아직 등록된 작업이 없습니다.</div>`;
    return;
  }

  taskListEl.innerHTML = state.tasks
    .map((task) => {
      const active = task.id === state.selectedTaskId ? "active" : "";
      return `
        <article class="task-card ${active}" data-task-id="${task.id}">
          <div class="task-card-header">
            <div>
              <h3>${escapeHtml(task.original_filename)}</h3>
              <p class="task-subtext">${escapeHtml(task.message || "")}</p>
            </div>
            <span class="${statusClass(task.status)}">${prettyStatus(task.status)}</span>
          </div>
          <div class="task-footer">
            <div class="progress-bar"><span style="width:${formatPercent(task.progress)}"></span></div>
            <p class="task-subtext">${formatPercent(task.progress)} · ${escapeHtml(task.language)}</p>
            ${task.error_message ? `<p class="task-subtext">${escapeHtml(task.error_message)}</p>` : ""}
            <div class="detail-actions">
              <button class="ghost-button" data-action="select" data-task-id="${task.id}" type="button">열기</button>
              <button class="ghost-button" data-action="delete" data-task-id="${task.id}" type="button">삭제</button>
            </div>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderCueEditor(cues) {
  if (!cues.length) {
    cueEditorEl.innerHTML = `<div class="detail-empty">아직 편집 가능한 자막이 없습니다.</div>`;
    return;
  }

  cueEditorEl.innerHTML = cues
    .map(
      (cue, index) => `
        <div class="cue-row" data-index="${index}">
          <div class="cue-grid">
            <label class="field">
              <span>시작</span>
              <input type="number" step="0.01" class="cue-start" value="${cue.start}" />
            </label>
            <label class="field">
              <span>끝</span>
              <input type="number" step="0.01" class="cue-end" value="${cue.end}" />
            </label>
            <label class="field">
              <span>화자</span>
              <input type="text" class="cue-speaker" value="${escapeHtml(cue.speaker || "")}" />
            </label>
            <button class="ghost-button remove-button" data-action="remove-cue" data-index="${index}" type="button">삭제</button>
          </div>
          <label class="field">
            <span>텍스트</span>
            <textarea class="cue-text">${escapeHtml(cue.text)}</textarea>
          </label>
        </div>
      `
    )
    .join("");
}

function syncDetailView() {
  if (!state.detail) {
    detailEmptyEl.classList.remove("hidden");
    detailViewEl.classList.add("hidden");
    return;
  }

  detailEmptyEl.classList.add("hidden");
  detailViewEl.classList.remove("hidden");

  const task = state.detail;
  detailTitleEl.textContent = task.original_filename;
  detailMetaEl.textContent = `${prettyStatus(task.status)} · ${formatPercent(task.progress)} · ${task.language}`;
  transcriptTextEl.textContent = task.transcript_text || "전사 결과가 준비되면 이곳에 표시됩니다.";

  const videoUrl = task.artifacts.rendered_video || task.artifacts.source_video;
  if (videoUrl) {
    previewVideoEl.src = videoUrl;
  } else {
    previewVideoEl.removeAttribute("src");
  }

  sourceLink.href = task.artifacts.source_video || "#";
  renderedLink.href = task.artifacts.rendered_video || task.artifacts.source_video || "#";
  transcriptLink.href = task.artifacts.transcript_json || "#";
  srtLink.href = task.artifacts.srt || "#";

  sourceLink.style.display = task.artifacts.source_video ? "inline" : "none";
  renderedLink.style.display = task.artifacts.rendered_video ? "inline" : "none";
  transcriptLink.style.display = task.artifacts.transcript_json ? "inline" : "none";
  srtLink.style.display = task.artifacts.srt ? "inline" : "none";

  retryButton.disabled = task.status !== "failed";
  renderCueEditor(task.cues || []);
}

async function loadHealth() {
  try {
    const health = await requestJson("/api/health");
    queueSizeEl.textContent = String(health.queue_size);
    workerCountEl.textContent = String(health.worker_count);
    ffmpegStatusEl.textContent = health.ffmpeg_available ? "Ready" : "Missing";
    whisperStatusEl.textContent = health.whisper_configured ? "Ready" : "Config";
  } catch (error) {
    ffmpegStatusEl.textContent = "Error";
    whisperStatusEl.textContent = "Error";
  }
}

async function loadTasks({ preserveSelection = true } = {}) {
  const tasks = await requestJson("/api/tasks");
  state.tasks = tasks;

  if (preserveSelection && state.selectedTaskId) {
    const exists = tasks.some((task) => task.id === state.selectedTaskId);
    if (!exists) {
      state.selectedTaskId = null;
      state.detail = null;
    }
  }

  renderTasks();

  if (state.selectedTaskId) {
    await loadTaskDetail(state.selectedTaskId, { silent: true });
  }
}

async function loadTaskDetail(taskId, { silent = false } = {}) {
  try {
    const detail = await requestJson(`/api/tasks/${taskId}`);
    state.selectedTaskId = taskId;
    state.detail = detail;
    renderTasks();
    syncDetailView();
    if (!silent) {
      setMessage(editorMessageEl, "");
    }
  } catch (error) {
    if (!silent) {
      setMessage(editorMessageEl, error.message, "error");
    }
  }
}

function collectCues() {
  const rows = [...cueEditorEl.querySelectorAll(".cue-row")];
  return rows.map((row, index) => ({
    id: `cue-${String(index + 1).padStart(4, "0")}`,
    start: Number(row.querySelector(".cue-start").value),
    end: Number(row.querySelector(".cue-end").value),
    speaker: row.querySelector(".cue-speaker").value.trim() || null,
    text: row.querySelector(".cue-text").value.trim(),
  }));
}

function addCueRow() {
  const cues = state.detail?.cues ? [...state.detail.cues] : [];
  const lastCue = cues[cues.length - 1];
  const start = lastCue ? Number(lastCue.end) + 0.2 : 0;
  cues.push({
    id: `cue-${String(cues.length + 1).padStart(4, "0")}`,
    start,
    end: start + 2.5,
    speaker: "",
    text: "",
  });
  state.detail.cues = cues;
  renderCueEditor(cues);
}

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(uploadForm);
  setMessage(uploadMessageEl, "업로드 중...", "");
  try {
    const detail = await requestJson("/api/tasks", {
      method: "POST",
      body: formData,
    });
    uploadForm.reset();
    setMessage(uploadMessageEl, "작업이 큐에 등록되었습니다.", "success");
    state.selectedTaskId = detail.id;
    await loadTasks({ preserveSelection: false });
    await loadTaskDetail(detail.id);
  } catch (error) {
    setMessage(uploadMessageEl, error.message, "error");
  }
});

taskListEl.addEventListener("click", async (event) => {
  const target = event.target.closest("button, .task-card");
  if (!target) {
    return;
  }

  const action = target.dataset.action || "select";
  const taskId = target.dataset.taskId || target.closest(".task-card")?.dataset.taskId;
  if (!taskId) {
    return;
  }

  if (action === "delete") {
    const confirmed = window.confirm("이 작업과 생성된 파일을 삭제할까요?");
    if (!confirmed) {
      return;
    }
    try {
      const result = await requestJson(`/api/tasks/${taskId}`, { method: "DELETE" });
      if (state.selectedTaskId === taskId && !result.accepted) {
        state.selectedTaskId = null;
        state.detail = null;
        syncDetailView();
      }
      await loadTasks();
    } catch (error) {
      setMessage(uploadMessageEl, error.message, "error");
    }
    return;
  }

  await loadTaskDetail(taskId);
});

retryButton.addEventListener("click", async () => {
  if (!state.selectedTaskId) {
    return;
  }
  try {
    await requestJson(`/api/tasks/${state.selectedTaskId}/retry`, { method: "POST" });
    setMessage(editorMessageEl, "작업을 다시 큐에 넣었습니다.", "success");
    await loadTasks();
  } catch (error) {
    setMessage(editorMessageEl, error.message, "error");
  }
});

refreshDetailButton.addEventListener("click", async () => {
  if (!state.selectedTaskId) {
    return;
  }
  await loadTaskDetail(state.selectedTaskId);
});

addCueButton.addEventListener("click", () => {
  if (!state.detail) {
    return;
  }
  addCueRow();
});

cueEditorEl.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action='remove-cue']");
  if (!button || !state.detail) {
    return;
  }
  const index = Number(button.dataset.index);
  state.detail.cues.splice(index, 1);
  renderCueEditor(state.detail.cues);
});

saveCuesButton.addEventListener("click", async () => {
  if (!state.selectedTaskId) {
    return;
  }
  const cues = collectCues().filter((cue) => cue.text);
  try {
    const detail = await requestJson(`/api/tasks/${state.selectedTaskId}/captions`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        cues,
        rerender: true,
      }),
    });
    state.detail = detail;
    setMessage(editorMessageEl, "자막을 저장하고 다시 렌더링했습니다.", "success");
    syncDetailView();
    await loadTasks();
  } catch (error) {
    setMessage(editorMessageEl, error.message, "error");
  }
});

async function refreshLoop() {
  try {
    await Promise.all([loadHealth(), loadTasks()]);
  } catch (error) {
    setMessage(uploadMessageEl, error.message, "error");
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  await refreshLoop();
  window.setInterval(refreshLoop, 5000);
});
