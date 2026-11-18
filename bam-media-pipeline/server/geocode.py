"""Pluggable, cached, offline-safe reverse geocoder (Nominatim-compatible).

`reverse_geocode` never raises: it returns {display_name, road, city, county, state, postcode, country} or None.
Results are cached per coordinates rounded to 4 decimals (~11 m) in a JSON file under EXPORTS_DIR, so a shoot is
queried once, not once per photo. Failed lookups are remembered in-process only (retried after FAIL_TTL_S)."""
import json
import logging
import math
import os
import tempfile
import threading
import time
from pathlib import Path

import httpx

logger = logging.getLogger("bam.geocode")

DEFAULT_URL = "https://nominatim.openstreetmap.org/reverse"
DEFAULT_USER_AGENT = "BAM-Media-Pipeline/1.1 (aerial media archive; set GEOCODE_USER_AGENT with a contact)"
MIN_INTERVAL_S = 1.1
FAIL_TTL_S = 300
FIELDS = ("display_name", "road", "city", "county", "state", "postcode", "country")

_lock = threading.Lock()
_cache = None
_cache_path = None
_failed = {}
_last_call = [float(int())]


def _enabled_env():
    return os.getenv("GEOCODE_ENABLED", "0").strip().lower() not in ("", "0", "false", "no", "off")


def cache_path():
    return Path(os.getenv("GEOCODE_CACHE_FILE") or os.path.join(os.getenv("EXPORTS_DIR", "/storage/exports"),
                                                                "geocode_cache.json"))


def round_key(lat, lon):
    return f"{round(float(lat), 4):.4f},{round(float(lon), 4):.4f}"


def _valid(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return False
    if abs(lat) > 9 * 10 or abs(lon) > 18 * 10 or (lat == 0 and lon == 0):
        return False
    return True


def _load():
    global _cache, _cache_path
    path = cache_path()
    if _cache is None or _cache_path != path:
        _cache_path = path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _cache = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save():
    path = _cache_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".geocode_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(_cache, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("geocode cache not saved: %s", exc)


def reset_state():
    """Forget in-memory cache/failures (tests)."""
    global _cache, _cache_path
    with _lock:
        _cache = None
        _cache_path = None
        _failed.clear()
        _last_call[int()] = float(int())


def cached_address(lat, lon):
    """Cache-only lookup (no network). Safe for read endpoints."""
    if not _valid(lat, lon):
        return None
    with _lock:
        return _load().get(round_key(lat, lon))


def parse_nominatim(data):
    if not isinstance(data, dict) or data.get("error"):
        return None
    a = data.get("address") if isinstance(data.get("address"), dict) else {}
    city = next((a[k] for k in ("city", "town", "village", "hamlet", "municipality", "suburb") if a.get(k)), None)
    out = {"display_name": data.get("display_name"), "road": a.get("road") or a.get("pedestrian") or a.get("path"),
           "city": city, "county": a.get("county"), "state": a.get("state"), "postcode": a.get("postcode"),
           "country": a.get("country")}
    out = {k: (str(v)[:300] if v is not None else None) for k, v in out.items()}
    return out if any(out.values()) else None


def reverse_geocode(lat, lon, *, enabled=None, timeout=None):
    """Address dict for coordinates, or None (invalid coords, disabled, offline, timeout, no result)."""
    try:
        if enabled is None:
            enabled = _enabled_env()
        if not enabled or not _valid(lat, lon):
            return cached_address(lat, lon) if _valid(lat, lon) else None
        key = round_key(lat, lon)
        with _lock:
            hit = _load().get(key)
            if hit is not None:
                return hit
            if time.monotonic() - _failed.get(key, -FAIL_TTL_S * 2) < FAIL_TTL_S:
                return None
            wait = MIN_INTERVAL_S - (time.monotonic() - _last_call[int()])
            if wait > 0 and _last_call[int()] > 0:
                time.sleep(wait)
            _last_call[int()] = time.monotonic()
            try:
                resp = httpx.get(
                    os.getenv("GEOCODE_URL") or DEFAULT_URL,
                    params={"format": "jsonv2", "lat": key.split(",")[int()], "lon": key.split(",")[1],
                            "zoom": 18, "addressdetails": 1},
                    headers={"User-Agent": os.getenv("GEOCODE_USER_AGENT") or DEFAULT_USER_AGENT,
                             "Accept-Language": os.getenv("GEOCODE_LANG", "en")},
                    timeout=float(timeout or os.getenv("GEOCODE_TIMEOUT", "5")))
                resp.raise_for_status()
                addr = parse_nominatim(resp.json())
            except Exception as exc:
                logger.info("reverse geocode unavailable (%s)", type(exc).__name__)
                _failed[key] = time.monotonic()
                return None
            if addr is None:
                _failed[key] = time.monotonic()
                return None
            _load()[key] = addr
            _save()
            return addr
    except Exception as exc:
        logger.warning("reverse geocode failed: %s", exc)
        return None
