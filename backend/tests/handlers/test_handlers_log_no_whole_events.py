# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""No handler interpolates a whole Lambda event, stream record or image into a log message.

`safeLogger` redacts by key, so a value rendered into the message text is never redacted, and a
stream image holds every field value of its row (a metadata row even holds the field name as a
value). A handler logs `common.eventLogSummary.event_log_summary(event)` and the record's ids
instead. This scan reads every module under `backend/handlers` and fails on an f-string passed to a
logger method that renders one of the event-shaped names below, directly or through `json.dumps`,
`str` or `repr`.

A second scan (#1552) fails when a handler passes the event, a request body or the query-string
parameters to a logger call unformatted (`logger.info(event)`) or renders `event[...]`/`event.get(...)`
content for them (`event['body']`, `event.get('queryStringParameters')`) or the generated search query
clause into a message. Those carry typed request text (search terms, filter patterns, comment text) that
key-driven redaction cannot see. A handler logs `event_log_summary(event)` instead.

Scope: both scans read the AST of every module under `backend/handlers`. A request body bound to another
name than `body`, `request_body` or `requestBody` is not recognised; the behavioural tests of the handlers
named in the #1552 inventory cover the paths that matter most.
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

# Names a handler gives request content, and the event keys that hold it.
REQUEST_CONTENT_NAMES = {
    "event", "body", "request_body", "requestBody", "query_params", "queryParams",
    "query_clause", "queryClause", "query_string_parameters",
}
REQUEST_CONTENT_KEYS = {"body", "queryStringParameters", "multiValueQueryStringParameters"}


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


def _is_request_content(expression):
    """The expression is request content: a bare name above, `event['body']` or `event.get('body')`."""
    if isinstance(expression, ast.Name):
        return expression.id in REQUEST_CONTENT_NAMES
    if isinstance(expression, ast.Subscript):
        key = expression.slice
        return isinstance(key, ast.Constant) and key.value in REQUEST_CONTENT_KEYS
    if isinstance(expression, ast.Call):
        func = expression.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "get" and expression.args and isinstance(expression.args[0], ast.Constant):
            return expression.args[0].value in REQUEST_CONTENT_KEYS
        if name in RENDERING_CALLS and expression.args:
            return _is_request_content(expression.args[0])
    return False


def _content_violations(path):
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in LOG_METHODS):
            continue
        # Only a logger (`logger.x(...)`), not any object that has an `info` method.
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id.endswith("logger")):
            continue
        for argument in node.args:
            if _is_request_content(argument) or any(
                isinstance(part, ast.FormattedValue) and _is_request_content(part.value)
                for part in ast.walk(argument)
            ):
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


@pytest.mark.unit
def test_no_handler_logs_request_content():
    violations = [hit for path in _handler_modules() for hit in _content_violations(path)]
    assert not violations, (
        "log calls pass the event, a request body, query parameters or a search query clause; log "
        "event_log_summary(event) and ids instead: " + ", ".join(violations)
    )


@pytest.mark.unit
@pytest.mark.parametrize("source, flagged", [
    ("logger.info(event)", True),
    ('logger.info(f"Request body: {body}")', True),
    ("logger.debug(f\"q {event.get('queryStringParameters', {})}\")", True),
    ("logger.info(json.dumps(event['body']))", True),
    ('logger.info(f"clause {json.dumps(query_clause)}")', True),
    ('logger.info(f"Event: {event_log_summary(event)}")', False),
    ('logger.info(f"Asset {event[\'assetId\']} found")', False),
    ('logger.info("body parsed")', False),
])
def test_the_content_scan_recognises_the_pattern(tmp_path, source, flagged):
    module = tmp_path / "sample.py"
    module.write_text(source + "\n")
    assert bool(_content_violations(str(module))) is flagged
