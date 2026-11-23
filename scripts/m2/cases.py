# Synthetic inputs for the M2 scripts: triage messages with draft expected risk levels, vitals readings,
# and a generated test photo. No real patient data.

import base64
import json
import math
import struct
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from app.motors.vitals.logic import CHECKS
from app.shared.contracts import RISK_ORDER, highest_risk

HERE = Path(__file__).resolve().parent
TRIAGE_CASES_FILE = HERE / "triage_cases.json"

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def triage_cases(path: Path = TRIAGE_CASES_FILE) -> List[dict]:
    """[{"id", "country", "expected_risk", "message"}, ...]"""
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


# Vitals readings, one or two per risk level. expected_risk is what app/motors/vitals/logic.py decides.
VITALS_CASES: List[dict] = [
    {"id": "V01", "country": "US", "data": {"heart_rate": 72, "spo2": 98, "systolic": 118, "diastolic": 76, "respiratory_rate": 14}},
    {"id": "V02", "country": "MX", "data": {"heart_rate": 64, "spo2": 97}},
    {"id": "V03", "country": "US", "data": {"heart_rate": 108, "spo2": 96}},
    {"id": "V04", "country": "MX", "data": {"systolic": 134, "diastolic": 84}},
    {"id": "V05", "country": "US", "data": {"heart_rate": 128, "respiratory_rate": 26}},
    {"id": "V06", "country": "MX", "data": {"spo2": 90, "heart_rate": 98}},
    {"id": "V07", "country": "US", "data": {"spo2": 85, "heart_rate": 118}},
    {"id": "V08", "country": "MX", "data": {"systolic": 186, "diastolic": 122}},
]


def vitals_rule_risk(data: Dict[str, Optional[int]]) -> str:
    """The risk level the vitals rules decide (the "alert decision"), without any AI call.
    Same rules as app/motors/vitals/logic.py analyze()."""
    levels = [check(data[name]) for name, check in CHECKS.items() if data.get(name) is not None]
    return highest_risk(["low", *levels])


def risk_distance(answer: str, expected: str) -> int:
    """answer level minus expected level: 0 = exact, negative = under-triage (less urgent than expected)."""
    return RISK_ORDER.index(answer) - RISK_ORDER.index(expected)


# ---------- test photo ----------

def _png(width: int, height: int, pixel) -> bytes:
    """A PNG file built with the standard library only (no Pillow). pixel(x, y) -> (r, g, b)."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type: none
        for x in range(width):
            rows.extend(pixel(x, y))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")


@lru_cache(maxsize=2)
def synthetic_skin_photo(size: int = 900) -> str:
    """A made-up 900x900 photo (the size the proposal's cost-per-image example uses): skin-coloured background
    with a reddish oval in the middle. Base64 PNG, the format /ai/assess takes. Good for cost and latency;
    it is not a real lesion, so it says nothing about visual accuracy."""
    cx, cy = size / 2, size / 2
    rx, ry = size * 0.18, size * 0.12

    def pixel(x, y):
        shade = int(12 * math.sin(x / 37.0) * math.cos(y / 53.0))  # soft texture, so the image is not flat
        inside = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
        if inside <= 1:
            depth = 1 - inside
            return (min(255, 190 + shade), max(0, int(90 - 40 * depth) + shade), max(0, int(85 - 35 * depth) + shade))
        return (min(255, 224 + shade), min(255, 182 + shade), min(255, 150 + shade))

    return base64.b64encode(_png(size, size, pixel)).decode("ascii")


def photos(images_dir: Optional[Path] = None) -> List[dict]:
    """[{"id", "image"}]: every image in images_dir (de-identified test photos the client provides),
    or the synthetic photo when no folder is given."""
    if images_dir is None:
        return [{"id": "synthetic-900px", "image": synthetic_skin_photo()}]
    files = sorted(path for path in images_dir.iterdir() if path.suffix.lower() in IMAGE_EXTENSIONS)
    if not files:
        raise SystemExit(f"No images ({', '.join(IMAGE_EXTENSIONS)}) in {images_dir}")
    return [{"id": path.name, "image": base64.b64encode(path.read_bytes()).decode("ascii")} for path in files]
