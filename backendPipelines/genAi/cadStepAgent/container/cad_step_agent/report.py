# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The outcome documents of a run: the Markdown report written beside the STEP file, the file metadata
and file attribute documents the process-output step applies, and the bounded task-token payload.

All builders are pure; every free-text field is bounded so a verbose model cannot grow the execution
record or a metadata value past what VAMS accepts.
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

STATUS_SUCCEEDED = "succeeded"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

SUMMARY_MAX_CHARS = 2000
LIST_MAX_CHARS = 2000
TOKEN_SUMMARY_MAX_CHARS = 1500
CAUSE_MAX_CHARS = 256
MAX_LIST_ITEMS = 25
REPORT_FILE_SUFFIX = ".cad-agent-report.md"

METADATA_KEY_STATUS = "cadAgentStatus"
METADATA_KEY_SUMMARY = "cadAgentSummary"
METADATA_KEY_UNRESOLVED = "cadAgentUnresolved"
METADATA_KEY_SOURCES = "cadAgentSources"
METADATA_KEY_MODEL = "cadAgentModel"
METADATA_KEY_ATTEMPTS = "cadAgentAttempts"
METADATA_KEY_GEOMETRY = "cadAgentGeometry"
METADATA_KEY_RUN_ID = "cadAgentRunId"

# File attributes: the measured figures as separate, filterable values. VAMS file attributes are
# string-typed, so every figure is a decimal string; one the outcome lacks is omitted, never blank.
ATTRIBUTE_KEY_STATUS = "cadAgentStatus"
ATTRIBUTE_KEY_MODE = "cadAgentMode"
ATTRIBUTE_KEY_VOLUME = "cadVolumeMm3"
ATTRIBUTE_KEY_BBOX_X = "cadBboxXMm"
ATTRIBUTE_KEY_BBOX_Y = "cadBboxYMm"
ATTRIBUTE_KEY_BBOX_Z = "cadBboxZMm"
ATTRIBUTE_KEY_SOLIDS = "cadSolidCount"
ATTRIBUTE_KEY_HOLES = "cadHoleCount"
ATTRIBUTE_KEY_FILLETS = "cadFilletCount"
ATTRIBUTE_KEY_UNITS = "cadUnits"
# cad_io measures every figure in millimetres: Open CASCADE converts a file's declared unit on import and
# the agent writes its scripts in millimetres.
GEOMETRY_UNITS = "mm"
ATTRIBUTE_DECIMALS = 3


@dataclass
class AttemptRecord:
    number: int
    succeeded: bool
    summary: str
    output_tail: str = ""


@dataclass
class RunOutcome:
    status: str
    prompt: str
    mode: str
    output_file_name: str
    model: str
    run_id: str
    attempts: List[AttemptRecord] = field(default_factory=list)
    summary: str = ""
    unresolved: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    geometry: Optional[Dict] = None
    wall_seconds: float = 0.0
    research_allowed: bool = True
    checks: List[str] = field(default_factory=list)  # per-feature "expected - measured - ok|mismatch" lines


def _clip(text, limit):
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _bounded_list(items, max_items=MAX_LIST_ITEMS, max_chars=LIST_MAX_CHARS):
    """A JSON array string of at most ``max_items`` entries that fits in ``max_chars`` characters."""
    kept = [str(i) for i in (items or [])][:max_items]
    while kept and len(json.dumps(kept)) > max_chars:
        kept = kept[:-1]
    return json.dumps(kept)


def metadata_document(outcome, update_type="update"):
    """The ``<file>.metadata.json`` body the process-output step applies to the output file."""
    entries = [
        {"metadataKey": METADATA_KEY_STATUS, "metadataValue": outcome.status},
        {"metadataKey": METADATA_KEY_SUMMARY, "metadataValue": _clip(outcome.summary, SUMMARY_MAX_CHARS)},
        {"metadataKey": METADATA_KEY_UNRESOLVED, "metadataValue": _bounded_list(outcome.unresolved)},
        {"metadataKey": METADATA_KEY_SOURCES, "metadataValue": _bounded_list(outcome.sources)},
        {"metadataKey": METADATA_KEY_MODEL, "metadataValue": _clip(outcome.model, 256)},
        {"metadataKey": METADATA_KEY_ATTEMPTS, "metadataValue": str(len(outcome.attempts))},
        {"metadataKey": METADATA_KEY_RUN_ID, "metadataValue": _clip(outcome.run_id, 128)},
    ]
    if outcome.geometry:
        entries.append({"metadataKey": METADATA_KEY_GEOMETRY,
                        "metadataValue": _clip(json.dumps(outcome.geometry, sort_keys=True), LIST_MAX_CHARS)})
    return {"type": "metadata", "updateType": update_type, "metadata": entries}


def _decimal(value, places=ATTRIBUTE_DECIMALS):
    """A number as a plain decimal string: rounded to ``places``, no exponent, no trailing zeros."""
    text = f"{round(float(value), places):.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _feature_total(features, key):
    """The summed ``count`` of one feature list, or None when the feature summary is absent."""
    if not isinstance(features, dict) or not isinstance(features.get(key), list):
        return None
    return sum(int(entry.get("count", 0)) for entry in features[key] if isinstance(entry, dict))


def attribute_document(outcome, update_type="update"):
    """The ``<file>.attribute.json`` body: the run's status and mode and its measured figures as file
    attributes. A figure the outcome does not carry is left out rather than written empty."""
    entries = [
        {"metadataKey": ATTRIBUTE_KEY_STATUS, "metadataValue": outcome.status},
        {"metadataKey": ATTRIBUTE_KEY_MODE, "metadataValue": outcome.mode},
    ]
    geometry = outcome.geometry if isinstance(outcome.geometry, dict) else {}
    figures = []
    if geometry.get("volume_mm3") is not None:
        figures.append((ATTRIBUTE_KEY_VOLUME, _decimal(geometry["volume_mm3"])))
    bbox = geometry.get("bounding_box_mm")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 6:
        for key, axis in ((ATTRIBUTE_KEY_BBOX_X, 0), (ATTRIBUTE_KEY_BBOX_Y, 1), (ATTRIBUTE_KEY_BBOX_Z, 2)):
            figures.append((key, _decimal(bbox[axis + 3] - bbox[axis])))
    if geometry.get("solid_count") is not None:
        figures.append((ATTRIBUTE_KEY_SOLIDS, str(int(geometry["solid_count"]))))
    for key, feature in ((ATTRIBUTE_KEY_HOLES, "holes"), (ATTRIBUTE_KEY_FILLETS, "fillet_like_faces")):
        total = _feature_total(geometry.get("features"), feature)
        if total is not None:
            figures.append((key, str(total)))
    if figures:
        figures.append((ATTRIBUTE_KEY_UNITS, GEOMETRY_UNITS))
    entries += [{"metadataKey": key, "metadataValue": value} for key, value in figures]
    return {"type": "attribute", "updateType": update_type, "metadata": entries}


def markdown_report(outcome):
    """The human-readable report written beside the STEP file."""
    lines = [
        f"# CAD STEP agent run {outcome.run_id}",
        "",
        f"- Status: **{outcome.status}**",
        f"- Mode: {outcome.mode}",
        f"- Output file: `{outcome.output_file_name}`",
        f"- Model: {outcome.model}",
        f"- Attempts: {len(outcome.attempts)}",
        f"- Internet research: {'allowed' if outcome.research_allowed else 'disabled'}",
        f"- Wall time: {outcome.wall_seconds:.0f} s",
        "",
        "## Instruction",
        "",
        _clip(outcome.prompt, 4000),
        "",
        "## Summary",
        "",
        _clip(outcome.summary, SUMMARY_MAX_CHARS) or "(none)",
        "",
        "## Geometry",
        "",
        json.dumps(outcome.geometry, indent=2, sort_keys=True) if outcome.geometry else "(no valid geometry produced)",
        "",
        "## Not completed",
        "",
    ]
    lines += [f"- {_clip(item, 500)}" for item in outcome.unresolved[:MAX_LIST_ITEMS]] or ["- (nothing outstanding)"]
    lines += ["", "## Verification (agent's per-feature checks)", ""]
    lines += [f"- {_clip(item, 500)}" for item in outcome.checks[:MAX_LIST_ITEMS]] or ["- (the agent recorded no checks)"]
    lines += ["", "## Sources consulted", ""]
    lines += [f"- {_clip(src, 500)}" for src in outcome.sources[:MAX_LIST_ITEMS]] or ["- (none)"]
    lines += ["", "## Attempts", ""]
    for attempt in outcome.attempts:
        marker = "ok" if attempt.succeeded else "failed"
        lines.append(f"### Attempt {attempt.number} - {marker}")
        lines.append("")
        lines.append(_clip(attempt.summary, 1000) or "(no summary)")
        if attempt.output_tail:
            lines += ["", "```text", _clip(attempt.output_tail, 6000), "```"]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def report_file_name(output_file_name):
    return f"{output_file_name}{REPORT_FILE_SUFFIX}"


def success_payload(outcome):
    """The SendTaskSuccess output: the outcome, never the job's stdout."""
    return {
        "status": outcome.status,
        "outputFile": outcome.output_file_name,
        "attempts": len(outcome.attempts),
        "unresolvedCount": len(outcome.unresolved),
        "summary": _clip(outcome.summary, TOKEN_SUMMARY_MAX_CHARS),
    }


def failure_cause(message):
    return _clip(message, CAUSE_MAX_CHARS)
