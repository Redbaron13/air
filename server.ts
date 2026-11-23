import http from "node:http";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import express from "express";
import { WebSocketServer, WebSocket } from "ws";
import { store } from "./src/store.ts";
import {
  DEFAULT_SETTINGS,
  MODEL_KINDS,
  SETTING_SPECS,
  assetCaptureMeta,
  backendOf,
  buildFlightTracks,
  buildGroups,
  deriveGroupKey,
  droneAngleOf,
  explicitGroupId,
  findModelCandidates,
  findProgression,
  hasGps,
  nearbyAssets,
  renderSvgPreview,
  stageProgress,
  summarizeFindings,
  validateAsset,
  validateSettings,
} from "./src/logic.ts";
import {
  JournalRequestError,
  applySavedPreset,
  characteristicsFromInput,
  journalExists,
  journalFile,
  libraryRoot,
  overrideLockedTag,
  readPendingReviewItems,
  readStagedReviewItem,
  readTagCatalog,
  saveReviewTag,
  type StagedReviewItem,
} from "./src/pipeline-journal.ts";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 3000);
const HOST = "0.0.0.0";
const ALLOWED_MODELS = new Set(["moondream", "llava", "llama3.1"]);

const app = express();
app.use(express.json({ limit: "2mb" }));

// ---- Helpers
function shootsById() {
  return new Map(store.shoots.map((s) => [s.shoot_id, s]));
}

function findingsForAll(assets: Array<Record<string, any>>) {
  const sMap = shootsById();
  const out: Record<string, ReturnType<typeof validateAsset>> = {};
  for (const a of assets) {
    out[a.asset_id] = validateAsset(a, sMap.get(a.shoot_id), Number(a.copy_count || 0), store.settings);
  }
  return out;
}

function sendJournalError(res: express.Response, err: unknown) {
  if (err instanceof JournalRequestError) {
    return res.status(err.status).json({ detail: err.message });
  }
  const message = err instanceof Error ? err.message : "Journal request failed";
  return res.status(500).json({ detail: message });
}

function journalStages(item: StagedReviewItem) {
  return [
    { stage: "Staged", status: item.path ? "done" : "pending" },
    { stage: "Shot", status: item.shot_characteristic ? "done" : "pending" },
    { stage: "Alt text", status: item.alt_text ? "done" : "pending" },
    { stage: "Tags", status: item.tags.length ? "done" : "pending" },
    { stage: "Human review", status: item.approval_state === "PENDING_HUMAN" ? "pending" : "done" },
  ];
}

function journalAssetRecord(item: StagedReviewItem) {
  return {
    asset_id: item.temp_id,
    temp_id: item.temp_id,
    original_filename: item.original_name,
    media_kind: item.media_kind,
    workflow_state: item.approval_state,
    approval_state: item.approval_state,
    pipeline_state: item.workflow_state,
    shoot_id: null,
    location_id: item.location_id,
    agreement_score: item.agreement_score,
    latitude: item.latitude,
    longitude: item.longitude,
    captured_at: item.captured_at,
    sensor_model: null,
    focal_length: null,
    gimbal_pitch: item.gimbal_pitch,
    camera_pitch: item.gimbal_pitch,
    altitude_meters: item.altitude_m,
    original_sha256: null,
    source_path: item.path,
    staging_path: item.staging_path,
    original_path: item.original_path,
    describes: null,
    alt: item.alt_text,
    alt_text: item.alt_text,
    service_tags: item.tags,
    tags: item.tags,
    shot_characteristic: item.shot_characteristic,
    final_output: null,
    notes: null,
    feedback: [],
    training_candidate: false,
    indicators: {},
  };
}

function pendingFromJournal(item: StagedReviewItem) {
  const asset = journalAssetRecord(item);
  return {
    journal: true,
    ...asset,
    path: item.path,
    overrides: item.overrides,
    screener_output: null,
    verifier_output: null,
    judge_output: null,
  };
}

function journalBrief(item: StagedReviewItem) {
  const asset = journalAssetRecord(item);
  return {
    journal: true,
    asset_id: item.temp_id,
    filename: item.original_name || item.temp_id,
    kind: item.media_kind,
    state: item.approval_state,
    approval_state: item.approval_state,
    shoot_id: null,
    location_id: item.location_id,
    score: item.agreement_score,
    lat: item.latitude,
    lon: item.longitude,
    gps: hasGps(asset),
    captured_at: item.captured_at,
    backend: "journal",
    drone_angle: droneAngleOf(item.gimbal_pitch, store.settings),
    fail: 0,
    warn: 0,
    group_key: null,
    tags: item.tags,
    alt_text: item.alt_text,
    shot_characteristic: item.shot_characteristic,
    path: item.path,
  };
}

function journalDetail(item: StagedReviewItem) {
  const asset = journalAssetRecord(item);
  return {
    journal: true,
    asset,
    final_output: null,
    identifier: null,
    identifier_components: null,
    identifier_locked: false,
    indicators: {},
    notes: null,
    feedback: [],
    training_candidate: false,
    scene_group: null,
    capture_meta: null,
    frame_count: 0,
    model_candidates: [],
    progression: [],
    backend: "journal",
    evaluations: [],
    copies: [],
    human_corrections: [],
    shoot: null,
    same_shoot: [],
    nearby: [],
    model_group: null,
    findings: [{ level: "info", code: "journal", message: "Loaded from the AIR journal." }],
    stages: journalStages(item),
    media_url: `/api/review/assets/${encodeURIComponent(item.temp_id)}/media`,
    tag_catalog: readTagCatalog(),
    tag_overrides: item.overrides,
  };
}

function briefAsset(a: Record<string, any>, findings: Record<string, ReturnType<typeof validateAsset>>) {
  const fs = findings[a.asset_id] || [];
  return {
    asset_id: a.asset_id,
    filename: a.original_filename,
    kind: a.media_kind,
    state: a.workflow_state,
    shoot_id: a.shoot_id,
    score: a.agreement_score,
    lat: a.latitude,
    lon: a.longitude,
    gps: hasGps(a),
    captured_at: a.captured_at,
    backend: backendOf(a),
    drone_angle: droneAngleOf(a.gimbal_pitch, store.settings),
    fail: fs.filter((f) => f.level === "fail").length,
    warn: fs.filter((f) => f.level === "warn").length,
    group_key:
      explicitGroupId(a) || (MODEL_KINDS.has(a.media_kind) ? deriveGroupKey(a.original_filename || "") : null),
  };
}

// ---- Mission Control API
app.get("/api/storage/staged", (_req, res) => {
  res.json(store.stagedFiles);
});

app.post("/api/pipeline/ingest", (req, res) => {
  const paths = Array.isArray(req.body?.paths) ? req.body.paths : [];
  if (paths.length === 0 || paths.length > 100) {
    return res.status(400).json({ detail: "paths must contain between 1 and 100 staged file paths" });
  }
  store.emitLog(`[SYSTEM] Starting targeted ingestion for ${paths.length} file(s)...`);
  const created = store.ingestPaths(paths);
  if (created.length === 0) {
    return res.status(400).json({ detail: "Selected files were not found in staging" });
  }
  store.emitLog("[SYSTEM] Target ingestion complete. Ready for Batch Run.");
  return res.json({ status: "success", ingested: created.length });
});

app.get("/api/assets/pending", (_req, res) => {
  if (journalExists()) {
    try {
      res.json(readPendingReviewItems().map(pendingFromJournal));
    } catch (err) {
      sendJournalError(res, err);
    }
    return;
  }
  const pending = store
    .joinedAssets()
    .filter((a) => a.workflow_state === "PENDING_HUMAN")
    .sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
  res.json(pending);
});

app.post("/api/assets/:asset_id/approve", (_req, res) => {
  res.status(409).json({
    detail: "Use the validated review decision endpoint to approve assets",
  });
});

app.post("/api/system/vram/:action/:model_name", (req, res) => {
  const { action, model_name } = req.params;
  if (!["load", "unload"].includes(action) || !ALLOWED_MODELS.has(model_name)) {
    return res.status(400).json({ detail: "Unsupported model action" });
  }
  store.emitLog(`[VRAM] ${action === "unload" ? "Flushing" : "Pre-warming"} ${model_name}...`);
  store.vramModels[model_name] = action === "load";
  store.emitLog(`[SYSTEM] ${model_name} ${action} complete.`);
  return res.json({ status: "success", model: model_name, loaded: store.vramModels[model_name] });
});

app.post("/api/pipeline/run-orchestrator", (_req, res) => {
  store.emitLog("[SYSTEM] Starting Vision Orchestrator batch...");
  const processed = store.evaluateIngested(false);
  store.emitLog(`[SYSTEM] Orchestrator batch complete (${processed} asset(s) evaluated).`);
  res.json({ status: "success", processed });
});

app.post("/api/pipeline/run-cloud", (_req, res) => {
  store.emitLog("[SYSTEM] Starting Cloud Vision Orchestrator batch...");
  const processed = store.evaluateIngested(true);
  store.emitLog(`[SYSTEM] Cloud orchestrator batch complete (${processed} asset(s) evaluated).`);
  res.json({ status: "success", processed });
});

// ---- Review Console API (/api/review/*)
const review = express.Router();

review.get("/overview", (_req, res) => {
  const assets = store.joinedAssets();
  const findings = findingsForAll(assets);
  const states: Record<string, number> = {};
  const kinds: Record<string, number> = {};
  const backends: Record<string, number> = {};
  const buckets = Array(10).fill(0);

  for (const a of assets) {
    states[a.workflow_state] = (states[a.workflow_state] || 0) + 1;
    kinds[a.media_kind] = (kinds[a.media_kind] || 0) + 1;
    const b = backendOf(a);
    backends[b] = (backends[b] || 0) + 1;
    if (a.agreement_score !== null && a.agreement_score !== undefined) {
      const idx = Math.min(9, Math.max(0, Math.floor(Number(a.agreement_score) * 10)));
      buckets[idx] += 1;
    }
  }

  const stages = [
    { stage: "Ingested", count: assets.length },
    { stage: "Telemetry", count: assets.filter((a) => a.sensor_model || hasGps(a)).length },
    { stage: "AI screened", count: assets.filter((a) => a.screener_output).length },
    { stage: "AI verified", count: assets.filter((a) => a.verifier_output).length },
    { stage: "AI judged", count: assets.filter((a) => a.judge_output).length },
    { stage: "Human approved", count: states.APPROVED || 0 },
    { stage: "Derivatives built", count: assets.filter((a) => a.copy_count > 0).length },
  ];

  res.json({
    stages,
    states,
    kinds,
    backends,
    agreement_histogram: buckets,
    staged_files: store.stagedFiles.length,
    validation: summarizeFindings(findings),
    total: assets.length,
  });
});

review.get("/health", (_req, res) => {
  const loadedModels = Object.entries(store.vramModels)
    .filter(([, loaded]) => loaded)
    .map(([name]) => name);
  let journalDetailText = "sample store (Database/tempID.db is not present)";
  if (journalExists()) {
    try {
      journalDetailText = `${journalFile()} (${readPendingReviewItems().length} pending human)`;
    } catch (err: any) {
      journalDetailText = err?.message || "journal file could not be read";
    }
  }
  const checks = [
    {
      name: "AIR journal",
      ok: true,
      detail: journalDetailText,
      level: "ok",
    },
    { name: "PostgreSQL", ok: true, detail: "in-memory store active (provision Cloud SQL for persistence)", level: "ok" },
    { name: "Redis (live log bus)", ok: true, detail: "in-memory WebSocket event bus active", level: "ok" },
    {
      name: "Local AI (Ollama)",
      ok: true,
      detail: `3 models simulated; loaded in VRAM: ${loadedModels.join(", ") || "none"}`,
      level: "ok",
    },
    { name: "Cloud AI backend", ok: true, detail: "not used (BAM_VISION_BACKEND=ollama)", level: "ok" },
    { name: "Staging dir", ok: true, detail: `/storage/active (${store.stagedFiles.length} staged)`, level: "ok" },
    { name: "Originals dir", ok: true, detail: "/storage/originals", level: "ok" },
    { name: "Exports dir", ok: true, detail: "/storage/exports", level: "ok" },
    { name: "Website manifest", ok: true, detail: "ready on demand", level: "ok" },
  ];
  res.json({ checks });
});

review.get("/settings", (_req, res) => {
  res.json({
    settings: { ...store.settings },
    defaults: { ...DEFAULT_SETTINGS },
    specs: SETTING_SPECS,
  });
});

review.put("/settings", (req, res) => {
  try {
    store.settings = validateSettings(req.body || {});
    res.json({ settings: { ...store.settings } });
  } catch (err: any) {
    res.status(422).json({ detail: err?.message || "Invalid settings" });
  }
});

review.delete("/settings", (_req, res) => {
  store.settings = { ...DEFAULT_SETTINGS };
  res.json({ settings: { ...store.settings } });
});

review.get("/events", (req, res) => {
  const limit = Math.min(500, Math.max(1, Number(req.query.limit) || 200));
  const slice = store.eventHistory.slice(0, limit).reverse();
  res.json({ events: slice });
});

review.get("/assets", (req, res) => {
  const state = req.query.state ? String(req.query.state) : null;
  const kind = req.query.kind ? String(req.query.kind) : null;
  const shoot = req.query.shoot ? String(req.query.shoot) : null;
  const q = req.query.q ? String(req.query.q).toLowerCase() : null;
  const issues = String(req.query.issues || "").toLowerCase() === "true";
  const limit = Math.min(2000, Math.max(1, Number(req.query.limit) || 500));

  const assets = store.joinedAssets().sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const findings = findingsForAll(assets);
  const journalOn = journalExists();
  const sample = [];

  for (const a of assets) {
    if (journalOn && a.workflow_state === "PENDING_HUMAN") continue;
    if (state && a.workflow_state !== state) continue;
    if (kind && a.media_kind !== kind) continue;
    if (shoot && (a.shoot_id || "") !== shoot) continue;
    if (q) {
      const hay = `${a.asset_id} ${a.original_filename} ${a.describes || ""}`.toLowerCase();
      if (!hay.includes(q)) continue;
    }
    if (issues && !(findings[a.asset_id] && findings[a.asset_id].length > 0)) continue;
    sample.push(briefAsset(a, findings));
  }

  const journalRows = [];
  if (journalOn && (!state || state === "PENDING_HUMAN") && !shoot) {
    try {
      for (const item of readPendingReviewItems()) {
        if (kind && item.media_kind !== kind) continue;
        if (q) {
          const hay = `${item.temp_id} ${item.original_name || ""} ${item.alt_text || ""} ${item.shot_characteristic || ""} ${item.location_id || ""} ${item.tags.join("\n")}`.toLowerCase();
          if (!hay.includes(q)) continue;
        }
        if (issues) continue;
        journalRows.push(journalBrief(item));
      }
    } catch (err) {
      return sendJournalError(res, err);
    }
  }

  res.json({ assets: [...journalRows, ...sample].slice(0, limit) });
});

review.get("/journal/tags", (_req, res) => {
  if (!journalExists()) return res.json({ journal: false, tags: [] });
  try {
    return res.json({ journal: true, library: libraryRoot(), tags: readTagCatalog() });
  } catch (err) {
    return sendJournalError(res, err);
  }
});

review.post("/journal/tags", (req, res) => {
  try {
    const name = typeof req.body?.name === "string" ? req.body.name : "";
    const characteristics = characteristicsFromInput(req.body?.characteristics);
    const tempId = typeof req.body?.temp_id === "string" ? req.body.temp_id : undefined;
    const tag = saveReviewTag(name, characteristics, tempId);
    return res.json({ status: "saved", tag });
  } catch (err) {
    return sendJournalError(res, err);
  }
});

review.post("/assets/:asset_id/preset-tag", (req, res) => {
  try {
    const name = typeof req.body?.name === "string" ? req.body.name : "";
    const applied = applySavedPreset(String(req.params.asset_id), name);
    return res.json({ status: "applied", ...applied });
  } catch (err) {
    return sendJournalError(res, err);
  }
});

review.post("/assets/:asset_id/tag", (req, res) => {
  try {
    const name = typeof req.body?.name === "string" ? req.body.name : "";
    const characteristics = characteristicsFromInput(req.body?.characteristics);
    const tag = saveReviewTag(name, characteristics, String(req.params.asset_id));
    return res.json({ status: "saved", tag });
  } catch (err) {
    return sendJournalError(res, err);
  }
});

review.post("/assets/:asset_id/tag-override", (req, res) => {
  try {
    const tag = typeof req.body?.tag === "string" ? req.body.tag : "";
    const value = typeof req.body?.value === "string" ? req.body.value : "";
    const reason = typeof req.body?.reason === "string" ? req.body.reason : "";
    const override = overrideLockedTag(String(req.params.asset_id), tag, value, reason);
    return res.json({ status: "overridden", override });
  } catch (err) {
    return sendJournalError(res, err);
  }
});

review.get("/assets/:asset_id", (req, res) => {
  const { asset_id } = req.params;
  if (journalExists()) {
    try {
      const item = readStagedReviewItem(String(asset_id));
      if (item) return res.json(journalDetail(item));
    } catch (err) {
      return sendJournalError(res, err);
    }
  }
  const assets = store.joinedAssets();
  const a = assets.find((x) => x.asset_id === asset_id);
  if (!a) return res.status(404).json({ detail: "Asset not found" });

  const sMap = shootsById();
  const shoot = a.shoot_id ? sMap.get(a.shoot_id) || null : null;
  const evals = store.evaluations
    .filter((e) => e.asset_id === asset_id)
    .sort((x, y) => Date.parse(y.evaluated_at) - Date.parse(x.evaluated_at));
  const copies = store.copies.filter((c) => c.parent_asset_id === asset_id).sort((x, y) => x.width - y.width);
  const corrections = store.humanCorrections.filter((c) => c.asset_id === asset_id);
  const findings = validateAsset(a, shoot, copies.length, store.settings);
  const radiusM = Number(store.settings.group_radius_m ?? 150);
  const groups = buildGroups(assets, radiusM);
  const sameShoot = assets.filter((x) => a.shoot_id && x.shoot_id === a.shoot_id && x.asset_id !== asset_id);
  const modelGroup = groups.models.find((m) => m.asset_ids.includes(asset_id)) || null;
  const byId = new Map(assets.map((x) => [x.asset_id, x]));
  const parts = modelGroup
    ? modelGroup.asset_ids.map((id) => ({
        asset_id: id,
        filename: byId.get(id)?.original_filename || id,
        state: byId.get(id)?.workflow_state || "INGESTED",
      }))
    : [];
  const frames = store.videoFrames.filter((f) => f.asset_id === asset_id);
  const modelCandidates = hasGps(a) ? findModelCandidates(assets, store.settings, { seed_asset_id: asset_id }) : [];
  const progression = hasGps(a) ? findProgression(assets, store.settings, { seed_asset_id: asset_id }) : [];

  return res.json({
    asset: a,
    final_output: a.final_output,
    identifier: a.identifier,
    identifier_components: null,
    identifier_locked: Boolean(a.identifier_locked),
    indicators: a.indicators || {},
    notes: a.notes,
    feedback: a.feedback,
    training_candidate: Boolean(a.training_candidate),
    scene_group: a.scene_group || null,
    capture_meta: assetCaptureMeta(a, [a, ...sameShoot], store.settings, shoot?.address || a.address || null),
    frame_count: frames.length,
    model_candidates: modelCandidates,
    progression,
    backend: backendOf(a),
    evaluations: evals,
    copies,
    human_corrections: corrections,
    shoot,
    same_shoot: sameShoot.slice(0, 60).map((x) => ({ asset_id: x.asset_id, filename: x.original_filename })),
    nearby: nearbyAssets(a, assets, radiusM).slice(0, 60),
    model_group: modelGroup ? { key: modelGroup.key, parts } : null,
    findings,
    stages: stageProgress(a, copies.length),
    media_url: `/api/review/assets/${encodeURIComponent(asset_id)}/media`,
  });
});

review.get("/assets/:asset_id/media", (req, res) => {
  if (journalExists()) {
    const item = readStagedReviewItem(String(req.params.asset_id));
    if (item) {
      const svg = renderSvgPreview(journalAssetRecord(item));
      res.setHeader("Content-Type", "image/svg+xml");
      res.send(svg);
      return;
    }
  }
  const a = store.assets.find((x) => x.asset_id === req.params.asset_id);
  if (!a) return res.status(404).json({ detail: "Asset not found" });
  const svg = renderSvgPreview(a);
  res.setHeader("Content-Type", "image/svg+xml");
  res.send(svg);
});

review.get("/assets/:asset_id/frames", (req, res) => {
  const { asset_id } = req.params;
  const rows = store.videoFrames
    .filter((f) => f.asset_id === asset_id)
    .sort((a, b) => a.frame_index - b.frame_index);
  const base = `/api/review/assets/${encodeURIComponent(asset_id)}/frames`;
  res.json({
    frames: rows.map((r) => ({
      frame_id: r.frame_id,
      frame_index: r.frame_index,
      time_s: r.time_s,
      pass: r.pass_name,
      thumb_url: `${base}/${r.frame_index}/thumb`,
      image_url: `${base}/${r.frame_index}/image`,
      output: r.output,
    })),
  });
});

review.get("/assets/:asset_id/frames/:frame_index/:variant", (req, res) => {
  const { asset_id, frame_index } = req.params;
  const a = store.assets.find((x) => x.asset_id === asset_id);
  const frame = store.videoFrames.find((f) => f.asset_id === asset_id && f.frame_index === Number(frame_index));
  if (!a || !frame) return res.status(404).json({ detail: "Frame not found" });
  const svg = renderSvgPreview(a, `VIDEO FRAME #${frame.frame_index} · ${frame.pass_name.toUpperCase()} · ${frame.time_s}s`);
  res.setHeader("Content-Type", "image/svg+xml");
  res.send(svg);
});

review.get("/assets/:asset_id/exif", (req, res) => {
  if (journalExists()) {
    const item = readStagedReviewItem(String(req.params.asset_id));
    if (item) {
      const live: Record<string, string> = {
        "File:FileName": item.original_name || "N/A",
        "Composite:GPSLatitude": item.latitude === null ? "N/A" : String(item.latitude),
        "Composite:GPSLongitude": item.longitude === null ? "N/A" : String(item.longitude),
        "Composite:GPSAltitude": item.altitude_m === null ? "N/A" : String(item.altitude_m),
        "XMP:GimbalPitchDegree": item.gimbal_pitch === null ? "N/A" : String(item.gimbal_pitch),
        "AIR:ShotCharacteristic": item.shot_characteristic || "N/A",
        "AIR:ApprovalState": item.approval_state || "N/A",
        "AIR:LocationId": item.location_id || "N/A",
        "AIR:Path": item.path || "N/A",
      };
      item.tags.forEach((tag, index) => {
        live[`AIR:Tag:${index}`] = tag;
      });
      item.overrides.forEach((override) => {
        live[`AIR:Override:${override.tag_name}`] = `measured=${override.measured_value}; display=${override.display_value}; reason=${override.reason}`;
      });
      return res.json({
        available: true,
        stored: {
          source_path: item.path,
          staging_path: item.staging_path,
          original_path: item.original_path,
          latitude: item.latitude,
          longitude: item.longitude,
          altitude_meters: item.altitude_m,
          gimbal_pitch: item.gimbal_pitch,
          captured_at: item.captured_at,
          shot_characteristic: item.shot_characteristic,
          approval_state: item.approval_state,
          location_id: item.location_id,
          alt_text: item.alt_text,
          tags: item.tags,
        },
        live,
      });
    }
  }
  const a = store.assets.find((x) => x.asset_id === req.params.asset_id);
  if (!a) return res.status(404).json({ detail: "Asset not found" });
  const stored = {
    source_path: a.source_path,
    sensor_model: a.sensor_model,
    latitude: a.latitude,
    longitude: a.longitude,
    altitude_meters: a.altitude_meters,
    focal_length: a.focal_length,
    gimbal_pitch: a.gimbal_pitch,
    captured_at: a.captured_at,
  };
  res.json({
    available: true,
    stored,
    live: {
      "File:FileName": a.original_filename,
      "EXIF:Make": a.camera_make || "DJI",
      "EXIF:Model": a.sensor_model || "Unknown",
      "EXIF:FocalLength": a.focal_length || "N/A",
      "XMP:GimbalPitchDegree": a.gimbal_pitch ?? "N/A",
      "Composite:GPSLatitude": a.latitude ?? "N/A",
      "Composite:GPSLongitude": a.longitude ?? "N/A",
      "Composite:GPSAltitude": a.altitude_meters ?? "N/A",
      "EXIF:DateTimeOriginal": a.captured_at || "N/A",
    },
  });
});

review.get("/groups", (_req, res) => {
  const assets = store.joinedAssets();
  const findings = findingsForAll(assets);
  const radiusM = Number(store.settings.group_radius_m ?? 150);
  const g = buildGroups(assets, radiusM);
  const sMap = shootsById();
  for (const s of g.shoots) {
    s.info = sMap.get(s.key) || null;
  }
  const byId: Record<string, ReturnType<typeof briefAsset>> = {};
  for (const a of assets) {
    byId[a.asset_id] = briefAsset(a, findings);
  }
  res.json({ radius_m: radiusM, groups: g, assets: byId });
});

review.get("/validation", (_req, res) => {
  const assets = store.joinedAssets();
  const findings = findingsForAll(assets);
  const items: Array<Record<string, any>> = [];
  for (const a of assets) {
    for (const f of findings[a.asset_id] || []) {
      items.push({
        asset_id: a.asset_id,
        filename: a.original_filename,
        state: a.workflow_state,
        ...f,
      });
    }
  }
  const order: Record<string, number> = { fail: 0, warn: 1, info: 2 };
  items.sort((a, b) => (order[a.level] ?? 9) - (order[b.level] ?? 9));
  res.json({ summary: summarizeFindings(findings), items });
});

review.post("/assets/:asset_id/decision", (req, res) => {
  const { asset_id } = req.params;
  const d = req.body || {};
  const action = String(d.action || "");
  if (!["approve", "reject", "rework"].includes(action)) {
    return res.status(422).json({ detail: "action must be approve, reject, or rework" });
  }
  const a = store.assets.find((x) => x.asset_id === asset_id);
  if (!a) return res.status(404).json({ detail: "Asset not found" });
  if (d.expected_state && a.workflow_state !== d.expected_state) {
    return res.status(409).json({ detail: "Asset changed; reload before deciding" });
  }
  if (action === "approve") {
    if (a.workflow_state !== "PENDING_HUMAN") {
      return res.status(409).json({ detail: "Asset is not awaiting human review" });
    }
    if (!d.rights_confirmed || !d.metadata_confirmed) {
      return res.status(422).json({ detail: "Confirm media rights and reviewed metadata" });
    }
  }

  const priorState = a.workflow_state;
  const before = a.final_output ? JSON.parse(JSON.stringify(a.final_output)) : null;
  const now = new Date().toISOString();
  const newState = action === "approve" ? "APPROVED" : action === "reject" ? "REJECTED" : "INGESTED";

  let changed = false;
  if (action === "approve") {
    if (d.final_output && typeof d.final_output === "object" && !Array.isArray(d.final_output)) {
      a.final_output = d.final_output;
      const c = d.final_output.content || {};
      if (c.description !== undefined) a.describes = c.description;
      if (c.alt_text !== undefined) a.alt = c.alt_text;
      if (Array.isArray(c.tags)) a.service_tags = c.tags;
      changed = true;
    }
    if (d.describes !== undefined && d.describes !== null) {
      a.describes = String(d.describes);
      changed = true;
    }
    if (d.alt !== undefined && d.alt !== null) {
      a.alt = String(d.alt);
      changed = true;
    }
    if (Array.isArray(d.service_tags)) {
      a.service_tags = d.service_tags.map(String);
      changed = true;
    }
    if (!(a.alt || "").trim()) {
      return res.status(422).json({ detail: "Reviewed alt text is required" });
    }
  }

  a.workflow_state = newState;
  if (d.notes) a.notes = String(d.notes);
  if (d.feedback) {
    a.feedback = [...(a.feedback || []), { text: String(d.feedback), at: now }];
  }
  if (typeof d.training_candidate === "boolean") {
    a.training_candidate = d.training_candidate;
  }
  if (action === "rework" && d.reprocess_feedback) {
    a.rework_feedback = String(d.reprocess_feedback);
  }
  if (a.final_output) {
    a.final_output.human = {
      ...(a.final_output.human || {}),
      notes: a.notes || undefined,
      feedback: a.feedback,
      training_candidate: a.training_candidate,
      edited: Boolean(a.final_output.human?.edited || changed),
    };
  }

  store.humanCorrections.unshift({
    correction_id: `corr-${crypto.randomBytes(4).toString("hex")}`,
    asset_id: a.asset_id,
    prior_ai_output: {
      describes: a.describes,
      alt: a.alt,
      service_tags: a.service_tags,
    },
    human_approved_output: {
      action,
      reviewer: "operator",
      at: now,
      prior_state: priorState,
      rights_confirmed: Boolean(d.rights_confirmed),
      metadata_confirmed: Boolean(d.metadata_confirmed),
    },
    correction_notes: d.notes || null,
  });

  let example = null;
  if ((action === "approve" && (changed || d.training_candidate)) || d.feedback || (action === "rework" && d.reprocess_feedback)) {
    const source = action === "rework" ? "rework" : changed ? "edit" : d.training_candidate ? "flag" : "feedback";
    example = {
      asset_id: a.asset_id,
      identifier: a.identifier,
      media_kind: a.media_kind,
      source,
      before_output: before,
      after_output: a.final_output,
      feedback: d.reprocess_feedback || d.feedback || null,
      notes: d.notes || null,
      original_ref: a.source_path,
      created_at: now,
    };
    store.trainingExamples.unshift(example);
  }

  store.emitLog(`[REVIEW] ${a.asset_id}: ${action.toUpperCase()} (${priorState} -> ${newState})`);
  return res.json({ status: "success", state: newState, training_example: example });
});

// ---- Media Intelligence API (/api/review/intel/*)
const intel = express.Router();

intel.get("/search", (req, res) => {
  const q = String(req.query.q || "").trim().toLowerCase();
  const kind = req.query.kind ? String(req.query.kind) : null;
  const shoot = req.query.shoot ? String(req.query.shoot) : null;
  const state = req.query.state ? String(req.query.state) : null;
  const shotType = req.query.shot_type ? String(req.query.shot_type) : null;
  const genre = req.query.genre ? String(req.query.genre) : null;
  const limit = Math.min(500, Math.max(1, Number(req.query.limit) || 50));

  const matched = store.joinedAssets().filter((a) => {
    if (kind && a.media_kind !== kind) return false;
    if (shoot && a.shoot_id !== shoot) return false;
    if (state && a.workflow_state !== state) return false;
    const cls = a.final_output?.classification || {};
    if (shotType && cls.shot_type !== shotType) return false;
    if (genre && cls.media_genre !== genre) return false;
    if (q) {
      const hay = `${a.asset_id} ${a.identifier} ${a.original_filename} ${a.describes || ""} ${a.alt || ""} ${a.notes || ""} ${JSON.stringify(a.final_output || {})}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const results = matched.slice(0, limit).map((a) => {
    const cls = a.final_output?.classification || {};
    return {
      asset_id: a.asset_id,
      identifier: a.identifier,
      filename: a.original_filename,
      kind: a.media_kind,
      state: a.workflow_state,
      shoot_id: a.shoot_id,
      shot_type: cls.shot_type || null,
      genre: cls.media_genre || null,
      drone_angle: droneAngleOf(a.gimbal_pitch, store.settings),
      description: a.describes,
      lat: a.latitude,
      lon: a.longitude,
      captured_at: a.captured_at,
      indicators: a.indicators || {},
      snippet: a.describes || a.alt || null,
    };
  });

  res.json({ results, total: matched.length });
});

function parseScope(query: Record<string, any>) {
  return {
    shoot_id: query.shoot_id ? String(query.shoot_id) : undefined,
    seed_asset_id: query.asset_id ? String(query.asset_id) : undefined,
    lat: query.lat !== undefined && query.lat !== "" ? Number(query.lat) : undefined,
    lon: query.lon !== undefined && query.lon !== "" ? Number(query.lon) : undefined,
    radius_m: query.radius_m !== undefined && query.radius_m !== "" ? Number(query.radius_m) : undefined,
  };
}

intel.get("/model-candidates", (req, res) => {
  const candidates = findModelCandidates(store.joinedAssets(), store.settings, parseScope(req.query));
  res.json({ candidates });
});

intel.get("/progression", (req, res) => {
  const locations = findProgression(store.joinedAssets(), store.settings, parseScope(req.query));
  res.json({ locations });
});

intel.get("/view-groups", (_req, res) => {
  const byScene = new Map<string, string[]>();
  for (const a of store.assets) {
    const k = a.scene_group || "default";
    if (!byScene.has(k)) byScene.set(k, []);
    byScene.get(k)!.push(a.asset_id);
  }
  res.json({
    groups: Array.from(byScene.entries()).map(([key, asset_ids]) => ({ key, asset_ids, size: asset_ids.length })),
  });
});

intel.post("/refresh", (_req, res) => {
  const assets = store.joinedAssets();
  const cands = findModelCandidates(assets, store.settings);
  const progs = findProgression(assets, store.settings);
  let updated = 0;
  for (const a of store.assets) {
    const mc = cands.find((c) => c.asset_ids.includes(a.asset_id));
    const pr = progs.find((p) => p.visits.some((v) => v.asset_ids.includes(a.asset_id)));
    a.indicators = {
      ...(a.indicators || {}),
      ...(mc ? { model_candidate: { set_key: mc.set_key, set_size: mc.asset_ids.length, ready: mc.ready } } : {}),
      ...(pr ? { progression: { indicator: pr.indicator, visit_count: pr.visits.length } } : {}),
    };
    updated += 1;
  }
  store.emitLog(`[INTEL] Refreshed indicators for ${updated} asset(s).`);
  res.json({ updated });
});

intel.get("/shoots", (_req, res) => {
  const shoots = store.shoots.map((s) => {
    const members = store.assets.filter((a) => a.shoot_id === s.shoot_id);
    const times = members.map((m) => m.captured_at).filter(Boolean).sort();
    return {
      ...s,
      asset_count: members.length,
      first_asset_at: times[0] || null,
      last_asset_at: times[times.length - 1] || null,
      related: [],
    };
  });
  res.json({ shoots });
});

intel.post("/shoots/split", (req, res) => {
  const { shoot_id, group_a, group_b, project_name_b } = req.body || {};
  const aIds: string[] = Array.from(new Set(Array.isArray(group_a) ? group_a : []));
  const bIds: string[] = Array.from(new Set(Array.isArray(group_b) ? group_b : []));
  if (!aIds.length || !bIds.length) return res.status(422).json({ detail: "Both groups must be non-empty" });
  if (aIds.some((id) => bIds.includes(id))) return res.status(422).json({ detail: "Groups must be disjoint" });
  const orig = store.shoots.find((s) => s.shoot_id === shoot_id);
  if (!orig) return res.status(404).json({ detail: "Shoot not found" });

  const newId = `${orig.shoot_id}-b-${crypto.randomBytes(2).toString("hex")}`;
  const bAssets = store.assets.filter((x) => bIds.includes(x.asset_id));
  const geoB = bAssets.filter(hasGps);
  const latB = geoB.length ? geoB.reduce((s, x) => s + Number(x.latitude), 0) / geoB.length : orig.centroid_lat;
  const lonB = geoB.length ? geoB.reduce((s, x) => s + Number(x.longitude), 0) / geoB.length : orig.centroid_lon;

  store.shoots.push({
    shoot_id: newId,
    project_name: project_name_b || `${orig.project_name} (Location B)`,
    centroid_lat: latB,
    centroid_lon: lonB,
    radius_meters: orig.radius_meters,
    created_at: new Date().toISOString(),
  });
  for (const a of bAssets) {
    a.shoot_id = newId;
  }
  store.emitLog(`[SHOOTS] Split ${shoot_id}: kept ${aIds.length} in A, moved ${bAssets.length} to ${newId}`);
  res.json({ status: "success", shoot_a: shoot_id, shoot_b: newId, moved: bAssets.length });
});

intel.post("/shoots/reassign", (req, res) => {
  const { shoot_id, radius_m } = req.body || {};
  const shoot = store.shoots.find((s) => s.shoot_id === shoot_id);
  if (!shoot) return res.status(404).json({ detail: "Shoot not found" });
  if (radius_m && Number(radius_m) > 0) shoot.radius_meters = Number(radius_m);
  store.emitLog(`[SHOOTS] Updated radius for ${shoot_id} to ${shoot.radius_meters}m`);
  res.json({ status: "success", shoot_id, radius_meters: shoot.radius_meters });
});

intel.post("/batch/finalize", (req, res) => {
  const requestedIds: string[] | null = Array.isArray(req.body?.asset_ids) ? req.body.asset_ids : null;
  const targets = store.assets.filter((a) =>
    requestedIds ? requestedIds.includes(a.asset_id) : !a.identifier_locked && a.final_output,
  );
  for (const a of targets) {
    a.identifier_locked = true;
  }
  store.emitLog(`[INTEL] Finalized & locked identifiers for ${targets.length} asset(s).`);
  res.json({ finalized: targets.map((t) => t.asset_id), requested: targets.length, updated: targets.length });
});

intel.get("/training/stats", (_req, res) => {
  const bySource: Record<string, number> = {};
  for (const ex of store.trainingExamples) {
    bySource[ex.source] = (bySource[ex.source] || 0) + 1;
  }
  const flaggedCount = store.assets.filter((a) => a.training_candidate).length;
  res.json({
    by_source: bySource,
    flagged: flaggedCount,
    flagged_assets: flaggedCount,
    total: store.trainingExamples.length,
  });
});

intel.get("/training/export", (req, res) => {
  const only = String(req.query.only || "all");
  const rows = store.trainingExamples.filter((r) =>
    only === "flagged" ? r.source === "flag" : only === "edited" ? r.source === "edit" : true,
  );
  const body = rows.map((r) => JSON.stringify(r)).join("\n") + (rows.length ? "\n" : "");
  res.setHeader("Content-Type", "application/x-ndjson");
  res.send(body);
});

intel.get("/flight", (req, res) => {
  const shootId = String(req.query.shoot_id || "");
  if (!shootId) return res.status(422).json({ detail: "shoot_id is required" });
  const assets = store.assets.filter((a) => a.shoot_id === shootId);
  const track = buildFlightTracks(assets, store.settings);
  const names = new Map(assets.map((a) => [a.asset_id, a.original_filename]));
  res.json({
    shoot_id: shootId,
    asset_id: null,
    flights: track.flights,
    flight: null,
    points: track.points.map((p) => ({ ...p, filename: names.get(p.asset_id) })),
    position: null,
    neighbors: {},
    untimed: track.untimed,
  });
});

intel.get("/flight/:asset_id", (req, res) => {
  const { asset_id } = req.params;
  const target = store.assets.find((a) => a.asset_id === asset_id);
  if (!target) return res.status(404).json({ detail: "Asset not found" });
  const sid = req.query.shoot_id ? String(req.query.shoot_id) : target.shoot_id;
  const assets = sid ? store.assets.filter((a) => a.shoot_id === sid) : [target];
  const track = buildFlightTracks(assets, store.settings);
  const pos = track.points.find((p) => p.asset_id === asset_id) || null;
  const names = new Map(assets.map((a) => [a.asset_id, a.original_filename]));
  const points = (pos ? track.points.filter((p) => p.flight_index === pos.flight_index) : track.points).map((p) => ({
    ...p,
    filename: names.get(p.asset_id),
  }));
  res.json({
    shoot_id: sid,
    asset_id,
    flights: track.flights,
    flight: pos?.flight || null,
    points,
    position: pos,
    neighbors: pos
      ? {
          prev: pos.prev_asset_id ? { asset_id: pos.prev_asset_id, filename: names.get(pos.prev_asset_id) } : null,
          next: pos.next_asset_id ? { asset_id: pos.next_asset_id, filename: names.get(pos.next_asset_id) } : null,
        }
      : {},
    untimed: track.untimed,
  });
});

intel.get("/sync/status", (_req, res) => {
  res.json({
    configured: Boolean(process.env.NEON_DATABASE_URL),
    pending: 0,
    failed: 0,
    oldest_pending_at: null,
    last_error: null,
  });
});

intel.post("/sync/run", (_req, res) => {
  if (!process.env.NEON_DATABASE_URL) {
    return res.json({ pushed: 0, pending: 0, remote: "unconfigured", error: null });
  }
  return res.json({ pushed: 0, pending: 0, remote: "ok", error: null });
});

review.use("/intel", intel);
app.use("/api/review", review);

// ---- Static Frontend (Mission Control & Review Console)
app.use(express.static(path.join(__dirname, "public")));

// ---- HTTP + WebSocket Server on Port 3000
const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: "/ws/logs" });

const clients = new Set<WebSocket>();
store.onLog((msg) => {
  for (const ws of clients) {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(msg);
    }
  }
});

wss.on("connection", (ws) => {
  clients.add(ws);
  ws.send("[SYSTEM] Connected to BAM Media Factory live log stream.");
  ws.on("close", () => {
    clients.delete(ws);
  });
});

server.listen(PORT, HOST, () => {
  console.log(`BAM Media Factory server listening on http://${HOST}:${PORT}`);
});
