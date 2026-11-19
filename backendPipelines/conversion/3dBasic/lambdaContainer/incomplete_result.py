# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What a conversion that left referenced files out reports: the completion message, the refusal for
MISSING_REFERENCES fail, and the ``conversion-incomplete.json`` result document.

The document has the shape and the caps of the 3D thumbnail's ``preview-incomplete.json``
(``preview_pipeline/dependencies.incomplete_result_document``): a schema id, the status, the
completion message, the input's asset-relative path, the counts by kind, at most ``MAX_RESULT_ENTRIES``
skipped files (geometry layers first) with ``truncated`` set when any were left out, every text cut to
``MAX_RESULT_TEXT_CHARS`` and the encoded document at most ``MAX_RESULT_DOCUMENT_BYTES``. Paths are
relative to the input's asset; a reference refused as outside the asset is named only by a safe base
file name (``reference_collectors.safe_base_name``), never by a bucket, host, S3 key or full URI.
It is written under the execution's results folder, where the workflow's output step records it on the
execution (Output Results). A complete run writes none.
"""

import json
from typing import List

from reference_collectors import safe_base_name

INCOMPLETE_RESULT_FILENAME = "conversion-incomplete.json"
INCOMPLETE_RESULT_SCHEMA = "vams.conversion3dBasic.incomplete/v1"
MAX_RESULT_ENTRIES = 100
MAX_RESULT_TEXT_CHARS = 256
MAX_RESULT_DOCUMENT_BYTES = 64 * 1024
# Files named in a MISSING_REFERENCES fail refusal; the rest are counted.
MAX_REFUSAL_FILES = 20

KIND_GEOMETRY_LAYER = "geometryLayer"
KIND_DATA_FILE = "dataFile"
KIND_TEXTURE_OR_OTHER = "textureOrOther"
_ORDER = (KIND_GEOMETRY_LAYER, KIND_DATA_FILE, KIND_TEXTURE_OR_OTHER)


def _plural(count, one, many):
    return one if count == 1 else many


def _bounded(text):
    text = text if isinstance(text, str) else str(text)
    return text if len(text) <= MAX_RESULT_TEXT_CHARS else text[:MAX_RESULT_TEXT_CHARS - 3] + "..."


def _sorted(entries: List[dict]) -> List[dict]:
    return sorted(entries, key=lambda e: _ORDER.index(e["kind"]) if e["kind"] in _ORDER else len(_ORDER))


def _counts(entries):
    return {kind: sum(1 for e in entries if e["kind"] == kind) for kind in _ORDER}


def summary(entries: List[dict]) -> str:
    counts = _counts(entries)
    total = len(entries)
    geometry, data, other = counts[KIND_GEOMETRY_LAYER], counts[KIND_DATA_FILE], counts[KIND_TEXTURE_OR_OTHER]
    return (f"{total} referenced {_plural(total, 'file', 'files')} could not be included "
            f"({geometry} geometry {_plural(geometry, 'layer', 'layers')}, {data} data "
            f"{_plural(data, 'file', 'files')}, {other} {_plural(other, 'texture/other file', 'textures/other files')})")


def message(input_name: str, entries: List[dict]) -> str:
    return (f"Converted {input_name}, but the converted file is incomplete: {summary(entries)}. "
            f"See {INCOMPLETE_RESULT_FILENAME} in the run's results and the run log for the list.")


def _shown_path(entry):
    return safe_base_name(entry["reference"]) if entry.get("rejected") else _bounded(entry["reference"])


def refusal(input_name: str, entries: List[dict]) -> str:
    """The error of a MISSING_REFERENCES fail run, naming the first files and their reasons."""
    listed = "; ".join(f"{_shown_path(e) or 'a reference outside the asset'} ({e['reason']})"
                       for e in _sorted(entries)[:MAX_REFUSAL_FILES])
    more = f"; and {len(entries) - MAX_REFUSAL_FILES} more" if len(entries) > MAX_REFUSAL_FILES else ""
    return (f"{input_name} was not converted because MISSING_REFERENCES is fail and {summary(entries)}: "
            f"{listed}{more}. Upload the files to the asset at those paths, or set MISSING_REFERENCES "
            f"to skip to convert without them.")


def log(logger, entries: List[dict]):
    """One WARNING summary, then each skipped file with its reason, geometry layers first; every
    name is quoted with repr so a reference cannot forge a log line."""
    logger.warning(f"Incomplete conversion: {summary(entries)}")
    for entry in _sorted(entries)[:200]:
        logger.warning(f"  {entry['kind']}: {entry['reference']!r} (named in {entry['referrer']!r}): {entry['reason']}")


def document(input_name: str, input_path: str, entries: List[dict]) -> bytes:
    rows = [{"kind": e["kind"], "path": _shown_path(e), "namedIn": _bounded(e["referrer"]),
             "reason": _bounded(e["reason"])} for e in _sorted(entries)]
    counts = _counts(entries)
    body_doc = {
        "schema": INCOMPLETE_RESULT_SCHEMA,
        "status": "INCOMPLETE",
        "message": message(input_name, entries),
        "inputFile": _bounded(input_path),
        "counts": {
            "skipped": len(entries),
            "geometryLayers": counts[KIND_GEOMETRY_LAYER],
            "dataFiles": counts[KIND_DATA_FILE],
            "texturesOrOther": counts[KIND_TEXTURE_OR_OTHER],
            "notFollowed": 0,
        },
        "fetchStoppedEarly": False,
        "skippedFiles": rows[:MAX_RESULT_ENTRIES],
        "truncated": len(rows) > MAX_RESULT_ENTRIES,
    }
    body = json.dumps(body_doc, indent=2).encode("utf-8")
    while len(body) > MAX_RESULT_DOCUMENT_BYTES and body_doc["skippedFiles"]:
        body_doc["skippedFiles"] = body_doc["skippedFiles"][:len(body_doc["skippedFiles"]) // 2]
        body_doc["truncated"] = True
        body = json.dumps(body_doc, indent=2).encode("utf-8")
    return body
