#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A log-safe one-line description of a Lambda event: its shape and counts, never its content.

An event-source Lambda (the indexers, the bucket sync, the Garnet add-on indexers) is invoked with
DynamoDB stream records, SNS/SQS envelopes or S3 notifications. A stream image carries every field
value of the row it describes, and a metadata row stores the field NAME as a value
(`metadataKey`/`metadataValue`), so the key-driven redaction in `safeLogger` cannot recognise a
sensitive field in it even when the event is logged as a structured dict. Interpolating the event
into the message bypasses redaction entirely. Handlers therefore log `event_log_summary(event)` at
entry, and the per-record identifiers (event name, database id, asset id, file key) where each
record is handled.
"""

from collections import Counter
from itertools import islice
from typing import Any

# Identifier fields of a direct (non event-source) invocation that are safe to name in a log line.
DIRECT_INVOCATION_ID_FIELDS = (
    "operation", "databaseId", "assetId", "filePath", "s3Key",
    "executionId", "workflowId", "pipelineId", "workflowDatabaseId", "pipelineDatabaseId",
)

# Distinct event sources named in one line; more are counted, not listed.
MAX_LISTED_SOURCES = 5

# A source label or identifier longer than this is cut, so a malformed event cannot grow the line.
MAX_LABEL_LENGTH = 256


def _label(value: Any) -> str:
    text = value if isinstance(value, str) else type(value).__name__
    return text if len(text) <= MAX_LABEL_LENGTH else text[:MAX_LABEL_LENGTH] + "..."


def _record_source(record: Any) -> str:
    if not isinstance(record, dict):
        return "unknown"
    # SNS records spell the key with a capital E.
    source = record.get("eventSource") or record.get("EventSource")
    return _label(source) if source else "unknown"


def exception_log_summary(error: BaseException) -> str:
    """Describe a search-service exception by class and HTTP status, never its text.

    An OpenSearch `RequestError` carries the service's reason, and for a rejected document that
    reason quotes the offending field value (the "immense term" error prints the first bytes of
    the value). The exception text and the traceback `logger.exception` adds both carry it, so a
    caller that indexes a document logs this summary with `logger.error` and no exception info.
    """
    status = getattr(error, "status_code", None)
    name = type(error).__name__
    return f"{name} (HTTP {status})" if isinstance(status, int) else name


# Returned when an event is so malformed that describing it raised. A summary must never raise:
# several handlers call it before their own try block, and the identity provider triggers sit on
# the login path.
FALLBACK_SUMMARY = "direct invocation (event could not be summarised)"

# Path parameter names listed in one line; more are counted.
MAX_LISTED_PATH_PARAMETERS = 5


def _api_request_summary(event: dict) -> str:
    """Describe an API Gateway request by route template and the size of what it carried.

    Names the route template (never the concrete path, which can hold a file key), the NAMES of
    the path parameters (at most MAX_LISTED_PATH_PARAMETERS), and counts the query parameters and
    body characters. Their values (typed search text, filter patterns, comment text) are never
    repeated.
    """
    request_context = event.get("requestContext")
    http = request_context.get("http") if isinstance(request_context, dict) else None
    http = http if isinstance(http, dict) else {}
    method = _label(http.get("method") or event.get("httpMethod") or "unknown")
    route = _label(event.get("routeKey") or event.get("resource") or "unknown")
    path_parameters = event.get("pathParameters")
    path_parameters = path_parameters if isinstance(path_parameters, dict) else {}
    path_names = sorted(_label(name) for name in islice(path_parameters, MAX_LISTED_PATH_PARAMETERS))
    path_names_text = str(path_names)
    if len(path_parameters) > MAX_LISTED_PATH_PARAMETERS:
        path_names_text += f" +{len(path_parameters) - MAX_LISTED_PATH_PARAMETERS} more"
    query = event.get("queryStringParameters")
    body = event.get("body")
    body_size = len(body) if isinstance(body, (str, bytes)) else (0 if body is None else 1)
    return (
        f"api request ({method} route={route}, pathParameters={path_names_text}, "
        f"queryParameters={len(query) if isinstance(query, dict) else 0}, body={body_size} char(s))"
    )


def event_log_summary(event: Any) -> str:
    """Describe `event` by record count and event source, or by the ids of a direct invocation.

    Never includes a record body, a stream image, an SNS message or any other field value, and
    never raises: an event it cannot describe gets FALLBACK_SUMMARY.
    """
    try:
        return _event_log_summary(event)
    except Exception:  # a summary must not break the handler that logs it
        return FALLBACK_SUMMARY


def _event_log_summary(event: Any) -> str:
    if not isinstance(event, dict):
        return f"non-object event ({type(event).__name__})"

    records = event.get("Records")
    if isinstance(records, list):
        sources = Counter(_record_source(record) for record in records)
        listed = ", ".join(f"{source}={count}" for source, count in sources.most_common(MAX_LISTED_SOURCES))
        others = len(sources) - MAX_LISTED_SOURCES
        if others > 0:
            listed += f", {others} more source(s)"
        return f"{len(records)} record(s) [{listed}]" if records else "0 record(s)"

    request_context = event.get("requestContext")
    if isinstance(request_context, dict) and (
        "routeKey" in event or "httpMethod" in event or "http" in request_context
    ):
        return _api_request_summary(event)

    trigger = event.get("triggerSource")
    if isinstance(trigger, str) and isinstance(event.get("request"), dict):
        return f"identity provider trigger ({_label(trigger)})"

    ids = [
        f"{field}={_label(event[field])}"
        for field in DIRECT_INVOCATION_ID_FIELDS
        if event.get(field) not in (None, "")
    ]
    return f"direct invocation ({', '.join(ids)})" if ids else f"direct invocation ({len(event)} field(s))"


# A key of a search query that names a clause (bool, match, term, wildcard, ...) is one of these
# shapes; every other key is a field name or a value and is only counted, never named.
_QUERY_CONTAINER_KEYS = ("bool", "must", "should", "must_not", "filter", "nested", "query", "dis_max", "queries")


# A clause deeper than this, or with more nodes than MAX_CLAUSE_NODES, is not walked further.
MAX_CLAUSE_DEPTH = 50
MAX_CLAUSE_NODES = 10_000

_QUERY_LEAF_KEYS = (
    "match", "match_phrase", "multi_match", "term", "terms", "wildcard", "prefix",
    "query_string", "simple_query_string", "exists", "range", "match_all", "regexp",
)


def query_clause_log_summary(clause: Any) -> str:
    """Describe a search query clause by clause kinds and node count, never its values.

    A simple-search clause holds every term the caller typed (names, file keys, metadata keys
    and values, free text), so a log line names the clause kinds the builder used and how many
    nodes it has. The walk is iterative, bounded in depth and node count, and visits each
    container once, so a very deep or cyclic structure cannot raise or run away.
    """
    kinds: Counter = Counter()
    nodes = 0
    truncated = False
    seen: set = set()
    stack = [(clause, 0)]
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, (dict, list)):
            continue
        if id(node) in seen:
            continue
        if depth > MAX_CLAUSE_DEPTH or nodes >= MAX_CLAUSE_NODES:
            truncated = True
            continue
        seen.add(id(node))
        if isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)
            continue
        nodes += 1
        for key, child in node.items():
            if isinstance(key, str) and (
                key in _QUERY_CONTAINER_KEYS or (isinstance(child, dict) and key in _QUERY_LEAF_KEYS)
            ):
                kinds[key] += 1
            stack.append((child, depth + 1))
    listed = ", ".join(f"{kind}={count}" for kind, count in sorted(kinds.items())) or "none"
    return f"{nodes} node(s) [{listed}]" + (" (truncated)" if truncated else "")
