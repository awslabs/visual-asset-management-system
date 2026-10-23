#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Unwrapping of a file-indexer message into its S3 event records.

`sqsBucketSync.publish_to_file_indexer_sns` republishes the event it was invoked with, filtered to
the records it processed and stamped with `ASSET_BUCKET_NAME` / `ASSET_BUCKET_PREFIX`, so the SNS
`Message` on the file indexer topic keeps the shape of the bucket notification's delivery path. The
deployed path is S3 -> SNS -> SQS -> sqsBucketSync, which yields an SQS-wrapped message:

    {"Records": [{"eventSource": "aws:sqs",
                  "body": "{\\"Type\\": \\"Notification\\", \\"Message\\": \\"<S3 event JSON>\\"}"}],
     "ASSET_BUCKET_NAME": ..., "ASSET_BUCKET_PREFIX": ...}

where the S3 event is `{"Records": [{"eventSource": "aws:s3", "eventName": ..., "eventTime": ...,
"s3": {"bucket": {"name": ...}, "object": {"key": ..., "versionId": ...}}}]}`. A message queued
through a second SNS -> SQS hop wraps the same thing in one more `Notification` envelope, and a
bucket notified through a direct SNS or S3 subscription arrives as an SNS event
(`Records[].Sns.Message`) or as the flat S3 event itself.

`s3_records_from_indexer_message` flattens every one of those into the list of S3 event records,
so the file indexer and the compliance trigger read the same records from the same message without
each re-implementing the envelope walk. The bucket identity stamped on the outer message is the
caller's to read: it belongs to the message, not to a record.
"""

import json
from typing import Any, List, Optional

# A record that carries an `s3` object is an S3 event record whatever envelope it arrived in.
S3_RECORD_KEY = "s3"

# The SNS envelope an SQS body carries after an SNS -> SQS hop.
SNS_NOTIFICATION_TYPE = "Notification"


def s3_records_from_indexer_message(message: Any) -> List[dict]:
    """The flat list of S3 event records a file-indexer message carries, in delivery order.

    `message` is the raw SNS `Message` payload (a dict or its JSON string form). Accepted shapes are
    the SQS-wrapped message (`Records[].body` -> `Notification.Message` -> S3 `Records[]`, including
    a further nested `Notification` layer), an SNS event (`Records[].Sns.Message`), an SNS envelope
    itself, a flat S3 event (`Records[]` of records carrying `s3`) and a single `{s3: ...}` record.
    Anything else -- a DynamoDB stream record, a compliance message, malformed JSON -- yields `[]`.
    """
    payload = _as_json_object(message)
    if payload is None:
        return []
    return _collect_s3_records(payload)


def _as_json_object(value: Any) -> Optional[dict]:
    """`value` as a JSON object: the dict itself, or the dict its JSON string form parses to."""
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _collect_s3_records(payload: dict) -> List[dict]:
    """Walk one envelope level of `payload` and gather the S3 records beneath it."""
    if isinstance(payload.get(S3_RECORD_KEY), dict):
        return [payload]

    if payload.get("Type") == SNS_NOTIFICATION_TYPE and "Message" in payload:
        inner = _as_json_object(payload.get("Message"))
        return _collect_s3_records(inner) if inner is not None else []

    records = payload.get("Records")
    if not isinstance(records, list):
        return []

    found: List[dict] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if isinstance(record.get(S3_RECORD_KEY), dict):
            found.append(record)
        elif "body" in record:
            inner = _as_json_object(record.get("body"))
            if inner is not None:
                found.extend(_collect_s3_records(inner))
        elif isinstance(record.get("Sns"), dict):
            inner = _as_json_object(record["Sns"].get("Message"))
            if inner is not None:
                found.extend(_collect_s3_records(inner))
    return found
