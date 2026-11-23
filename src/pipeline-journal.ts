import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";

export class JournalRequestError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "JournalRequestError";
    this.status = status;
  }
}

export interface TagOverrideRecord {
  tag_name: string;
  measured_value: string;
  display_value: string;
  reason: string;
}

export interface CatalogTag {
  name: string;
  locked: boolean;
  source: string;
  characteristics: Record<string, string>;
}

export interface StagedReviewItem {
  temp_id: string;
  media_kind: string;
  alt_text: string | null;
  tags: string[];
  shot_characteristic: string | null;
  approval_state: string | null;
  staging_path: string | null;
  original_path: string | null;
  path: string | null;
  location_id: string | null;
  latitude: number | null;
  longitude: number | null;
  original_name: string | null;
  agreement_score: number | null;
  altitude_m: number | null;
  gimbal_pitch: number | null;
  captured_at: string | null;
  workflow_state: string | null;
  overrides: TagOverrideRecord[];
}

export interface AirCommandResult {
  ok: boolean;
  status: number;
  result: Record<string, unknown> | null;
  detail: string;
}

const ASSET_SQL = `
  SELECT
    temp_id, media_kind, alt_text, shot_characteristic, approval_state,
    staging_path, original_path, original_store, location_id,
    latitude, longitude, original_name, agreement_score, altitude_m,
    gimbal_pitch, captured_at, workflow_state
  FROM assets
`;

export function repoRoot(): string {
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
}

export function libraryRoot(explicit?: string): string {
  const raw = explicit || process.env.AIR_LIBRARY || "library";
  let expanded = raw;
  if (raw === "~") expanded = os.homedir();
  else if (raw.startsWith("~/")) expanded = path.join(os.homedir(), raw.slice(2));
  return path.resolve(repoRoot(), expanded);
}

export function journalFile(root = libraryRoot()): string {
  return path.join(root, "Database", "tempID.db");
}

export function journalExists(file = journalFile()): boolean {
  try {
    return fs.existsSync(file) && fs.statSync(file).isFile();
  } catch {
    return false;
  }
}

/** Missing coordinates and the unset 0,0 pair stay null. A real equator point is kept. */
export function presentGps(latitude: unknown, longitude: unknown): { latitude: number | null; longitude: number | null } {
  if (latitude === null || latitude === undefined || latitude === "" || longitude === null || longitude === undefined || longitude === "") {
    return { latitude: null, longitude: null };
  }
  const lat = typeof latitude === "number" ? latitude : Number(latitude);
  const lon = typeof longitude === "number" ? longitude : Number(longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || (lat === 0 && lon === 0)) {
    return { latitude: null, longitude: null };
  }
  return { latitude: lat, longitude: lon };
}

export function readPendingReviewItems(file = journalFile()): StagedReviewItem[] {
  if (!journalExists(file)) return [];
  return readAssets(file, "WHERE approval_state = ? ORDER BY temp_id", ["PENDING_HUMAN"]);
}

export function readStagedReviewItem(tempId: string, file = journalFile()): StagedReviewItem | null {
  if (!journalExists(file)) return null;
  const rows = readAssets(file, "WHERE temp_id = ?", [tempId]);
  return rows[0] || null;
}

export function readTagCatalog(file = journalFile()): CatalogTag[] {
  if (!journalExists(file)) return [];
  return withDatabase(file, true, (db) => {
    const rows = db.prepare(
      "SELECT name, locked, source, characteristics_json FROM tag_definitions WHERE saved = 1 ORDER BY name",
    ).all();
    return rows.map((row) => ({
      name: textOrNull(row.name) || "",
      locked: Boolean(row.locked),
      source: textOrNull(row.source) || "preset",
      characteristics: characteristicsFromJson(row.characteristics_json),
    })).filter((tag) => tag.name);
  });
}

export function characteristicsFromInput(input: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  const add = (key: unknown, value: unknown) => {
    const name = typeof key === "string" ? key.trim() : "";
    if (!name || value === undefined || value === null) return;
    out[name] = typeof value === "string" ? value : JSON.stringify(value);
  };
  if (Array.isArray(input)) {
    for (const item of input) {
      if (typeof item === "string") {
        const eq = item.indexOf("=");
        if (eq > 0) add(item.slice(0, eq), item.slice(eq + 1));
        continue;
      }
      if (item && typeof item === "object") {
        const record = item as Record<string, unknown>;
        add(record.key ?? record.name, record.value);
      }
    }
    return out;
  }
  if (input && typeof input === "object") {
    for (const [key, value] of Object.entries(input as Record<string, unknown>)) add(key, value);
  }
  return out;
}

export function applySavedPreset(tempId: string, name: string, root = libraryRoot()): { name: string; tags: string[] } {
  const requested = typeof name === "string" ? name.trim() : "";
  if (!requested) throw new JournalRequestError(422, "tag name is empty");
  const file = journalFile(root);
  if (!journalExists(file)) throw new JournalRequestError(404, "Journal database is not present");
  let saved = readTagCatalog(file).find((tag) => tag.name === requested);
  if (!saved) {
    const attempted = runReviewTag(root, requested, {});
    saved = readTagCatalog(file).find((tag) => tag.name === requested);
    if (!saved) throw new JournalRequestError(422, attempted.detail);
  }
  assignExact(file, tempId, saved.name, saved.source || "preset");
  const item = readStagedReviewItem(tempId, file);
  return { name: saved.name, tags: item?.tags ?? [] };
}

export function saveReviewTag(name: string, characteristics: Record<string, string>, tempId?: string, root = libraryRoot()): Record<string, unknown> {
  const requested = typeof name === "string" ? name.trim() : "";
  if (!requested) throw new JournalRequestError(422, "tag name is empty");
  const file = journalFile(root);
  if (!journalExists(file)) throw new JournalRequestError(404, "Journal database is not present");
  const command = runReviewTag(root, requested, characteristics || {});
  if (!command.ok || !command.result) throw new JournalRequestError(422, command.detail);
  const savedName = typeof command.result.name === "string" && command.result.name ? command.result.name : requested;
  if (tempId) assignExact(file, tempId, savedName, "user");
  return command.result;
}

export function overrideLockedTag(tempId: string, tag: string, displayValue: string, reason: string, root = libraryRoot()): Record<string, unknown> {
  const file = journalFile(root);
  if (!journalExists(file)) throw new JournalRequestError(404, "Journal database is not present");
  if (!readStagedReviewItem(tempId, file)) throw new JournalRequestError(404, "Asset not found");
  const command = runReviewOverride(root, tempId, tag, displayValue, reason);
  if (!command.ok || !command.result) throw new JournalRequestError(422, command.detail);
  return command.result;
}

export function runReviewTag(root: string, name: string, characteristics: Record<string, string>): AirCommandResult {
  const args = ["-m", "pipeline.air", "--library", root, "review", "tag", "--name", name];
  for (const [key, value] of Object.entries(characteristics || {})) {
    if (!key) continue;
    args.push("--characteristic", `${key}=${value ?? ""}`);
  }
  return runAir(args);
}

export function runReviewOverride(root: string, tempId: string, tag: string, displayValue: string, reason: string): AirCommandResult {
  return runAir([
    "-m", "pipeline.air", "--library", root, "review", "override",
    "--temp-id", tempId,
    "--tag", tag,
    "--value", displayValue,
    "--reason", reason,
  ]);
}

function runAir(args: string[]): AirCommandResult {
  const proc = spawnSync("python3", args, {
    cwd: repoRoot(),
    encoding: "utf8",
    timeout: 30_000,
  });
  const stdout = proc.stdout || "";
  const stderr = proc.stderr || "";
  if (proc.error) {
    return { ok: false, status: proc.status ?? 1, result: null, detail: proc.error.message };
  }
  const status = proc.status ?? 1;
  if (status !== 0) {
    return { ok: false, status, result: null, detail: pythonDetail(stderr, stdout) };
  }
  try {
    const parsed = JSON.parse(stdout);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false, status, result: null, detail: "pipeline command returned an unexpected payload" };
    }
    return { ok: true, status, result: parsed as Record<string, unknown>, detail: "" };
  } catch {
    return { ok: false, status, result: null, detail: pythonDetail(stderr, stdout) };
  }
}

function pythonDetail(stderr: string, stdout: string): string {
  const lines = `${stderr}\n${stdout}`.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const match = lines[i].match(/^(?:[\w.]*)?(?:Error|Exception):\s*(.*)$/);
    if (match && match[1]) return match[1];
  }
  return lines[lines.length - 1] || "pipeline command failed";
}

function assignExact(file: string, tempId: string, tagName: string, source: string): void {
  withDatabase(file, false, (db) => {
    const asset = db.prepare("SELECT temp_id FROM assets WHERE temp_id = ?").get(tempId);
    if (!asset) throw new JournalRequestError(404, "Asset not found");
    db.prepare(
      `INSERT INTO tag_assignments(temp_id, tag_name, source) VALUES (?, ?, ?)
       ON CONFLICT(temp_id, tag_name) DO NOTHING`,
    ).run(tempId, tagName, source || "preset");
  });
}

function readAssets(file: string, where: string, params: string[]): StagedReviewItem[] {
  return withDatabase(file, true, (db) => {
    const rows = db.prepare(`${ASSET_SQL} ${where}`).all(...params);
    const ids = rows.map((row) => String(row.temp_id));
    const tags = tagsByAsset(db, ids);
    const overrides = overridesByAsset(db, ids);
    return rows.map((row) => mapAsset(row, tags.get(String(row.temp_id)) || [], overrides.get(String(row.temp_id)) || []));
  });
}

function mapAsset(row: Record<string, unknown>, tags: string[], overrides: TagOverrideRecord[]): StagedReviewItem {
  const staging = textOrNull(row.staging_path);
  const original = textOrNull(row.original_path) || textOrNull(row.original_store);
  const gps = presentGps(row.latitude, row.longitude);
  return {
    temp_id: String(row.temp_id),
    media_kind: textOrNull(row.media_kind) || "still",
    alt_text: textOrNull(row.alt_text),
    tags,
    shot_characteristic: textOrNull(row.shot_characteristic),
    approval_state: textOrNull(row.approval_state),
    staging_path: staging,
    original_path: original,
    path: staging || original,
    location_id: textOrNull(row.location_id),
    latitude: gps.latitude,
    longitude: gps.longitude,
    original_name: textOrNull(row.original_name),
    agreement_score: numberOrNull(row.agreement_score),
    altitude_m: numberOrNull(row.altitude_m),
    gimbal_pitch: numberOrNull(row.gimbal_pitch),
    captured_at: textOrNull(row.captured_at),
    workflow_state: textOrNull(row.workflow_state),
    overrides,
  };
}

function tagsByAsset(db: DatabaseSync, ids: string[]): Map<string, string[]> {
  const map = new Map<string, string[]>();
  if (!ids.length) return map;
  const marks = ids.map(() => "?").join(", ");
  const rows = db.prepare(
    `SELECT temp_id, tag_name FROM tag_assignments WHERE temp_id IN (${marks}) ORDER BY temp_id, tag_name`,
  ).all(...ids);
  for (const row of rows) {
    const name = textOrNull(row.tag_name);
    if (!name) continue;
    const id = String(row.temp_id);
    const list = map.get(id);
    if (list) list.push(name);
    else map.set(id, [name]);
  }
  return map;
}

function overridesByAsset(db: DatabaseSync, ids: string[]): Map<string, TagOverrideRecord[]> {
  const map = new Map<string, TagOverrideRecord[]>();
  if (!ids.length) return map;
  const marks = ids.map(() => "?").join(", ");
  const rows = db.prepare(
    `SELECT temp_id, tag_name, measured_value, display_value, reason
     FROM tag_overrides WHERE temp_id IN (${marks}) ORDER BY temp_id, tag_name`,
  ).all(...ids);
  for (const row of rows) {
    const id = String(row.temp_id);
    const record = {
      tag_name: textOrNull(row.tag_name) || "",
      measured_value: textOrNull(row.measured_value) || "",
      display_value: textOrNull(row.display_value) || "",
      reason: textOrNull(row.reason) || "",
    };
    if (!record.tag_name) continue;
    const list = map.get(id);
    if (list) list.push(record);
    else map.set(id, [record]);
  }
  return map;
}

function characteristicsFromJson(value: unknown): Record<string, string> {
  if (typeof value !== "string" || !value) return {};
  try {
    return characteristicsFromInput(JSON.parse(value));
  } catch {
    return {};
  }
}

function textOrNull(value: unknown): string | null {
  if (typeof value !== "string" || value === "") return null;
  return value;
}

function numberOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

function withDatabase<T>(file: string, readOnly: boolean, fn: (db: DatabaseSync) => T): T {
  const db = new DatabaseSync(file, { readOnly, timeout: 5000 });
  try {
    return fn(db);
  } finally {
    db.close();
  }
}
