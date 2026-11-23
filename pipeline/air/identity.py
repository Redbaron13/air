"""Identifier builders and parsers.

The ids do different jobs and are not interchangeable.

- Temp id: assigned at prep. It names one file inside the local library.
- Keyframe id: a still pulled from a movie. It keeps the first two characters
  of the video unique token and stores the parent video temp id.
- Location id: one place across dates. Every photo of that place stays on it.
  Before-and-after pairs are chosen from this pool.
- Overlap link id: one visit at that place. Significant overlap marks a
  photogrammetry candidate. A weak visit stays insufficient.
- Compiled id: assigned after the location holds. It is how a shot is found
  for the website, together with its tags and capture facts.
- Copy id: one public derivative of a compiled asset.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

MONTH_BY_NUMBER = {
    1: "A",
    2: "F",
    3: "R",
    4: "P",
    5: "M",
    6: "J",
    7: "Y",
    8: "G",
    9: "S",
    10: "O",
    11: "N",
    12: "D",
}
MONTH_BY_LETTER = {letter: number for number, letter in MONTH_BY_NUMBER.items()}
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
KIND_LETTER = {"still": "P", "video": "M", "model3d": "D", "other": "X"}
LETTER_KIND = {letter: kind for kind, letter in KIND_LETTER.items()}
TYPE_CODE = {"still": "PHO", "video": "VID", "model3d": "MOD", "other": "OTH"}
CODE_KIND = {code: kind for kind, code in TYPE_CODE.items()}
SIZE_TIERS = {2560: "2D4", 1600: "1T6", 800: "6M7", 320: "3M2"}

_TEMP_RE = re.compile(
    r"^(?P<kind>[PMDX])(?P<unique>[0-9A-Z]{4})_"
    r"(?P<dd>\d{2})(?P<mon>[AFRPMJYGSOND])(?P<yy>\d{2})(?P<hms>\d{6})-"
    r"(?P<sub>[0-9A-Z]{2})(?P<seq>\d{3})$"
)
_KEYFRAME_RE = re.compile(r"^K(?P<unique>[0-9A-Z]{4})(?P<seq>\d{2})$")
_COMPILED_RE = re.compile(
    r"^(?P<date>\d{2}[AFRPMJYGSOND]\d{2})-"
    r"(?P<seq>\d{3})(?P<set>[0-9A-Z]{3})(?P<type>PHO|VID|MOD|OTH)(?P<batch>[0-9A-Z]{3})-"
    r"(?P<unique>[0-9A-Z]{4})$"
)
_COPY_RE = re.compile(r"^(?P<tier>[0-9A-Z]{3})-(?P<unique>[0-9A-Z]{4})-(?P<version>[A-Z]\d{2})$")
_LOCATION_RE = re.compile(r"^L[0-9A-Z]{6}$")
_OVERLAP_RE = re.compile(r"^O[0-9A-Z]{7}$")


def date_code(when: datetime) -> str:
    """Return DDMYY using the shared month alphabet. 21 January 2026 is 21A26."""
    return f"{when.day:02d}{MONTH_BY_NUMBER[when.month]}{when.year % 100:02d}"


def capture_stamp(when: datetime) -> str:
    return f"{date_code(when)}{when.hour:02d}{when.minute:02d}{when.second:02d}"


def _token(seed: str, length: int) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    chars = [ALPHABET[byte % 36] for byte in digest]
    while len(chars) < length:
        digest = hashlib.sha256(digest).digest()
        chars.extend(ALPHABET[byte % 36] for byte in digest)
    return "".join(chars[:length])


def unique4(seed: str, taken: set[str] | None = None) -> str:
    """Four alphanumeric characters, unique among ``taken``."""
    used = taken or set()
    salt = 0
    while True:
        token = _token(f"{seed}:{salt}", 4)
        if token not in used:
            return token
        salt += 1


def build_temp_id(kind: str, unique: str, when: datetime, subfolder: str, sequence: int) -> str:
    letter = KIND_LETTER[kind]
    if not re.fullmatch(r"[0-9A-Z]{4}", unique):
        raise ValueError("unique token must be 4 characters from 0-9 and A-Z")
    if not re.fullmatch(r"[0-9A-Z]{2}", subfolder):
        raise ValueError("subfolder code must be 2 characters from 0-9 and A-Z")
    if sequence < 0 or sequence > 999:
        raise ValueError("sequence must be 000-999")
    return f"{letter}{unique}_{capture_stamp(when)}-{subfolder}{sequence:03d}"


def parse_temp_id(value: str) -> dict:
    match = _TEMP_RE.fullmatch(value or "")
    if not match:
        raise ValueError(f"not a temp id: {value}")
    parts = match.groupdict()
    month = MONTH_BY_LETTER[parts["mon"]]
    year = 2000 + int(parts["yy"])
    hour = int(parts["hms"][0:2])
    minute = int(parts["hms"][2:4])
    second = int(parts["hms"][4:6])
    return {
        "kind": LETTER_KIND[parts["kind"]],
        "unique": parts["unique"],
        "captured_at": datetime(year, month, int(parts["dd"]), hour, minute, second),
        "subfolder": parts["sub"],
        "sequence": int(parts["seq"]),
    }


def shared_stem(unique: str) -> str:
    if not re.fullmatch(r"[0-9A-Z]{4}", unique):
        raise ValueError("unique token must be 4 characters")
    return unique[:2]


def build_keyframe_id(video_unique: str, index: int) -> str:
    """Keyframe id keeps the video's four-character token so two videos that share a stem do not collide.

    The first two characters of that token are still the shared stem. Example: video unique 7K2M, frame 5 is K7K2M05.
    """
    if not re.fullmatch(r"[0-9A-Z]{4}", video_unique):
        raise ValueError("unique token must be 4 characters")
    if index < 1 or index > 99:
        raise ValueError("keyframe index must be 01-99")
    return f"K{video_unique}{index:02d}"


def parse_keyframe_id(value: str) -> dict:
    match = _KEYFRAME_RE.fullmatch(value or "")
    if not match:
        raise ValueError(f"not a keyframe id: {value}")
    unique = match.group("unique")
    return {"unique": unique, "stem": unique[:2], "index": int(match.group("seq"))}


def location_id_for(latitude: float, longitude: float) -> str:
    """Stable id for one rounded place. The same coordinates reuse the same id."""
    key = f"{round(float(latitude), 4):.4f},{round(float(longitude), 4):.4f}"
    return "L" + _token(key, 6)


def location_code(location_id: str) -> str:
    if not _LOCATION_RE.fullmatch(location_id or ""):
        raise ValueError("location id must look like L plus 6 characters")
    return location_id[1:4]


def overlap_id_for(location_id: str, visit_key: str) -> str:
    return "O" + _token(f"{location_id}|{visit_key}", 7)


def build_compiled_id(
    when: datetime,
    sequence: int,
    location: str,
    kind: str,
    batch: str,
    unique: str,
) -> str:
    """Website file id. Example: 21A26-003H7KPHOA01-K4M2."""
    if sequence < 0 or sequence > 999:
        raise ValueError("compiled sequence must be 000-999")
    set_code = location_code(location) if location.startswith("L") else location
    if not re.fullmatch(r"[0-9A-Z]{3}", set_code):
        raise ValueError("location code must be 3 characters")
    if not re.fullmatch(r"[0-9A-Z]{3}", batch):
        raise ValueError("batch must be 3 characters")
    if not re.fullmatch(r"[0-9A-Z]{4}", unique):
        raise ValueError("unique token must be 4 characters")
    body = f"{sequence:03d}{set_code}{TYPE_CODE[kind]}{batch}"
    return f"{date_code(when)}-{body}-{unique}"


def parse_compiled_id(value: str) -> dict:
    match = _COMPILED_RE.fullmatch(value or "")
    if not match:
        raise ValueError(f"not a compiled id: {value}")
    parts = match.groupdict()
    month = MONTH_BY_LETTER[parts["date"][2]]
    return {
        "date_code": parts["date"],
        "month": month,
        "sequence": int(parts["seq"]),
        "location_code": parts["set"],
        "kind": CODE_KIND[parts["type"]],
        "batch": parts["batch"],
        "unique": parts["unique"],
    }


def build_copy_id(width: int, compiled_unique: str, version: str = "I10") -> str:
    if width not in SIZE_TIERS:
        raise ValueError(f"unsupported copy width: {width}")
    if not re.fullmatch(r"[0-9A-Z]{4}", compiled_unique):
        raise ValueError("compiled unique must be 4 characters")
    if not re.fullmatch(r"[A-Z]\d{2}", version):
        raise ValueError("version code must look like I10")
    return f"{SIZE_TIERS[width]}-{compiled_unique}-{version}"


def parse_copy_id(value: str) -> dict:
    match = _COPY_RE.fullmatch(value or "")
    if not match:
        raise ValueError(f"not a copy id: {value}")
    tier = match.group("tier")
    width = next(width for width, code in SIZE_TIERS.items() if code == tier)
    return {"width": width, "unique": match.group("unique"), "version": match.group("version")}
