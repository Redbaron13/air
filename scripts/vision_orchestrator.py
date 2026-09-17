import httpx
import json
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import exifread

DB_DSN = os.getenv("DATABASE_URL")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")

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
        "camera_pitch": None
    }
    
    if not os.path.exists(filepath): return telemetry
    
    try:
        with open(filepath, 'rb') as f:
            tags = exifread.process_file(f, details=True)
            
            if 'Image Model' in tags:
                telemetry["sensor"] = str(tags['Image Model'])
            if 'EXIF FocalLength' in tags:
                telemetry["focal_length"] = str(tags['EXIF FocalLength'])
            if 'Image Orientation' in tags:
                telemetry["orientation"] = str(tags['Image Orientation'])
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
                    break
    except Exception as e:
        print(f"EXIF Error: {e}")
        
    return telemetry

def call_ollama(model: str, prompt: str, image_path: str = None):
    payload = {"model": model, "prompt": prompt, "stream": False, "keep_alive": -1, "format": "json"}
    if image_path and os.path.exists(image_path):
        import base64
        with open(image_path, "rb") as img:
            payload["images"] = [base64.b64encode(img.read()).decode("utf-8")]
    try:
        res = httpx.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=None)
        if res.status_code != 200:
            return {}
        return json.loads(res.json().get("response", "{}"))
    except Exception as e:
        print(f"[AI EXCEPTION] {model}: {str(e)}")
        return {}

def execute_phased_batch():
    conn = psycopg2.connect(DB_DSN, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM assets WHERE workflow_state = 'INGESTED'")
    assets = cur.fetchall()
    if not assets:
        print("No assets to process.")
        conn.close()
        return

    # Phase 0: Full Telemetry & Angle Extraction
    print(f"\n[PHASE 0] Extracting complete telemetry and EXIF angles for {len(assets)} assets...")
    for a in assets:
        t = get_exif_data(a["source_path"])
        cur.execute("""
            UPDATE assets SET sensor_model = %s, latitude = %s, longitude = %s, altitude_meters = %s
            WHERE asset_id = %s
        """, (t["sensor"], t["lat"], t["lon"], t["alt"], a["asset_id"]))
        
        # Attach telemetry data directly to the runtime asset dictionary for prompt injection
        a.update(t)
    conn.commit()

    screener_res = {}
    verifier_res = {}

    # Phase 1A: Screener (Moondream with Strict Altitude Rules & No Prompt Echoing)
    print(f"\n[PHASE 1A] Loading Moondream and screening {len(assets)} assets...")
    for a in assets:
        m_kind = a.get("media_kind", "still")
        alt = a.get('alt', 0) or 0
        
        telemetry_block = f"""
        [TELEMETRY DATA]
        - Sensor Model: {a.get('sensor')}
        - Altitude: {alt} meters
        - Focal Length: {a.get('focal_length')}
        - Camera Pitch/Angle Tag: {a.get('camera_pitch')}
        """
        
        prompt = f"""{telemetry_block}
        You are an aerial imagery analyst. 
        CRITICAL RULE: Because the telemetry reports an altitude of {alt} meters (> 0), this asset IS an aerial photograph. It is physically impossible for this to be 'eye-level'. Choose strictly between 'nadir' (top-down) or 'oblique' (slanted angle). Do not output template text; provide actual values.
        Return ONLY valid JSON matching this exact schema:
        {{
            "media_genre": "infrastructure",
            "camera_perspective": "oblique",
            "perspective_reasoning": "Explain why based on the {alt}m altitude and downward view",
            "primary_subjects": ["specific subject 1", "specific subject 2"],
            "contains_infrastructure_or_architecture": true
        }}"""
        screener_res[a["asset_id"]] = call_ollama("moondream", prompt, a["source_path"])

    # Phase 1B: Verifier (LLaVA - Granular Details, 3D Orientation & Aesthetics)
    print(f"\n[PHASE 1B] Loading LLaVA and verifying {len(assets)} assets...")
    for a in assets:
        m_kind = a.get("media_kind", "still")
        telemetry_info = f"[EXIF Telemetry -> Altitude: {a.get('altitude_meters')}m]"
        
        if m_kind == "3d_model":
            prompt = f"""{telemetry_info} You are a 3D asset inspection engine. Analyze this preview render of a 3D model. 
            Check if the model is oriented correctly on the vertical Y-axis (upright). If it is tilted, sideways, or upside-down, calculate the axis correction needed.
            Return ONLY valid JSON matching this exact schema:
            {{
                "is_correctly_oriented": true,
                "suggested_axis_rotations": {{"x": 0, "y": 0, "z": 0}},
                "orientation_notes": "Explain orientation status",
                "structural_and_design_elements": ["element1"]
            }}"""
        else:
            prompt = f"""{telemetry_info} You are an expert visual archivist analyzing an asset for a Cloudflare R2 web database. Factor altitude telemetry into scale assessment.
            Return ONLY valid JSON matching this exact schema:
            {{
                "primary_subjects": ["tag1", "tag2"],
                "structural_and_design_elements": ["bridge deck", "outward-facing staircase", "scale-patterned walls", "none"],
                "materials_and_textures": ["concrete", "asphalt", "glass", "wood", "metal"],
                "objects_spotted": ["excavator", "vehicles", "furniture", "trees", "none"],
                "dominant_colors": ["red", "gold", "earth tones", "monochrome"],
                "visual_mood": ["professional", "dramatic", "serene", "industrial"],
                "telemetry_verification_notes": "How do visual elements match reported altitude?",
                "comprehensive_summary": "A concise, 2-sentence professional description of the entire scene."
            }}"""
            
        verifier_res[a["asset_id"]] = call_ollama("llava", prompt, a["source_path"])

    # Phase 1C: Judge (Llama 3.1)
    print(f"\n[PHASE 1C] Loading Llama 3.1 to judge results...")
    for a in assets:
        aid = a["asset_id"]
        prompt = f"""You are the final review judge. Compare these outputs.
        Screener: {screener_res[aid]}
        Verifier: {verifier_res[aid]}
        Do they agree on the core subject and perspective? Return ONLY valid JSON matching this schema exactly:
        {{"reasoning": "Step-by-step explanation of agreements and differences", "agreementScore": 0.85}}"""
        
        judge_res = call_ollama("llama3.1", prompt)
        
        try:
            score = float(judge_res.get("agreementScore", 0.0))
        except (ValueError, TypeError):
            score = 0.0
            
        cur.execute("""
            INSERT INTO ai_evaluations (asset_id, screener_output, verifier_output, judge_output, agreement_score)
            VALUES (%s, %s, %s, %s, %s)
        """, (aid, json.dumps(screener_res[aid]), json.dumps(verifier_res[aid]), json.dumps(judge_res), score))
        
        cur.execute("UPDATE assets SET workflow_state = 'PENDING_HUMAN' WHERE asset_id = %s", (aid,))
    
    conn.commit()
    conn.close()
    print("\nBatch complete. Awaiting human review.")

if __name__ == "__main__":
    execute_phased_batch()