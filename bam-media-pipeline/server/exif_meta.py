"""Pure EXIF/XMP helpers: drone (DJI) metadata extraction and JSON-safe raw EXIF dumps.

No DB or network. `read_exif_dump` shells out to exiftool when present and falls back to scanning the
embedded XMP packet, so DJI gimbal/flight angles are still captured without exiftool."""
import json
import logging
import math
import re
import subprocess
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("bam.exif")

XMP_SCAN_BYTES = 1024 * 1024
RAW_STR_MAX = 2048
RAW_KEYS_MAX = 1024
_XMP_ATTR = re.compile(rb'(drone-dji|Camera|tiff|exif|xmp|aux|crs):([A-Za-z][A-Za-z0-9]*)\s*=\s*"([^"]*)"')
_XMP_ELEM = re.compile(rb'<(drone-dji):([A-Za-z][A-Za-z0-9]*)>([^<]*)</\1:\2>')
_DJI_ALIASES = {"GimbalPitchDegree": "GimbalPitchDegree", "GimbalYawDegree": "GimbalYawDegree",
                "GimbalRollDegree": "GimbalRollDegree", "FlightYawDegree": "FlightYawDegree",
                "FlightRollDegree": "FlightRollDegree", "FlightPitchDegree": "FlightPitchDegree",
                "RelativeAltitude": "RelativeAltitude", "AbsoluteAltitude": "AbsoluteAltitude",
                "CameraSerialNumber": "CameraSerialNumber", "FlightXSpeed": "FlightXSpeed",
                "FlightYSpeed": "FlightYSpeed", "FlightZSpeed": "FlightZSpeed"}


def num(v):
    """Lenient float: accepts 12, '+12.5', '12.5 m', rational strings '1/500'. None when unparseable."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    s = str(v).strip()
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", s)
    if m:
        d = float(m.group(2))
        return float(m.group(1)) / d if d else None
    m = re.search(r"[+-]?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def json_safe(obj, _depth=0):
    """Recursively convert to JSON-safe primitives (no bytes, NaN, or oversized strings)."""
    if _depth > 6:
        return None
    if obj is None or isinstance(obj, (bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (bytes, bytearray)):
        return None
    if isinstance(obj, str):
        if obj.startswith("(Binary data"):
            return None
        return obj[:RAW_STR_MAX]
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, dict):
        out = {}
        for k, v in list(obj.items())[:RAW_KEYS_MAX]:
            sv = json_safe(v, _depth + 1)
            if sv is not None:
                out[str(k)[:128]] = sv
        return out
    if isinstance(obj, (list, tuple, set)):
        return [x for x in (json_safe(v, _depth + 1) for v in list(obj)[:256]) if x is not None]
    return str(obj)[:RAW_STR_MAX]


def parse_xmp_attrs(data: bytes) -> dict:
    """Pull DJI/camera XMP attributes (attribute or element form) out of raw file bytes."""
    out = {}
    if not data:
        return out
    start = data.find(b"<x:xmpmeta")
    if start < 0 or start > len(data):
        start = data.find(b"<?xpacket")
    if start < 0:
        return out
    end = data.find(b"</x:xmpmeta>", start)
    packet = data[start:end if end > 0 else None]
    for rx in (_XMP_ATTR, _XMP_ELEM):
        for m in rx.finditer(packet):
            ns, key, val = (g.decode("utf-8", "replace") for g in m.groups())
            if ns == "drone-dji" and key in _DJI_ALIASES:
                out.setdefault(key, val)
            elif ns != "drone-dji" and key in ("Make", "Model", "LensModel"):
                out.setdefault(key, val)
            elif ns == "drone-dji":
                out.setdefault(key, val)
    return out


def read_exif_dump(filepath: str, timeout=30) -> dict:
    """Raw EXIF/XMP/GPS dump (JSON-safe). Never raises; {} when nothing readable."""
    dump = {}
    try:
        res = subprocess.run(["exiftool", "-j", "-n", "-a", "-u", "-struct", filepath],
                             capture_output=True, text=True, timeout=timeout)
        data = json.loads(res.stdout)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            dump = data[0]
    except Exception:
        dump = {}
    try:
        with open(filepath, "rb") as fh:
            xmp = parse_xmp_attrs(fh.read(XMP_SCAN_BYTES))
        for k, v in xmp.items():
            dump.setdefault(k, v)
    except Exception:
        pass
    dump.pop("SourceFile", None)
    return json_safe(dump) or {}


def _first(d, *keys):
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return v
    return None


def format_offset(minutes):
    sign = "+" if minutes >= 0 else "-"
    m = abs(int(minutes))
    return f"{sign}{m // 60:02d}:{m % 60:02d}"


def parse_offset(v):
    """'+02:00' / '-0730' / 'Z' / 'UTC+2' -> minutes east of UTC, or None."""
    if v is None:
        return None
    s = str(v).strip()
    if s.upper() in ("Z", "UTC", "GMT"):
        return 0
    m = re.fullmatch(r"(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?", s, re.I)
    if not m:
        return None
    mins = int(m.group(2)) * 60 + int(m.group(3) or 0)
    return -mins if m.group(1) == "-" else mins


def parse_capture_time(value, offset=None) -> dict:
    """EXIF capture time -> {utc, local, offset, iso}. Naive values without an offset are treated as UTC
    (matching media_intel._dt) and flagged tz_known=False."""
    res = {"utc": None, "local": None, "offset": None, "iso": None, "tz_known": False}
    if value is None or value == "":
        return res
    if isinstance(value, datetime):
        d = value
    else:
        s = str(value).strip()
        m = re.fullmatch(r"(\d{4})[:-](\d{2})[:-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?", s)
        if not m:
            return res
        try:
            d = datetime(*(int(m.group(i)) for i in range(1, 7)))
        except ValueError:
            return res
        if m.group(7) and offset in (None, ""):
            offset = m.group(7)
    mins = parse_offset(offset) if offset not in (None, "") else None
    if d.tzinfo is not None and mins is None:
        mins = int(d.utcoffset().total_seconds() // 60)
        d = d.replace(tzinfo=None)
    elif d.tzinfo is not None:
        d = d.replace(tzinfo=None)
    if mins is None:
        res.update(utc=d.strftime("%Y-%m-%d %H:%M:%S"), local=d.strftime("%Y-%m-%d %H:%M:%S"),
                   iso=d.isoformat() + "Z")
        return res
    utc = d - timedelta(minutes=mins)
    off = format_offset(mins)
    res.update(utc=utc.strftime("%Y-%m-%d %H:%M:%S"), local=d.strftime("%Y-%m-%d %H:%M:%S"), offset=off,
               iso=d.isoformat() + off, tz_known=True)
    return res


def _fmt_shutter(v):
    x = num(v)
    if x is None or x <= 0:
        return None
    return f"1/{int(round(1 / x))}" if x < 1 else f"{x:g}"


def parse_exif_dump(dump: dict) -> dict:
    """Normalize a raw dump (exiftool -n style keys) into the pipeline's drone/camera metadata."""
    d = dump if isinstance(dump, dict) else {}
    out = {}
    when = _first(d, "DateTimeOriginal", "CreateDate", "DateTimeDigitized", "ModifyDate")
    offset = _first(d, "OffsetTimeOriginal", "OffsetTimeDigitized", "OffsetTime", "TimeZone")
    ct = parse_capture_time(when, offset)
    out["captured_at"] = ct["utc"]
    out["captured_at_local"] = ct["local"]
    out["capture_utc_offset"] = ct["offset"]
    lat = num(d.get("GPSLatitude"))
    lon = num(d.get("GPSLongitude"))
    if lat is not None and str(d.get("GPSLatitudeRef", "N")).upper().startswith("S") and lat > 0:
        lat = -lat
    if lon is not None and str(d.get("GPSLongitudeRef", "E")).upper().startswith("W") and lon > 0:
        lon = -lon
    if lat is None or lon is None:
        lat = lon = None
    elif not (-90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None
    out["lat"], out["lon"] = lat, lon
    alt = num(d.get("GPSAltitude"))
    if alt is not None and str(d.get("GPSAltitudeRef", "0")) in ("1", "Below Sea Level") and alt > 0:
        alt = -alt
    out["alt"] = alt
    out["make"] = _first(d, "Make")
    out["sensor"] = _first(d, "Model", "CameraModelName")
    out["lens_model"] = _first(d, "LensModel", "Lens", "LensID")
    fl = num(_first(d, "FocalLength"))
    out["focal_length"] = f"{fl:g} mm" if fl is not None else None
    out["focal_length_35mm"] = num(_first(d, "FocalLengthIn35mmFormat", "FocalLength35efl"))
    iso = num(_first(d, "ISO"))
    out["iso"] = int(iso) if iso is not None else None
    out["shutter_speed"] = _fmt_shutter(_first(d, "ExposureTime", "ShutterSpeed", "ShutterSpeedValue"))
    out["aperture"] = num(_first(d, "FNumber", "Aperture", "ApertureValue"))
    out["gimbal_pitch"] = num(_first(d, "GimbalPitchDegree", "CameraPitch"))
    out["gimbal_yaw"] = num(_first(d, "GimbalYawDegree", "CameraYaw"))
    out["gimbal_roll"] = num(_first(d, "GimbalRollDegree", "CameraRoll"))
    out["flight_yaw"] = num(_first(d, "FlightYawDegree"))
    out["flight_roll"] = num(_first(d, "FlightRollDegree"))
    out["flight_pitch"] = num(_first(d, "FlightPitchDegree"))
    out["rel_altitude"] = num(_first(d, "RelativeAltitude"))
    out["abs_altitude"] = num(_first(d, "AbsoluteAltitude"))
    serial = _first(d, "CameraSerialNumber", "SerialNumber", "BodySerialNumber")
    out["camera_serial"] = str(serial) if serial is not None else None
    out["is_drone"] = bool(
        any(out[k] is not None for k in ("gimbal_pitch", "gimbal_yaw", "flight_yaw", "rel_altitude"))
        or "dji" in str(out["make"] or "").lower())
    return out


def build_asset_meta(dump: dict) -> dict:
    """parse_exif_dump + raw_exif; drops None entries is NOT done so callers can COALESCE by key."""
    meta = parse_exif_dump(dump)
    meta["raw_exif"] = dump if isinstance(dump, dict) else {}
    return meta


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)
