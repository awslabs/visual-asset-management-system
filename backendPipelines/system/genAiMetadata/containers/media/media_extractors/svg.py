# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""SVG (.svg): size, viewBox and element counts from the XML -> sys_image. The SVG source is the analysis
input; nothing is rasterised. Parsing goes through defusedxml, which refuses entity declarations and
external references (the constructs behind entity-expansion and XXE attacks) while accepting the plain
DOCTYPE that SVG 1.1 exports carry."""

import os
import re
from typing import Dict, Optional, Tuple
from xml.etree.ElementTree import ParseError

import defusedxml
import defusedxml.ElementTree as SafeET

from .common import CLASS_IMAGE, SVG_MAX_BYTES, BranchResult, ExtractContext, human_count, truncate_text

_LENGTH = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([a-z%]*)\s*$", re.IGNORECASE)
_TEXT_ELEMENTS = ("text", "tspan", "title", "desc")
_MAX_ELEMENTS_COUNTED = 200_000
_TEXT_CONTENT_MAX_CHARS = 500
_TOP_ELEMENT_KINDS = 25


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_length(value) -> Optional[Tuple[float, str]]:
    """`"200"`, `"200px"`, `"12.5mm"`, `"40%"` -> (number, unit); anything else -> None."""
    if value is None:
        return None
    match = _LENGTH.match(str(value))
    if not match:
        return None
    return float(match.group(1)), (match.group(2) or "px").lower()


def extract_svg(path: str, ctx: ExtractContext) -> BranchResult:
    result = BranchResult(file_class=CLASS_IMAGE)
    size = os.path.getsize(path)
    sys_image: Dict[str, object] = {"format": "SVG", "vector": True, "sizeBytes": size}
    result.attributes["sys_image"] = sys_image
    result.facts["imageFormat"] = "SVG"
    if size > SVG_MAX_BYTES:
        result.warnings.append(
            f"SVG is {size:,} bytes, above the {SVG_MAX_BYTES:,} parse budget; header attributes only")
        return result
    with open(path, "rb") as handle:
        raw = handle.read()
    source = raw.decode("utf-8", "replace")
    result.text_excerpt = truncate_text(source, ctx.max_text_chars)
    try:
        root = SafeET.fromstring(raw)
    except defusedxml.DefusedXmlException as exc:
        # The document's text is what an entity expansion produces, so it is withheld along with the parse.
        result.text_excerpt = ""
        result.warnings.append(f"SVG declares XML entities or external references and was not parsed: {exc}")
        return result
    except ParseError as exc:
        result.warnings.append(f"SVG is not well-formed XML: {exc}")
        return result
    if _local(root.tag) != "svg":
        result.warnings.append(f"Root element is <{_local(root.tag)}>, not <svg>")
    for attribute in ("width", "height"):
        parsed = parse_length(root.get(attribute))
        if parsed:
            sys_image[attribute] = parsed[0]
            sys_image[attribute + "Unit"] = parsed[1]
    view_box = root.get("viewBox")
    if view_box:
        try:
            numbers = [float(part) for part in re.split(r"[\s,]+", view_box.strip()) if part]
        except ValueError:
            numbers = []
        if len(numbers) == 4:
            sys_image["viewBox"] = numbers
            sys_image.setdefault("width", numbers[2])
            sys_image.setdefault("height", numbers[3])
        else:
            result.warnings.append(f"Unparseable viewBox: {view_box!r}")
    counts: Dict[str, int] = {}
    text_parts = []
    total = 0
    for element in root.iter():
        if total >= _MAX_ELEMENTS_COUNTED:
            result.warnings.append(f"Element count capped at {_MAX_ELEMENTS_COUNTED:,}")
            break
        total += 1
        name = _local(element.tag)
        counts[name] = counts.get(name, 0) + 1
        if name in _TEXT_ELEMENTS and element.text and element.text.strip():
            text_parts.append(element.text.strip())
    sys_image["elementCount"] = total
    sys_image["elementCounts"] = dict(sorted(counts.items(), key=lambda item: -item[1])[:_TOP_ELEMENT_KINDS])
    sys_image["hasText"] = bool(text_parts)
    if text_parts:
        sys_image["textContent"] = truncate_text(" ".join(text_parts), _TEXT_CONTENT_MAX_CHARS)
    if "width" in sys_image and "height" in sys_image:
        unit = sys_image.get("widthUnit") or sys_image.get("heightUnit") or "px"
        result.facts["dimensions"] = f"{sys_image['width']:g} x {sys_image['height']:g} {unit}"
    result.facts["elements"] = human_count(total, "element")
    return result
