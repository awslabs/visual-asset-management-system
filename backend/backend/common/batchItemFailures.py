#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Partial-batch failure reporting for an SQS event-source handler.

The core indexers were fixed for exactly this defect: a failed indexing operation was swallowed and
reported as success, so the event-source mapping deleted the SQS message and the document was silently
never indexed. The fix there was a DLQ, `reportBatchItemFailures: true` on the event source, and a
`batchItemFailures` response. The Garnet add-on had the same defect, untouched.

Both halves are required and each is inert without the other. `reportBatchItemFailures` tells the
event-source mapping to READ a `batchItemFailures` key; a response that carries no such key is a
whole-batch SUCCESS, so setting the flag while returning the old response shape changes nothing at all.

Lives in `common/` rather than beside the Garnet handlers so it is importable the same way in both
contexts: a Lambda cold start and a test that loads a handler BY PATH. A sibling module reached with
a relative import (`from .batchFailures import ...`) raises
`attempted relative import with no known parent package` under path loading, which is how the Garnet
suites load these modules -- measured, after it broke 22 of them.

Mirrors the semantics in `handlers/indexing/assetIndexer.py` rather than importing from it: that
module builds its OpenSearch client and resolves its own resource names at import time, so importing
it here would pull an unrelated dependency into every cold start.
"""

from typing import Dict, List

from customLogging.logger import safeLogger

logger = safeLogger(service_name="GarnetBatchFailures")


def batch_item_identifier(record) -> str:
    """The id the event-source mapping uses to redrive one record.

    For an SQS event source that is `messageId`. The DynamoDB `SequenceNumber` fallback matches the
    core helper, so a handler wired to a stream source later behaves the same rather than silently
    reporting nothing.
    """
    if not isinstance(record, dict):
        return ""
    message_id = record.get('messageId')
    if message_id:
        return message_id
    sequence_number = (record.get('dynamodb') or {}).get('SequenceNumber')
    return sequence_number or ""


def all_batch_item_failures(event) -> List[Dict[str, str]]:
    """Every record in the event, for a failure that cannot be attributed to one of them.

    Re-processing an already-indexed record is harmless -- Garnet indexing is an upsert keyed by the
    entity id -- so redriving the whole batch is the safe direction when the cause is unknown.
    """
    failures: List[Dict[str, str]] = []
    if not isinstance(event, dict):
        return failures
    for record in (event.get('Records') or []):
        identifier = batch_item_identifier(record)
        if identifier:
            failures.append({'itemIdentifier': identifier})
    return failures


def with_batch_item_failures(response, event, failures: List[Dict[str, str]]):
    """Attach the partial-batch failure report to an event-source response.

    Applied on EVERY exit path of an event-source invocation, including the error ones: a response
    without the key is read as a clean batch, so an error path that omits it deletes the messages it
    just failed to process -- which is the original defect, reached by a different route.

    The key is added only for an event that actually carries `Records`. A direct or test invocation is
    not an event-source batch, and reporting failures for it would put a key the caller does not
    understand into an API-shaped response.
    """
    if isinstance(event, dict) and 'Records' in event:
        if failures:
            logger.warning(f"Reporting {len(failures)} failed record(s) for redrive")
        response['batchItemFailures'] = failures
    return response
