# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""No handler interpolates a whole Lambda event, stream record or image into a log message.

`safeLogger` redacts by key, so a value rendered into the message text is never redacted, and a
stream image holds every field value of its row (a metadata row even holds the field name as a
value). A handler logs `common.eventLogSummary.event_log_summary(event)` and the record's ids
instead. This scan reads every module under `backend/handlers` and fails on an f-string passed to a
logger method that renders one of the event-shaped names below, directly or through `json.dumps`,
`str` or `repr`.
"""

import ast
import os

import pytest

_HANDLERS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "backend", "handlers",
)

LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}

# Names the handlers give a whole event, a stream record or a stream image.
EVENT_NAMES = {"event", "record", "records", "event_record", "stream_record", "new_image", "old_image"}

RENDERING_CALLS = {"dumps", "str", "repr"}


def _renders_event(expression):
    if isinstance(expression, ast.Name):
        return expression.id in EVENT_NAMES
    if isinstance(expression, ast.Call) and expression.args:
        func = expression.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        return name in RENDERING_CALLS and _renders_event(expression.args[0])
    return False


def _violations(path):
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in LOG_METHODS):
            continue
        for argument in node.args:
            for part in ast.walk(argument):
                if isinstance(part, ast.FormattedValue) and _renders_event(part.value):
                    found.append(os.path.relpath(path, _HANDLERS_DIR) + ":" + str(node.lineno))
    return found


def _handler_modules():
    for root, _dirs, files in os.walk(_HANDLERS_DIR):
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(root, name)


@pytest.mark.unit
def test_no_handler_logs_a_whole_event():
    violations = [hit for path in _handler_modules() for hit in _violations(path)]
    assert not violations, (
        "log messages render a whole event, record or stream image; log "
        "event_log_summary(event) and the record ids instead: " + ", ".join(violations)
    )


@pytest.mark.unit
@pytest.mark.parametrize("source, flagged", [
    ('logger.info(f"Processing event: {json.dumps(event, default=str)}")', True),
    ('logger.warning(f"Bad record {record}")', True),
    ('logger.debug(f"image {str(new_image)}")', True),
    ('logger.info(f"Processing event: {event_log_summary(event)}")', False),
    ('logger.info(f"Asset {event[\'assetId\']} not found")', False),
    ('logger.info(f"Processed {len(event.get(\'Records\', []))} records")', False),
])
def test_the_scan_recognises_the_pattern(tmp_path, source, flagged):
    module = tmp_path / "sample.py"
    module.write_text(source + "\n")
    assert bool(_violations(str(module))) is flagged
