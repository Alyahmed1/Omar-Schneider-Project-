"""Schneider Electric public product lookup by commercial reference."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote, unquote

import httpx
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

COUNTRY_BASES = [
    ("eg", "https://www.se.com/eg/en"),
    ("uk", "https://www.se.com/uk/en"),
    ("ww", "https://www.se.com/ww/en"),
]


def normalize_part_numbers(raw: str) -> list[str]:
    if not raw:
        return []
    text = raw.replace(";", "\n").replace(",", "\n").replace("\t", "\n")
    parts: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        token = line.strip().upper()
        token = re.sub(r"^[\-\*\u2022]+\s*", "", token)
        token = re.sub(r"[^A-Z0-9\-_/\.]", "", token)
        if len(token) < 4 or token in seen:
            continue
        seen.add(token)
        parts.append(token)
    return parts


def _product_url(base: str, part: str) -> str:
    return f"{base}/product/{quote(part)}/"


def _search_url(base: str, part: str) -> str:
    return f"{base}/search/{quote(part)}"


def _title_from_slug(url: str, part: str) -> str:
    """Build a readable title from /product/PART/slug/ URLs."""
    m = re.search(rf"/product/{re.escape(part)}/([^/?#]+)/?", url, re.I)
    if not m:
        return part
    slug = unquote(m.group(1)).replace("-", " ").strip()
    return slug[:1].upper() + slug[1:] if slug else part


def _extract_meta(soup: BeautifulSoup) -> dict[str, str]:
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip() or title
    desc = ""
    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        desc = og_desc["content"].strip()
    if not desc:
        md = soup.find("meta", attrs={"name": "description"})
        if md and md.get("content"):
            desc = md["content"].strip()
    h1 = ""
    h1_tag = soup.find("h1")
    if h1_tag:
        h1 = h1_tag.get_text(" ", strip=True)
    return {"title": title, "description": desc, "h1": h1}


def _is_product_datasheet(doc: dict[str, str]) -> bool:
    t = f"{doc.get('doc_type','')} {doc.get('title','')} {doc.get('file_name','')}".lower()
    return (
        "product datasheet" in t
        or "product data sheet" in t
        or (t.strip().startswith("datasheet") and "environmental" not in t)
        or "/product/download-pdf/" in (doc.get("url") or "").lower()
    )


def filter_product_datasheets(docs: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep only Schneider Product Datasheet entries."""
    out = [d for d in docs if _is_product_datasheet(d)]
    # Deduplicate by URL
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for d in out:
        key = (d.get("url") or d.get("file_name") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(d)
    return unique


def product_datasheet_url(base: str, part: str, filename: str | None = None) -> str:
    """Construct the Schneider Product Datasheet PDF endpoint."""
    part = part.strip().upper()
    url = f"{base.rstrip('/')}/product/download-pdf/{quote(part)}"
    if filename:
        url += f"?filename={quote(filename)}"
    return url


def _extract_documents(html: str, part: str = "", product_url: str = "") -> list[dict[str, str]]:
    """
    Extract Schneider documents, always including Product Datasheet via
    /product/download-pdf/{PART} (client-rendered Main documents entry).
    """
    from urllib.parse import parse_qs, urlparse, unquote as uq

    docs: list[dict[str, str]] = []
    seen: set[str] = set()

    cleaned = (
        html.replace("&amp;", "&")
        .replace("\\u0026", "&")
        .replace("\\/", "/")
    )

    # 1) Explicit Product Datasheet from hydrated DOM pattern if present in HTML
    for m in re.finditer(
        rf"https?://www\.se\.com/[^\"'\s<>]*/product/download-pdf/{re.escape(part)}[^\"'\s<>]*",
        cleaned,
        re.I,
    ):
        url = m.group(0).rstrip(").,;]")
        qs = parse_qs(urlparse(url).query)
        file_name = uq((qs.get("filename") or [f"{part}_Product_Datasheet.pdf"])[0])
        if not file_name.lower().endswith(".pdf"):
            file_name = f"{file_name}.pdf"
        key = file_name.lower()
        if key not in seen:
            seen.add(key)
            docs.append(
                {
                    "title": file_name,
                    "doc_type": "Product Datasheet",
                    "url": url,
                    "file_name": file_name,
                }
            )

    # 2) Construct Product Datasheet URL from product page country base
    if part and product_url:
        m = re.match(r"(https://www\.se\.com/[^/]+/en)", product_url)
        base = m.group(1) if m else "https://www.se.com/uk/en"
        # Prefer UK/EG bases that worked for PDF generation
        if "/eg/" in product_url:
            base = "https://www.se.com/eg/en"
        elif "/uk/" in product_url:
            base = "https://www.se.com/uk/en"
        file_name = f"Schneider_Electric_{part}_Product_Datasheet.pdf"
        url = product_datasheet_url(base, part, file_name)
        if file_name.lower() not in seen and not any(
            "/product/download-pdf/" in (d.get("url") or "").lower() for d in docs
        ):
            seen.add(file_name.lower())
            docs.insert(
                0,
                {
                    "title": file_name,
                    "doc_type": "Product Datasheet",
                    "url": url,
                    "file_name": file_name,
                },
            )

    # 3) Other download.schneider-electric.com PDFs (kept for reference listing only;
    #    filtered out later by filter_product_datasheets for ZIP/attach)
    for m in re.finditer(
        r"https?://download\.schneider-electric\.com/files\?[^\s\"'<>]+",
        cleaned,
        re.I,
    ):
        url = m.group(0).rstrip(").,;]")
        if "p_File_Name=" not in url and ".pdf" not in url.lower():
            continue
        if "rendition" in url.lower() or "File_Type=rendition" in url:
            continue

        qs = parse_qs(urlparse(url).query)
        file_name = uq((qs.get("p_File_Name") or [""])[0])
        doc_type = uq((qs.get("p_enDocType") or ["Document"])[0]).replace("+", " ")
        if not file_name:
            file_name = url.split("p_File_Name=")[-1].split("&")[0]
            file_name = uq(file_name.replace("+", " "))
        if not file_name.lower().endswith(".pdf"):
            continue
        key = file_name.lower()
        if key in seen:
            continue
        seen.add(key)
        docs.append(
            {
                "title": file_name,
                "doc_type": doc_type or "Document",
                "url": url,
                "file_name": file_name,
            }
        )

    # Return Product Datasheet first, then others (UI/ZIP will filter)
    datasheets = [d for d in docs if _is_product_datasheet(d)]
    others = [d for d in docs if not _is_product_datasheet(d)]
    return datasheets + others


def _extract_highlights(soup: BeautifulSoup, html: str) -> list[str]:

    highlights: list[str] = []
    for sel in [
        "[class*='feature'] li",
        "[class*='highlight'] li",
        "[class*='benefit'] li",
    ]:
        for li in soup.select(sel):
            text = li.get_text(" ", strip=True)
            if 20 <= len(text) <= 180:
                highlights.append(text)
            if len(highlights) >= 5:
                break
        if highlights:
            break

    # Fallback: pull short marketing sentences near known phrases in HTML
    if not highlights:
        for pat in [
            r"Embedded power measurement[^<.\"]{0,80}",
            r"Embedded process monitoring[^<.\"]{0,80}",
            r"Stop and Go function[^<.\"]{0,80}",
            r"Asset monitoring[^<.\"]{0,80}",
            r"Drift monitoring[^<.\"]{0,40}",
        ]:
            m = re.search(pat, html, re.I)
            if m:
                highlights.append(re.sub(r"\s+", " ", m.group(0)).strip())

    out: list[str] = []
    seen: set[str] = set()
    for h in highlights:
        key = h.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out[:5]


def _is_access_denied(html: str) -> bool:
    low = html[:2000].lower()
    return "access denied" in low or "<title>access denied</title>" in low


def _looks_like_product(final_url: str, html: str, part: str) -> bool:
    if _is_access_denied(html):
        return False
    path = final_url.lower()
    if f"/product/{part.lower()}" in path and "product-country-selector" not in path:
        # Redirect to slug URL is a strong signal even for SPA shells
        if re.search(rf"/product/{re.escape(part.lower())}/[^/]+", path):
            return True
        if part.upper() in html.upper():
            return True
    return False


async def _warm(client: httpx.AsyncClient, base: str) -> None:
    try:
        await client.get(f"{base}/", headers={**HEADERS, "Sec-Fetch-Site": "none"})
    except httpx.HTTPError:
        pass


async def _fetch(
    client: httpx.AsyncClient, url: str, referer: str | None = None
) -> tuple[int, str, str]:
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"
    try:
        resp = await client.get(url, headers=headers, follow_redirects=True)
        return resp.status_code, str(resp.url), resp.text
    except httpx.HTTPError:
        return 0, url, ""


async def lookup_one(part: str, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    part = part.strip().upper()
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(headers=HEADERS, timeout=35.0, follow_redirects=True)

    result: dict[str, Any] = {
        "part_number": part,
        "status": "Not found",
        "country": "",
        "url": "",
        "title": "",
        "description": "",
        "highlights": [],
        "documents": [],
        "datasheet_missing": True,
        "search_url": "",
    }

    try:
        assert client is not None
        for country, base in COUNTRY_BASES:
            result["search_url"] = _search_url(base, part)
            await _warm(client, base)
            product_url = _product_url(base, part)
            code, final_url, html = await _fetch(client, product_url, referer=f"{base}/")

            if code == 200 and _looks_like_product(final_url, html, part):
                soup = BeautifulSoup(html, "lxml")
                meta = _extract_meta(soup)
                title = meta.get("h1") or meta.get("title") or ""
                if not title or title.lower() in {"access denied", "schneider electric"}:
                    title = _title_from_slug(final_url, part)
                # Clean site suffix from titles
                title = re.sub(
                    r"\s*\|\s*Schneider Electric.*$",
                    "",
                    title,
                    flags=re.I,
                ).strip()
                desc = meta.get("description") or ""
                if not desc:
                    desc = f"Schneider Electric commercial reference {part}."
                all_docs = _extract_documents(html, part=part, product_url=final_url)
                result.update(
                    {
                        "status": "Found",
                        "country": country.upper(),
                        "url": final_url,
                        "title": title or part,
                        "description": desc,
                        "highlights": _extract_highlights(soup, html),
                        "documents": filter_product_datasheets(all_docs),
                        "datasheet_missing": not bool(filter_product_datasheets(all_docs)),
                    }
                )
                return result

            # Search fallback
            search_url = _search_url(base, part)
            code, final_url, html = await _fetch(client, search_url, referer=f"{base}/")
            if code == 200 and not _is_access_denied(html):
                if f"/product/{part.lower()}" in final_url.lower() and _looks_like_product(
                    final_url, html, part
                ):
                    soup = BeautifulSoup(html, "lxml")
                    meta = _extract_meta(soup)
                    title = meta.get("h1") or meta.get("title") or _title_from_slug(final_url, part)
                    title = re.sub(r"\s*\|\s*Schneider Electric.*$", "", title, flags=re.I).strip()
                    all_docs = _extract_documents(html, part=part, product_url=final_url)
                    result.update(
                        {
                            "status": "Found",
                            "country": country.upper(),
                            "url": final_url,
                            "title": title or part,
                            "description": meta.get("description")
                            or f"Schneider Electric commercial reference {part}.",
                            "highlights": _extract_highlights(soup, html),
                            "documents": filter_product_datasheets(all_docs),
                            "datasheet_missing": not bool(filter_product_datasheets(all_docs)),
                        }
                    )
                    return result

                soup = BeautifulSoup(html, "lxml")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if f"/product/{part}" in href or f"/product/{part.lower()}" in href.lower():
                        if href.startswith("/"):
                            href = f"https://www.se.com{href}"
                        code2, final2, html2 = await _fetch(client, href, referer=search_url)
                        if code2 == 200 and _looks_like_product(final2, html2, part):
                            soup2 = BeautifulSoup(html2, "lxml")
                            meta = _extract_meta(soup2)
                            title = (
                                meta.get("h1")
                                or meta.get("title")
                                or _title_from_slug(final2, part)
                            )
                            title = re.sub(
                                r"\s*\|\s*Schneider Electric.*$",
                                "",
                                title,
                                flags=re.I,
                            ).strip()
                            all_docs = _extract_documents(html2, part=part, product_url=final2)
                            result.update(
                                {
                                    "status": "Found",
                                    "country": country.upper(),
                                    "url": final2,
                                    "title": title or part,
                                    "description": meta.get("description")
                                    or f"Schneider Electric commercial reference {part}.",
                                    "highlights": _extract_highlights(soup2, html2),
                                    "documents": filter_product_datasheets(all_docs),
                                    "datasheet_missing": not bool(
                                        filter_product_datasheets(all_docs)
                                    ),
                                }
                            )
                            return result
                        break

                # Search pages echo the query string — require a product-like hit
                if re.search(
                    rf"/product/{re.escape(part)}",
                    html,
                    re.I,
                ) or re.search(
                    rf"commercialReference[^A-Z0-9]{{0,20}}{re.escape(part)}",
                    html,
                    re.I,
                ):
                    result.update(
                        {
                            "status": "Ambiguous",
                            "country": country.upper(),
                            "url": search_url,
                            "title": f"Search results for {part}",
                            "description": (
                                "Could not open a single product page — use Search on Schneider."
                            ),
                        }
                    )
                    return result

        # Always provide a best-effort Egypt product URL for manual open
        result["url"] = _product_url(COUNTRY_BASES[0][1], part)
        result["search_url"] = _search_url(COUNTRY_BASES[0][1], part)
        return result
    finally:
        if owns_client and client is not None:
            await client.aclose()


async def lookup_many(parts: list[str], concurrency: int = 2) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(headers=HEADERS, timeout=35.0, follow_redirects=True) as client:

        async def _one(p: str) -> dict[str, Any]:
            async with sem:
                await asyncio.sleep(0.35)
                return await lookup_one(p, client)

        return list(await asyncio.gather(*[_one(p) for p in parts]))


async def download_document_bytes(url: str, client: httpx.AsyncClient | None = None) -> bytes:
    owns = client is None
    if owns:
        client = httpx.AsyncClient(headers=HEADERS, timeout=90.0, follow_redirects=True)
    try:
        assert client is not None
        # Warm matching country homepage + product page for download-pdf endpoints
        if "www.se.com" in url and "/product/download-pdf/" in url:
            m = re.match(r"(https://www\.se\.com/[^/]+/en)", url)
            base = m.group(1) if m else "https://www.se.com/uk/en"
            await _warm(client, base)
            part_m = re.search(r"/product/download-pdf/([^/?#]+)", url)
            if part_m:
                part = part_m.group(1)
                await client.get(
                    f"{base}/product/{part}/",
                    headers={**HEADERS, "Referer": f"{base}/", "Sec-Fetch-Site": "same-origin"},
                )
                referer = f"{base}/product/{part}/"
            else:
                referer = f"{base}/"
            resp = await client.get(
                url,
                headers={
                    **HEADERS,
                    "Referer": referer,
                    "Sec-Fetch-Site": "same-origin",
                    "Accept": "application/pdf,*/*",
                },
            )
        else:
            await _warm(client, COUNTRY_BASES[0][1])
            resp = await client.get(
                url,
                headers={
                    **HEADERS,
                    "Referer": "https://www.se.com/eg/en/",
                    "Sec-Fetch-Site": "cross-site",
                    "Accept": "application/pdf,*/*",
                },
            )
        if resp.status_code != 200 or not resp.content:
            raise RuntimeError(f"Failed to download document ({resp.status_code})")
        if not resp.content.startswith(b"%PDF") and "pdf" not in (resp.headers.get("content-type") or "").lower():
            raise RuntimeError("Download did not return a PDF (blocked or missing Product Datasheet).")
        return resp.content
    finally:
        if owns and client is not None:
            await client.aclose()


def merged_datasheet_filename(parts: list[str]) -> str:
    """Safe download name for a combined Product Datasheet PDF."""
    cleaned = [re.sub(r"[^A-Z0-9\-_]", "", str(p).upper()) for p in parts if p]
    cleaned = [p for p in cleaned if p]
    if not cleaned:
        return "Combined_Product_Datasheets.pdf"
    stem = "_".join(cleaned)
    if len(stem) > 80:
        stem = "_".join(cleaned[:3]) + f"_and_{len(cleaned) - 3}_more"
        if len(stem) > 80:
            stem = stem[:80].rstrip("_")
    return f"{stem}_Product_Datasheets.pdf"


def _build_cover_pdf(included_parts: list[str]) -> bytes:
    """A4 cover listing part numbers included in the merged datasheet."""
    import fitz
    from datetime import datetime, timezone

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    y = 72
    page.insert_text((72, y), "Combined Product Datasheets", fontsize=18, fontname="helv")
    y += 28
    page.insert_text((72, y), "Schneider Electric — Product Datasheet pack", fontsize=11, fontname="helv")
    y += 22
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    page.insert_text((72, y), f"Generated: {stamp}", fontsize=10, fontname="helv")
    y += 28
    page.insert_text(
        (72, y),
        "The following Product Datasheets are included (in order):",
        fontsize=11,
        fontname="helv",
    )
    y += 24
    for i, part in enumerate(included_parts, start=1):
        if y > 780:
            page = doc.new_page(width=595, height=842)
            y = 72
        page.insert_text((84, y), f"{i}. {part}", fontsize=11, fontname="helv")
        y += 18
    y += 16
    if y < 780:
        page.insert_text(
            (72, y),
            "Pages that follow are the official Schneider Product Datasheet PDFs.",
            fontsize=10,
            fontname="helv",
        )
    data = doc.tobytes()
    doc.close()
    return data


async def build_merged_datasheet_pdf(
    items: list[dict[str, Any]],
) -> tuple[bytes, list[str]]:
    """
    Download each part's Product Datasheet and merge into one PDF:
    cover page (part list) + datasheet pages back-to-back.

    Returns (pdf_bytes, included_part_numbers).
    """
    import fitz

    downloaded: list[tuple[str, bytes]] = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=90.0, follow_redirects=True) as client:
        for item in items:
            part = str(item.get("part_number") or "UNKNOWN").upper()
            docs = filter_product_datasheets(item.get("documents") or [])
            if not docs:
                continue
            doc = docs[0]
            url = doc.get("url")
            if not url:
                continue
            try:
                data = await download_document_bytes(url, client)
                downloaded.append((part, data))
            except Exception:
                continue

    if not downloaded:
        raise RuntimeError(
            "No Product Datasheet PDFs could be downloaded. "
            "Open the product on Schneider and verify Product Datasheet is available."
        )

    included = [p for p, _ in downloaded]
    cover_bytes = _build_cover_pdf(included)
    out = fitz.open("pdf", cover_bytes)
    try:
        for part, pdf_bytes in downloaded:
            src = fitz.open("pdf", pdf_bytes)
            try:
                out.insert_pdf(src)
            finally:
                src.close()
        return out.tobytes(), included
    finally:
        out.close()
