"use strict";
/* ClipForge: interface web (JavaScript puro, sem build). */

// ---------- utilidades ----------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const el = (tag, attrs = {}, ...children) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "dataset") Object.assign(e.dataset, v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
};

function fmt(t) {
  if (!isFinite(t)) return "--:--";
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const ss = s.toFixed(1).padStart(4, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${String(m).padStart(2, "0")}:${ss}`;
}
const round3 = (x) => Math.round(x * 1000) / 1000;

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, opts);
  } catch {
    throw new Error("Sem conexão com o ClipForge. O servidor está rodando?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : `Erro ${res.status}`);
  }
  return data;
}

function toast(msg, kind = "", action = null) {
  const t = el("div", { class: `toast ${kind}` }, msg);
  if (action) {
    t.append(" ", el("button", { class: "btn small ghost", onclick: () => { action.fn(); t.remove(); } }, action.label));
  }
  $("#toasts").append(t);
  setTimeout(() => t.remove(), action ? 8000 : kind === "error" ? 9000 : 4000);
}
const fail = (e) => toast(e.message || String(e), "error");

// Confirmação em dois cliques (sem janelas de diálogo): o 1º clique muda o texto do botão.
function confirmClick(btn, question, fn) {
  if (btn.dataset.armed) {
    delete btn.dataset.armed;
    btn.textContent = btn.dataset.label;
    fn();
    return;
  }
  btn.dataset.label = btn.textContent;
  btn.dataset.armed = "1";
  btn.textContent = question;
  setTimeout(() => {
    if (btn.dataset.armed) { delete btn.dataset.armed; btn.textContent = btn.dataset.label; }
  }, 4000);
}

function fmtBytes(n) {
  if (!n) return "0 MB";
  return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${(n / 1e6).toFixed(n >= 1e8 ? 0 : 1)} MB`;
}

const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem("cf." + k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("cf." + k, JSON.stringify(v)); } catch { /* sem storage */ } },
};

// ---------- estado global ----------
const S = {
  status: null,
  presets: {},
  cleanup: [],       // funções para desmontar a tela atual (timers, listeners)
};

// ---------- roteador ----------
function route() {
  S.cleanup.forEach((f) => f());
  S.cleanup = [];
  const m = location.hash.match(/^#\/p\/([a-z0-9-]+)/);
  if (m) projectView(m[1]);
  else projectsView();
}
window.addEventListener("hashchange", route);

async function boot() {
  try {
    [S.status, S.presets] = await Promise.all([api("GET", "/api/status"), api("GET", "/api/presets")]);
    const b = $("#api-badge");
    b.textContent = S.status.api_key ? `IA: ${S.status.claude_model}` : "IA desligada (sem chave)";
    b.className = "badge " + (S.status.api_key ? "ok" : "warn");
    if (S.status.problems.length) {
      const box = $("#problems");
      box.replaceChildren(el("strong", {}, "O ClipForge encontrou problemas no ambiente: "),
        ...S.status.problems.map((p) => el("div", {}, p)),
        el("div", { class: "small" }, "Rode “python -m app.doctor” no terminal para ver como corrigir."));
      box.classList.remove("hidden");
    }
  } catch (e) { fail(e); }
  setupQueue();
  setupQuit();
  route();
}

// ---------- fila global (topo) ----------
function setupQueue() {
  const btn = $("#queue-btn"), panel = $("#queue-panel");
  const kinds = { process: "Transcrição + IA", transcribe: "Transcrição", suggest: "Sugestões da IA", render: "Render" };
  const statusPt = { queued: "na fila", running: "rodando", done: "pronto", error: "erro", cancelled: "cancelado" };
  const refresh = async () => {
    let list;
    try { list = await api("GET", "/api/jobs"); } catch { return; }
    const active = list.filter((j) => j.status === "queued" || j.status === "running");
    btn.textContent = active.length ? `Fila (${active.length})` : "Fila";
    btn.classList.toggle("primary", active.length > 0);
    if (panel.classList.contains("hidden")) return;
    if (!list.length) { panel.replaceChildren(el("p", { class: "muted" }, "Nenhuma tarefa ainda.")); return; }
    panel.replaceChildren(...list.slice(0, 30).map((j) => {
      const running = j.status === "queued" || j.status === "running";
      return el("div", { class: "queue-item" },
        el("div", {},
          el("div", {}, el("strong", {}, kinds[j.kind] || j.kind), j.clip_id ? ` · corte ${j.clip_id}` : ""),
          el("a", { href: `#/p/${j.project_id}`, class: "small muted" }, j.project_name),
          el("div", { class: "small " + (j.status === "error" ? "status error" : "muted") },
            running ? `${j.stage} ${Math.round(j.progress * 100)}%` : statusPt[j.status] + (j.message ? `: ${j.message}` : ""))),
        running ? el("button", { class: "btn small ghost", onclick: async () => {
          try { await api("POST", `/api/jobs/${j.id}/cancel`); refresh(); } catch (e) { fail(e); }
        } }, "Cancelar") : el("span"),
        running ? el("div", { class: "progress" }, el("div", { class: "bar", style: `width:${j.progress * 100}%` })) : null);
    }));
  };
  btn.addEventListener("click", () => { panel.classList.toggle("hidden"); refresh(); });
  document.addEventListener("mousedown", (e) => {
    if (!panel.contains(e.target) && e.target !== btn) panel.classList.add("hidden");
  });
  refresh();
  setInterval(refresh, 2000);
}

// ---------- encerrar ----------
function setupQuit() {
  const btn = $("#quit-btn");
  btn.addEventListener("click", async () => {
    let active = 0;
    try { active = (await api("GET", "/api/jobs")).filter((j) => j.status === "queued" || j.status === "running").length; } catch { /* servidor já fora */ }
    const question = active ? `Cancelar ${active} tarefa(s) e encerrar?` : "Confirmar: encerrar?";
    confirmClick(btn, question, async () => {
      try {
        await api("POST", `/api/shutdown${active ? "?force=true" : ""}`);
        document.body.replaceChildren(el("main", {},
          el("h2", {}, "ClipForge encerrado."),
          el("p", { class: "muted" }, "Pode fechar esta aba. Para abrir de novo, use o atalho ClipForge no menu de aplicativos.")));
      } catch (e) { fail(e); }
    });
  });
}

// ======================================================================
// Tela: projetos
// ======================================================================
async function projectsView() {
  $("#topbar-title").textContent = "";
  const view = $("#view");
  view.replaceChildren($("#tpl-projects").content.cloneNode(true));

  const drop = $("#drop"), input = $("#file");
  input.addEventListener("change", () => input.files[0] && upload(input.files[0]));
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]));

  const list = $("#project-list");
  const render = async () => {
    if (list.querySelector("[data-armed]")) return;  // não redesenha no meio de uma confirmação
    try {
      const projects = await api("GET", "/api/projects");
      if (!projects.length) {
        list.replaceChildren(el("p", { class: "empty" }, "Nenhum projeto ainda. Envie um vídeo para começar."));
        return;
      }
      list.replaceChildren(...projects.map((p) => {
        const job = p.jobs[0];
        const state = job ? `${job.stage} ${Math.round(job.progress * 100)}%`
          : !p.has_transcript ? "Não transcrito" : `${p.clips} corte(s) · ${p.rendered} renderizado(s)`;
        const del = el("button", { class: "btn small ghost danger del", title: "Excluir projeto" }, "Excluir");
        del.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          confirmClick(del, "Excluir mesmo?", async () => {
            try {
              await api("DELETE", `/api/projects/${p.id}`);
              toast(`Projeto “${p.name}” excluído. O vídeo original no seu computador não foi apagado.`, "ok");
              render();
            } catch (err) { fail(err); }
          });
        });
        return el("a", { class: "project-card", href: `#/p/${p.id}` },
          del,
          el("div", { class: "name" }, p.name),
          el("div", { class: "muted small" }, `${fmt(p.info.duration)} · ${p.info.width}×${p.info.height}` +
            (p.generated_bytes ? ` · gerados: ${fmtBytes(p.generated_bytes)}` : "")),
          el("div", { class: "small" }, state));
      }));
    } catch (e) { fail(e); }
  };
  await render();
  const timer = setInterval(render, 3000);
  S.cleanup.push(() => clearInterval(timer));
}

function upload(file) {
  const box = $("#upload-progress"), bar = $(".bar", box), label = $(".label", box);
  box.classList.remove("hidden");
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/projects");
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = (e.loaded / e.total) * 100;
    bar.style.width = pct + "%";
    label.textContent = pct < 100 ? `Enviando ${pct.toFixed(0)}%` : "Verificando o vídeo...";
  };
  xhr.onerror = () => { box.classList.add("hidden"); toast("Falha no envio. O servidor está rodando?", "error"); };
  xhr.onload = async () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch { /* resposta vazia */ }
    if (xhr.status !== 201) {
      box.classList.add("hidden");
      toast(data.detail || `Erro ${xhr.status} no envio.`, "error");
      return;
    }
    try {
      if (!data.has_transcript) await api("POST", `/api/projects/${data.id}/process`);
    } catch (e) { fail(e); }
    location.hash = `#/p/${data.id}`;
  };
  const form = new FormData();
  form.append("file", file);
  xhr.send(form);
}

// ======================================================================
// Tela: projeto
// ======================================================================
function projectView(pid) {
  const P = {
    id: pid, project: null, transcript: null, clips: [], selected: null,
    jobs: {}, markIn: null, markOut: null, stopAt: null,
    edits: {}, savedEdits: {}, cues: null, cueStyle: null,
    saving: false, dirty: false, saveTimer: null, keySeq: 0,
  };
  const view = $("#view");
  view.replaceChildren($("#tpl-project").content.cloneNode(true));
  const video = $("#player");

  // ---------- carregamento ----------
  async function loadProject() {
    P.project = await api("GET", `/api/projects/${pid}`);
    $("#topbar-title").textContent = P.project.name;
    $("#disk-usage").textContent = `Vídeos e legendas gerados: ${fmtBytes(P.project.generated_bytes)}. Os cortes continuam na lista.`;
    if (!video.src) video.src = P.project.source_url;
    const vertical = P.project.info.height > P.project.info.width;
    $("#vertical-note").textContent = vertical
      ? "Este vídeo já é vertical: ele não é recortado, só ajustado à tela (fit)." : "";
    $('[data-act="transcribe"]').classList.toggle("hidden", P.project.has_transcript);
    $('[data-act="suggest"]').disabled = !S.status?.api_key || !P.project.has_transcript;
    $('[data-act="suggest"]').title = S.status?.api_key ? "" : "Defina ANTHROPIC_API_KEY no .env para usar a IA";
  }

  async function loadTranscript() {
    if (!P.project.has_transcript) {
      P.transcript = null;
      $("#transcript").replaceChildren(el("p", { class: "muted" }, "Ainda sem transcrição."));
      $("#caption-editor").replaceChildren(el("p", { class: "muted" }, "Ainda sem transcrição."));
      return;
    }
    P.transcript = await api("GET", `/api/projects/${pid}/transcript`);
    P.savedEdits = { ...P.transcript.edits };
    P.edits = { ...P.transcript.edits };
    P.starts = P.transcript.words.map((w) => w.start);
    renderTranscript();
    renderCaptionEditor();
    P.cues = null;
  }

  async function loadClips() {
    const clips = await api("GET", `/api/projects/${pid}/clips`);
    P.clips = clips.map((c) => ({ ...c, _k: ++P.keySeq }));
    if (P.selected && !P.clips.some((c) => c._k === P.selected)) P.selected = null;
    renderClips();
    renderExport();
  }

  // ---------- tarefas (polling) ----------
  async function pollJobs() {
    let list;
    try { list = await api("GET", `/api/projects/${pid}/jobs`); } catch { return; }
    let reloadAll = false, reloadClips = false;
    for (const j of list) {
      const prev = P.jobs[j.id];
      const finished = ["done", "error", "cancelled"].includes(j.status);
      if (prev && !["done", "error", "cancelled"].includes(prev.status) && finished) {
        if (j.kind === "render") {
          reloadClips = true;
          if (j.status === "error") toast(`Render do corte ${j.clip_id}: ${j.message}`, "error");
          if (j.status === "done") toast(`Corte ${j.clip_id} renderizado.`, "ok");
        } else {
          reloadAll = true;
          if (j.status === "error") showNotice(j.message, true);
          else if (j.message) showNotice(j.message);
          else if (j.status === "done" && j.result?.suggested !== undefined) toast(`${j.result.suggested} corte(s) sugerido(s) pela IA.`, "ok");
        }
      }
      P.jobs[j.id] = j;
    }
    renderBanner();
    renderExportJobs();
    if (reloadAll) {
      await loadProject();
      await loadTranscript();
      await loadClips();
    } else if (reloadClips) {
      await refreshClipStatus();
    }
  }

  async function refreshClipStatus() {
    // Atualiza só status/arquivos (não mexe no que o usuário está editando).
    const fresh = await api("GET", `/api/projects/${pid}/clips`);
    const byId = Object.fromEntries(fresh.map((c) => [c.id, c]));
    for (const c of P.clips) {
      const f = byId[c.id];
      if (f) Object.assign(c, { status: f.status, files: f.files, style: f.style, vertical_mode: f.vertical_mode, output_path: f.output_path });
    }
    renderExport();
    renderClips();
  }

  const activeJobs = () => Object.values(P.jobs).filter((j) => j.status === "queued" || j.status === "running");

  function renderBanner() {
    const job = activeJobs().find((j) => j.kind !== "render");
    const banner = $("#job-banner");
    banner.classList.toggle("hidden", !job);
    if (!job) return;
    $(".stage", banner).textContent = job.stage;
    $(".pct", banner).textContent = `${Math.round(job.progress * 100)}%`;
    $(".bar", banner).style.width = `${job.progress * 100}%`;
    banner.dataset.job = job.id;
  }

  function showNotice(msg, error = false) {
    const n = $("#notice");
    n.textContent = msg;
    n.classList.toggle("error", error);
    n.classList.remove("hidden");
  }

  // ---------- transcrição (aba Cortes) ----------
  function wordSpans(editable) {
    const frag = document.createDocumentFragment();
    for (const s of P.transcript.segments) {
      const p = el("p", {}, el("span", { class: "ts" }, fmt(s.start)));
      for (let i = s.first; i <= s.last; i++) {
        const w = P.transcript.words[i];
        const span = el("span", { class: "w", dataset: { i } });
        if ((w.prob ?? 1) < 0.5) span.classList.add("low");
        if (editable) decorateEdited(span, i);
        else span.textContent = displayWord(i);
        p.append(span, " ");
      }
      frag.append(p);
    }
    return frag;
  }

  const displayWord = (i) => {
    const e = P.edits[i];
    return e === undefined || e === "" ? P.transcript.words[i].word : e;
  };

  function renderTranscript() {
    const box = $("#transcript");
    box.replaceChildren(wordSpans(false));
    markClipWords();
  }

  function markClipWords() {
    if (!P.transcript) return;
    const c = P.clips.find((x) => x._k === P.selected);
    for (const span of $$("#transcript .w")) {
      const w = P.transcript.words[span.dataset.i];
      span.classList.toggle("in-clip", !!c && w.start >= c.start - 0.01 && w.end <= c.end + 0.01);
    }
  }

  function wordAt(t) {
    // busca binária: última palavra que começa antes de t
    const a = P.starts;
    if (!a || !a.length) return -1;
    let lo = 0, hi = a.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (a[mid] <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (ans >= 0 && P.transcript.words[ans].end + 0.15 < t) return -1;
    return ans;
  }

  let lastCurrent = null;
  function highlightCurrent() {
    const i = P.transcript ? wordAt(video.currentTime) : -1;
    const span = i >= 0 ? $(`#transcript .w[data-i="${i}"]`) : null;
    if (span === lastCurrent) return;
    lastCurrent?.classList.remove("current");
    span?.classList.add("current");
    lastCurrent = span;
    if (span && !video.paused) {
      const box = $("#transcript"), r = span.getBoundingClientRect(), b = box.getBoundingClientRect();
      if (r.top < b.top || r.bottom > b.bottom) box.scrollTop += r.top - b.top - b.height / 3;
    }
  }

  // seleção de trecho na transcrição -> criar corte
  function onSelection(e) {
    const btn = $("#sel-btn");
    const sel = window.getSelection();
    const box = $("#transcript");
    if (!sel || sel.isCollapsed || !box.contains(sel.anchorNode) || !box.contains(sel.focusNode)) {
      btn.classList.add("hidden");
      return;
    }
    const idx = (node) => {
      const n = node.nodeType === 3 ? node.parentElement : node;
      const w = n.closest?.(".w") || n.nextElementSibling?.closest?.(".w") || n.querySelector?.(".w");
      return w ? Number(w.dataset.i) : null;
    };
    const a = idx(sel.anchorNode), b = idx(sel.focusNode);
    if (a === null || b === null) return;
    const [i, j] = a <= b ? [a, b] : [b, a];
    const words = P.transcript.words;
    btn.dataset.start = words[i].start;
    btn.dataset.end = words[j].end;
    btn.textContent = `Criar corte ${fmt(words[i].start)} → ${fmt(words[j].end)}`;
    btn.style.left = `${Math.min(e.clientX + 8, window.innerWidth - 260)}px`;
    btn.style.top = `${e.clientY + 12}px`;
    btn.classList.remove("hidden");
  }

  // ---------- cortes ----------
  function clipDurClass(c) {
    const d = c.end - c.start;
    return d < S.status.clip_min_seconds || d > S.status.clip_max_seconds ? "dur bad" : "dur";
  }

  function renderClips() {
    const list = $("#clip-list");
    if (!P.clips.length) {
      list.replaceChildren(el("p", { class: "muted" },
        P.project?.has_transcript
          ? "Nenhum corte. Use “Sugerir cortes com IA”, marque início/fim no player ou selecione um trecho da transcrição."
          : "Os cortes aparecem aqui depois da transcrição."));
    } else {
      list.replaceChildren(...P.clips.map(clipCard));
    }
    renderTimeline();
    markClipWords();
  }

  function clipCard(c, idx) {
    const scoreCls = c.score >= 75 ? "high" : c.score >= 50 ? "mid" : "low";
    const timeField = (label, key) => el("div", { class: "time-field" },
      el("span", {}, label),
      el("button", { class: "btn small icon", title: "-0,1 s", onclick: () => nudge(c, key, -0.1) }, "−"),
      el("input", { type: "number", step: "0.1", min: "0", value: c[key].toFixed(2),
        onchange: (e) => setTime(c, key, parseFloat(e.target.value)) }),
      el("button", { class: "btn small icon", title: "+0,1 s", onclick: () => nudge(c, key, 0.1) }, "+"),
      el("span", {}, fmt(c[key])));
    const statusText = { done: `✓ renderizado${c.vertical_mode ? " · " + c.vertical_mode : ""}${c.style ? " · " + c.style : ""}`,
      error: "erro no render", rendering: "renderizando…", pending: "" }[c.status] || "";

    return el("div", { class: "clip" + (c._k === P.selected ? " selected" : ""), onclick: () => select(c) },
      el("div", { class: "clip-head" },
        c.id?.startsWith("c") ? el("span", { class: `score ${scoreCls}`, title: "Nota da IA (0 a 100)" }, c.score) : null,
        el("input", { class: "title", value: c.title, placeholder: "Título do corte",
          oninput: (e) => { c.title = e.target.value; scheduleSave(); } }),
        el("span", { class: "status " + (c.status || "") }, statusText)),
      c.reason ? el("div", { class: "reason" }, c.reason) : null,
      el("div", { class: "clip-times" },
        timeField("Início", "start"), timeField("Fim", "end"),
        el("span", { class: clipDurClass(c), title: `Recomendado: ${S.status.clip_min_seconds} a ${S.status.clip_max_seconds} s` },
          `${(c.end - c.start).toFixed(1)} s`)),
      el("div", { class: "clip-actions" },
        el("button", { class: "btn small", onclick: (e) => { e.stopPropagation(); playClip(c); } }, "▶ Tocar"),
        el("button", { class: "btn small", title: "Ajusta início/fim para não cortar palavras", onclick: (e) => { e.stopPropagation(); snapClip(c); } }, "Encaixar nas palavras"),
        el("button", { class: "btn small ghost", onclick: (e) => { e.stopPropagation(); duplicate(idx); } }, "Duplicar"),
        el("button", { class: "btn small ghost icon", title: "Subir", disabled: idx === 0, onclick: (e) => { e.stopPropagation(); move(idx, -1); } }, "↑"),
        el("button", { class: "btn small ghost icon", title: "Descer", disabled: idx === P.clips.length - 1, onclick: (e) => { e.stopPropagation(); move(idx, 1); } }, "↓"),
        el("button", { class: "btn small ghost danger", onclick: (e) => { e.stopPropagation(); remove(idx); } }, "Excluir")));
  }

  function select(c, seek = true) {
    if (P.selected === c._k) return;
    P.selected = c._k;
    $$("#clip-list .clip").forEach((n, i) => n.classList.toggle("selected", P.clips[i]._k === c._k));
    renderTimeline();
    markClipWords();
    if (seek) video.currentTime = c.start;
  }

  function setTime(c, key, v) {
    if (!isFinite(v)) { renderClips(); return; }
    const dur = P.project.info.duration;
    v = Math.max(0, Math.min(dur, round3(v)));
    if (key === "start" && v >= c.end) v = Math.max(0, c.end - 0.1);
    if (key === "end" && v <= c.start) v = Math.min(dur, c.start + 0.1);
    c[key] = v;
    video.currentTime = v;
    changed(c);
  }
  const nudge = (c, key, d) => setTime(c, key, c[key] + d);

  async function snapClip(c) {
    try {
      const r = await api("POST", `/api/projects/${pid}/snap`, { start: c.start, end: c.end });
      c.start = r.start; c.end = r.end;
      changed(c);
    } catch (e) { fail(e); }
  }

  function changed(c) {
    if (c) c.status = c.status === "done" ? "pending" : c.status;  // o servidor confirma ao salvar
    renderClips();
    renderExport();
    scheduleSave(0);
  }

  function duplicate(idx) {
    const c = P.clips[idx];
    const copy = { ...c, id: null, _k: ++P.keySeq, title: c.title + " (cópia)", status: "pending", files: {}, score: c.score };
    P.clips.splice(idx + 1, 0, copy);
    P.selected = copy._k;
    changed();
  }

  function move(idx, d) {
    const [c] = P.clips.splice(idx, 1);
    P.clips.splice(idx + d, 0, c);
    changed();
  }

  function remove(idx) {
    const [c] = P.clips.splice(idx, 1);
    changed();
    toast(`Corte “${c.title}” excluído.`, "", { label: "Desfazer", fn: () => { P.clips.splice(idx, 0, c); changed(); } });
  }

  async function addClip(start, end, title) {
    if (!P.project.has_transcript) { toast("Espere a transcrição terminar.", "error"); return; }
    try {
      const r = await api("POST", `/api/projects/${pid}/snap`, { start, end });
      const c = { id: null, _k: ++P.keySeq, start: r.start, end: r.end, title, hook: "", reason: "", score: 0, status: "pending", files: {} };
      P.clips.unshift(c);
      P.selected = c._k;
      changed();
      toast("Corte criado.", "ok");
    } catch (e) { fail(e); }
  }

  function playClip(c) {
    select(c, false);
    video.currentTime = c.start;
    P.stopAt = c.end;
    video.play();
  }

  // salvar (PUT da lista inteira, com espera curta para juntar edições)
  function scheduleSave(delay = 700) {
    P.dirty = true;
    $("#save-state").textContent = "Alterações não salvas…";
    clearTimeout(P.saveTimer);
    P.saveTimer = setTimeout(save, delay);
  }

  async function save() {
    if (P.saving) { P.saveTimer = setTimeout(save, 300); return; }
    if (!P.dirty) return;
    P.saving = true;
    P.dirty = false;
    const sent = P.clips.slice();
    try {
      const body = sent.map((c) => ({ id: c.id, start: c.start, end: c.end, title: c.title, hook: c.hook || "", reason: c.reason || "", score: c.score || 0 }));
      const saved = await api("PUT", `/api/projects/${pid}/clips`, body);
      saved.forEach((s, i) => {
        const c = sent[i];
        c.id = s.id;
        Object.assign(c, { status: s.status, files: s.files, style: s.style, vertical_mode: s.vertical_mode });
      });
      $("#save-state").textContent = P.dirty ? "Alterações não salvas…" : "Salvo";
      renderExport();
    } catch (e) {
      P.dirty = true;
      $("#save-state").textContent = "Erro ao salvar";
      fail(e);
    } finally {
      P.saving = false;
    }
  }

  async function flushSave() {
    clearTimeout(P.saveTimer);
    while (P.dirty || P.saving) {
      if (!P.saving) await save();
      else await new Promise((r) => setTimeout(r, 100));
    }
  }

  // ---------- linha do tempo ----------
  function renderTimeline() {
    const dur = P.project?.info.duration || 1;
    $("#tl-marks").replaceChildren(...P.clips.map((c) => el("div", {
      class: "tl-mark" + (c._k === P.selected ? " selected" : ""),
      style: `left:${(c.start / dur) * 100}%;width:${Math.max(0.3, ((c.end - c.start) / dur) * 100)}%`,
      title: `${c.title} (${fmt(c.start)} → ${fmt(c.end)})`,
    }, c.title)));
    const pend = $("#tl-pending");
    const a = P.markIn, b = P.markOut;
    if (a !== null) {
      const end = b !== null && b > a ? b : a;
      pend.style.left = `${(a / dur) * 100}%`;
      pend.style.width = `${Math.max(0.2, ((end - a) / dur) * 100)}%`;
      pend.classList.remove("hidden");
    } else pend.classList.add("hidden");
    $("#mark-range").textContent = a !== null ? `${fmt(a)} → ${b !== null ? fmt(b) : "…"}` : "";
    $('[data-act="create-from-marks"]').classList.toggle("hidden", !(a !== null && b !== null && b > a));
  }

  function updateHead() {
    const dur = P.project?.info.duration || 1;
    $("#tl-head").style.left = `${(video.currentTime / dur) * 100}%`;
    $("#cur-time").textContent = fmt(video.currentTime);
  }

  // ---------- legendas: estilo e prévia ----------
  const currentStyle = () => store.get("style", "classic");

  function renderStylePicker() {
    const box = $("#style-picker");
    box.replaceChildren(...Object.entries(S.presets).map(([key, p]) => {
      const sample = el("span", { class: "sample" }, p.uppercase ? "OLÁ MUNDO" : "Olá mundo");
      styleText(sample, p, 20 / p.size);
      if (p.kind === "karaoke") {
        sample.textContent = "";
        const t = (s) => p.uppercase ? s.toUpperCase() : s;
        sample.append(el("span", { style: `color:${p.highlight_color}` }, t("Olá")), " ", t("mundo"));
      }
      if (p.kind === "boxed") sample.style.background = rgba(p.box_color, p.box_opacity), sample.style.padding = "2px 6px";
      const del = p.builtin ? null : el("button", { class: "btn small ghost danger del", title: "Apagar este estilo" }, "×");
      del?.addEventListener("click", (e) => {
        e.stopPropagation();
        confirmClick(del, "apagar?", async () => {
          try {
            await api("DELETE", `/api/presets/${key}`);
            S.presets = await api("GET", "/api/presets");
            if (currentStyle() === key) store.set("style", "classic");
            renderStylePicker(); syncStyleSelect(); loadCues();
            toast(`Estilo “${p.name}” apagado.`, "ok");
          } catch (err) { fail(err); }
        });
      });
      return el("div", { class: "style-card" + (key === currentStyle() ? " active" : ""),
        onclick: () => { store.set("style", key); renderStylePicker(); syncStyleSelect(); fillStyleForm(); loadCues(); } },
        del, sample, el("span", { class: "label" }, p.name));
    }));
  }

  // formulário "criar estilo a partir do selecionado"
  function fillStyleForm() {
    const f = $("#style-form"), p = S.presets[currentStyle()];
    if (!f || !p) return;
    f.font.replaceChildren(...(S.status?.fonts || [p.font]).map((x) => el("option", { value: x }, x)));
    for (const [k, v] of Object.entries(p)) {
      const input = f.elements[k];
      if (!input || k === "name") continue;
      if (input.type === "checkbox") input.checked = !!v;
      else if (k === "position") input.value = Math.round(v * 100);
      else input.value = v;
    }
    f.name.value = "";
  }

  function readStyleForm() {
    const f = $("#style-form");
    const num = (k) => Number(f.elements[k].value);
    return {
      name: f.name.value.trim(), kind: f.kind.value, font: f.font.value, size: num("size"),
      primary_color: f.primary_color.value.toUpperCase(), highlight_color: f.highlight_color.value.toUpperCase(),
      outline_color: f.outline_color.value.toUpperCase(), outline: num("outline"), shadow: num("shadow"),
      box_color: f.box_color.value.toUpperCase(), box_opacity: num("box_opacity"),
      position: num("position") / 100, uppercase: f.uppercase.checked, words_per_line: num("words_per_line"),
    };
  }

  $("#style-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("POST", "/api/presets", readStyleForm());
      S.presets = await api("GET", "/api/presets");
      store.set("style", r.key);
      renderStylePicker(); syncStyleSelect(); loadCues();
      $("#style-editor").open = false;
      toast(`Estilo “${r.name}” criado. Ele já está selecionado.`, "ok");
    } catch (err) { fail(err); }
  });

  function rgba(hex, a = 1) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
  }

  function styleText(node, p, scale) {
    const o = Math.max(0.5, p.outline * scale), sh = p.shadow * scale;
    node.style.fontFamily = `"${p.font}", sans-serif`;
    node.style.fontSize = `${p.size * scale}px`;
    node.style.color = p.primary_color;
    node.style.textTransform = p.uppercase ? "uppercase" : "none";
    if (p.kind === "boxed") { node.style.textShadow = "none"; return; }
    // contorno com várias sombras (funciona em todos os navegadores)
    const shadows = [];
    for (let a = 0; a < 16; a++) {
      const r = (a / 16) * Math.PI * 2;
      shadows.push(`${(Math.cos(r) * o).toFixed(1)}px ${(Math.sin(r) * o).toFixed(1)}px 0 ${p.outline_color}`);
    }
    if (sh > 0) shadows.push(`${sh}px ${sh}px ${sh}px rgba(0,0,0,.6)`);
    node.style.textShadow = shadows.join(",");
  }

  async function loadCues() {
    if (!P.project?.has_transcript) return;
    const style = currentStyle();
    try {
      const r = await api("GET", `/api/projects/${pid}/captions/cues?style=${encodeURIComponent(style)}`);
      P.cues = r.cues; P.cuePreset = r.preset; P.cueStyle = style;
      P.cueStarts = P.cues.map((c) => c.start);
      lastCueKey = null;
      renderPreview();
    } catch (e) { fail(e); }
  }

  function wrapWords(words, maxChars) {
    const lines = [];
    for (const w of words) {
      const last = lines[lines.length - 1];
      if (last && last.text.length + 1 + w.word.length <= maxChars) { last.text += " " + w.word; last.words.push(w); }
      else lines.push({ text: w.word, words: [w] });
    }
    return lines;
  }

  let lastCueKey = null;
  function renderPreview() {
    const box = $("#caption-preview");
    const active = !$('[data-panel="captions"]').classList.contains("hidden");
    if (!active || !P.cues || !video.videoWidth) { box.classList.add("hidden"); return; }
    const t = video.currentTime, p = P.cuePreset;
    let lo = 0, hi = P.cueStarts.length - 1, ci = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (P.cueStarts[m] <= t) { ci = m; lo = m + 1; } else hi = m - 1; }
    const cue = ci >= 0 && P.cues[ci].end > t ? P.cues[ci] : null;
    if (!cue) { box.classList.add("hidden"); lastCueKey = null; return; }
    let wi = cue.words.findIndex((w, k) => t >= w.start && (k === cue.words.length - 1 || t < cue.words[k + 1].start));
    if (wi < 0) wi = 0;
    const key = `${ci}:${p.kind === "karaoke" || p.kind === "pop" ? wi : ""}`;
    box.classList.remove("hidden");
    if (key === lastCueKey) return;
    lastCueKey = key;

    // área real do vídeo dentro do elemento (object-fit: contain)
    const wrap = $("#player-wrap").getBoundingClientRect();
    const vr = video.getBoundingClientRect();
    const k = Math.min(vr.width / video.videoWidth, vr.height / video.videoHeight);
    const w = video.videoWidth * k, h = video.videoHeight * k;
    const x = vr.left - wrap.left + (vr.width - w) / 2, y = vr.top - wrap.top + (vr.height - h) / 2;
    const scale = Math.min(w, h) / 1080;
    box.className = `caption-preview ${p.kind}`;
    box.style.left = `${x + w * 0.06}px`;
    box.style.right = `${wrap.width - (x + w) + w * 0.06}px`;
    box.style.bottom = `${wrap.height - (y + h) + p.position * h}px`;
    styleText(box, p, scale);

    const words = p.kind === "pop" ? [cue.words[wi]] : cue.words;
    const lines = wrapWords(words, p.max_chars);
    box.replaceChildren(...lines.flatMap((line, li) => {
      const lineEl = el("span", { class: "line" });
      if (p.kind === "boxed") lineEl.style.background = rgba(p.box_color, p.box_opacity);
      line.words.forEach((wd, j) => {
        const span = el("span", { class: "cw" }, wd.word);
        if (p.kind === "karaoke" && wd === cue.words[wi]) span.style.color = p.highlight_color;
        lineEl.append(span, j < line.words.length - 1 ? " " : "");
      });
      return li < lines.length - 1 ? [lineEl, "\n"] : [lineEl];
    }));
  }

  // ---------- legendas: correção de texto ----------
  function decorateEdited(span, i) {
    const e = P.edits[i];
    span.textContent = e === undefined || e === "" ? P.transcript.words[i].word : e;
    span.classList.toggle("edited", e !== undefined && e !== "");
    span.classList.toggle("removed", e === "");
  }

  function renderCaptionEditor() {
    $("#caption-editor").replaceChildren(wordSpans(true));
    updateCaptionButtons();
  }

  const editsDirty = () => JSON.stringify(sortObj(P.edits)) !== JSON.stringify(sortObj(P.savedEdits));
  const sortObj = (o) => Object.fromEntries(Object.entries(o).sort((a, b) => a[0] - b[0]));

  function updateCaptionButtons() {
    const dirty = editsDirty();
    $('[data-act="save-captions"]').disabled = !dirty;
    $('[data-act="discard-captions"]').disabled = !dirty;
    const n = Object.keys(P.edits).length;
    $("#captions-state").textContent = dirty ? "Correções não salvas" : n ? `${n} palavra(s) corrigida(s)` : "";
  }

  function startEdit(span) {
    const i = Number(span.dataset.i);
    const original = P.transcript.words[i].word;
    span.textContent = P.edits[i] ?? original;
    span.classList.remove("removed");
    span.contentEditable = "true";
    span.focus();
    document.getSelection().selectAllChildren(span);
    let done = false;
    const finish = (commit) => {
      // Tirar o contentEditable dispara "blur" na hora: a trava evita rodar duas vezes.
      if (done) return;
      done = true;
      const text = span.textContent.replace(/\s+/g, " ").trim();
      span.removeEventListener("keydown", onKey);
      span.removeEventListener("blur", onBlur);
      span.contentEditable = "false";
      if (commit) {
        if (text === original) delete P.edits[i];
        else P.edits[i] = text;
      }
      decorateEdited(span, i);
      const mirror = $(`#transcript .w[data-i="${i}"]`);
      if (mirror) mirror.textContent = displayWord(i);
      updateCaptionButtons();
    };
    const onKey = (e) => {
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      if (e.key === "Escape") { e.preventDefault(); finish(false); }
    };
    const onBlur = () => finish(true);
    span.addEventListener("keydown", onKey);
    span.addEventListener("blur", onBlur);
  }

  async function saveCaptions() {
    try {
      await api("PUT", `/api/projects/${pid}/captions`, { edits: P.edits });
      P.savedEdits = { ...P.edits };
      updateCaptionButtons();
      toast("Correções salvas. Renderize de novo os cortes para aplicar.", "ok");
      loadCues();
    } catch (e) { fail(e); }
  }

  // ---------- exportar ----------
  function syncStyleSelect() {
    const sel = $("#opt-style");
    const val = store.get("export.style", currentStyle());
    sel.replaceChildren(el("option", { value: "" }, "Sem legenda"),
      ...Object.entries(S.presets).map(([k, p]) => el("option", { value: k }, p.name)));
    sel.value = val in S.presets || val === "" ? val : currentStyle();
  }

  function exportOptions() {
    const vertical = $("#opt-vertical").value || null;
    return { vertical, style: $("#opt-style").value || null, resolution: $("#opt-resolution").value };
  }

  function renderExport() {
    const list = $("#export-list");
    if (!list) return;
    if (!P.clips.length) { list.replaceChildren(el("p", { class: "muted" }, "Nenhum corte para exportar.")); return; }
    list.replaceChildren(...P.clips.map((c) => {
      const job = c.id && activeJobs().find((j) => j.kind === "render" && j.clip_id === c.id);
      const f = c.files || {};
      const files = el("div", { class: "files" },
        f.mp4 ? el("a", { href: f.mp4, target: "_blank" }, "Ver") : null,
        f.mp4 ? el("a", { href: f.mp4 + "?download=1" }, "MP4") : null,
        f.srt ? el("a", { href: f.srt + "?download=1" }, "SRT") : null,
        f.ass ? el("a", { href: f.ass + "?download=1" }, "ASS") : null);
      const info = c.status === "done"
        ? `renderizado${c.vertical_mode ? " · " + c.vertical_mode : " · original"}${c.style ? " · " + c.style : " · sem legenda"}`
        : c.status === "error" ? "erro no último render" : "ainda não renderizado";
      return el("div", { class: "export-item", dataset: { k: c._k } },
        el("div", {}, el("div", { class: "title" }, c.title),
          el("div", { class: "muted small" }, `${fmt(c.start)} → ${fmt(c.end)} · ${(c.end - c.start).toFixed(1)} s · ${info}`),
          files),
        el("div", { class: "row-actions" },
          job ? el("button", { class: "btn small ghost", onclick: () => cancelJob(job.id) }, "Cancelar")
            : el("button", { class: "btn small", onclick: () => renderOne(c) }, c.status === "done" ? "Renderizar de novo" : "Renderizar")),
        job ? el("div", { class: "progress" }, el("div", { class: "bar", style: `width:${job.progress * 100}%` })) : null,
        job ? el("div", { class: "muted small", style: "grid-column:1/-1" }, job.status === "queued" ? "Na fila…" : `${job.stage} ${Math.round(job.progress * 100)}%`) : null);
    }));
  }

  function renderExportJobs() {
    // atualiza só as barras (evita recriar a lista inteira a cada segundo)
    for (const c of P.clips) {
      const item = $(`.export-item[data-k="${c._k}"]`);
      const job = c.id && activeJobs().find((j) => j.kind === "render" && j.clip_id === c.id);
      const hasBar = item && $(".progress", item);
      if (!item || !!job !== !!hasBar) { renderExport(); return; }
      if (job) {
        $(".bar", item).style.width = `${job.progress * 100}%`;
        $(".progress + div", item).textContent = job.status === "queued" ? "Na fila…" : `${job.stage} ${Math.round(job.progress * 100)}%`;
      }
    }
  }

  async function renderOne(c) {
    try {
      await flushSave();
      const j = await api("POST", `/api/projects/${pid}/clips/${c.id}/render`, exportOptions());
      P.jobs[j.id] = j;
      renderExport();
    } catch (e) { fail(e); }
  }

  async function renderAll() {
    try {
      await flushSave();
      const js = await api("POST", `/api/projects/${pid}/render-all`, exportOptions());
      js.forEach((j) => (P.jobs[j.id] = j));
      renderExport();
    } catch (e) { fail(e); }
  }

  async function cancelJob(id) {
    try {
      P.jobs[id] = await api("POST", `/api/jobs/${id}/cancel`);
      renderBanner();
      renderExport();
    } catch (e) { fail(e); }
  }

  // ---------- ações (delegação de cliques) ----------
  let suggestArmed = null;
  const actions = {
    "cancel-job": () => cancelJob($("#job-banner").dataset.job),
    "mark-in": () => { P.markIn = video.currentTime; if (P.markOut !== null && P.markOut <= P.markIn) P.markOut = null; renderTimeline(); },
    "mark-out": () => { P.markOut = video.currentTime; renderTimeline(); },
    "create-from-marks": async () => {
      await addClip(P.markIn, P.markOut, `Corte manual ${fmt(P.markIn)}`);
      P.markIn = P.markOut = null;
      renderTimeline();
    },
    "create-from-selection": async (btn) => {
      btn.classList.add("hidden");
      await addClip(Number(btn.dataset.start), Number(btn.dataset.end), `Corte ${fmt(Number(btn.dataset.start))}`);
      window.getSelection().removeAllRanges();
    },
    transcribe: async () => {
      try { const j = await api("POST", `/api/projects/${pid}/process`); P.jobs[j.id] = j; renderBanner(); } catch (e) { fail(e); }
    },
    suggest: async (btn) => {
      const hasAi = P.clips.some((c) => c.id?.startsWith("c"));
      if (hasAi && suggestArmed !== btn) {
        // confirmação em dois cliques (sem janela de diálogo)
        suggestArmed = btn;
        btn.textContent = "Confirmar: substituir as sugestões atuais?";
        btn.classList.add("primary");
        setTimeout(() => { if (suggestArmed === btn) { suggestArmed = null; btn.textContent = "Sugerir cortes com IA"; btn.classList.remove("primary"); } }, 4000);
        return;
      }
      suggestArmed = null;
      btn.textContent = "Sugerir cortes com IA";
      btn.classList.remove("primary");
      try {
        await flushSave();
        const j = await api("POST", `/api/projects/${pid}/suggest`);
        P.jobs[j.id] = j;
        renderBanner();
      } catch (e) { fail(e); }
    },
    "save-captions": saveCaptions,
    "delete-renders": (btn) => confirmClick(btn, "Apagar mesmo?", async () => {
      try {
        const r = await api("DELETE", `/api/projects/${pid}/renders`);
        toast(`${r.deleted_files} arquivo(s) apagado(s).`, "ok");
        $("#captions-files").replaceChildren();
        await loadProject();
        await loadClips();
      } catch (e) { fail(e); }
    }),
    "preview-style": () => {
      if (!P.cues) { toast("Espere a transcrição para ver a prévia.", "error"); return; }
      P.cuePreset = { ...P.cuePreset, ...readStyleForm() };
      lastCueKey = null;
      toast("Prévia aplicada (cores, tamanho e posição). O agrupamento de palavras muda depois de salvar.");
    },
    "discard-captions": () => { P.edits = { ...P.savedEdits }; renderCaptionEditor(); renderTranscript(); },
    "render-all": renderAll,
    "export-captions": async () => {
      const style = $("#opt-style").value || currentStyle();
      try {
        const r = await api("POST", `/api/projects/${pid}/captions/export`, { style });
        $("#captions-files").replaceChildren(
          el("a", { href: r.srt + "?download=1" }, "Baixar .srt"), " · ",
          el("a", { href: r.ass + "?download=1" }, `Baixar .ass (${style})`));
      } catch (e) { fail(e); }
    },
  };

  view.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (btn && actions[btn.dataset.act]) { e.stopPropagation(); actions[btn.dataset.act](btn); return; }
    const tab = e.target.closest("[data-tab]");
    if (tab) {
      $$(".tabs button").forEach((b) => b.classList.toggle("active", b === tab));
      $$("[data-panel]").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== tab.dataset.tab));
      store.set("tab", tab.dataset.tab);
      if (tab.dataset.tab === "captions" && P.cueStyle !== currentStyle()) loadCues();
      if (tab.dataset.tab === "export") renderExport();
      renderPreview();
      return;
    }
    const w = e.target.closest(".w");
    if (w && P.transcript) {
      const i = Number(w.dataset.i);
      if (w.closest("#caption-editor")) {
        if (w.contentEditable !== "true") { video.currentTime = P.transcript.words[i].start; startEdit(w); }
      } else if (window.getSelection().isCollapsed) {
        video.currentTime = P.transcript.words[i].start;
      }
    }
  });

  $("#transcript").addEventListener("mouseup", (e) => setTimeout(() => onSelection(e), 0));
  const hideSel = (e) => { if (!e.target.closest("#sel-btn") && !e.target.closest("#transcript")) $("#sel-btn").classList.add("hidden"); };
  document.addEventListener("mousedown", hideSel);
  S.cleanup.push(() => document.removeEventListener("mousedown", hideSel));

  $("#timeline").addEventListener("click", (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    video.currentTime = ((e.clientX - r.left) / r.width) * (P.project?.info.duration || 0);
  });

  ["opt-vertical", "opt-style", "opt-resolution"].forEach((id) =>
    $("#" + id).addEventListener("change", (e) => store.set("export." + id.slice(4), e.target.value)));

  // ---------- laço de animação (playhead, palavra atual, prévia) ----------
  let raf = 0;
  const tick = () => {
    updateHead();
    highlightCurrent();
    renderPreview();
    if (P.stopAt !== null && video.currentTime >= P.stopAt) { video.pause(); P.stopAt = null; }
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
  video.addEventListener("seeking", () => { if (P.stopAt !== null && (video.currentTime < P.stopAt - 120)) P.stopAt = null; });
  video.addEventListener("pause", () => { P.stopAt = null; });
  video.addEventListener("loadedmetadata", () => { lastCueKey = null; });
  const onResize = () => { lastCueKey = null; };
  window.addEventListener("resize", onResize);

  const beforeUnload = (e) => { if (P.dirty || editsDirty()) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", beforeUnload);

  const poll = setInterval(pollJobs, 1000);
  S.cleanup.push(() => {
    clearInterval(poll);
    cancelAnimationFrame(raf);
    window.removeEventListener("resize", onResize);
    window.removeEventListener("beforeunload", beforeUnload);
    if (P.dirty) save();
    video.pause();
    video.removeAttribute("src");
    video.load();
  });

  // ---------- início ----------
  (async () => {
    try {
      await loadProject();
      // restaura preferências
      const tab = store.get("tab", "clips");
      $(`[data-tab="${tab}"]`)?.click();
      $("#opt-vertical").value = store.get("export.vertical", "blur");
      $("#opt-resolution").value = store.get("export.resolution", "1080p");
      syncStyleSelect();
      renderStylePicker();
      fillStyleForm();
      await Promise.all([loadTranscript(), loadClips()]);
      for (const j of P.project.jobs) P.jobs[j.id] = j;
      renderBanner();
      if (!P.project.has_transcript && !activeJobs().length) {
        showNotice("Este vídeo ainda não foi transcrito. Clique em “Transcrever” na aba Cortes.");
      }
      pollJobs();
    } catch (e) {
      fail(e);
      if (/não encontrado/.test(e.message)) location.hash = "#/";
    }
  })();
}

boot();
