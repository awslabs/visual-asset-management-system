# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""PDF (.pdf): page count, document information (with the CreationDate as ISO-8601 createdAt), a text excerpt
and the first two pages rasterised for the vision model via pypdfium2 -> sys_document."""

import re
from datetime import datetime
from typing import Dict, List, Optional

import pypdfium2 as pdfium

from .common import (
    CLASS_DOCUMENT,
    PDF_RASTER_PAGES,
    RENDER_SKIPPED_ERROR,
    VISION_MAX_LONG_EDGE_PX,
    BranchResult,
    ExtractContext,
    human_count,
    truncate_text,
)
from .imaging import normalise_for_vision, write_png

# Pages are rendered at up to 2x their point size and then fitted to the vision bound, so small pages stay
# legible and large ones are not rasterised far beyond what the model receives.
_RASTER_SCALE_MAX = 2.0
_INFO_VALUE_MAX_CHARS = 500
# PDF 1.7 §7.9.4: D:YYYYMMDDHHmmSS with every field after the year optional and a zone of Z or ±HH'mm'.
_PDF_DATE = re.compile(
    r"^D:(?P<year>\d{4})(?P<month>\d{2})?(?P<day>\d{2})?(?P<hour>\d{2})?(?P<minute>\d{2})?(?P<second>\d{2})?"
    r"(?P<zone>Z|[+-]\d{2}(?:'\d{2})?'?)?$")


def _camel(key: str) -> str:
    return key[:1].lower() + key[1:]


def pdf_date_to_iso(value) -> Optional[str]:
    """A PDF date string as ISO-8601 (`D:20260102030405Z` -> `2026-01-02T03:04:05Z`, `D:20260102030405+02'00'`
    -> `2026-01-02T03:04:05+02:00`, `D:2026` -> `2026-01-01T00:00:00`); None when the text is not a PDF date or a
    field is out of range."""
    match = _PDF_DATE.match(str(value or "").strip())
    if not match:
        return None
    defaults = {"month": 1, "day": 1, "hour": 0, "minute": 0, "second": 0}
    fields = {name: int(match.group(name)) if match.group(name) else defaults.get(name, 0)
              for name in ("year", "month", "day", "hour", "minute", "second")}
    try:
        stamp = datetime(**fields)
    except ValueError:
        return None
    zone = match.group("zone") or ""
    if zone == "Z":
        suffix = "Z"
    elif zone:
        minutes = zone[4:6] if len(zone) >= 6 else "00"
        suffix = f"{zone[0]}{zone[1:3]}:{minutes}"
    else:
        suffix = ""
    return stamp.isoformat() + suffix


def _page_text(pdf, index: int) -> str:
    page = pdf[index]
    try:
        textpage = page.get_textpage()
        try:
            return (textpage.get_text_bounded() or "").strip()
        finally:
            textpage.close()
    finally:
        page.close()


def _page_image(pdf, index: int):
    page = pdf[index]
    try:
        width, height = page.get_size()
        scale = min(_RASTER_SCALE_MAX, VISION_MAX_LONG_EDGE_PX / max(width, height, 1.0))
        return page.render(scale=scale).to_pil()
    finally:
        page.close()


def extract_pdf(path: str, ctx: ExtractContext) -> BranchResult:
    result = BranchResult(file_class=CLASS_DOCUMENT)
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        message = str(exc)
        result.attributes["sys_document"] = {
            "format": "PDF", "decodable": False, "encrypted": "password" in message.lower()}
        result.render_skipped = RENDER_SKIPPED_ERROR
        result.warnings.append(f"PDF could not be opened: {message}")
        return result
    try:
        page_count = len(pdf)
        sys_document: Dict[str, object] = {"format": "PDF", "decodable": True, "pageCount": page_count}
        try:
            version = pdf.get_version()
            if version:
                sys_document["pdfVersion"] = f"{version // 10}.{version % 10}"
        except pdfium.PdfiumError:
            pass
        for key, value in pdf.get_metadata_dict().items():
            if value:
                sys_document[_camel(key)] = str(value)[:_INFO_VALUE_MAX_CHARS]
        if sys_document.get("creationDate"):
            created_at = pdf_date_to_iso(sys_document["creationDate"])
            if created_at:
                sys_document["createdAt"] = created_at
            else:
                result.warnings.append(
                    f"PDF CreationDate {sys_document['creationDate']!r} is not a PDF date; createdAt omitted")
        if page_count:
            width, height = pdf.get_page_size(0)
            sys_document["pageWidthPt"], sys_document["pageHeightPt"] = round(width, 1), round(height, 1)
        excerpt_parts: List[str] = []
        collected = pages_with_text = pages_scanned = 0
        for index in range(page_count):
            if collected >= ctx.max_text_chars:
                break
            text = _page_text(pdf, index)
            pages_scanned += 1
            if text:
                pages_with_text += 1
                excerpt_parts.append(text)
                collected += len(text)
        sys_document["hasText"] = pages_with_text > 0
        sys_document["pagesWithText"] = pages_with_text
        sys_document["pagesScannedForText"] = pages_scanned
        result.text_excerpt = truncate_text("\n\n".join(excerpt_parts), ctx.max_text_chars)
        rendered = 0
        for index in range(min(PDF_RASTER_PAGES, page_count)):
            try:
                png, _ = normalise_for_vision(_page_image(pdf, index))
                result.render_images.append(write_png(png, ctx.work_dir, f"media-{index + 1:02d}.png"))
                rendered += 1
            except Exception as exc:  # noqa: BLE001 - one page that will not rasterise keeps the text excerpt
                result.warnings.append(f"PDF page {index + 1} could not be rasterised: {exc}")
        if page_count and not rendered:
            result.render_skipped = RENDER_SKIPPED_ERROR
    finally:
        pdf.close()
    result.attributes["sys_document"] = sys_document
    result.facts["pages"] = human_count(page_count, "page")
    for key in ("title", "author", "subject"):
        if sys_document.get(key):
            result.facts[key] = str(sys_document[key])
    if sys_document.get("createdAt"):
        result.facts["created"] = str(sys_document["createdAt"])
    return result
