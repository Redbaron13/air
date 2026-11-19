import crypto from "node:crypto";
import { DEFAULT_SETTINGS, type SettingsMap, droneAngleOf } from "./logic.ts";

export interface ShootRecord {
  shoot_id: string;
  project_name: string;
  centroid_lat: number;
  centroid_lon: number;
  radius_meters: number;
  created_at: string;
  address?: Record<string, string>;
}

export interface EvalRecord {
  eval_id: string;
  asset_id: string;
  screener_output: Record<string, any>;
  verifier_output: Record<string, any>;
  judge_output: Record<string, any>;
  deep_output: Record<string, any> | null;
  agreement_score: number;
  evaluated_at: string;
}

export interface CopyRecord {
  copy_id: string;
  parent_asset_id: string;
  size_tier: string;
  version_code: string;
  format: string;
  width: number;
  height: number;
  byte_size: number;
  sha256: string;
  r2_storage_key: string;
  created_at: string;
}

export interface FrameRecord {
  frame_id: string;
  asset_id: string;
  frame_index: number;
  time_s: number;
  pass_name: "base" | "detail";
  output: Record<string, any>;
}

export interface StagedFile {
  path: string;
  folder: string;
  name: string;
}

export interface PipelineEvent {
  t: string;
  msg: string;
}

export interface AssetRecord {
  asset_id: string;
  unique_stem: string;
  identifier: string;
  identifier_locked: boolean;
  original_filename: string;
  source_path: string;
  original_sha256: string;
  media_kind: string;
  shoot_id: string | null;
  captured_at: string | null;
  capture_utc_offset?: string;
  sensor_model: string | null;
  camera_make?: string;
  lens_model?: string;
  focal_length: string | null;
  gimbal_pitch: number | null;
  gimbal_yaw?: number | null;
  camera_pitch?: number | null;
  latitude: number | null;
  longitude: number | null;
  altitude_meters: number | null;
  rel_altitude?: number | null;
  describes: string | null;
  alt: string | null;
  service_tags: string[];
  workflow_state: string;
  created_at: string;
  notes: string | null;
  feedback: Array<string | { text: string; at: string }>;
  rework_feedback?: string | null;
  training_candidate: boolean;
  scene_group?: string | null;
  indicators: Record<string, any>;
  final_output: Record<string, any> | null;
  address?: Record<string, string> | null;
}

function sha256(seed: string): string {
  return crypto.createHash("sha256").update(seed).digest("hex");
}

export class InMemoryPipelineStore {
  settings: SettingsMap = { ...DEFAULT_SETTINGS };
  vramModels: Record<string, boolean> = {
    moondream: true,
    llava: false,
    "llama3.1": false,
  };

  shoots: ShootRecord[] = [
    {
      shoot_id: "shoot-harbor-2026",
      project_name: "Harbor Bridge Span Inspection",
      centroid_lat: 37.8199,
      centroid_lon: -122.4783,
      radius_meters: 300,
      created_at: "2026-11-12T16:00:00Z",
      address: {
        display_name: "Harbor Suspension Bridge, Marine Corridor, San Francisco, CA",
        road: "Marine Corridor",
        city: "San Francisco",
        county: "San Francisco County",
        state: "California",
        postcode: "94129",
        country: "United States",
      },
    },
    {
      shoot_id: "shoot-solar-2026",
      project_name: "Mesa Solar Array Progress Survey",
      centroid_lat: 33.4152,
      centroid_lon: -111.8315,
      radius_meters: 350,
      created_at: "2026-11-14T18:20:00Z",
      address: {
        display_name: "East Mesa Photovoltaic Field, Mesa, AZ",
        road: "Solar Ridge Access Rd",
        city: "Mesa",
        county: "Maricopa County",
        state: "Arizona",
        postcode: "85207",
        country: "United States",
      },
    },
  ];

  stagedFiles: StagedFile[] = [
    { path: "/storage/active/harbor_bridge/DJI_20261118_094211_0014.JPG", folder: "harbor_bridge", name: "DJI_20261118_094211_0014.JPG" },
    { path: "/storage/active/harbor_bridge/DJI_20261118_094305_0015.JPG", folder: "harbor_bridge", name: "DJI_20261118_094305_0015.JPG" },
    { path: "/storage/active/mesa_solar/DJI_20261118_141022_0108.JPG", folder: "mesa_solar", name: "DJI_20261118_141022_0108.JPG" },
    { path: "/storage/active/models_3d/harbor_pier_part3.glb", folder: "models_3d", name: "harbor_pier_part3.glb" },
  ];

  assets: AssetRecord[] = [
    {
      asset_id: "BAM-20261112-HBR01",
      unique_stem: "HBR01A2F",
      identifier: "bam_harbor_20261112_hbr01",
      identifier_locked: false,
      original_filename: "DJI_20261112_161012_0001.JPG",
      source_path: "/storage/active/harbor_bridge/DJI_20261112_161012_0001.JPG",
      original_sha256: sha256("DJI_20261112_161012_0001.JPG"),
      media_kind: "still",
      shoot_id: "shoot-harbor-2026",
      captured_at: "2026-11-12T16:10:12Z",
      sensor_model: "DJI Mavic 3 Enterprise",
      focal_length: "24mm",
      gimbal_pitch: -42.5,
      camera_pitch: -42.5,
      gimbal_yaw: 58,
      latitude: 37.81992,
      longitude: -122.47835,
      altitude_meters: 84.2,
      rel_altitude: 72.4,
      describes: "Medium-oblique aerial inspection frame of the north suspension tower steel cables and roadway deck under clear afternoon light.",
      alt: "Aerial oblique view of Harbor Bridge north suspension tower and steel cables over water.",
      service_tags: ["infrastructure", "bridge-inspection", "aerial-oblique", "coastal"],
      workflow_state: "PENDING_HUMAN",
      created_at: "2026-11-12T16:18:00Z",
      notes: "Clear rivet detail on east flange.",
      feedback: [],
      training_candidate: true,
      scene_group: "harbor-north-tower",
      indicators: {
        progression: { indicator: "before_after", visit_count: 2 },
        view_group: { size: 3 },
      },
      final_output: {
        classification: { shot_type: "oblique_inspection", media_genre: "infrastructure", domain: "civil_engineering" },
        content: {
          description: "Medium-oblique aerial inspection frame of the north suspension tower steel cables and roadway deck under clear afternoon light.",
          alt_text: "Aerial oblique view of Harbor Bridge north suspension tower and steel cables over water.",
          tags: ["infrastructure", "bridge-inspection", "aerial-oblique", "coastal"],
          primary_subjects: ["suspension bridge tower", "main cables"],
          objects: ["roadway deck", "maintenance catwalk"],
          structures: ["north tower"],
        },
        photogrammetry: { suitable_for_model: true, overlap_hint: "high" },
        view: { label: "North tower east elevation" },
        quality: { sharpness: 0.94, exposure: "balanced", motion_blur: false },
      },
    },
    {
      asset_id: "BAM-20261115-HBR02",
      unique_stem: "HBR02C9E",
      identifier: "bam_harbor_20261115_hbr02",
      identifier_locked: false,
      original_filename: "DJI_20261115_162240_0002.JPG",
      source_path: "/storage/active/harbor_bridge/DJI_20261115_162240_0002.JPG",
      original_sha256: sha256("DJI_20261115_162240_0002.JPG"),
      media_kind: "still",
      shoot_id: "shoot-harbor-2026",
      captured_at: "2026-11-15T16:22:40Z",
      sensor_model: "DJI Mavic 3 Enterprise",
      focal_length: "24mm",
      gimbal_pitch: -89.4,
      camera_pitch: -89.4,
      gimbal_yaw: 62,
      latitude: 37.82001,
      longitude: -122.47821,
      altitude_meters: 96.5,
      rel_altitude: 88.0,
      describes: "Nadir orthomosaic mapping pass directly above the bridge deck expansion joint and lane markings.",
      alt: "Straight-down aerial mapping image of bridge roadway deck and structural expansion joint.",
      service_tags: ["orthomosaic", "nadir", "bridge-inspection", "mapping"],
      workflow_state: "PENDING_HUMAN",
      created_at: "2026-11-15T16:30:00Z",
      notes: "",
      feedback: [],
      training_candidate: false,
      scene_group: "harbor-north-tower",
      indicators: {
        progression: { indicator: "before_after", visit_count: 2 },
        model_candidate: { set_key: "model-set-1", set_size: 2, ready: false },
      },
      final_output: {
        classification: { shot_type: "nadir_mapping", media_genre: "photogrammetry", domain: "civil_engineering" },
        content: {
          description: "Nadir orthomosaic mapping pass directly above the bridge deck expansion joint and lane markings.",
          alt_text: "Straight-down aerial mapping image of bridge roadway deck and structural expansion joint.",
          tags: ["orthomosaic", "nadir", "bridge-inspection", "mapping"],
          primary_subjects: ["bridge deck", "expansion joint"],
          objects: ["lane dividers", "guardrail"],
          structures: ["roadway span"],
        },
        photogrammetry: { suitable_for_model: true, overlap_hint: "80%" },
        view: { label: "North tower nadir deck" },
        quality: { sharpness: 0.91, exposure: "balanced", motion_blur: false },
      },
    },
    {
      asset_id: "BAM-20261114-SLR01",
      unique_stem: "SLR01B4D",
      identifier: "bam_solar_20261114_slr01",
      identifier_locked: true,
      original_filename: "DJI_20261114_183110_0041.JPG",
      source_path: "/storage/originals/mesa_solar/DJI_20261114_183110_0041.JPG",
      original_sha256: sha256("DJI_20261114_183110_0041.JPG"),
      media_kind: "still",
      shoot_id: "shoot-solar-2026",
      captured_at: "2026-11-14T18:31:10Z",
      sensor_model: "DJI Zenmuse P1",
      focal_length: "35mm",
      gimbal_pitch: -65.0,
      camera_pitch: -65.0,
      gimbal_yaw: 182,
      latitude: 33.41525,
      longitude: -111.83148,
      altitude_meters: 112.0,
      rel_altitude: 95.0,
      describes: "High-oblique overview of Sector B photovoltaic tracker rows showing newly mounted inverter skids and perimeter trenching.",
      alt: "Aerial high-oblique photo of Mesa Solar Farm Sector B photovoltaic panel rows and inverter skids.",
      service_tags: ["solar", "construction-progress", "photogrammetry", "energy"],
      workflow_state: "APPROVED",
      created_at: "2026-11-14T18:45:00Z",
      notes: "Approved for November client progress report.",
      feedback: [{ text: "Accurate identification of inverter skids.", at: "2026-11-14T19:15:00Z" }],
      training_candidate: true,
      scene_group: "mesa-sector-b",
      indicators: {
        view_group: { size: 2 },
      },
      final_output: {
        classification: { shot_type: "high_oblique", media_genre: "construction", domain: "renewable_energy" },
        content: {
          description: "High-oblique overview of Sector B photovoltaic tracker rows showing newly mounted inverter skids and perimeter trenching.",
          alt_text: "Aerial high-oblique photo of Mesa Solar Farm Sector B photovoltaic panel rows and inverter skids.",
          tags: ["solar", "construction-progress", "photogrammetry", "energy"],
          primary_subjects: ["solar tracker rows", "inverter skids"],
          objects: ["service road", "perimeter fence"],
          structures: ["substation pad"],
        },
        photogrammetry: { suitable_for_model: true },
        view: { label: "Sector B south overview" },
        quality: { sharpness: 0.96, exposure: "balanced", motion_blur: false },
        human: { edited: false, training_candidate: true },
      },
    },
    {
      asset_id: "BAM-20261114-SLR02",
      unique_stem: "SLR02E8A",
      identifier: "bam_solar_20261114_slr02",
      identifier_locked: false,
      original_filename: "DJI_20261114_183600_0042.MP4",
      source_path: "/storage/active/mesa_solar/DJI_20261114_183600_0042.MP4",
      original_sha256: sha256("DJI_20261114_183600_0042.MP4"),
      media_kind: "video",
      shoot_id: "shoot-solar-2026",
      captured_at: "2026-11-14T18:36:00Z",
      sensor_model: "DJI Mavic 3 Enterprise",
      focal_length: "24mm",
      gimbal_pitch: -28.0,
      camera_pitch: -28.0,
      gimbal_yaw: 195,
      latitude: 33.41541,
      longitude: -111.83122,
      altitude_meters: 58.0,
      rel_altitude: 42.0,
      describes: "42-second low-oblique flyover clip tracking east-to-west across installed photovoltaic rows toward the substation.",
      alt: "Aerial drone flyover video of solar panel rows moving toward the substation pad.",
      service_tags: ["solar", "flyover-video", "progress"],
      workflow_state: "PENDING_HUMAN",
      created_at: "2026-11-14T18:50:00Z",
      notes: "",
      feedback: [],
      training_candidate: false,
      scene_group: "mesa-sector-b",
      indicators: {},
      final_output: {
        classification: { shot_type: "low_oblique_tracking", media_genre: "construction", domain: "renewable_energy" },
        content: {
          description: "42-second low-oblique flyover clip tracking east-to-west across installed photovoltaic rows toward the substation.",
          alt_text: "Aerial drone flyover video of solar panel rows moving toward the substation pad.",
          tags: ["solar", "flyover-video", "progress"],
          primary_subjects: ["solar arrays", "substation"],
        },
        view: { label: "Sector B flyover corridor" },
        video: {
          duration_s: 42,
          summary: "Smooth lateral tracking shot across completed tracker rows ending on the east substation.",
          timeline: ["00:00 — Entry over east perimeter", "00:18 — Mid-array inverter skids", "00:36 — Substation reveal"],
        },
        quality: { sharpness: 0.89, exposure: "balanced", motion_blur: false },
      },
    },
    {
      asset_id: "BAM-20261112-MOD01",
      unique_stem: "MOD01F3C",
      identifier: "bam_harbor_20261112_mod01",
      identifier_locked: false,
      original_filename: "harbor_pier_part1.glb",
      source_path: "/storage/active/models_3d/harbor_pier_part1.glb",
      original_sha256: sha256("harbor_pier_part1.glb"),
      media_kind: "3d_model",
      shoot_id: "shoot-harbor-2026",
      captured_at: "2026-11-12T17:05:00Z",
      sensor_model: "Photogrammetry Mesh",
      focal_length: null,
      gimbal_pitch: null,
      latitude: 37.81988,
      longitude: -122.47841,
      altitude_meters: 15.0,
      describes: "Textured 3D mesh tile (Part 1 of 2) covering the lower concrete caisson of the Harbor Bridge north pier.",
      alt: "3D photogrammetry mesh tile of Harbor Bridge concrete pier base (Part 1).",
      service_tags: ["3d-model", "photogrammetry", "bridge-inspection"],
      workflow_state: "APPROVED",
      created_at: "2026-11-12T17:12:00Z",
      notes: "Paired with part 2.",
      feedback: [],
      training_candidate: false,
      indicators: {},
      final_output: {
        classification: { shot_type: "3d_mesh_tile", media_genre: "photogrammetry" },
        content: {
          description: "Textured 3D mesh tile (Part 1 of 2) covering the lower concrete caisson of the Harbor Bridge north pier.",
          alt_text: "3D photogrammetry mesh tile of Harbor Bridge concrete pier base (Part 1).",
          tags: ["3d-model", "photogrammetry", "bridge-inspection"],
        },
      },
    },
    {
      asset_id: "BAM-20261112-MOD02",
      unique_stem: "MOD02A7B",
      identifier: "bam_harbor_20261112_mod02",
      identifier_locked: false,
      original_filename: "harbor_pier_part2.glb",
      source_path: "/storage/active/models_3d/harbor_pier_part2.glb",
      original_sha256: sha256("harbor_pier_part2.glb"),
      media_kind: "3d_model",
      shoot_id: "shoot-harbor-2026",
      captured_at: "2026-11-12T17:06:00Z",
      sensor_model: "Photogrammetry Mesh",
      focal_length: null,
      gimbal_pitch: null,
      latitude: 37.81989,
      longitude: -122.47839,
      altitude_meters: 35.0,
      describes: "Textured 3D mesh tile (Part 2 of 2) covering the upper steel column and saddle of the Harbor Bridge north pier.",
      alt: "3D photogrammetry mesh tile of Harbor Bridge upper steel tower section (Part 2).",
      service_tags: ["3d-model", "photogrammetry", "bridge-inspection"],
      workflow_state: "INGESTED",
      created_at: "2026-11-18T12:00:00Z",
      notes: "",
      feedback: [],
      training_candidate: false,
      indicators: {},
      final_output: null,
    },
  ];

  evaluations: EvalRecord[] = [
    {
      eval_id: "eval-hbr01-1",
      asset_id: "BAM-20261112-HBR01",
      screener_output: {
        model: "moondream",
        scene: "suspension bridge steel tower over coastal waterway",
        lighting: "clear afternoon sun",
        hazards_visible: false,
        confidence: 0.91,
      },
      verifier_output: {
        model: "llava",
        verified_subjects: ["suspension tower", "steel cables", "bridge deck"],
        quality: "high resolution, crisp structural edges",
        matches_telemetry: true,
        confidence: 0.93,
      },
      judge_output: {
        model: "llama3.1",
        agreementScore: 0.92,
        decision: "READY_FOR_HUMAN",
        summary: "High agreement between Moondream and LLaVA on bridge tower structural inspection frame.",
        tags: ["infrastructure", "bridge-inspection", "aerial-oblique", "coastal"],
      },
      deep_output: null,
      agreement_score: 0.92,
      evaluated_at: "2026-11-12T16:20:00Z",
    },
    {
      eval_id: "eval-hbr02-1",
      asset_id: "BAM-20261115-HBR02",
      screener_output: {
        model: "moondream",
        scene: "top-down view of highway lanes and bridge joint",
        lighting: "even daylight",
        confidence: 0.84,
      },
      verifier_output: {
        model: "llava",
        verified_subjects: ["roadway deck", "expansion joint", "lane markings"],
        quality: "sharp nadir mapping frame",
        matches_telemetry: true,
        confidence: 0.88,
      },
      judge_output: {
        model: "llama3.1",
        agreementScore: 0.86,
        decision: "READY_FOR_HUMAN",
        summary: "Consistent nadir orthomosaic assessment; slight variance on traffic density.",
        tags: ["orthomosaic", "nadir", "bridge-inspection", "mapping"],
      },
      deep_output: null,
      agreement_score: 0.86,
      evaluated_at: "2026-11-15T16:33:00Z",
    },
    {
      eval_id: "eval-slr01-1",
      asset_id: "BAM-20261114-SLR01",
      screener_output: {
        model: "moondream",
        scene: "large utility-scale solar farm rows in arid terrain",
        confidence: 0.95,
      },
      verifier_output: {
        model: "llava",
        verified_subjects: ["photovoltaic tracker arrays", "inverter skids", "access road"],
        matches_telemetry: true,
        confidence: 0.96,
      },
      judge_output: {
        model: "llama3.1",
        agreementScore: 0.95,
        decision: "READY_FOR_HUMAN",
        summary: "Strong multi-model consensus for Sector B construction survey.",
        tags: ["solar", "construction-progress", "photogrammetry", "energy"],
      },
      deep_output: null,
      agreement_score: 0.95,
      evaluated_at: "2026-11-14T18:48:00Z",
    },
    {
      eval_id: "eval-slr02-1",
      asset_id: "BAM-20261114-SLR02",
      screener_output: {
        model: "moondream",
        scene: "aerial video tracking across solar panels",
        confidence: 0.72,
      },
      verifier_output: {
        model: "llava",
        verified_subjects: ["solar tracker rows", "substation pad"],
        matches_telemetry: true,
        confidence: 0.76,
      },
      judge_output: {
        model: "llama3.1",
        agreementScore: 0.74,
        decision: "READY_FOR_HUMAN",
        summary: "Moderate agreement across sampled video frames; recommend operator check of substation reveal.",
        tags: ["solar", "flyover-video", "progress"],
      },
      deep_output: null,
      agreement_score: 0.74,
      evaluated_at: "2026-11-14T18:54:00Z",
    },
    {
      eval_id: "eval-mod01-1",
      asset_id: "BAM-20261112-MOD01",
      screener_output: { model: "moondream", scene: "3D concrete bridge pier mesh", confidence: 0.89 },
      verifier_output: { model: "llava", verified_subjects: ["concrete caisson", "3D mesh"], confidence: 0.9 },
      judge_output: { model: "llama3.1", agreementScore: 0.89, group_id: "harbor_pier", summary: "Validated mesh tile 1." },
      deep_output: null,
      agreement_score: 0.89,
      evaluated_at: "2026-11-12T17:15:00Z",
    },
  ];

  copies: CopyRecord[] = [
    {
      copy_id: "copy-slr01-w1600-webp",
      parent_asset_id: "BAM-20261114-SLR01",
      size_tier: "web",
      version_code: "v1",
      format: "webp",
      width: 1600,
      height: 1067,
      byte_size: 284120,
      sha256: sha256("copy-slr01-w1600-webp"),
      r2_storage_key: "exports/shoot-solar-2026/SLR01B4D_web.webp",
      created_at: "2026-11-14T19:20:00Z",
    },
    {
      copy_id: "copy-slr01-w480-avif",
      parent_asset_id: "BAM-20261114-SLR01",
      size_tier: "thumb",
      version_code: "v1",
      format: "avif",
      width: 480,
      height: 320,
      byte_size: 41280,
      sha256: sha256("copy-slr01-w480-avif"),
      r2_storage_key: "exports/shoot-solar-2026/SLR01B4D_thumb.avif",
      created_at: "2026-11-14T19:20:02Z",
    },
  ];

  videoFrames: FrameRecord[] = [
    {
      frame_id: "vf-slr02-0",
      asset_id: "BAM-20261114-SLR02",
      frame_index: 0,
      time_s: 2.0,
      pass_name: "base",
      output: {
        classification: { shot_type: "low_oblique" },
        content: { description: "Entry over east perimeter fence and tracker rows." },
        quality: { sharpness: 0.91 },
      },
    },
    {
      frame_id: "vf-slr02-1",
      asset_id: "BAM-20261114-SLR02",
      frame_index: 1,
      time_s: 18.0,
      pass_name: "base",
      output: {
        classification: { shot_type: "low_oblique" },
        content: { description: "Mid-array inverter skids and cabling trench." },
        quality: { sharpness: 0.88 },
      },
    },
    {
      frame_id: "vf-slr02-2",
      asset_id: "BAM-20261114-SLR02",
      frame_index: 2,
      time_s: 36.0,
      pass_name: "detail",
      output: {
        classification: { shot_type: "low_oblique" },
        content: { description: "Substation pad and transformer yard reveal." },
        quality: { sharpness: 0.92 },
      },
    },
  ];

  humanCorrections: Array<Record<string, any>> = [
    {
      correction_id: "corr-slr01-1",
      asset_id: "BAM-20261114-SLR01",
      prior_ai_output: {
        describes: "Aerial photo of solar panels.",
        alt: "Solar panels from above.",
        service_tags: ["solar"],
      },
      human_approved_output: {
        action: "approve",
        reviewer: "operator",
        at: "2026-11-14T19:15:00Z",
        prior_state: "PENDING_HUMAN",
        rights_confirmed: true,
        metadata_confirmed: true,
      },
      correction_notes: "Approved for November client progress report.",
    },
  ];

  trainingExamples: Array<Record<string, any>> = [
    {
      asset_id: "BAM-20261114-SLR01",
      identifier: "bam_solar_20261114_slr01",
      media_kind: "still",
      source: "flag",
      before_output: null,
      after_output: null,
      feedback: "Accurate identification of inverter skids.",
      notes: "Approved for November client progress report.",
      original_ref: "/storage/originals/mesa_solar/DJI_20261114_183110_0041.JPG",
      created_at: "2026-11-14T19:15:00Z",
    },
  ];

  eventHistory: PipelineEvent[] = [
    { t: "2026-11-18T12:00:00Z", msg: "[SYSTEM] BAM Media Factory API server started (in-memory store active)" },
    { t: "2026-11-18T12:00:02Z", msg: "[SYSTEM] Loaded 6 assets across 2 shoots; 4 staged files awaiting ingestion" },
  ];

  private listeners = new Set<(msg: string) => void>();

  onLog(cb: (msg: string) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  emitLog(msg: string) {
    const entry = { t: new Date().toISOString(), msg };
    this.eventHistory.unshift(entry);
    if (this.eventHistory.length > 500) this.eventHistory.length = 500;
    for (const cb of this.listeners) {
      try {
        cb(msg);
      } catch {
        // ignore dropped client
      }
    }
  }

  latestEval(assetId: string): EvalRecord | undefined {
    return this.evaluations
      .filter((e) => e.asset_id === assetId)
      .sort((a, b) => Date.parse(b.evaluated_at) - Date.parse(a.evaluated_at))[0];
  }

  joinedAssets(): Array<Record<string, any>> {
    return this.assets.map((a) => {
      const ev = this.latestEval(a.asset_id);
      const copyCount = this.copies.filter((c) => c.parent_asset_id === a.asset_id).length;
      return {
        ...a,
        camera_pitch: a.camera_pitch ?? a.gimbal_pitch,
        eval_id: ev?.eval_id ?? null,
        screener_output: ev?.screener_output ?? null,
        verifier_output: ev?.verifier_output ?? null,
        judge_output: ev?.judge_output ?? null,
        deep_output: ev?.deep_output ?? null,
        agreement_score: ev?.agreement_score ?? null,
        evaluated_at: ev?.evaluated_at ?? null,
        copy_count: copyCount,
        drone_angle: droneAngleOf(a.gimbal_pitch, this.settings),
      };
    });
  }

  ingestPaths(paths: string[]): AssetRecord[] {
    const created: AssetRecord[] = [];
    for (const p of paths) {
      const idx = this.stagedFiles.findIndex((f) => f.path === p);
      if (idx === -1) continue;
      const staged = this.stagedFiles[idx];
      this.stagedFiles.splice(idx, 1);
      const ext = staged.name.split(".").pop()?.toLowerCase() || "";
      const kind = ["mp4", "mov", "m4v"].includes(ext)
        ? "video"
        : ["glb", "gltf", "obj", "usdz"].includes(ext)
          ? "3d_model"
          : "still";
      const stem = crypto.randomBytes(4).toString("hex").toUpperCase();
      const assetId = `BAM-${new Date().toISOString().slice(0, 10).replace(/-/g, "")}-${stem.slice(0, 5)}`;
      const isSolar = staged.folder.includes("solar");
      const shootId = isSolar ? "shoot-solar-2026" : "shoot-harbor-2026";
      const baseLat = isSolar ? 33.4153 : 37.81995;
      const baseLon = isSolar ? -111.8314 : -122.4783;
      const jitter = (Math.random() - 0.5) * 0.0004;

      const record: AssetRecord = {
        asset_id: assetId,
        unique_stem: stem,
        identifier: `bam_${isSolar ? "solar" : "harbor"}_${stem.toLowerCase()}`,
        identifier_locked: false,
        original_filename: staged.name,
        source_path: staged.path,
        original_sha256: sha256(staged.path + Date.now()),
        media_kind: kind,
        shoot_id: shootId,
        captured_at: new Date().toISOString(),
        sensor_model: kind === "3d_model" ? "Photogrammetry Mesh" : "DJI Mavic 3 Enterprise",
        focal_length: kind === "3d_model" ? null : "24mm",
        gimbal_pitch: kind === "3d_model" ? null : -45.0,
        camera_pitch: kind === "3d_model" ? null : -45.0,
        latitude: baseLat + jitter,
        longitude: baseLon + jitter,
        altitude_meters: kind === "3d_model" ? 20.0 : 82.0,
        rel_altitude: kind === "3d_model" ? 20.0 : 70.0,
        describes: null,
        alt: null,
        service_tags: [],
        workflow_state: "INGESTED",
        created_at: new Date().toISOString(),
        notes: "",
        feedback: [],
        training_candidate: false,
        indicators: {},
        final_output: null,
      };
      this.assets.unshift(record);
      created.push(record);
      this.emitLog(`[INGEST] Hashed & recorded ${staged.name} -> ${assetId} (INGESTED)`);
    }
    return created;
  }

  evaluateIngested(forceCloud = false): number {
    const targets = this.assets.filter((a) => a.workflow_state === "INGESTED");
    if (!targets.length) {
      this.emitLog("[ORCHESTRATOR] No INGESTED assets waiting for evaluation.");
      return 0;
    }
    const backendLabel = forceCloud ? "cloud:azure" : "local";
    for (const a of targets) {
      const score = Math.round((0.82 + Math.random() * 0.14) * 100) / 100;
      const desc = `Evaluated aerial ${a.media_kind} asset (${a.original_filename}) captured during ${a.shoot_id || "unassigned shoot"}.`;
      const alt = `Aerial ${a.media_kind} frame ${a.original_filename} from ${a.sensor_model || "drone sensor"}.`;
      const tags = [a.media_kind, a.shoot_id?.includes("solar") ? "solar" : "infrastructure", "aerial"];

      const ev: EvalRecord = {
        eval_id: `eval-${crypto.randomBytes(4).toString("hex")}`,
        asset_id: a.asset_id,
        screener_output: {
          model: "moondream",
          scene: desc,
          confidence: Math.max(0.5, Number((score - 0.03).toFixed(2))),
        },
        verifier_output: {
          model: "llava",
          verified_subjects: tags,
          matches_telemetry: true,
          confidence: Math.min(0.99, Number((score + 0.02).toFixed(2))),
        },
        judge_output: {
          model: "llama3.1",
          ...(forceCloud ? { backend: "azure" } : {}),
          agreementScore: score,
          decision: "READY_FOR_HUMAN",
          summary: `Three-model consensus (${Math.round(score * 100)}%) via ${backendLabel}.`,
          tags,
        },
        deep_output: forceCloud ? { provider: "azure", verified: true, confidence: score } : null,
        agreement_score: score,
        evaluated_at: new Date().toISOString(),
      };
      this.evaluations.unshift(ev);
      a.describes = desc;
      a.alt = alt;
      a.service_tags = tags;
      a.workflow_state = "PENDING_HUMAN";
      a.final_output = {
        classification: { shot_type: a.gimbal_pitch && a.gimbal_pitch <= -80 ? "nadir_mapping" : "oblique", media_genre: "aerial" },
        content: {
          description: desc,
          alt_text: alt,
          tags,
          primary_subjects: tags,
        },
        photogrammetry: { suitable_for_model: a.media_kind === "still" },
        view: { label: "Evaluated aerial vantage" },
        quality: { sharpness: 0.9, exposure: "balanced" },
      };
      this.emitLog(`[PHASE 1A/1B/1C] Evaluated ${a.asset_id} -> agreement ${Math.round(score * 100)}% (PENDING_HUMAN)`);
    }
    return targets.length;
  }
}

export const store = new InMemoryPipelineStore();
