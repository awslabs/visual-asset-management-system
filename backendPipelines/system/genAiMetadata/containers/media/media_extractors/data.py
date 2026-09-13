# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tabular data (.csv .fcs): columns, row count and sample rows -> sys_data; the header plus sample rows are
the analysis input. FCS files are read from their TEXT segment with the standard library; the binary DATA
segment is not decoded."""

import csv
import io
import re
from typing import Dict, List

from .common import (
    CLASS_DATA,
    DATA_CELL_MAX_CHARS,
    DATA_MAX_COLUMNS,
    DATA_MAX_ROWS_COUNTED,
    DATA_SAMPLE_ROWS,
    DATA_SNIFF_BYTES,
    BranchResult,
    ExtractContext,
    human_count,
    other_fallback,
    truncate_text,
)
from .text import decode_head

_DELIMITERS = ",;\t|"
_NUMBER = re.compile(r"^\s*[-+]?(\d+([.,]\d*)?|[.,]\d+)([eE][-+]?\d+)?\s*$")
_FCS_HEADER_BYTES = 58
# FCS keywords carried into sys_data.keywords when present (acquisition context, never per-event data).
_FCS_KEYWORDS = ("$DATE", "$BTIM", "$ETIM", "$CYT", "$CYTSN", "$SRC", "$SYS", "$FIL", "$INST", "$OP", "$PROJ",
                 "$EXP", "$SMNO", "$CELLS", "$COM")
# Largest cell csv.reader accepts (embedded JSON and base64 columns are common in exported data); the
# stdlib default is 128 KiB. A cell above this raises csv.Error, which demotes the file to `other`.
CSV_FIELD_SIZE_LIMIT = 1024 * 1024
csv.field_size_limit(CSV_FIELD_SIZE_LIMIT)


def sniff_dialect(sample: str):
    try:
        return csv.Sniffer().sniff(sample, delimiters=_DELIMITERS)
    except csv.Error:
        return csv.excel


def _has_header(sample: str) -> bool:
    try:
        return csv.Sniffer().has_header(sample)
    except csv.Error:
        return True


def _cell(value: str) -> str:
    return value[:DATA_CELL_MAX_CHARS]


def _int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def column_types(rows: List[List[str]], column_count: int) -> List[str]:
    """`number`, `text` or `empty` per column over the sample rows."""
    types = []
    for index in range(column_count):
        values = [row[index].strip() for row in rows if index < len(row) and row[index].strip()]
        if not values:
            types.append("empty")
        elif all(_NUMBER.match(value) for value in values):
            types.append("number")
        else:
            types.append("text")
    return types


def extract_csv(path: str, ctx: ExtractContext) -> BranchResult:
    with open(path, "rb") as handle:
        head = handle.read(DATA_SNIFF_BYTES)
    text, encoding = decode_head(head)
    if text is None:
        return other_fallback(ctx, "not decodable as text")
    result = BranchResult(file_class=CLASS_DATA)
    sample = text
    if len(head) == DATA_SNIFF_BYTES:
        # The head may end mid-line; the sniffer sees whole lines only.
        sample = text[:text.rfind("\n") + 1] or text
    dialect = sniff_dialect(sample)
    header_present = _has_header(sample)
    columns: List[str] = []
    sample_rows: List[List[str]] = []
    row_count = 0
    capped = False
    try:
        with open(path, "r", encoding=encoding, errors="replace", newline="") as handle:
            reader = csv.reader(handle, dialect)
            first = next(reader, None)
            if first is not None:
                if header_present:
                    columns = [_cell(name.strip()) for name in first]
                else:
                    columns = [f"column{index + 1}" for index in range(len(first))]
                    sample_rows.append([_cell(value) for value in first[:DATA_MAX_COLUMNS]])
                    row_count = 1
                for row in reader:
                    row_count += 1
                    if len(sample_rows) < DATA_SAMPLE_ROWS:
                        sample_rows.append([_cell(value) for value in row[:DATA_MAX_COLUMNS]])
                    if row_count >= DATA_MAX_ROWS_COUNTED:
                        capped = True
                        break
    except csv.Error as exc:
        return other_fallback(ctx, f"CSV could not be parsed: {exc}")
    recorded_columns = columns[:DATA_MAX_COLUMNS]
    sys_data: Dict[str, object] = {
        "format": "CSV",
        "encoding": encoding,
        "delimiter": dialect.delimiter,
        "hasHeader": header_present,
        "columnCount": len(columns),
        "columns": recorded_columns,
        "rowCount": row_count,
        "rowCountExact": not capped,
        "columnTypes": column_types(sample_rows, len(recorded_columns)),
        "sampleRows": sample_rows,
    }
    if len(columns) > DATA_MAX_COLUMNS:
        result.warnings.append(f"{len(columns)} columns; the first {DATA_MAX_COLUMNS} are recorded")
    result.attributes["sys_data"] = sys_data
    result.facts["columns"] = human_count(len(columns), "column")
    result.facts["rows"] = human_count(row_count, "row") + (" (counting stopped)" if capped else "")
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=dialect.delimiter, lineterminator="\n")
    writer.writerow(recorded_columns)
    writer.writerows(sample_rows)
    result.text_excerpt = truncate_text(buffer.getvalue(), ctx.max_text_chars)
    return result


def parse_fcs_text_segment(data: bytes) -> Dict[str, str]:
    """Keyword -> value from the TEXT segment the 58-byte FCS header points at, plus `__version__`."""
    if len(data) < _FCS_HEADER_BYTES or not data.startswith(b"FCS"):
        raise ValueError("not an FCS file")
    text_start = _int(data[10:18].decode("ascii", "replace"), -1)
    text_end = _int(data[18:26].decode("ascii", "replace"), -1)
    if text_start < _FCS_HEADER_BYTES or text_end <= text_start or text_end >= len(data):
        raise ValueError("FCS TEXT segment offsets are out of range")
    text = data[text_start:text_end + 1].decode("utf-8", "replace")
    delimiter = text[0]
    parts = text[1:].split(delimiter)
    keywords: Dict[str, str] = {"__version__": data[:6].decode("ascii", "replace").strip()}
    for index in range(0, len(parts) - 1, 2):
        key = parts[index].strip()
        if key:
            keywords[key] = parts[index + 1]
    return keywords


def extract_fcs(path: str, ctx: ExtractContext) -> BranchResult:
    with open(path, "rb") as handle:
        header = handle.read(_FCS_HEADER_BYTES)
        if len(header) < _FCS_HEADER_BYTES or not header.startswith(b"FCS"):
            return other_fallback(ctx, "not an FCS file")
        text_end = _int(header[18:26].decode("ascii", "replace"), -1)
        handle.seek(0)
        data = handle.read(max(text_end + 1, _FCS_HEADER_BYTES))
    try:
        keywords = parse_fcs_text_segment(data)
    except ValueError as exc:
        return other_fallback(ctx, str(exc))
    result = BranchResult(file_class=CLASS_DATA)
    parameters = _int(keywords.get("$PAR"))
    columns = []
    for number in range(1, parameters + 1):
        short = keywords.get(f"$P{number}N", f"P{number}")
        long_name = keywords.get(f"$P{number}S")
        columns.append(_cell(f"{short} ({long_name})" if long_name else short))
    events = _int(keywords.get("$TOT"))
    context = {key: keywords[key][:DATA_CELL_MAX_CHARS] for key in _FCS_KEYWORDS if keywords.get(key)}
    sys_data: Dict[str, object] = {
        "format": "FCS",
        "fcsVersion": keywords["__version__"],
        "columnCount": parameters,
        "columns": columns[:DATA_MAX_COLUMNS],
        "rowCount": events,
        "rowCountExact": True,
        "dataType": keywords.get("$DATATYPE"),
        "mode": keywords.get("$MODE"),
        "byteOrder": keywords.get("$BYTEORD"),
        "sampleRows": [],
        "keywords": context or None,
    }
    sys_data = {key: value for key, value in sys_data.items() if value is not None}
    result.attributes["sys_data"] = sys_data
    result.facts["columns"] = human_count(parameters, "parameter")
    result.facts["rows"] = human_count(events, "event")
    if context.get("$CYT"):
        result.facts["cytometer"] = context["$CYT"]
    lines = [f"{key}: {value}" for key, value in context.items()]
    lines.append("parameters: " + ", ".join(columns))
    result.text_excerpt = truncate_text("\n".join(lines), ctx.max_text_chars)
    return result


def extract_data(path: str, ctx: ExtractContext) -> BranchResult:
    if ctx.extension.lower() == ".fcs":
        return extract_fcs(path, ctx)
    return extract_csv(path, ctx)
