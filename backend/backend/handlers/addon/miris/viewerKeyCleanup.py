# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""DynamoDB stream consumer that deletes expired per-view Miris viewer keys.

Each per-view viewer key VAMS mints is recorded in the add-on's viewer key table under its Miris
key ID, with a `ttl` after the key's expiry. An expired key stays in the Miris workspace until it is
deleted, so when DynamoDB TTL removes the row, this handler deletes the key from Miris.

The event source mapping filters the KEYS_ONLY stream to TTL deletions: `REMOVE` records whose
`userIdentity` is the DynamoDB service. Any other record that reaches the handler is skipped.

A record whose key could not be deleted is reported in `batchItemFailures` under its
`dynamodb.SequenceNumber`, so Lambda retries from that record rather than from the start of the
batch. A failed record without a `SequenceNumber` cannot be reported on its own, so the handler
raises after the batch and Lambda retries the whole batch. A key Miris no longer has counts as
deleted, which keeps a retried record idempotent. A record whose key ID is not a UUID is logged
and skipped, since no retry can delete it.
"""
from typing import Any, Dict, List

from aws_lambda_powertools.utilities.typing import LambdaContext

from customLogging.auditLogging import audited_handler
from customLogging.logger import safeLogger

from . import mirisIntegration

logger = safeLogger(service_name="MirisViewerKeyCleanup")

_KEY_ATTRIBUTE = "mirisViewerKeyId"


def _is_ttl_removal(record: Dict[str, Any]) -> bool:
    """True for a stream record written by a DynamoDB TTL deletion."""
    identity = record.get("userIdentity") or {}
    return (
        record.get("eventName") == "REMOVE"
        and identity.get("type") == "Service"
        and identity.get("principalId") == "dynamodb.amazonaws.com"
    )


class UnreportableRecordFailure(Exception):
    """A key could not be deleted for a record that has no `SequenceNumber` to report."""


@audited_handler
def lambda_handler(event, context: LambdaContext) -> Dict[str, List[Dict[str, str]]]:
    failures: List[Dict[str, str]] = []
    unreportable_failures = 0
    for record in event.get("Records") or []:
        if not _is_ttl_removal(record):
            continue
        stream_record = record.get("dynamodb") or {}
        sequence_number = stream_record.get("SequenceNumber")
        record_ref = sequence_number or record.get("eventID")
        key_id = ((stream_record.get("Keys") or {}).get(_KEY_ATTRIBUTE) or {}).get("S")

        if not mirisIntegration.is_viewer_key_id(key_id):
            # A retry cannot repair the record, so it is skipped rather than reported.
            logger.error(
                f"Skipping stream record {record_ref}: key ID {key_id!r:.100} is not a viewer key ID"
            )
            continue
        try:
            mirisIntegration.delete_viewer_key(key_id)
            logger.info(f"Deleted Miris viewer key {key_id}")
            continue
        except mirisIntegration.MirisIntegrationError as e:
            logger.error(f"Could not delete Miris viewer key {key_id}: {e}")
        except Exception as e:
            logger.error(f"Could not delete Miris viewer key {key_id} ({type(e).__name__})")
        if sequence_number:
            failures.append({"itemIdentifier": sequence_number})
        else:
            logger.error(f"Stream record {record_ref} has no SequenceNumber; the whole batch is retried")
            unreportable_failures += 1

    if unreportable_failures:
        raise UnreportableRecordFailure(
            f"{unreportable_failures} failed stream record(s) carry no SequenceNumber"
        )
    return {"batchItemFailures": failures}
