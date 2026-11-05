// BAM Review Console. All dynamic text goes through h()/textContent, never innerHTML.
const $ = (id) => document.getElementById(id);
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const clear = (el) => { el.replaceChildren(); return el; };
async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = JSON.stringify((await res.json()).detail); } catch (_) {}
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json();
}
const pct = (v) => (v === null || v === undefined ? "--" : `${Math.round(Number(v) * 100)}%`);
const fmt = (v, d = 6) => (v === null || v === undefined ? "N/A" : Number(v).toFixed(d));
const scoreClass = (v) => v === null || v === undefined ? "text-slate-400" : v >= 0.8 ? "text-emerald-400" : v >= 0.5 ? "text-amber-400" : "text-red-400";

let currentTab = "pipeline", assets = [], selectedId = null;

// ---- Tabs
document.querySelectorAll(".tab-btn").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  currentTab = name;
  document.querySelectorAll(".tab-btn").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name));
  for (const t of ["pipeline", "review", "groups", "validation"]) {
    const s = $(`tab-${t}`);
    s.classList.toggle("hidden", t !== name);
  }
  if (name === "pipeline") loadPipeline();
  if (name === "review") loadAssets();
  if (name === "groups") loadGroups();
  if (name === "validation") loadValidation();
}

// ---- Pipeline tab
function barList(el, data, colors = {}) {
  clear(el);
  const total = Object.values(data).reduce((a, b) => a + b, 0) || 1;
  const entries = Object.entries(data);
  if (!entries.length) el.append(h("div", { class: "text-slate-600 text-xs" }, "No data"));
  for (const [k, v] of entries) {
    el.append(h("div", { class: "mb-1" },
      h("div", { class: "flex justify-between text-xs" }, h("span", {}, k), h("span", { class: "text-slate-400" }, v)),
      h("div", { class: "h-1.5 bg-slate-800 rounded" }, h("div", { class: `h-1.5 rounded ${colors[k] || "bg-emerald-500"}`, style: `width:${(v / total) * 100}%` }))));
  }
}
async function loadPipeline() {
  try {
    const o = await api("/api/review/overview");
    const flow = clear($("stage-flow"));
    const max = Math.max(1, ...o.stages.map((s) => s.count));
    o.stages.forEach((s, i) => {
      const prev = i ? o.stages[i - 1].count : s.count;
      const drop = prev - s.count;
      flow.append(h("div", { class: "flex-1 min-w-[90px] bg-slate-800 rounded p-2 text-center" },
        h("div", { class: "text-[10px] uppercase text-slate-400" }, s.stage),
        h("div", { class: "text-2xl font-bold text-emerald-400" }, s.count),
        h("div", { class: "h-1 bg-slate-700 rounded mt-1" }, h("div", { class: "h-1 bg-emerald-500 rounded", style: `width:${(s.count / max) * 100}%` })),
        h("div", { class: `text-[10px] mt-1 ${drop > 0 ? "text-amber-400" : "text-slate-600"}` }, i && drop > 0 ? `${drop} not yet here` : "\u00a0")));
    });
    $("staged-count").textContent = o.staged_files;
    barList($("by-state"), o.states, { REJECTED: "bg-red-500", PENDING_HUMAN: "bg-amber-500", INGESTED: "bg-blue-500" });
    barList($("by-kind"), o.kinds);
    barList($("by-backend"), o.backends, { none: "bg-slate-500" });
    const hist = clear($("histogram"));
    const hm = Math.max(1, ...o.agreement_histogram);
    o.agreement_histogram.forEach((c, i) => hist.append(h("div", { class: "flex-1 flex flex-col items-center justify-end h-full" },
      h("span", { class: "text-[10px] text-slate-400" }, c || ""),
      h("div", { class: i >= 8 ? "w-full bg-emerald-500" : i >= 5 ? "w-full bg-amber-500" : "w-full bg-red-500", style: `height:${(c / hm) * 80}%` }),
      h("span", { class: "text-[9px] text-slate-600" }, `${i * 10}`))));
    const v = o.validation;
    clear($("validation-chip")).append(h("button", { class: "underline", onclick: () => showTab("validation") },
      `Validation: ${v.by_level.fail} failures, ${v.by_level.warn} warnings across ${o.total} assets`));
  } catch (e) { $("stage-flow").textContent = `Overview failed: ${e.message}`; }
  try {
    const hc = await api("/api/review/health");
    const box = clear($("health"));
    hc.checks.forEach((c) => box.append(h("div", { class: "flex justify-between gap-2" },
      h("span", { class: `lvl-${c.level}` }, `${c.level === "ok" ? "\u25cf" : c.level === "warn" ? "\u25b2" : "\u2715"} ${c.name}`),
      h("span", { class: "text-slate-500 text-right" }, c.detail))));
  } catch (e) { $("health").textContent = `Health failed: ${e.message}`; }
}
function addEvent(text, t) {
  const box = $("events");
  const cls = /\[ERROR\]|error|failed/i.test(text) ? "text-red-400" : /\[SYSTEM\]/.test(text) ? "text-sky-400" : "";
  box.append(h("div", { class: cls }, `${t ? new Date(t).toLocaleTimeString() + " " : ""}${text}`));
  while (box.childElementCount > 500) box.firstChild.remove();
  box.scrollTop = box.scrollHeight;
}
async function startEvents() {
  try { (await api("/api/review/events")).events.forEach((e) => addEvent(e.msg, e.t)); } catch (_) {}
  const connect = () => {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/logs`);
    ws.onopen = () => { $("ws-state").textContent = "(live)"; };
    ws.onmessage = (e) => { addEvent(e.data); if (currentTab === "pipeline") schedulePipelineRefresh(); };
    ws.onclose = () => { $("ws-state").textContent = "(reconnecting)"; setTimeout(connect, 2000); };
  };
  connect();
}
let refreshTimer = null;
function schedulePipelineRefresh() { clearTimeout(refreshTimer); refreshTimer = setTimeout(loadPipeline, 1500); }

// ---- Review tab
["f-state", "f-kind", "f-issues"].forEach((id) => $(id).addEventListener("change", loadAssets));
let qTimer; $("f-q").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(loadAssets, 300); });

async function loadAssets(keepSelection = false) {
  const p = new URLSearchParams();
  if ($("f-state").value) p.set("state", $("f-state").value);
  if ($("f-kind").value) p.set("kind", $("f-kind").value);
  if ($("f-q").value) p.set("q", $("f-q").value);
  if ($("f-issues").checked) p.set("issues", "true");
  try { assets = (await api(`/api/review/assets?${p}`)).assets; } catch (e) { $("asset-list").textContent = e.message; return; }
  $("asset-count").textContent = `${assets.length} assets`;
  renderList();
  if (!keepSelection || !assets.some((a) => a.asset_id === selectedId)) { if (assets.length) selectAsset(assets[0].asset_id); else { selectedId = null; clear($("detail")).append(h("div", { class: "text-slate-500 italic p-6" }, "Nothing matches the current filters.")); } }
}
function renderList() {
  const list = clear($("asset-list"));
  assets.forEach((a) => list.append(h("div", { class: `p-1.5 rounded cursor-pointer hover:bg-slate-800 ${a.asset_id === selectedId ? "row-sel" : ""}`, "data-id": a.asset_id, onclick: () => selectAsset(a.asset_id) },
    h("div", { class: "flex justify-between" }, h("span", { class: "font-mono truncate" }, a.filename),
      h("span", { class: scoreClass(a.score) }, pct(a.score))),
    h("div", { class: "text-[10px] text-slate-500 flex gap-2" },
      h("span", {}, a.kind), h("span", {}, a.state), h("span", {}, a.backend), a.gps ? h("span", {}, "\ud83d\udccd") : h("span", { class: "text-amber-500" }, "no GPS"),
      a.fail ? h("span", { class: "lvl-fail" }, `${a.fail} fail`) : null, a.warn ? h("span", { class: "lvl-warn" }, `${a.warn} warn`) : null))));
}
function step(delta) {
  const i = assets.findIndex((a) => a.asset_id === selectedId);
  const n = assets[Math.min(assets.length - 1, Math.max(0, i + delta))];
  if (n) selectAsset(n.asset_id);
}
function jsonBlock(title, data, extra) {
  return h("div", { class: "bg-black border border-slate-800 rounded p-2 min-w-0" },
    h("h4", { class: "text-[10px] uppercase font-bold text-slate-500 mb-1" }, title),
    extra || null,
    h("pre", { class: "text-[11px] text-slate-300 max-h-64 overflow-auto" }, data ? JSON.stringify(data, null, 2) : "Not produced yet"));
}
function section(title, ...kids) {
  return h("div", { class: "bg-slate-900 border border-slate-800 rounded p-3" }, h("h3", { class: "text-xs uppercase font-bold text-slate-500 mb-2" }, title), ...kids);
}
function kv(rows) {
  return h("table", { class: "w-full text-xs" }, h("tbody", {}, rows.map(([k, v, warn]) =>
    h("tr", { class: "border-b border-slate-800/60" }, h("td", { class: "py-0.5 pr-2 text-slate-500 whitespace-nowrap" }, k), h("td", { class: `py-0.5 font-mono break-all ${warn ? "lvl-warn" : ""}` }, v === null || v === undefined || v === "" ? "N/A" : String(v))))));
}
function jumpLink(id, label) { return h("button", { class: "underline text-sky-400 mr-2 font-mono", onclick: () => { selectedId = id; $("f-state").value = ""; loadAssets(true).then(() => selectAsset(id)); } }, label || id); }

async function selectAsset(id) {
  selectedId = id;
  renderList();
  const box = clear($("detail"));
  box.append(h("div", { class: "text-slate-500 p-4" }, "Loading..."));
  let d;
  try { d = await api(`/api/review/assets/${encodeURIComponent(id)}`); } catch (e) { box.textContent = e.message; return; }
  if (selectedId !== id) return;
  const a = d.asset, ev = d.evaluations[0];
  clear(box);

  // Header + decision
  const notes = h("textarea", { class: "w-full bg-slate-800 rounded p-1 text-xs", rows: 2, placeholder: "Reviewer notes (stored in human_corrections)" });
  const descEdit = h("textarea", { class: "w-full bg-slate-800 rounded p-1 text-xs", rows: 3 }); descEdit.value = a.describes || "";
  const altEdit = h("input", { class: "w-full bg-slate-800 rounded p-1 text-xs" }); altEdit.value = a.alt || "";
  const tagsEdit = h("input", { class: "w-full bg-slate-800 rounded p-1 text-xs" }); tagsEdit.value = (a.service_tags || []).join(", ");
  const decide = async (action) => {
    const body = { action, notes: notes.value };
    if (action === "approve") {
      if (descEdit.value !== (a.describes || "")) body.describes = descEdit.value;
      if (altEdit.value !== (a.alt || "")) body.alt = altEdit.value;
      const tags = tagsEdit.value.split(",").map((t) => t.trim()).filter(Boolean);
      if (tags.join(",") !== (a.service_tags || []).join(",")) body.service_tags = tags;
    }
    try { await api(`/api/review/assets/${encodeURIComponent(id)}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
    catch (e) { alert(`Decision failed: ${e.message}`); return; }
    const idx = assets.findIndex((x) => x.asset_id === id);
    await loadAssets(true);
    const next = assets[Math.min(idx, assets.length - 1)];
    if (next && next.asset_id !== id) selectAsset(next.asset_id); else if (next) selectAsset(id);
  };
  const btn = (label, cls, action) => h("button", { class: `${cls} px-3 py-1 rounded text-xs font-bold uppercase`, onclick: () => decide(action) }, label);
  box.append(h("div", { class: "bg-slate-900 border border-slate-800 rounded p-3 flex justify-between items-center" },
    h("div", {}, h("div", { class: "font-mono text-emerald-400 font-bold" }, a.asset_id), h("div", { class: "text-xs text-slate-500" }, `${a.original_filename} \u00b7 ${a.media_kind} \u00b7 ${a.workflow_state} \u00b7 AI source: ${d.backend}`)),
    h("div", { class: "flex items-center gap-3" }, h("div", { class: "text-center" }, h("div", { class: "text-[10px] uppercase text-slate-500" }, "AI agreement"), h("div", { class: `text-xl font-bold ${scoreClass(a.agreement_score)}` }, pct(a.agreement_score))),
      btn("Approve (A)", "bg-emerald-600 text-slate-950", "approve"), btn("Rework (W)", "bg-blue-600", "rework"), btn("Reject (R)", "bg-red-700", "reject"))));

  // Stage timeline
  box.append(section("Pipeline stages for this asset", h("div", { class: "flex gap-1" }, d.stages.map((s) =>
    h("div", { class: `flex-1 text-center rounded p-1 text-[10px] ${s.status === "done" ? "bg-emerald-900/50 text-emerald-300" : s.status === "pending" ? "bg-slate-800 text-slate-500" : "bg-red-900/50 text-red-300"}` }, h("div", { class: "uppercase" }, s.stage), h("div", { class: "font-bold" }, s.status))))));

  // Findings
  box.append(section("Validation findings", d.findings.length ? h("ul", { class: "text-xs space-y-0.5" }, d.findings.map((f) => h("li", { class: `lvl-${f.level}` }, `[${f.level.toUpperCase()}] ${f.message}`))) : h("div", { class: "text-xs lvl-ok" }, "\u2713 No problems detected for this asset.")));

  // Preview + EXIF + GPS
  const preview = h("div", { class: "bg-black rounded flex items-center justify-center min-h-[200px]" }, h("span", { class: "text-slate-600 text-xs" }, "Loading preview..."));
  const isVideo = /\.(mp4|m4v|mov)$/i.test(a.original_filename);
  const media = isVideo ? h("video", { src: d.media_url, controls: true, class: "max-h-[420px] max-w-full" }) : h("img", { src: d.media_url, alt: a.alt || a.original_filename, class: "max-h-[420px] max-w-full object-contain" });
  const noPreview = () => clear(preview).append(h("span", { class: "text-amber-500 text-xs p-4" }, "No browser preview (format not supported or file not on disk)."));
  media.addEventListener("error", noPreview);
  clear(preview).append(media);

  const exifBox = h("div", { class: "text-xs text-slate-500" }, "Reading EXIF from file...");
  const gps = a.latitude && a.longitude && !(Number(a.latitude) === 0 && Number(a.longitude) === 0);
  box.append(h("div", { class: "grid grid-cols-2 gap-3" }, section("Media", preview),
    section("Metadata (EXIF stored in database)", kv([["Captured", a.captured_at], ["Sensor", a.sensor_model], ["Focal length", a.focal_length], ["Gimbal pitch", a.gimbal_pitch],
      ["Altitude (m)", a.altitude_meters], ["Latitude", gps ? fmt(a.latitude) : "N/A", !gps], ["Longitude", gps ? fmt(a.longitude) : "N/A", !gps], ["SHA-256", a.original_sha256], ["Source path", a.source_path], ["Project / shoot", a.shoot_id]]),
      gps ? h("a", { class: "text-sky-400 underline text-xs", target: "_blank", rel: "noopener", href: `https://www.openstreetmap.org/?mlat=${a.latitude}&mlon=${a.longitude}#map=17/${a.latitude}/${a.longitude}` }, "Open location in OpenStreetMap") : null,
      d.shoot ? h("div", { class: "text-xs text-slate-400 mt-1" }, `Shoot "${d.shoot.project_name}" centroid ${fmt(d.shoot.centroid_lat, 5)}, ${fmt(d.shoot.centroid_lon, 5)} (radius ${d.shoot.radius_meters} m)`) : null,
      h("details", { class: "mt-2" }, h("summary", { class: "cursor-pointer text-xs text-slate-400" }, "Live EXIF cross-check from file"), exifBox))));
  api(`/api/review/assets/${encodeURIComponent(id)}/exif`).then((x) => {
    if (selectedId !== id) return;
    clear(exifBox);
    if (!x.available) { exifBox.append(h("div", { class: "text-amber-500" }, `Unavailable${x.error ? `: ${x.error}` : " (file not found)"}`)); return; }
    exifBox.append(h("pre", { class: "text-[10px] max-h-64 overflow-auto text-slate-300" }, Object.entries(x.live).map(([k, v]) => `${k}: ${v}`).join("\n")));
  }).catch((e) => { clear(exifBox).append(e.message); });

  // AI outputs
  const agreeNote = ev ? h("div", { class: "text-[10px] text-slate-500 mb-1" }, `Evaluated ${ev.evaluated_at || ""} \u00b7 ${d.evaluations.length} evaluation run(s)`) : null;
  box.append(section("AI review (internal cross-check)", agreeNote, h("div", { class: "grid grid-cols-3 gap-2" },
    jsonBlock("1A Screener", ev && ev.screener_output), jsonBlock("1B Verifier", ev && ev.verifier_output), jsonBlock("1C Judge", ev && ev.judge_output)),
    ev && ev.deep_output ? h("div", { class: "mt-2" }, jsonBlock("Cloud deep pass", ev.deep_output)) : null));
  if (d.evaluations.length > 1) box.append(section("Earlier evaluation runs", h("pre", { class: "text-[10px] max-h-40 overflow-auto" }, d.evaluations.slice(1).map((e) => `${e.evaluated_at} score=${e.agreement_score}`).join("\n"))));

  // Editable result
  box.append(section("Final description (edit before approving)", h("label", { class: "text-[10px] text-slate-500" }, "Describes"), descEdit, h("label", { class: "text-[10px] text-slate-500" }, "Alt text"), altEdit, h("label", { class: "text-[10px] text-slate-500" }, "Service tags (comma separated)"), tagsEdit, h("div", { class: "mt-2" }, notes)));

  // Grouping
  const groupBits = [];
  if (d.model_group) groupBits.push(h("div", { class: "mb-2" }, h("div", { class: "text-xs text-slate-400 mb-1" }, `Model/map identifier "${d.model_group.key}" \u2014 ${d.model_group.parts.length} part(s)`), d.model_group.parts.map((p) => jumpLink(p.asset_id, `${p.filename} (${p.state})`))));
  groupBits.push(h("div", { class: "mb-2" }, h("div", { class: "text-xs text-slate-400 mb-1" }, a.shoot_id ? `Same project/shoot ${a.shoot_id} (${d.same_shoot.length})` : "Not assigned to a project/shoot"), d.same_shoot.slice(0, 30).map((s) => jumpLink(s.asset_id, s.filename))));
  groupBits.push(h("div", {}, h("div", { class: "text-xs text-slate-400 mb-1" }, `Nearby geotagged media within the grouping radius (${d.nearby.length})`), d.nearby.slice(0, 30).map((n) => jumpLink(n.asset_id, `${n.asset_id.slice(-8)} \u00b7 ${n.distance_m} m`))));
  box.append(section("Related media / grouping", groupBits));

  if (d.copies.length) box.append(section("Derivative copies", kv(d.copies.map((c) => [`${c.size_tier} ${c.format}`, `${c.width}x${c.height} \u00b7 ${c.byte_size} B \u00b7 ${c.r2_storage_key}`]))));
  if (d.human_corrections.length) box.append(section("Human correction history", h("pre", { class: "text-[10px]" }, JSON.stringify(d.human_corrections, null, 2))));
}
document.addEventListener("keydown", (e) => {
  if (currentTab !== "review" || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || e.ctrlKey || e.metaKey) return;
  const k = e.key.toLowerCase();
  if (k === "j") step(1); else if (k === "k") step(-1);
  else if (["a", "r", "w"].includes(k)) {
    const label = { a: "Approve", r: "Reject", w: "Rework" }[k];
    [...document.querySelectorAll("#detail button")].find((b) => b.textContent.startsWith(label))?.click();
  }
});

// ---- Groups tab
async function loadGroups() {
  let g;
  try { g = await api("/api/review/groups"); } catch (e) { $("group-models").textContent = e.message; return; }
  $("radius").textContent = g.radius_m;
  const goto = (id) => { showTab("review"); $("f-state").value = ""; loadAssets(true).then(() => selectAsset(id)); };
  const chips = (ids) => ids.map((id) => h("button", { class: "underline text-sky-400 font-mono mr-2 text-xs", onclick: () => goto(id) }, g.assets[id]?.filename || id));
  const card = (title, sub, ids, cls = "") => h("div", { class: `bg-slate-800/50 rounded p-2 mb-2 ${cls}` }, h("div", { class: "text-xs font-bold" }, title, h("span", { class: "text-slate-500 font-normal ml-2" }, sub)), h("div", { class: "mt-1" }, chips(ids)));
  clear($("group-models")).append(section("Models / maps with shared identifier", g.groups.models.length ? g.groups.models.map((m) => card(m.key, `${m.asset_ids.length} part(s)${m.single_part ? " \u2014 only one part found, others may be missing or named differently" : ""}`, m.asset_ids, m.single_part ? "border border-amber-700" : "")) : h("div", { class: "text-xs text-slate-600" }, "No 3D models or maps ingested.")));
  clear($("group-shoots")).append(section("Projects / shoots", g.groups.shoots.map((s) => card(s.key, `${s.asset_ids.length} asset(s)${s.info ? ` \u00b7 ${s.info.project_name}` : ""}`, s.asset_ids.slice(0, 40), s.key === "(unassigned)" ? "border border-amber-700" : ""))));
  clear($("group-locations")).append(section("Location clusters", g.groups.locations.length ? g.groups.locations.map((l) => card(`${fmt(l.center.lat, 5)}, ${fmt(l.center.lon, 5)}`, `${l.asset_ids.length} asset(s) \u00b7 shoots: ${l.shoots.join(", ")}`, l.asset_ids.slice(0, 40))) : h("div", { class: "text-xs text-slate-600" }, "No geotagged media.")));
  drawMap(g);
}
function drawMap(g) {
  const pts = Object.values(g.assets).filter((a) => a.gps);
  const box = clear($("map"));
  if (!pts.length) { box.append(h("div", { class: "text-xs text-slate-600" }, "No geotagged assets to plot.")); return; }
  const lats = pts.map((p) => p.lat), lons = pts.map((p) => p.lon);
  const [minLa, maxLa, minLo, maxLo] = [Math.min(...lats), Math.max(...lats), Math.min(...lons), Math.max(...lons)];
  const W = 900, H = 320, pad = 20;
  const sx = (lo) => pad + (maxLo === minLo ? 0.5 : (lo - minLo) / (maxLo - minLo)) * (W - 2 * pad);
  const sy = (la) => H - pad - (maxLa === minLa ? 0.5 : (la - minLa) / (maxLa - minLa)) * (H - 2 * pad);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg"); svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("class", "w-full bg-black rounded");
  const colors = { APPROVED: "#34d399", PENDING_HUMAN: "#fbbf24", INGESTED: "#60a5fa", REJECTED: "#f87171" };
  pts.forEach((p) => {
    const c = document.createElementNS(ns, "circle");
    c.setAttribute("cx", sx(p.lon)); c.setAttribute("cy", sy(p.lat)); c.setAttribute("r", 5); c.setAttribute("fill", colors[p.state] || "#94a3b8"); c.setAttribute("fill-opacity", "0.75");
    const t = document.createElementNS(ns, "title"); t.textContent = `${p.filename} (${p.state}) ${fmt(p.lat, 5)}, ${fmt(p.lon, 5)}`; c.append(t);
    c.style.cursor = "pointer"; c.addEventListener("click", () => { showTab("review"); $("f-state").value = ""; loadAssets(true).then(() => selectAsset(p.asset_id)); });
    svg.append(c);
  });
  box.append(svg, h("div", { class: "text-[10px] text-slate-500 mt-1" }, `Relative plot of ${pts.length} geotagged assets (green approved, amber pending, blue ingested, red rejected). Extent: ${fmt(minLa, 5)}..${fmt(maxLa, 5)} lat, ${fmt(minLo, 5)}..${fmt(maxLo, 5)} lon.`));
}

// ---- Validation tab
async function loadValidation() {
  let v;
  try { v = await api("/api/review/validation"); } catch (e) { $("validation-rows").textContent = e.message; return; }
  const s = clear($("validation-summary"));
  for (const l of ["fail", "warn", "info"]) s.append(h("span", { class: `px-2 py-1 bg-slate-900 rounded lvl-${l}` }, `${l}: ${v.summary.by_level[l]}`));
  s.append(h("span", { class: "px-2 py-1 bg-slate-900 rounded text-slate-400" }, `${v.summary.assets_with_failures} assets with failures`));
  const body = clear($("validation-rows"));
  if (!v.items.length) body.append(h("tr", {}, h("td", { colspan: 5, class: "p-3 lvl-ok" }, "\u2713 All checks passed for every asset.")));
  v.items.forEach((i) => body.append(h("tr", { class: "border-b border-slate-800 hover:bg-slate-900 cursor-pointer", onclick: () => { showTab("review"); $("f-state").value = ""; loadAssets(true).then(() => selectAsset(i.asset_id)); } },
    h("td", { class: `p-1 font-bold lvl-${i.level}` }, i.level), h("td", { class: "font-mono" }, i.code), h("td", { class: "font-mono" }, i.filename), h("td", {}, i.state), h("td", { class: "text-slate-400" }, i.message))));
}

startEvents();
loadPipeline();
setInterval(() => { if (currentTab === "pipeline") loadPipeline(); }, 15000);
