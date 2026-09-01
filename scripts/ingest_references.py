"""Ingest compliance sheets from data/references/ into the gold answer DB.

Usage:
  .\\.venv\\Scripts\\python.exe scripts\\ingest_references.py
  .\\.venv\\Scripts\\python.exe scripts\\ingest_references.py --replace
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.compliance import match_capability, shorten_remark  # noqa: E402
from app.corrections import _norm_status, parse_correction_file  # noqa: E402
from app.gold_reference import (  # noqa: E402
    GOLD_DIR,
    REFERENCE_DB,
    REFERENCES_DIR,
    append_reference_rows,
)


def _parse_pdf(path: Path) -> list[dict[str, str]]:
    """Best-effort PDF extraction for Comply-style sheets."""
    try:
        import fitz
    except ImportError:
        print(f"  skip PDF (no pymupdf): {path.name}")
        return []
    doc = fitz.open(path)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    items: list[dict[str, str]] = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        status = _norm_status(nxt) or _norm_status(line)
        if status and len(line) > 25 and not _norm_status(line):
            remark = ""
            if i + 2 < len(lines) and not _norm_status(lines[i + 2]):
                remark = lines[i + 2][:300]
            items.append(
                {
                    "requirement": line[:500],
                    "status": status,
                    "remarks": remark,
                    "source_document": "",
                }
            )
            i += 2
            continue
        i += 1
    return items


def ingest_file(path: Path) -> list[dict[str, Any]]:
    name = path.name
    lower = name.lower()
    rows: list[dict[str, str]] = []
    if lower.endswith((".xlsx", ".xlsm", ".docx")):
        rows = parse_correction_file(name, path.read_bytes())
    elif lower.endswith(".pdf"):
        rows = _parse_pdf(path)
    else:
        print(f"  skip unsupported: {name}")
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        req = (r.get("requirement") or "").strip()
        if not req:
            continue
        status = _norm_status(r.get("status") or "") or ""
        remark = shorten_remark(r.get("remarks") or "", 400)
        cap = match_capability(req) or ""
        out.append(
            {
                "requirement": req,
                "status": status,
                "remarks": remark,
                "capability_key": cap,
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest data/references/ into gold DB")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Wipe reference_answers.jsonl before ingest",
    )
    args = parser.parse_args()

    REFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    GOLD_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(
        p
        for p in REFERENCES_DIR.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and p.suffix.lower() in {".xlsx", ".xlsm", ".docx", ".pdf"}
        and p.name.lower() != "readme.txt"
    )
    if not files:
        print(f"No sheets in {REFERENCES_DIR}")
        print("Copy your 10 correct compliance Excel/Word/PDF files there, then re-run.")
        if args.replace or not REFERENCE_DB.exists():
            REFERENCE_DB.write_text("", encoding="utf-8")
        return 0

    if args.replace:
        REFERENCE_DB.write_text("", encoding="utf-8")
        print(f"Replaced {REFERENCE_DB}")

    total = 0
    for path in files:
        rows = ingest_file(path)
        n = append_reference_rows(rows, source_file=path.name, family="")
        total += n
        print(f"  {path.name}: {n} rows")

    print(f"Done — {total} answers in {REFERENCE_DB.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
