# Small helpers shared by the M2 scripts: percentiles, summaries and saving the report files.

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "reports"


def percentile(values: Iterable[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile (pct 0-100): the smallest value with at least pct% of the values at or below it.
    p95 of 20 values = the 19th smallest. None for no values."""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def summary_ms(values: List[float]) -> dict:
    """count, p50, p95, max and mean of a list of milliseconds (None when empty)."""
    return {
        "count": len(values),
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "max_ms": max(values) if values else None,
        "mean_ms": round(sum(values) / len(values), 1) if values else None,
    }


def fmt_ms(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:,.0f} ms"


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def save(name: str, data: dict, markdown: str, out_dir: Path = REPORTS_DIR) -> List[Path]:
    """Write reports/<name>-<time>.json and .md. Returns the two paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{name}-{timestamp()}"
    json_path, md_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return [json_path, md_path]
