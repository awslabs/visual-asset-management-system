#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A log-safe one-line description of a Lambda event: its shape and counts, never its content.

An event-source Lambda (the indexers, the bucket sync, the Garnet add-on indexers) is invoked with
DynamoDB stream records, SNS/SQS envelopes or S3 notifications. A stream image carries every field
value of the row it describes, and a metadata row stores the field NAME as a value
(`metadataKey`/`metadataValue`), so the key-driven redaction in `safeLogger` cannot recognise a
sensitive field in it even when the event is logged as a structured dict. Interpolating the event
into the message bypasses redaction entirely. Handlers therefore log `event_log_summary(event)` at
entry, and the per-record identifiers (event name, database id, asset id, file key) where each
record is handled.
"""

from collections import Counter
from typing import Any

# Identifier fields of a direct (non event-source) invocation that are safe to name in a log line.
DIRECT_INVOCATION_ID_FIELDS = ("operation", "databaseId", "assetId", "filePath", "s3Key")

# Distinct event sources named in one line; more are counted, not listed.
MAX_LISTED_SOURCES = 5

# A source label or identifier longer than this is cut, so a malformed event cannot grow the line.
MAX_LABEL_LENGTH = 256


def _label(value: Any) -> str:
    text = value if isinstance(value, str) else type(value).__name__
    return text if len(text) <= MAX_LABEL_LENGTH else text[:MAX_LABEL_LENGTH] + "..."


def _record_source(record: Any) -> str:
    if not isinstance(record, dict):
        return "unknown"
    # SNS records spell the key with a capital E.
    source = record.get("eventSource") or record.get("EventSource")
    return _label(source) if source else "unknown"


def event_log_summary(event: Any) -> str:
    """Describe `event` by record count and event source, or by the ids of a direct invocation.

    Never includes a record body, a stream image, an SNS message or any other field value.
    """
    if not isinstance(event, dict):
        return f"non-object event ({type(event).__name__})"

    records = event.get("Records")
    if isinstance(records, list):
        sources = Counter(_record_source(record) for record in records)
        listed = ", ".join(f"{source}={count}" for source, count in sources.most_common(MAX_LISTED_SOURCES))
        others = len(sources) - MAX_LISTED_SOURCES
        if others > 0:
            listed += f", {others} more source(s)"
        return f"{len(records)} record(s) [{listed}]" if records else "0 record(s)"

    ids = [
        f"{field}={_label(event[field])}"
        for field in DIRECT_INVOCATION_ID_FIELDS
        if event.get(field) not in (None, "")
    ]
    return f"direct invocation ({', '.join(ids)})" if ids else f"direct invocation ({len(event)} field(s))"
