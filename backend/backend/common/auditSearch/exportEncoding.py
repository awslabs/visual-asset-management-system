# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Export encoding of audit rows: CSV that a spreadsheet cannot run, and JSON.

Rows reaching this module are already redacted and encoded (``redaction``), so control and bidi
characters are visible escapes. The CSV rules are the remaining ones:

* every cell is quoted (``QUOTE_ALL``), lines end in CRLF, the file is UTF-8 with no byte order mark;
* a text cell that starts with ``=``, ``+``, ``-``, ``@``, tab or carriage return (or a backslash
  escape of one of the last two) gets a leading apostrophe, so a spreadsheet reads it as text;
* the columns and their order are fixed, and the header is written on the first chunk only;
* a nested value is one JSON text cell, compact, ASCII, subject to the same prefix rule.
"""

import csv
import io
import json
from typing import Any, Dict, Iterable, List, Sequence

from common.auditSearch.redaction import encode_text

# The fixed column order of a CSV export. A column is a key of a row (``rowModel.AuditRow.to_dict``).
CSV_COLUMNS: Sequence[str] = (
    "timestamp", "groupKey", "groupLabel", "action", "legacyType", "outcome", "reason", "user",
    "onBehalfOf", "authMethod", "apiKeyId", "entityType", "entityId", "databaseId", "assetId",
    "filePath", "requestId", "method", "route", "sourceIp", "bytes", "fileCount", "itemCount",
    "durationMs", "probe", "formatVersion", "parseStatus", "truncated", "requestParameters",
    "details", "changes", "previous",
)

# The first characters a spreadsheet reads as a formula, plus the two controls some read as one.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\\u0009", "\\u000d")


def csv_cell(value: Any) -> str:
    """One CSV cell's text for a value, with the formula-injection prefix applied to text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)
    else:
        # Rows are encoded already; encoding again is idempotent and keeps a raw row safe too.
        text = encode_text(value if isinstance(value, str) else str(value))
    # The escaped forms of tab and CR are checked too: they are visible text here, but a consumer
    # that decodes the escapes would see the control character.
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _flatten(row: Dict[str, Any]) -> Dict[str, Any]:
    flat = dict(row)
    entity = row.get("entity") or {}
    request = row.get("request") or {}
    flat["entityType"] = entity.get("type")
    flat["entityId"] = entity.get("id")
    for key in ("requestId", "method", "route", "sourceIp"):
        flat[key] = request.get(key)
    return flat


def rows_to_csv(rows: Iterable[Dict[str, Any]], include_header: bool = True) -> str:
    """The CSV text of the rows (each a ``to_dict`` row). The header is written when asked for."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    if include_header:
        writer.writerow([csv_cell(name) for name in CSV_COLUMNS])
    for row in rows:
        flat = _flatten(row)
        writer.writerow([csv_cell(flat.get(column)) for column in CSV_COLUMNS])
    return buffer.getvalue()


def rows_to_json(rows: Iterable[Dict[str, Any]]) -> str:
    """The JSON text of the rows: an array of row objects, ASCII only."""
    return json.dumps(list(rows), ensure_ascii=True, default=str)


def encode_chunk(rows: List[Dict[str, Any]], export_format: str, first_chunk: bool) -> bytes:
    """One export chunk as bytes. JSON chunks are standalone arrays; a CSV header is on the first only."""
    if export_format == "csv":
        return rows_to_csv(rows, include_header=first_chunk).encode("utf-8")
    if export_format == "json":
        return rows_to_json(rows).encode("utf-8")
    raise ValueError("unsupported export format")
