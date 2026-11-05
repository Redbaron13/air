"""Export reviewed stills in the website's actual manifest format; never uploads media.

Reads the existing PostgreSQL schema, or a local snapshot for offline validation.
No source paths, EXIF GPS, client details or assumed locations enter the public file.
"""
import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

SERVICES = {'real-estate', 'construction', 'inspections', 'damage', 'roof-solar', 'mapping', 'events'}
MAX_VARIANT_BYTES = 50 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def positive(value, name):
    require(type(value) is int and value > 0, f'{name} must be a positive integer')
    return value


def digest(value, name):
    require(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value), f'{name} needs a lowercase SHA-256')
    return value


def public_origin(value):
    url = urlsplit(value)
    require(url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password
            and url.path in ('', '/') and not url.query and not url.fragment,
            'Media origin must be an HTTPS origin, without credentials, path or query')
    return value.rstrip('/')


def compile_manifest(rows, copies, origin):
    origin = public_origin(origin)
    assets, paths, seen, skipped = {}, {}, set(), []
    by_parent = {}
    for copy in copies:
        by_parent.setdefault(copy['parent_asset_id'], []).append(copy)
    for row in sorted(rows, key=lambda item: item['asset_id']):
        aid = row['asset_id']
        require(isinstance(aid, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', aid), 'Invalid asset ID')
        require(aid not in assets, f'{aid}: duplicate ID')
        require(row.get('workflow_state') == 'APPROVED', f'{aid}: human approval is required')
        variants = by_parent.get(aid, [])
        # Other media remain in the pipeline until a video/model-specific contract exists.
        if str(row.get('media_kind', '')).lower() not in ('image', 'still', 'photo'):
            skipped.append({'id': aid, 'reason': 'Only reviewed still images are supported in this export'})
            continue
        sha = digest(row.get('original_sha256'), aid)
        require(sha not in seen, f'{aid}: duplicate original content hash')
        seen.add(sha)
        alt = row.get('alt')
        require(isinstance(alt, str) and 10 <= len(alt.strip()) <= 1000, f'{aid}: supply a reviewed, descriptive alt text')
        tags = row.get('service_tags')
        if isinstance(tags, str):
            tags = json.loads(tags)
        require(isinstance(tags, list) and tags and all(isinstance(t, str) and t in SERVICES for t in tags),
                f'{aid}: use reviewed website service tags: {", ".join(sorted(SERVICES))}')
        webp, widths, keys = [], set(), set()
        for copy in variants:
            if str(copy.get('format', '')).lower() != 'webp':
                continue
            width = positive(copy.get('width'), f'{aid}: width')
            height = positive(copy.get('height'), f'{aid}: height')
            size = positive(copy.get('byte_size'), f'{aid}: bytes')
            require(size <= MAX_VARIANT_BYTES, f'{aid}: derivative exceeds 50 MB')
            key = copy.get('r2_storage_key')
            require(isinstance(key, str) and not key.startswith('/') and '\\' not in key
                    and not any(part in ('', '.', '..') for part in key.split('/'))
                    and not any(ord(c) < 32 for c in key) and key.endswith('.webp'), f'{aid}: invalid immutable WebP object key')
            require(key not in keys, f'{aid}: duplicate derivative key')
            keys.add(key)
            # withoutEnlargement can legitimately make multiple tiers the same width.
            if width in widths:
                continue
            widths.add(width)
            src = origin + '/' + quote(key, safe='/')
            webp.append({'src': src, 'width': width, 'height': height, 'bytes': size,
                         'sha256': digest(copy.get('sha256'), f'{aid}: derivative')})
        require(webp, f'{aid}: build and register at least one WebP derivative first')
        webp.sort(key=lambda v: v['width'])
        largest = webp[-1]
        ratio = largest['width'] / largest['height']
        require(all(abs(v['width']/v['height'] - ratio) < .02 for v in webp), f'{aid}: derivatives have inconsistent aspect ratios')
        assets[aid] = {'id': aid, 'src': largest['src'], 'alt': alt.strip(),
                       'width': largest['width'], 'height': largest['height'], 'sha256': sha,
                       'serviceTags': sorted(set(tags)), 'variants': webp,
                       'reviewMethod': 'Human-approved pipeline metadata'}
        for variant in webp:
            require(variant['src'] not in paths, 'An object key is assigned to more than one asset')
            paths[variant['src']] = aid
    require(assets, 'No approved still images are ready; existing exports are preserved')
    return {'version': 1, 'assets': assets, 'paths': paths,
            'publication': {'status': 'staged', 'origin': origin}, 'skipped': skipped}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def verify_remote(manifest):
    """Read each configured public object, checking actual bytes and browser texture CORS."""
    opener = build_opener(NoRedirect())
    for asset in manifest['assets'].values():
        for variant in asset['variants']:
            request = Request(variant['src'], headers={'Origin': 'https://baronaerial.com'})
            with opener.open(request, timeout=30) as response:
                require(response.status == 200, 'Public derivative did not return HTTP 200')
                require(response.headers.get_content_type() == 'image/webp', 'Public derivative is not served as image/webp')
                require(response.headers.get('Access-Control-Allow-Origin') in ('*', 'https://baronaerial.com'),
                        'Configure R2 CORS for https://baronaerial.com before using WebGL textures')
                content = response.read(variant['bytes'] + 1)
            require(len(content) == variant['bytes'], 'Public derivative byte count differs from database')
            require(hashlib.sha256(content).hexdigest() == variant['sha256'], 'Public derivative hash differs from database')
    manifest['publication']['status'] = 'verified'
    manifest['publication']['verifiedAt'] = datetime.now(timezone.utc).isoformat()


def read_database():
    # Import only in database mode so offline validation has no dependencies.
    import psycopg2
    from psycopg2.extras import RealDictCursor
    dsn = os.environ.get('DATABASE_URL')
    require(dsn, 'Set DATABASE_URL or use --snapshot for offline validation')
    with psycopg2.connect(dsn, connect_timeout=10) as connection:
        connection.set_session(isolation_level='REPEATABLE READ', readonly=True)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute("SELECT asset_id, original_sha256, media_kind, alt, service_tags, workflow_state FROM assets WHERE workflow_state='APPROVED'")
            assets = cursor.fetchall()
            cursor.execute("SELECT c.parent_asset_id,c.format,c.width,c.height,c.byte_size,c.sha256,c.r2_storage_key FROM asset_copies c JOIN assets a ON a.asset_id=c.parent_asset_id WHERE a.workflow_state='APPROVED' ORDER BY c.width,c.copy_id")
            copies = cursor.fetchall()
    return assets, copies


def atomic_write(path, manifest):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as output:
            temp_name = output.name
            json.dump(manifest, output, indent=2, ensure_ascii=False)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True, help='Actual HTTPS media origin; no placeholder defaults')
    parser.add_argument('--output', required=True, help='Local manifest output path')
    parser.add_argument('--snapshot', type=Path, help='Offline JSON object with assets and copies arrays')
    parser.add_argument('--verify-remote', action='store_true', help='Read uploaded derivatives and verify hashes, MIME and CORS; does not upload')
    args = parser.parse_args()
    if args.snapshot:
        data = json.loads(args.snapshot.read_text())
        rows, copies = data['assets'], data['copies']
    else:
        rows, copies = read_database()
    manifest = compile_manifest(rows, copies, args.origin)
    if args.verify_remote:
        verify_remote(manifest)
    atomic_write(args.output, manifest)
    print(f"Website manifest: {len(manifest['assets'])} approved stills; {len(manifest['skipped'])} other media held; {manifest['publication']['status']}. Output: {args.output}")


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Database connection errors may contain credentials; don't echo raw exceptions.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__ + ': export failed; check local configuration and service availability'
        raise SystemExit('Website export stopped: ' + message)
