/* Spelunk dashboard. Plain JS, no build step, no network access beyond the
 * local Spelunk server. All file names are inserted as text, never as HTML. */
"use strict";

// ------------------------------------------------------------------ token
let TOKEN = "";
(function readToken() {
  const m = location.hash.match(/t=([A-Za-z0-9_\-]+)/);
  if (m) {
    TOKEN = m[1];
    try { sessionStorage.setItem("spelunk-token", TOKEN); } catch (e) { /* private mode */ }
    history.replaceState(null, "", location.pathname);
  } else {
    try { TOKEN = sessionStorage.getItem("spelunk-token") || ""; } catch (e) { TOKEN = ""; }
  }
})();

async function api(path, body) {
  const opt = { headers: { "X-Spelunk-Token": TOKEN } };
  if (body !== undefined) {
    opt.method = "POST";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

// ---------------------------------------------------------------- helpers
const $ = (id) => document.getElementById(id);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
function human(n) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (Math.abs(n) >= 1000 && i < units.length - 1) { n /= 1000; i++; }
  return i === 0 ? `${Math.round(n)} B` : `${n.toFixed(n >= 100 ? 0 : 1)} ${units[i]}`;
}
function ago(ts) {
  if (!ts) return "";
  const days = (Date.now() / 1000 - ts) / 86400;
  if (days < 1) return "changed today";
  if (days < 60) return `changed ${Math.round(days)} days ago`;
  if (days < 730) return `changed ${Math.round(days / 30)} months ago`;
  return `changed ${Math.round(days / 365)} years ago`;
}
function shortPath(p, home) {
  return home && p.startsWith(home) ? "~" + p.slice(home.length) : p;
}
const SAFETY_LABEL = { safe: "Safe to clear", review: "Your call", keep: "Keep / manage in app", never: "Don't remove", unknown: "Unknown" };
function badge(safety) {
  return h("span", { class: `badge ${safety || "unknown"}`, text: SAFETY_LABEL[safety] || "Unknown" });
}
let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 4000);
}

// ------------------------------------------------------------------ state
const state = {
  home: "",
  queue: new Map(), // path -> {path, name, size, safety}
  selected: null,
  explorePath: "",
  scanDone: false,
  externals: [],
};

// --------------------------------------------------------------- scanning
async function poll() {
  let s;
  try {
    s = await api("/api/state");
  } catch (e) {
    $("scanStatus").textContent = TOKEN ? `Can't reach Spelunk: ${e.message}` : "Open Spelunk from the link printed in your terminal.";
    return;
  }
  state.home = s.home;
  const p = s.scan;
  if (p.running) {
    state.scanDone = false;
    $("scanStatus").textContent = `Scanning… ${p.files.toLocaleString()} files, ${human(p.bytes)} · ${shortPath(p.current, state.home)}`;
    setTimeout(poll, 600);
    return;
  }
  if (s.error) $("scanStatus").textContent = `Scan failed: ${s.error}`;
  else if (s.has_tree) $("scanStatus").textContent = `Scanned ${p.files.toLocaleString()} files in ${p.elapsed}s` + (p.denied ? ` · ${p.denied} folders had no access` : "");
  if (!state.scanDone && s.has_tree) {
    state.scanDone = true;
    await refreshAll();
  }
}

async function refreshAll() {
  await Promise.all([loadOverview(), loadSuggestions(), loadExplore(state.explorePath), loadExternals()]);
}

$("rescanBtn").addEventListener("click", async () => {
  await api("/api/scan", {});
  state.scanDone = false;
  poll();
});

// --------------------------------------------------------------- overview
const ROOT_LABELS = {
  "/Applications": "Apps", "/Library": "System Library", "/opt/homebrew": "Homebrew",
  "/usr/local": "/usr/local", "/Users/Shared": "Shared", "/private/var": "System temp & swap",
};
const SEG_COLORS = ["var(--seg-1)", "var(--seg-2)", "var(--seg-3)", "var(--seg-4)", "var(--seg-5)", "var(--seg-6)", "var(--seg-7)"];

async function loadOverview() {
  const ov = await api("/api/overview");
  const total = ov.disk.total;
  const segs = [];
  if (ov.system_bytes) segs.push({ label: "macOS (system, recovery, swap)", bytes: ov.system_bytes, color: "var(--seg-system)" });
  let ci = 0;
  for (const [root, bytes] of Object.entries(ov.scanned)) {
    const label = root === state.home ? "Your home folder" : (ROOT_LABELS[root] || root);
    segs.push({ label, bytes, color: SEG_COLORS[ci++ % SEG_COLORS.length], root });
  }
  if (ov.hidden > 0) segs.push({ label: "Hidden (not readable / snapshots)", bytes: ov.hidden, color: "var(--seg-hidden)" });
  segs.push({ label: "Free", bytes: ov.disk.free, color: "var(--seg-free)" });

  $("diskNumbers").replaceChildren(
    h("strong", { text: human(ov.disk.free) }), " free of ", h("strong", { text: human(total) }),
  );
  const bar = $("diskBar");
  bar.replaceChildren(...segs.map((s) => h("span", {
    title: `${s.label}: ${human(s.bytes)}`,
    style: { width: `${Math.max(0, (s.bytes / total) * 100)}%`, background: s.color },
  })));
  $("diskLegend").replaceChildren(...segs.map((s) => h("li", {},
    h("i", { style: { background: s.color } }), h("span", { class: "lbl", text: s.label }), h("strong", { text: human(s.bytes) }))));

  const notes = [];
  if (ov.full_disk_access === false) {
    const how = ov.app_mode
      ? "Click +, choose Spelunk in Applications, switch it on, then quit and reopen Spelunk."
      : "Add the app you started Spelunk from (Terminal, iTerm, VS Code…), restart it, then rescan.";
    notes.push(h("div", { class: "note action" },
      h("div", {},
        h("strong", { text: "Let Spelunk see everything. " }),
        "macOS hides Mail, Messages, Safari and parts of Library until you allow Full Disk Access. ", how),
      h("button", { class: "btn small", type: "button", text: "Open Full Disk Access settings",
        onclick: () => api("/api/open-privacy-settings", {}).catch((e) => toast(e.message)) })));
  }
  if (ov.hidden > 1e9) {
    notes.push(h("div", { class: "note" },
      h("strong", { text: `${human(ov.hidden)} couldn't be measured. ` }),
      ov.full_disk_access === false
        ? "Most of it will appear once Full Disk Access is on."
        : "This is usually system files, local snapshots or other users' data that need admin rights to read."));
  }
  for (const snap of ov.snapshots || []) {
    if (snap.kind === "os") continue;
    notes.push(h("div", { class: "note info" }, h("strong", { text: "Snapshot: " }), h("code", { text: snap.name }), " — ", snap.note));
  }
  if (!ov.volumes.length && ov.platform !== "Darwin") {
    notes.push(h("div", { class: "note info", text: "Not running on macOS: system volumes and snapshots aren't shown." }));
  }
  $("diskNotes").replaceChildren(...notes);
}

// ------------------------------------------------------------ suggestions
async function loadSuggestions() {
  const { items } = await api("/api/recommendations");
  const groups = [
    { key: "safe", title: "Safe to clear", help: "Rebuilds itself or is a leftover.", items: [] },
    { key: "review", title: "Your call", help: "Check before removing.", items: [] },
    { key: "keep", title: "Manage inside the app", help: "Big, but the app needs it. Use the tip to shrink it.", items: [] },
  ];
  for (const it of items) {
    const g = it.tip_only ? groups[2] : groups.find((x) => x.key === it.info.safety) || groups[1];
    g.items.push(it);
  }
  const box = $("suggestList");
  box.replaceChildren();
  if (!items.length) {
    box.append(h("p", { class: "hint", text: "No large items found yet." }));
    return;
  }
  for (const g of groups) {
    if (!g.items.length) continue;
    const sum = g.items.reduce((s, i) => s + i.size, 0);
    const head = h("div", { class: "group-head" },
      badge(g.key), h("span", { text: g.title }), h("span", { class: "sum", text: human(sum) }));
    if (g.key === "safe") {
      head.append(h("button", { class: "btn small", type: "button", text: "Add all to review",
        onclick: () => { g.items.forEach(addToQueue); toast(`Added ${g.items.length} items to the review list`); } }));
    }
    const rows = h("div", { class: "rows" }, g.items.map((it) => suggestionRow(it, g.key === "keep")));
    box.append(h("div", { class: "group" }, head, rows));
  }
}

function suggestionRow(it, tipOnly) {
  const cb = tipOnly ? h("span", { class: "icon" }) : h("input", {
    type: "checkbox", "aria-label": `Add ${it.name} to review list`, checked: state.queue.has(it.path),
    onclick: (e) => { e.stopPropagation(); e.target.checked ? addToQueue(it) : removeFromQueue(it.path); },
  });
  return h("div", { class: "row", "data-path": it.path, onclick: () => select(it) },
    cb,
    h("div", {},
      h("div", { class: "name", text: `${it.info.title} — ${it.name}` }),
      h("div", { class: "sub", text: shortPath(it.path, state.home) })),
    h("div", { class: "size", text: human(it.size) }));
}

// --------------------------------------------------------------- explore
async function loadExplore(path) {
  let node;
  try {
    node = await api("/api/node" + (path ? `?path=${encodeURIComponent(path)}` : ""));
  } catch (e) {
    if (path) return loadExplore("");
    return;
  }
  if (!node.path) {
    lastRoots = Object.fromEntries((node.children || []).map((c) => [c.path, c.size]));
    // With a single scanned folder, skip the pointless one-tile overview.
    if ((node.children || []).length === 1) return loadExplore(node.children[0].path);
  }
  state.explorePath = node.path;
  renderCrumbs(node);
  renderTreemap(node);
  renderList(node);
}

function renderCrumbs(node) {
  const c = $("crumbs");
  c.replaceChildren();
  c.append(h("button", { type: "button", text: "All scanned", onclick: () => loadExplore("") }));
  if (!node.path) return;
  // Find which scanned root we are in, then add each folder below it.
  const roots = Object.keys(lastRoots);
  const root = roots.filter((r) => node.path === r || node.path.startsWith(r.replace(/\/$/, "") + "/")).sort((a, b) => b.length - a.length)[0];
  if (!root) return;
  const parts = node.path === root ? [] : node.path.slice(root.length).split("/").filter(Boolean);
  const chain = [{ label: shortPath(root, state.home) || root, path: root }];
  let acc = root;
  for (const p of parts) { acc = acc.replace(/\/$/, "") + "/" + p; chain.push({ label: p, path: acc }); }
  chain.forEach((step, i) => {
    c.append(h("span", { class: "sep", text: "›" }));
    if (i === chain.length - 1) c.append(h("span", { class: "here", text: step.label }));
    else c.append(h("button", { type: "button", text: step.label, onclick: () => loadExplore(step.path) }));
  });
}
let lastRoots = {};

function renderTreemap(node) {
  const box = $("treemap");
  box.replaceChildren();
  const kids = (node.children || []).filter((c) => c.size > 0);
  if (!kids.length) {
    box.append(h("div", { class: "empty-map", text: node.denied ? "No access to this folder." : "Nothing large here." }));
    return;
  }
  const W = box.clientWidth || 800, H = box.clientHeight || 380;
  for (const r of squarify(kids, 0, 0, W, H)) {
    const n = r.n;
    const safety = n.denied ? "denied" : (n.info && n.info.safety) || "unknown";
    const tile = h("div", {
      class: `tile ${safety}` + (state.selected && state.selected.path === n.path ? " selected" : ""),
      title: `${n.name} — ${human(n.size)}`,
      style: { left: `${r.x}px`, top: `${r.y}px`, width: `${Math.max(0, r.w)}px`, height: `${Math.max(0, r.h)}px` },
      onclick: () => { select(n); if (n.is_dir && !n.denied && n.child_count) loadExplore(n.path); },
    });
    if (r.w > 60 && r.h > 32) tile.append(h("b", { text: n.name }), h("small", { text: human(n.size) }));
    box.append(tile);
  }
}

function renderList(node) {
  const list = $("exploreList");
  list.replaceChildren(...(node.children || []).slice(0, 60).map((n) => {
    const selectable = n.info && n.info.safety !== "never" && n.name !== "(smaller items)" && n.path;
    const cb = selectable ? h("input", {
      type: "checkbox", "aria-label": `Add ${n.name} to review list`, checked: state.queue.has(n.path),
      onclick: (e) => { e.stopPropagation(); e.target.checked ? addToQueue(n) : removeFromQueue(n.path); },
    }) : h("span", { class: "icon" });
    return h("div", { class: "row" + (n.denied ? " denied" : ""), onclick: () => { select(n); if (n.is_dir && !n.denied && n.child_count) loadExplore(n.path); } },
      cb,
      h("div", {},
        h("div", { class: "name", text: (n.is_dir ? "▸ " : "") + n.name }),
        h("div", { class: "sub" }, n.info && n.info.id === "small" ? null : badge(n.info && n.info.safety), " ", n.info ? n.info.title : "")),
      h("div", { class: "size", text: human(n.size) }));
  }));
}

// Squarified treemap (Bruls, Huizing, van Wijk).
function squarify(nodes, x, y, w, h) {
  const total = nodes.reduce((s, n) => s + n.size, 0);
  const out = [];
  if (!total || w <= 0 || h <= 0) return out;
  const scale = (w * h) / total;
  let items = nodes.map((n) => ({ n, a: n.size * scale })).filter((i) => i.a > 0.5);
  let rx = x, ry = y, rw = w, rh = h;
  const worst = (row, sum, side) => {
    let max = 0, min = Infinity;
    for (const r of row) { max = Math.max(max, r.a); min = Math.min(min, r.a); }
    const s2 = sum * sum, w2 = side * side;
    return Math.max((w2 * max) / s2, s2 / (w2 * min));
  };
  while (items.length) {
    const side = Math.min(rw, rh);
    const row = [items[0]];
    let sum = items[0].a, best = worst(row, sum, side), i = 1;
    while (i < items.length) {
      const next = worst([...row, items[i]], sum + items[i].a, side);
      if (next > best) break;
      row.push(items[i]); sum += items[i].a; best = next; i++;
    }
    const thick = sum / side;
    let off = 0;
    for (const it of row) {
      const len = it.a / thick;
      if (rw >= rh) out.push({ n: it.n, x: rx, y: ry + off, w: thick, h: len });
      else out.push({ n: it.n, x: rx + off, y: ry, w: len, h: thick });
      off += len;
    }
    if (rw >= rh) { rx += thick; rw -= thick; } else { ry += thick; rh -= thick; }
    items = items.slice(i);
  }
  return out;
}

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (state.scanDone && !$("pane-explore").classList.contains("hidden")) loadExplore(state.explorePath); }, 200);
});

// ---------------------------------------------------------------- detail
function select(n) {
  state.selected = n;
  document.querySelectorAll(".row.selected, .tile.selected").forEach((el) => el.classList.remove("selected"));
  document.querySelectorAll(".row").forEach((el) => { if (el.dataset.path === n.path) el.classList.add("selected"); });
  const info = n.info || {};
  const d = $("detail");
  const canAct = n.path && info.safety !== "never" && n.name !== "(smaller items)";
  const dl = h("dl", {},
    h("dt", { text: "What it is" }), h("dd", { text: info.what || "—" }),
    h("dt", { text: "If you remove it" }), h("dd", { text: info.if_removed || "—" }));
  if (info.owner) dl.prepend(h("dt", { text: "Belongs to" }), h("dd", { text: info.owner }));
  if (info.how) {
    dl.append(h("dt", { text: "Better way" }), h("dd", { class: "how" },
      h("code", { text: info.how }),
      h("button", { class: "btn small", type: "button", text: "Copy", onclick: () => copy(info.how) })));
  }
  if (n.denied) dl.append(h("dt", { text: "Access" }), h("dd", { text: "macOS blocked Spelunk from reading this folder. Allow Full Disk Access (see the note at the top) and rescan." }));
  d.replaceChildren(
    h("div", {}, badge(info.safety)),
    h("h2", { text: info.title || n.name, style: { marginTop: "8px" } }),
    h("div", { class: "bigsize", text: human(n.size) }),
    h("div", { class: "path mono", text: n.path ? shortPath(n.path, state.home) : "" }),
    h("div", { class: "sub", text: [n.files ? `${n.files.toLocaleString()} ${n.files === 1 ? "file" : "files"}` : "", ago(n.mtime)].filter(Boolean).join(" · ") }),
    dl,
    h("div", { class: "detail-actions" },
      canAct ? h("button", { class: "btn primary", type: "button", text: state.queue.has(n.path) ? "In review list ✓" : "Add to review list",
        onclick: (e) => { addToQueue(n); e.target.textContent = "In review list ✓"; } }) : null,
      n.path ? h("button", { class: "btn", type: "button", text: "Show in Finder", onclick: () => api("/api/reveal", { path: n.path }) }) : null,
    ),
  );
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied"); } catch (e) { toast("Copy failed — select the text instead"); }
}

// ----------------------------------------------------------------- queue
function addToQueue(n) {
  if (!n.path || state.queue.has(n.path)) return;
  state.queue.set(n.path, { path: n.path, name: n.name, size: n.size, safety: n.info ? n.info.safety : "unknown" });
  renderQueue();
}
function removeFromQueue(path) {
  state.queue.delete(path);
  renderQueue();
}
function renderQueue() {
  const items = [...state.queue.values()];
  const total = items.reduce((s, i) => s + i.size, 0);
  $("queueTotal").textContent = items.length ? `${items.length} items · ${human(total)}` : "Empty";
  $("queueList").replaceChildren(...items.map((i) => h("li", {},
    h("span", { text: i.name, title: i.path }), h("span", { class: "mono", text: human(i.size) }),
    h("button", { class: "x", type: "button", "aria-label": `Remove ${i.name}`, text: "×", onclick: () => { removeFromQueue(i.path); syncChecks(); } }))));
  const has = items.length > 0;
  $("trashBtn").disabled = !has;
  $("deleteBtn").disabled = !has;
  $("moveBtn").disabled = !has || !$("driveSelect").value;
  syncChecks();
}
function syncChecks() {
  document.querySelectorAll(".row").forEach((row) => {
    const cb = row.querySelector("input[type=checkbox]");
    if (cb && row.dataset.path) cb.checked = state.queue.has(row.dataset.path);
  });
}

async function loadExternals() {
  const { volumes } = await api("/api/externals");
  state.externals = volumes.filter((v) => v.writable && !v.is_disk_image);
  const sel = $("driveSelect");
  sel.replaceChildren(...(state.externals.length
    ? state.externals.map((v) => h("option", { value: v.path, title: v.filesystem || "", text: `${v.name} · ${human(v.free)} free` }))
    : [h("option", { value: "", text: "No external drive connected" })]));
  renderQueue();
}
$("driveSelect").addEventListener("change", renderQueue);

// --------------------------------------------------------- plan + execute
const KIND_TEXT = {
  trash: { title: "Move to Bin", verb: "Move to Bin" },
  delete: { title: "Delete permanently", verb: "Delete permanently" },
  move: { title: "Move to external drive", verb: "Copy, verify and move" },
};
let currentPlan = null;

async function openPlan(kind) {
  const paths = [...state.queue.keys()];
  const dest = kind === "move" ? $("driveSelect").value : undefined;
  let plan;
  try {
    plan = await api("/api/plan", { kind, paths, dest });
  } catch (e) { toast(e.message); return; }
  currentPlan = plan;
  $("planTitle").textContent = KIND_TEXT[kind].title;
  const body = $("planBody");
  body.replaceChildren();
  body.append(h("p", {}, h("strong", { text: `${plan.count} items · ${human(plan.total_bytes)}` }),
    plan.blocked ? h("span", { text: ` (${plan.blocked} blocked and will be skipped)` }) : ""));
  if (plan.dest && plan.dest.archive) body.append(h("p", { class: "sub" }, "Destination: ", h("code", { text: plan.dest.archive })));
  for (const w of plan.warnings) body.append(h("div", { class: "warn", text: w }));
  if (plan.dest && plan.dest.error) body.append(h("div", { class: "err", text: plan.dest.error }));
  body.append(h("ul", {}, plan.items.map((i) => h("li", { class: i.blocked ? "blocked" : "" },
    h("span", { class: "p", title: i.path }, badge(i.safety), " ", shortPath(i.path, state.home)),
    h("span", { class: "mono", text: human(i.bytes || 0) }),
    i.blocked ? h("span", { class: "why", text: i.blocked }) : null))));
  $("ackRow").classList.toggle("hidden", !plan.needs_ack);
  $("ackBox").checked = false;
  const btn = $("confirmBtn");
  btn.textContent = KIND_TEXT[kind].verb;
  btn.classList.toggle("danger", kind === "delete");
  btn.disabled = plan.count === 0 || (plan.needs_ack && !$("ackBox").checked);
  $("planDialog").showModal();
}
$("ackBox").addEventListener("change", () => {
  $("confirmBtn").disabled = !currentPlan || currentPlan.count === 0 || !$("ackBox").checked;
});
$("trashBtn").addEventListener("click", () => openPlan("trash"));
$("deleteBtn").addEventListener("click", () => openPlan("delete"));
$("moveBtn").addEventListener("click", () => openPlan("move"));

$("confirmBtn").addEventListener("click", async () => {
  if (!currentPlan) return;
  const btn = $("confirmBtn");
  btn.disabled = true;
  let job;
  try {
    job = await api("/api/execute", { plan_id: currentPlan.id, ack: $("ackBox").checked });
  } catch (e) {
    $("planBody").prepend(h("div", { class: "err", text: e.message }));
    return;
  }
  $("cancelBtn").classList.add("hidden");
  const bar = h("i");
  const status = h("div", { class: "sub", text: "Working…" });
  $("planBody").replaceChildren(h("div", { class: "progress" }, bar), status);
  $("ackRow").classList.add("hidden");
  while (job.state === "running") {
    await new Promise((r) => setTimeout(r, 500));
    job = await api(`/api/job?id=${job.id}`);
    bar.style.width = job.total ? `${Math.min(100, (job.done_bytes / job.total) * 100)}%` : "50%";
    status.textContent = job.current ? shortPath(job.current, state.home) : "Working…";
  }
  bar.style.width = "100%";
  const ok = job.results.filter((r) => r.ok);
  const bad = job.results.filter((r) => !r.ok);
  const lines = [h("p", {}, h("strong", { text: `${ok.length} done` }), bad.length ? ` · ${bad.length} failed` : "",
    job.kind !== "trash" ? ` · ${human(job.freed)} freed` : " · empty the Bin to free the space")];
  for (const r of bad) lines.push(h("div", { class: "err", text: `${shortPath(r.path, state.home)}: ${r.error}` }));
  $("planBody").replaceChildren(...lines);
  btn.textContent = "Done";
  btn.disabled = false;
  btn.onclick = () => { $("planDialog").close(); btn.onclick = null; };
  for (const r of ok) state.queue.delete(r.path);
  currentPlan = null;
  renderQueue();
  await Promise.all([loadOverview(), loadSuggestions(), loadExplore(state.explorePath), loadHistory()]);
});
$("planDialog").addEventListener("close", () => {
  $("cancelBtn").classList.remove("hidden");
  $("confirmBtn").onclick = null;
  $("confirmBtn").classList.remove("danger");
});

// --------------------------------------------------------------- history
async function loadHistory() {
  const { items } = await api("/api/history");
  $("historyList").replaceChildren(...(items.length ? items.map((i) => h("div", { class: "row" },
    h("span", { class: "icon", text: i.ok ? "✓" : "!" }),
    h("div", {},
      h("div", { class: "name", text: `${i.action === "move" ? "Moved" : i.action === "trash" ? "Moved to Bin" : "Deleted"} ${shortPath(i.path, state.home)}` }),
      h("div", { class: "sub", text: i.ok ? (i.to ? `→ ${i.to}` : i.time) : i.error })),
    h("div", { class: "size", text: human(i.bytes || 0) }))) : [h("p", { class: "hint", text: "Nothing yet." })]));
}

// ------------------------------------------------------------------ tabs
document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((t) => { t.classList.toggle("active", t === tab); t.setAttribute("aria-selected", t === tab); });
  for (const name of ["suggest", "explore", "history"]) $(`pane-${name}`).classList.toggle("hidden", tab.dataset.tab !== name);
  if (tab.dataset.tab === "explore" && state.scanDone) loadExplore(state.explorePath);
  if (tab.dataset.tab === "history") loadHistory();
}));

renderQueue();
poll();
