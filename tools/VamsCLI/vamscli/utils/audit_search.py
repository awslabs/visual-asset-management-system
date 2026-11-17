"""Helpers of the 'audit' commands: filter building, time parsing, the long-poll loop, paging,
export and formatting.

Kept out of commands/audit.py so the MCP server and tests can use them without Click.

The audit log search API starts a search, answers a handle (`queryId`), and a client then polls the
handle until the search is final. The server holds each poll for up to about 20 seconds (long poll),
so a client sends about one request per wait. Rows come back only once the search is `Complete`.
Nothing here counts, hides or infers rows: whatever the API returns is what is shown, and the API
decides what the caller may see.
"""

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ..constants import (
    AUDIT_CONTAINS_ALLOWED_PATTERN,
    AUDIT_CONTAINS_MAX_LENGTH,
    AUDIT_CONTAINS_MIN_LENGTH,
    AUDIT_DEFAULT_PAGE_SIZE,
    AUDIT_DEFAULT_WAIT_TIMEOUT_SECONDS,
    AUDIT_DEFAULT_WINDOW,
    AUDIT_EXPORT_FORMATS,
    AUDIT_FINAL_STATUSES,
    AUDIT_LONG_POLL_WAIT_SECONDS,
    AUDIT_MAX_CONTAINS_TERMS,
    AUDIT_MAX_FILTER_VALUES,
    AUDIT_MAX_PAGE_SIZE,
    AUDIT_MAX_POLL_GAP_SECONDS,
    AUDIT_MIN_POLL_GAP_SECONDS,
    AUDIT_NETWORK_RETRIES,
    AUDIT_OUTCOMES,
    AUDIT_SORT_ORDERS,
)
from .exceptions import (
    APIError,
    AuditSearchFailedError,
    AuditSearchTimeoutError,
    GlobalInfrastructureError,
    InvalidAuditRequestError,
)

_CONTAINS_RE = re.compile(rf"^{AUDIT_CONTAINS_ALLOWED_PATTERN}$")
_RELATIVE_RE = re.compile(r"^(\d+)\s*([smhdw])$", re.IGNORECASE)
_RELATIVE_UNITS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}


def is_final(status: Optional[str]) -> bool:
    """True when a search status is final: no further poll changes it."""
    return status in AUDIT_FINAL_STATUSES


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def parse_time(value: str, now: Optional[datetime] = None) -> int:
    """Turn a --from / --to value into epoch seconds.

    Accepts a relative age (`90m`, `24h`, `7d`, `2w`, `30s`: that long before now), `now`, an
    ISO 8601 date or date-time (a value without a zone is read as UTC), or an integer of epoch
    seconds. Raises InvalidAuditRequestError for anything else, without echoing more than the value
    the user typed.
    """
    text = (value or '').strip()
    reference = now or datetime.now(timezone.utc)
    if not text:
        raise InvalidAuditRequestError("A time value is empty")
    if text.lower() == 'now':
        return int(reference.timestamp())
    match = _RELATIVE_RE.match(text)
    if match:
        seconds = int(match.group(1)) * _RELATIVE_UNITS[match.group(2).lower()]
        return int((reference - timedelta(seconds=seconds)).timestamp())
    if re.fullmatch(r"\d{9,11}", text):
        return int(text)
    iso = text[:-1] + '+00:00' if text.endswith(('Z', 'z')) else text
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        raise InvalidAuditRequestError(
            f"Cannot read the time '{text}'. Use a relative age such as 24h or 7d, 'now', "
            f"an ISO 8601 date or date-time, or epoch seconds.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def build_window(time_from: Optional[str], time_to: Optional[str],
                 now: Optional[datetime] = None) -> Tuple[int, int]:
    """The (startTime, endTime) of a search. `--from` defaults to AUDIT_DEFAULT_WINDOW ago and
    `--to` to now. The end must be after the start; the server checks the longest window."""
    start = parse_time(time_from or AUDIT_DEFAULT_WINDOW, now)
    end = parse_time(time_to or 'now', now)
    if end <= start:
        raise InvalidAuditRequestError("--to must be later than --from")
    return start, end


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def _values(name: str, values: Optional[Iterable[str]]) -> List[str]:
    cleaned: List[str] = []
    for value in values or ():
        text = (value or '').strip()
        if not text:
            raise InvalidAuditRequestError(f"{name} values cannot be empty")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
            raise InvalidAuditRequestError(f"{name} values cannot contain control characters")
        if text not in cleaned:
            cleaned.append(text)
    if len(cleaned) > AUDIT_MAX_FILTER_VALUES:
        raise InvalidAuditRequestError(
            f"{name} takes at most {AUDIT_MAX_FILTER_VALUES} values")
    return cleaned


def validate_contains(terms: Optional[Iterable[str]]) -> List[str]:
    """Check --contains terms against the server's limits (the server stays authoritative).

    Each term is 3 to 64 characters from letters, digits, space and `. _ : @ + / -`; at most 3
    terms, all of which must match (case-insensitive). The term itself is never echoed in the
    error: it may be the sensitive value being searched for."""
    cleaned = [(term or '').strip() for term in (terms or ())]
    if len(cleaned) > AUDIT_MAX_CONTAINS_TERMS:
        raise InvalidAuditRequestError(
            f"--contains takes at most {AUDIT_MAX_CONTAINS_TERMS} terms")
    for term in cleaned:
        if not (AUDIT_CONTAINS_MIN_LENGTH <= len(term) <= AUDIT_CONTAINS_MAX_LENGTH):
            raise InvalidAuditRequestError(
                f"Each --contains term must be {AUDIT_CONTAINS_MIN_LENGTH} to "
                f"{AUDIT_CONTAINS_MAX_LENGTH} characters")
        if not _CONTAINS_RE.match(term):
            raise InvalidAuditRequestError(
                "A --contains term may hold only letters, digits, spaces and . _ : @ + / -")
    return cleaned


def build_filters(*, groups=None, actions=None, entity_types=None, entity_ids=None, users=None,
                  api_key_ids=None, databases=None, assets=None, outcomes=None, request_ids=None,
                  contains=None, time_from: Optional[str] = None, time_to: Optional[str] = None,
                  include_probes: bool = False, order: Optional[str] = None,
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """Build the POST /audit/queries body from the command's options.

    Only filters that were given are sent. `includeProbes` is always sent (False hides the
    permission-probe records, as the web page does by default). No query text is ever built here:
    the API accepts none.
    """
    start, end = build_window(time_from, time_to, now)
    body: Dict[str, Any] = {'startTime': start, 'endTime': end}
    for key, name, given in (
        ('logGroups', '--group', groups), ('actions', '--action', actions),
        ('entityTypes', '--entity-type', entity_types), ('entityIds', '--entity-id', entity_ids),
        ('users', '--user', users), ('apiKeyIds', '--api-key-id', api_key_ids),
        ('databaseIds', '--database', databases), ('assetIds', '--asset', assets),
        ('outcomes', '--outcome', outcomes), ('requestIds', '--request-id', request_ids),
    ):
        values = _values(name, given)
        if key == 'outcomes':
            bad = [v for v in values if v not in AUDIT_OUTCOMES]
            if bad:
                raise InvalidAuditRequestError(
                    f"--outcome must be one of: {', '.join(AUDIT_OUTCOMES)}")
        if values:
            body[key] = values
    terms = validate_contains(contains)
    if terms:
        body['contains'] = terms
    body['includeProbes'] = bool(include_probes)
    if order:
        if order not in AUDIT_SORT_ORDERS:
            raise InvalidAuditRequestError(f"--order must be one of: {', '.join(AUDIT_SORT_ORDERS)}")
        body['sort'] = order
    return body


def check_page_size(page_size: Optional[int]) -> Optional[int]:
    if page_size is None:
        return None
    if not 1 <= page_size <= AUDIT_MAX_PAGE_SIZE:
        raise InvalidAuditRequestError(f"--page-size must be 1 to {AUDIT_MAX_PAGE_SIZE}")
    return page_size


# ---------------------------------------------------------------------------
# Long poll
# ---------------------------------------------------------------------------

def wait_for_audit_query(api_client, query_id: str, *, timeout: int = AUDIT_DEFAULT_WAIT_TIMEOUT_SECONDS,
                         page_size: Optional[int] = None, sort: Optional[str] = None,
                         on_update: Optional[Callable[[Dict[str, Any]], None]] = None,
                         sleep: Callable[[float], None] = time.sleep,
                         clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """Poll a search with the server long poll until its status is final; return the last page.

    Polls are sequential (the server answers a second concurrent poller at once with the current
    status). Honours a `pollAfterMs` the server sends, with a floor and a ceiling between polls,
    retries a failed poll a few times with backoff, and gives up after `timeout` seconds with
    AuditSearchTimeoutError (the search keeps running; read it later with `audit results`).

    Raises:
        AuditSearchTimeoutError: When `timeout` passes first
        AuditQueryNotFoundError / PermissionDeniedError / ...: a refusal ends the loop at once
    """
    deadline = clock() + timeout
    failures = 0
    while True:
        if clock() > deadline:
            raise AuditSearchTimeoutError(
                f"Gave up waiting after {timeout} seconds. The search keeps running; read it with "
                f"'vamscli audit results --handle {query_id}'.", query_id)
        try:
            page = api_client.get_audit_query(
                query_id, wait=AUDIT_LONG_POLL_WAIT_SECONDS, page_size=page_size, sort=sort)
        except GlobalInfrastructureError:
            raise
        except APIError:
            failures += 1
            if failures > AUDIT_NETWORK_RETRIES:
                raise
            sleep(min(AUDIT_MIN_POLL_GAP_SECONDS * (2 ** failures), 4))
            continue
        failures = 0
        if on_update:
            on_update(page)
        if is_final(page.get('status')):
            return page
        gap_ms = page.get('pollAfterMs') if isinstance(page.get('pollAfterMs'), (int, float)) else 0
        sleep(min(max(gap_ms / 1000.0, AUDIT_MIN_POLL_GAP_SECONDS), AUDIT_MAX_POLL_GAP_SECONDS))


def raise_if_not_complete(page: Dict[str, Any], query_id: str) -> None:
    """Raise AuditSearchFailedError for a final status other than Complete."""
    status = page.get('status')
    if status == 'Complete':
        return
    reasons = {
        'Failed': "The audit search failed.",
        'Cancelled': "The audit search was stopped. A stopped search returns no rows.",
        'Timeout': "The audit search passed its time limit and was stopped. "
                   "Narrow the time range or add filters.",
    }
    raise AuditSearchFailedError(reasons.get(status, f"The audit search ended as {status}."),
                                 query_id, status)


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------

def collect_rows(api_client, query_id: str, first_page: Optional[Dict[str, Any]] = None, *,
                 max_rows: int, page_size: Optional[int] = None,
                 sort: Optional[str] = None) -> Dict[str, Any]:
    """Read pages of a Complete search until `max_rows` rows are held or no page follows.

    Returns the first page's envelope (status, statistics, warnings) with `Items` holding at most
    `max_rows` rows, and `NextToken` when more rows follow. `more` is True when rows past the cap
    exist on the server."""
    page = first_page
    if page is None:
        page = api_client.get_audit_query(query_id, page_size=page_size, sort=sort)
    envelope = {k: v for k, v in page.items() if k not in ('Items', 'NextToken')}
    rows: List[Dict[str, Any]] = list(page.get('Items') or [])
    token = page.get('NextToken')
    while token and len(rows) < max_rows:
        page = api_client.get_audit_query(query_id, page_size=page_size, starting_token=token, sort=sort)
        rows.extend(page.get('Items') or [])
        token = page.get('NextToken')
    result = dict(envelope)
    if len(rows) > max_rows:
        rows = rows[:max_rows]
        # The token of the last page points past the dropped rows, so it is not a resume point.
        token = None
        result['truncatedByMaxRows'] = True
    result['Items'] = rows
    if token:
        result['NextToken'] = token
    return result


def find_record(api_client, query_id: str, record_id: str, *, page_size: Optional[int] = None,
                max_pages: int = 50) -> Optional[Dict[str, Any]]:
    """Find one row of a Complete search by its `recordId`, reading pages in order. None if absent."""
    token = None
    for _ in range(max_pages):
        page = api_client.get_audit_query(query_id, page_size=page_size, starting_token=token)
        for row in page.get('Items') or []:
            if row.get('recordId') == record_id:
                return row
        token = page.get('NextToken')
        if not token:
            return None
    return None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def check_export_format(export_format: str) -> str:
    if export_format not in AUDIT_EXPORT_FORMATS:
        raise InvalidAuditRequestError(f"--format must be one of: {', '.join(AUDIT_EXPORT_FORMATS)}")
    return export_format


def export_chunks(api_client, query_id: str, export_format: str):
    """Yield (content, chunk) for each export chunk in order. The content is written exactly as the
    API returned it: it is never parsed, re-quoted or evaluated, so a cell the API guarded against
    spreadsheet formulas stays guarded."""
    token = None
    while True:
        chunk = api_client.export_audit_query(query_id, export_format, token)
        yield chunk.get('content') or '', chunk
        token = chunk.get('NextToken')
        if not token:
            return


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_groups(result: Dict[str, Any]) -> str:
    groups = result.get('logGroups') or []
    lines: List[str] = []
    if not groups:
        lines.append("You may not search any audit log group in this deployment.")
    else:
        lines.append("Log groups you may search:")
        for group in groups:
            flags = ", ".join(group.get('sensitivity') or [])
            suffix = f"  [{flags}]" if flags else ""
            lines.append(f"  {group.get('key')}  ({group.get('label')}, prefix {group.get('prefix')}){suffix}")
            if group.get('description'):
                lines.append(f"      {group['description']}")
    actions = result.get('actions') or []
    if actions:
        lines.append("")
        lines.append("Actions:")
        for action in actions:
            lines.append(f"  {action.get('name')}  ({action.get('kind')}, "
                         f"{action.get('entityType')}, {action.get('logGroup')})")
    if result.get('entityTypes'):
        lines.append("")
        lines.append("Entity types: " + ", ".join(result['entityTypes']))
    if result.get('outcomes'):
        lines.append("Outcomes: " + ", ".join(result['outcomes']))
    limits = result.get('limits') or {}
    if limits:
        lines.append("")
        lines.append("Limits:")
        for key, value in limits.items():
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def _entity(row: Dict[str, Any]) -> str:
    entity = row.get('entity') or {}
    if entity.get('type') or entity.get('id'):
        return f"{entity.get('type') or '-'}:{entity.get('id') or '-'}"
    return '-'


def format_row_line(row: Dict[str, Any]) -> str:
    user = row.get('user') or '-'
    if row.get('onBehalfOf'):
        user = f"{user} (for {row['onBehalfOf']})"
    return (f"{row.get('timestamp') or '-'}  {row.get('outcome') or '-':<8} "
            f"{row.get('action') or row.get('legacyType') or '-'}  {_entity(row)}  {user}"
            f"  {row.get('groupKey') or '-'}")


def format_rows(page: Dict[str, Any]) -> str:
    """The text view of a page of rows, one line per row, and the paging hint."""
    rows = page.get('Items') or []
    lines: List[str] = []
    stats = page.get('statistics') or {}
    if page.get('status') and page.get('status') != 'Complete':
        lines.append(f"Status: {page['status']}")
    for row in rows:
        lines.append(format_row_line(row))
    if not rows:
        lines.append("No matching records.")
    summary = []
    if stats.get('rowCount') is not None:
        summary.append(f"{stats['rowCount']} matching records")
    if stats.get('recordsScanned') is not None:
        summary.append(f"{stats['recordsScanned']} scanned")
    if summary:
        lines.append("")
        lines.append("Search: " + ", ".join(summary))
    if stats.get('candidateLimitReached'):
        lines.append("Warning: the candidate limit was reached; narrow the time range or add "
                     "filters. A result without this notice is not proof of completeness.")
    for warning in page.get('warnings') or []:
        lines.append(f"Warning: {warning}")
    if page.get('truncatedByMaxRows'):
        lines.append("Stopped at --max-rows; more records exist. Raise --max-rows or narrow the search.")
    if page.get('NextToken'):
        lines.append(f"More rows: use --page-token {page['NextToken']}")
    return "\n".join(lines)


def format_row_detail(row: Dict[str, Any]) -> str:
    """The full text view of one row: every field the API returned, as indented JSON."""
    return json.dumps(row, indent=2, ensure_ascii=False, sort_keys=False)
