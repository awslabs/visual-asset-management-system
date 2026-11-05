# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset executions list carries a shortened copy of each row's stored execution log.

A run stores up to 256 KB of log text, and a list page holds up to 200 rows, so listing the stored
text whole could exceed the 6 MB synchronous Lambda response limit. Each listed row carries at most
the newest 16 KB, led by the same marker the stored bound uses; the stored value and the logs
endpoint keep the whole stored log."""

import functools
import json
import os
import random
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")

from backend.backend.common.logRedaction import redact_log_text  # noqa: E402
from backend.backend.common.workflows import executionLogCapture as elc  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"
ROWS = 200
LAMBDA_RESPONSE_LIMIT = 6 * 1024 * 1024
LISTED_MAX = 16 * 1024


def _stored_log(execution_id):
    """A stored log at the 256 KB store bound: the stored marker, then numbered events, newest last."""
    lines = [elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=7)]
    size = len(lines[0]) + 1
    i = 0
    while True:
        line = f'{{"id": "{i:06d}", "type": "TaskStateEntered", "execution": "{execution_id}", "pad": "{"x" * 200}"}}'
        size += len(line) + 1
        if size > elc.STORED_EXECUTION_LOG_MAX_BYTES:
            break
        lines.append(line)
        i += 1
    return "\n".join(lines), i


def _page():
    main_rows, newest = {}, {}
    input_items = []
    for n in range(ROWS):
        execution_id = f"e{n:031d}"
        text, count = _stored_log(execution_id)
        newest[execution_id] = (text.split("\n")[-1], count)
        main_rows[execution_id] = {
            "workflowExecutionId": execution_id, "workflowId": "wf", "workflowDatabaseId": "wdb",
            "executionStatus": "SUCCEEDED", "executionStartDate": "2026-11-03T10:00:00Z",
            "executionStopDate": "2026-11-03T10:05:00Z", "executionError": "",
            "executionLog": text}
        input_items.append({"workflowExecutionId": execution_id, "inputAssetFileKey": "/f.glb",
                            "databaseId": "db", "assetId": "a1"})
    items = le.build_execution_items(
        input_items=input_items, fetch_main_row=main_rows.get, describe_execution=MagicMock(),
        persist_main_row=MagicMock(), workflow_id_filter="", workflow_database_id="")
    return items, main_rows, newest


@pytest.mark.unit
class TestTheListedLogIsBounded:
    def test_a_full_page_of_full_stored_logs_stays_under_the_lambda_response_limit(self):
        items, _, _ = _page()
        assert len(items) == ROWS
        body = json.dumps({"message": {"Items": items, "filterStartDate": "2026-08-05T10:00:00Z"}})
        assert len(body.encode("utf-8")) < LAMBDA_RESPONSE_LIMIT

    def test_each_listed_log_is_at_most_16_kb_and_ends_with_the_newest_events(self):
        items, main_rows, newest = _page()
        for item in items:
            listed = item["executionLog"]
            assert len(listed.encode("utf-8")) <= LISTED_MAX
            last_line, count = newest[item["workflowExecutionId"]]
            assert listed.endswith(last_line)
            listed_lines = listed.split("\n")
            # The marker counts the store's own 7 plus the events the list left out.
            listed_events = len(listed_lines) - 1
            assert listed_lines[0] == elc.EARLIER_EVENTS_OMITTED_MARKER.format(
                count=7 + count - listed_events)

    def test_the_stored_row_keeps_the_whole_log(self):
        _, main_rows, _ = _page()
        for row in main_rows.values():
            assert len(row["executionLog"].encode("utf-8")) > 200 * 1024


@pytest.mark.unit
class TestTheLogsEndpointReturnsTheWholeStoredLog:
    def test_truncated_mode_returns_the_stored_log_unshortened(self):
        execution_id = "e" + "0" * 31
        text, _ = _stored_log(execution_id)
        main = {"workflowExecutionId": execution_id, "workflowId": "wf", "workflowDatabaseId": "wdb",
                "executionLogGroupArn": "arn:aws:logs:us-east-1:000000000000:log-group:/g:*",
                "executionLog": text, "executionError": ""}
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
                patch(f"{MOD}.authorize_execution_access", return_value=(True, "")):
            response = le.get_execution_logs({}, execution_id, {})
        assert response["statusCode"] == 200, response
        message = json.loads(response["body"])["message"]
        assert message["logsSource"] == "stored"
        assert message["executionLog"] == text


@pytest.mark.unit
class TestBoundLogText:
    def test_text_within_the_bound_is_unchanged(self):
        assert elc.bound_log_text("a\nb", 100) == "a\nb"
        assert elc.bound_log_text("", 100) == ""
        assert elc.bound_log_text(None, 100) == ""

    def test_keeps_the_newest_lines_with_a_marker(self):
        lines = [f"event {i:04d} " + "y" * 90 for i in range(100)]
        out = elc.bound_log_text("\n".join(lines), 2048)
        assert len(out.encode("utf-8")) <= 2048
        out_lines = out.split("\n")
        assert out_lines[-1] == lines[-1]
        assert out_lines[1:] == lines[100 - (len(out_lines) - 1):]
        assert out_lines[0] == elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=100 - (len(out_lines) - 1))

    def test_keeps_the_read_stopped_marker_at_the_end(self):
        lines = ["z" * 100] * 50 + [elc.READ_STOPPED_MARKER]
        out = elc.bound_log_text("\n".join(lines), 2048)
        assert out.endswith(elc.READ_STOPPED_MARKER)

    def test_a_single_oversized_newest_line_keeps_its_end(self):
        line = "start-" + "é" * 5000 + "-end"
        out = elc.bound_log_text("old\n" + line, 2048)
        assert len(out.encode("utf-8")) <= 2048
        assert out.endswith("-end")
        assert out.split("\n")[0] == elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=1)


# --- The listed copy redacts only the newest window of the stored log -------------------------------

_SECRET = "S3cr" + "Q9xZ" * 50
_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSIsInBhZCI6IiJ9." + "sIgN" * 40

# (pair as it appears in a log line, the secret the redactor must hide)
SENSITIVE_PAIRS = [
    (f'"SecretAccessKey": "{_SECRET}"', _SECRET),
    (f'\\"TaskToken\\": \\"{_SECRET}\\"', _SECRET),
    (f'\\\\\\"sfnExternalTaskToken\\\\\\": \\\\\\"{_SECRET}\\\\\\"', _SECRET),
    (f'"VamsTaskToken": {{"String": "{_SECRET}"}}', _SECRET),
    (f'"Name": "VAMS_TASK_TOKEN", "Value": "{_SECRET}"', _SECRET),
    (f"authorization={_SECRET} next", _SECRET),
    (f"Authorization: Bearer {_SECRET}", _SECRET),
    (f'"userArn": "arn:aws:iam::000000000000:user/{_SECRET}"', _SECRET),
    (f"jwt {_JWT} end", _JWT),
    ("key AKIAABCDEFGHIJKLMNOP end", "AKIAABCDEFGHIJKLMNOP"),
]

PAIR_IDS = ["json", "escaped-json", "double-escaped-json", "deadline-wrapped", "name-value", "bare",
            "bearer", "client-detail", "jwt", "access-key-id"]

SMALL_MAX, SMALL_MARGIN = 1024, 2048


@functools.lru_cache(maxsize=None)
def _filler(total, tag):
    lines = [f"{tag} event {i:05d} " + "y" * 48 for i in range(total // 60 + 2)]
    return "\n".join(lines)


def _text_with_pair_at(pair, offset_from_cut, max_bytes, margin_bytes):
    """A log whose listed-window cut falls ``offset_from_cut`` characters after the pair starts
    (negative: the pair starts that far inside the window)."""
    window = max_bytes + margin_bytes
    pre = _filler(3 * window, "old")
    head = pre + "\nctx "
    tail_len = window - (len(pair) - offset_from_cut) - len(" ctx\n")
    tail = _filler(2 * window, "new")[-tail_len:]
    text = head + pair + " ctx\n" + tail
    assert len(text) - window == len(head) + offset_from_cut
    return text


def _fragments(secret, size=8):
    return {secret[i:i + size] for i in range(len(secret) - size + 1)}


def _assert_listed_copy_hides(text, secret, **bounds):
    max_bytes = bounds.get("max_bytes", LISTED_MAX)
    out = elc.listed_log_text(text, **bounds)
    assert out == elc.bound_log_text(redact_log_text(text), max_bytes)
    assert len(out.encode("utf-8")) <= max_bytes
    leaked = [f for f in _fragments(secret) if f in out]
    assert not leaked, leaked[:3]


@pytest.mark.unit
class TestTheListedWindowNeverLeaksASecretTheCutSplits:
    @pytest.mark.parametrize("pair,secret", SENSITIVE_PAIRS, ids=PAIR_IDS)
    def test_the_cut_at_every_offset_of_the_pair(self, pair, secret):
        bounds = dict(max_bytes=SMALL_MAX, margin_bytes=SMALL_MARGIN)
        for offset in range(0, len(pair) + 1):
            text = _text_with_pair_at(pair, offset, SMALL_MAX, SMALL_MARGIN)
            _assert_listed_copy_hides(text, secret, **bounds)

    @pytest.mark.parametrize("pair,secret", SENSITIVE_PAIRS, ids=PAIR_IDS)
    def test_the_pair_at_every_offset_in_the_margin(self, pair, secret):
        bounds = dict(max_bytes=SMALL_MAX, margin_bytes=SMALL_MARGIN)
        for start in range(0, SMALL_MARGIN + 1):
            text = _text_with_pair_at(pair, -start, SMALL_MAX, SMALL_MARGIN)
            _assert_listed_copy_hides(text, secret, **bounds)

    @pytest.mark.parametrize("pair,secret", SENSITIVE_PAIRS, ids=PAIR_IDS)
    def test_the_cut_across_the_pair_at_the_real_bounds(self, pair, secret):
        offsets = sorted(set(range(0, len(pair) + 1, 7)) | {1, len(pair) - 1, len(pair)})
        for offset in offsets:
            text = _text_with_pair_at(pair, offset, LISTED_MAX,
                                      elc.LISTED_EXECUTION_LOG_REDACTION_MARGIN_BYTES)
            _assert_listed_copy_hides(text, secret)

    def test_a_window_redaction_shrinks_into_its_start_falls_back_to_the_whole_log(self):
        # Key ids shrink from 20 bytes to 10 when redacted, so a window of them drops below the bound
        # and the listed copy would reach back to the cut: the whole log is redacted instead.
        ids = " ".join("AKIA" + f"{i:016d}".replace("0", "A") for i in range(3000))
        text = _filler(300 * 1024, "old") + "\n" + ids
        with patch.object(elc, "redact_log_text", side_effect=elc.redact_log_text) as spy:
            out = elc.listed_log_text(text)
        # The window was redacted first, then the whole log: two calls, the last over the full text.
        assert spy.call_args is not None and spy.call_args.args[0] == text
        assert spy.call_count <= 2
        assert out == elc.bound_log_text(redact_log_text(text), LISTED_MAX)
        assert "AKIA" not in out

    def test_multibyte_text_at_the_cut_matches_the_whole_log_path(self):
        text = "\n".join(f"é event {i:05d} " + "ü" * 40 for i in range(6000))
        assert elc.listed_log_text(text) == elc.bound_log_text(redact_log_text(text), LISTED_MAX)

    def test_a_stored_log_matches_the_whole_log_path_with_its_marker_count(self):
        text, _ = _stored_log("e" + "1" * 31)
        assert elc.listed_log_text(text) == elc.bound_log_text(redact_log_text(text), LISTED_MAX)

    def test_a_stored_log_with_only_its_marker_line_before_the_window(self):
        marker = elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=3)
        window = LISTED_MAX + elc.LISTED_EXECUTION_LOG_REDACTION_MARGIN_BYTES
        body = _filler(2 * window, "new")[-(window + 10):]
        text = marker + "\n" + body
        assert elc.listed_log_text(text) == elc.bound_log_text(redact_log_text(text), LISTED_MAX)

    def test_short_logs_are_unchanged_by_the_window(self):
        for text in ("", "one line", f'"SecretAccessKey": "{_SECRET}"'):
            assert elc.listed_log_text(text) == elc.bound_log_text(redact_log_text(text), LISTED_MAX)
        assert elc.listed_log_text(None) == ""


# Templates for one sensitive pair around a value of any length; "{s}" is the value.
LONG_PAIR_TEMPLATES = [
    '{{"TaskToken": "{s}"}}',
    '\\"sfnExternalTaskToken\\": \\"{s}\\"',
    '"VamsTaskToken": {{"String": "{s}"}}',
    '"Name": "VAMS_TASK_TOKEN", "Value": "{s}"',
    "authorization={s} next",
    "Authorization: Bearer {s}",
    '"userAgent": "{s}"',
]
LONG_PAIR_IDS = ["json", "escaped-json", "deadline-wrapped", "name-value", "bare", "bearer",
                 "client-detail"]

# Pairs whose match runs over a line break: a quoted value with line breaks in it, and the whitespace
# between a key, its delimiter and its value.
LINE_BREAK_PAIR_TEMPLATES = [
    '{{"TaskToken": "{s}\n{s}\n{s}"}}',
    '\\"userAgent\\": \\"{s}\n{s}\\"',
    '"Name": "VAMS_TASK_TOKEN",\n"Value": "{s}"',
    '"TaskToken"\n: "{s}"',
    '"userAgent":\n "{s}"',
    "request authorization\n= {s}",
    "Authorization: Bearer\n{s}",
]
LINE_BREAK_PAIR_IDS = ["json-value", "escaped-client-value", "name-value-comma", "json-key-line",
                       "client-delimiter-line", "bare-key-line", "bearer-line"]


def _value(length):
    return ("Q9xZ" * (length // 4 + 1))[:length]


def _assert_listed_copy_equals_whole_log_path(text, max_bytes, **bounds):
    out = elc.listed_log_text(text, max_bytes=max_bytes, **bounds)
    assert out == elc.bound_log_text(redact_log_text(text), max_bytes)
    assert len(out.encode("utf-8")) <= max_bytes
    assert "Q9xZQ9xZ" not in out


@pytest.mark.unit
class TestALineLongerThanTheWindow:
    """A stored line whose value is longer than the listed window, or a match that runs over a line
    break, with the window's start at any offset from it."""

    @pytest.mark.parametrize("template", LONG_PAIR_TEMPLATES + LINE_BREAK_PAIR_TEMPLATES,
                             ids=LONG_PAIR_IDS + LINE_BREAK_PAIR_IDS)
    def test_the_long_line_at_every_distance_from_the_end(self, template):
        bounds = dict(margin_bytes=SMALL_MARGIN)
        window = SMALL_MAX + SMALL_MARGIN
        old = _filler(3 * window, "old")
        new = _filler(3 * window, "new")
        for value_length in (window // 2, window - 7, window, window + 1, 2 * window + 13):
            line = template.format(s=_value(value_length))
            for tail_length in range(0, 2 * window, 97):
                tail = ("\n" + new[-tail_length:]) if tail_length else ""
                text = old + "\n" + line + tail
                _assert_listed_copy_equals_whole_log_path(text, SMALL_MAX, **bounds)

    @pytest.mark.parametrize("template", LONG_PAIR_TEMPLATES + LINE_BREAK_PAIR_TEMPLATES,
                             ids=LONG_PAIR_IDS + LINE_BREAK_PAIR_IDS)
    def test_the_newest_line_at_the_real_bounds(self, template):
        window = LISTED_MAX + elc.LISTED_EXECUTION_LOG_REDACTION_MARGIN_BYTES
        old = _filler(200 * 1024, "old")
        for value_length in (20 * 1024, 40 * 1024, 100 * 1024):
            line = template.format(s=_value(value_length))
            for tail in ("", "\nnew event tail", "\n" + _filler(window, "new")[-(window // 3):]):
                _assert_listed_copy_equals_whole_log_path(old + "\n" + line + tail, LISTED_MAX)

    def test_a_long_line_behind_the_stored_marker(self):
        marker = elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=4)
        line = LONG_PAIR_TEMPLATES[0].format(s=_value(60 * 1024))
        for tail in ("", "\nnew event tail"):
            _assert_listed_copy_equals_whole_log_path(marker + "\n" + line + tail, LISTED_MAX)

    def test_random_logs_with_long_lines_and_matches_over_line_breaks(self):
        rng = random.Random(945)
        bounds = dict(margin_bytes=SMALL_MARGIN)
        window = SMALL_MAX + SMALL_MARGIN
        for _ in range(400):
            lines = []
            if rng.random() < 0.3:
                lines.append(elc.EARLIER_EVENTS_OMITTED_MARKER.format(count=rng.randint(1, 50)))
            for _ in range(rng.randint(1, 60)):
                roll = rng.random()
                if roll < 0.15:
                    template = rng.choice(LONG_PAIR_TEMPLATES + LINE_BREAK_PAIR_TEMPLATES)
                    lines.append(template.format(s=_value(rng.randint(1, 3 * window))))
                elif roll < 0.2:
                    lines.append("é" * rng.randint(1, window))
                else:
                    lines.append("event " + "y" * rng.randint(0, 400))
            text = "\n".join(lines)
            _assert_listed_copy_equals_whole_log_path(text, SMALL_MAX, **bounds)


SOUP_TOKENS = ['"', "'", "\\", "\\\\", "\\\\\\", ":", "=", ",", "{", "}", " ", "\n", "\n", "\t",
               "TaskToken", "authorization", "Bearer ", "userAgent", "Name", "Value", "VAMS_TASK_TOKEN",
               "String", "apiKeyId", "x:apiKeyId", "AKIAABCDEFGHIJKLMNOP", "eyJa.eyJb.c", "abc", "é",
               "sourceIp", "SecretAccessKey"]


@pytest.mark.unit
class TestLogsBuiltFromRedactionSyntax:
    def test_random_mixes_of_keys_quotes_escapes_and_line_breaks_match_the_whole_log_path(self):
        rng = random.Random(1007)
        for _ in range(1500):
            filler = "\n".join("event " + "y" * rng.randint(0, 120) for _ in range(rng.randint(0, 40)))
            soup = ["".join(rng.choice(SOUP_TOKENS) for _ in range(rng.randint(1, n))) for n in (400, 200)]
            newest = "\n".join("new " + "z" * 50 for _ in range(rng.randint(0, 30)))
            text = "\n".join([filler, soup[0], filler[:rng.randint(0, len(filler))], soup[1], newest])
            max_bytes = rng.choice([256, 600, 1024])
            margin = rng.choice([128, 512, 2048])
            assert elc.listed_log_text(text, max_bytes, margin) == elc.bound_log_text(
                redact_log_text(text), max_bytes), text


@pytest.mark.unit
class TestTheListIsCheapOnAFullPage:
    def test_200_rows_of_256_kb_logs_redact_only_the_newest_window_of_each(self):
        redacted_bytes = []

        def counting_redact(text):
            redacted_bytes.append(len(text.encode("utf-8")))
            return redact_log_text(text)

        # The handler imports the module under its Lambda path, a separate module object from `elc`.
        with patch.object(le.elc, "redact_log_text", side_effect=counting_redact):
            items, _, _ = _page()
        assert len(items) == ROWS
        window = LISTED_MAX + elc.LISTED_EXECUTION_LOG_REDACTION_MARGIN_BYTES
        # One window per row, each widened by at most one stored line (about 260 bytes here).
        assert len(redacted_bytes) == ROWS
        assert max(redacted_bytes) <= window + 1024
        assert sum(redacted_bytes) < ROWS * elc.STORED_EXECUTION_LOG_MAX_BYTES / 4

    def test_escaped_step_functions_lines_take_the_window(self):
        line = ('{"type": "TaskStateEntered", "details": {"input": "{\\"TaskToken\\": \\"<redacted>'
                '\\", \\"asset\\": {\\"name\\": \\"a \\\\\\"b\\\\\\" c\\"}}"}}')
        text = "\n".join([line] * 2000)
        redacted_bytes = []

        def counting_redact(value):
            redacted_bytes.append(len(value.encode("utf-8")))
            return redact_log_text(value)

        with patch.object(elc, "redact_log_text", side_effect=counting_redact):
            out = elc.listed_log_text(text)
        assert redacted_bytes and max(redacted_bytes) < len(text.encode("utf-8")) / 4
        assert out == elc.bound_log_text(redact_log_text(text), LISTED_MAX)
