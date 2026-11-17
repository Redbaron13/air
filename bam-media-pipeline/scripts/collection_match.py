"""Post-review local registry and evidence-backed relationship proposals, never merges."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3

from local_review import Ollama, VERSION, object_schema, TEXT, validate, save_json

RELATION = object_schema({
    'location': {'type': 'string', 'enum': ['same_place_candidate', 'different_place', 'unknown']},
    'visual_changes': TEXT,
    'subtle_differences': TEXT,
    'reason': TEXT,
})


def completed(data):
    if data.get('schema_version') != VERSION or data.get('state') != 'PENDING_HUMAN':
        raise ValueError('Only completed independent review results can enter matching')
    if data.get('completed_local_rounds') not in (1, 2) or not data.get('rounds'):
        raise ValueError('Missing independent model reviews')
    digest = data.get('source_sha256', '')
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Missing source identity')
    if not data.get('preview_sha256') or 'metadata' not in data or 'final' not in data:
        raise ValueError('Incomplete result')
    return digest


def field(data, name):
    item = data['metadata']['fields'].get(name, {})
    return item.get('value') if item.get('status') == 'recorded' else None


def gps(data):
    lat, lon = field(data, 'latitude'), field(data, 'longitude')
    if (isinstance(lat, (int, float)) and not isinstance(lat, bool) and
        isinstance(lon, (int, float)) and not isinstance(lon, bool) and
        math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return lat, lon
    return None


def capture_time(data):
    raw, offset = field(data, 'captured_at'), field(data, 'capture_offset')
    if not raw or not offset:
        return None  # A local timestamp without offset cannot safely establish a timeline.
    try:
        return datetime.strptime(raw + offset, '%Y:%m:%d %H:%M:%S%z')
    except (TypeError, ValueError):
        return None


def evidence(a, b):
    ga, gb = gps(a), gps(b)
    distance = None
    if ga and gb:
        p, q = math.radians(ga[0]), math.radians(gb[0])
        dlat, dlon = q - p, math.radians(gb[1] - ga[1])
        h = math.sin(dlat / 2)**2 + math.cos(p) * math.cos(q) * math.sin(dlon / 2)**2
        distance = round(6371000 * 2 * math.asin(math.sqrt(min(1, max(0, h)))), 2)
    ta, tb = capture_time(a), capture_time(b)
    delta = None if not ta or not tb else (tb - ta).total_seconds()
    return {'camera_distance_m': distance, 'b_minus_a_seconds': delta,
            'note': 'Camera GPS is not the viewed subject location; proximity is only candidate evidence.',
            'photogrammetry_membership': 'unverified', 'flight_plan_membership': 'unverified'}


@contextmanager
def connect(path):
    db = sqlite3.connect(path)
    try:
        if str(path) != ':memory:':
            Path(path).chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS reviews(source_sha TEXT PRIMARY KEY, result_sha TEXT NOT NULL, latitude REAL, longitude REAL, result_json TEXT NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS reviews_geo ON reviews(latitude,longitude)')
        yield db
    finally:
        db.close()


def register(db, data):
    digest = completed(data)
    raw = json.dumps(data, sort_keys=True, allow_nan=False)
    result_sha = hashlib.sha256(raw.encode()).hexdigest()
    existing = db.execute('SELECT result_sha FROM reviews WHERE source_sha=?', (digest,)).fetchone()
    if existing and existing[0] != result_sha:
        raise ValueError('Existing review differs; explicit version migration required')
    lat, lon = gps(data) or (None, None)
    with db:
        db.execute('INSERT OR IGNORE INTO reviews VALUES(?,?,?,?,?)', (digest, result_sha, lat, lon, raw))
    return digest


def candidates(db, target, limit=20):
    if not 1 <= limit <= 100:
        raise ValueError('Candidate limit must be 1–100')
    coord = gps(target)
    if not coord:
        return []  # No invented location or silent visual identity match.
    lat, lon = coord
    # Broad indexed latitude band; bounded result query, including across date lines.
    # Exact distance applied afterward. Dense catalogs need PostGIS/KNN, not huge prompts.
    rows = db.execute('SELECT result_json FROM reviews WHERE latitude BETWEEN ? AND ? AND source_sha != ? ORDER BY ABS(latitude-?) LIMIT 500',
                      (lat - .002, lat + .002, completed(target), lat))
    found = []
    for (raw,) in rows:
        data = json.loads(raw)
        distance = evidence(target, data)['camera_distance_m']
        if distance is not None and distance <= 150:
            found.append((distance, data))
    return [data for _, data in sorted(found, key=lambda row: row[0])[:limit]]


def propose(a, b, call, model):
    completed(a)
    completed(b)
    facts = evidence(a, b)
    # Full reviews are supplied only AFTER their independent stages have completed.
    prompt = ('Compare these complete independent image review records. Return only a '
              'location candidate and observations about differences. Do not establish identity, '
              '3D membership, a flight plan or verified change from similar descriptions. '
              'Missing evidence means unknown. Aerial camera location differs from subject location. '
              'Inputs: ' + json.dumps({'a': a, 'b': b, 'measured_evidence': facts}))
    # Fail closed rather than silently truncate full stage outputs or exceed default context.
    if len(prompt.encode()) + 4096 > 32768:
        raise ValueError('Full pair exceeds model context budget; use a validated larger-context adapter')
    observation = validate(call(model, prompt, RELATION), RELATION)
    return {'schema_version': 'bam-relationship-1', 'a': a['source_sha256'], 'b': b['source_sha256'],
            'evidence': facts, 'model_proposal': observation, 'state': 'PROPOSED',
            'human_confirmation_required': True, 'auto_merge_allowed': False}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--import-result', type=Path, action='append', default=[])
    parser.add_argument('--target')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--model', default='qwen3:4b')
    args = parser.parse_args()
    with connect(args.database) as db:
        for path in args.import_result:
            if path.stat().st_size > 1024 * 1024:
                raise ValueError('Oversized result')
            register(db, json.loads(path.read_text()))
        if args.target:
            if not args.output:
                parser.error('--output is required with --target')
            target = json.loads(db.execute('SELECT result_json FROM reviews WHERE source_sha=?', (args.target,)).fetchone()[0])
            client = Ollama(context=32768)
            info = client.post('/api/show', {'model': args.model})
            if info.get('remote_host') or info.get('remote_model') or args.model.endswith(':cloud'):
                raise ValueError('Collection matching requires a local model')
            proposals = [propose(target, other, client, args.model) for other in candidates(db, target)]
            save_json(args.output, {'proposals': proposals, 'coverage': 'GPS candidates only; max 20 from 500 nearby-latitude records'})


if __name__ == '__main__':
    main()
