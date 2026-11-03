# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Capture one workflow execution's CloudWatch log as the text stored on its execution record.

Every workflow state machine logs to one shared log group, so the read is scoped to the execution by
a quoted filter on its id, and anchored on its start time: FilterLogEvents spends a bounded scan
budget across the group's streams, so an unanchored read on a group with older streams returns empty
pages, or only other executions' streams, before it reaches this run's events. Those empty pages
carry a nextToken, so the read follows it until the log is exhausted or the page/time budget ends.

The stored text is bounded to STORED_EXECUTION_LOG_MAX_BYTES and keeps the NEWEST events, since a
failure is usually reported last; a marker line says how many earlier events were left out, and
another when the budget stopped the read before the end. Each event is redacted before it is
measured or stored. A read that finds no events returns an empty text.
"""

import re
import time
from collections import deque
from datetime import datetime, timezone

from common.logRedaction import redact_log_text

# Largest log text stored on an execution record. The main execution row also carries the error
# summary and its fixed fields, and its item size is capped at 400 KB.
STORED_EXECUTION_LOG_MAX_BYTES = 256 * 1024

# Largest log text an execution LIST row carries. A list page holds up to 200 rows, and a synchronous
# Lambda response is capped at 6 MB, so the listed copy is a shortened tail of the stored log; the
# execution logs endpoint returns the whole stored log.
LISTED_EXECUTION_LOG_MAX_BYTES = 16 * 1024

# Bytes held back from the bound for the marker lines.
MARKER_RESERVE_BYTES = 512

# Margin taken off the recorded start so clock skew cannot drop the first events.
LOG_CAPTURE_START_MARGIN_MS = 5 * 60 * 1000

EARLIER_EVENTS_OMITTED_MARKER = (
    "[{count} earlier log events are not stored: the stored log keeps the newest events.]")
_EARLIER_EVENTS_OMITTED_PATTERN = re.compile(
    r"^\[(\d+) earlier log events are not stored: the stored log keeps the newest events\.\]$")
READ_STOPPED_MARKER = (
    "[The log read stopped before the end of the execution's events; later events are not stored.]")


def log_capture_start_ms(execution_start_date):
    """Epoch-ms lower bound for the read from an ``executionStartDate`` (``%Y-%m-%dT%H:%M:%SZ``),
    less LOG_CAPTURE_START_MARGIN_MS. None when the value is absent or does not parse, which leaves
    the read unanchored."""
    if not execution_start_date:
        return None
    try:
        parsed = datetime.strptime(str(execution_start_date), "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return int(parsed.replace(tzinfo=timezone.utc).timestamp() * 1000) - LOG_CAPTURE_START_MARGIN_MS


def _clip(text, limit):
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")


def _clip_tail(text, limit):
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[len(encoded) - limit:].decode("utf-8", errors="ignore")


def bound_log_text(text, max_bytes=LISTED_EXECUTION_LOG_MAX_BYTES):
    """Shorten a stored log to at most ``max_bytes`` UTF-8 bytes, keeping the newest lines.

    Text within the bound is returned unchanged. Otherwise the oldest lines are left out and the
    EARLIER_EVENTS_OMITTED_MARKER line leads the result, its count adding any count the stored text's
    own marker already carried. A newest line that alone exceeds the bound keeps its end."""
    text = text or ""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    lines = text.split("\n")
    omitted = 0
    match = _EARLIER_EVENTS_OMITTED_PATTERN.match(lines[0])
    if match:
        omitted = int(match.group(1))
        lines = lines[1:]
    kept, _ = _keep_newest_lines(lines, max_bytes)
    return _with_marker(kept, omitted + len(lines) - len(kept))


def _keep_newest_lines(lines, max_bytes):
    """The newest ``lines`` that fit ``max_bytes`` less the marker reserve, and their joined size in
    UTF-8 bytes. A newest line that alone exceeds the budget keeps its end."""
    budget = max(max_bytes - MARKER_RESERVE_BYTES, 0)
    kept = deque()
    kept_bytes = 0
    for line in reversed(lines):
        if not kept:
            line = _clip_tail(line, budget)
        size = len(line.encode("utf-8")) + 1
        if kept and kept_bytes + size > budget:
            break
        kept.appendleft(line)
        kept_bytes += size
    return kept, max(kept_bytes - 1, 0)


def _with_marker(kept, omitted):
    if omitted:
        kept.appendleft(EARLIER_EVENTS_OMITTED_MARKER.format(count=omitted))
    return "\n".join(kept)


# Longest stretch of text a single redaction match is taken to cover: a labelled key (namespace
# prefixes included, at most 128 characters), its delimiters and escaping, and a value up to a JWT or
# Step Functions task token in size. A match straddling the listed window's cut lies within this many
# bytes of the cut.
REDACTION_SPAN_BYTES = 4 * 1024

# Bytes of the stored log taken before the cut, beyond the listed bound. Redacting this window rather
# than the whole stored log keeps a 200-row page of 256 KB logs cheap. The margin is four times the
# span, so the bytes the listed copy keeps sit well past any match the cut could have broken.
LISTED_EXECUTION_LOG_REDACTION_MARGIN_BYTES = 4 * REDACTION_SPAN_BYTES


def listed_log_text(text, max_bytes=LISTED_EXECUTION_LOG_MAX_BYTES,
                    margin_bytes=LISTED_EXECUTION_LOG_REDACTION_MARGIN_BYTES,
                    span_bytes=REDACTION_SPAN_BYTES):
    """The redacted, bounded copy of a stored log that an execution LIST row carries.

    Returns the same text as ``bound_log_text(redact_log_text(text), max_bytes)``, but redacts only the
    newest ``max_bytes + margin_bytes`` of a longer log. The oldest bytes of that window can hold the
    unredacted end of a match the cut broke, so the window is used only when the copy it yields keeps
    none of the window's first ``2 * span_bytes`` redacted bytes (room for the broken match plus any
    growth from replacing short values). Otherwise, as when redaction shrank the window far enough
    that the bound reaches back into its start, the whole log is redacted as before."""
    text = text or ""
    encoded = text.encode("utf-8")
    window_bytes = max_bytes + margin_bytes
    if len(encoded) <= window_bytes:
        return bound_log_text(redact_log_text(text), max_bytes)
    omitted = 0
    first_newline = encoded.find(b"\n")
    cut = len(encoded) - window_bytes
    if first_newline >= 0:
        match = _EARLIER_EVENTS_OMITTED_PATTERN.match(encoded[:first_newline].decode("utf-8", "ignore"))
        if match:
            omitted = int(match.group(1))
            # The stored marker is not an event: never cut inside it, and do not count it below.
            cut = max(cut, first_newline + 1)
            omitted -= 1
    # Every newline before the cut ends a line the listed copy leaves out; a line the cut splits is
    # the window's first line and is counted when the bound leaves it out.
    omitted += encoded[:cut].count(b"\n")
    window = encoded[cut:].decode("utf-8", errors="ignore")
    redacted = redact_log_text(window)
    lines = redacted.split("\n")
    kept, kept_bytes = _keep_newest_lines(lines, max_bytes)
    if len(redacted.encode("utf-8")) - kept_bytes < 2 * span_bytes:
        return bound_log_text(redact_log_text(text), max_bytes)
    return _with_marker(kept, omitted + len(lines) - len(kept))


def capture_execution_log(logs_client, log_group_name, execution_id, start_time_ms=None,
                          max_bytes=STORED_EXECUTION_LOG_MAX_BYTES, max_pages=50, deadline=None):
    """Read the execution's log events and return ``(text, stream_name)``.

    ``max_pages`` bounds the FilterLogEvents calls and ``deadline`` (a ``time.monotonic()`` value)
    bounds the time spent following nextToken; the first call is always made. An exception from the
    first call propagates; a later call that fails ends the read with what was collected, marked as
    stopped. ``stream_name`` is the stream of the first event read, or ``""``."""
    kwargs = {"logGroupName": log_group_name, "filterPattern": f'"{execution_id}"'}
    if start_time_ms is not None:
        kwargs["startTime"] = int(start_time_ms)
    budget = max(max_bytes - MARKER_RESERVE_BYTES, 0)

    kept = deque()
    kept_bytes = 0
    omitted = 0
    stream_name = ""
    stopped = False
    token = None
    pages = 0
    while True:
        request = dict(kwargs, nextToken=token) if token else kwargs
        if pages == 0:
            response = logs_client.filter_log_events(**request)
        else:
            try:
                response = logs_client.filter_log_events(**request)
            except Exception:
                stopped = True
                break
        pages += 1
        for event in response.get("events", []) or []:
            if not stream_name:
                stream_name = event.get("logStreamName", "") or ""
            line = _clip(redact_log_text(str(event.get("message", "") or "")), budget)
            kept.append(line)
            kept_bytes += len(line.encode("utf-8")) + 1
            while kept_bytes > budget and len(kept) > 1:
                dropped = kept.popleft()
                kept_bytes -= len(dropped.encode("utf-8")) + 1
                omitted += 1
        token = response.get("nextToken")
        if not token:
            break
        if pages >= max_pages or (deadline is not None and time.monotonic() >= deadline):
            stopped = True
            break

    lines = list(kept)
    if not lines:
        # An empty stored log is what lets the readers fall back to a live search, so a read that
        # found nothing stores nothing rather than a marker alone.
        return "", stream_name
    if omitted:
        lines.insert(0, EARLIER_EVENTS_OMITTED_MARKER.format(count=omitted))
    if stopped:
        lines.append(READ_STOPPED_MARKER)
    return "\n".join(lines), stream_name
