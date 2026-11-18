import httpx
import json
import os
import re
import sys
import logging
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
import exifread

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_SCRIPTS_DIR), _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import video_frames as vf  # noqa: E402
from server import exif_meta  # noqa: E402
from server import finalize as fin  # noqa: E402
from server import media_intel as mi  # noqa: E402
from server import output_schema as osch  # noqa: E402

DB_DSN = os.getenv("DATABASE_URL")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("bam.vision")

_DRONE_TELEMETRY_KEYS = ("make", "lens_model", "iso", "shutter_speed", "aperture", "camera_serial", "gimbal_yaw",
                         "flight_yaw", "flight_roll", "rel_altitude", "abs_altitude", "capture_utc_offset",
                         "captured_at_local")


def get_exif_data(filepath):
    """Extracts comprehensive EXIF telemetry, angles, orientation, and GPS coordinates."""
    def _convert_gps(coords, ref):
        try:
            d = float(coords.values[0].num) / float(coords.values[0].den)
            m = float(coords.values[1].num) / float(coords.values[1].den)
            s = float(coords.values[2].num) / float(coords.values[2].den)
            decimal = d + (m / 60.0) + (s / 3600.0)
            if ref in ['S', 'W']: decimal = -decimal
            return decimal
        except:
            return None

    telemetry = {
        "sensor": None, 
        "lat": None, 
        "lon": None, 
        "alt": None, 
        "focal_length": None, 
        "orientation": None,
        "camera_pitch": None,
        "gimbal_pitch": None,
        "captured_at": None
    }
    for _k in _DRONE_TELEMETRY_KEYS:
        telemetry[_k] = None
    telemetry["raw_exif"] = None
    
    if not os.path.exists(filepath):
        logger.warning("Source file is missing; path=%s", filepath)
        return telemetry
    
    try:
        logger.info("Reading extended EXIF telemetry; path=%s", filepath)
        with open(filepath, 'rb') as f:
            tags = exifread.process_file(f, details=True)
            
            if 'Image Model' in tags:
                telemetry["sensor"] = str(tags['Image Model'])
            if 'EXIF FocalLength' in tags:
                telemetry["focal_length"] = str(tags['EXIF FocalLength'])
            if 'Image Orientation' in tags:
                telemetry["orientation"] = str(tags['Image Orientation'])
            for key in ('EXIF DateTimeOriginal', 'EXIF DateTimeDigitized', 'Image DateTime'):
                if key in tags:
                    raw = str(tags[key]).strip()
                    try:
                        telemetry["captured_at"] = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
                        break
                    except ValueError:
                        pass
            if 'GPS GPSLatitude' in tags and 'GPS GPSLatitudeRef' in tags:
                telemetry["lat"] = _convert_gps(tags['GPS GPSLatitude'], str(tags['GPS GPSLatitudeRef']))
            if 'GPS GPSLongitude' in tags and 'GPS GPSLongitudeRef' in tags:
                telemetry["lon"] = _convert_gps(tags['GPS GPSLongitude'], str(tags['GPS GPSLongitudeRef']))
            if 'GPS GPSAltitude' in tags:
                telemetry["alt"] = float(tags['GPS GPSAltitude'].values[0].num) / float(tags['GPS GPSAltitude'].values[0].den)
                
            # Scan for embedded drone gimbal/pitch telemetry keys if present in maker notes
            for tag_key in tags.keys():
                if 'pitch' in tag_key.lower() or 'gimbal' in tag_key.lower():
                    telemetry["camera_pitch"] = str(tags[tag_key])
                    m = re.search(r"-?\d+(?:\.\d+)?", telemetry["camera_pitch"])
                    telemetry["gimbal_pitch"] = float(m.group(0)) if m else None
                    break
    except Exception as e:
        logger.warning("Extended EXIF extraction failed; path=%s error=%s", filepath, e)

    try:
        meta = exif_meta.build_asset_meta(exif_meta.read_exif_dump(filepath))
        for k in _DRONE_TELEMETRY_KEYS:
            if meta.get(k) is not None:
                telemetry[k] = meta[k]
        for src, dst in (("sensor", "sensor"), ("focal_length", "focal_length"), ("lat", "lat"), ("lon", "lon"),
                         ("alt", "alt"), ("captured_at", "captured_at")):
            if meta.get(src) is not None and (telemetry.get(dst) is None or src == "captured_at"):
                telemetry[dst] = meta[src]
        if meta.get("gimbal_pitch") is not None:
            telemetry["gimbal_pitch"] = meta["gimbal_pitch"]
            telemetry["camera_pitch"] = telemetry.get("camera_pitch") or str(meta["gimbal_pitch"])
        telemetry["raw_exif"] = meta.get("raw_exif") or None
    except Exception as e:
        logger.warning("Drone metadata extraction failed; path=%s error=%s", filepath, e)

    return telemetry

def call_ollama(model: str, prompt: str, image_path: str = None):
    from vision_client import call_free_model, validate_output
    if os.getenv("BAM_VISION_BACKEND", "ollama") == "openrouter":
        return call_free_model(prompt, image_path)
    payload = {"model": model, "prompt": prompt, "stream": False, "keep_alive": "5m", "format": "json"}
    if image_path:
        if not os.path.isfile(image_path) or os.path.getsize(image_path)>10*1024*1024:
            raise ValueError("Use an existing preview below 10 MiB")
        import base64
        with open(image_path, "rb") as img:
            payload["images"] = [base64.b64encode(img.read()).decode("utf-8")]
    try:
        logger.info("Calling Ollama; model=%s image_attached=%s", model, "images" in payload)
        res = httpx.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=120.0)
        res.raise_for_status()
        response = json.loads(res.json().get("response", "{}"))
        if not isinstance(response, dict) or not response:
            raise ValueError(f"{model} returned an empty or invalid JSON object")
        logger.info("Ollama response accepted; model=%s fields=%s", model, sorted(response.keys()))
        return validate_output(response)
    except Exception as e:
        logger.exception("Ollama call failed; model=%s error=%s", model, e)
        raise

SCREENER_MODEL = (os.getenv("BAM_SCREENER_MODEL") or os.getenv("OLLAMA_SCREENER_MODEL")
                  or os.getenv("OLLAMA_VISION_MODEL") or "moondream")
VERIFIER_MODEL = (os.getenv("BAM_VERIFIER_MODEL") or os.getenv("OLLAMA_VERIFIER_MODEL")
                  or os.getenv("OLLAMA_VISION_MODEL") or "llava")
JUDGE_MODEL = os.getenv("BAM_JUDGE_MODEL") or os.getenv("OLLAMA_JUDGE_MODEL") or "llama3.1"
BACKEND = f"ollama:{SCREENER_MODEL}+{VERIFIER_MODEL}+{JUDGE_MODEL}"
_KIND_MAP = {"still": "still", "video": "video", "3d_model": "model3d"}


def schema_kind(media_kind):
    return _KIND_MAP.get(media_kind or "still", "still")


def _iso(v):
    return v.isoformat(sep=" ") if hasattr(v, "isoformat") else v


def build_telemetry(a):
    """Telemetry dict for prompts/records, taken from the (EXIF-updated) asset row."""
    alt = a.get("altitude_meters")
    return {
        "captured_at": _iso(a.get("captured_at")),
        "latitude": a.get("latitude"),
        "longitude": a.get("longitude"),
        "altitude_m": alt if alt else None,
        "sensor_model": a.get("sensor_model") or a.get("sensor"),
        "focal_length": a.get("focal_length"),
        "gimbal_pitch_deg": a.get("gimbal_pitch"),
        "gimbal_yaw_deg": a.get("gimbal_yaw"),
        "flight_yaw_deg": a.get("flight_yaw"),
        "flight_roll_deg": a.get("flight_roll"),
        "rel_altitude_m": a.get("rel_altitude"),
        "camera_make": a.get("camera_make"),
        "lens_model": a.get("lens_model"),
        "iso": a.get("iso"),
        "shutter_speed": a.get("shutter_speed"),
        "aperture": a.get("aperture"),
        "camera_serial": a.get("camera_serial"),
        "utc_offset": a.get("capture_utc_offset"),
        "captured_at_local": a.get("captured_at_local"),
        "media_kind": a.get("media_kind"),
        "orientation": a.get("orientation"),
        "duration_s": a.get("duration_s"),
    }


def _num(v):
    if v is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group(0)) if m else None


def run_phase_zero(conn, cur, assets):
    """EXIF telemetry -> assets (never overwrite existing values with NULL)."""
    logger.info("Phase 0 started; extracting EXIF telemetry for %d assets", len(assets))
    for a in assets:
        try:
            t = get_exif_data(a["source_path"])
            pitch = _num(t.get("gimbal_pitch", t.get("camera_pitch")))
            cur.execute("""
                UPDATE assets SET sensor_model = COALESCE(%s, sensor_model), latitude = COALESCE(%s, latitude),
                    longitude = COALESCE(%s, longitude), altitude_meters = COALESCE(%s, altitude_meters),
                    focal_length = COALESCE(%s, focal_length), gimbal_pitch = COALESCE(%s, gimbal_pitch),
                    captured_at = COALESCE(%s, captured_at), camera_make = COALESCE(%s, camera_make),
                    lens_model = COALESCE(%s, lens_model), iso = COALESCE(%s, iso),
                    shutter_speed = COALESCE(%s, shutter_speed), aperture = COALESCE(%s, aperture),
                    camera_serial = COALESCE(%s, camera_serial), gimbal_yaw = COALESCE(%s, gimbal_yaw),
                    flight_yaw = COALESCE(%s, flight_yaw), flight_roll = COALESCE(%s, flight_roll),
                    rel_altitude = COALESCE(%s, rel_altitude), abs_altitude = COALESCE(%s, abs_altitude),
                    capture_utc_offset = COALESCE(%s, capture_utc_offset),
                    captured_at_local = COALESCE(%s, captured_at_local),
                    raw_exif = COALESCE(%s::jsonb, raw_exif)
                WHERE asset_id = %s
            """, (t["sensor"], t["lat"], t["lon"], t["alt"], t.get("focal_length"), pitch,
                  t.get("captured_at"), t.get("make"), t.get("lens_model"), t.get("iso"), t.get("shutter_speed"),
                  t.get("aperture"), t.get("camera_serial"), t.get("gimbal_yaw"), t.get("flight_yaw"),
                  t.get("flight_roll"), t.get("rel_altitude"), t.get("abs_altitude"), t.get("capture_utc_offset"),
                  t.get("captured_at_local"), json.dumps(t["raw_exif"]) if t.get("raw_exif") else None,
                  a["asset_id"]))
            merged = {"sensor_model": t["sensor"], "latitude": t["lat"], "longitude": t["lon"],
                      "altitude_meters": t["alt"], "focal_length": t.get("focal_length"), "gimbal_pitch": pitch,
                      "captured_at": t.get("captured_at")}
            for k in _DRONE_TELEMETRY_KEYS:
                merged[k] = t.get(k)
            for k, v in merged.items():
                if v is not None:
                    a[k] = v
            a["orientation"] = t.get("orientation")
            if t.get("make"):
                a["camera_make"] = t["make"]
            a["camera_pitch"] = t.get("camera_pitch")
        except Exception:
            logger.exception("Phase 0 failed for asset_id=%s", a.get("asset_id"))
    conn.commit()
    logger.info("Phase 0 complete; telemetry updates committed")


def _compact_frame(f):
    out = f.get("output") or {}
    content = out.get("content") or {}
    return {"time_s": f.get("time_s"), "shot_type": (out.get("classification") or {}).get("shot_type"),
            "primary_subjects": content.get("primary_subjects"), "description": content.get("description"),
            "changes_since_previous": out.get("changes_since_previous")}


def _analyse_frames(ctx, pass_name, stage, model, frames, base_done):
    """Run the frame prompt for each extracted frame, in time order. Returns list of frame dicts."""
    done = []
    known = list(base_done)
    for fr in sorted(frames, key=lambda x: x["time_s"]):
        context = {"time_s": fr["time_s"], "duration_s": ctx["telemetry"].get("duration_s")}
        if stage == "video_detail":
            earlier = [k for k in known if k["time_s"] < fr["time_s"]]
            context["base_frames"] = [_compact_frame(k) for k in base_done]
            if earlier:
                context["previous_frame"] = _compact_frame(max(earlier, key=lambda k: k["time_s"]))
        prompt = osch.build_prompt("video_frame", ctx["telemetry"], stage=stage, context=context,
                                   feedback=ctx["feedback"])
        raw = call_ollama(model, prompt, fr["file_path"])
        item = dict(fr, pass_name=pass_name, output=osch.normalize_model_output("video_frame", raw))
        done.append(item)
        known.append(item)
    return done


def video_base_pass(ctx, settings):
    a = ctx["asset"]
    info = vf.probe_video(a["source_path"])
    ctx["telemetry"]["duration_s"] = info["duration_s"]
    ctx["video_info"] = info
    plan = mi.plan_video_frames(info["duration_s"], settings)
    ctx["plan"] = plan
    out_dir = vf.frames_dir(a["asset_id"])
    frames = vf.extract_frames(a["source_path"], plan["base"], out_dir, settings["video_thumb_edge"], "base", 0)
    if not frames:
        raise RuntimeError("no base frames could be extracted")
    ctx["base_frames"] = _analyse_frames(ctx, "base", "video_base", SCREENER_MODEL, frames, [])
    ctx["screener"] = {"base_frames": [dict(_compact_frame(f), frame_index=i, output=f["output"])
                                       for i, f in enumerate(ctx["base_frames"])]}


def video_detail_pass(ctx, settings):
    a = ctx["asset"]
    times = ctx["plan"]["detail"]
    ctx["detail_frames"] = []
    if not times:
        return
    out_dir = vf.frames_dir(a["asset_id"])
    frames = vf.extract_frames(a["source_path"], times, out_dir, settings["video_thumb_edge"], "detail", 0)
    ctx["detail_frames"] = _analyse_frames(ctx, "detail", "video_detail", VERIFIER_MODEL, frames, ctx["base_frames"])


def video_synthesis(ctx):
    context = {"duration_s": ctx["telemetry"].get("duration_s"),
               "base_frames": [_compact_frame(f) for f in ctx["base_frames"]],
               "detail_frames": [_compact_frame(f) for f in ctx["detail_frames"]]}
    prompt = osch.build_prompt("video", ctx["telemetry"], stage="video_synthesis", context=context,
                               feedback=ctx["feedback"])
    ctx["verifier"] = osch.normalize_model_output("video", call_ollama(JUDGE_MODEL, prompt))


def video_extra(ctx):
    frames = []
    for i, f in enumerate(ctx["base_frames"] + ctx["detail_frames"]):
        f["frame_index"] = i
        frames.append({"frame_index": i, "time_s": f["time_s"], "pass": f["pass_name"], "file_path": f["file_path"],
                       "thumb_path": f.get("thumb_path"),
                       "description": (f["output"].get("content") or {}).get("description", "")})
    frames.sort(key=lambda x: x["time_s"])
    syn = ctx["verifier"]
    video = {k: syn.get(k) for k in ("summary", "activity", "scene_changes", "camera_motion", "subjects", "timeline")}
    video.update(duration_s=ctx["telemetry"].get("duration_s"), frames=frames)
    return {"backend": BACKEND, "video": video}


def stage_still(ctx, stage):
    a = ctx["asset"]
    kind = ctx["kind"]
    if stage == "screener":
        prompt = osch.build_prompt(kind, ctx["telemetry"], stage="screener", feedback=ctx["feedback"])
        ctx["screener"] = osch.normalize_model_output(kind, call_ollama(SCREENER_MODEL, prompt, a["source_path"]))
    else:
        prompt = osch.build_prompt(kind, ctx["telemetry"], stage="verifier", context={"screener": ctx["screener"]},
                                   feedback=ctx["feedback"])
        ctx["verifier"] = osch.normalize_model_output(kind, call_ollama(VERIFIER_MODEL, prompt, a["source_path"]))


def judge(ctx):
    context = {"screener": ctx["screener"], "verifier": ctx["verifier"]}
    prompt = osch.build_prompt(ctx["kind"], ctx["telemetry"], stage="judge", context=context, feedback=ctx["feedback"])
    res = call_ollama(JUDGE_MODEL, prompt)
    try:
        score = float(res.get("agreementScore", 0.0))
    except (ValueError, TypeError):
        score = 0.0
    ctx["judge"] = res
    ctx["score"] = max(0.0, min(1.0, score))


def build_final_record(ctx, previous):
    a = ctx["asset"]
    extra = video_extra(ctx) if ctx["kind"] == "video" else {"backend": BACKEND}
    rec = osch.assemble_record(ctx["kind"], a, ctx["telemetry"], ctx["screener"] if ctx["kind"] != "video" else {},
                               ctx["verifier"], ctx["judge"], extra=extra)
    if isinstance(previous, dict) and previous.get("identifier"):
        # a re-run must never lose the locked identity or human annotations
        for k in ("identifier", "identifier_components", "shoot", "human", "indicators"):
            if k in previous:
                rec[k] = previous[k]
    problems = osch.validate_record(rec)
    if problems:
        logger.warning("Record has schema problems; asset_id=%s problems=%s", a["asset_id"], problems[:5])
    return rec


def persist_asset(conn, cur, ctx):
    a = ctx["asset"]
    aid = a["asset_id"]
    rec = build_final_record(ctx, a.get("final_output"))
    content = rec["content"]
    cur.execute("""
        UPDATE assets SET final_output = %s, workflow_state = 'PENDING_HUMAN',
            describes = CASE WHEN identifier_locked THEN %s ELSE describes END,
            alt = CASE WHEN identifier_locked THEN %s ELSE alt END,
            service_tags = CASE WHEN identifier_locked THEN %s::jsonb ELSE service_tags END
        WHERE asset_id = %s
    """, (json.dumps(rec, default=str), content["description"], content["alt_text"],
          json.dumps(content["tags"]), aid))
    cur.execute("""
        INSERT INTO ai_evaluations (asset_id, screener_output, verifier_output, judge_output, agreement_score)
        VALUES (%s, %s, %s, %s, %s)
    """, (aid, json.dumps(ctx["screener"]), json.dumps(ctx["verifier"]), json.dumps(ctx["judge"]), ctx["score"]))
    if ctx["kind"] == "video":
        frames = ctx["base_frames"] + ctx["detail_frames"]
        cur.execute("DELETE FROM video_frames WHERE asset_id = %s AND NOT (frame_index = ANY(%s::integer[]))",
                    (aid, [f["frame_index"] for f in frames]))
        for f in frames:
            cur.execute("""
                INSERT INTO video_frames (asset_id, frame_index, time_s, pass_name, file_path, thumb_path, output)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (asset_id, frame_index) DO UPDATE SET time_s = EXCLUDED.time_s,
                    pass_name = EXCLUDED.pass_name, file_path = EXCLUDED.file_path,
                    thumb_path = EXCLUDED.thumb_path, output = EXCLUDED.output
            """, (aid, f["frame_index"], f["time_s"], f["pass_name"], f["file_path"], f.get("thumb_path"),
                  json.dumps(f["output"])))
    if ctx["feedback"]:
        cur.execute("UPDATE assets SET rework_feedback = NULL WHERE asset_id = %s", (aid,))
    conn.commit()
    logger.info("Asset moved to PENDING_HUMAN; asset_id=%s agreement_score=%s", aid, ctx["score"])


def _run_stage(ctxs, failed, label, fn):
    for aid, ctx in ctxs.items():
        if aid in failed:
            continue
        try:
            fn(ctx)
            logger.info("%s complete for asset_id=%s", label, aid)
        except Exception as e:
            failed[aid] = str(e)
            logger.exception("%s failed for asset_id=%s; leaving it INGESTED", label, aid)


def finalize_batch(conn, asset_ids, settings):
    """Shoot assignment + locked identifiers + indicators for the successfully evaluated assets."""
    if not asset_ids:
        return
    try:
        fin.finalize_assets(conn, asset_ids, settings)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Batch finalize failed; retrying per asset")
        for aid in asset_ids:
            try:
                fin.finalize_assets(conn, [aid], settings)
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception("Finalize failed for asset_id=%s; it stays PENDING_HUMAN without identifier", aid)
    try:
        n = fin.refresh_indicators(conn, settings)
        conn.commit()
        logger.info("Indicators refreshed; updated=%d", n)
    except Exception:
        conn.rollback()
        logger.exception("Indicator refresh failed")


def process_assets(conn, assets, settings=None):
    """Run all phases over the given INGESTED asset rows. Returns {'done': [...], 'failed': {id: error}}."""
    settings = settings or mi.load_settings()
    cur = conn.cursor()
    run_phase_zero(conn, cur, assets)

    ctxs, failed = {}, {}
    for a in assets:
        ctxs[a["asset_id"]] = {"asset": a, "kind": schema_kind(a.get("media_kind")), "telemetry": build_telemetry(a),
                               "feedback": (a.get("rework_feedback") or "").strip() or None}
    stills = {k: c for k, c in ctxs.items() if c["kind"] != "video"}
    videos = {k: c for k, c in ctxs.items() if c["kind"] == "video"}

    logger.info("Phase 1A started; screening %d assets with %s", len(ctxs), SCREENER_MODEL)
    _run_stage(stills, failed, "Phase 1A", lambda c: stage_still(c, "screener"))
    _run_stage(videos, failed, "Phase 1A (video base frames)", lambda c: video_base_pass(c, settings))

    logger.info("Phase 1B started; verifying %d assets with %s", len(ctxs), VERIFIER_MODEL)
    _run_stage(stills, failed, "Phase 1B", lambda c: stage_still(c, "verifier"))
    _run_stage(videos, failed, "Phase 1B (video detail frames)", lambda c: video_detail_pass(c, settings))

    logger.info("Phase 1C started; judging %d assets with %s", len(ctxs), JUDGE_MODEL)
    _run_stage(videos, failed, "Phase 1C (video synthesis)", video_synthesis)
    _run_stage(ctxs, failed, "Phase 1C", judge)

    done = []
    for aid, ctx in ctxs.items():
        if aid in failed:
            continue
        try:
            persist_asset(conn, cur, ctx)
            done.append(aid)
        except Exception as e:
            conn.rollback()
            failed[aid] = str(e)
            logger.exception("Persisting asset failed; asset_id=%s; leaving it INGESTED", aid)
    finalize_batch(conn, done, settings)
    return {"done": done, "failed": failed}


def execute_phased_batch():
    logger.info("Starting vision orchestration batch")
    conn = psycopg2.connect(DB_DSN, cursor_factory=RealDictCursor)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM assets WHERE workflow_state = 'INGESTED'")
        assets = cur.fetchall()
        if not assets:
            logger.info("No INGESTED assets found; batch is complete")
            return
        res = process_assets(conn, [dict(a) for a in assets])
        logger.info("Vision batch complete; done=%d failed=%d; awaiting human review", len(res["done"]), len(res["failed"]))
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        execute_phased_batch()
    except Exception:
        logger.exception("Vision orchestration batch failed")
        raise
