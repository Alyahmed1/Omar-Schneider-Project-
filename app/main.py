"""FastAPI application — Schneider VFD Compliance + Part Lookup."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.corrections import apply_corrections, parse_correction_file
from app.compliance import draft_compliance, resolve_pattern_key

from app.evidence import save_datasheet_bytes
from app.exporters import export_excel, export_word
from app.llm import llm_status
from app.pdf_extract import clauses_to_dicts, extract_vfd_clauses
from app.se_lookup import (
    build_merged_datasheet_pdf,
    download_document_bytes,
    filter_product_datasheets,
    lookup_many,
    merged_datasheet_filename,
    normalize_part_numbers,
)
from app.sizing import size_request


ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = ROOT / "uploads"
EXPORT_DIR = ROOT / "exports"
KNOWLEDGE_DIR = ROOT / "data" / "knowledge"
FRONTEND_DIR = ROOT / "frontend"
DATASHEET_CACHE = ROOT / "uploads" / "datasheets"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
DATASHEET_CACHE.mkdir(parents=True, exist_ok=True)

# Compliance draft sessions
SESSIONS: dict[str, dict[str, Any]] = {}
# Attached part datasheets for compliance (shared workspace pack)
ATTACHED_PARTS: list[dict[str, Any]] = []

app = FastAPI(
    title="Schneider VFD Compliance + Part Lookup",
    version="1.1.0",
    description="Draft VFD compliance sheets from consultant PDFs and look up Schneider part numbers/datasheets.",
)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


class DraftUpdate(BaseModel):
    session_id: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    drive_family: str | None = None


class LookupRequest(BaseModel):
    part_numbers: str


class AttachPartsRequest(BaseModel):
    parts: list[dict[str, Any]] = Field(default_factory=list)


class MergedPdfRequest(BaseModel):
    parts: list[dict[str, Any]] = Field(default_factory=list)


class SizeLine(BaseModel):
    kw: float
    hp: float | None = None
    qty: int | None = None
    duty: str | None = None
    cabinet: bool | None = None
    hz: int | None = None
    harmonics: str | None = None
    enclosure_ip: str | None = None


class SizeRequest(BaseModel):
    lines: list[SizeLine] = Field(default_factory=list)
    duty: str = "AUTO"
    cabinet: bool = False
    supply_pref: str = "380-480"
    hz: int = 50
    harmonics: str = "standard"
    enclosure_ip: str = "21"


def _safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>Frontend missing</h1>", status_code=500)
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "port_hint": 8001,
        **llm_status(),
        "attached_parts": [
            {
                "part_number": p.get("part_number"),
                "document_count": len(p.get("documents") or []),
            }
            for p in ATTACHED_PARTS
        ],
        "knowledge_files": sorted(p.name for p in KNOWLEDGE_DIR.glob("*.pdf")),
    }


@app.post("/api/size")
async def size_drives(body: SizeRequest) -> dict[str, Any]:
    """kW/HP → recommended drive Reference + passive filter options (lookup, not ML)."""
    if not body.lines:
        raise HTTPException(400, "Provide at least one line with kw.")
    payload = [
        {
            "kw": line.kw,
            "hp": line.hp,
            "qty": line.qty,
            "duty": line.duty,
            "cabinet": line.cabinet if line.cabinet is not None else body.cabinet,
            "hz": line.hz if line.hz is not None else body.hz,
            "harmonics": line.harmonics if line.harmonics is not None else body.harmonics,
            "enclosure_ip": line.enclosure_ip if line.enclosure_ip is not None else body.enclosure_ip,
        }
        for line in body.lines
    ]
    return size_request(
        payload,
        duty=body.duty,
        cabinet=body.cabinet,
        supply_pref=body.supply_pref,
        hz=body.hz,
        harmonics=body.harmonics,
        enclosure_ip=body.enclosure_ip,
    )


@app.get("/api/attached")
async def get_attached() -> dict[str, Any]:
    return {
        "count": len(ATTACHED_PARTS),
        "offer": _offer_from_attached(),
        "parts": ATTACHED_PARTS,
    }


def _offer_from_attached(parts: list[dict[str, Any]] | None = None) -> str:
    parts = parts if parts is not None else ATTACHED_PARTS
    nums = [str(p.get("part_number") or "").strip().upper() for p in parts]
    nums = [n for n in nums if n]
    return "/".join(nums)


def _normalize_attached(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in parts:
        pn = str(item.get("part_number") or "").strip().upper()
        if not pn:
            continue
        docs = filter_product_datasheets(item.get("documents") or [])
        entry: dict[str, Any] = {
            "part_number": pn,
            "title": item.get("title") or pn,
            "url": item.get("url") or "",
            "documents": docs,
        }
        page = item.get("source_page") or item.get("catalog_page")
        if page:
            entry["source_page"] = str(page)
        if item.get("family"):
            entry["family"] = str(item.get("family"))
        if item.get("ip_note"):
            entry["ip_note"] = str(item.get("ip_note"))
        cleaned.append(entry)
    return cleaned


@app.post("/api/attached")
async def set_attached(body: AttachPartsRequest) -> dict[str, Any]:
    """Attach looked-up parts + Product Datasheet docs for compliance."""
    global ATTACHED_PARTS
    ATTACHED_PARTS = _normalize_attached(body.parts)
    cached = 0
    for item in ATTACHED_PARTS:
        pn = str(item.get("part_number") or "")
        docs = item.get("documents") or []
        url = (docs[0].get("url") if docs else "") or ""
        if not pn or not url:
            continue
        try:
            data = await download_document_bytes(url)
            save_datasheet_bytes(pn, data)
            cached += 1
        except Exception:
            continue
    return {
        "ok": True,
        "count": len(ATTACHED_PARTS),
        "datasheets_cached": cached,
        "offer": _offer_from_attached(),
        "parts": ATTACHED_PARTS,
    }


@app.delete("/api/attached")
async def clear_attached() -> dict[str, Any]:
    global ATTACHED_PARTS
    ATTACHED_PARTS = []
    return {"ok": True, "count": 0}


@app.post("/api/compliance/generate")
async def generate_compliance(
    file: UploadFile = File(...),
) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")

    if not ATTACHED_PARTS:
        raise HTTPException(
            400,
            "Compliance requires Product Datasheets of imported part numbers. "
            "Use Part Lookup first, then click Attach for compliance.",
        )
    if not any(p.get("documents") for p in ATTACHED_PARTS):
        raise HTTPException(
            400,
            "Attached parts have no Product Datasheet. Look up parts again and attach ones with a Product Datasheet.",
        )

    offer = _offer_from_attached()

    session_id = uuid.uuid4().hex
    dest = UPLOAD_DIR / f"{session_id}_{Path(file.filename).name}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        clauses = extract_vfd_clauses(dest)
        if not clauses:
            raise HTTPException(
                422,
                "No VFD / VSD related clauses were found. Check that the PDF contains "
                "variable frequency / speed drive requirements.",
            )
        draft = draft_compliance(clauses, offer, attached_parts=ATTACHED_PARTS)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Failed to process PDF: {exc}") from exc

    SESSIONS[session_id] = {
        "drive_family": offer,
        "pattern_key": resolve_pattern_key(offer),
        "source_file": file.filename,
        "clauses": clauses_to_dicts(clauses),
        "draft": draft,
        "attached_parts": list(ATTACHED_PARTS),
    }

    return {
        "session_id": session_id,
        "drive_family": offer,
        "pattern_key": resolve_pattern_key(offer),
        "source_file": file.filename,
        "clause_count": len(draft),
        "attached_parts": ATTACHED_PARTS,
        "rows": draft,
    }


@app.post("/api/compliance/update")
async def update_compliance(body: DraftUpdate) -> dict[str, Any]:
    session = SESSIONS.get(body.session_id)
    if not session:
        raise HTTPException(404, "Session not found. Generate a draft first.")
    cleaned = []
    for row in body.rows:
        status = str(row.get("status", "Yes")).strip()
        status_norm = status.upper().replace("NOT APPLICABLE", "N/A")
        if status_norm in ("YES", "Y"):
            status = "Yes"
        elif status_norm in ("NO", "N"):
            status = "No"
        elif status_norm in ("N/A", "NA"):
            status = "N/A"
        else:
            status = "Yes"
        row = dict(row)
        row["status"] = status
        cleaned.append(row)
    session["draft"] = cleaned
    if body.drive_family is not None and body.drive_family.strip():
        session["drive_family"] = body.drive_family.strip()
    return {"ok": True, "clause_count": len(cleaned), "rows": cleaned}


@app.post("/api/compliance/regenerate")
async def regenerate_compliance(
    session_id: str = Form(...),
) -> dict[str, Any]:
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found.")
    attached = session.get("attached_parts") or ATTACHED_PARTS
    if not attached or not any(p.get("documents") for p in attached):
        raise HTTPException(
            400,
            "Compliance requires attached Product Datasheets of imported part numbers.",
        )
    offer = _offer_from_attached(attached)
    draft = draft_compliance(session["clauses"], offer, attached_parts=attached)
    session["drive_family"] = offer
    session["pattern_key"] = resolve_pattern_key(offer)
    session["draft"] = draft
    return {
        "session_id": session_id,
        "drive_family": offer,
        "clause_count": len(draft),
        "rows": draft,
    }


@app.get("/api/compliance/export/{fmt}")
async def export_compliance(fmt: str, session_id: str) -> Response:
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found. Generate the compliance draft again, then download.")
    draft = session.get("draft") or []
    if not draft:
        raise HTTPException(400, "No draft to export. Generate a compliance sheet first.")
    family = session.get("drive_family", "")
    attached = session.get("attached_parts") or ATTACHED_PARTS
    safe = _safe_name(family)[:40] or "offer"
    fmt = fmt.lower()
    try:
        if fmt in ("xlsx", "excel"):
            data = export_excel(draft, family, attached)
            return Response(
                content=data,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f'attachment; filename="compliance_{safe}.xlsx"'},
            )
        if fmt in ("docx", "word"):
            data = export_word(draft, family, attached)
            return Response(
                content=data,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": f'attachment; filename="compliance_{safe}.docx"'},
            )
    except Exception as exc:
        raise HTTPException(500, f"Export failed: {exc}") from exc
    raise HTTPException(400, "Format must be xlsx or docx")


@app.post("/api/compliance/import-corrections")
async def import_corrections(
    file: UploadFile = File(...),
    offer: str = Form(""),
) -> dict[str, Any]:
    name = file.filename or ""
    if not name.lower().endswith((".xlsx", ".xlsm", ".docx")):
        raise HTTPException(400, "Upload an Excel (.xlsx) or Word (.docx) compliance sheet.")
    data = await file.read()
    try:
        rows = parse_correction_file(name, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Could not parse file: {exc}") from exc
    if not rows:
        raise HTTPException(422, "No requirement rows found. Use the Excel or Word export from this app.")
    offer_text = (offer or "").strip() or _offer_from_attached()
    return apply_corrections(rows, filename=name, offer_text=offer_text)


@app.post("/api/knowledge/upload")
async def upload_knowledge(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    dest = KNOWLEDGE_DIR / Path(file.filename).name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {
        "ok": True,
        "saved": dest.name,
        "files": sorted(p.name for p in KNOWLEDGE_DIR.glob("*.pdf")),
    }


@app.post("/api/lookup")
async def part_lookup(body: LookupRequest) -> dict[str, Any]:
    parts = normalize_part_numbers(body.part_numbers)
    if not parts:
        raise HTTPException(400, "No valid part numbers found in the input.")
    if len(parts) > 40:
        raise HTTPException(400, "Please look up at most 40 part numbers at a time.")
    results = await lookup_many(parts)
    return {"count": len(results), "results": results}


@app.post("/api/lookup/merged-pdf")
async def lookup_merged_pdf(body: MergedPdfRequest) -> Response:
    if not body.parts:
        raise HTTPException(400, "No parts provided for combined datasheet.")
    pack = []
    for p in body.parts:
        docs = filter_product_datasheets(p.get("documents") or [])
        if docs:
            pack.append({**p, "documents": docs[:1]})
    if not pack:
        raise HTTPException(
            400,
            "No Product Datasheet available to merge. Only Product Datasheet PDFs are included.",
        )
    try:
        data, included = await build_merged_datasheet_pdf(pack)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Combined datasheet build failed: {exc}") from exc
    if not data:
        raise HTTPException(500, "Merged PDF was empty — Product Datasheet download may have failed.")
    filename = merged_datasheet_filename(included)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/lookup/document")
async def proxy_document(url: str, filename: str = "document.pdf") -> Response:
    allowed = (
        "download.schneider-electric.com" in url
        or ("www.se.com" in url and "/product/download-pdf/" in url)
    )
    if not allowed:
        raise HTTPException(400, "Only Schneider Product Datasheet / document URLs are allowed.")
    try:
        data = await download_document_bytes(url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Download failed: {exc}") from exc
    safe = _safe_name(filename) or "document.pdf"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


@app.get("/api/session/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found.")
    return {
        "session_id": session_id,
        "drive_family": session.get("drive_family"),
        "source_file": session.get("source_file"),
        "attached_parts": session.get("attached_parts", []),
        "rows": session.get("draft", []),
    }
