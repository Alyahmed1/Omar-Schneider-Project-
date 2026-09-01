"""Index Catalog / Programming / Installation PDFs and write verified citations."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = ROOT / "data" / "knowledge"

MANUALS = {
    "catalog": ROOT / "Catalog Altivar Process ATV600 variable speed drives.pdf",
    "programming": ROOT / "ATV600-Programming-Manual-EN-EAV64318-14.pdf",
    "installation": ROOT / "ATV630_650_Installation_manual_EN_EAV64301_13.pdf",
}

SEARCHES: dict[str, dict[str, Any]] = {
    "afe_low_harmonic": {
        "manual": "catalog",
        "need_any": ["thdi y", "embedded low harmonic"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "ieee519": {
        "manual": "catalog",
        "need_any": ["thdi y", "embedded low harmonic"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "line_reactors": {
        "manual": "catalog",
        "need_any": ["dc choke", "embedded low harmonic"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "power_factor": {
        "manual": "catalog",
        "need_any": ["optimum power factor", "optimum power"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "protections": {
        "manual": "programming",
        "need_any": ["[overcurrent]", "motor overload"],
        "min_pdf": 630,
        "doc_type": "User guide",
        "document": "ATV600 Programming Manual (EAV64318)",
    },
    "dry_contacts": {
        "manual": "catalog",
        "need_any": ["relay outputs"],
        "min_pdf": 20,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "analog_io": {
        "manual": "catalog",
        "need_any": ["analog inputs"],
        "min_pdf": 20,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "hoa_local_remote": {
        "manual": "catalog",
        "need_any": ["graphic display terminal"],
        "min_pdf": 20,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "comms": {
        "manual": "catalog",
        "need_any": ["modbus serial", "ethernet ip", "dual port ethernet"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "output_filter": {
        "manual": "catalog",
        "need_any": ["option: passive", "vw3a461"],
        "min_pdf": 50,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "emc_rfi": {
        "manual": "catalog",
        "need_any": ["integrated emc", "emc filter"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "ip54_enclosure": {
        "manual": "catalog",
        "need_any": ["ip55"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "efficiency_98": {
        "manual": "catalog",
        "need_any": ["efficiency"],
        "min_pdf": 12,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "pwm_vhz": {
        "manual": "programming",
        "need_any": ["voltage/frequency"],
        "min_pdf": 80,
        "doc_type": "User guide",
        "document": "ATV600 Programming Manual (EAV64318)",
    },
    "pid": {
        "manual": "programming",
        "need_any": ["pid regulator"],
        "min_pdf": 80,
        "doc_type": "User guide",
        "document": "ATV600 Programming Manual (EAV64318)",
    },
    "skip_frequencies": {
        "manual": "programming",
        "need_any": ["skip frequency"],
        "min_pdf": 100,
        "doc_type": "User guide",
        "document": "ATV600 Programming Manual (EAV64318)",
    },
    "power_ride_through": {
        "manual": "programming",
        "need_any": ["stop and go"],
        "min_pdf": 20,
        "doc_type": "User guide",
        "document": "ATV600 Programming Manual (EAV64318)",
    },
    "auto_restart": {
        "manual": "programming",
        "need_any": ["automatic restart"],
        "min_pdf": 25,
        "doc_type": "User guide",
        "document": "ATV600 Programming Manual (EAV64318)",
    },
    "flux_optimization": {
        "manual": "programming",
        "need_any": ["energy saving"],
        "min_pdf": 80,
        "doc_type": "User guide",
        "document": "ATV600 Programming Manual (EAV64318)",
    },
    "noise_switching": {
        "manual": "programming",
        "need_any": ["switching frequency"],
        "min_pdf": 50,
        "doc_type": "User guide",
        "document": "ATV600 Programming Manual (EAV64318)",
    },
    "output_freq_500": {
        "manual": "catalog",
        "need_any": ["0.1...500 hz", "0.1…500"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "cable_length": {
        "manual": "installation",
        "need_any": ["motor cable"],
        "min_pdf": 15,
        "doc_type": "Instruction sheet",
        "document": "ATV600 Installation Manual (EAV64301)",
    },
    "space_heater": {
        "manual": "installation",
        "need_any": ["space heater", "anti-condensation"],
        "min_pdf": 10,
        "doc_type": "Instruction sheet",
        "document": "ATV600 Installation Manual (EAV64301)",
    },
    "bypass": {
        "manual": "catalog",
        "need_any": ["bypass contactor", "manual bypass"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "ambient_50c": {
        "manual": "catalog",
        "need_any": ["ambient operating temperature"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "altitude_1000m": {
        "manual": "catalog",
        "need_any": ["1,000 m", "1000 m without"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "ce_iec": {
        "manual": "catalog",
        "need_any": ["european directives"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "ul_etl": {
        "manual": "catalog",
        "need_any": ["ul 508c"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "iso14001": {
        "manual": "catalog",
        "need_any": ["iso 14001", "recyclable"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
    "factory_test": {
        "manual": "catalog",
        "need_any": ["factory test", "routine test"],
        "min_pdf": 16,
        "doc_type": "Catalog",
        "document": "Catalog Altivar Process ATV600 variable speed drives",
    },
}


def _printed_page(text: str, pdf_page: int, kind: str) -> str:
    if kind == "catalog":
        m = re.search(r"(?m)^(\d{1,2}/\d{1,2})\s*$", text[:400])
        if not m:
            m = re.search(r"(?m)^(\d{1,2}/\d{1,2})\b", text[:400])
        if m:
            return m.group(1)
        return str(pdf_page)
    m = re.search(r"(?m)^(\d{1,3})\s*\nEAV\d+", text[-400:])
    if m:
        return m.group(1)
    m = re.search(r"(?m)^(\d{1,3})\s*$", text[-80:])
    if m:
        return m.group(1)
    return str(pdf_page)


def _is_toc(text: str) -> bool:
    return text.count("....") >= 6 or text.count("…") >= 8


def index_pdf(path: Path, kind: str) -> list[dict[str, Any]]:
    doc = fitz.open(path)
    pages = []
    for i, page in enumerate(doc, start=1):
        text = page.get_text("text") or ""
        pages.append(
            {
                "pdf_page": i,
                "printed_page": _printed_page(text, i, kind),
                "text": text.lower(),
                "toc": _is_toc(text),
            }
        )
    return pages


def first_hit(
    pages: list[dict[str, Any]],
    need_any: list[str],
    *,
    min_pdf: int = 1,
) -> dict[str, Any] | None:
    for p in pages:
        if p["pdf_page"] < min_pdf or p["toc"]:
            continue
        blob = p["text"]
        for kw in need_any:
            if kw.lower() in blob:
                return {
                    "pdf_page": p["pdf_page"],
                    "printed_page": p["printed_page"],
                    "matched": kw,
                }
    return None


def page_exists(manual: str, printed: str) -> bool:
    """Used by correction import to verify a client-typed page."""
    idx_path = KNOWLEDGE / "page_index.json"
    needle = str(printed).strip()
    if not needle:
        return False
    if idx_path.exists():
        data = json.loads(idx_path.read_text(encoding="utf-8"))
        pages = data.get(manual) or []
        return any(str(p.get("printed_page")) == needle or str(p.get("pdf_page")) == needle for p in pages)
    src = MANUALS.get(manual)
    if not src or not src.exists():
        return False
    pages = index_pdf(src, manual)
    return any(str(p["printed_page"]) == needle or str(p["pdf_page"]) == needle for p in pages)



def main() -> None:
    KNOWLEDGE.mkdir(parents=True, exist_ok=True)
    caches: dict[str, list[dict[str, Any]]] = {}
    copied = []
    for key, src in MANUALS.items():
        if not src.exists():
            raise SystemExit(f"Missing manual: {src}")
        dest = KNOWLEDGE / src.name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        copied.append(dest.name)
        print(f"indexing {src.name}…")
        caches[key] = index_pdf(src, key)
        print(f"  pages={len(caches[key])}")

    sources: dict[str, Any] = {
        "_meta": {
            "policy": "Page numbers only when the topic keywords appear on that PDF page in the indexed manuals.",
            "manuals": copied,
        }
    }
    for cap, section in (
        ("overload_vt", "ND/HD rating table for selected reference"),
        ("input_voltage_380_400", "Supply / rating table for selected reference"),
        ("freq_50hz", "Supply / rating table for selected reference"),
    ):
        sources[cap] = {
            "doc_type": "Catalog",
            "document": "Catalog Altivar Process ATV600 variable speed drives",
            "page": None,
            "use_selected_catalog_page": True,
            "section": section,
            "evidence": "Page from sized drive CSV source_page only.",
        }

    for cap, spec in SEARCHES.items():
        hit = first_hit(
            caches[spec["manual"]],
            spec["need_any"],
            min_pdf=int(spec.get("min_pdf") or 1),
        )
        if hit:
            sources[cap] = {
                "doc_type": spec["doc_type"],
                "document": spec["document"],
                "page": str(hit["printed_page"]),
                "pdf_page": hit["pdf_page"],
                "evidence": (
                    f"Indexed {spec['manual']} PDF p.{hit['pdf_page']} "
                    f"(printed {hit['printed_page']}); matched '{hit['matched']}'."
                ),
            }
            print(f"OK {cap}: printed={hit['printed_page']} pdf={hit['pdf_page']} ({hit['matched']})")
        else:
            sources[cap] = {
                "doc_type": spec["doc_type"],
                "document": spec["document"],
                "page": None,
                "section": f"{cap} (page not verified in manuals)",
                "evidence": "Keywords not found as a unique page hit.",
            }
            print(f"MISS {cap}")

    for cap, section in (
        ("submittals", "Submittal package"),
        ("manufacturer_approved", "Schneider Electric manufacturer offer"),
        ("local_support_egypt", "Support / services (page not verified)"),
    ):
        sources[cap] = {
            "doc_type": "Product Datasheet" if cap == "submittals" else "Catalog",
            "document": "Attached Product Datasheets + Catalog"
            if cap == "submittals"
            else "Catalog Altivar Process ATV600 variable speed drives",
            "page": None,
            "section": section,
            "evidence": "No single verified page.",
        }

    idx_path = ROOT / "data" / "knowledge" / "page_index.json"
    idx_path.write_text(
        json.dumps(
            {
                key: [{"pdf_page": p["pdf_page"], "printed_page": p["printed_page"]} for p in pages]
                for key, pages in caches.items()
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("wrote", idx_path)

    out = ROOT / "data" / "patterns" / "schneider_sources.json"
    out.write_text(json.dumps(sources, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", out)



if __name__ == "__main__":
    main()
