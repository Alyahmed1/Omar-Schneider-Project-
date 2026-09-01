"""kW/HP drive sizing from catalog CSV + passive filter suggestions."""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = ROOT / "data" / "catalog"
DRIVE_CSV = CATALOG_DIR / "drive_ratings.csv"
DRIVE_CSV_NEW = CATALOG_DIR / "drive_ratings_new.csv"
FILTER_CSV = CATALOG_DIR / "passive_filter_selection.csv"


def _to_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except ValueError:
        return None


def _drive_csv_path() -> Path:
    """Prefer extracted _new file when present (e.g. Excel had main CSV locked)."""
    if DRIVE_CSV_NEW.exists():
        return DRIVE_CSV_NEW
    return DRIVE_CSV


@lru_cache(maxsize=4)
def _load_drive_ratings_cached(path_str: str, mtime: float) -> list[dict[str, Any]]:
    path = Path(path_str)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(
                {
                    **r,
                    "kw": _to_float(r.get("kw")),
                    "hp": _to_float(r.get("hp")),
                    "variant_rank": int(float(r.get("variant_rank") or 0)),
                    "is_default_variant": str(r.get("is_default_variant", "")).lower()
                    == "true",
                }
            )
    return rows


def load_drive_ratings() -> list[dict[str, Any]]:
    path = _drive_csv_path()
    if not path.exists():
        return []
    return _load_drive_ratings_cached(str(path), path.stat().st_mtime)


@lru_cache(maxsize=1)
def load_filter_selection() -> dict[str, dict[str, str]]:
    if not FILTER_CSV.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    with FILTER_CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ref = str(r.get("reference") or "").strip().upper()
            if ref:
                out[ref] = r
    return out


def normalize_harmonics(harmonics: str | None) -> str:
    raw = (harmonics or "standard").strip().lower().replace(" ", "_")
    if raw in ("low", "low_harmonics", "lh", "thdi5", "ieee519"):
        return "low"
    return "standard"


def normalize_enclosure_ip(enclosure_ip: str | None) -> str:
    """IP21 (default) vs IP54/IP55 overlay. Returns '21', '54', or '55'."""
    raw = re.sub(r"[^0-9]", "", str(enclosure_ip or "21"))
    if raw in ("54", "55"):
        return raw
    return "21"


def preferred_family_for_kw(
    kw: float,
    harmonics: str = "standard",
    enclosure_ip: str | None = None,
) -> str:
    """
    Locked family preference by harmonics only (IP is a Step 2 compliance rule).
    Low: <110 ATV630 (+ passive filter); ≥110 ATV680.
    Standard: <110 ATV630; 110–315 ATV630 (over 660); >315 ATV660.
    """
    mode = normalize_harmonics(harmonics)
    if mode == "low":
        if kw < 110:
            return "ATV630"
        return "ATV680"
    if kw < 110:
        return "ATV630"
    if kw <= 315:
        return "ATV630"
    return "ATV660"


def _supply_ok(row: dict[str, Any], supply_pref: str) -> bool:
    s = (row.get("supply_v") or "").lower()
    if supply_pref == "380-480":
        return any(x in s for x in ("380-480", "380-440", "380-415", "480"))
    if supply_pref == "200-240":
        return "200-240" in s
    if supply_pref == "500-690":
        return "500-690" in s
    return True


def _cabinet_ok(row: dict[str, Any], cabinet: bool) -> bool:
    mounting = row.get("mounting") or ""
    ref = row.get("reference") or ""
    is_cab = mounting == "cabinet_Z" or str(ref).endswith("Z")
    if cabinet:
        return is_cab
    return not is_cab


def find_candidates(
    kw: float,
    hp: float | None = None,
    *,
    duty: str = "AUTO",
    cabinet: bool = False,
    supply_pref: str = "380-480",
) -> list[dict[str, Any]]:
    """
    Match catalog rows by kW (+ optional HP).
    duty: ND | HD | AUTO (search both Normal and Heavy duty).
    """
    duty_u = (duty or "AUTO").upper().replace(" ", "")
    if duty_u in ("AUTO", "BOTH", "ANY", ""):
        allowed = {"ND", "HD"}
    elif duty_u in ("ND", "HD"):
        allowed = {duty_u}
    else:
        allowed = {"ND", "HD"}

    matches: list[dict[str, Any]] = []
    for row in load_drive_ratings():
        if row.get("duty") not in allowed:
            continue
        if row.get("kw") is None:
            continue
        if abs(float(row["kw"]) - float(kw)) > 0.051:
            continue
        if hp is not None and row.get("hp") is not None:
            if abs(float(row["hp"]) - float(hp)) > 0.51:
                continue
        if not _supply_ok(row, supply_pref):
            continue
        if not _cabinet_ok(row, cabinet):
            continue
        matches.append(row)

    # If cabinet requested but nothing found, fall back to non-cabinet
    if cabinet and not matches:
        return find_candidates(
            kw, hp, duty=duty, cabinet=False, supply_pref=supply_pref
        )
    return matches


def pick_recommended(
    candidates: list[dict[str, Any]],
    kw: float,
    *,
    harmonics: str = "standard",
    enclosure_ip: str | None = None,
) -> dict[str, Any] | None:
    if not candidates:
        return None
    prefer = preferred_family_for_kw(kw, harmonics, enclosure_ip)
    mode = normalize_harmonics(harmonics)

    def score(row: dict[str, Any]) -> tuple:
        family = row.get("family") or ""
        duty = (row.get("duty") or "").upper()
        # Always prefer ND when both ND and HD match the same kW
        duty_pen = 0 if duty == "ND" else 1
        family_ok = 0 if family == prefer else 1
        default_pen = 0 if row.get("is_default_variant") else 1
        # Family tie-breakers within / outside preferred family
        if prefer == "ATV630":
            fam_tie = {"ATV630": 0, "ATV650": 1, "ATV660": 2, "ATV680": 3}.get(
                family, 4
            )
        elif prefer == "ATV660":
            fam_tie = 0 if family == "ATV660" else 5
        elif prefer == "ATV680":
            fam_tie = 0 if family == "ATV680" else 5
        else:
            fam_tie = 0 if family == prefer else 5
        # Egypt / 380-480 preference: Q4 (380-415) before T4 (480)
        ref = row.get("reference") or ""
        supply = (row.get("supply_v") or "").lower()
        volt_pen = 0
        if "t4" in ref.lower() or supply.startswith("480"):
            volt_pen = 2
        elif "q4" in ref.lower() or "380-415" in supply:
            volt_pen = 0
        elif "380-440" in supply:
            volt_pen = 1
        return (
            family_ok,
            duty_pen,
            default_pen,
            volt_pen,
            row.get("variant_rank", 99),
            fam_tie,
            ref,
            mode,
        )

    return sorted(candidates, key=score)[0]


def parse_filter_qty(code: str) -> tuple[str, int]:
    """Handle 'VW3A46116 x2' style cells."""
    raw = (code or "").strip()
    if not raw:
        return "", 1
    m = re.match(r"^([A-Z0-9]+)\s*x\s*(\d+)$", raw, re.I)
    if m:
        return m.group(1).upper(), int(m.group(2))
    return raw.upper(), 1


def suggest_filters(
    reference: str,
    hz: int = 50,
    *,
    harmonics: str = "standard",
    family: str | None = None,
) -> dict[str, Any]:
    """Passive filters only in Low harmonics mode. Standard → no filter offer."""
    ref = (reference or "").strip().upper()
    mode = normalize_harmonics(harmonics)
    empty = {
        "reference": ref,
        "hz": int(hz),
        "filter_5pct": None,
        "quantity_5pct": None,
        "preferred_filter": None,
        "preferred_quantity": None,
        "preferred_thdi": None,
        "fan_kit": None,
        "emc_filter": None,
        "available": False,
        "required_for_low_harmonics": False,
    }
    if mode != "low":
        return {
            **empty,
            "note": "Standard harmonics: no passive filter (filters only for Low THDi <5%).",
        }
    table = load_filter_selection()
    row = table.get(ref)
    base_note = ""
    if (family or "").startswith("ATV630"):
        base_note = "Low harmonics mode: 5% THDi passive filter with ATV630."
    elif (family or "").startswith("ATV650"):
        base_note = "Low harmonics mode: 5% THDi passive filter with ATV650."
    if not row:
        return {
            **empty,
            "required_for_low_harmonics": (family or "").startswith(("ATV630", "ATV650")),
            "note": base_note
            or "No 5% passive-filter row for this drive (common for ATV660/ATV680 systems).",
        }
    if int(hz) == 60:
        f5 = row.get("filter_5pct_60hz", "")
    else:
        f5 = row.get("filter_5pct_50hz", "")
    code5, qty5 = parse_filter_qty(f5)
    preferred = code5 or None
    preferred_qty = qty5 if code5 else None
    note = base_note or "Offer uses 5% THDi passive filter only."
    if not preferred:
        note = (note + " " if note else "") + "5% filter SKU missing for this rating — check selection guide."
    elif code5:
        note = (note + " " if note else "") + "Filter = 5% THDi."
    return {
        "reference": ref,
        "hz": int(hz),
        "filter_5pct": code5 or None,
        "quantity_5pct": qty5 if code5 else None,
        "preferred_filter": preferred,
        "preferred_quantity": preferred_qty,
        "preferred_thdi": "5pct" if code5 else None,
        "fan_kit": row.get("fan_kit") or None,
        "emc_filter": row.get("emc_filter") or None,
        "available": bool(code5),
        "required_for_low_harmonics": (family or "").startswith(("ATV630", "ATV650")),
        "note": note,
    }


def size_line(
    kw: float,
    hp: float | None = None,
    *,
    duty: str = "AUTO",
    cabinet: bool = False,
    supply_pref: str = "380-480",
    hz: int = 50,
    qty: int | None = None,
    harmonics: str = "standard",
    enclosure_ip: str | None = "21",
) -> dict[str, Any]:
    mode = normalize_harmonics(harmonics)
    ip = normalize_enclosure_ip(enclosure_ip)
    candidates = find_candidates(
        kw, hp, duty=duty, cabinet=cabinet, supply_pref=supply_pref
    )
    recommended = pick_recommended(
        candidates, kw, harmonics=mode, enclosure_ip=ip
    )
    prefer = preferred_family_for_kw(kw, mode, ip)
    filters = (
        suggest_filters(
            recommended["reference"],
            hz=hz,
            harmonics=mode,
            family=recommended.get("family"),
        )
        if recommended
        else None
    )
    matched_duty = (recommended.get("duty") if recommended else None) or None
    return {
        "input": {
            "kw": kw,
            "hp": hp,
            "duty": (duty or "AUTO").upper(),
            "cabinet": cabinet,
            "supply_pref": supply_pref,
            "hz": hz,
            "qty": qty,
            "harmonics": mode,
            "enclosure_ip": ip,
        },
        "preferred_family": prefer,
        "recommended": {
            "reference": recommended.get("reference"),
            "family": recommended.get("family"),
            "duty": matched_duty,
            "kw": recommended.get("kw"),
            "hp": recommended.get("hp"),
            "mounting": recommended.get("mounting"),
            "supply_v": recommended.get("supply_v"),
            "has_dc_choke": recommended.get("has_dc_choke"),
            "ip_note": recommended.get("ip_note"),
            "source_page": recommended.get("source_page"),
        }
        if recommended
        else None,
        "candidates": [
            {
                "reference": c.get("reference"),
                "family": c.get("family"),
                "duty": c.get("duty"),
                "mounting": c.get("mounting"),
                "is_default_variant": c.get("is_default_variant"),
                "supply_v": c.get("supply_v"),
                "source_page": c.get("source_page"),
            }
            for c in sorted(
                candidates,
                key=lambda x: (
                    0 if (x.get("duty") or "").upper() == "ND" else 1,
                    0 if x.get("is_default_variant") else 1,
                    x.get("variant_rank", 99),
                    x.get("reference") or "",
                ),
            )[:12]
        ],
        "passive_filters": filters,
        "ok": recommended is not None,
        "message": None
        if recommended
        else f"No catalog match for {kw} kW under {supply_pref} V (ND/HD).",
    }


def size_request(
    lines: list[dict[str, Any]],
    *,
    duty: str = "AUTO",
    cabinet: bool = False,
    supply_pref: str = "380-480",
    hz: int = 50,
    harmonics: str = "standard",
    enclosure_ip: str = "21",
) -> dict[str, Any]:
    mode = normalize_harmonics(harmonics)
    ip = normalize_enclosure_ip(enclosure_ip)
    results = []
    for line in lines:
        kw = _to_float(line.get("kw"))
        if kw is None:
            results.append(
                {
                    "ok": False,
                    "message": "Missing kw",
                    "input": line,
                    "recommended": None,
                    "candidates": [],
                    "passive_filters": None,
                }
            )
            continue
        hp = _to_float(line.get("hp"))
        qty = line.get("qty")
        try:
            qty_i = int(qty) if qty is not None and str(qty).strip() != "" else None
        except ValueError:
            qty_i = None
        line_duty = str(line.get("duty") or duty or "AUTO")
        line_cabinet = bool(line.get("cabinet", cabinet))
        line_hz = int(line.get("hz") or hz)
        line_harm = normalize_harmonics(str(line.get("harmonics") or mode))
        line_ip = normalize_enclosure_ip(str(line.get("enclosure_ip") or ip))
        results.append(
            size_line(
                kw,
                hp,
                duty=line_duty,
                cabinet=line_cabinet,
                supply_pref=str(line.get("supply_pref") or supply_pref),
                hz=line_hz,
                qty=qty_i,
                harmonics=line_harm,
                enclosure_ip=line_ip,
            )
        )
    return {
        "count": len(results),
        "ok_count": sum(1 for r in results if r.get("ok")),
        "harmonics": mode,
        "enclosure_ip": ip,
        "results": results,
    }
