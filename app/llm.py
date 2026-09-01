"""Optional Ollama remark writer. Never decides Yes/No or citations.

Disabled unless LLM_ENABLED=true. Generate keeps using rules + PDF evidence.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_TRUE = {"1", "true", "yes", "on"}


def llm_enabled() -> bool:
    return os.environ.get("LLM_ENABLED", "false").strip().lower() in _TRUE


def llm_model() -> str:
    return os.environ.get("OLLAMA_MODEL", "qwen2.5:3b").strip() or "qwen2.5:3b"


def llm_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").strip().rstrip("/")


def llm_status() -> dict[str, Any]:
    return {
        "llm_enabled": llm_enabled(),
        "llm_model": llm_model(),
    }


def _xml_safe(value: Any) -> str:
    if value is None:
        return ""
    return _ILLEGAL_XML.sub(" ", str(value)).strip()


def _extract_remarks(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    remarks = _xml_safe(data.get("remarks"))
    return remarks or None


def polish_remark(
    *,
    requirement: str,
    status: str,
    remarks: str,
    citation: str = "",
) -> str | None:
    """Rewrite Why-text only. Returns None to keep the original remark."""
    if not llm_enabled():
        return None
    original = _xml_safe(remarks)
    if not original:
        return None
    if "no related evidence" in original.lower():
        return None

    locked_status = _xml_safe(status) or "Yes"
    req = _xml_safe(requirement)[:400]
    cite = _xml_safe(citation)[:240]

    payload = {
        "model": llm_model(),
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_predict": 120},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You rewrite Schneider VFD compliance Why-remarks. "
                    "Return JSON only: {\"remarks\": \"...\"}. "
                    "Do not change Yes/No/N/A. Do not add or invent page numbers or documents. "
                    "Use only the given remark and requirement. One short engineer sentence."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Locked status: {locked_status}\n"
                    f"Locked source (do not repeat unless already in remark): {cite or '(none)'}\n"
                    f"Requirement: {req}\n"
                    f"Current remark: {original}\n"
                    "Rewrite the remark. Keep the same meaning. No new facts."
                ),
            },
        ],
    }

    try:
        with httpx.Client(timeout=8.0) as client:
            res = client.post(f"{llm_host()}/api/chat", json=payload)
            res.raise_for_status()
            body = res.json()
    except (httpx.HTTPError, json.JSONDecodeError, OSError):
        return None

    raw = ""
    if isinstance(body, dict):
        message = body.get("message") or {}
        if isinstance(message, dict):
            raw = str(message.get("content") or "")
        if not raw:
            raw = str(body.get("response") or "")
    polished = _extract_remarks(raw)
    if not polished or polished.lower() == original.lower():
        return None
    return polished


def polish_row(row: dict[str, Any]) -> dict[str, Any]:
    """Replace remarks only. Status and citations stay as-is."""
    if not llm_enabled() or not isinstance(row, dict):
        return row
    polished = polish_remark(
        requirement=str(row.get("requirement") or ""),
        status=str(row.get("status") or ""),
        remarks=str(row.get("remarks") or ""),
        citation=str(row.get("source_document") or ""),
    )
    if not polished:
        return row
    row["remarks"] = polished
    origin = str(row.get("answer_origin") or "rules")
    if not origin.endswith("_llm"):
        row["answer_origin"] = f"{origin}_llm"
    return row
