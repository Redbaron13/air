export interface SettingSpec {
  key: string;
  label: string;
  help: string;
  type: "int" | "float";
  default: number | null;
  min: number;
  max: number;
  nullable?: boolean;
  env?: string;
}

export type SettingsMap = Record<string, number | null>;

function spec(
  key: string,
  label: string,
  help: string,
  type: "int" | "float",
  def: number | null,
  min: number,
  max: number,
  nullable = false,
  env?: string,
): SettingSpec {
  const s: SettingSpec = { key, label, help, type, default: def, min, max };
  if (nullable) s.nullable = true;
  if (env) s.env = env;
  return s;
}

export const SETTING_SPECS: SettingSpec[] = [
  spec("group_radius_m", "Group radius (m)", "Radius used to group nearby assets for review.", "float", 150.0, 5, 5000, false, "REVIEW_GROUP_RADIUS_M"),
  spec("shoot_radius_m", "Shoot radius override (m)", "Optional override of shoot radius used during validation.", "float", null, 5, 5000, true),
  spec("stuck_minutes", "Stuck after (min)", "Minutes in a state before an asset is flagged as stuck.", "float", 60.0, 1, 1440, false, "REVIEW_STUCK_MINUTES"),
  spec("low_agreement", "Low agreement threshold", "Model agreement below this value is flagged.", "float", 0.5, 0, 1),
  spec("min_alt_length", "Minimum alt text length", "Minimum characters for alt text.", "int", 10, 1, 500),
  spec("shoot_new_radius_m", "New shoot radius (m)", "Radius given to newly created shoots.", "float", 300.0, 10, 5000),
  spec("shoot_time_window_hours", "Shoot time window (h)", "Max gap from a shoot's time range to join it; 0 disables.", "float", 12, 0, 720),
  spec("related_shoot_radius_m", "Related shoot radius (m)", "Shoots within this distance are related.", "float", 500.0, 10, 20000),
  spec("model_cluster_radius_m", "Model cluster radius (m)", "Single-linkage radius for 3D model photo sets.", "float", 75, 5, 1000),
  spec("model_min_photos", "Model min photos", "Photos needed for a model set to be ready.", "int", 12, 3, 500),
  spec("model_min_angle_coverage_deg", "Model min angle coverage (deg)", "Bearing coverage needed around the subject.", "float", 180, 0, 360),
  spec("model_max_span_hours", "Model max capture span (h)", "Max capture time span within one set.", "float", 6, 0.1, 168),
  spec("model_focal_tolerance_pct", "Model focal tolerance (%)", "Allowed focal length deviation from the set median.", "float", 10, 0, 100),
  spec("progression_radius_m", "Progression radius (m)", "Radius for grouping captures into one location.", "float", 25, 1, 1000),
  spec("progression_min_days_apart", "Visit min days apart", "Minimum days between distinct visits.", "int", 1, 0, 365),
  spec("before_after_min_visits", "Before/after min visits", "Visits needed for before/after.", "int", 2, 2, 50),
  spec("progression_min_visits", "Progression min visits", "Visits needed for site progression.", "int", 3, 2, 50),
  spec("view_radius_m", "View radius (m)", "Radius for treating captures as the same view.", "float", 15, 1, 500),
  spec("view_pitch_tolerance_deg", "View pitch tolerance (deg)", "Allowed gimbal pitch difference within a view.", "float", 15, 0, 90),
  spec("view_subject_overlap", "View subject overlap", "Minimum subject Jaccard similarity within a view.", "float", 0.3, 0, 1),
  spec("video_base_frames_short", "Video base frames (short)", "Base frames for short videos.", "int", 6, 2, 60),
  spec("video_base_frames_long", "Video base frames (long)", "Base frames for long videos.", "int", 12, 2, 120),
  spec("video_long_threshold_s", "Long video threshold (s)", "Videos this long use the long frame count.", "float", 120, 1, 3600),
  spec("video_detail_interval_s", "Detail frame interval (s)", "Spacing of detail frames.", "float", 2, 0.25, 60),
  spec("video_max_detail_frames", "Max detail frames", "Cap on detail frames per video.", "int", 60, 0, 500),
  spec("video_thumb_edge", "Video thumbnail edge (px)", "Long edge of extracted frames.", "int", 320, 32, 2048),
  spec("drone_nadir_max_off_deg", "Nadir max off-vertical (deg)", "Gimbal pitch within this many degrees of straight down counts as nadir.", "float", 5, 0, 30),
  spec("drone_high_oblique_min_down_deg", "High oblique from (deg below horizon)", "Downward angle at/above which a non-nadir frame is a high oblique.", "float", 60, 1, 89),
  spec("drone_medium_oblique_min_down_deg", "Medium oblique from (deg below horizon)", "Downward angle at/above which a frame is a medium oblique.", "float", 30, 1, 89),
  spec("drone_low_oblique_min_down_deg", "Low oblique from (deg below horizon)", "Downward angle at/above which a frame is a low oblique; shallower is horizon.", "float", 10, 0, 89),
  spec("drone_alt_low_max_m", "Low altitude band up to (m AGL)", "Relative altitude at or below this is the low band.", "float", 30, 0, 5000),
  spec("drone_alt_high_min_m", "High altitude band from (m AGL)", "Relative altitude at or above this is the high band.", "float", 90, 0, 5000),
  spec("drone_orbit_tolerance_deg", "Orbit POI tolerance (deg)", "Camera yaw within this angle of the bearing to the subject centre marks an orbit/POI frame.", "float", 25, 1, 90),
  spec("flight_gap_s", "Flight split gap (s)", "A gap between consecutive shots longer than this starts a new flight.", "float", 600, 1, 86400),
  spec("geocode_enabled", "Reverse geocoding enabled", "1 = look up addresses for shoots/assets via the geocoder (cached, opt-in); 0 = never call it.", "int", 0, 0, 1, false, "GEOCODE_ENABLED"),
];

export const DEFAULT_SETTINGS: SettingsMap = Object.fromEntries(
  SETTING_SPECS.map((s) => [s.key, s.default]),
);

export function validateSettings(input: Record<string, unknown>): SettingsMap {
  const out: SettingsMap = {};
  for (const s of SETTING_SPECS) {
    const raw = Object.prototype.hasOwnProperty.call(input, s.key) ? input[s.key] : s.default;
    if (raw === null || raw === undefined || raw === "") {
      if (s.nullable) {
        out[s.key] = null;
        continue;
      }
      throw new Error(`${s.key} must not be null`);
    }
    if (typeof raw === "boolean") {
      throw new Error(`${s.key} must be a number`);
    }
    const num = Number(raw);
    if (!Number.isFinite(num)) {
      throw new Error(`${s.key} must be a number`);
    }
    if (s.type === "int" && !Number.isInteger(num)) {
      throw new Error(`${s.key} must be an integer`);
    }
    if (num < s.min || num > s.max) {
      throw new Error(`${s.key} must be between ${s.min} and ${s.max}`);
    }
    out[s.key] = num;
  }
  const nadirDown = 90 - Number(out.drone_nadir_max_off_deg);
  const hi = Number(out.drone_high_oblique_min_down_deg);
  const mid = Number(out.drone_medium_oblique_min_down_deg);
  const low = Number(out.drone_low_oblique_min_down_deg);
  if (!(nadirDown > hi && hi > mid && mid > low)) {
    throw new Error("drone angle thresholds must satisfy: 90-nadir_max_off > high_oblique > medium_oblique > low_oblique");
  }
  if (Number(out.drone_alt_low_max_m) > Number(out.drone_alt_high_min_m)) {
    throw new Error("drone_alt_low_max_m must not exceed drone_alt_high_min_m");
  }
  return out;
}

export const MODEL_KINDS = new Set(["3d_model", "model", "map", "orthomosaic"]);
export const STILL_LIKE = new Set(["still", "video"]);

const PART_SUFFIX = /(?:[\s_\-.]*(?:part|prt|p|tile|chunk|section|sec|piece|lod|level|block|seg|segment|r|row|c|col|x|y)[\s_\-.]*\d+)+|[\s_\-.]*\d+[\s_\-.]*of[\s_\-.]*\d+|[\s_\-.]*\(\d+\)|[\s_\-.]*\d+$/i;

export function deriveGroupKey(filename: string): string {
  let stem = (filename || "").replace(/\.[A-Za-z0-9]{2,5}$/, "");
  let prev = "";
  while (prev !== stem) {
    prev = stem;
    stem = stem.replace(PART_SUFFIX, "").replace(/[ _\-.]+$/, "");
  }
  return (stem || filename || "").toLowerCase();
}

export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = 6371000.0;
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dp = p2 - p1;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(a));
}

export function bearingDeg(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const y = Math.sin(dl) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

export function compassOf(deg: number | null | undefined): string | null {
  if (deg === null || deg === undefined || !Number.isFinite(deg)) return null;
  const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return dirs[Math.round((((deg % 360) + 360) % 360) / 45) % 8];
}

export function hasGps(asset: Record<string, any>): boolean {
  const lat = asset.latitude;
  const lon = asset.longitude;
  return (
    lat !== null &&
    lat !== undefined &&
    lon !== null &&
    lon !== undefined &&
    Number.isFinite(Number(lat)) &&
    Number.isFinite(Number(lon)) &&
    !(Number(lat) === 0 && Number(lon) === 0)
  );
}

export function explicitGroupId(asset: Record<string, any>): string | null {
  for (const field of ["judge_output", "deep_output"]) {
    const out = asset[field];
    if (out && typeof out === "object") {
      for (const k of ["group_id", "shared_identifier", "model_id"]) {
        if (out[k]) return String(out[k]).toLowerCase();
      }
    }
  }
  return null;
}

export function backendOf(asset: Record<string, any>): string {
  const judge = asset.judge_output;
  if (judge && typeof judge === "object" && judge.backend) {
    return `cloud:${judge.backend}`;
  }
  if (asset.deep_output) return "cloud";
  if (asset.judge_output || asset.screener_output) return "local";
  return "none";
}

export function droneAngleOf(pitch: number | null | undefined, cfg: SettingsMap): string | null {
  if (pitch === null || pitch === undefined || !Number.isFinite(Number(pitch))) return null;
  const down = Math.abs(Number(pitch));
  const nadirMax = Number(cfg.drone_nadir_max_off_deg ?? 5);
  const hiMin = Number(cfg.drone_high_oblique_min_down_deg ?? 60);
  const midMin = Number(cfg.drone_medium_oblique_min_down_deg ?? 30);
  const lowMin = Number(cfg.drone_low_oblique_min_down_deg ?? 10);
  if (down >= 90 - nadirMax) return "nadir";
  if (down >= hiMin) return "high_oblique";
  if (down >= midMin) return "medium_oblique";
  if (down >= lowMin) return "low_oblique";
  return "horizon";
}

export function nearbyAssets(target: Record<string, any>, others: Record<string, any>[], radiusM = 150.0) {
  if (!hasGps(target)) return [];
  const found: Array<{ asset_id: string; distance_m: number }> = [];
  for (const o of others) {
    if (o.asset_id === target.asset_id || !hasGps(o)) continue;
    const d = haversineM(Number(target.latitude), Number(target.longitude), Number(o.latitude), Number(o.longitude));
    if (d <= radiusM) {
      found.push({ asset_id: o.asset_id, distance_m: Math.round(d * 10) / 10 });
    }
  }
  return found.sort((a, b) => a.distance_m - b.distance_m);
}

export function clusterByLocation(assets: Record<string, any>[], radiusM = 150.0): Record<string, any>[][] {
  const geo = assets.filter(hasGps);
  const parent = geo.map((_, i) => i);
  const find = (i: number): number => {
    while (parent[i] !== i) {
      parent[i] = parent[parent[i]];
      i = parent[i];
    }
    return i;
  };
  for (let i = 0; i < geo.length; i++) {
    for (let j = i + 1; j < geo.length; j++) {
      if (find(i) === find(j)) continue;
      const d = haversineM(Number(geo[i].latitude), Number(geo[i].longitude), Number(geo[j].latitude), Number(geo[j].longitude));
      if (d <= radiusM) parent[find(i)] = find(j);
    }
  }
  const buckets = new Map<number, Record<string, any>[]>();
  geo.forEach((a, i) => {
    const root = find(i);
    if (!buckets.has(root)) buckets.set(root, []);
    buckets.get(root)!.push(a);
  });
  return Array.from(buckets.values()).sort((a, b) => b.length - a.length);
}

export function buildGroups(assets: Record<string, any>[], radiusM = 150.0) {
  const shoots = new Map<string, string[]>();
  const models = new Map<string, Record<string, any>[]>();
  for (const a of assets) {
    const sk = a.shoot_id || "(unassigned)";
    if (!shoots.has(sk)) shoots.set(sk, []);
    shoots.get(sk)!.push(a.asset_id);

    if (MODEL_KINDS.has(a.media_kind)) {
      const mk = explicitGroupId(a) || deriveGroupKey(a.original_filename || "");
      if (!models.has(mk)) models.set(mk, []);
      models.get(mk)!.push(a);
    }
  }
  const locations = clusterByLocation(assets, radiusM).map((cluster, idx) => {
    const lats = cluster.map((a) => Number(a.latitude));
    const lons = cluster.map((a) => Number(a.longitude));
    return {
      key: `loc-${idx + 1}`,
      center: {
        lat: lats.reduce((s, v) => s + v, 0) / lats.length,
        lon: lons.reduce((s, v) => s + v, 0) / lons.length,
      },
      asset_ids: cluster.map((a) => a.asset_id),
      shoots: Array.from(new Set(cluster.map((a) => a.shoot_id || "(unassigned)"))).sort(),
    };
  });
  return {
    shoots: Array.from(shoots.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, asset_ids]) => ({ key, asset_ids, info: null as Record<string, any> | null })),
    locations,
    models: Array.from(models.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, list]) => ({
        key,
        asset_ids: list.map((a) => a.asset_id),
        filenames: list.map((a) => a.original_filename),
        single_part: list.length === 1,
      })),
  };
}

export interface Finding {
  level: "fail" | "warn" | "info";
  code: string;
  message: string;
}

export function validateAsset(
  asset: Record<string, any>,
  shoot: Record<string, any> | null | undefined,
  copies: number,
  cfg: SettingsMap,
): Finding[] {
  const out: Finding[] = [];
  const add = (level: "fail" | "warn" | "info", code: string, message: string) =>
    out.push({ level, code, message });

  const state = asset.workflow_state;
  const hasEval = Boolean(asset.screener_output || asset.verifier_output || asset.judge_output);
  const lowAgreement = Number(cfg.low_agreement ?? 0.5);
  const minAlt = Number(cfg.min_alt_length ?? 10);
  const stuckMinutes = Number(cfg.stuck_minutes ?? 60);
  const shootRadiusOverride = cfg.shoot_radius_m;

  if ((state === "PENDING_HUMAN" || state === "APPROVED") && !hasEval) {
    add("fail", "no_ai_eval", `State is ${state} but no AI evaluation exists.`);
  }
  if (hasEval) {
    const missing = ["screener_output", "verifier_output", "judge_output"].filter((k) => !asset[k]);
    if (missing.length) {
      add(
        "fail",
        "eval_incomplete",
        "Missing AI stage output: " + missing.map((m) => m.replace("_output", "")).join(", "),
      );
    }
    const score = asset.agreement_score;
    if (score === null || score === undefined) {
      add("warn", "no_score", "No agreement score recorded.");
    } else {
      const numScore = Number(score);
      if (numScore < 0 || numScore > 1) {
        add("fail", "score_range", `Agreement score ${score} is outside 0-1.`);
      } else if (numScore < lowAgreement) {
        add("warn", "low_agreement", `Low AI agreement (${Math.round(numScore * 100)}%); review carefully.`);
      }
      if (asset.judge_output && asset.judge_output.agreementScore !== undefined) {
        const js = Number(asset.judge_output.agreementScore);
        if (!Number.isFinite(js)) {
          add("warn", "score_mismatch", "Judge agreementScore is not numeric.");
        } else if (Math.abs(js - numScore) > 0.01) {
          add("warn", "score_mismatch", "Stored score differs from the judge's own agreementScore.");
        }
      }
    }
    if (state === "INGESTED") {
      add("warn", "eval_not_advanced", "AI evaluation exists but asset is still INGESTED.");
    }
  }

  if (state === "INGESTED" && asset.created_at && !hasEval) {
    const createdMs = Date.parse(asset.created_at);
    if (Number.isFinite(createdMs) && Date.now() - createdMs > stuckMinutes * 60_000) {
      add("warn", "stuck_ingested", `Waiting for AI processing for over ${stuckMinutes} minutes.`);
    }
  }

  if (STILL_LIKE.has(asset.media_kind)) {
    if (!hasGps(asset)) {
      add("warn", "no_gps", "No GPS in metadata; cannot be located or grouped by place.");
    }
    if (!asset.sensor_model || asset.sensor_model === "Unknown") {
      add("info", "no_sensor", "Sensor/model not captured from EXIF.");
    }
  }

  if (!asset.shoot_id) {
    add("info", "no_shoot", "Not assigned to a project/shoot.");
  } else if (shoot && hasGps(asset)) {
    const radius = shootRadiusOverride ?? shoot.radius_meters;
    if (radius && shoot.centroid_lat !== undefined && shoot.centroid_lon !== undefined) {
      const d = haversineM(
        Number(asset.latitude),
        Number(asset.longitude),
        Number(shoot.centroid_lat),
        Number(shoot.centroid_lon),
      );
      if (d > Number(radius)) {
        add("warn", "outside_shoot", `${Math.round(d)} m from shoot centroid (radius ${Math.round(Number(radius))} m).`);
      }
    }
  }

  if (state === "APPROVED") {
    if ((asset.alt || "").trim().length < minAlt) {
      add("warn", "short_alt", "Approved with missing or very short alt text.");
    }
    if (!(asset.describes || "").trim()) {
      add("warn", "no_describes", "Approved with no description.");
    }
    if (copies === 0) {
      add("warn", "no_derivatives", "Approved but no derivative copies have been built.");
    }
  }

  return out;
}

export function summarizeFindings(findingsByAsset: Record<string, Finding[]>) {
  const byCode: Record<string, number> = {};
  const byLevel = { fail: 0, warn: 0, info: 0 };
  let assetsWithFailures = 0;
  for (const list of Object.values(findingsByAsset)) {
    let hasFail = false;
    for (const f of list) {
      byCode[f.code] = (byCode[f.code] || 0) + 1;
      byLevel[f.level] += 1;
      if (f.level === "fail") hasFail = true;
    }
    if (hasFail) assetsWithFailures += 1;
  }
  return { by_level: byLevel, by_code: byCode, assets_with_failures: assetsWithFailures };
}

export function stageProgress(asset: Record<string, any>, copies = 0) {
  const state = asset.workflow_state;
  const doneAi = Boolean(asset.judge_output);
  const rejected = state === "REJECTED";
  return [
    { stage: "Ingested", status: "done" },
    { stage: "Telemetry (EXIF/GPS)", status: asset.sensor_model || hasGps(asset) ? "done" : "missing" },
    { stage: "AI screener", status: asset.screener_output ? "done" : "pending" },
    { stage: "AI verifier", status: asset.verifier_output ? "done" : "pending" },
    { stage: "AI judge", status: doneAi ? "done" : "pending" },
    { stage: "Human review", status: rejected ? "rejected" : state === "APPROVED" ? "done" : "pending" },
    { stage: "Derivatives", status: copies ? "done" : "pending" },
  ];
}

export function buildFlightTracks(assets: Record<string, any>[], cfg: SettingsMap) {
  const gapS = Number(cfg.flight_gap_s ?? 600);
  const timed = assets
    .filter((a) => a.captured_at && Number.isFinite(Date.parse(a.captured_at)))
    .sort((a, b) => Date.parse(a.captured_at) - Date.parse(b.captured_at) || a.asset_id.localeCompare(b.asset_id));
  const untimed = assets
    .filter((a) => !a.captured_at || !Number.isFinite(Date.parse(a.captured_at)))
    .map((a) => a.asset_id);

  const flightsList: Record<string, any>[][] = [];
  for (const a of timed) {
    const cur = flightsList[flightsList.length - 1];
    if (!cur) {
      flightsList.push([a]);
    } else {
      const prev = cur[cur.length - 1];
      const dt = (Date.parse(a.captured_at) - Date.parse(prev.captured_at)) / 1000;
      if (dt > gapS) flightsList.push([a]);
      else cur.push(a);
    }
  }

  const flights: Record<string, any>[] = [];
  const points: Record<string, any>[] = [];

  flightsList.forEach((group, fIdx) => {
    const startMs = Date.parse(group[0].captured_at);
    const endMs = Date.parse(group[group.length - 1].captured_at);
    let totalDist = 0;
    const fPoints: Record<string, any>[] = [];

    group.forEach((a, idx) => {
      const tMs = Date.parse(a.captured_at);
      const prev = idx > 0 ? group[idx - 1] : null;
      const next = idx < group.length - 1 ? group[idx + 1] : null;
      const dtPrev = prev ? (tMs - Date.parse(prev.captured_at)) / 1000 : null;
      const dtNext = next ? (Date.parse(next.captured_at) - tMs) / 1000 : null;
      let distPrev: number | null = null;
      let heading: number | null = null;
      let speed: number | null = null;
      if (prev && hasGps(prev) && hasGps(a)) {
        distPrev = haversineM(Number(prev.latitude), Number(prev.longitude), Number(a.latitude), Number(a.longitude));
        totalDist += distPrev;
        heading = bearingDeg(Number(prev.latitude), Number(prev.longitude), Number(a.latitude), Number(a.longitude));
        if (dtPrev && dtPrev > 0) speed = distPrev / dtPrev;
      }
      fPoints.push({
        asset_id: a.asset_id,
        flight_index: fIdx + 1,
        flight_count: flightsList.length,
        seq: idx + 1,
        total: group.length,
        captured_at: a.captured_at,
        elapsed_s: Math.round((tMs - startMs) / 1000),
        dt_prev_s: dtPrev,
        dt_next_s: dtNext,
        dist_prev_m: distPrev !== null ? Math.round(distPrev * 10) / 10 : null,
        speed_mps: speed !== null ? Math.round(speed * 10) / 10 : null,
        heading_deg: heading !== null ? Math.round(heading) : null,
        heading_compass: compassOf(heading),
        latitude: a.latitude,
        longitude: a.longitude,
        rel_altitude: a.rel_altitude ?? a.altitude_meters ?? null,
        prev_asset_id: prev ? prev.asset_id : null,
        next_asset_id: next ? next.asset_id : null,
      });
    });

    const durationS = Math.max(0, Math.round((endMs - startMs) / 1000));
    const summary = {
      flight_index: fIdx + 1,
      start_at: group[0].captured_at,
      end_at: group[group.length - 1].captured_at,
      duration_s: durationS,
      photo_count: group.length,
      total_distance_m: Math.round(totalDist),
      avg_speed_mps: durationS > 0 ? Math.round((totalDist / durationS) * 10) / 10 : null,
    };
    flights.push(summary);
    fPoints.forEach((p) => points.push({ ...p, flight: summary }));
  });

  return { flights, points, untimed };
}

export function assetCaptureMeta(
  asset: Record<string, any>,
  shootAssets: Record<string, any>[],
  cfg: SettingsMap,
  address: Record<string, any> | null,
) {
  const angle = droneAngleOf(asset.gimbal_pitch, cfg);
  const track = buildFlightTracks(shootAssets, cfg);
  const fl = track.points.find((p) => p.asset_id === asset.asset_id) || null;
  const relAlt = asset.rel_altitude ?? asset.altitude_meters ?? null;
  const altBand =
    relAlt === null
      ? null
      : Number(relAlt) <= Number(cfg.drone_alt_low_max_m ?? 30)
        ? "low"
        : Number(relAlt) >= Number(cfg.drone_alt_high_min_m ?? 90)
          ? "high"
          : "mid";

  return {
    capture: {
      captured_at: asset.captured_at,
      local_display: asset.captured_at ? new Date(asset.captured_at).toLocaleString("en-US", { timeZone: "UTC" }) + " UTC" : null,
      captured_at_utc: asset.captured_at,
      utc_offset: asset.capture_utc_offset || "+00:00",
      tz_known: true,
    },
    location: {
      gps_valid: hasGps(asset),
      latitude: asset.latitude,
      longitude: asset.longitude,
      altitude_m: asset.altitude_meters,
      rel_altitude_m: relAlt,
      address: address || asset.address || null,
    },
    camera: {
      make: asset.camera_make || "DJI",
      model: asset.sensor_model || "Mavic 3 Enterprise",
      lens_model: asset.lens_model || "24mm f/2.8 Equivalent",
      focal_length: asset.focal_length || "24mm",
      iso: asset.iso || 100,
      shutter_speed: asset.shutter_speed || "1/800",
      aperture: asset.aperture || "f/2.8",
      serial: asset.camera_serial || "BAM-UAV-042",
    },
    drone: {
      drone_angle: angle,
      shot_type: asset.final_output?.classification?.shot_type || (angle === "nadir" ? "orthomosaic" : "oblique"),
      viewed_from: angle === "nadir" ? "overhead" : "elevated",
      facing_compass: compassOf(asset.gimbal_yaw ?? fl?.heading_deg ?? 45),
      facing_relative: "subject",
      altitude_band: altBand,
      mapping_role: angle === "nadir" ? "grid_mapping" : "context_oblique",
      gimbal_pitch_deg: asset.gimbal_pitch,
      gimbal_yaw_deg: asset.gimbal_yaw ?? fl?.heading_deg ?? 45,
      flight_yaw_deg: fl?.heading_deg ?? 45,
      flight_roll_deg: 0.4,
      rel_altitude_m: relAlt,
      abs_altitude_m: asset.altitude_meters,
      basis: [
        asset.gimbal_pitch !== null && asset.gimbal_pitch !== undefined
          ? `Gimbal pitch ${asset.gimbal_pitch}° classified as ${angle || "unknown"}`
          : "No gimbal pitch recorded",
      ],
    },
    flight: fl,
    raw_exif: {
      "EXIF:Make": asset.camera_make || "DJI",
      "EXIF:Model": asset.sensor_model || "Mavic 3 Enterprise",
      "EXIF:FocalLength": asset.focal_length || "24mm",
      "EXIF:DateTimeOriginal": asset.captured_at || "N/A",
      "XMP:GimbalPitchDegree": asset.gimbal_pitch ?? "N/A",
      "XMP:RelativeAltitude": relAlt ?? "N/A",
      "Composite:GPSLatitude": asset.latitude ?? "N/A",
      "Composite:GPSLongitude": asset.longitude ?? "N/A",
      "File:FileName": asset.original_filename,
    },
  };
}

export function findModelCandidates(
  assets: Record<string, any>[],
  cfg: SettingsMap,
  scope: { shoot_id?: string; seed_asset_id?: string; lat?: number; lon?: number; radius_m?: number } = {},
) {
  let pool = assets.filter((a) => a.media_kind === "still" && hasGps(a));
  if (scope.shoot_id) pool = pool.filter((a) => a.shoot_id === scope.shoot_id);
  if (scope.lat !== undefined && scope.lon !== undefined) {
    const rad = scope.radius_m ?? Number(cfg.model_cluster_radius_m ?? 75) * 4;
    pool = pool.filter((a) => haversineM(scope.lat!, scope.lon!, Number(a.latitude), Number(a.longitude)) <= rad);
  }
  const clusters = clusterByLocation(pool, Number(cfg.model_cluster_radius_m ?? 75));
  const minPhotos = Number(cfg.model_min_photos ?? 12);
  return clusters
    .filter((c) => !scope.seed_asset_id || c.some((a) => a.asset_id === scope.seed_asset_id))
    .map((c, i) => {
      const ready = c.length >= minPhotos;
      const missing: string[] = [];
      if (c.length < minPhotos) missing.push(`Needs ${minPhotos - c.length} more photos for full coverage`);
      return {
        set_key: `model-set-${i + 1}`,
        ready,
        score: Math.min(1, Math.round((c.length / minPhotos) * 100) / 100),
        asset_ids: c.map((a) => a.asset_id),
        shoot_ids: Array.from(new Set(c.map((a) => a.shoot_id).filter(Boolean))),
        stats: {
          photo_count: c.length,
          angle_coverage_deg: Math.min(360, c.length * 42),
          span_hours: 0.4,
        },
        reasons: ["Geotagged stills clustered within photogrammetry radius", "Consistent focal length"],
        missing,
      };
    });
}

export function findProgression(
  assets: Record<string, any>[],
  cfg: SettingsMap,
  scope: { shoot_id?: string; seed_asset_id?: string; lat?: number; lon?: number; radius_m?: number } = {},
) {
  let pool = assets.filter(hasGps);
  if (scope.shoot_id) pool = pool.filter((a) => a.shoot_id === scope.shoot_id);
  if (scope.lat !== undefined && scope.lon !== undefined) {
    const rad = scope.radius_m ?? Number(cfg.progression_radius_m ?? 25) * 6;
    pool = pool.filter((a) => haversineM(scope.lat!, scope.lon!, Number(a.latitude), Number(a.longitude)) <= rad);
  }
  const clusters = clusterByLocation(pool, Math.max(Number(cfg.progression_radius_m ?? 25), 80));
  return clusters
    .filter((c) => !scope.seed_asset_id || c.some((a) => a.asset_id === scope.seed_asset_id))
    .map((c, idx) => {
      const byDay = new Map<string, Record<string, any>[]>();
      for (const a of c) {
        const day = (a.captured_at || "2026-11-01").slice(0, 10);
        if (!byDay.has(day)) byDay.set(day, []);
        byDay.get(day)!.push(a);
      }
      const visits = Array.from(byDay.entries())
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([date, list]) => ({ date, asset_ids: list.map((x) => x.asset_id) }));
      const indicator =
        visits.length >= Number(cfg.progression_min_visits ?? 3)
          ? "site_progression"
          : visits.length >= Number(cfg.before_after_min_visits ?? 2)
            ? "before_after"
            : "none";
      const pairs =
        visits.length >= 2
          ? [
              {
                before_asset_id: visits[0].asset_ids[0],
                after_asset_id: visits[visits.length - 1].asset_ids[0],
                days_apart: Math.max(
                  1,
                  Math.round(
                    (Date.parse(visits[visits.length - 1].date) - Date.parse(visits[0].date)) / 86_400_000,
                  ),
                ),
              },
            ]
          : [];
      const lats = c.map((a) => Number(a.latitude));
      const lons = c.map((a) => Number(a.longitude));
      return {
        location_key: `site-${idx + 1}`,
        indicator,
        center: {
          lat: lats.reduce((s, v) => s + v, 0) / lats.length,
          lon: lons.reduce((s, v) => s + v, 0) / lons.length,
        },
        visits,
        view_groups: [
          {
            view_key: `view-${idx + 1}`,
            label: c[0].final_output?.view?.label || "Primary aerial vantage",
            visits,
            pairs,
          },
        ],
      };
    });
}

export function renderSvgPreview(asset: Record<string, any>, subtitle?: string): string {
  const esc = (v: string) =>
    String(v ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  const lat = hasGps(asset) ? `${Number(asset.latitude).toFixed(5)}, ${Number(asset.longitude).toFixed(5)}` : "NO GPS";
  const alt = asset.altitude_meters ? `${Number(asset.altitude_meters).toFixed(1)}m AGL` : "ALT N/A";
  const pitch = asset.gimbal_pitch !== null && asset.gimbal_pitch !== undefined ? `PITCH ${asset.gimbal_pitch}°` : "PITCH N/A";
  const kind = String(asset.media_kind || "still").toUpperCase();
  const title = esc(asset.original_filename || asset.asset_id);
  const sub = esc(subtitle || `${kind} · ${asset.sensor_model || "Aerial Sensor"}`);
  const desc = esc((asset.describes || asset.alt || "Aerial media preview").slice(0, 88));

  return `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="480" viewBox="0 0 800 480">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#0f172a"/>
      <stop offset="55%" stop-color="#1e293b"/>
      <stop offset="100%" stop-color="#020617"/>
    </linearGradient>
    <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
      <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#334155" stroke-opacity="0.35" stroke-width="1"/>
    </pattern>
  </defs>
  <rect width="800" height="480" fill="url(#sky)"/>
  <rect width="800" height="480" fill="url(#grid)"/>
  <g stroke="#34d399" stroke-opacity="0.45" fill="none">
    <path d="M 30 70 L 30 30 L 70 30"/>
    <path d="M 730 30 L 770 30 L 770 70"/>
    <path d="M 30 410 L 30 450 L 70 450"/>
    <path d="M 730 450 L 770 450 L 770 410"/>
    <circle cx="400" cy="220" r="56" stroke-dasharray="6 6"/>
    <line x1="330" y1="220" x2="470" y2="220" stroke-opacity="0.3"/>
    <line x1="400" y1="150" x2="400" y2="290" stroke-opacity="0.3"/>
  </g>
  <text x="48" y="64" fill="#34d399" font-family="monospace" font-size="14" font-weight="bold">BAM AERIAL PREVIEW // ${esc(asset.asset_id)}</text>
  <text x="752" y="64" text-anchor="end" fill="#94a3b8" font-family="monospace" font-size="13">${esc(alt)} · ${esc(pitch)}</text>
  <text x="400" y="216" text-anchor="middle" fill="#f8fafc" font-family="monospace" font-size="22" font-weight="bold">${title}</text>
  <text x="400" y="246" text-anchor="middle" fill="#38bdf8" font-family="monospace" font-size="14">${sub}</text>
  <text x="400" y="340" text-anchor="middle" fill="#cbd5e1" font-family="sans-serif" font-size="14">${desc}</text>
  <text x="48" y="432" fill="#94a3b8" font-family="monospace" font-size="13">GPS: ${esc(lat)}</text>
  <text x="752" y="432" text-anchor="end" fill="#64748b" font-family="monospace" font-size="13">STATE: ${esc(asset.workflow_state || "INGESTED")}</text>
</svg>`;
}
