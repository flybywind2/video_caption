const DEFAULT_GLOBAL_STYLE = {
  font_family: "NanumGothic",
  font_size: 48,
  text_color: "#ffffff",
  outline_color: "#101010",
  alignment: "bottom-center",
  offset_x: 0,
  offset_y: 0,
};

const ALIGNMENT_OPTIONS = [
  { value: "top-left", label: "상단 왼쪽" },
  { value: "top-center", label: "상단 중앙" },
  { value: "top-right", label: "상단 오른쪽" },
  { value: "middle-left", label: "중앙 왼쪽" },
  { value: "middle-center", label: "정중앙" },
  { value: "middle-right", label: "중앙 오른쪽" },
  { value: "bottom-left", label: "하단 왼쪽" },
  { value: "bottom-center", label: "하단 중앙" },
  { value: "bottom-right", label: "하단 오른쪽" },
];

const state = {
  tasks: [],
  selectedTaskId: null,
  detail: null,
  previewVideoUrl: null,
  renderedDetailSignature: null,
  renderedTaskId: null,
  activeCueId: null,
  uploadPolicy: {
    thresholdBytes: 500 * 1024 * 1024,
    promptSeconds: 20 * 60,
    chunkSeconds: 10 * 60,
  },
};

const APP_BASE = new URL(window.location.href);
if (!APP_BASE.pathname.endsWith("/")) {
  APP_BASE.pathname = `${APP_BASE.pathname}/`;
}

const API_BASE = new URL("api/", APP_BASE);

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
const saveCuesButton = document.getElementById("save-cues-button");
const applyGlobalStyleButton = document.getElementById("apply-global-style-button");
const resetGlobalStyleButton = document.getElementById("reset-global-style-button");
const sourceLink = document.getElementById("source-link");
const renderedLink = document.getElementById("rendered-link");
const transcriptLink = document.getElementById("transcript-link");
const srtLink = document.getElementById("srt-link");
const globalFontSizeEl = document.getElementById("global-font-size");
const globalAlignmentEl = document.getElementById("global-alignment");
const globalTextColorEl = document.getElementById("global-text-color");
const globalOutlineColorEl = document.getElementById("global-outline-color");
const globalOffsetXEl = document.getElementById("global-offset-x");
const globalOffsetYEl = document.getElementById("global-offset-y");
const globalStylePanelEl = document.querySelector(".global-style-panel");

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
    blocked: "Blocked",
    processing: "Processing",
    rendering: "Rendering",
    completed: "Completed",
    failed: "Failed",
    deleting: "Deleting",
  };
  return labels[status] || status;
}

function formatCueTime(seconds) {
  const total = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = Math.floor(total % 60);
  const centiseconds = Math.floor((total - Math.floor(total)) * 100);
  if (hours > 0) {
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(centiseconds).padStart(2, "0")}`;
  }
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(centiseconds).padStart(2, "0")}`;
}

function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value >= 1024 ** 3) {
    return `${(value / 1024 ** 3).toFixed(1)}GB`;
  }
  return `${(value / 1024 ** 2).toFixed(0)}MB`;
}

function formatMinutes(seconds) {
  const totalMinutes = Math.max(1, Math.round((Number(seconds) || 0) / 60));
  return `${totalMinutes}분`;
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

function apiUrl(path) {
  return new URL(path.replace(/^\//, ""), API_BASE).toString();
}

function resolveAppUrl(path) {
  if (!path) {
    return null;
  }
  return new URL(path, APP_BASE).toString();
}

function resolveArtifactUrl(path, version = "") {
  const url = resolveAppUrl(path);
  if (!url) {
    return null;
  }
  if (!version) {
    return url;
  }
  const resolved = new URL(url);
  resolved.searchParams.set("v", version);
  return resolved.toString();
}

function clampNumber(value, min, max, fallback) {
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, Math.round(numeric)));
}

function normalizeColor(value, fallback) {
  const color = String(value || "").trim();
  return /^#[0-9a-fA-F]{6}$/.test(color) ? color.toLowerCase() : fallback;
}

function normalizeFontFamily(value, fallback = DEFAULT_GLOBAL_STYLE.font_family) {
  return fallback;
}

function alignmentAnchorPercent(alignment) {
  const map = {
    "top-left": [10, 12],
    "top-center": [50, 12],
    "top-right": [90, 12],
    "middle-left": [10, 50],
    "middle-center": [50, 50],
    "middle-right": [90, 50],
    "bottom-left": [10, 90],
    "bottom-center": [50, 90],
    "bottom-right": [90, 90],
  };
  return map[alignment] || map[DEFAULT_GLOBAL_STYLE.alignment];
}

function legacyOffset(value, anchorPercent, pixelsPerPercent, fallback) {
  if (value === undefined || value === null || value === "") {
    return fallback;
  }
  const clampedPercent = clampNumber(value, 0, 100, anchorPercent);
  return Math.round((clampedPercent - anchorPercent) * pixelsPerPercent);
}

function normalizeGlobalStyle(style = {}) {
  const alignment = ALIGNMENT_OPTIONS.some((option) => option.value === style.alignment)
    ? style.alignment
    : DEFAULT_GLOBAL_STYLE.alignment;
  const [anchorX, anchorY] = alignmentAnchorPercent(alignment);
  return {
    font_family: normalizeFontFamily(style.font_family),
    font_size: clampNumber(style.font_size, 18, 120, DEFAULT_GLOBAL_STYLE.font_size),
    text_color: normalizeColor(style.text_color, DEFAULT_GLOBAL_STYLE.text_color),
    outline_color: normalizeColor(style.outline_color, DEFAULT_GLOBAL_STYLE.outline_color),
    alignment,
    offset_x: clampNumber(
      style.offset_x,
      -1920,
      1920,
      legacyOffset(style.position_x, anchorX, 19.2, DEFAULT_GLOBAL_STYLE.offset_x)
    ),
    offset_y: clampNumber(
      style.offset_y,
      -1080,
      1080,
      legacyOffset(style.position_y, anchorY, 10.8, DEFAULT_GLOBAL_STYLE.offset_y)
    ),
  };
}

function mergeCueStyle(globalStyle, override = {}) {
  return {
    ...normalizeGlobalStyle(globalStyle),
    ...normalizeGlobalStyle({
      ...globalStyle,
      ...Object.fromEntries(
        Object.entries(override || {}).filter(([, value]) => value !== null && value !== undefined && value !== "")
      ),
    }),
  };
}

function compactStyleOverride(style, globalStyle) {
  const normalizedStyle = normalizeGlobalStyle(style);
  const normalizedGlobal = normalizeGlobalStyle(globalStyle);
  return Object.fromEntries(
    Object.entries(normalizedStyle).filter(([key, value]) => normalizedGlobal[key] !== value)
  );
}

function detailSignature(detail) {
  return JSON.stringify({
    global_style: normalizeGlobalStyle(detail?.global_style || {}),
    cues: (detail?.cues || []).map((cue) => ({
      id: cue.id,
      start: cue.start,
      end: cue.end,
      speaker: cue.speaker || "",
      text: cue.text,
      style: cue.style || {},
    })),
  });
}

function alignmentOptionsMarkup(selectedValue) {
  return ALIGNMENT_OPTIONS.map(
    (option) =>
      `<option value="${option.value}" ${option.value === selectedValue ? "selected" : ""}>${option.label}</option>`
  ).join("");
}

function setGlobalStyleForm(style) {
  const normalized = normalizeGlobalStyle(style);
  globalFontSizeEl.value = String(normalized.font_size);
  globalAlignmentEl.innerHTML = alignmentOptionsMarkup(normalized.alignment);
  globalTextColorEl.value = normalized.text_color;
  globalOutlineColorEl.value = normalized.outline_color;
  globalOffsetXEl.value = String(normalized.offset_x);
  globalOffsetYEl.value = String(normalized.offset_y);
}

function collectGlobalStyle() {
  return normalizeGlobalStyle({
    font_family: DEFAULT_GLOBAL_STYLE.font_family,
    font_size: globalFontSizeEl.value,
    alignment: globalAlignmentEl.value,
    text_color: globalTextColorEl.value,
    outline_color: globalOutlineColorEl.value,
    offset_x: globalOffsetXEl.value,
    offset_y: globalOffsetYEl.value,
  });
}

function cueStyleBadgeLabel(mode) {
  return mode === "custom" ? "개별 스타일 적용" : "전체 스타일 사용";
}

function setCueStyleInputs(row, style) {
  row.querySelector(".cue-font-size").value = String(style.font_size);
  row.querySelector(".cue-alignment").value = style.alignment;
  row.querySelector(".cue-text-color").value = style.text_color;
  row.querySelector(".cue-outline-color").value = style.outline_color;
  row.querySelector(".cue-offset-x").value = String(style.offset_x);
  row.querySelector(".cue-offset-y").value = String(style.offset_y);
}

function collectCueStyle(row) {
  return normalizeGlobalStyle({
    font_family: DEFAULT_GLOBAL_STYLE.font_family,
    font_size: row.querySelector(".cue-font-size").value,
    alignment: row.querySelector(".cue-alignment").value,
    text_color: row.querySelector(".cue-text-color").value,
    outline_color: row.querySelector(".cue-outline-color").value,
    offset_x: row.querySelector(".cue-offset-x").value,
    offset_y: row.querySelector(".cue-offset-y").value,
  });
}

function updateCueStyleMode(row, mode) {
  row.dataset.styleMode = mode;
  const badge = row.querySelector(".cue-style-mode");
  if (badge) {
    badge.textContent = cueStyleBadgeLabel(mode);
    badge.classList.toggle("custom", mode === "custom");
  }
}

function syncGlobalStyleRows() {
  const globalStyle = collectGlobalStyle();
  cueEditorEl.querySelectorAll(".cue-row").forEach((row) => {
    if (row.dataset.styleMode === "global") {
      setCueStyleInputs(row, globalStyle);
      updateCueStyleMode(row, "global");
    }
  });
}

function applyActiveCueState() {
  const rows = cueEditorEl.querySelectorAll(".cue-row");
  rows.forEach((row) => {
    row.classList.toggle("active", row.dataset.cueId === state.activeCueId);
  });
}

function findCueByTime(cues, currentTime) {
  return (cues || []).find((cue) => currentTime >= cue.start && currentTime <= cue.end + 0.05) || null;
}

function syncActiveCueFromPlayback() {
  if (!state.detail?.cues?.length) {
    return;
  }
  const cue = findCueByTime(state.detail.cues, previewVideoEl.currentTime);
  const nextCueId = cue?.id || null;
  if (nextCueId !== state.activeCueId) {
    state.activeCueId = nextCueId;
    applyActiveCueState();
  }
}

function seekPreview(seconds) {
  const targetTime = Math.max(0, Number(seconds) || 0);
  const applySeek = () => {
    previewVideoEl.currentTime = targetTime;
    syncActiveCueFromPlayback();
  };

  if (previewVideoEl.readyState >= 1) {
    applySeek();
    return;
  }

  previewVideoEl.addEventListener("loadedmetadata", applySeek, { once: true });
}

function renderTasks() {
  if (!state.tasks.length) {
    taskListEl.innerHTML = `<div class="detail-empty">아직 등록된 작업이 없습니다.</div>`;
    return;
  }

  taskListEl.innerHTML = state.tasks
    .map((task) => {
      const active = task.id === state.selectedTaskId ? "active" : "";
      const batchLabel = task.batch_total > 1 ? `분할 ${task.batch_index}/${task.batch_total}` : "";
      const subtext = [batchLabel, task.message || ""].filter(Boolean).join(" · ");
      return `
        <article class="task-card ${active}" data-task-id="${task.id}">
          <div class="task-card-header">
            <div>
              <h3>${escapeHtml(task.original_filename)}</h3>
              <p class="task-subtext">${escapeHtml(subtext)}</p>
            </div>
            <span class="${statusClass(task.status)}">${prettyStatus(task.status)}</span>
          </div>
          <div class="task-footer">
            <div class="progress-bar"><span style="width:${formatPercent(task.progress)}"></span></div>
            <p class="task-subtext">${formatPercent(task.progress)} · ${escapeHtml(task.language)}</p>
            ${task.error_message ? `<p class="task-subtext">${escapeHtml(task.error_message)}</p>` : ""}
            <div class="task-card-actions">
              <button class="ghost-button" data-action="select" data-task-id="${task.id}" type="button">열기</button>
              <button class="ghost-button" data-action="delete" data-task-id="${task.id}" type="button">삭제</button>
            </div>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderCueEditor(cues, globalStyle) {
  if (!cues.length) {
    cueEditorEl.innerHTML = `<div class="detail-empty">아직 편집 가능한 자막이 없습니다.</div>`;
    return;
  }

  cueEditorEl.innerHTML = cues
    .map((cue, index) => {
      const hasCustomStyle = Object.keys(cue.style || {}).length > 0;
      const effectiveStyle = mergeCueStyle(globalStyle, cue.style || {});
      return `
        <div
          class="cue-row ${cue.id === state.activeCueId ? "active" : ""}"
          data-index="${index}"
          data-cue-id="${escapeHtml(cue.id)}"
          data-start="${cue.start}"
          data-end="${cue.end}"
          data-style-mode="${hasCustomStyle ? "custom" : "global"}"
          tabindex="0"
        >
          <div class="cue-row-head">
            <button class="cue-seek-button" data-action="seek-cue" data-index="${index}" type="button">
              ${formatCueTime(cue.start)} → ${formatCueTime(cue.end)}
            </button>
            <div class="cue-row-meta">
              <span class="cue-index">Cue ${String(index + 1).padStart(2, "0")}</span>
              <span class="cue-style-mode ${hasCustomStyle ? "custom" : ""}">${cueStyleBadgeLabel(hasCustomStyle ? "custom" : "global")}</span>
            </div>
          </div>
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
          <details class="cue-style-panel">
            <summary>
              <span>세부 스타일</span>
              <span class="cue-style-summary">크기, 색상, 위치를 이 자막만 따로 조정합니다.</span>
            </summary>
            <div class="style-grid cue-style-grid">
              <label class="field">
                <span>크기</span>
                <input type="number" min="18" max="120" step="1" class="cue-style-input cue-font-size" value="${effectiveStyle.font_size}" />
              </label>
              <label class="field">
                <span>정렬</span>
                <select class="cue-style-input cue-alignment">
                  ${alignmentOptionsMarkup(effectiveStyle.alignment)}
                </select>
              </label>
              <label class="field">
                <span>글자색</span>
                <input type="color" class="cue-style-input cue-text-color" value="${effectiveStyle.text_color}" />
              </label>
              <label class="field">
                <span>외곽선색</span>
                <input type="color" class="cue-style-input cue-outline-color" value="${effectiveStyle.outline_color}" />
              </label>
              <label class="field">
                <span>X 미세조정 (px)</span>
                <input type="number" min="-960" max="960" step="1" class="cue-style-input cue-offset-x" value="${effectiveStyle.offset_x}" />
              </label>
              <label class="field">
                <span>Y 미세조정 (px)</span>
                <input type="number" min="-540" max="540" step="1" class="cue-style-input cue-offset-y" value="${effectiveStyle.offset_y}" />
              </label>
            </div>
            <div class="cue-style-actions">
              <button class="ghost-button reset-style-button" data-action="reset-cue-style" data-index="${index}" type="button">
                전체 스타일로 초기화
              </button>
            </div>
          </details>
          <div class="cue-row-actions">
            <button class="ghost-button cue-insert-button" data-action="insert-cue-after" data-index="${index}" type="button">
              다음 줄에 자막 추가
            </button>
          </div>
        </div>
      `;
    })
    .join("");
}

function syncDetailView() {
  if (!state.detail) {
    detailEmptyEl.classList.remove("hidden");
    detailViewEl.classList.add("hidden");
    state.previewVideoUrl = null;
    state.renderedDetailSignature = null;
    state.renderedTaskId = null;
    state.activeCueId = null;
    return;
  }

  detailEmptyEl.classList.add("hidden");
  detailViewEl.classList.remove("hidden");

  const task = state.detail;
  const cueList = task.cues || [];
  const globalStyle = normalizeGlobalStyle(task.global_style || {});
  detailTitleEl.textContent = task.original_filename;
  detailMetaEl.textContent = `${prettyStatus(task.status)} · ${formatPercent(task.progress)} · ${task.language}`;
  transcriptTextEl.textContent = task.transcript_text || "전사 결과가 준비되면 이곳에 표시됩니다.";

  const videoUrl = task.artifacts.rendered_video
    ? resolveArtifactUrl(task.artifacts.rendered_video, task.completed_at || "")
    : resolveAppUrl(task.artifacts.source_video);
  if (videoUrl && state.previewVideoUrl !== videoUrl) {
    previewVideoEl.src = videoUrl;
    state.previewVideoUrl = videoUrl;
  } else if (!videoUrl && state.previewVideoUrl) {
    previewVideoEl.removeAttribute("src");
    previewVideoEl.load();
    state.previewVideoUrl = null;
  }

  sourceLink.href = resolveAppUrl(task.artifacts.source_video) || "#";
  renderedLink.href =
    resolveArtifactUrl(
      task.artifacts.rendered_video || task.artifacts.source_video,
      task.artifacts.rendered_video ? task.completed_at || "" : ""
    ) || "#";
  transcriptLink.href = resolveAppUrl(task.artifacts.transcript_json) || "#";
  srtLink.href = resolveAppUrl(task.artifacts.srt) || "#";

  sourceLink.style.display = task.artifacts.source_video ? "inline" : "none";
  renderedLink.style.display = task.artifacts.rendered_video ? "inline" : "none";
  transcriptLink.style.display = task.artifacts.transcript_json ? "inline" : "none";
  srtLink.style.display = task.artifacts.srt ? "inline" : "none";

  retryButton.disabled = task.status !== "failed";
  const nextDetailSignature = detailSignature(task);
  const taskChanged = state.renderedTaskId !== task.id;
  const isEditingEditor = detailViewEl.contains(document.activeElement);
  if (taskChanged || (state.activeCueId && !cueList.some((cue) => cue.id === state.activeCueId))) {
    state.activeCueId = cueList[0]?.id || null;
  }
  if ((!isEditingEditor || taskChanged) && (taskChanged || state.renderedDetailSignature !== nextDetailSignature)) {
    setGlobalStyleForm(globalStyle);
    renderCueEditor(cueList, globalStyle);
    state.renderedDetailSignature = nextDetailSignature;
    state.renderedTaskId = task.id;
  }
  applyActiveCueState();
}

async function loadHealth() {
  try {
    const health = await requestJson(apiUrl("health"));
    queueSizeEl.textContent = String(health.queue_size);
    workerCountEl.textContent = String(health.worker_count);
    ffmpegStatusEl.textContent = health.ffmpeg_available ? "Ready" : "Missing";
    whisperStatusEl.textContent = health.whisper_configured ? "Ready" : "Config";
    state.uploadPolicy = {
      thresholdBytes: health.upload_split_threshold_bytes || state.uploadPolicy.thresholdBytes,
      promptSeconds: health.upload_split_prompt_seconds || state.uploadPolicy.promptSeconds,
      chunkSeconds: health.upload_split_chunk_seconds || state.uploadPolicy.chunkSeconds,
    };
  } catch (error) {
    ffmpegStatusEl.textContent = "Error";
    whisperStatusEl.textContent = "Error";
  }
}

function readLocalVideoDuration(file) {
  return new Promise((resolve) => {
    if (!file || !file.type.startsWith("video/")) {
      resolve(null);
      return;
    }

    const objectUrl = URL.createObjectURL(file);
    const probeVideo = document.createElement("video");
    const cleanup = () => {
      URL.revokeObjectURL(objectUrl);
      probeVideo.removeAttribute("src");
      probeVideo.load();
    };

    probeVideo.preload = "metadata";
    probeVideo.onloadedmetadata = () => {
      const duration = Number.isFinite(probeVideo.duration) ? probeVideo.duration : null;
      cleanup();
      resolve(duration);
    };
    probeVideo.onerror = () => {
      cleanup();
      resolve(null);
    };
    probeVideo.src = objectUrl;
  });
}

async function chooseUploadSplitMode(file) {
  const policy = state.uploadPolicy;
  const duration = await readLocalVideoDuration(file);
  const reasons = splitReasons(file, duration);

  if (!reasons.length) {
    return "single";
  }

  const estimatedParts =
    duration !== null ? Math.max(2, Math.ceil(duration / policy.chunkSeconds)) : null;
  const confirmed = window.confirm(
    [
      "큰 영상이라 분할 등록을 권장합니다.",
      `기준: ${reasons.join(", ")}`,
      `확인: ${formatMinutes(policy.chunkSeconds)} 단위로 분할 등록`,
      "취소: 단일 작업으로 그대로 등록",
      estimatedParts ? `예상 파트 수: 약 ${estimatedParts}개` : "",
    ]
      .filter(Boolean)
      .join("\n")
  );
  return confirmed ? "chunked" : "single";
}

function splitReasons(file, duration) {
  const policy = state.uploadPolicy;
  const reasons = [];

  if ((file?.size || 0) >= policy.thresholdBytes) {
    reasons.push(`파일 크기 ${formatBytes(file.size)}`);
  }
  if (duration !== null && duration >= policy.promptSeconds) {
    reasons.push(`영상 길이 ${formatMinutes(duration)}`);
  }
  return reasons;
}

async function chooseUploadSplitModes(files) {
  const uploads = [...files];
  if (!uploads.length) {
    return [];
  }

  if (uploads.length === 1) {
    return [await chooseUploadSplitMode(uploads[0])];
  }

  const durations = await Promise.all(uploads.map((file) => readLocalVideoDuration(file)));
  const largeUploads = uploads
    .map((file, index) => ({
      file,
      duration: durations[index],
      reasons: splitReasons(file, durations[index]),
    }))
    .filter((entry) => entry.reasons.length > 0);

  if (!largeUploads.length) {
    return uploads.map(() => "single");
  }

  const preview = largeUploads
    .slice(0, 3)
    .map((entry) => `- ${entry.file.name}: ${entry.reasons.join(", ")}`);
  if (largeUploads.length > 3) {
    preview.push(`- 외 ${largeUploads.length - 3}개 파일`);
  }

  const confirmed = window.confirm(
    [
      `${uploads.length}개 파일 중 ${largeUploads.length}개가 큰 영상입니다.`,
      "큰 파일만 자동 분할 등록할까요?",
      ...preview,
      `확인: 큰 파일은 ${formatMinutes(state.uploadPolicy.chunkSeconds)} 단위 분할`,
      "취소: 모든 파일을 단일 작업으로 등록",
    ].join("\n")
  );

  return uploads.map((file, index) =>
    confirmed && splitReasons(file, durations[index]).length > 0 ? "chunked" : "single"
  );
}

async function loadTasks({ preserveSelection = true } = {}) {
  const tasks = await requestJson(apiUrl("tasks"));
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
    const detail = await requestJson(apiUrl(`tasks/${taskId}`));
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
  const globalStyle = collectGlobalStyle();
  const rows = [...cueEditorEl.querySelectorAll(".cue-row")];
  return rows.map((row, index) => {
    const styleMode = row.dataset.styleMode || "global";
    const effectiveStyle = collectCueStyle(row);
    return {
      id: row.dataset.cueId || `cue-${String(index + 1).padStart(4, "0")}`,
      start: Number(row.querySelector(".cue-start").value),
      end: Number(row.querySelector(".cue-end").value),
      speaker: row.querySelector(".cue-speaker").value.trim() || null,
      text: row.querySelector(".cue-text").value.trim(),
      style: styleMode === "custom" ? compactStyleOverride(effectiveStyle, globalStyle) : {},
    };
  });
}

function buildInsertedCue(afterCue, index) {
  const start = afterCue ? Number(afterCue.end) + 0.2 : 0;
  return {
    id: `cue-${String(index + 1).padStart(4, "0")}`,
    start,
    end: start + 2.5,
    speaker: "",
    text: "",
    style: {},
  };
}

function addCueRow(afterIndex = null) {
  const cues = state.detail?.cues ? [...state.detail.cues] : [];
  const insertIndex =
    typeof afterIndex === "number" && afterIndex >= 0
      ? Math.min(afterIndex + 1, cues.length)
      : cues.length;
  const previousCue = cues[insertIndex - 1] || null;
  cues.splice(insertIndex, 0, buildInsertedCue(previousCue, insertIndex));
  const normalized = cues.map((cue, index) => ({
    ...cue,
    id: `cue-${String(index + 1).padStart(4, "0")}`,
    style: cue.style || {},
  }));
  state.detail.cues = normalized;
  state.activeCueId = normalized[insertIndex].id;
  renderCueEditor(normalized, collectGlobalStyle());
  state.renderedDetailSignature = detailSignature({
    ...state.detail,
    global_style: collectGlobalStyle(),
    cues: normalized,
  });
}

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const fileInput = uploadForm.querySelector('input[name="files"]');
  const files = [...(fileInput?.files || [])];
  if (!files.length) {
    setMessage(uploadMessageEl, "영상 파일을 하나 이상 선택해 주세요.", "error");
    return;
  }

  setMessage(uploadMessageEl, "파일 확인 중...", "");
  try {
    const splitModes = await chooseUploadSplitModes(files);
    const baseFormData = new FormData(uploadForm);
    const formData = new FormData();
    formData.set("language", String(baseFormData.get("language") || "ko"));
    formData.set("split_mode", splitModes[0] || "single");
    formData.set("split_mode_plan", JSON.stringify(splitModes));
    files.forEach((file) => formData.append("files", file));
    const chunkedCount = splitModes.filter((mode) => mode === "chunked").length;
    setMessage(
      uploadMessageEl,
      chunkedCount > 0
        ? `${files.length}개 파일 업로드 중, ${chunkedCount}개는 분할 등록 준비 중...`
        : `${files.length}개 파일 업로드 중...`,
      ""
    );
    const result = await requestJson(apiUrl("tasks"), {
      method: "POST",
      body: formData,
    });
    uploadForm.reset();
    setMessage(uploadMessageEl, result.message || "작업이 큐에 등록되었습니다.", "success");
    state.selectedTaskId = result.primary_task_id;
    await loadTasks({ preserveSelection: false });
    await loadTaskDetail(result.primary_task_id);
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
      const result = await requestJson(apiUrl(`tasks/${taskId}`), {
        method: "DELETE",
      });
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
    await requestJson(apiUrl(`tasks/${state.selectedTaskId}/retry`), {
      method: "POST",
    });
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

globalStylePanelEl.addEventListener("input", () => {
  syncGlobalStyleRows();
});

globalStylePanelEl.addEventListener("change", () => {
  syncGlobalStyleRows();
});

resetGlobalStyleButton.addEventListener("click", () => {
  setGlobalStyleForm(DEFAULT_GLOBAL_STYLE);
  if (!state.detail) {
    return;
  }
  state.detail.global_style = collectGlobalStyle();
  syncGlobalStyleRows();
  state.renderedDetailSignature = detailSignature(state.detail);
  setMessage(editorMessageEl, "전체 스타일 값을 기본값으로 되돌렸습니다. 저장하면 렌더에 반영됩니다.", "success");
});

applyGlobalStyleButton.addEventListener("click", () => {
  if (!state.detail) {
    return;
  }
  const globalStyle = collectGlobalStyle();
  state.detail.global_style = globalStyle;
  state.detail.cues = (state.detail.cues || []).map((cue) => ({
    ...cue,
    style: {},
  }));
  renderCueEditor(state.detail.cues, globalStyle);
  state.renderedDetailSignature = detailSignature(state.detail);
  applyActiveCueState();
  setMessage(editorMessageEl, "현재 전체 스타일을 모든 자막에 적용했습니다. 저장하면 렌더에 반영됩니다.", "success");
});

cueEditorEl.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action='remove-cue']");
  if (button && state.detail) {
    const index = Number(button.dataset.index);
    state.detail.cues.splice(index, 1);
    state.activeCueId = state.detail.cues[Math.max(0, index - 1)]?.id || state.detail.cues[0]?.id || null;
    renderCueEditor(state.detail.cues, collectGlobalStyle());
    state.renderedDetailSignature = detailSignature({
      ...state.detail,
      global_style: collectGlobalStyle(),
    });
    applyActiveCueState();
    return;
  }

  const insertButton = event.target.closest("button[data-action='insert-cue-after']");
  if (insertButton && state.detail) {
    addCueRow(Number(insertButton.dataset.index));
    return;
  }

  const resetStyleButton = event.target.closest("button[data-action='reset-cue-style']");
  if (resetStyleButton) {
    const row = resetStyleButton.closest(".cue-row");
    if (!row) {
      return;
    }
    setCueStyleInputs(row, collectGlobalStyle());
    updateCueStyleMode(row, "global");
    setMessage(editorMessageEl, "이 자막은 다시 전체 스타일을 따릅니다. 저장하면 반영됩니다.", "success");
    return;
  }

  if (!state.detail) {
    return;
  }

  const seekButton = event.target.closest("button[data-action='seek-cue']");
  const row = event.target.closest(".cue-row");
  const isFormControl = event.target.closest("input, textarea, select, summary");
  if (!row || (!seekButton && isFormControl)) {
    return;
  }

  const index = Number(row.dataset.index);
  const cue = state.detail.cues[index];
  if (!cue) {
    return;
  }

  state.activeCueId = cue.id;
  applyActiveCueState();
  seekPreview(cue.start);
});

function handleCueStyleInput(event) {
  const row = event.target.closest(".cue-row");
  if (!row) {
    return;
  }
  if (event.target.classList.contains("cue-style-input")) {
    updateCueStyleMode(row, "custom");
  }
}

cueEditorEl.addEventListener("input", handleCueStyleInput);
cueEditorEl.addEventListener("change", handleCueStyleInput);

cueEditorEl.addEventListener("keydown", (event) => {
  if ((event.key !== "Enter" && event.key !== " ") || !state.detail) {
    return;
  }

  const row = event.target.closest(".cue-row");
  if (!row || event.target.closest("input, textarea, button, select, summary")) {
    return;
  }

  event.preventDefault();
  const cue = state.detail.cues[Number(row.dataset.index)];
  if (!cue) {
    return;
  }

  state.activeCueId = cue.id;
  applyActiveCueState();
  seekPreview(cue.start);
});

saveCuesButton.addEventListener("click", async () => {
  if (!state.selectedTaskId) {
    return;
  }
  const globalStyle = collectGlobalStyle();
  const cues = collectCues().filter((cue) => cue.text);
  try {
    const detail = await requestJson(apiUrl(`tasks/${state.selectedTaskId}/captions`), {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        global_style: globalStyle,
        cues,
        rerender: true,
      }),
    });
    state.detail = detail;
    setMessage(editorMessageEl, "자막과 스타일을 저장하고 다시 렌더링했습니다.", "success");
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
  globalAlignmentEl.innerHTML = alignmentOptionsMarkup(DEFAULT_GLOBAL_STYLE.alignment);
  setGlobalStyleForm(DEFAULT_GLOBAL_STYLE);
  await refreshLoop();
  window.setInterval(refreshLoop, 5000);
});

previewVideoEl.addEventListener("timeupdate", syncActiveCueFromPlayback);
