"""Search attached Product Datasheets, then Catalog / Programming / Installation."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = ROOT / "data" / "knowledge"
DATASHEET_CACHE = ROOT / "uploads" / "datasheets"
PAGE_INDEX = KNOWLEDGE / "page_index.json"

MANUAL_LABELS = {
    "catalog": (
        "Catalog",
        "Catalog Altivar Process ATV600 variable speed drives",
    ),
    "programming": (
        "User guide",
        "ATV600 Programming Manual (EAV64318)",
    ),
    "installation": (
        "Instruction sheet",
        "ATV600 Installation Manual (EAV64301)",
    ),
}

MANUAL_FILES = {
    "catalog": [
        KNOWLEDGE / "Catalog Altivar Process ATV600 variable speed drives.pdf",
        ROOT / "Catalog Altivar Process ATV600 variable speed drives.pdf",
    ],
    "programming": [
        KNOWLEDGE / "ATV600-Programming-Manual-EN-EAV64318-14.pdf",
        ROOT / "ATV600-Programming-Manual-EN-EAV64318-14.pdf",
    ],
    "installation": [
        KNOWLEDGE / "ATV630_650_Installation_manual_EN_EAV64301_13.pdf",
        ROOT / "ATV630_650_Installation_manual_EN_EAV64301_13.pdf",
    ],
}

# Distinctive phrases per capability — not generic "drive/shall".
CAP_QUERIES: dict[str, list[str]] = {
    "afe_low_harmonic": ["thdi", "low harmonic", "active front end", "afe"],
    "ieee519": ["ieee 519", "harmonic"],
    "ip54_enclosure": ["ip54", "ip 54", "ip55", "ip 55"],
    "ip21_enclosure": ["ip21", "ip 21"],
    "emc_rfi": ["emc", "rfi", "61800-3"],
    "pwm_vhz": ["pwm", "v/f", "flux vector", "converter", "inverter"],
    "protections": ["overcurrent", "short circuit", "motor overload", "phase loss"],
    "comms": ["modbus", "ethernet"],
    "pid": ["pid"],
    "skip_frequencies": ["skip frequency", "skip frequencies"],
    "power_ride_through": ["ride-through", "ride through", "power loss"],
    "efficiency_98": ["efficiency", "98%"],
    "ambient_50c": ["50 °c", "50°c", "+50"],
    "altitude_1000m": ["1000 m", "1,000 m", "1000m"],
    "overload_vt": ["110%", "overload", "normal duty"],
    "output_freq_500": ["500 hz", "output frequency"],
    "hoa_local_remote": ["graphic display", "local/remote", "hmi"],
    "dry_contacts": ["relay output", "form-c", "dry contact"],
    "analog_io": ["analog input", "0-10", "4-20"],
    "bypass": ["bypass"],
    "line_reactors": ["dc choke", "line reactor"],
    "output_filter": ["sinus filter", "dv/dt", "motor choke"],
    "ul_etl": ["ul 508", "ul listed"],
    "ce_iec": ["ce marked", "european directives"],
    "iso14001": ["iso 14001", "recyclable"],
    "space_heater": ["space heater"],
    "input_voltage_380_400": ["380", "480 v", "400 v"],
    "freq_50hz": ["50 hz"],
    "power_factor": ["power factor"],
    "cable_length": ["cable length", "motor cable"],
    "auto_restart": ["automatic restart", "auto restart", "automatic fault reset"],
    "flux_optimization": ["flux optimization", "energy saving"],
    "noise_switching": ["switching frequency", "audible"],
    "factory_test": ["factory test", "routine test", "dynamometer"],
    "submittals": ["catalog", "product data"],
    "manufacturer_approved": ["schneider electric"],
    "local_support_egypt": ["egypt", "local support"],
    "current_limit": ["current limit", "current limitation", "cli"],
    "safety_shutdown": ["safe torque off", "sto", "safety function"],
}

STOP = {
    "the", "and", "for", "shall", "with", "that", "this", "from", "are", "was",
    "were", "been", "have", "has", "will", "can", "may", "not", "all", "any",
    "per", "via", "drive", "drives", "vfd", "vsd", "motor", "provided",
    "including", "include", "complete", "variable", "frequency", "speed",
}


def _resolve_manual(kind: str) -> Path | None:
    for path in MANUAL_FILES.get(kind, []):
        if path.exists():
            return path
    return None


def _printed_lookup() -> dict[str, dict[int, str]]:
    if not PAGE_INDEX.exists():
        return {}
    raw = json.loads(PAGE_INDEX.read_text(encoding="utf-8"))
    out: dict[str, dict[int, str]] = {}
    for kind, rows in raw.items():
        if not isinstance(rows, list):
            continue
        out[kind] = {}
        for row in rows:
            pdf = row.get("pdf_page")
            printed = row.get("printed_page")
            if pdf is None or printed is None:
                continue
            try:
                out[kind][int(pdf)] = str(printed)
            except (TypeError, ValueError):
                continue
    return out


@lru_cache(maxsize=8)
def _extract_pdf_pages(path_str: str, mtime: float) -> tuple[dict[str, Any], ...]:
    import fitz

    path = Path(path_str)
    pages: list[dict[str, Any]] = []
    doc = fitz.open(path)
    try:
        for i, page in enumerate(doc, start=1):
            text = page.get_text() or ""
            pages.append({"pdf_page": i, "text": text})
    finally:
        doc.close()
    return tuple(pages)


def _pages_for(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return list(_extract_pdf_pages(str(path), path.stat().st_mtime))


def protection_queries(requirement: str) -> list[str]:
    """Named protection types from the clause — not the whole datasheet protection block."""
    t = (requirement or "").lower()
    out: list[str] = []
    pairs = [
        (r"phase\s*loss|input phase", ["phase loss", "input phase loss"]),
        (r"under[\s\-]?volt", ["undervoltage", "under-voltage"]),
        (r"over[\s\-]?volt", ["overvoltage", "over-voltage"]),
        (r"over[\s\-]?current|overcurrent", ["overcurrent", "over-current"]),
        (r"overload", ["overload", "motor overload"]),
        (r"ground\s*fault|earth\s*fault", ["ground fault", "earth fault"]),
        (r"short[\s\-]?circuit", ["short-circuit", "short circuit"]),
        (r"over[\s\-]?temp", ["overtemperature", "overheating"]),
        (r"stall", ["stall"]),
    ]
    for pat, qs in pairs:
        if re.search(pat, t):
            out.extend(qs)
    return out


def _queries(requirement: str, cap_key: str | None) -> list[str]:
    q: list[str] = []
    prot = protection_queries(requirement)
    if cap_key == "protections" and prot:
        q.extend(prot)
    elif cap_key:
        q.extend(CAP_QUERIES.get(cap_key, []))
    if prot and cap_key != "protections":
        q.extend(prot)
    req = (requirement or "").lower()
    if re.search(r"converter|inverter", req):
        q.extend(["converter", "inverter", "rectifier"])
    if re.search(r"current\s*limit", req):
        q.extend(["current limit", "current limitation", "cli"])
    toks = re.findall(r"[a-z0-9%]{4,}", req)
    q.extend(t for t in toks if t not in STOP)
    seen: set[str] = set()
    out: list[str] = []
    for item in q:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out[:12]


def _score_page(text: str, queries: list[str], cap_queries: list[str] | None = None) -> tuple[int, bool]:
    low = (text or "").lower()
    hits = 0
    cap_hit = False
    for q in cap_queries or []:
        if q and q.lower() in low:
            hits += 3
            cap_hit = True
    for q in queries:
        if cap_queries and q in cap_queries:
            continue
        if q and q.lower() in low:
            hits += 1
    return hits, cap_hit


def _snippet(text: str, queries: list[str], max_len: int = 140) -> str:
    low = (text or "").lower()
    for q in queries:
        if not q:
            continue
        i = low.find(q.lower())
        if i >= 0:
            start = max(0, i - 40)
            chunk = re.sub(r"\s+", " ", text[start : start + max_len]).strip()
            return chunk
    return ""


def snippet_is_related(
    snippet: str,
    requirement: str,
    cap_key: str | None,
) -> bool:
    """True only if the PDF cut actually talks about this clause's topic."""
    snip = (snippet or "").lower()
    if len(snip) < 8:
        return False
    req = (requirement or "").lower()
    terms: list[str] = []
    if cap_key == "protections":
        terms.extend(protection_queries(requirement) or CAP_QUERIES.get("protections", []))
    else:
        terms.extend(CAP_QUERIES.get(cap_key or "", []))
    terms.extend(protection_queries(requirement))
    if re.search(r"converter|inverter", req):
        terms.extend(["converter", "inverter", "rectifier", "dc bus"])
    if re.search(r"dynamometer|factory test", req):
        terms.extend(["dynamometer", "factory test", "routine test"])
    if re.search(r"current\s*limit", req):
        terms.extend(["current limit", "cli"])
    toks = re.findall(r"[a-z0-9%]{5,}", req)
    terms.extend(t for t in toks if t not in STOP)
    return any(t and t.lower() in snip for t in terms)


def cache_path_for_part(part_number: str) -> Path:
    DATASHEET_CACHE.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Z0-9._-]", "_", (part_number or "PART").upper())
    return DATASHEET_CACHE / f"{safe}.pdf"


def save_datasheet_bytes(part_number: str, data: bytes) -> Path:
    path = cache_path_for_part(part_number)
    path.write_bytes(data)
    _extract_pdf_pages.cache_clear()
    return path


def _cap_queries_for(requirement: str, cap_key: str | None) -> list[str]:
    if cap_key == "protections":
        named = protection_queries(requirement)
        if named:
            return named
    return list(CAP_QUERIES.get(cap_key or "", []))


def _pick_related(
    pages: list[dict[str, Any]],
    *,
    requirement: str,
    cap_key: str | None,
    queries: list[str],
    cap_queries: list[str],
    skip_front: int = 0,
) -> tuple[dict[str, Any] | None, int]:
    best = None
    best_score = 0
    for page in pages:
        if skip_front and int(page["pdf_page"]) <= skip_front:
            continue
        score, cap_hit = _score_page(page["text"], queries, cap_queries)
        if cap_queries and not cap_hit:
            continue
        if score <= best_score:
            continue
        snip = _snippet(page["text"], queries)
        if not snippet_is_related(snip, requirement, cap_key):
            continue
        best_score = score
        best = page
    return best, best_score


def search_datasheets(
    requirement: str,
    *,
    cap_key: str | None = None,
    attached_parts: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Related hit in attached Product Datasheets only."""
    queries = _queries(requirement, cap_key)
    if not queries:
        return None
    cap_queries = _cap_queries_for(requirement, cap_key)
    for item in attached_parts or []:
        pn = str(item.get("part_number") or "").upper()
        if not pn:
            continue
        path = cache_path_for_part(pn)
        if not path.exists():
            continue
        best, best_score = _pick_related(
            _pages_for(path),
            requirement=requirement,
            cap_key=cap_key,
            queries=queries,
            cap_queries=cap_queries,
            skip_front=0,
        )
        if best and best_score >= 1:
            snip = _snippet(best["text"], queries)
            return {
                "kind": "datasheet",
                "part": pn,
                "page": str(best["pdf_page"]),
                "score": best_score,
                "snippet": snip,
                "related": True,
                "citation": f"Product Datasheet | {pn} | p.{best['pdf_page']}",
            }
    return None


def search_manuals(
    requirement: str,
    *,
    cap_key: str | None = None,
) -> dict[str, Any] | None:
    """Related hit in Catalog / Programming / Installation. Current limit prefers Programming."""
    queries = _queries(requirement, cap_key)
    if not queries:
        return None
    cap_queries = _cap_queries_for(requirement, cap_key)
    printed = _printed_lookup()
    kinds: tuple[str, ...] = ("catalog", "programming", "installation")
    if cap_key == "current_limit" or re.search(r"current\s*limit", (requirement or "").lower()):
        kinds = ("programming", "catalog", "installation")
    best_hit: dict[str, Any] | None = None
    best_score = -1
    for kind in kinds:
        path = _resolve_manual(kind)
        if not path:
            continue
        page, score = _pick_related(
            _pages_for(path),
            requirement=requirement,
            cap_key=cap_key,
            queries=queries,
            cap_queries=cap_queries,
            skip_front=6,
        )
        if not page or score < 3:
            continue
        if score > best_score:
            best_score = score
            pdf_page = int(page["pdf_page"])
            printed_page = printed.get(kind, {}).get(pdf_page) or str(pdf_page)
            doc_type, document = MANUAL_LABELS[kind]
            snip = _snippet(page["text"], queries)
            best_hit = {
                "kind": kind,
                "part": "",
                "page": printed_page,
                "pdf_page": pdf_page,
                "score": score,
                "snippet": snip,
                "related": True,
                "citation": f"{doc_type} | {document} | p.{printed_page}",
            }
    return best_hit


def search_evidence(
    requirement: str,
    *,
    cap_key: str | None = None,
    attached_parts: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Datasheet first, then manuals. Kept for callers that want both."""
    hit = search_datasheets(
        requirement, cap_key=cap_key, attached_parts=attached_parts
    )
    if hit:
        return hit
    return search_manuals(requirement, cap_key=cap_key)
