"use strict";

const API = {
  settings: "/api/v5/settings",
  resetSettings: "/api/v5/settings/reset",
  connectors: "/api/v5/connectors",
  connectorPresets: "/api/v5/connectors/presets",
  connectorActivate: "/api/v5/connectors/preset/activate",
  modelProviders: "/api/v5/model-providers",
  azureStatus: "/api/v5/model-providers/azure-openai/env-status",
  ollamaDiscover: "/api/v5/model-providers/discover-ollama",
  agentChat: "/api/v5/agent/chat",
  agentWorkspaces: "/api/v5/agent/workspaces",
  observability: "/api/v5/observability",
  // RAG / Knowledge base
  uploadDocument: "/api/v5/ingestion/upload",
  listDocuments: "/api/v5/ingestion/documents",
  deleteDocument: (id) => `/api/v5/ingestion/documents/${id}`,
  ragQuery: "/api/v5/rag/query",
  // Supporting subsystems
  hooksRecords: "/api/v5/hooks/records",
  memoryStats: "/api/v5/memory/stats",
  memoryExport: "/api/v5/memory/export",
};

const ROUTES = ["chat", "runs", "connectors", "models", "observability", "settings", "documents"];

const state = {
  settings: {
    active_provider_id: "azure_openai",
    active_model: null,
    theme: "system",
    default_mode: "operator",
    telemetry_opt_in: false,
  },
  providers: [],
  connectors: [],
  presets: [],
  recentRuns: [],
  lastResponse: null,
  maxIterations: 12,
  route: "chat",
  // Document / RAG state
  uploadedDocs: [],
  ragMode: false,
  selectedDocIds: [],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

async function api(path, { method = "GET", body, query } = {}) {
  const url = new URL(path, window.location.origin);
  if (query) Object.entries(query).forEach(([k, v]) => v != null && url.searchParams.set(k, v));
  const init = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) init.body = typeof body === "string" ? body : JSON.stringify(body);
  const response = await fetch(url, init);
  const text = await response.text();
  let payload;
  try { payload = text ? JSON.parse(text) : null; } catch (_) { payload = text; }
  if (!response.ok) {
    const detail = (payload && (payload.detail || payload.message)) || response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload;
}

// ─── Multipart upload helper ────────────────────────────────────────────
async function apiUpload(file, onProgress) {
  const form = new FormData();
  form.append("file", file);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", API.uploadDocument);
    if (onProgress) {
      xhr.upload.addEventListener("progress", (evt) => {
        if (evt.lengthComputable) onProgress(Math.round((evt.loaded / evt.total) * 100));
      });
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)); }
        catch (_) { resolve(xhr.responseText); }
      } else {
        let msg = xhr.statusText;
        try { const parsed = JSON.parse(xhr.responseText); msg = parsed.detail || msg; } catch (_) {}
        reject(new Error(msg));
      }
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.send(form);
  });
}

// ─── Toasts ─────────────────────────────────────────────────────────────function toast(message, kind = "info") {
  const rail = $("#toast-rail");
  if (!rail) return;
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  rail.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity 200ms";
    setTimeout(() => el.remove(), 240);
  }, 3500);
}

// ─── Modal ──────────────────────────────────────────────────────────────
function openModal(title, content) {
  $("#modal-title").textContent = title;
  const body = $("#modal-body");
  body.innerHTML = "";
  if (typeof content === "string") body.innerHTML = content;
  else if (content instanceof Node) body.appendChild(content);
  $("#modal-root").hidden = false;
}
function closeModal() { $("#modal-root").hidden = true; }
$$("#modal-root [data-close]").forEach((node) => node.addEventListener("click", closeModal));
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeModal(); });

// ─── Theme handling ─────────────────────────────────────────────────────
function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "system") {
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "dark" : "light";
  } else {
    root.dataset.theme = theme;
  }
  const label = $("#theme-label");
  const icon = $("#theme-icon");
  if (label) label.textContent = theme.charAt(0).toUpperCase() + theme.slice(1);
  if (icon) icon.textContent = theme === "dark" ? "◑" : theme === "light" ? "◐" : "◓";
}
$("#theme-toggle").addEventListener("click", () => {
  const order = ["light", "dark", "system"];
  const next = order[(order.indexOf(state.settings.theme) + 1) % order.length];
  state.settings.theme = next;
  applyTheme(next);
  api(API.settings, { method: "POST", body: { theme: next } }).catch(() => {});
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (state.settings.theme === "system") applyTheme("system");
});

// ─── Router ─────────────────────────────────────────────────────────────
function currentRoute() {
  const hash = window.location.hash.replace(/^#\/?/, "").split("/")[0];
  return ROUTES.includes(hash) ? hash : "chat";
}

function syncSidebar(route) {
  $$(".nav-item").forEach((node) => {
    node.classList.toggle("is-active", node.dataset.route === route);
  });
  const labels = {
    chat: "Agent chat",
    runs: "Runs",
    connectors: "Connectors",
    models: "Models",
    observability: "Observability",
    settings: "Settings",
    documents: "Knowledge Base",
  };
  $("#crumb-section").textContent = labels[route] || route;
  $("#crumb-detail").textContent = route === "chat" ? "new run" : "overview";
}

function render() {
  const route = currentRoute();
  state.route = route;
  syncSidebar(route);
  const tpl = $(`#tpl-${route}`);
  const root = $("#page-root");
  root.innerHTML = "";
  if (!tpl) {
    root.textContent = "Unknown page.";
    return;
  }
  root.appendChild(tpl.content.cloneNode(true));
  const init = PAGE_INIT[route];
  if (typeof init === "function") init();
}
window.addEventListener("hashchange", render);

// ─── Page initializers ──────────────────────────────────────────────────
const PAGE_INIT = {
  chat: initChatPage,
  runs: initRunsPage,
  connectors: initConnectorsPage,
  models: initModelsPage,
  observability: initObservabilityPage,
  settings: initSettingsPage,
  documents: initDocumentsPage,
};

// ─── Helpers ────────────────────────────────────────────────────────────
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function eventBadge(kind) {
  const badges = {
    thinking: "Thinking",
    hooks: "Hooks",
    middleware: "Middleware",
    tool: "Tool call",
    tools: "Tool calls",
    model: "Model",
    planner: "Planner",
    error: "Error",
    info: "Info",
  };
  return badges[kind] || kind;
}

function pushTimeline(timelineEl, events) {
  if (!Array.isArray(events) || !events.length) return;
  const empty = timelineEl.querySelector(".timeline-empty");
  if (empty) empty.remove();
  for (const evt of events) {
    const node = document.createElement("div");
    node.className = "tl-event";
    node.dataset.kind = evt.kind || "info";
    const detail = evt.detail ? JSON.stringify(evt.detail, null, 2) : "";
    const subtitle = evt.kind === "tool" && evt.detail?.args ? renderToolSubtitle(evt) : "";
    node.innerHTML = `
      <div class="tl-head">
        <span class="tl-kind">${escapeHtml(eventBadge(evt.kind))}</span>
        <span class="tl-title">${escapeHtml(evt.title || "")}</span>
        <span class="tl-status">${escapeHtml(evt.status || "")}</span>
      </div>
      ${subtitle ? `<div class="tl-sub muted small">${subtitle}</div>` : ""}
      ${detail ? `<details class="tl-detail"><summary class="muted small">Details</summary><pre class="tl-body">${escapeHtml(detail)}</pre></details>` : ""}
    `;
    timelineEl.appendChild(node);
    timelineEl.scrollTop = timelineEl.scrollHeight;
  }
}

function renderToolSubtitle(evt) {
  const args = evt.detail.args || {};
  const obs = evt.detail.observation || {};
  const summary = [];
  if (args.path) summary.push(`<code>${escapeHtml(args.path)}</code>`);
  if (args.command) summary.push(`<code>$ ${escapeHtml(Array.isArray(args.command) ? args.command.join(" ") : args.command)}</code>`);
  if (typeof args.code === "string") summary.push(`<code>${escapeHtml(args.code.slice(0, 80))}${args.code.length > 80 ? "…" : ""}</code>`);
  if (args.query) summary.push(`<code>${escapeHtml(args.query)}</code>`);
  if (typeof obs?.exit_code !== "undefined") summary.push(`exit=${obs.exit_code}`);
  if (typeof obs?.bytes !== "undefined") summary.push(`${obs.bytes} bytes`);
  return summary.join(" · ");
}

function addMessage(messagesEl, role, content) {
  const node = document.createElement("div");
  node.className = `msg ${role}`;
  node.textContent = content;
  messagesEl.appendChild(node);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return node;
}

function renderArtifacts(panel, artifacts, workspaceId) {
  if (!panel) return;
  if (!Array.isArray(artifacts) || !artifacts.length) {
    panel.innerHTML = '<div class="muted small">No files yet. Files the agent writes will show up here.</div>';
    return;
  }
  panel.innerHTML = artifacts
    .map(
      (file) => `
        <div class="artifact">
          <div class="artifact-name"><code>${escapeHtml(file.path)}</code></div>
          <div class="artifact-meta muted small">${file.size} bytes · <a href="/preview/${escapeHtml(workspaceId)}/${escapeHtml(file.path)}" target="_blank" rel="noopener">open</a></div>
        </div>
      `,
    )
    .join("");
}

function renderPreview(panel, previewUrl) {
  if (!panel) return;
  if (!previewUrl) {
    panel.innerHTML = '<div class="muted small">When the agent serves a static asset (e.g. index.html) it will be embedded here.</div>';
    return;
  }
  panel.innerHTML = `
    <div class="preview-bar muted small">Preview: <a href="${escapeHtml(previewUrl)}" target="_blank" rel="noopener">${escapeHtml(previewUrl)}</a></div>
    <iframe class="preview-frame" src="${escapeHtml(previewUrl)}" sandbox="allow-scripts allow-same-origin"></iframe>
  `;
}

// ─── Chat page (Cursor-style autonomous run) ───────────────────────────
function initChatPage() {
  const messagesEl = $("#chat-messages");
  const emptyEl = $("#chat-empty");
  const timelineEl = $("#tab-timeline");
  const artifactsEl = $("#tab-artifacts");
  const previewEl = $("#tab-preview");
  const rawEl = $("#raw-json");
  const ragCtxEl = $("#rag-context-pane");
  const memoryEl = $("#memory-entries");
  const hooksEl = $("#hooks-entries");

  $$(".inspector-tabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$(".inspector-tabs .tab").forEach((t) => t.classList.remove("is-active"));
      btn.classList.add("is-active");
      const target = btn.dataset.tab;
      $$(".tab-pane").forEach((p) => p.classList.toggle("is-active", p.id === `tab-${target}`));
      // Lazy-load memory and hooks when those tabs are clicked
      if (target === "memory") refreshMemoryPane(memoryEl);
      if (target === "hooks") refreshHooksPane(hooksEl);
    });
  });

  // ── RAG toolbar setup ────────────────────────────────────────────────
  const ragToggle = $("#rag-toggle");
  const ragDocCount = $("#rag-doc-count");
  const ragSelector = $("#rag-doc-selector");
  const ragChips = $("#rag-doc-chips");
  const ragManageBtn = $("#rag-manage-btn");

  function updateRagDocCount() {
    const n = state.uploadedDocs.length;
    if (ragDocCount) ragDocCount.textContent = n === 1 ? "1 doc loaded" : `${n} docs loaded`;
  }

  async function refreshUploadedDocs() {
    try {
      state.uploadedDocs = await api(API.listDocuments);
    } catch (_) {
      state.uploadedDocs = [];
    }
    updateRagDocCount();
    renderRagChips();
  }

  function renderRagChips() {
    if (!ragChips) return;
    ragChips.innerHTML = state.uploadedDocs.map((doc) => {
      const active = state.selectedDocIds.includes(doc.document_id);
      return `<button type="button" class="rag-chip${active ? " is-active" : ""}" data-doc-id="${escapeHtml(doc.document_id)}">${escapeHtml(doc.filename)}</button>`;
    }).join("") || '<span class="muted small">No documents uploaded.</span>';
    $$(".rag-chip", ragChips).forEach((chip) => {
      chip.addEventListener("click", () => {
        const docId = chip.dataset.docId;
        if (state.selectedDocIds.includes(docId)) {
          state.selectedDocIds = state.selectedDocIds.filter((id) => id !== docId);
        } else {
          state.selectedDocIds.push(docId);
        }
        chip.classList.toggle("is-active", state.selectedDocIds.includes(docId));
      });
    });
  }

  if (ragToggle) {
    ragToggle.checked = state.ragMode;
    if (ragSelector) ragSelector.hidden = !state.ragMode;
    ragToggle.addEventListener("change", () => {
      state.ragMode = ragToggle.checked;
      if (ragSelector) ragSelector.hidden = !state.ragMode;
    });
  }
  if (ragManageBtn) {
    ragManageBtn.addEventListener("click", () => { window.location.hash = "#/documents"; });
  }
  refreshUploadedDocs();

  // ── Memory refresh button ────────────────────────────────────────────
  const memRefreshBtn = $("#memory-refresh-btn");
  if (memRefreshBtn) memRefreshBtn.addEventListener("click", () => refreshMemoryPane(memoryEl));

  // ── Hooks refresh button ─────────────────────────────────────────────
  const hooksRefreshBtn = $("#hooks-refresh-btn");
  if (hooksRefreshBtn) hooksRefreshBtn.addEventListener("click", () => refreshHooksPane(hooksEl));

  $("#prompt-provider").textContent = state.settings.active_provider_id || "default";

  // Hint cards just paste their text into the prompt
  $$(".hint-card").forEach((card) => {
    card.addEventListener("click", () => {
      const text = card.querySelector(".hint-title")?.textContent?.trim();
      if (text) {
        $("#prompt-input").value = text;
        $("#prompt-input").focus();
      }
    });
  });

  $("#prompt-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#prompt-form").requestSubmit();
    }
  });

  $("#prompt-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = $("#prompt-input").value.trim();
    if (!value) return;
    if (emptyEl) emptyEl.hidden = true;
    messagesEl.hidden = false;
    addMessage(messagesEl, "user", value);
    const thinking = addMessage(messagesEl, "system", "Thinking and selecting tools…");

    timelineEl.innerHTML = "";
    pushTimeline(timelineEl, [{ kind: "thinking", title: "Operator received query", status: "queued", detail: { query: value } }]);
    artifactsEl.innerHTML = '<div class="muted small">Working…</div>';
    previewEl.innerHTML = '<div class="muted small">Working…</div>';
    rawEl.textContent = "Running…";
    if (ragCtxEl) ragCtxEl.innerHTML = '<div class="muted small">Retrieving context…</div>';

    $("#prompt-input").value = "";
    $("#prompt-send").disabled = true;

    try {
      let response;
      if (state.ragMode && state.uploadedDocs.length > 0) {
        // ── RAG path ──────────────────────────────────────────────────
        response = await api(API.ragQuery, {
          method: "POST",
          body: {
            query: value,
            document_ids: state.selectedDocIds,
            top_k: 6,
          },
        });
        renderRagContext(ragCtxEl, response.citations || []);
        // Switch inspector to RAG Context tab automatically
        $$(".inspector-tabs .tab").forEach((t) => t.classList.toggle("is-active", t.dataset.tab === "rag"));
        $$(".tab-pane").forEach((p) => p.classList.toggle("is-active", p.id === "tab-rag"));
        pushTimeline(timelineEl, [
          { kind: "info", title: "RAG retrieval", status: "done", detail: { chunks_retrieved: response.chunks_retrieved } },
        ]);
        if (Array.isArray(response.timeline)) pushTimeline(timelineEl, response.timeline);
      } else {
        // ── Standard agent chat path ──────────────────────────────────
        response = await api(API.agentChat, {
          method: "POST",
          body: {
            query: value,
            provider_id: state.settings.active_provider_id || null,
            model: state.settings.active_model || null,
            max_iterations: state.maxIterations || null,
          },
        });
        if (ragCtxEl) ragCtxEl.innerHTML = '<div class="muted small">No RAG context for this run. Enable RAG mode to use uploaded documents.</div>';
        pushTimeline(timelineEl, response.timeline || []);
        renderArtifacts(artifactsEl, response.artifacts, response.workspace_id);
        renderPreview(previewEl, response.preview_url);
        if (response.preview_url) {
          $$(".inspector-tabs .tab").forEach((t) => t.classList.toggle("is-active", t.dataset.tab === "preview"));
          $$(".tab-pane").forEach((p) => p.classList.toggle("is-active", p.id === "tab-preview"));
        }
        state.recentRuns.unshift({
          run_id: response.run_id,
          workspace_id: response.workspace_id,
          query: value,
          ts: Date.now(),
          artifacts: response.artifacts || [],
          preview_url: response.preview_url || null,
        });
        state.recentRuns = state.recentRuns.slice(0, 25);
        $("#crumb-detail").textContent = response.run_id || "run";
      }

      thinking.remove();
      state.lastResponse = response;
      rawEl.textContent = JSON.stringify(response, null, 2);

      const answer = response.answer || response.result || "Run completed.";
      const assistantNode = addMessage(messagesEl, "assistant", answer);
      if (response.preview_url) {
        const link = document.createElement("a");
        link.href = response.preview_url;
        link.target = "_blank";
        link.rel = "noopener";
        link.className = "msg-preview-link";
        link.textContent = `↗ Open preview (${response.preview_url})`;
        assistantNode.appendChild(document.createElement("br"));
        assistantNode.appendChild(link);
      }
      // Append citation footnotes for RAG answers
      if (response.citations && response.citations.length > 0) {
        const footNote = document.createElement("div");
        footNote.className = "msg-citations muted small";
        footNote.innerHTML = response.citations
          .map((c) => `[${c.index}] ${escapeHtml(c.filename)} (score: ${c.score})`)
          .join(" · ");
        assistantNode.appendChild(footNote);
      }
    } catch (error) {
      thinking.remove();
      addMessage(messagesEl, "system", `Run failed: ${error.message}`);
      pushTimeline(timelineEl, [{ kind: "error", title: "Run failed", status: "error", detail: { error: error.message } }]);
      toast(error.message, "error");
    } finally {
      $("#prompt-send").disabled = false;
    }
  });
}

// ─── RAG / Memory / Hooks helpers ───────────────────────────────────────
function renderRagContext(pane, citations) {
  if (!pane) return;
  if (!citations || !citations.length) {
    pane.innerHTML = '<div class="muted small">No relevant chunks retrieved.</div>';
    return;
  }
  pane.innerHTML = citations.map((c) => `
    <div class="rag-citation">
      <div class="rag-citation-head">
        <span class="rag-citation-idx">[${c.index}]</span>
        <span class="rag-citation-file">${escapeHtml(c.filename)}</span>
        <span class="rag-score muted small">score ${c.score}</span>
      </div>
      <div class="rag-citation-text muted small">${escapeHtml(c.text_preview)}</div>
    </div>
  `).join("");
}

async function refreshMemoryPane(pane) {
  if (!pane) return;
  pane.innerHTML = '<div class="muted small">Loading…</div>';
  try {
    const data = await api(API.memoryStats);
    pane.innerHTML = `<pre class="raw-json small">${escapeHtml(JSON.stringify(data, null, 2))}</pre>`;
  } catch (err) {
    pane.innerHTML = `<div class="muted small">Could not load memory: ${escapeHtml(err.message)}</div>`;
  }
}

async function refreshHooksPane(pane) {
  if (!pane) return;
  pane.innerHTML = '<div class="muted small">Loading…</div>';
  try {
    const records = await api(API.hooksRecords);
    const items = Array.isArray(records) ? records : (records.records || []);
    if (!items.length) {
      pane.innerHTML = '<div class="muted small">No hook events recorded yet.</div>';
      return;
    }
    pane.innerHTML = items.slice(-50).reverse().map((evt) => `
      <div class="hooks-entry">
        <div class="hooks-entry-head">
          <span class="hooks-event-name">${escapeHtml(evt.event || evt.hook_name || "event")}</span>
          <span class="muted small">${escapeHtml(evt.fired_at || evt.timestamp || "")}</span>
        </div>
        ${evt.payload ? `<pre class="tl-body small">${escapeHtml(JSON.stringify(evt.payload, null, 2))}</pre>` : ""}
      </div>
    `).join("");
  } catch (err) {
    pane.innerHTML = `<div class="muted small">Could not load hooks: ${escapeHtml(err.message)}</div>`;
  }
}

// ─── Documents / Knowledge Base page ───────────────────────────────────
async function initDocumentsPage() {
  const docList = $("#doc-list");
  const uploadZone = $("#upload-zone");
  const fileInput = $("#file-input");
  const progressList = $("#upload-progress-list");
  const ragTestInput = $("#rag-test-input");
  const ragTestBtn = $("#rag-test-btn");
  const ragTestResult = $("#rag-test-result");

  async function loadDocuments() {
    if (!docList) return;
    docList.innerHTML = '<div class="muted small">Loading…</div>';
    try {
      state.uploadedDocs = await api(API.listDocuments);
    } catch (err) {
      docList.innerHTML = `<div class="muted small">Could not load documents: ${escapeHtml(err.message)}</div>`;
      return;
    }
    if (!state.uploadedDocs.length) {
      docList.innerHTML = '<div class="muted small">No documents yet. Upload some above.</div>';
      return;
    }
    docList.innerHTML = state.uploadedDocs.map((doc) => `
      <div class="doc-item" data-doc-id="${escapeHtml(doc.document_id)}">
        <div class="doc-icon">📄</div>
        <div class="doc-details">
          <div class="doc-name">${escapeHtml(doc.filename)}</div>
          <div class="muted small">
            ${doc.chunk_count} chunks · ${doc.page_count || 0} pages ·
            quality ${doc.average_quality ?? "—"} ·
            <span title="${escapeHtml(doc.uploaded_at || "")}">${formatRelTime(doc.uploaded_at)}</span>
          </div>
        </div>
        <div class="doc-actions">
          <button class="ghost small doc-delete-btn" type="button" data-doc-id="${escapeHtml(doc.document_id)}" title="Delete">✕</button>
        </div>
      </div>
    `).join("");
    $$(".doc-delete-btn", docList).forEach((btn) => {
      btn.addEventListener("click", async () => {
        const docId = btn.dataset.docId;
        const item = btn.closest(".doc-item");
        btn.disabled = true;
        try {
          await api(API.deleteDocument(docId), { method: "DELETE" });
          item?.remove();
          state.uploadedDocs = state.uploadedDocs.filter((d) => d.document_id !== docId);
          toast("Document deleted", "success");
          if (!state.uploadedDocs.length) {
            docList.innerHTML = '<div class="muted small">No documents yet. Upload some above.</div>';
          }
        } catch (err) {
          btn.disabled = false;
          toast(err.message, "error");
        }
      });
    });
  }

  async function uploadFiles(files) {
    for (const file of files) {
      const progressItem = document.createElement("div");
      progressItem.className = "upload-progress-item";
      progressItem.innerHTML = `
        <div class="upload-file-name">${escapeHtml(file.name)}</div>
        <div class="upload-bar-wrap"><div class="upload-bar" style="width:0%"></div></div>
        <span class="upload-status muted small">Uploading…</span>
      `;
      progressList.appendChild(progressItem);
      const bar = progressItem.querySelector(".upload-bar");
      const statusEl = progressItem.querySelector(".upload-status");
      try {
        await apiUpload(file, (pct) => { bar.style.width = `${pct}%`; });
        bar.style.width = "100%";
        bar.classList.add("done");
        statusEl.textContent = "Done";
        toast(`Uploaded ${file.name}`, "success");
        setTimeout(() => progressItem.remove(), 2500);
      } catch (err) {
        bar.classList.add("error");
        statusEl.textContent = `Failed: ${err.message}`;
        toast(`Upload failed: ${err.message}`, "error");
      }
    }
    await loadDocuments();
  }

  // Drag-and-drop
  if (uploadZone) {
    uploadZone.addEventListener("click", () => fileInput?.click());
    uploadZone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") fileInput?.click(); });
    uploadZone.addEventListener("dragover", (e) => { e.preventDefault(); uploadZone.classList.add("drag-over"); });
    uploadZone.addEventListener("dragleave", () => uploadZone.classList.remove("drag-over"));
    uploadZone.addEventListener("drop", (e) => {
      e.preventDefault();
      uploadZone.classList.remove("drag-over");
      const files = Array.from(e.dataTransfer.files);
      if (files.length) uploadFiles(files);
    });
  }
  if (fileInput) {
    fileInput.addEventListener("change", () => {
      const files = Array.from(fileInput.files || []);
      if (files.length) { uploadFiles(files); fileInput.value = ""; }
    });
  }

  // Refresh button
  const refreshBtn = $("#doc-refresh-btn");
  if (refreshBtn) refreshBtn.addEventListener("click", loadDocuments);

  // RAG test query
  if (ragTestBtn) {
    ragTestBtn.addEventListener("click", async () => {
      const q = ragTestInput?.value.trim();
      if (!q) { toast("Enter a question first", "error"); return; }
      if (!state.uploadedDocs.length) { toast("Upload documents first", "error"); return; }
      ragTestBtn.disabled = true;
      ragTestResult.hidden = true;
      try {
        const resp = await api(API.ragQuery, {
          method: "POST",
          body: { query: q, top_k: 5 },
        });
        ragTestResult.hidden = false;
        const answer = resp.answer || resp.result || "Query submitted.";
        const citationsHtml = (resp.citations || []).map((c) =>
          `<div class="rag-citation"><span class="rag-citation-idx">[${c.index}]</span> <strong>${escapeHtml(c.filename)}</strong> <span class="muted small">score ${c.score}</span><div class="muted small">${escapeHtml(c.text_preview)}</div></div>`
        ).join("");
        ragTestResult.innerHTML = `
          <div class="rag-answer"><strong>Answer:</strong> ${escapeHtml(answer)}</div>
          <div class="rag-citations-block">${citationsHtml}</div>
        `;
      } catch (err) {
        ragTestResult.hidden = false;
        ragTestResult.innerHTML = `<div class="muted small" style="color:var(--bad)">Error: ${escapeHtml(err.message)}</div>`;
      } finally {
        ragTestBtn.disabled = false;
      }
    });
  }
  if (ragTestInput) {
    ragTestInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); ragTestBtn?.click(); }
    });
  }

  await loadDocuments();
}

function formatRelTime(iso) {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return new Date(iso).toLocaleDateString();
}

// ─── Runs page ──────────────────────────────────────────────────────────
async function initRunsPage() {
  const list = $("#runs-list");
  if (!list) return;
  let serverRuns = [];
  try {
    const response = await api(API.agentWorkspaces);
    serverRuns = response.workspaces || [];
  } catch (_) { /* ignore */ }

  const merged = mergeRuns(state.recentRuns, serverRuns);
  if (!merged.length) {
    list.innerHTML = '<div class="muted">No runs yet. Start one from the Agent chat.</div>';
    return;
  }
  list.innerHTML = merged.map((run) => `
    <div class="recent-item" data-workspace="${escapeHtml(run.workspace_id)}">
      <strong>${escapeHtml(run.query || run.workspace_id)}</strong>
      <div class="muted small">${run.ts ? new Date(run.ts).toLocaleString() : (run.created_at || "")} · ${escapeHtml(run.workspace_id)}</div>
      ${run.preview_url ? `<a href="${escapeHtml(run.preview_url)}" target="_blank" rel="noopener" class="muted small">↗ preview</a>` : ""}
    </div>
  `).join("");
  $$("#runs-list .recent-item").forEach((node) => {
    node.addEventListener("click", async () => {
      const workspaceId = node.dataset.workspace;
      try {
        const snapshot = await api(`${API.agentWorkspaces}/${workspaceId}`);
        openModal(workspaceId, renderWorkspaceSnapshot(snapshot));
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });
}

function mergeRuns(local, server) {
  const seen = new Set();
  const out = [];
  for (const run of local) {
    if (run.workspace_id && !seen.has(run.workspace_id)) {
      seen.add(run.workspace_id);
      out.push(run);
    }
  }
  for (const run of server) {
    if (!seen.has(run.workspace_id)) {
      seen.add(run.workspace_id);
      out.push(run);
    }
  }
  return out;
}

function renderWorkspaceSnapshot(snapshot) {
  const container = document.createElement("div");
  container.innerHTML = `
    <div class="muted small">${escapeHtml(snapshot.path || "")}</div>
    <div class="mt"><strong>Files (${snapshot.file_count})</strong></div>
    <ul class="file-list">
      ${(snapshot.files || []).map((f) => `<li><code>${escapeHtml(f.path)}</code> <span class="muted small">${f.size}b</span> <a href="/preview/${escapeHtml(snapshot.workspace_id)}/${escapeHtml(f.path)}" target="_blank" rel="noopener">open</a></li>`).join("")}
    </ul>
  `;
  return container;
}

// ─── Connectors page ───────────────────────────────────────────────────
async function refreshConnectors() {
  try {
    const [connectors, presets] = await Promise.all([
      api(API.connectors),
      api(API.connectorPresets),
    ]);
    state.connectors = connectors;
    state.presets = presets;
    renderConnectors();
    renderPresets();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderConnectors() {
  const list = $("#connector-list");
  if (!list) return;
  if (!state.connectors.length) {
    list.innerHTML = '<div class="muted small">No connectors yet. Activate a preset or add a custom MCP server.</div>';
    return;
  }
  list.innerHTML = state.connectors
    .map((c) => `
      <div class="connector-item">
        <div class="connector-meta">
          <div class="connector-name">${escapeHtml(c.name)} <span class="muted small">· ${escapeHtml(c.category)}</span></div>
          <div class="connector-url">${escapeHtml(c.url || "—")}</div>
          ${c.auth?.env_var ? `<div class="muted small">Secret env: ${escapeHtml(c.auth.env_var)} ${c.secret_configured ? "✓" : "(missing)"}</div>` : ""}
        </div>
        <span class="status-pill ${c.status}">${escapeHtml(c.status === "connected" ? "Connected" : "Needs setup")}</span>
        <button class="ghost" data-remove="${escapeHtml(c.id)}">Remove</button>
      </div>
    `).join("");
  $$("#connector-list [data-remove]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api(`${API.connectors}/${btn.dataset.remove}`, { method: "DELETE" });
        toast("Connector removed", "success");
        refreshConnectors();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });
}

function renderPresets() {
  const grid = $("#connector-presets");
  if (!grid) return;
  grid.innerHTML = state.presets
    .map((p) => `
      <button class="preset-card" data-preset="${escapeHtml(p.id)}">
        <div class="preset-title">${escapeHtml(p.name)}</div>
        <div class="preset-cat">${escapeHtml(p.category)}</div>
        <div class="preset-desc">${escapeHtml(p.description)}</div>
        <div class="muted small">URL: ${escapeHtml(p.default_url)}</div>
        ${p.auth?.env_var ? `<div class="muted small">Env: ${escapeHtml(p.auth.env_var)}</div>` : ""}
      </button>
    `).join("");
  $$("#connector-presets [data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => openPresetModal(btn.dataset.preset));
  });
}

function openPresetModal(presetId) {
  const preset = state.presets.find((p) => p.id === presetId);
  if (!preset) return;
  const form = document.createElement("form");
  form.innerHTML = `
    <p class="muted">${escapeHtml(preset.description)}</p>
    <label>MCP server URL
      <input type="text" name="url" value="${escapeHtml(preset.default_url)}" required />
    </label>
    ${preset.auth?.env_var ? `<label>Auth env variable
      <input type="text" name="auth_env_var" value="${escapeHtml(preset.auth.env_var)}" />
      <span class="muted small">Set this env var on the server with your token.</span>
    </label>` : ""}
    <label class="toggle">
      <input type="checkbox" name="enabled" checked />
      <span class="toggle-track"><span class="toggle-thumb"></span></span>
      <span class="toggle-label">Enabled</span>
    </label>
    <div class="row gap" style="justify-content:flex-end">
      <button type="button" class="ghost" data-cancel>Cancel</button>
      <button type="submit">Activate</button>
    </div>
  `;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    try {
      await api(API.connectorActivate, {
        method: "POST",
        body: {
          preset_id: presetId,
          url: data.get("url"),
          auth_env_var: data.get("auth_env_var") || (preset.auth?.env_var || null),
          enabled: true,
        },
      });
      toast(`${preset.name} activated`, "success");
      closeModal();
      refreshConnectors();
    } catch (error) {
      toast(error.message, "error");
    }
  });
  form.querySelector("[data-cancel]").addEventListener("click", closeModal);
  openModal(`Activate ${preset.name}`, form);
}

function openCustomConnectorModal() {
  const form = document.createElement("form");
  form.innerHTML = `
    <label>Connector ID<input type="text" name="id" placeholder="my-mcp" required /></label>
    <label>Display name<input type="text" name="name" placeholder="My MCP server" /></label>
    <label>URL<input type="text" name="url" placeholder="https://my-mcp.example.com" required /></label>
    <label>Transport
      <select name="transport">
        <option value="streamable_http">streamable_http</option>
        <option value="sse">sse</option>
        <option value="stdio">stdio</option>
        <option value="local">local</option>
      </select>
    </label>
    <label>Description<textarea name="description" rows="2"></textarea></label>
    <label>Auth env variable (optional)<input type="text" name="auth_env_var" placeholder="MY_MCP_TOKEN" /></label>
    <label class="toggle">
      <input type="checkbox" name="enabled" checked />
      <span class="toggle-track"><span class="toggle-thumb"></span></span>
      <span class="toggle-label">Enabled</span>
    </label>
    <div class="row gap" style="justify-content:flex-end">
      <button type="button" class="ghost" data-cancel>Cancel</button>
      <button type="submit">Add connector</button>
    </div>
  `;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    try {
      await api(API.connectors, {
        method: "POST",
        body: {
          id: data.get("id"),
          name: data.get("name") || data.get("id"),
          url: data.get("url"),
          transport: data.get("transport"),
          description: data.get("description") || "",
          auth_type: data.get("auth_env_var") ? "bearer" : "none",
          auth_env_var: data.get("auth_env_var") || null,
          enabled: true,
        },
      });
      toast("Connector added", "success");
      closeModal();
      refreshConnectors();
    } catch (error) {
      toast(error.message, "error");
    }
  });
  form.querySelector("[data-cancel]").addEventListener("click", closeModal);
  openModal("Add custom MCP server", form);
}

function initConnectorsPage() {
  refreshConnectors();
  $("#add-custom-connector").addEventListener("click", openCustomConnectorModal);
}

// ─── Models page ────────────────────────────────────────────────────────
async function refreshProviders() {
  try {
    const list = await api(API.modelProviders);
    state.providers = list;
    populateProviderSelect();
    renderProviderList();
  } catch (error) {
    toast(error.message, "error");
  }
  try {
    const status = await api(API.azureStatus);
    const azureBox = $("#azure-status");
    if (azureBox) {
      azureBox.textContent = `Azure OpenAI env: endpoint ${status.endpoint_present ? "✓" : "✗"}, key ${status.api_key_present ? "✓" : "✗"}, deployment ${status.deployment_present ? "✓" : "✗"}`;
    }
  } catch (_) { /* azure status optional */ }
}

function populateProviderSelect() {
  const select = $("#model-provider");
  if (!select) return;
  select.innerHTML = state.providers
    .map((p) => `<option value="${escapeHtml(p.provider_id)}" ${p.provider_id === state.settings.active_provider_id ? "selected" : ""}>${escapeHtml(p.provider_id)} (${escapeHtml(p.kind)})</option>`)
    .join("");
  $("#model-name").value = state.settings.active_model || "";
}

function renderProviderList() {
  const grid = $("#provider-list");
  if (!grid) return;
  grid.innerHTML = state.providers
    .map((p) => `
      <div class="provider-card">
        <strong>${escapeHtml(p.provider_id)}</strong>
        <div class="muted">Kind: ${escapeHtml(p.kind)}</div>
        <div class="muted">Default model: ${escapeHtml(p.default_model || "—")}</div>
      </div>
    `).join("");
}

function initModelsPage() {
  refreshProviders();
  $("#save-model").addEventListener("click", async () => {
    const provider = $("#model-provider").value;
    const model = $("#model-name").value.trim() || null;
    try {
      const updated = await api(API.settings, { method: "POST", body: { active_provider_id: provider, active_model: model } });
      state.settings = updated;
      toast("Model preference saved", "success");
      const pp = $("#prompt-provider"); if (pp) pp.textContent = provider;
      updateEnvPill();
    } catch (error) {
      toast(error.message, "error");
    }
  });
  $("#discover-ollama").addEventListener("click", async () => {
    const baseUrl = $("#ollama-url").value;
    const result = $("#ollama-result");
    result.textContent = "Discovering…";
    try {
      const data = await api(API.ollamaDiscover, { method: "POST", body: { base_url: baseUrl } });
      if (!data.available) {
        result.innerHTML = `<span class="status-pill needs_setup">Unreachable</span> Could not contact ${escapeHtml(baseUrl)}.`;
      } else {
        result.innerHTML = `<span class="status-pill connected">Online</span> Found ${data.models.length} model(s): ${data.models.map(escapeHtml).join(", ") || "—"}.`;
      }
    } catch (error) {
      result.textContent = error.message;
    }
  });
}

// ─── Observability page ─────────────────────────────────────────────────
async function refreshObservability() {
  try {
    const data = await api(API.observability);
    const stats = $("#obs-stats");
    if (stats) {
      stats.innerHTML = `
        <div class="stat-item"><div class="stat-key">SLIs</div><div class="stat-val">${data.sli_count ?? 0}</div></div>
        <div class="stat-item"><div class="stat-key">SLOs</div><div class="stat-val">${data.slos?.length ?? 0}</div></div>
        <div class="stat-item"><div class="stat-key">Alerts</div><div class="stat-val">${data.alerts?.length ?? 0}</div></div>
        <div class="stat-item"><div class="stat-key">Runbooks</div><div class="stat-val">${data.runbooks?.length ?? 0}</div></div>
      `;
    }
    const alerts = $("#obs-alerts");
    if (alerts) {
      if (!data.alerts?.length) {
        alerts.innerHTML = '<div class="muted">No alerts.</div>';
      } else {
        alerts.innerHTML = data.alerts.map((a) => `
          <div class="alert-item">
            <strong>${escapeHtml(a.severity || "info")}</strong> · ${escapeHtml(a.message || a.runbook_id || "")}
            <div class="muted small">${escapeHtml(a.created_at || "")}</div>
          </div>
        `).join("");
      }
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

function initObservabilityPage() {
  refreshObservability();
  $("#obs-refresh").addEventListener("click", refreshObservability);
}

// ─── Settings page ──────────────────────────────────────────────────────
function initSettingsPage() {
  $$(".seg-item").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.themeValue === state.settings.theme);
    btn.addEventListener("click", async () => {
      $$(".seg-item").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      state.settings.theme = btn.dataset.themeValue;
      applyTheme(btn.dataset.themeValue);
      try {
        const updated = await api(API.settings, { method: "POST", body: { theme: btn.dataset.themeValue } });
        state.settings = updated;
        toast("Theme updated", "success");
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });

  $("#max-iter-input").value = state.maxIterations;
  $("#max-iter-input").addEventListener("change", (event) => {
    const value = Number(event.target.value);
    if (Number.isFinite(value) && value >= 3 && value <= 40) {
      state.maxIterations = value;
      toast(`Max iterations set to ${value}`, "info");
    }
  });

  const telem = $("#telemetry-toggle");
  telem.checked = !!state.settings.telemetry_opt_in;
  telem.addEventListener("change", async (event) => {
    try {
      const updated = await api(API.settings, { method: "POST", body: { telemetry_opt_in: event.target.checked } });
      state.settings = updated;
      toast(`Telemetry ${updated.telemetry_opt_in ? "enabled" : "disabled"}`, "info");
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#reset-settings").addEventListener("click", async () => {
    try {
      const updated = await api(API.resetSettings, { method: "POST" });
      state.settings = updated;
      applyTheme(updated.theme);
      toast("Settings reset", "success");
      render();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#copy-config").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(state.settings, null, 2));
      toast("Config copied", "success");
    } catch (_) {
      toast("Copy failed", "error");
    }
  });

  $("#settings-json").textContent = JSON.stringify(state.settings, null, 2);
}

// ─── Top-bar actions ───────────────────────────────────────────────────
function resetChat() {
  const empty = $("#chat-empty");
  if (empty) empty.hidden = false;
  const messages = $("#chat-messages");
  if (messages) { messages.hidden = true; messages.innerHTML = ""; }
  const tl = $("#tab-timeline");
  if (tl) tl.innerHTML = '<div class="timeline-empty muted">Tool calls, hooks and middleware events will appear here as the agent works.</div>';
  const art = $("#tab-artifacts");
  if (art) art.innerHTML = '<div class="muted small">No files yet. Files the agent writes will show up here.</div>';
  const prev = $("#tab-preview");
  if (prev) prev.innerHTML = '<div class="muted small">When the agent serves a static asset (e.g. index.html) it will be embedded here.</div>';
  const raw = $("#raw-json");
  if (raw) raw.textContent = "No run yet.";
  $("#crumb-detail").textContent = "new run";
}

$("#new-run-btn").addEventListener("click", () => {
  if (currentRoute() !== "chat") {
    window.location.hash = "#/chat";
    setTimeout(resetChat, 80);
  } else {
    resetChat();
  }
});

$("#share-btn").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(window.location.href);
    toast("Permalink copied", "success");
  } catch (_) {
    toast("Copy failed", "error");
  }
});

// ─── Bootstrapping ─────────────────────────────────────────────────────
function updateEnvPill() {
  const text = $("#env-text");
  if (!text) return;
  text.textContent = `${state.settings.active_provider_id || "default"}${state.settings.active_model ? ` · ${state.settings.active_model}` : ""}`;
}

async function boot() {
  try {
    state.settings = await api(API.settings);
  } catch (_) {
    // keep defaults
  }
  applyTheme(state.settings.theme || "system");
  updateEnvPill();
  if (!window.location.hash) window.location.hash = "#/chat";
  render();
}

boot();
