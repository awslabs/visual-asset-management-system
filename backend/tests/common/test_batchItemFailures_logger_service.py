# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The shared batch item failure helper logs under the calling handler's service name.

The helper is imported by the indexing SNS queue handler and by the add-on handlers. Every
Powertools logger in a Lambda process shares one underlying logger, and the first one configured
sets the `service_name` key for every line. A helper that configures a logger of its own therefore
labels every line of a handler that imports it before creating its logger.

The suite's conftest replaces `safeLogger` with a stand-in, so the real logger is exercised in a
fresh interpreter, where the import order is the one a Lambda cold start runs.
"""

import json
import os
import subprocess
import sys
import textwrap

BACKEND_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

_SCRIPT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {src!r})
    from customLogging.logger import safeLogger
    {first}
    {second}
    caller.info("caller line")
    from common.batchItemFailures import with_batch_item_failures
    with_batch_item_failures({{}}, {{"Records": [{{"messageId": "m1"}}]}}, [{{"itemIdentifier": "m1"}}])
    """
)

_IMPORT_HELPER = "import common.batchItemFailures"
_CREATE_CALLER = 'caller = safeLogger(service_name="SnsQueuing")'


def _emitted_lines(helper_imported_first: bool):
    first, second = (
        (_IMPORT_HELPER, _CREATE_CALLER) if helper_imported_first else (_CREATE_CALLER, _IMPORT_HELPER)
    )
    script = _SCRIPT.format(src=BACKEND_SRC, first=first, second=second)
    env = {k: v for k, v in os.environ.items() if not k.startswith("POWERTOOLS_")}
    env["AWS_REGION"] = env.get("AWS_REGION", "us-east-1")
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, env=env
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _by_message(lines):
    return {line["message"]: line for line in lines}


def test_helper_imported_before_the_handler_logger_keeps_the_handler_service_name():
    """The cold-start order of the indexing SNS queue handler: helper import, then its logger."""
    lines = _by_message(_emitted_lines(helper_imported_first=True))

    assert "caller line" in lines
    assert "Reporting 1 failed record(s) for redrive" in lines
    assert {line.get("service_name") for line in lines.values()} == {"SnsQueuing"}


def test_helper_imported_after_the_handler_logger_carries_the_handler_service_name():
    lines = _by_message(_emitted_lines(helper_imported_first=False))

    assert lines["Reporting 1 failed record(s) for redrive"].get("service_name") == "SnsQueuing"
    assert lines["caller line"].get("service_name") == "SnsQueuing"
