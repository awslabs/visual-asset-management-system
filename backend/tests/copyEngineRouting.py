# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Answer the workflow output step's copy engine calls in-process (tests only).

The output step holds no grant on the copy jobs table: it invokes the copy engine
(`common.largeObjectCopy.call_engine`, the engine's "outputStep" action) for its batch, task token,
outcome and cancel work. `route_to_engine` patches the output step's `_engine` so each call runs the
REAL engine handler (`handlers.assets.largeObjectCopy.output_step`) against the given jobs table,
with the answer passed through JSON like a Lambda response. Patches a test makes on `po.loc` apply,
since the output step and the engine import the same `common.largeObjectCopy` module.
"""

import json
import os
from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

for _name, _value in (("LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE_NAME", "t-copy-jobs"),
                      ("ASSET_STORAGE_TABLE_NAME", "assetStorageTable"),
                      ("ASSET_UPLOAD_TABLE_NAME", "t-uploads"),
                      ("SEND_EMAIL_FUNCTION_NAME", "sendEmailFunction"),
                      ("PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME", "processOutput")):
    os.environ.setdefault(_name, _value)

from backend.backend.handlers.assets import largeObjectCopy as engine  # noqa: E402


class _Context:
    function_name = "largeObjectCopyEngine"


@contextmanager
def route_to_engine(po, jobs_table=None, sfn_client=None, lambda_client=None, s3_client=None,
                    calls=None):
    """Route `po._engine` to the engine's output_step. `calls` (a list) records each operation."""
    jobs_table = MagicMock() if jobs_table is None else jobs_table

    def _engine(operation, **fields):
        if calls is not None:
            calls.append((operation, fields))
        event = json.loads(json.dumps({"action": engine.loc.ACTION_OUTPUT_STEP,
                                       "operation": operation, **fields}, default=str))
        answer = engine.lambda_handler(event, _Context())
        return json.loads(json.dumps(answer, default=str))

    with ExitStack() as stack:
        stack.enter_context(patch.object(engine, "jobs_table", jobs_table))
        stack.enter_context(patch.object(engine, "sfn_client",
                                         sfn_client if sfn_client is not None else MagicMock()))
        stack.enter_context(patch.object(engine, "lambda_client",
                                         lambda_client if lambda_client is not None else MagicMock()))
        if s3_client is not None:
            stack.enter_context(patch.object(engine, "s3", s3_client))
        stack.enter_context(patch.object(po, "_engine", _engine))
        yield _engine
