# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Drive a database binding change applied in the background (`complianceBindingApply`, D161) to
its end in a test, the way the compliance trigger runs it: one run per message until the stored
apply is no longer `applying`. A test module that uses this sets `USES_BINDING_APPLY_RECORD = True`
(conftest `no_binding_apply`)."""

from handlers.compliance import complianceBindingApply as binding_apply


def drain(database_id, sqs=None, queue_url="", page_size=binding_apply.PAGE_SIZE, max_runs=200,
          deadline=float("inf"), clock=None):
    """Run the database's apply until it ends; returns the apply record as it ended (None when the
    database has none). `sqs` and `queue_url` receive the evaluations the apply queues."""
    for _ in range(max_runs):
        record = binding_apply.get_record(database_id)
        if not record or record.get("applyStatus") != binding_apply.STATUS_APPLYING:
            return record
        kwargs = {"clock": clock} if clock is not None else {}
        binding_apply.run(database_id, record["applyId"], record["runId"], deadline, sqs=sqs,
                          queue_url=queue_url, page_size=page_size, **kwargs)
    raise AssertionError(f"The binding apply of {database_id} did not end in {max_runs} runs")
