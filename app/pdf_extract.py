"""VFD / VSD clause extraction from consultant specification PDFs."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fitz  # pymupdf


SECTION_ENTER = re.compile(
    r"(?:2\.04\s+)?VARIABLE\s+FREQUENCY\s+DRIVE\b|"
    r"2\.6\s+Variable\s+speed\s+drives|"
    r"Variable\s+speed\s+drives\s*\(VSDS?\)",
    re.IGNORECASE,
)

SECTION_LEAVE = re.compile(
    r"\bPART\s+3\s*\(NOT APPLICABLE\)|\bEND\s+OF\s+SECTION\b|"
    r"\bSECTION\s+23\s+21\b|\bHYDRONIC\s+PUMPS\b",
    re.IGNORECASE,
)

STRONG_VFD = re.compile(
    r"variable\s+frequency\s+drive|\bVFDs?\b|variable\s+speed\s+drive|\bVSDs?\b|"
    r"high\s+frequency\s+drive|high\s+speed\s+drive|active\s+front\s+end|\bAFE\b|"
    r"low[\s\-]?harmonic|\bTHDi?\b|IEEE\s*519|frequency\s+inverter|"
    r"sinus\s+filter|input\s+line\s+(?:reactor|filter)|EMC\s+filter|RFI\s+filter|"
    r"The\s+VFD\b|VFD\s+enclosure|All\s+VFDs?\b|Furnish\s+complete\s+variable\s+frequency|"
    r"PWM\s+type\s+drives|Hand/Off/Auto|manual\s+by-?pass|"
    r"stepless\s+motor\s+speed|converter\s+section|inverter\s+section|"
    r"Protective\s+Features|Interface\s+Features|Special\s+Features|"
    r"Service\s+Conditions|Quality\s+Assurance|drive\s+disconnect|"
    r"motor\s+circuit\s+protector|Drive/Off/Line",
    re.IGNORECASE,
)

TOPIC_SPLIT = re.compile(
    r"(?=(?:Protective Features|Interface Features|Adjustments|Service Conditions|"
    r"Special Features|Quality Assurance|Submittals)\s*:)|"
    r"(?:(?<=^)|(?<=\.\s))"
    r"(?=(?:The VFD\b|Furnish complete variable frequency|Power line noise|"
    r"Motor noise as a result of the VFD|All VFDs shall include|"
    r"Manual by-pass shall|A door interlocked|A motor circuit protector|"
    r"The disconnect and by-pass|Input line reactors shall be provided|"
    r"Provide Active front end|All VFD are to be|All VFDs to include|"
    r"VFD to be sized|VFD to be light|Internal EMC filter|"
    r"Input and output line reactor|VFD are to include space heater|"
    r"Protection functions of VFD|Variable Speed Pumps:))",
    re.IGNORECASE,
)

SKIP_LINE = re.compile(
    r"^(?:DCP-\d+|COMMON ELECTRICAL|HYDRONIC PUMPS|DIVISION|PART \d|"
    r"END OF SECTION|"
    r"\d{4}\s+(?:FEB|JAN|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)|"
    r".*Page\s+\d+\s+of\s+\d+)",
    re.IGNORECASE,
)

NOISE = re.compile(
    r"Float Switch|Pressure Switch|Low Suction|Square D Company|"
    r"Motor Control Center|Motor Control Panels|Switch Disconnectors|"
    r"Starter Type|Hydraulic modelling|impeller diameter|"
    r"pump base plate|Owner Representatives|business class|"
    r"Stator insulation|Pump Pressure Ratings|PERFORMANCE REQUIREMENTS",
    re.IGNORECASE,
)

NUMBERED_ITEM = re.compile(r"^(?P<label>\d+)[\.\)]\s+(?P<body>.+)$")
LETTERED_ITEM = re.compile(r"^(?P<label>[A-Za-z])[\.\)]\s+(?P<body>.+)$")


@dataclass
class Clause:
    clause_id: str
    requirement: str
    source_page: int
    section: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_pdf_text(path: str | Path) -> list[tuple[int, str]]:
    doc = fitz.open(path)
    pages: list[tuple[int, str]] = []
    try:
        for i, page in enumerate(doc):
            text = (page.get_text("text") or "").replace("\r\n", "\n").replace("\r", "\n")
            pages.append((i + 1, text))
    finally:
        doc.close()
    return pages


def _page_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if not line or SKIP_LINE.match(line):
            continue
        if not re.search(r"[A-Za-z]{3,}", line):
            continue
        lines.append(line)
    return lines


def _split_topic_blocks(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts = TOPIC_SPLIT.split(text)
    return [p.strip() for p in parts if p and len(p.strip()) >= 30]


def _expand_bullets(block: str) -> list[tuple[str, str]]:
    """
    Expand a topic block into atomic clauses.
    Keep topic intro as one clause; numbered lines become separate clauses.
    """
    # Prefer splitting on '. ' boundaries for protective feature sentences
    # that were flattened without numbers in some PDFs.
    labeled: list[tuple[str, str]] = []

    # If block has explicit numbered/lettered lines restored somehow
    sentences = re.split(r"(?<=\.)\s+(?=[A-Z])", block)
    # Also split known repeated protection phrases
    if re.match(r"Protective Features", block, re.I):
        body = re.sub(r"^Protective Features:\s*", "", block, flags=re.I)
        bits = re.split(
            r"(?=(?:Individual motor|Protection against|Protect VFD|Automatically reset|"
            r"Status lights|Controller capable|Input line reactors))",
            body,
        )
        intro = "Protective Features"
        for i, bit in enumerate(bits):
            bit = bit.strip(" :")
            if len(bit) < 25:
                continue
            labeled.append((f"J.{i+1}" if i else "J", f"{intro}: {bit}" if i == 0 else bit))
        return labeled or [("", block)]

    if re.match(r"Interface Features", block, re.I):
        body = re.sub(r"^Interface Features:\s*", "", block, flags=re.I)
        bits = re.split(
            r"(?=(?:Door mounted|Manual speed|Local/Remote|Power/On light|Digital meter|"
            r"A set of form-C|A 0 to 10|VFD to have terminal|VFD to safety))",
            body,
        )
        for i, bit in enumerate(bits):
            bit = bit.strip(" :")
            if len(bit) < 20:
                continue
            labeled.append((f"K.{i+1}" if i else "K", bit if i else f"Interface Features: {bit}"))
        return labeled or [("", block)]

    if re.match(r"Adjustments", block, re.I):
        body = re.sub(r"^Adjustments:\s*", "", block, flags=re.I)
        bits = re.split(
            r"(?=(?:Maximum speed|Acceleration time|Deceleration time|Current limit|"
            r"Overload trip|Offset and gain))",
            body,
        )
        for i, bit in enumerate(bits):
            bit = bit.strip(" :")
            if len(bit) < 15:
                continue
            labeled.append((f"L.{i+1}" if i else "L", bit if i else f"Adjustments: {bit}"))
        return labeled or [("", block)]

    if re.match(r"Service Conditions", block, re.I):
        body = re.sub(r"^Service Conditions:\s*", "", block, flags=re.I)
        bits = re.split(
            r"(?=(?:Ambient temperature|0 to 95 percent|Elevation to|AC line voltage))",
            body,
        )
        for i, bit in enumerate(bits):
            bit = bit.strip(" :")
            if len(bit) < 15:
                continue
            labeled.append((f"M.{i+1}" if i else "M", bit if i else f"Service Conditions: {bit}"))
        return labeled or [("", block)]

    # Default: one clause per topic block; try lettered/numbered prefix
    m = LETTERED_ITEM.match(block) or NUMBERED_ITEM.match(block)
    if m:
        return [(m.group("label"), m.group("body"))]
    return [("", block)]


def extract_vfd_clauses(path: str | Path) -> list[Clause]:
    pages = extract_pdf_text(path)
    clauses: list[Clause] = []
    seen: set[str] = set()
    counter = 0
    in_section = False
    section_name = "VARIABLE FREQUENCY DRIVE"
    section_buf: list[tuple[int, str]] = []  # (page, line)

    def add(label: str, requirement: str, page_no: int, section: str) -> None:
        nonlocal counter
        requirement = re.sub(r"\s+", " ", requirement).strip()
        requirement = re.sub(
            r"^(?:VARIABLE\s+FREQUENCY\s+DRIVE\s*)+",
            "",
            requirement,
            flags=re.I,
        ).strip()
        if len(requirement) < 40:
            return
        # Drop orphan mid-sentence fragments created by bad splits
        if re.match(r"^the VFD\b", requirement) and not re.search(
            r"shall|enclosure shall|will start|will run", requirement, re.I
        ):
            return
        if NOISE.search(requirement) and not STRONG_VFD.search(requirement):
            return
        # Must look like a VFD requirement
        if not STRONG_VFD.search(requirement) and section != section_name:
            return
        if section == section_name and not (
            STRONG_VFD.search(requirement)
            or re.search(
                r"shall|protect|ambient|adjust|bypass|submittal|test|voltage|frequency|listed",
                requirement,
                re.I,
            )
        ):
            return

        key = requirement[:180].lower()
        if key in seen:
            return
        seen.add(key)
        counter += 1
        clause_id = label if label else f"VFD-{counter:03d}"
        if any(c.clause_id == clause_id for c in clauses):
            clause_id = f"{clause_id}-{counter}"
        clauses.append(
            Clause(
                clause_id=str(clause_id),
                requirement=requirement,
                source_page=page_no,
                section=section,
            )
        )

    def flush_section() -> None:
        nonlocal section_buf
        if not section_buf:
            return
        # Build continuous text but remember first page of each segment
        text = " ".join(line for _, line in section_buf)
        # Approximate page: use page of first line that contains a snippet
        for block in _split_topic_blocks(text):
            page_no = section_buf[0][0]
            for pno, line in section_buf:
                if line[:40].lower() in block.lower() or block[:40].lower() in line.lower():
                    page_no = pno
                    break
            for label, body in _expand_bullets(block):
                add(label, body, page_no, section_name)
        section_buf = []

    for page_no, text in pages:
        lines = _page_lines(text)
        if not lines:
            continue
        joined = "\n".join(lines)

        # Enter VFD section
        if SECTION_ENTER.search(joined):
            # If already buffering something else, flush first
            if in_section and section_buf:
                flush_section()
            in_section = True
            m = SECTION_ENTER.search(joined)
            section_name = re.sub(r"\s+", " ", m.group(0)).strip()[:80] if m else section_name
            started = False
            for line in lines:
                if not started:
                    if SECTION_ENTER.search(line):
                        started = True
                    continue  # skip header line itself
                if SECTION_LEAVE.search(line):
                    in_section = False
                    break
                section_buf.append((page_no, line))
            if not in_section:
                flush_section()
            continue

        if in_section:
            for line in lines:
                if SECTION_LEAVE.search(line):
                    in_section = False
                    break
                section_buf.append((page_no, line))
            if not in_section:
                flush_section()
            continue

        # Outside section — hydronic / scattered strong VFD requirements
        capture_lines: list[str] = []
        capturing = False
        for line in lines:
            if re.search(
                r"Provide\s+Active\s+front\s+end|Variable\s+Speed\s+Pumps|"
                r"All\s+VFDs?\s+are\s+to\s+be|All\s+VFDs?\s+to\s+include|"
                r"Protection functions of VFD",
                line,
                re.I,
            ):
                capturing = True
            if capturing:
                if re.match(r"^[A-Z]\.\s+[A-Z].{20,}", line) and not STRONG_VFD.search(line):
                    break
                if re.match(r"^C\.\s+Double\s+Suction", line, re.I):
                    break
                capture_lines.append(line)

        if capture_lines:
            block_text = " ".join(capture_lines)
            for block in _split_topic_blocks(block_text):
                if not STRONG_VFD.search(block):
                    continue
                for label, body in _expand_bullets(block):
                    add(label, body, page_no, "Hydronic Pumps — Variable Speed / AFE")

    if section_buf:
        flush_section()

    clauses.sort(key=lambda c: (c.source_page, c.clause_id))
    return clauses


def clauses_to_dicts(clauses: list[Clause]) -> list[dict[str, Any]]:
    return [c.to_dict() for c in clauses]
