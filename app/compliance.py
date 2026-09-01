"""Draft Yes/No/N/A compliance answers with short why-remarks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.evidence import search_datasheets, search_manuals, snippet_is_related
from app.gold_reference import find_best_gold_match
from app.llm import llm_enabled, polish_row
from app.pdf_extract import Clause


ROOT = Path(__file__).resolve().parent.parent
PATTERNS_PATH = ROOT / "data" / "patterns" / "drive_capabilities.json"
SOURCES_PATH = ROOT / "data" / "patterns" / "schneider_sources.json"
CATALOG_NAME = "Catalog Altivar Process ATV600 variable speed drives"

RULES: list[tuple[list[str], str]] = [
    (["iso 14001", "environment certification", "iso14001"], "iso14001"),
    (["approved vendor", "approved by engineer", "manufacturers", "manufacturer shall"], "manufacturer_approved"),
    (["local support", "regional office", "24 hours", "spare parts", "commissioning", "start-up service", "training shall"], "local_support_egypt"),
    (["active front end", "afe", "thdi", "thd<", "thd <", "low harmonic", "low-harmonic", "ieee 519", "harmonic distortion", "harmonic spectrum"], "afe_low_harmonic"),
    (["ieee 519"], "ieee519"),
    (["ip 54", "ip54", "ip 55", "ip55", "ip54/55", "ip54 / 55"], "ip54_enclosure"),
    (["ip 21", "ip21"], "ip21_enclosure"),
    (["emc", "rfi", "fcc", "61800-3", "radiated"], "emc_rfi"),
    (["pulse width", "pwm", "v/hz", "v/f", "flux vector", "direct torque", "squirrel cage", "stepless motor speed", "converter section", "inverter section"], "pwm_vhz"),
    (["protection", "over current", "overcurrent", "short circuit", "phase loss", "under-voltage", "undervoltage", "over-voltage", "overvoltage", "over-temperature", "stall", "ground fault", "protective features"], "protections"),
    (["modbus", "ethernet", "bms", "scada", "communication protocol", "plc"], "comms"),
    (["pid"], "pid"),
    (["skip speed", "critical speed", "skip frequenc"], "skip_frequencies"),
    (["ride-through", "ride through", "power loss"], "power_ride_through"),
    (["98 percent", "98%", "efficiency"], "efficiency_98"),
    (["ambient temperature", "50 deg", "50 degrees", "45 deg"], "ambient_50c"),
    (["altitude", "1000m", "1,000", "3300 feet", "3,300"], "altitude_1000m"),
    (["overload", "110 %", "110%", "normal duty", "light load duty"], "overload_vt"),
    (["0 to 500", "500 hz", "output frequency"], "output_freq_500"),
    (["hand/off", "hand / off", "hoa", "local/remote", "local / remote", "manual speed", "selector switch"], "hoa_local_remote"),
    (["form-c", "form c", "dry contacts", "run mode", "fault mode"], "dry_contacts"),
    (["0 to 10", "0-10", "analog", "follower signal"], "analog_io"),
    (["by-pass", "bypass", "drive/off/line"], "bypass"),
    (["line reactor", "input line", "dc choke", "ac choke", "input line filter"], "line_reactors"),
    (["sinus filter", "output choke", "motor output", "output line reactor"], "output_filter"),
    (["ul listed", "ul 508", "ansi/ul", "etl listed", "listed by ul"], "ul_etl"),
    (["ce marked", "ce mark", "iec standards", "iec en"], "ce_iec"),
    (["space heater"], "space_heater"),
    (["380", "400v", "400 v", "input voltage", "±10", "+/-10", "voltage tolerance", "voltage unbalance"], "input_voltage_380_400"),
    (["50hz", "50 hz", "rated input frequency"], "freq_50hz"),
    (["power factor", "0.97"], "power_factor"),
    (["cable type", "cable length", "between the vsd and motor", "motor cable"], "cable_length"),
    (["automatic restart", "auto restart", "attempt an automatic"], "auto_restart"),
    (["flux optimization"], "flux_optimization"),
    (["audible noise", "switching frequency", "2 khz", "db(a)"], "noise_switching"),
    (["factory", "tested by the manufacturer", "dynamometer", "quality assurance"], "factory_test"),
    (["current limit", "current limitation"], "current_limit"),
    (["safety shutdown", "safe torque off", "safety function sto"], "safety_shutdown"),
    (["submittal", "product data", "dimensional drawings", "catalogue", "catalog information"], "submittals"),
    (["relative humidity", "humidity"], "ambient_50c"),
    (["recyclable", "non- toxic", "flame retardant", "materials used"], "iso14001"),
]

# Schneider citations live in data/patterns/schneider_sources.json (gold-curated).
# Consultant PDF page stays in source_page separately.


def load_patterns() -> dict[str, Any]:
    with open(PATTERNS_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_schneider_sources() -> dict[str, Any]:
    if not SOURCES_PATH.exists():
        return {}
    with open(SOURCES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not str(k).startswith("_") and isinstance(v, dict)}


def resolve_pattern_key(offer_text: str) -> str:
    """Map free-text offered family to a capability pattern key."""
    text = (offer_text or "").upper()
    if "ATV680" in text:
        return "ATV680"
    if "ATV660" in text:
        return "ATV660"
    if "ATV650" in text:
        return "ATV650"
    if "ATV630" in text:
        return "ATV630"
    if "ATV600" in text:
        return "ATV600"
    return "ATV630"


def required_enclosure_ip(requirement: str) -> str | None:
    """Consultant-required IP from the clause text (compliance rule, not a UI choice)."""
    t = (requirement or "").lower()
    if re.search(r"\bip[\s\-]?55\b", t) or re.search(r"ingress\s+protection\s*[:\s]*55", t):
        return "55"
    if re.search(r"\bip[\s\-]?54\b", t) or re.search(r"ingress\s+protection\s*[:\s]*54", t):
        return "54"
    if re.search(r"\bip[\s\-]?21\b", t) or re.search(r"ingress\s+protection\s*[:\s]*21", t):
        return "21"
    return None


def offered_drive_facts(
    offer_text: str,
    attached_parts: list[dict[str, Any]] | None,
) -> dict[str, str]:
    """Family + IP of the offered / attached drive (not filters)."""
    bits = [offer_text or ""]
    ip_notes: list[str] = []
    families: list[str] = []
    for item in attached_parts or []:
        pn = str(item.get("part_number") or "").upper()
        if pn.startswith("VW3A"):
            continue
        bits.append(pn)
        bits.append(str(item.get("family") or ""))
        if item.get("ip_note"):
            ip_notes.append(str(item.get("ip_note")))
        if item.get("family"):
            families.append(str(item.get("family")).upper())
    blob = " ".join(bits).upper()
    family = ""
    if "ATV650" in blob:
        family = "ATV650"
    elif "ATV680" in blob:
        family = "ATV680"
    elif "ATV660" in blob:
        family = "ATV660"
    elif "ATV630" in blob:
        family = "ATV630"
    elif families:
        family = families[0]
    ip = ""
    note = " ".join(ip_notes).upper()
    if "55" in note:
        ip = "55"
    elif "54" in note:
        ip = "54"
    elif "21" in note:
        ip = "21"
    elif family == "ATV650":
        ip = "55"
    elif family in ("ATV630", "ATV600"):
        ip = "21"
    return {"family": family, "ip": ip}


def apply_ip_hard_rule(
    requirement: str,
    offer_text: str,
    attached_parts: list[dict[str, Any]] | None,
) -> tuple[str, str] | None:
    """
    IP54/55 requires ATV650. IP21 ATV630 against IP54/55 → No with reason.
    Returns (status, remark) or None to keep normal answering.
    """
    needed = required_enclosure_ip(requirement)
    if not needed:
        return None
    facts = offered_drive_facts(offer_text, attached_parts)
    fam = facts["family"]
    offered_ip = facts["ip"]
    if needed in ("54", "55"):
        if fam == "ATV650" or offered_ip in ("54", "55"):
            return (
                "Yes",
                f"Offered {fam or 'drive'} is IP{offered_ip or needed} (ATV650 Process enclosure). "
                f"Meets consultant IP{needed}.",
            )
        return (
            "No",
            f"Does not comply: consultant requires IP{needed} (ATV650 / IP54–IP55 enclosure). "
            f"Offered {fam or 'drive'} is IP{offered_ip or '21'} (ATV630 path). "
            "Select ATV650 for this IP rating.",
        )
    if needed == "21":
        if fam == "ATV650" or offered_ip in ("54", "55"):
            return (
                "Yes",
                f"Offered {fam or 'drive'} IP{offered_ip or '55'} exceeds IP21.",
            )
        return (
            "Yes",
            f"Offered {fam or 'ATV630'} is IP21 wall/floor Process drive as specified.",
        )
    return None


def shorten_remark(text: str, max_len: int = 180) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"


def _map_cap_status(raw: str) -> str:
    raw = (raw or "").lower()
    if raw == "yes":
        return "Yes"
    if raw == "no":
        return "No"
    if raw == "partial":
        return "Yes"
    if raw in ("na", "n/a"):
        return "N/A"
    return "Yes"


NO_EVIDENCE_NOTE = "Note: No related evidence found in Product Datasheet or manuals."

EXTRA_CAPS: dict[str, dict[str, str]] = {
    "current_limit": {
        "status": "yes",
        "remark": "Current limitation (CLI) is a programming-manual function of Altivar Process, independent of enclosure IP.",
    },
    "safety_shutdown": {
        "status": "yes",
        "remark": "Safety shutdown / Safe Torque Off is a drive safety function, not related to Low or Standard harmonics topology.",
    },
}


def _text_has_kw(text: str, kw: str) -> bool:
    kw = (kw or "").lower()
    if not kw:
        return False
    if re.search(r"[^a-z0-9]", kw):
        return kw in text
    if len(kw) <= 3:
        return bool(re.search(rf"\b{re.escape(kw)}\b", text))
    return kw in text


def match_capability(requirement: str) -> str | None:
    keys = match_all_capabilities(requirement)
    return keys[0] if keys else None


def match_all_capabilities(requirement: str) -> list[str]:
    text = (requirement or "").lower()
    if re.search(r"\bnfpa\b", text):
        skip = {"ul_etl", "ce_iec"}
    else:
        skip = set()
    if re.search(r"safety\s+shutdown|safe torque off", text):
        skip.update({"afe_low_harmonic", "ieee519"})
    found: list[str] = []
    seen: set[str] = set()
    for keywords, cap_key in RULES:
        if cap_key in skip or cap_key in seen:
            continue
        if any(_text_has_kw(text, kw) for kw in keywords):
            found.append(cap_key)
            seen.add(cap_key)
    return found


def apply_motor_margin_rule(requirement: str) -> tuple[str, str] | None:
    """VFD sized vs motor kW — no motor rating input → always No."""
    t = (requirement or "").lower()
    if re.search(
        r"sized bigger than motor|bigger than motor rating|motor rating at 100%|"
        r"suitable marigin|suitable margin that allows for smooth",
        t,
    ):
        return (
            "No",
            "Does not comply: motor rating / sizing margin is not an input to this workbench, "
            "so oversize versus motor kW cannot be verified.",
        )
    return None


def requires_low_harmonics(requirement: str) -> bool:
    t = (requirement or "").lower()
    return bool(
        re.search(
            r"thdi?\s*[<≤]|thd\s*<|less than\s*5|low[\s\-]?harmonic|active front end|\bafe\b",
            t,
        )
    )


def offer_has_low_harmonics(
    offer_text: str,
    attached_parts: list[dict[str, Any]] | None,
) -> bool:
    blob = (offer_text or "").upper()
    if any(x in blob for x in ("ATV680", "ATV660", "AFE", "LOW HARMONIC")):
        return True
    for item in attached_parts or []:
        pn = str(item.get("part_number") or "").upper()
        if pn.startswith("VW3A") or "ATV680" in pn:
            return True
        fam = str(item.get("family") or "").upper()
        if "680" in fam or "660" in fam:
            return True
    return False


def _harmonics_hard_rule(
    requirement: str,
    offer_text: str,
    attached_parts: list[dict[str, Any]] | None,
) -> tuple[str, str] | None:
    if not requires_low_harmonics(requirement):
        return None
    if offer_has_low_harmonics(offer_text, attached_parts):
        return (
            "Yes",
            "Offer includes a low-harmonics path (ATV680 / AFE / 5% filter) for THDi <5%.",
        )
    return (
        "No",
        "Does not comply for harmonics: consultant requires low harmonics / THDi <5%; "
        "offer is standard ATV630 without AFE or 5% filter.",
    )


def _cap_entry(caps: dict[str, Any], cap_key: str) -> dict[str, Any] | None:
    if cap_key in caps:
        return caps[cap_key]
    return EXTRA_CAPS.get(cap_key)


def _auto_restart_remark(requirement: str, base: str) -> str:
    t = (requirement or "").lower()
    ov = bool(re.search(r"over[\s\-]?volt", t))
    uv = bool(re.search(r"under[\s\-]?volt", t))
    if ov and not uv:
        return (
            "Automatic restart applies after an overvoltage trip (programmable auto-reset), "
            "not a generic catch-all fault reset."
        )
    if uv and not ov:
        return (
            "Automatic restart applies after an undervoltage / mains-loss trip "
            "(programmable auto-reset), not a generic catch-all fault reset."
        )
    if ov and uv:
        return (
            "Automatic restart is programmable after overvoltage and/or undervoltage shutdown; "
            "set the fault type in the programming manual."
        )
    return base


def _clean_remark(text: str) -> str:
    t = re.sub(r"(?i)\bstill learn(?:ing)?(?: remarks)?\.?\s*", "", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _gold_usable(gold: dict[str, Any], requirement: str, cap_key: str | None) -> bool:
    """Reject gold rows whose Why is a different topic (e.g. UL text on a converter clause)."""
    rem = _clean_remark(str(gold.get("remarks") or ""))
    if not rem:
        return True
    low = rem.lower()
    if cap_key not in ("ul_etl", "ce_iec") and (
        "ul type 1" in low or "ul certification" in low or "ce variants" in low
    ):
        return False
    gcap = str(gold.get("capability_key") or "")
    if gcap and cap_key and gcap != cap_key:
        return False
    return snippet_is_related(rem, requirement, cap_key) or len(rem) < 40


def _related_evidence(evidence: dict[str, Any] | None, requirement: str, cap_key: str | None) -> dict[str, Any] | None:
    if not evidence:
        return None
    snip = str(evidence.get("snippet") or "")
    if evidence.get("related") and snippet_is_related(snip, requirement, cap_key):
        return evidence
    if snippet_is_related(snip, requirement, cap_key):
        evidence = dict(evidence)
        evidence["related"] = True
        return evidence
    return None


def _format_schneider_source(
    cap_key: str | None,
    *,
    selected_catalog_page: str | None = None,
) -> str:
    """User guide / Instruction sheet / Catalog + verified page (never fake section-as-page)."""
    sources = load_schneider_sources()
    meta = sources.get(cap_key or "") if cap_key else None
    if not meta:
        if selected_catalog_page:
            return f"Catalog | {CATALOG_NAME} | selected product p.{selected_catalog_page}"
        return f"Catalog | {CATALOG_NAME} | page not verified"
    doc_type = meta.get("doc_type") or "Catalog"
    document = meta.get("document") or CATALOG_NAME
    page = meta.get("page")
    section = meta.get("section")

    if meta.get("use_selected_catalog_page") and selected_catalog_page:
        page = str(selected_catalog_page)

    parts = [str(doc_type), str(document)]
    if page not in (None, "", "null"):
        parts.append(f"p.{page}")
    elif section:
        parts.append(f"section: {section}")
    else:
        parts.append("page not verified")
    return " | ".join(parts)


def _selected_catalog_page(attached: list[dict[str, Any]] | None) -> str | None:
    """Catalog rating page from sized drive — only for use_selected_catalog_page caps."""
    if not attached:
        return None
    for item in attached:
        pn = str(item.get("part_number") or "").upper()
        if pn.startswith("VW3A"):
            continue  # filter accessory — not a drive rating page
        page = item.get("source_page") or item.get("catalog_page")
        if page:
            return str(page)
    return None


def _product_datasheet_source(attached_parts: list[dict[str, Any]] | None) -> str:
    """Primary Schneider evidence: combined / attached Product Datasheet names."""
    if not attached_parts:
        return ""
    ds_bits: list[str] = []
    for item in attached_parts[:4]:
        pn = str(item.get("part_number") or "")
        docs = item.get("documents") or []
        if docs:
            title = docs[0].get("file_name") or docs[0].get("title") or "Product Datasheet"
            ds_bits.append(f"{pn}: {title}" if pn else str(title))
        elif pn and not pn.upper().startswith("VW3A"):
            ds_bits.append(pn)
    if not ds_bits:
        return ""
    return "Product Datasheet | " + " | ".join(ds_bits)


def _source_document(evidence: dict[str, Any] | None) -> str:
    """Cite only a related PDF hit. Gold and filler datasheet lists are never Source."""
    if evidence and evidence.get("related") and evidence.get("citation"):
        return str(evidence["citation"])
    return ""


def _apply_related_snippet(remark: str, evidence: dict[str, Any] | None) -> str:
    if not evidence or not evidence.get("related"):
        return remark
    snip = _clean_remark(str(evidence.get("snippet") or ""))
    if not snip:
        return remark
    return shorten_remark(f"{remark} Confirmed: {snip}", 220)


def draft_clause(
    clause: Clause | dict[str, Any],
    offer_text: str,
    patterns: dict[str, Any] | None = None,
    attached_parts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    patterns = patterns or load_patterns()
    pattern_key = resolve_pattern_key(offer_text)
    display_family = (offer_text or pattern_key).strip() or pattern_key
    caps_key = pattern_key if pattern_key in patterns else "ATV630"
    if pattern_key == "ATV650" and "ATV650" not in patterns:
        caps_key = "ATV630"
    if pattern_key == "ATV660" and "ATV660" not in patterns:
        caps_key = "ATV630"
    caps = patterns[caps_key]["capabilities"]
    family_name = patterns[caps_key]["family_name"]
    if pattern_key == "ATV650":
        family_name = "Altivar Process ATV650 (IP54/IP55)"
    if pattern_key == "ATV660":
        family_name = "Altivar Process ATV660 (Compact Drive System)"

    if isinstance(clause, Clause):
        data = clause.to_dict()
    else:
        data = dict(clause)

    requirement = data.get("requirement", "")
    cap_keys = match_all_capabilities(requirement)
    cap_key = cap_keys[0] if cap_keys else None

    answer_origin = "rules"
    gold_match: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    status = ""
    remark = ""

    motor = apply_motor_margin_rule(requirement)
    ip_part = apply_ip_hard_rule(requirement, offer_text, attached_parts)
    harm_part = _harmonics_hard_rule(requirement, offer_text, attached_parts)

    if motor:
        status, remark = motor
        answer_origin = "motor_margin_rule"
    elif ip_part or harm_part:
        bits: list[str] = []
        nos = False
        if ip_part:
            bits.append(ip_part[1])
            if ip_part[0] == "No":
                nos = True
        if harm_part:
            bits.append(harm_part[1])
            if harm_part[0] == "No":
                nos = True
        status = "No" if nos else "Yes"
        remark = shorten_remark(" ".join(bits), 220)
        answer_origin = "ip_rule" if ip_part else "harmonics_rule"
        if ip_part and harm_part:
            answer_origin = "ip_harmonics_rule"
    else:
        ds = _related_evidence(
            search_datasheets(
                requirement, cap_key=cap_key, attached_parts=attached_parts
            ),
            requirement,
            cap_key,
        )
        gold, gold_score = find_best_gold_match(
            requirement,
            capability_key=cap_key,
            family=pattern_key,
        )
        if gold and not _gold_usable(gold, requirement, cap_key):
            gold = None
        manuals = None
        if ds:
            evidence = ds
            entry = _cap_entry(caps, cap_key or "")
            if entry:
                status = _map_cap_status(entry.get("status", "yes"))
                remark = shorten_remark(entry.get("remark", ""))
            else:
                status, remark = _fallback_answer(requirement, family_name)
                remark = shorten_remark(remark)
            if cap_key == "auto_restart":
                remark = _auto_restart_remark(requirement, remark)
            if re.search(r"converter section|inverter section", (requirement or "").lower()):
                remark = (
                    "The offered Altivar Process drive includes a converter section (AC to DC) "
                    "and an inverter section (DC to variable-frequency AC)."
                )
            remark = _apply_related_snippet(remark, evidence)
            answer_origin = "evidence_datasheet"
        elif gold and (gold.get("status") or gold.get("remarks")):
            status = _map_cap_status(gold.get("status") or "yes")
            remark = _clean_remark(str(gold.get("remarks") or ""))
            remark = shorten_remark(remark, 220)
            if not remark:
                entry = _cap_entry(caps, cap_key or "")
                remark = shorten_remark((entry or {}).get("remark", "") or "")
                if not remark:
                    _, remark = _fallback_answer(requirement, family_name)
                    remark = shorten_remark(remark)
            answer_origin = "reference"
            gold_match = {
                "score": round(gold_score, 3),
                "source_file": gold.get("source_file") or "",
                "requirement_snippet": str(gold.get("requirement") or "")[:160],
            }
        else:
            manuals = _related_evidence(
                search_manuals(requirement, cap_key=cap_key),
                requirement,
                cap_key,
            )
            if manuals:
                evidence = manuals
                entry = _cap_entry(caps, cap_key or "")
                if entry:
                    status = _map_cap_status(entry.get("status", "yes"))
                    remark = shorten_remark(entry.get("remark", ""))
                else:
                    status, remark = _fallback_answer(requirement, family_name)
                    remark = shorten_remark(remark)
                if cap_key == "auto_restart":
                    remark = _auto_restart_remark(requirement, remark)
                if re.search(r"converter section|inverter section", (requirement or "").lower()):
                    remark = (
                        "The offered Altivar Process drive includes a converter section (AC to DC) "
                        "and an inverter section (DC to variable-frequency AC)."
                    )
                remark = _apply_related_snippet(remark, evidence)
                answer_origin = f"evidence_{evidence.get('kind') or 'pdf'}"
            else:
                entry = _cap_entry(caps, cap_key or "")
                if entry:
                    status = _map_cap_status(entry.get("status", "yes"))
                    remark = shorten_remark(entry.get("remark", ""))
                    if cap_key == "auto_restart":
                        remark = _auto_restart_remark(requirement, remark)
                    if pattern_key == "ATV650" and cap_key == "ip54_enclosure":
                        status = "Yes"
                        remark = shorten_remark(
                            "ATV650 wall/floor Process enclosure is IP54/IP55 as catalogued (not IP21 ATV630)."
                        )
                    if pattern_key in ("ATV630", "ATV600") and cap_key == "ip54_enclosure":
                        status = "No"
                        remark = shorten_remark(
                            "Does not comply: IP54/IP55 requires ATV650. Offered ATV630 is IP21."
                        )
                    answer_origin = "rules"
                else:
                    status, remark = _fallback_answer(requirement, family_name)
                    remark = shorten_remark(remark)
                    answer_origin = "fallback"
                remark = NO_EVIDENCE_NOTE
                evidence = None

    remark = _clean_remark(remark)
    if answer_origin in ("rules", "fallback") and not evidence:
        remark = NO_EVIDENCE_NOTE
    if answer_origin == "reference":
        source_doc = ""
    elif answer_origin in ("motor_margin_rule", "ip_rule", "harmonics_rule", "ip_harmonics_rule"):
        source_doc = ""
    else:
        source_doc = _source_document(evidence)

    return {
        "clause_id": data.get("clause_id", ""),
        "requirement": requirement,
        "status": status,
        "remarks": remark,
        "source_page": data.get("source_page", ""),
        "source_document": source_doc,
        "section": data.get("section", ""),
        "drive_family": display_family,
        "pattern_key": pattern_key,
        "capability_key": cap_key or "",
        "answer_origin": answer_origin,
        "gold_match": gold_match,
        "evidence": {
            "kind": evidence.get("kind"),
            "page": evidence.get("page"),
            "citation": evidence.get("citation"),
        }
        if evidence
        else None,
    }


def _fallback_answer(requirement: str, family_name: str) -> tuple[str, str]:
    text = requirement.lower()

    if re.search(r"\bnot applicable\b|\bn/?a\b", text):
        return "N/A", "Not applicable to the offered VFD scope."

    if re.search(r"witness|airfare|hotel accommodation|business class", text) and not re.search(
        r"\bvfd\b|\bdrive\b", text
    ):
        return "N/A", "Pump witness/travel scope — outside VFD datasheet offer unless packaged."

    if re.search(r"shall|must|provide|furnish|include|be able|equipped", text):
        return "Yes", f"Complies with offered {family_name} per Schneider catalog / user guide."

    return "Yes", f"Covered by offered {family_name} technical documentation."


def draft_compliance(
    clauses: list[Clause] | list[dict[str, Any]],
    offer_text: str,
    attached_parts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    patterns = load_patterns()
    rows = [
        draft_clause(c, offer_text, patterns, attached_parts=attached_parts) for c in clauses
    ]
    if not llm_enabled():
        return rows
    for row in rows:
        why = str(row.get("remarks") or "")
        if NO_EVIDENCE_NOTE.lower() in why.lower():
            continue
        polish_row(row)
        row["remarks"] = shorten_remark(str(row.get("remarks") or ""), 220)
    return rows


def available_families() -> list[str]:
    return list(load_patterns().keys())
