// BAM Review Console - intelligence features (Explore tab, rich review detail, split tool, dynamic settings).
// Loaded after review.js; shares its top-level helpers. All dynamic text goes through h()/textContent.
const API = "/api/review";
const JSONH = { "Content-Type": "application/json" };
const ta = "w-full bg-slate-800 rounded p-1 text-xs";
const inp = "bg-slate-800 rounded p-1 text-xs";
const bPrimary = "bg-emerald-600 hover:bg-emerald-500 text-slate-950 rounded px-3 py-1 text-xs font-bold";
const bNeutral = "bg-slate-700 hover:bg-slate-600 rounded px-3 py-1 text-xs font-bold";
const mediaUrl = (id) => `${API}/assets/${encodeURIComponent(id)}/media`;
const post = (path, body) => api(`${API}${path}`, { method: "POST", headers: JSONH, body: JSON.stringify(body || {}) });
const gotoAsset = (id) => { showTab("review"); $("f-state").value = ""; loadAssets(true).then(() => selectAsset(id)); };
const badge = (text, cls) => h("span", { class: `inline-block px-2 py-0.5 rounded text-[10px] font-bold uppercase ${cls}` }, text);
const arr = (v) => (Array.isArray(v) ? v : v === null || v === undefined || v === "" ? [] : [v]);
const show = (v) => (v === null || v === undefined || v === "" ? null : Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : String(v));
function setStatus(el, text, ok) { el.className = `text-xs ${ok === false ? "text-red-400" : ok ? "text-emerald-400" : "text-slate-400"}`; el.textContent = text; }
function lazyImg(id, cls = "h-16 w-16") {
  return h("img", { src: mediaUrl(id), loading: "lazy", alt: `Asset ${id}`, class: `${cls} object-cover rounded bg-black` });
}

// ---- Indicator badges + readable output
function indicatorBadges(ind) {
  const out = [];
  if (!ind) return out;
  const m = ind.model_candidate, p = ind.progression, v = ind.view_group;
  if (m) out.push(badge(`3D model ${m.ready ? "ready" : "candidate"} (${m.set_size})`, m.ready ? "bg-emerald-900 text-emerald-300" : "bg-amber-900 text-amber-300"));
  if (p && p.indicator && p.indicator !== "none") out.push(badge(`${p.indicator === "before_after" ? "Before / after" : "Site progression"} (${p.visit_count} visits)`, "bg-sky-900 text-sky-300"));
  if (v) out.push(badge(`View group (${v.size})`, "bg-violet-900 text-violet-300"));
  return out;
}
function readableRows(obj) {
  return Object.entries(obj || {}).filter(([, v]) => show(v) !== null).map(([k, v]) => [k.replace(/_/g, " "), show(v)]);
}
function outputSections(fo, names = ["classification", "content", "photogrammetry", "view", "quality"]) {
  const secs = [];
  for (const n of names) {
    const rows = readableRows(fo && fo[n]);
    if (rows.length) secs.push(h("div", { class: "bg-slate-900 border border-slate-800 rounded p-2 min-w-0" }, h("h4", { class: "text-[10px] uppercase font-bold text-slate-500 mb-1" }, n), kv(rows)));
  }
  return secs.length ? secs : [h("div", { class: "text-xs text-slate-600" }, "No structured output yet.")];
}

// ---- Video frames
async function frameStrip(id, video) {
  const box = h("div", { class: "text-xs text-slate-500" }, "Loading frames...");
  const viewer = h("div", { class: "mt-2" });
  try {
    const { frames } = await api(`${API}/assets/${encodeURIComponent(id)}/frames`);
    clear(box);
    if (!frames.length) { box.textContent = "No analysis frames stored for this video."; return h("div", {}, box); }
    let activeBtn = null;
    const open = (f, btn) => {
      if (activeBtn) activeBtn.classList.remove("ring-2");
      activeBtn = btn; btn.classList.add("ring-2");
      if (video && Number.isFinite(Number(f.time_s))) { try { video.currentTime = Number(f.time_s); } catch (_) {} }
      clear(viewer).append(h("div", { class: "grid grid-cols-2 gap-3" },
        h("img", { src: f.image_url, alt: `Frame ${f.frame_index} at ${f.time_s}s`, class: "max-h-[360px] max-w-full object-contain bg-black rounded" }),
        h("div", {}, h("div", { class: "text-xs text-slate-400 mb-1" }, `Frame ${f.frame_index} \u00b7 ${f.pass} pass \u00b7 ${fmt(f.time_s, 2)} s`), ...outputSections(f.output))));
    };
    for (const [pass, label] of [["base", "Base pass"], ["detail", "Detail pass"]]) {
      const list = frames.filter((f) => f.pass === pass);
      if (!list.length) continue;
      box.append(h("div", { class: "mb-2" }, h("div", { class: "text-[10px] uppercase font-bold text-slate-500 mb-1" }, `${label} (${list.length})`),
        h("div", { class: "flex gap-1 flex-wrap" }, list.map((f) => {
          const b = h("button", { type: "button", class: "text-center rounded ring-emerald-400 focus:outline-none focus:ring-2", title: `Frame ${f.frame_index}` },
            h("img", { src: f.thumb_url, loading: "lazy", alt: `Frame ${f.frame_index}`, class: "h-14 w-20 object-cover rounded bg-black" }),
            h("div", { class: "text-[9px] text-slate-400" }, `${fmt(f.time_s, 1)}s`));
          b.addEventListener("click", () => open(f, b));
          return b;
        }))));
    }
  } catch (e) { box.textContent = `Frames unavailable: ${e.message}`; }
  return h("div", {}, box, viewer);
}

// ---- Review detail (replaces review.js selectAsset)
selectAsset = async function (id) {
  selectedId = id;
  renderList();
  const box = clear($("detail"));
  box.append(h("div", { class: "text-slate-500 p-4" }, "Loading..."));
  let d;
  try { d = await api(`${API}/assets/${encodeURIComponent(id)}`); } catch (e) { box.textContent = e.message; return; }
  if (selectedId !== id) return;
  const a = d.asset, ev = d.evaluations[0];
  const fo = d.final_output || {};
  const isVideo = a.media_kind === "video" || /\.(mp4|m4v|mov)$/i.test(a.original_filename || "");
  const ind = d.indicators || fo.indicators;
  clear(box);

  // Decision controls
  const notes = h("textarea", { class: ta, rows: 2, "aria-label": "Notes", placeholder: "Notes about this file (saved with the file)" }); notes.value = d.notes || "";
  const feedback = h("textarea", { class: ta, rows: 2, "aria-label": "Feedback for model training", placeholder: "Feedback for model training (what the AI got wrong/right)" });
  const reprocess = h("textarea", { class: ta, rows: 2, "aria-label": "Reprocess feedback", placeholder: "Reprocess feedback: what should the models do differently?" });
  const train = h("input", { type: "checkbox", id: "chk-train" }); train.checked = !!d.training_candidate;
  const editor = h("div", { class: "hidden mt-2 bg-slate-950 border border-slate-800 rounded p-2" });
  let editedFo = null;

  const decide = async (action, extra = {}) => {
    const body = { action, notes: notes.value, feedback: feedback.value, training_candidate: train.checked, ...extra };
    if (action === "rework" && !body.reprocess_feedback) body.reprocess_feedback = reprocess.value;
    try { await post(`/assets/${encodeURIComponent(id)}/decision`, body); }
    catch (e) { alert(`Decision failed: ${e.message}`); return; }
    const idx = assets.findIndex((x) => x.asset_id === id);
    await loadAssets(true);
    const next = assets[Math.min(idx, assets.length - 1)];
    if (next && next.asset_id !== id) selectAsset(next.asset_id); else if (next) selectAsset(id);
  };
  const btn = (label, cls, fn) => h("button", { type: "button", class: `${cls} px-3 py-1 rounded text-xs font-bold uppercase`, onclick: fn }, label);

  const trainBadge = d.training_candidate ? badge("Training data", "bg-emerald-900 text-emerald-300") : badge("Not flagged for training", "bg-slate-800 text-slate-400");
  box.append(h("div", { class: "bg-slate-900 border border-slate-800 rounded p-3 space-y-2" },
    h("div", { class: "flex justify-between items-start gap-3" },
      h("div", { class: "min-w-0" },
        h("div", { class: "font-mono text-emerald-400 font-bold break-all" }, d.identifier || a.asset_id, d.identifier_locked ? h("span", { class: "ml-2 text-amber-400", title: "Identifier is locked; edits never change it" }, "\ud83d\udd12 locked") : null),
        h("div", { class: "text-xs text-slate-500" }, `${a.original_filename} \u00b7 ${a.media_kind} \u00b7 ${a.workflow_state} \u00b7 AI source: ${d.backend}`),
        h("div", { class: "flex gap-1 flex-wrap mt-1" }, indicatorBadges(ind), trainBadge, fo.human && fo.human.edited ? badge("Edited", "bg-blue-900 text-blue-300") : null)),
      h("div", { class: "text-center shrink-0" }, h("div", { class: "text-[10px] uppercase text-slate-500" }, "AI agreement"), h("div", { class: `text-xl font-bold ${scoreClass(a.agreement_score)}` }, pct(a.agreement_score)))),
    h("label", { class: "block text-[10px] text-slate-500" }, "Notes (saved with the file)", notes),
    h("label", { class: "block text-[10px] text-slate-500" }, "Feedback (for model training)", feedback),
    h("label", { class: "block text-[10px] text-slate-500" }, "Reprocess feedback (used only by 'Reprocess with my feedback')", reprocess),
    h("div", { class: "flex items-center gap-3 flex-wrap" },
      h("label", { class: "flex items-center gap-1 text-xs text-slate-300", for: "chk-train" }, train, "Good training data"),
      btn("Approve (A)", "bg-emerald-600 text-slate-950", () => decide("approve")),
      btn("Edit data", "bg-slate-700", () => { editor.classList.toggle("hidden"); if (!editor.firstChild) buildEditor(); }),
      btn("Reprocess with my feedback (W)", "bg-blue-600", () => decide("rework", { reprocess_feedback: reprocess.value })),
      btn("Reject (R)", "bg-red-700", () => decide("reject"))),
    editor,
    arr(d.feedback).length ? h("div", {}, h("div", { class: "text-[10px] uppercase font-bold text-slate-500" }, "Previous feedback"),
      h("ul", { class: "text-xs text-slate-400 list-disc ml-4" }, arr(d.feedback).map((f) => h("li", {}, typeof f === "string" ? f : f.text || f.feedback || JSON.stringify(f))))) : null));

  function buildEditor() {
    const base = JSON.parse(JSON.stringify(fo));
    base.content = base.content || {}; base.classification = base.classification || {}; base.view = base.view || {}; base.photogrammetry = base.photogrammetry || {};
    if (!base.content.description && a.describes) base.content.description = a.describes;
    if (!base.content.alt_text && a.alt) base.content.alt_text = a.alt;
    if (!(base.content.tags || []).length && (a.service_tags || []).length) base.content.tags = a.service_tags;
    const json = h("textarea", { class: `${ta} font-mono`, rows: 10, "aria-label": "Advanced: full JSON" });
    const err = h("div", { class: "text-xs text-red-400" });
    const fields = [
      ["Description", "content", "description", "area"], ["Alt text", "content", "alt_text", "text"], ["Tags (comma separated)", "content", "tags", "list"],
      ["Primary subjects", "content", "primary_subjects", "list"], ["Objects", "content", "objects", "list"], ["Structures", "content", "structures", "list"],
      ["Shot type", "classification", "shot_type", "text"], ["Genre", "classification", "media_genre", "text"], ["View label", "view", "label", "text"],
      ["Suitable for 3D model", "photogrammetry", "suitable_for_model", "bool"]];
    const els = [];
    const parse = () => { try { err.textContent = ""; return JSON.parse(json.value); } catch (e) { err.textContent = `Invalid JSON: ${e.message}`; return null; } };
    const write = (o) => { json.value = JSON.stringify(o, null, 2); };
    const fill = (o) => els.forEach(({ el, sec, key, type }) => {
      const v = o && o[sec] ? o[sec][key] : undefined;
      if (type === "bool") el.checked = !!v; else el.value = type === "list" ? arr(v).join(", ") : v ?? "";
    });
    const grid = h("div", { class: "grid grid-cols-2 gap-2" });
    for (const [label, sec, key, type] of fields) {
      const el = type === "area" ? h("textarea", { class: ta, rows: 3 }) : type === "bool" ? h("input", { type: "checkbox" }) : h("input", { class: ta });
      els.push({ el, sec, key, type });
      el.addEventListener(type === "bool" ? "change" : "input", () => {
        const o = parse(); if (!o) return;
        o[sec] = o[sec] || {};
        o[sec][key] = type === "bool" ? el.checked : type === "list" ? el.value.split(",").map((t) => t.trim()).filter(Boolean) : el.value;
        write(o);
      });
      grid.append(h("label", { class: `block text-[10px] text-slate-500 ${type === "area" ? "col-span-2" : ""}` }, label, type === "bool" ? h("span", { class: "ml-2" }, el) : el));
    }
    json.addEventListener("input", () => { const o = parse(); if (o) fill(o); });
    write(base); fill(base);
    const save = h("button", { type: "button", class: bPrimary }, "Save edits & approve");
    save.addEventListener("click", () => {
      const o = parse(); if (!o || typeof o !== "object" || Array.isArray(o)) { err.textContent = err.textContent || "Final output must be a JSON object."; return; }
      editedFo = o;
      const extra = { final_output: o };
      const c = o.content || {};
      if (c.description !== undefined && c.description !== (a.describes || "")) extra.describes = c.description;
      if (c.alt_text !== undefined && c.alt_text !== (a.alt || "")) extra.alt = c.alt_text;
      if (c.tags && c.tags.join(",") !== (a.service_tags || []).join(",")) extra.service_tags = c.tags;
      decide("approve", extra);
    });
    editor.append(h("div", { class: "text-[10px] text-slate-500 mb-1" }, "Edits are stored with the original output so before/after is recorded. The identifier is locked and cannot be changed."),
      grid, h("details", { class: "mt-2" }, h("summary", { class: "cursor-pointer text-xs text-slate-400" }, "Advanced: full JSON"), json), err, h("div", { class: "mt-2" }, save));
  }

  // Stage timeline + findings
  box.append(section("Pipeline stages for this asset", h("div", { class: "flex gap-1" }, d.stages.map((s) =>
    h("div", { class: `flex-1 text-center rounded p-1 text-[10px] ${s.status === "done" ? "bg-emerald-900/50 text-emerald-300" : s.status === "pending" ? "bg-slate-800 text-slate-500" : "bg-red-900/50 text-red-300"}` }, h("div", { class: "uppercase" }, s.stage), h("div", { class: "font-bold" }, s.status))))));
  box.append(section("Validation findings", d.findings.length ? h("ul", { class: "text-xs space-y-0.5" }, d.findings.map((f) => h("li", { class: `lvl-${f.level}` }, `[${f.level.toUpperCase()}] ${f.message}`))) : h("div", { class: "text-xs lvl-ok" }, "\u2713 No problems detected for this asset.")));

  // Media (+ frames for video)
  const preview = h("div", { class: "bg-black rounded flex items-center justify-center min-h-[200px]" });
  const media = isVideo ? h("video", { src: d.media_url, controls: true, class: "max-h-[420px] max-w-full" }) : h("img", { src: d.media_url, alt: a.alt || a.original_filename, class: "max-h-[420px] max-w-full object-contain" });
  media.addEventListener("error", () => clear(preview).append(h("span", { class: "text-amber-500 text-xs p-4" }, "No browser preview (format not supported or file not on disk).")));
  preview.append(media);
  const exifBox = h("div", { class: "text-xs text-slate-500" }, "Reading EXIF from file...");
  const hasCoord = (v) => v !== null && v !== undefined && v !== "" && Number.isFinite(Number(v));
  const gps = hasCoord(a.latitude) && hasCoord(a.longitude) && !(Number(a.latitude) === 0 && Number(a.longitude) === 0);
  const mediaSec = section(isVideo ? "Video (original file)" : "Media", preview);
  box.append(h("div", { class: "grid grid-cols-2 gap-3" }, mediaSec,
    section("Metadata (EXIF stored in database)", kv([["Captured", a.captured_at], ["Sensor", a.sensor_model], ["Focal length", a.focal_length], ["Gimbal pitch", a.gimbal_pitch],
      ["Altitude (m)", a.altitude_meters], ["Latitude", gps ? fmt(a.latitude) : "N/A", !gps], ["Longitude", gps ? fmt(a.longitude) : "N/A", !gps], ["SHA-256", a.original_sha256], ["Source path", a.source_path], ["Project / shoot", a.shoot_id], ["Scene group", d.scene_group], ["Frames analysed", d.frame_count]]),
      gps ? h("a", { class: "text-sky-400 underline text-xs", target: "_blank", rel: "noopener", href: `https://www.openstreetmap.org/?mlat=${a.latitude}&mlon=${a.longitude}#map=17/${a.latitude}/${a.longitude}` }, "Open location in OpenStreetMap") : null,
      d.shoot ? h("div", { class: "text-xs text-slate-400 mt-1" }, `Shoot "${d.shoot.project_name}" centroid ${fmt(d.shoot.centroid_lat, 5)}, ${fmt(d.shoot.centroid_lon, 5)} (radius ${d.shoot.radius_meters} m)`) : null,
      h("details", { class: "mt-2" }, h("summary", { class: "cursor-pointer text-xs text-slate-400" }, "Live EXIF cross-check from file"), exifBox))));
  api(`${API}/assets/${encodeURIComponent(id)}/exif`).then((x) => {
    if (selectedId !== id) return;
    clear(exifBox);
    if (!x.available) { exifBox.append(h("div", { class: "text-amber-500" }, `Unavailable${x.error ? `: ${x.error}` : " (file not found)"}`)); return; }
    exifBox.append(h("pre", { class: "text-[10px] max-h-64 overflow-auto text-slate-300" }, Object.entries(x.live).map(([k, v]) => `${k}: ${v}`).join("\n")));
  }).catch((e) => { clear(exifBox).append(e.message); });
  if (isVideo) {
    const strip = h("div", {}, "Loading frames...");
    box.append(section("Frames used for analysis", strip));
    frameStrip(id, media).then((el) => { if (selectedId === id) clear(strip).append(el); });
  }

  // Standardised output
  const v = fo.video;
  box.append(section("Final output (standard record)",
    h("div", { class: "grid grid-cols-2 gap-2" }, ...outputSections(fo)),
    v ? h("div", { class: "mt-2" }, h("h4", { class: "text-[10px] uppercase font-bold text-slate-500 mb-1" }, "video"),
      kv(readableRows({ duration_s: v.duration_s, summary: v.summary })),
      arr(v.timeline).length ? h("ul", { class: "text-xs text-slate-400 list-disc ml-4 mt-1" }, arr(v.timeline).map((t) => h("li", {}, show(t)))) : null) : null,
    ind && ind.model_candidate ? h("div", { class: "text-xs text-slate-400 mt-2" }, `3D model set ${ind.model_candidate.set_key}: ${ind.model_candidate.set_size} photos, ${ind.model_candidate.ready ? "READY" : "not ready"}`) : null,
    h("details", { class: "mt-2" }, h("summary", { class: "cursor-pointer text-xs text-slate-400" }, "Raw JSON"),
      h("pre", { class: "text-[10px] text-slate-300 max-h-96 overflow-auto bg-black rounded p-2" }, d.final_output ? JSON.stringify(d.final_output, null, 2) : "Not produced yet"))));

  // AI outputs
  const agreeNote = ev ? h("div", { class: "text-[10px] text-slate-500 mb-1" }, `Evaluated ${ev.evaluated_at || ""} \u00b7 ${d.evaluations.length} evaluation run(s)`) : null;
  box.append(section("AI review (internal cross-check)", agreeNote, h("div", { class: "grid grid-cols-3 gap-2" },
    jsonBlock("1A Screener", ev && ev.screener_output), jsonBlock("1B Verifier", ev && ev.verifier_output), jsonBlock("1C Judge", ev && ev.judge_output)),
    ev && ev.deep_output ? h("div", { class: "mt-2" }, jsonBlock("Cloud deep pass", ev.deep_output)) : null));
  if (d.evaluations.length > 1) box.append(section("Earlier evaluation runs", h("pre", { class: "text-[10px] max-h-40 overflow-auto" }, d.evaluations.slice(1).map((e) => `${e.evaluated_at} score=${e.agreement_score}`).join("\n"))));

  // Grouping
  const groupBits = [];
  if (d.model_group) groupBits.push(h("div", { class: "mb-2" }, h("div", { class: "text-xs text-slate-400 mb-1" }, `Model/map identifier "${d.model_group.key}" \u2014 ${d.model_group.parts.length} part(s)`), d.model_group.parts.map((p) => jumpLink(p.asset_id, `${p.filename} (${p.state})`))));
  groupBits.push(h("div", { class: "mb-2" }, h("div", { class: "text-xs text-slate-400 mb-1" }, a.shoot_id ? `Same project/shoot ${a.shoot_id} (${d.same_shoot.length})` : "Not assigned to a project/shoot"), d.same_shoot.slice(0, 3 * 10).map((s) => jumpLink(s.asset_id, s.filename))));
  groupBits.push(h("div", {}, h("div", { class: "text-xs text-slate-400 mb-1" }, `Nearby geotagged media within the grouping radius (${d.nearby.length})`), d.nearby.slice(0, 3 * 10).map((n) => jumpLink(n.asset_id, `${n.asset_id.slice(-8)} \u00b7 ${n.distance_m} m`))));
  box.append(section("Related media / grouping", groupBits));
  if (d.copies.length) box.append(section("Derivative copies", kv(d.copies.map((c) => [`${c.size_tier} ${c.format}`, `${c.width}x${c.height} \u00b7 ${c.byte_size} B \u00b7 ${c.r2_storage_key}`]))));
  if (d.human_corrections.length) box.append(section("Human correction history", h("pre", { class: "text-[10px]" }, JSON.stringify(d.human_corrections, null, 2))));
};

// ---- Explore tab
let exploreBuilt = false, shootList = [];
const scopeUi = {};
function scopeParams() {
  const p = new URLSearchParams(), m = scopeUi.mode.value;
  if (m === "location") {
    if (scopeUi.lat.value && scopeUi.lon.value) { p.set("lat", scopeUi.lat.value); p.set("lon", scopeUi.lon.value); }
    if (scopeUi.radius.value) p.set("radius_m", scopeUi.radius.value);
  } else if (m === "shoot" && scopeUi.shoot.value) p.set("shoot_id", scopeUi.shoot.value);
  else if (m === "seed" && scopeUi.seed.value.trim()) p.set("asset_id", scopeUi.seed.value.trim());
  return p;
}
function shootOptions(sel, blank) {
  clear(sel).append(h("option", { value: "" }, blank));
  shootList.forEach((s) => sel.append(h("option", { value: s.shoot_id }, `${s.project_name || s.shoot_id} (${s.asset_count})`)));
}
async function loadShoots() {
  try { shootList = (await api(`${API}/intel/shoots`)).shoots; } catch (_) { shootList = []; }
}
function assetLinks(ids, limit = 8) {
  return h("div", { class: "flex gap-1 flex-wrap mt-1" }, ids.slice(0, limit).map((id) =>
    h("button", { type: "button", class: "rounded focus:ring-2 ring-emerald-400", title: id, onclick: () => gotoAsset(id) }, lazyImg(id))),
    ids.length > limit ? h("span", { class: "text-[10px] text-slate-500 self-center" }, `+${ids.length - limit} more`) : null);
}
function exploreSearchPanel() {
  const q = h("input", { class: `${inp} w-64`, "aria-label": "Search stored output", placeholder: "Search stored descriptions, tags, subjects..." });
  const kind = h("select", { class: inp, "aria-label": "Kind" }, ["", "still", "video", "3d_model"].map((k) => h("option", { value: k }, k || "Any type")));
  const shoot = h("select", { class: inp, "aria-label": "Shoot" });
  const shot = h("input", { class: `${inp} w-32`, "aria-label": "Shot type", placeholder: "Shot type" });
  const genre = h("input", { class: `${inp} w-32`, "aria-label": "Genre", placeholder: "Genre" });
  const status = h("div", { class: "text-xs text-slate-500 mt-1" }), list = h("div", { class: "mt-2 space-y-1" });
  scopeUi.searchShoot = shoot;
  const run = async () => {
    const p = new URLSearchParams({ limit: String(5 * 10) });
    if (q.value.trim()) p.set("q", q.value.trim());
    if (kind.value) p.set("kind", kind.value);
    if (shoot.value) p.set("shoot", shoot.value);
    if (shot.value.trim()) p.set("shot_type", shot.value.trim());
    if (genre.value.trim()) p.set("genre", genre.value.trim());
    setStatus(status, "Searching...");
    try {
      const r = await api(`${API}/intel/search?${p}`);
      setStatus(status, `${r.total} result(s)${r.total > r.results.length ? `, showing ${r.results.length}` : ""}.`);
      clear(list);
      r.results.forEach((x) => list.append(h("div", { class: "flex gap-2 items-start bg-slate-800/50 rounded p-1" },
        h("button", { type: "button", onclick: () => gotoAsset(x.asset_id) }, lazyImg(x.asset_id, "h-12 w-12")),
        h("div", { class: "min-w-0 text-xs" },
          h("button", { type: "button", class: "underline text-sky-400 font-mono text-left break-all", onclick: () => gotoAsset(x.asset_id) }, x.identifier || x.filename),
          h("div", { class: "text-[10px] text-slate-500" }, [x.kind, x.state, x.shot_type, x.genre, x.shoot_id].filter(Boolean).join(" \u00b7 ")),
          h("div", { class: "flex gap-1 flex-wrap" }, indicatorBadges(x.indicators)),
          h("div", { class: "text-slate-300" }, x.snippet || x.description || "")))));
    } catch (e) { setStatus(status, e.message, false); }
  };
  const go = h("button", { type: "submit", class: bPrimary }, "Search");
  const form = h("form", { class: "flex gap-2 flex-wrap items-center", onsubmit: (e) => { e.preventDefault(); run(); } }, q, kind, shoot, shot, genre, go);
  return section("Search", h("p", { class: "text-[10px] text-slate-500 mb-2" }, "Searches the stored output only; nothing is re-run through the models."), form, status, list);
}
function scopePanel() {
  const mode = h("select", { class: inp, "aria-label": "Scope" }, [["location", "By location"], ["shoot", "By shoot"], ["seed", "By seed photo"]].map(([v, l]) => h("option", { value: v }, l)));
  Object.assign(scopeUi, {
    mode, lat: h("input", { type: "number", step: "any", class: `${inp} w-28`, "aria-label": "Latitude", placeholder: "lat" }),
    lon: h("input", { type: "number", step: "any", class: `${inp} w-28`, "aria-label": "Longitude", placeholder: "lon" }),
    radius: h("input", { type: "number", step: "any", min: "0", class: `${inp} w-24`, "aria-label": "Radius in metres", placeholder: "radius m" }),
    shoot: h("select", { class: inp, "aria-label": "Shoot id" }), seed: h("input", { class: `${inp} w-48`, "aria-label": "Seed photo id", placeholder: "asset id" }) });
  const groups = { location: [scopeUi.lat, scopeUi.lon, scopeUi.radius], shoot: [scopeUi.shoot], seed: [scopeUi.seed] };
  const wrap = h("div", { class: "flex gap-2 flex-wrap items-center" }, mode, ...Object.values(groups).flat());
  const sync = () => Object.entries(groups).forEach(([k, els]) => els.forEach((e) => e.classList.toggle("hidden", k !== mode.value)));
  mode.addEventListener("change", sync); sync();
  return wrap;
}
function modelPanel() {
  const out = h("div", { class: "mt-2 space-y-2" }), status = h("div", { class: "text-xs mt-1" });
  const run = h("button", { type: "button", class: bPrimary }, "Find good 3D model sets");
  run.addEventListener("click", async () => {
    setStatus(status, "Searching stored indicators...");
    try {
      const { candidates } = await api(`${API}/intel/model-candidates?${scopeParams()}`);
      setStatus(status, `${candidates.length} candidate set(s).`);
      clear(out);
      candidates.forEach((c) => out.append(h("div", { class: `rounded p-2 border ${c.ready ? "border-emerald-700" : "border-amber-700"} bg-slate-800/40` },
        h("div", { class: "flex gap-2 items-center flex-wrap" }, badge(c.ready ? "READY" : "NOT READY", c.ready ? "bg-emerald-900 text-emerald-300" : "bg-amber-900 text-amber-300"),
          h("span", { class: "font-mono text-xs" }, c.set_key), h("span", { class: "text-xs text-slate-400" }, `score ${fmt(c.score, 2)}`)),
        kv([["Photos", c.stats && c.stats.photo_count], ["Angle coverage (deg)", c.stats && c.stats.angle_coverage_deg], ["Span (hours)", c.stats && c.stats.span_hours], ["Shoots", arr(c.shoot_ids).join(", ")], ["Why", arr(c.reasons).join("; ")], ["Missing", arr(c.missing).join("; "), arr(c.missing).length > 0]]),
        assetLinks(c.asset_ids))));
    } catch (e) { setStatus(status, e.message, false); }
  });
  return section("Find good 3D model sets", run, status, out);
}
function pairView(p) {
  const fig = (id, label) => h("figure", { class: "text-center" }, h("button", { type: "button", onclick: () => gotoAsset(id) }, lazyImg(id, "h-28 w-36")), h("figcaption", { class: "text-[10px] text-slate-500" }, label));
  return h("div", { class: "flex gap-2 items-center" }, fig(p.before_asset_id, "Before"), h("span", { class: "text-xs text-slate-500" }, `\u2192 ${p.days_apart ?? "?"} d`), fig(p.after_asset_id, "After"));
}
function progressionPanel() {
  const out = h("div", { class: "mt-2 space-y-2" }), status = h("div", { class: "text-xs mt-1" });
  const run = h("button", { type: "button", class: bPrimary }, "Find before/after & progression");
  run.addEventListener("click", async () => {
    setStatus(status, "Searching stored indicators...");
    try {
      const { locations } = await api(`${API}/intel/progression?${scopeParams()}`);
      setStatus(status, `${locations.length} location(s).`);
      clear(out);
      locations.forEach((l) => out.append(h("div", { class: "rounded p-2 border border-slate-700 bg-slate-800/40" },
        h("div", { class: "flex gap-2 items-center flex-wrap" },
          l.indicator === "none" ? badge("No progression", "bg-slate-800 text-slate-400") : badge(l.indicator === "before_after" ? "Before / after" : "Site progression", "bg-sky-900 text-sky-300"),
          h("span", { class: "font-mono text-xs" }, l.location_key), l.center ? h("span", { class: "text-[10px] text-slate-500" }, `${fmt(l.center.lat, 5)}, ${fmt(l.center.lon, 5)}`) : null),
        h("div", { class: "text-xs text-slate-400" }, `Visits: ${arr(l.visits).map((v) => `${v.date} (${arr(v.asset_ids).length})`).join(", ") || "none"}`),
        arr(l.view_groups).map((g) => h("div", { class: "mt-2" }, h("div", { class: "text-xs font-bold" }, g.label || g.view_key, h("span", { class: "text-slate-500 font-normal ml-2" }, `${arr(g.visits).length} visit(s)`)),
          arr(g.pairs).length ? h("div", { class: "flex gap-4 flex-wrap mt-1" }, g.pairs.map(pairView)) : h("div", { class: "text-[10px] text-slate-600" }, "No before/after pairs.")))))); 
    } catch (e) { setStatus(status, e.message, false); }
  });
  return section("Find before/after & progression", run, status, out);
}
function maintenancePanel() {
  const status = h("div", { class: "text-xs mt-1" });
  const act = (label, path, fmtOk) => {
    const b = h("button", { type: "button", class: bNeutral }, label);
    b.addEventListener("click", async () => {
      b.disabled = true; setStatus(status, `${label}...`);
      try { setStatus(status, fmtOk(await post(path, {})), true); } catch (e) { setStatus(status, e.message, false); }
      b.disabled = false;
    });
    return b;
  };
  return section("Maintenance", h("div", { class: "flex gap-2" },
    act("Refresh indicators", "/intel/refresh", (r) => `Indicators refreshed: ${r.updated} asset(s) updated.`),
    act("Finalize batch", "/intel/batch/finalize", (r) => `Batch finalized: ${JSON.stringify(r)}`)), status);
}
window.loadExplore = async function () {
  await loadShoots();
  if (!exploreBuilt) {
    exploreBuilt = true;
    const root = $("tab-explore");
    root.append(exploreSearchPanel(), section("Scope for model sets and progression", h("p", { class: "text-[10px] text-slate-500 mb-2" }, "Pick where to look; both finders below use this scope."), scopePanel()), modelPanel(), progressionPanel(), maintenancePanel());
  }
  shootOptions(scopeUi.shoot, "Choose shoot..."); shootOptions(scopeUi.searchShoot, "Any shoot");
};

// ---- Groups tab: split location tool
const baseLoadGroups = loadGroups;
loadGroups = async function () { await baseLoadGroups(); renderSplit().catch(() => {}); };
const splitState = { shoot: "", marks: {} };
async function renderSplit() {
  let host = $("group-split");
  if (!host) { host = h("div", { id: "group-split" }); $("tab-groups").append(host); }
  await loadShoots();
  const g = await api(`${API}/groups`);
  const sel = h("select", { class: inp, "aria-label": "Shoot to split" });
  shootOptions(sel, "Choose shoot..."); sel.value = splitState.shoot;
  const grid = h("div", { class: "flex gap-2 flex-wrap mt-2" }), result = h("div", { class: "text-xs mt-2" });
  const radius = h("input", { type: "number", step: "any", min: "0", class: `${inp} w-24`, "aria-label": "New radius in metres" });
  const nameB = h("input", { class: `${inp} w-48`, "aria-label": "Project name for location B", placeholder: "Name for location B (optional)" });
  const info = h("div", { class: "text-xs text-slate-400 mt-1" });
  const marked = (m) => Object.entries(splitState.marks).filter(([, v]) => v === m).map(([k]) => k);
  const draw = () => {
    clear(grid);
    const entry = g.groups.shoots.find((s) => s.key === splitState.shoot);
    if (!entry) { grid.append(h("div", { class: "text-xs text-slate-600" }, "Select a shoot to list its assets.")); info.textContent = ""; return; }
    entry.asset_ids.forEach((id) => {
      const m = splitState.marks[id];
      const b = h("button", { type: "button", "aria-pressed": m ? "true" : "false", class: `text-center rounded p-1 border-2 ${m === "A" ? "border-sky-400" : m === "B" ? "border-amber-400" : "border-transparent"}`, title: `${(g.assets[id] || {}).filename || id}: click to cycle A / B / none` },
        lazyImg(id, "h-20 w-24"), h("div", { class: "text-[10px] font-bold" }, m ? `Location ${m}` : "unmarked"));
      b.addEventListener("click", () => { const nx = { undefined: "A", A: "B", B: undefined }[m]; if (nx) splitState.marks[id] = nx; else delete splitState.marks[id]; draw(); });
      grid.append(b);
    });
    info.textContent = `A: ${marked("A").length} \u00b7 B: ${marked("B").length} \u00b7 unmarked: ${entry.asset_ids.length - marked("A").length - marked("B").length}`;
  };
  sel.addEventListener("change", () => { splitState.shoot = sel.value; splitState.marks = {}; draw(); });
  const finish = async (fn) => {
    try { const r = await fn(); setStatus(result, `Done: ${JSON.stringify(r)}`, true); splitState.marks = {}; await baseLoadGroups(); renderSplit().catch(() => {}); }
    catch (e) { setStatus(result, e.message, false); }
  };
  const split = h("button", { type: "button", class: bPrimary }, "Split into two shoots");
  split.addEventListener("click", () => {
    const a = marked("A"), b = marked("B");
    if (!splitState.shoot || !a.length || !b.length) { setStatus(result, "Select a shoot and mark at least one photo as A and one as B.", false); return; }
    if (!confirm(`Split shoot ${splitState.shoot} into two shoots (${a.length} photos in A, ${b.length} in B)?\n\nIdentifiers only change their shoot component; everything else about the assets stays the same.`)) return;
    finish(() => post("/intel/shoots/split", { shoot_id: splitState.shoot, group_a: a, group_b: b, project_name_b: nameB.value.trim() || undefined }));
  });
  const rerun = h("button", { type: "button", class: bNeutral }, "Re-run with new radius");
  rerun.addEventListener("click", () => {
    if (!splitState.shoot) { setStatus(result, "Select a shoot first.", false); return; }
    const body = { shoot_id: splitState.shoot }; if (radius.value) body.radius_m = Number(radius.value);
    finish(() => post("/intel/shoots/reassign", body));
  });
  clear(host).append(section("Split location",
    h("p", { class: "text-[10px] text-slate-500 mb-2" }, "Click a photo to mark it Location A, click again for Location B, again to clear."),
    h("div", { class: "flex gap-2 flex-wrap items-center" }, sel, nameB, split, h("label", { class: "text-xs text-slate-400" }, "New radius (m) ", radius), rerun),
    info, grid, result));
  draw();
}

// ---- Settings tab (dynamic from specs) + data & sync
const SETTING_SECTIONS = [
  ["Shoots", /^(shoot_|related_)/], ["3D models", /^model_/], ["Progression & views", /^(progression_|before_after_|view_)/], ["Video analysis", /^video_/], ["Grouping & validation", /./]];
const sectionOf = (key) => SETTING_SECTIONS.find(([, re]) => re.test(key))[0];
loadSettings = async function () {
  const form = clear($("settings-form")), status = clear($("settings-status"));
  form.classList.remove("max-w-2xl"); form.classList.add("max-w-4xl");
  let res;
  try { res = await api(`${API}/settings`); } catch (e) { status.textContent = e.message; return; }
  const inputs = {}, bySection = {};
  for (const s of res.specs || []) (bySection[sectionOf(s.key)] ||= []).push(s);
  for (const [title] of SETTING_SECTIONS) {
    const specs = bySection[title]; if (!specs) continue;
    form.append(h("div", {}, h("h3", { class: "text-xs uppercase font-bold text-emerald-400 border-b border-slate-800 pb-1 mb-2" }, title),
      h("div", { class: "grid grid-cols-2 gap-3" }, specs.map((s) => {
        const input = h("input", { type: "number", id: `set-${s.key}`, step: s.type === "int" ? "1" : "any", min: s.min, max: s.max, class: "bg-slate-800 rounded p-1 w-40", placeholder: s.nullable ? "auto" : null });
        input.value = res.settings[s.key] ?? "";
        inputs[s.key] = { input, spec: s };
        const def = res.defaults[s.key] ?? s.default;
        return h("label", { class: "block", for: `set-${s.key}` }, h("div", { class: "text-xs font-bold" }, s.label), input,
          h("div", { class: "text-[11px] text-slate-500" }, s.help, ` Default: ${def ?? "auto"}${s.min !== undefined && s.min !== null ? ` (min ${s.min}` : ""}${s.max !== undefined && s.max !== null ? `${s.min !== undefined && s.min !== null ? ", " : " ("}max ${s.max}` : ""}${(s.min !== undefined && s.min !== null) || (s.max !== undefined && s.max !== null) ? ")" : ""}.`));
      }))));
  }
  const save = h("button", { type: "submit", class: "bg-emerald-600 hover:bg-emerald-500 rounded px-3 py-1 text-xs font-bold mr-2" }, "Save");
  const reset = h("button", { type: "button", class: bNeutral }, "Reset to defaults");
  form.append(h("div", {}, save, reset));
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const body = { ...res.settings };
    for (const [k, { input, spec }] of Object.entries(inputs)) {
      const raw = input.value.trim();
      if (raw === "") { if (spec.nullable) body[k] = null; else { setStatus(status, `${spec.label} is required.`, false); return; } }
      else body[k] = Number(raw);
    }
    try { await api(`${API}/settings`, { method: "PUT", headers: JSONH, body: JSON.stringify(body) }); setStatus(status, "Saved.", true); res.settings = body; }
    catch (e) { setStatus(status, e.message, false); }
  };
  reset.onclick = async () => {
    try { await api(`${API}/settings`, { method: "DELETE" }); await loadSettings(); setStatus($("settings-status"), "Reset to defaults.", true); }
    catch (e) { setStatus(status, e.message, false); }
  };
  renderDataPanel();
};
async function renderDataPanel() {
  let host = $("settings-data");
  if (!host) { host = h("div", { id: "settings-data", class: "max-w-4xl mt-3" }); $("tab-settings").append(host); }
  const body = h("div", { class: "text-xs space-y-3" }, "Loading...");
  clear(host).append(section("Data & sync", body));
  const [stats, sync] = await Promise.all([api(`${API}/intel/training/stats`).catch((e) => ({ error: e.message })), api(`${API}/intel/sync/status`).catch((e) => ({ error: e.message }))]);
  const out = clear(body);
  out.append(h("div", {}, h("div", { class: "font-bold mb-1" }, "Training data"),
    stats.error ? h("div", { class: "text-red-400" }, stats.error) : kv([["Total records", stats.total], ["Flagged as good training data", stats.flagged], ["By source", JSON.stringify(stats.by_source || {})]]),
    h("div", { class: "flex gap-3 mt-1" }, [["all", "Download all"], ["flagged", "Download flagged"], ["edited", "Download edited"]].map(([only, label]) =>
      h("a", { class: "underline text-sky-400", href: `${API}/intel/training/export?only=${only}`, download: `training-${only}.ndjson` }, label)))));
  const status = h("div", { class: "text-xs mt-1" });
  const now = h("button", { type: "button", class: bNeutral }, "Sync now");
  now.addEventListener("click", async () => {
    now.disabled = true; setStatus(status, "Syncing...");
    try { const r = await post("/intel/sync/run", {}); setStatus(status, r.error ? `Sync error: ${r.error}` : `Pushed ${r.pushed}, ${r.pending} pending.`, !r.error); renderDataPanel(); }
    catch (e) { setStatus(status, e.message, false); }
    now.disabled = false;
  });
  out.append(h("div", {}, h("div", { class: "font-bold mb-1" }, "Neon sync"),
    sync.error ? h("div", { class: "text-red-400" }, sync.error) : kv([["Configured", sync.configured ? "yes" : "no", !sync.configured], ["Pending", sync.pending], ["Failed", sync.failed, sync.failed > 0], ["Oldest pending", sync.oldest_pending_at], ["Last error", sync.last_error, !!sync.last_error]]),
    h("div", { class: "mt-1" }, now), status));
}

Object.assign(window.BAM, { gotoAsset, indicatorBadges, post, scopeParams });
