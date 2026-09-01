"""Gold reference answer DB — past correct compliance rows (not drive ratings)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
GOLD_DIR = ROOT / "data" / "gold"
REFERENCE_DB = GOLD_DIR / "reference_answers.jsonl"
REFERENCES_DIR = ROOT / "data" / "references"

# Minimum similarity to reuse a past Yes/No + remark
MATCH_THRESHOLD = 0.42


def _tokenize(text: str) -> set[str]:
    raw = (text or "").lower()
    raw = re.sub(r"[^a-z0-9%\./\-\s]", " ", raw)
    toks = {t for t in raw.split() if len(t) > 2}
    stop = {
        "the",
        "and",
        "for",
        "shall",
        "with",
        "that",
        "this",
        "from",
        "are",
        "was",
        "were",
        "been",
        "have",
        "has",
        "will",
        "can",
        "may",
        "not",
        "all",
        "any",
        "per",
        "via",
    }
    return toks - stop


def normalize_requirement(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    return t[:500]


def score_similarity(a: str, b: str, *, cap_a: str = "", cap_b: str = "") -> float:
    """Token Jaccard with optional capability-key boost."""
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        na, nb = normalize_requirement(a), normalize_requirement(b)
        if na and na == nb:
            return 1.0
        if na and nb and (na in nb or nb in na) and min(len(na), len(nb)) > 40:
            return 0.75
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb) or 1
    j = inter / union
    if cap_a and cap_b and cap_a == cap_b:
        j = min(1.0, j + 0.18)
    na, nb = normalize_requirement(a), normalize_requirement(b)
    if na and nb and len(na) > 50 and (na in nb or nb in na):
        j = max(j, 0.8)
    return j


@lru_cache(maxsize=4)
def _load_reference_cached(path_str: str, mtime: float) -> tuple[dict[str, Any], ...]:
    path = Path(path_str)
    if not path.exists():
        return tuple()
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return tuple(rows)


def load_reference_answers(*, force_reload: bool = False) -> list[dict[str, Any]]:
    if force_reload:
        _load_reference_cached.cache_clear()
    if not REFERENCE_DB.exists():
        return []
    mtime = REFERENCE_DB.stat().st_mtime
    return list(_load_reference_cached(str(REFERENCE_DB), mtime))


def find_best_gold_match(
    requirement: str,
    *,
    capability_key: str | None = None,
    family: str | None = None,
) -> tuple[dict[str, Any] | None, float]:
    """Return (best_row, score) or (None, 0) if below threshold."""
    rows = load_reference_answers()
    if not rows or not (requirement or "").strip():
        return None, 0.0
    best: dict[str, Any] | None = None
    best_score = 0.0
    fam = (family or "").upper()
    for row in rows:
        if not row.get("status") and not row.get("remarks"):
            continue
        row_fam = str(row.get("family") or "").upper()
        score = score_similarity(
            requirement,
            str(row.get("requirement") or ""),
            cap_a=capability_key or "",
            cap_b=str(row.get("capability_key") or ""),
        )
        if fam and row_fam and fam == row_fam:
            score = min(1.0, score + 0.05)
        if score > best_score:
            best_score = score
            best = row
    if best is None or best_score < MATCH_THRESHOLD:
        return None, best_score
    return best, best_score


def append_reference_rows(
    rows: list[dict[str, Any]],
    *,
    source_file: str,
    family: str = "",
) -> int:
    """Append corrected / ingested answers to the gold DB. Never writes Schneider Source."""
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    with REFERENCE_DB.open("a", encoding="utf-8") as f:
        for row in rows:
            req = (row.get("requirement") or "").strip()
            if not req:
                continue
            status = (row.get("status") or "").strip().lower()
            if status in ("yes", "y", "comply", "complies"):
                status = "yes"
            elif status in ("no", "n"):
                status = "no"
            elif status in ("na", "n/a", "not applicable"):
                status = "na"
            record = {
                "requirement": req[:500],
                "status": status,
                "remarks": (row.get("remarks") or "")[:400],
                "capability_key": row.get("capability_key") or "",
                "family": family or row.get("family") or "",
                "source_file": source_file,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            n += 1
    _load_reference_cached.cache_clear()
    return n


def reference_db_stats() -> dict[str, Any]:
    rows = load_reference_answers()
    files = sorted({str(r.get("source_file") or "") for r in rows if r.get("source_file")})
    return {
        "path": str(REFERENCE_DB.relative_to(ROOT)) if REFERENCE_DB.exists() else "",
        "count": len(rows),
        "source_files": [f for f in files if f],
        "references_dir": str(REFERENCES_DIR.relative_to(ROOT)),
    }
