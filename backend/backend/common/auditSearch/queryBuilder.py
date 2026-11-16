# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Validating an audit search request and building its CloudWatch Logs Insights query.

The caller never sends query text. A request is a set of named, typed filters. :func:`validate_filters`
turns it into a :class:`SearchFilters` of validated values, and :func:`build_query` turns that into the
query text. Nothing from the caller reaches the text except a value that passed its class's validation
and the safe-literal set, written as an escaped regular expression literal. A value that is valid but
outside the safe-literal set is not pushed into the query; it is applied on the parsed rows only, and
the plan carries a ``widerScan`` warning.

Logs Insights narrows the candidates and the server decides: ``recordMatcher.row_matches`` applies every
filter again to each parsed, redacted row.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from common.auditActionTypes import AuditLogGroup
from common.auditActions import audit_spec_for_action
from common.auditLogGroups import AUDIT_LOG_GROUP_PREFIX_PATTERN, audit_log_group
from common.auditSearch import containsFilter
from common.auditSearch.redaction import UNSAFE_CHARS
from common.auditSearch.rowModel import OUTCOMES

# ---------------------------------------------------------------------------------------------------
# Constants, with the reason for each value
# ---------------------------------------------------------------------------------------------------

# Every list filter takes at most this many values: a longer list is a wider scan and a longer query
# for no reviewer need.
MAX_VALUES_PER_FILTER = 20

# The query keeps the first bytes of each message. The action-record cap is 32 KB (``AUDIT_RECORD_MAX_BYTES``
# in customLogging/auditLogging.py), and the other groups cap at ``AUDIT_EVENT_MAX_MESSAGE_BYTES``; the
# head is the action-record cap plus room for the prefix, which sits inside that cap anyway.
HEAD_BYTES = 36 * 1024

# The ceilings of the configurable limits. The configuration (a later change) may lower a limit.
DEFAULT_MAX_WINDOW_DAYS = 31
CEILING_MAX_WINDOW_DAYS = 366
DEFAULT_MAX_ROWS = 1000
CEILING_MAX_ROWS = 10000

SORT_DESC = "desc"
SORT_ASC = "asc"

# A value goes into a Logs Insights literal only when it is made of these characters and is at most
# 256 characters long. Quotes, backslash, backtick, brackets, braces, whitespace and every control
# character are outside the set, so they never reach the query text. ``/`` is inside it, written ``\/``
# like every other punctuation character (the delimiter of the pattern is escaped; Logs Insights reads
# ``\/database\/`` as the literal ``/database/``, checked live), so a file path in ``entityIds`` is
# pushed into the query instead of widening the scan to the candidate limit.
SAFE_LITERAL = re.compile(r"[A-Za-z0-9._@+:|=/-]{1,256}\Z")

# Validation patterns by value class (all matched with ``\Z``, never ``$``, which allows a trailing newline).
ACTION_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
ENTITY_TYPE_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]{0,63}\Z")
ID_PATTERN = re.compile(r"[-_A-Za-z0-9]{3,64}\Z")        # databaseId, assetId
TOKEN_ID_PATTERN = re.compile(r"[A-Za-z0-9-]{8,64}\Z")   # apiKeyId, requestId (UUID-shaped)
USER_MAX_LENGTH = 256
ENTITY_ID_MAX_LENGTH = 1024

_FILTER_KEYS = frozenset({
    "logGroups", "actions", "entityTypes", "entityIds", "users", "apiKeyIds", "databaseIds", "assetIds",
    "outcomes", "requestIds", "contains", "startTime", "endTime", "includeProbes", "sort",
})

WARNING_WIDER_SCAN = "widerScan"
WARNING_NO_PREFILTER = "candidateLimitMayApply"

# What a query line may hold, checked on the finished text (defense in depth: the lines are built from
# validated values, and a line that does not match this grammar is never returned).
_FIELDS_LINE = "fields @timestamp, @log, strlen(@message) as msgLen, substr(@message, 0, %d) as msg" % HEAD_BYTES
_BODY = r"(?:[A-Za-z0-9_()|?:^]|\\[ -/:-@\[-`{-~])*"
_FILTER_LINE = re.compile(r"filter @message like /" + _BODY + r"/\Z")
_SORT_LINE = re.compile(r"sort @timestamp (?:asc|desc)\Z")
_LIMIT_LINE = re.compile(r"limit [0-9]{1,5}\Z")


class AuditSearchValidationError(ValueError):
    """A request that is refused. ``code`` is for logs and tests; the message names no value."""

    def __init__(self, code: str):
        super().__init__("Invalid audit search request")
        self.code = code


@dataclass(frozen=True)
class SearchLimits:
    """The limits a request is validated against. Defaults apply when the configuration is silent."""

    max_window_days: int = DEFAULT_MAX_WINDOW_DAYS
    max_rows: int = DEFAULT_MAX_ROWS
    max_contains_terms: int = containsFilter.DEFAULT_MAX_TERMS
    max_contains_term_length: int = containsFilter.DEFAULT_MAX_TERM_LENGTH

    def clamped(self) -> "SearchLimits":
        """The limits held within their ceilings and at least their minimum."""
        return SearchLimits(
            max_window_days=min(max(1, int(self.max_window_days)), CEILING_MAX_WINDOW_DAYS),
            max_rows=min(max(1, int(self.max_rows)), CEILING_MAX_ROWS),
            max_contains_terms=min(max(1, int(self.max_contains_terms)), containsFilter.CEILING_MAX_TERMS),
            max_contains_term_length=min(max(containsFilter.MIN_TERM_LENGTH, int(self.max_contains_term_length)),
                                         containsFilter.CEILING_MAX_TERM_LENGTH),
        )


@dataclass(frozen=True)
class SearchFilters:
    """A validated search: every value passed its class's validation."""

    group_keys: Tuple[str, ...]
    start_time: int                  # epoch seconds, UTC
    end_time: int
    actions: Tuple[str, ...] = ()
    entity_types: Tuple[str, ...] = ()
    entity_ids: Tuple[str, ...] = ()
    users: Tuple[str, ...] = ()
    api_key_ids: Tuple[str, ...] = ()
    database_ids: Tuple[str, ...] = ()
    asset_ids: Tuple[str, ...] = ()
    outcomes: Tuple[str, ...] = ()
    request_ids: Tuple[str, ...] = ()
    contains: Tuple[str, ...] = ()
    include_probes: bool = True
    sort: str = SORT_DESC


@dataclass(frozen=True)
class QueryPlan:
    """The query to run and what to tell the caller about it."""

    query_string: str
    group_keys: Tuple[str, ...]
    start_time: int
    end_time: int
    limit: int
    warnings: Tuple[str, ...]
    # True when every filter that can narrow the scan was pushed into the query. When False the
    # candidate limit applies before the server recheck, and a match may fall outside it.
    prefilter_complete: bool


# ---------------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------------

def _values(raw: Any, name: str) -> List[Any]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise AuditSearchValidationError(name + ":type")
    if len(raw) > MAX_VALUES_PER_FILTER:
        raise AuditSearchValidationError(name + ":count")
    return list(raw)


def _unique(items: Iterable[str]) -> Tuple[str, ...]:
    seen: List[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def _pattern_values(raw: Any, name: str, pattern: "re.Pattern[str]") -> Tuple[str, ...]:
    values = _values(raw, name)
    for value in values:
        if not isinstance(value, str) or not pattern.match(value):
            raise AuditSearchValidationError(name + ":value")
    return _unique(values)


def _text_values(raw: Any, name: str, max_length: int, normalize: bool) -> Tuple[str, ...]:
    values = _values(raw, name)
    checked: List[str] = []
    for value in values:
        if not isinstance(value, str):
            raise AuditSearchValidationError(name + ":value")
        text = unicodedata.normalize("NFKC", value) if normalize else value
        if not (1 <= len(text) <= max_length) or UNSAFE_CHARS.search(text):
            raise AuditSearchValidationError(name + ":value")
        checked.append(text)
    return _unique(checked)


MAX_EPOCH_SECONDS = 4102444800   # 2100-01-01T00:00:00Z


def _checked_epoch(seconds: int, name: str) -> int:
    """An epoch second inside 0 .. year 2100, the same bounds for an integer and for an ISO string."""
    if seconds < 0 or seconds > MAX_EPOCH_SECONDS:
        raise AuditSearchValidationError(name + ":range")
    return seconds


def _epoch(value: Any, name: str) -> int:
    """An epoch second from an integer or an ISO 8601 UTC string."""
    if isinstance(value, bool):
        raise AuditSearchValidationError(name + ":type")
    if isinstance(value, int):
        return _checked_epoch(value, name)
    if isinstance(value, str) and re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z\Z", value):
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError:
            raise AuditSearchValidationError(name + ":value")
        return _checked_epoch(int(parsed.astimezone(timezone.utc).timestamp()), name)
    raise AuditSearchValidationError(name + ":type")


def validate_filters(raw: Any, allowed_group_keys: Iterable[str], limits: Optional[SearchLimits] = None,
                     groups: Optional[Mapping[str, AuditLogGroup]] = None) -> SearchFilters:
    """
    Validate a search request into :class:`SearchFilters`.

    ``allowed_group_keys`` are the groups the caller may query (the registry groups that are deployed and
    that the caller is authorized for); a request that names any other group is refused with
    ``groupNotAllowed``, and one that names none searches all of them. The request may carry only the
    keys of ``_FILTER_KEYS``: a field that is not a filter (``query``, ``queryString``, ``regex``) is
    refused, so no query text can be sent. ``groups`` is the registry to resolve a key in (the merged
    registry by default). Raises :class:`AuditSearchValidationError`.
    """
    limits = (limits or SearchLimits()).clamped()
    if not isinstance(raw, Mapping):
        raise AuditSearchValidationError("body:type")
    unknown = [key for key in raw if key not in _FILTER_KEYS]
    if unknown:
        raise AuditSearchValidationError("unknownFilter")
    allowed = _unique(allowed_group_keys)

    requested = _values(raw.get("logGroups"), "logGroups")
    for key in requested:
        if not isinstance(key, str) or audit_log_group(key, groups) is None:
            raise AuditSearchValidationError("groupUnknown")
        if key not in allowed:
            raise AuditSearchValidationError("groupNotAllowed")
    group_keys = _unique(requested) or allowed
    if not group_keys:
        raise AuditSearchValidationError("groupNone")

    if raw.get("startTime") is None or raw.get("endTime") is None:
        raise AuditSearchValidationError("window:missing")
    start, end = _epoch(raw["startTime"], "startTime"), _epoch(raw["endTime"], "endTime")
    if end <= start:
        raise AuditSearchValidationError("window:order")
    if end - start > limits.max_window_days * 86400:
        raise AuditSearchValidationError("window:tooLong")

    outcomes = _values(raw.get("outcomes"), "outcomes")
    if any(not isinstance(value, str) or value not in OUTCOMES for value in outcomes):
        raise AuditSearchValidationError("outcomes:value")

    probes = raw.get("includeProbes", True)
    if not isinstance(probes, bool):
        raise AuditSearchValidationError("includeProbes:type")
    sort = raw.get("sort", SORT_DESC)
    if sort not in (SORT_ASC, SORT_DESC):
        raise AuditSearchValidationError("sort:value")

    try:
        contains = containsFilter.validate_terms(raw.get("contains"), limits.max_contains_terms,
                                                 limits.max_contains_term_length)
    except containsFilter.ContainsValidationError as error:
        raise AuditSearchValidationError("contains:" + error.code)

    return SearchFilters(
        group_keys=group_keys, start_time=start, end_time=end,
        actions=_pattern_values(raw.get("actions"), "actions", ACTION_PATTERN),
        entity_types=_pattern_values(raw.get("entityTypes"), "entityTypes", ENTITY_TYPE_PATTERN),
        entity_ids=_text_values(raw.get("entityIds"), "entityIds", ENTITY_ID_MAX_LENGTH, normalize=False),
        users=_text_values(raw.get("users"), "users", USER_MAX_LENGTH, normalize=True),
        api_key_ids=_pattern_values(raw.get("apiKeyIds"), "apiKeyIds", TOKEN_ID_PATTERN),
        database_ids=_pattern_values(raw.get("databaseIds"), "databaseIds", ID_PATTERN),
        asset_ids=_pattern_values(raw.get("assetIds"), "assetIds", ID_PATTERN),
        outcomes=_unique(outcomes),
        request_ids=_pattern_values(raw.get("requestIds"), "requestIds", TOKEN_ID_PATTERN),
        contains=contains, include_probes=probes, sort=sort,
    )


# ---------------------------------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------------------------------

def escape_literal(value: str) -> str:
    """A safe-literal value as a regular expression literal (see ``containsFilter.escape_regex_literal``).

    Raises ``ValueError`` for a value outside the safe-literal set.
    """
    if not SAFE_LITERAL.match(value):
        raise ValueError("value is not a safe literal")
    return containsFilter.escape_regex_literal(value)


def action_aliases(action: str) -> Tuple[str, ...]:
    """The action and the ``[type: ...]`` value its records carried before the canonical name existed."""
    spec = audit_spec_for_action(action)
    legacy = spec.legacy_type if spec is not None else None
    return (action, legacy) if legacy and legacy != action else (action,)


def _alternation(values: Iterable[str]) -> Optional[str]:
    """``(a|b)`` of the escaped values, or None when any value is outside the safe-literal set."""
    values = list(values)
    if not values:
        return None
    if not all(SAFE_LITERAL.match(value) for value in values):
        return None
    escaped = [escape_literal(value) for value in values]
    return escaped[0] if len(escaped) == 1 else "(" + "|".join(escaped) + ")"


def _group_prefix_clause(group_keys: Iterable[str], groups: Optional[Mapping[str, AuditLogGroup]] = None) -> str:
    prefixes: List[str] = []
    for key in group_keys:
        group = audit_log_group(key, groups)
        if group is None or not AUDIT_LOG_GROUP_PREFIX_PATTERN.match(group.prefix):
            raise AuditSearchValidationError("groupUnknown")
        prefixes.append(containsFilter.escape_regex_literal(group.prefix))
    return "^\\[(" + "|".join(prefixes) + ")\\]"


def build_query(filters: SearchFilters, limits: Optional[SearchLimits] = None,
                groups: Optional[Mapping[str, AuditLogGroup]] = None) -> QueryPlan:
    """
    Build the Logs Insights query of a validated search.

    The group prefix is anchored. Action, user, id, request id and ``contains`` become substring
    prefilters where their values are safe literals; outcome, entity type and probe handling are
    applied on the parsed rows only. Every filter is applied again on the parsed rows by
    ``recordMatcher``.
    """
    limits = (limits or SearchLimits()).clamped()
    warnings: List[str] = []
    complete = True
    lines = [_FIELDS_LINE, "filter @message like /" + _group_prefix_clause(filters.group_keys, groups) + "/"]

    pushed: List[Tuple[str, Iterable[str]]] = [
        ("actions", [alias for action in filters.actions for alias in action_aliases(action)]),
        ("users", filters.users),
        ("entityIds", filters.entity_ids),
        ("apiKeyIds", filters.api_key_ids),
        ("databaseIds", filters.database_ids),
        ("assetIds", filters.asset_ids),
        ("requestIds", filters.request_ids),
    ]
    for name, values in pushed:
        values = list(values)
        if not values:
            continue
        clause = _alternation(values)
        if clause is None:
            if WARNING_WIDER_SCAN not in warnings:
                warnings.append(WARNING_WIDER_SCAN)
            complete = False
            continue
        lines.append("filter @message like /" + clause + "/")
    for term in filters.contains:
        lines.append("filter @message like /" + containsFilter.prefilter_term(term) + "/")
    if filters.outcomes or filters.entity_types or not filters.include_probes:
        complete = False
    lines.append("sort @timestamp " + filters.sort)
    lines.append("limit %d" % limits.max_rows)

    _check_lines(lines)
    return QueryPlan("\n| ".join(lines), tuple(filters.group_keys), filters.start_time, filters.end_time,
                     limits.max_rows, tuple(warnings), complete)


def _check_lines(lines: List[str]) -> None:
    """Refuse a query whose line does not match the grammar the builder emits."""
    for index, line in enumerate(lines):
        if index == 0:
            ok = line == _FIELDS_LINE
        else:
            ok = bool(_FILTER_LINE.match(line) or _SORT_LINE.match(line) or _LIMIT_LINE.match(line))
        if not ok:
            raise AuditSearchValidationError("queryGrammar")


def filters_summary(filters: SearchFilters) -> Dict[str, Any]:
    """The filters as an audit record may hold them: values by name, ``contains`` as count and length."""
    return {
        "logGroups": list(filters.group_keys), "startTime": filters.start_time, "endTime": filters.end_time,
        "actions": list(filters.actions), "entityTypes": list(filters.entity_types),
        "outcomes": list(filters.outcomes), "includeProbes": filters.include_probes,
        "containsTermCount": len(filters.contains),
        "containsTermLength": sum(len(term) for term in filters.contains),
    }
