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

CloudWatch Logs ingests events some seconds after they happen, and not in timestamp order, so a read
taken as a run ends can be empty or partial, and a read holding the newest events can still lack
older ones; capture_settled_execution_log re-reads within a deadline until two full reads in a row
find the same events, and marks a read it could not confirm that way. The readers still compare the
stored copy with a live read (held_log_lines, log_may_be_incomplete) and serve the more complete
one, since a stored copy can never hold the events logged after it was taken.
"""

import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import NamedTuple

from common.logRedaction import CLIENT_DETAIL_KEYS, SENSITIVE_KEYS, redact_log_text

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
POSSIBLY_INCOMPLETE_MARKER = (
    "[The log may be incomplete: CloudWatch Logs was still making the execution's events searchable "
    "when it was stored.]")


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


# Bytes of the stored log taken before the listed bound's own bytes. Redacting this window rather
# than the whole stored log keeps a 200-row page of 256 KB logs cheap; the margin leaves room for
# redaction to shrink the window and still fill the bound from lines inside it.
LISTED_EXECUTION_LOG_REDACTION_MARGIN_BYTES = 16 * 1024


def _quoted_value_branch(opening_backslashes):
    """A quoted value opened by a quote behind exactly ``opening_backslashes`` backslashes (None: nine
    or more), running to a line break. Inside it a quote behind at least ``2n + 1`` backslashes is
    taken as part of the value, which is how an escaped quote looks at the nesting depth the opening
    shows; any other quote ends it. Possessive repeats keep the scan linear."""
    if opening_backslashes is None:
        opening, inner = r'\\{9,}+["\']', r'\\++["\']'
    else:
        opening = r'\\{%d}["\']' % opening_backslashes
        inner = r'\\{%d,}+["\']' % (2 * opening_backslashes + 1)
    return opening + r'''(?:[^"'\n\\]|\\++(?!["'])|''' + inner + r')*+\n'


# The checks below decide whether redacting from a line start gives the same text after it as
# redacting the whole log. A redaction rule only replaces a key's value, a bearer token, a key id or
# a JWT, and never adds a line break, so the two agree on everything after the line start when no
# rule matches across that line break. A match can only cross it through a quoted value, which runs
# from its opening quote to a closing one and may run over line breaks, or through the whitespace
# around a key's delimiter. The checks over-approximate both: a quote after ``:`` or ``=`` whose
# quoted text reaches a line break anywhere before the window (this also catches redaction that would
# join two lines before it, which would change the count of omitted lines), and a last line before
# the window that ends in a key, ``bearer``, a quote or one of the delimiters ``: = , {``.
_QUOTED_VALUE_OVER_A_LINE_BREAK = re.compile(
    r'[:=]\s*+(?:' + "|".join(_quoted_value_branch(n) for n in list(range(9)) + [None]) + r')')
_LINE_KEYS_ALT = "|".join(
    re.escape(k) for k in SENSITIVE_KEYS + CLIENT_DETAIL_KEYS + ["apiKeyId", "bearer"])
_LINE_ENDS_IN_A_KEY_OR_DELIMITER = re.compile(r'(?i)(?:["\':=,{]|' + _LINE_KEYS_ALT + r')\s*\Z')


def _redaction_is_line_local_before(prefix):
    """True when redacting ``prefix`` followed by any text can neither join two of ``prefix``'s lines
    nor match across its final line break. ``prefix`` ends with a line break."""
    if _QUOTED_VALUE_OVER_A_LINE_BREAK.search(prefix):
        return False
    return not _LINE_ENDS_IN_A_KEY_OR_DELIMITER.search(prefix[-512:])


def listed_log_text(text, max_bytes=LISTED_EXECUTION_LOG_MAX_BYTES,
                    margin_bytes=LISTED_EXECUTION_LOG_REDACTION_MARGIN_BYTES):
    """The redacted, bounded copy of a stored log that an execution LIST row carries.

    Returns the same text as ``bound_log_text(redact_log_text(text), max_bytes)``, but redacts only the
    newest ``max_bytes + margin_bytes`` of a longer log, widened back to the start of the line that
    offset falls in, so every line of the window is redacted whole however long it is. The window is
    used only when no redaction match can reach it from the lines before it or join two of those
    lines, and when the bound stops at a line inside it; otherwise the whole log is redacted."""
    text = text or ""
    encoded = text.encode("utf-8")
    window_bytes = max_bytes + margin_bytes
    if len(encoded) <= window_bytes:
        return bound_log_text(redact_log_text(text), max_bytes)
    cut = encoded.rfind(b"\n", 0, len(encoded) - window_bytes) + 1
    if cut == 0:
        return bound_log_text(redact_log_text(text), max_bytes)
    prefix = encoded[:cut].decode("utf-8")
    if not _redaction_is_line_local_before(prefix):
        return bound_log_text(redact_log_text(text), max_bytes)
    omitted = 0
    match = _EARLIER_EVENTS_OMITTED_PATTERN.match(prefix[:prefix.find("\n")])
    if match:
        # The stored marker is not an event: its count carries over, and its line is not counted below.
        omitted = int(match.group(1)) - 1
    # Every line before the cut is a line the listed copy leaves out.
    omitted += prefix.count("\n")
    redacted = redact_log_text(encoded[cut:].decode("utf-8"))
    lines = redacted.split("\n")
    kept, _ = _keep_newest_lines(lines, max_bytes)
    # The whole redacted log is longer than the redacted window, so a window over the bound means the
    # whole log is over it too. When the bound reaches the window's first line, the whole log could
    # fill it from further back.
    if len(redacted.encode("utf-8")) < max_bytes or len(kept) >= len(lines):
        return bound_log_text(redact_log_text(text), max_bytes)
    return _with_marker(kept, omitted + len(lines) - len(kept))


def held_log_lines(text):
    """How many event lines a log text holds, not counting the marker lines this module adds. Used to
    compare two copies of one execution's log joined the same way: the one holding more lines is the
    more complete."""
    if not text:
        return 0
    return sum(1 for line in text.split("\n")
               if line not in (READ_STOPPED_MARKER, POSSIBLY_INCOMPLETE_MARKER)
               and not _EARLIER_EVENTS_OMITTED_PATTERN.match(line))


def log_may_be_incomplete(text):
    """True when a stored log text ends with a marker saying its read may have missed events: the
    read stopped at its page or time budget, or the settling read never saw two full reads agree."""
    if not text:
        return False
    return text.rsplit("\n", 1)[-1] in (READ_STOPPED_MARKER, POSSIBLY_INCOMPLETE_MARKER)


def capture_execution_log(logs_client, log_group_name, execution_id, start_time_ms=None,
                          max_bytes=STORED_EXECUTION_LOG_MAX_BYTES, max_pages=50, deadline=None):
    """Read the execution's log events and return ``(text, stream_name)``.

    ``max_pages`` bounds the FilterLogEvents calls and ``deadline`` (a ``time.monotonic()`` value)
    bounds the time spent following nextToken; the first call is always made. An exception from the
    first call propagates; a later call that fails ends the read with what was collected, marked as
    stopped. ``stream_name`` is the stream of the first event read, or ``""``."""
    read = _read_execution_log(
        logs_client, log_group_name, execution_id, start_time_ms, max_bytes, max_pages, deadline)
    return read.text, read.stream_name


# CloudWatch Logs makes a run's events searchable some seconds after they happen (5 to 16 seconds
# were seen for workflow runs), and not in timestamp order: a run's later events can be searchable
# before its earlier ones. So no timestamp in a read shows that the events before it are in. A
# settling read re-reads every SETTLE_INTERVAL_SECONDS and settles once two full re-reads in a row,
# the later of them taken at least SETTLE_MIN_SECONDS after the settling began, find the same number
# of events with the same total size. The read is bounded by its deadline (SETTLE_BUDGET_SECONDS
# when none is given) and by SETTLE_MAX_READS.
SETTLE_INTERVAL_SECONDS = 3
# Past the longest searchable delay seen (16 seconds), counted from the writer's last status write,
# which comes after every event logged before the writer ran.
SETTLE_MIN_SECONDS = 18
SETTLE_BUDGET_SECONDS = 30
SETTLE_MAX_READS = 1 + SETTLE_BUDGET_SECONDS // SETTLE_INTERVAL_SECONDS


def capture_settled_execution_log(logs_client, log_group_name, execution_id, start_time_ms=None,
                                  max_bytes=STORED_EXECUTION_LOG_MAX_BYTES, max_pages=50,
                                  deadline=None, settle_from=None, sleep=time.sleep):
    """``capture_execution_log`` repeated until CloudWatch Logs has made the run's events searchable.

    ``settle_from`` (``time.monotonic()``) is when the caller's last status write ended, and defaults
    to this call. Two reads agree when neither stopped at its page or time budget and both find the
    same, non-zero number of events with the same total message size, counted before redaction and
    before the stored bound, so a log over the bound is compared by what CloudWatch returned, not by
    the lines kept. The read settles on the second of two agreeing re-reads taken at least
    SETTLE_MIN_SECONDS after ``settle_from``, and returns it.

    ``deadline`` (``time.monotonic()``) bounds the waiting and each re-read's own nextToken walk; no
    wait starts that would end past it. When the deadline or SETTLE_MAX_READS is reached first, or a
    re-read fails, the read that found the most events is returned with POSSIBLY_INCOMPLETE_MARKER
    as its last line, so the logs endpoint keeps preferring a live read. A run with no event found
    returns ``""``. An exception from the first read propagates. Returns ``(text, stream_name)``."""
    if settle_from is None:
        settle_from = time.monotonic()
    if deadline is None:
        deadline = settle_from + SETTLE_BUDGET_SECONDS
    agree_from = settle_from + SETTLE_MIN_SECONDS
    largest = previous = _read_execution_log(
        logs_client, log_group_name, execution_id, start_time_ms, max_bytes, max_pages, deadline)
    reads = 1
    while reads < SETTLE_MAX_READS:
        if time.monotonic() + SETTLE_INTERVAL_SECONDS >= deadline:
            break
        sleep(SETTLE_INTERVAL_SECONDS)
        read_at = time.monotonic()
        try:
            current = _read_execution_log(
                logs_client, log_group_name, execution_id, start_time_ms, max_bytes, max_pages,
                deadline)
        except Exception:
            break
        reads += 1
        if _totals(current) > _totals(largest):
            largest = current
        if (reads >= 3 and read_at >= agree_from and current.complete and previous.complete
                and current.event_count and _totals(current) == _totals(previous)):
            return current.text, current.stream_name
        previous = current
    if not largest.text:
        return "", largest.stream_name
    return largest.text + "\n" + POSSIBLY_INCOMPLETE_MARKER, largest.stream_name


def _totals(read):
    return read.event_count, read.event_bytes


class _LogRead(NamedTuple):
    text: str
    stream_name: str
    # Events CloudWatch returned and their total message size in UTF-8 bytes, before redaction and
    # before the stored bound leaves the oldest ones out.
    event_count: int
    event_bytes: int
    # False when the read stopped at its page or time budget, or on a failed follow-up page.
    complete: bool


def _read_execution_log(logs_client, log_group_name, execution_id, start_time_ms, max_bytes,
                        max_pages, deadline):
    """One bounded read for ``capture_execution_log``, as a ``_LogRead``."""
    kwargs = {"logGroupName": log_group_name, "filterPattern": f'"{execution_id}"'}
    if start_time_ms is not None:
        kwargs["startTime"] = int(start_time_ms)
    budget = max(max_bytes - MARKER_RESERVE_BYTES, 0)

    kept = deque()
    kept_bytes = 0
    omitted = 0
    stream_name = ""
    event_count = 0
    event_bytes = 0
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
            message = str(event.get("message", "") or "")
            event_count += 1
            event_bytes += len(message.encode("utf-8"))
            line = _clip(redact_log_text(message), budget)
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
        return _LogRead("", stream_name, 0, 0, not stopped)
    if omitted:
        lines.insert(0, EARLIER_EVENTS_OMITTED_MARKER.format(count=omitted))
    if stopped:
        lines.append(READ_STOPPED_MARKER)
    return _LogRead("\n".join(lines), stream_name, event_count, event_bytes, not stopped)
