# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Server-side filtering and paging for the compliance listings (database overview, quarantine,
cascades, audit), built for thousands of assets per database and tens of thousands of audit entries.

Every listing reads with a key condition on an index (database, database + state, event type, date)
and applies the remaining filters as a DynamoDB `FilterExpression` (or, for a per-item check such as
authorization, an `accept` callable). `query_page` reads pages until `max_items` items matched or a
read budget of `read_budget` queries is spent, whichever comes first, and returns a token that
resumes exactly after the last item returned. So a selective filter never loads a database or an
audit trail into memory, and a request reads a bounded number of pages however few rows match.

The per-row decision is bounded too. A listing that reads rows of many databases (the audit trail,
quarantine, cascades) asks the engine once per database id of the request, not once per row
(`once_per_key`): every row of a database gets the same answer on an object that names only the
database, and each refused check costs an authorization audit record. And `query_page` stops
deciding once `decision_budget` seconds were spent in `accept` (a decision that must stay per row,
on a policy that reads a field of the row itself), returning the rows found so far and a token
that resumes after the last row decided. So a reader who may read few databases gets a short, even
empty, page with a token in bounded time, however large the table, and a client keeps paging while
a token is returned.

A listing that a reader may see only part of reads only that part where an index allows it:
`merge_query_page` merges one newest-first query per partition (the audit trail of each database a
reader may read, D173) into one page in the same order, with the same read and decision budgets, so
the other partitions' rows never take a page's place.

Text filters match `assetSearchText` (state rows) or `searchText` (audit entries), a lowercased
copy of the fields a person searches by, written with the row (`asset_name_attributes`,
`audit_search_text`). The `assetNameSort` attribute is the key of the asset-state table's
`DatabaseAssetNameIndex` (databaseId, assetNameSort), which lists a database's assets by name.
Both state-row attributes serve the listings alone: every answer that returns asset-state records
builds them with `state_record_answer`, which leaves them out (PRIVATE_STATE_ATTRIBUTES).
"""

import base64
import json
import time
from collections import deque
from typing import Any, Callable, Dict, Hashable, Iterable, List, Optional, Tuple

from boto3.dynamodb.conditions import Attr

# Upper bound of `maxItems` on every compliance listing.
MAX_LISTING_PAGE_SIZE = 200
# Queries one listing request may spend filling a page (a selective filter can match few items).
DEFAULT_READ_BUDGET = 5
# Items a filtered query evaluates per read (DynamoDB also stops at 1 MB): enough that a selective
# filter fills a page in a few reads, few enough that one read stays small.
FILTERED_READ_LIMIT = 500
# The fewest items `merge_query_page` reads from one partition at a time.
MERGE_MIN_READ = 10
# Seconds one listing request may spend in `accept` deciding rows. A decision asked once per
# database id costs microseconds a row, so this binds only a decision made per row on the engine,
# where each refusal also writes an authorization audit record (about 9 ms a row when deployed).
DEFAULT_DECISION_BUDGET_SECONDS = 1.0

INVALID_PAGINATION_TOKEN_MESSAGE = "Invalid pagination token"

# The asset-state table's index of a database's assets by name.
DATABASE_ASSET_NAME_INDEX = "DatabaseAssetNameIndex"
DATABASE_ASSET_NAME_INDEX_KEYS = ("databaseId", "assetId", "assetNameSort")
BASE_STATE_KEYS = ("databaseId", "assetId")
COMPLIANCE_STATE_INDEX_KEYS = ("databaseId", "assetId", "complianceState")


def normalized_text(value: Any) -> str:
    """The lowercased, trimmed form a text filter is matched in."""
    return str(value or "").strip().lower()


def once_per_key(decide: Callable[[Hashable], Any]) -> Callable[[Hashable], bool]:
    """`decide(key)` as a bool, asked once per distinct key for the life of the returned callable
    (one request). For a decision that depends on nothing but the key, such as the engine's answer
    on a compliance object that names only a database id: a listing then asks it once per database
    of the rows it reads, not once per row."""
    answers: Dict[Hashable, bool] = {}

    def decided(key: Hashable) -> bool:
        if key not in answers:
            answers[key] = bool(decide(key))
        return answers[key]
    return decided


# The asset-state attributes kept for the listings only: the name index's sort key and what the
# `text` filter matches (`asset_name_attributes`). No API answer returns them (D163).
LISTING_STATE_ATTRIBUTES = ("assetNameSort", "assetSearchText")
# The id of the background database binding change that last wrote the record, and the time of that
# write (`complianceBindingApply`, D161), kept so a page it applies twice writes nothing twice and its
# queued evaluation is judged against the moment the binding reached the record.
BINDING_APPLY_STAMP_ATTRIBUTE = "bindingApplyId"
BINDING_APPLIED_AT_ATTRIBUTE = "bindingAppliedAt"
# Every asset-state attribute no API answer returns.
PRIVATE_STATE_ATTRIBUTES = LISTING_STATE_ATTRIBUTES + (BINDING_APPLY_STAMP_ATTRIBUTE,
                                                       BINDING_APPLIED_AT_ATTRIBUTE)


def state_record_answer(record: Dict[str, Any]) -> Dict[str, Any]:
    """An asset-state record as an API answer returns it: a copy without the attributes kept for
    the listings (PRIVATE_STATE_ATTRIBUTES)."""
    return {name: value for name, value in record.items() if name not in PRIVATE_STATE_ATTRIBUTES}


def asset_name_attributes(asset_id: str, asset_name: Optional[str]) -> Dict[str, str]:
    """The name attributes an asset-state row carries: `assetName` (shown in every listing),
    `assetNameSort` (the name index's sort key: the lowercased name, the id for an asset without
    one, then the id so names that tie keep a stable order) and `assetSearchText` (what the `text`
    filter matches: lowercased name and id)."""
    name = (asset_name or "").strip()
    sort_name = normalized_text(name) or normalized_text(asset_id)
    return {
        "assetName": name,
        "assetNameSort": f"{sort_name}#{asset_id}",
        "assetSearchText": f"{normalized_text(name)} {normalized_text(asset_id)}".strip(),
    }


def asset_text_filter(text: str) -> Tuple[Any, Callable[[Dict[str, Any]], bool]]:
    """`(condition, accept)` matching asset-state rows on a normalized `text`.

    The FilterExpression `condition` keeps the rows whose `assetSearchText` (lowercased name and
    id) contains `text`, plus the rows written before that attribute existed; `accept` matches those
    older rows on their asset id, in code, over the same bounded page fill. The id is never named in
    the FilterExpression: `assetId` is the sort key of the asset-state table, and DynamoDB refuses a
    filter on a key attribute of the table or index it queries (moto does not)."""
    condition = Attr("assetSearchText").contains(text) | Attr("assetSearchText").not_exists()

    def accept(row: Dict[str, Any]) -> bool:
        return "assetSearchText" in row or text in normalized_text(row.get("assetId"))

    return condition, accept


def audit_search_text(asset_name: Optional[str], asset_id: Optional[str], actor: Optional[str]) -> str:
    """What an audit entry's `text` filter matches: lowercased asset name, asset id and actor."""
    return " ".join(part for part in (normalized_text(asset_name), normalized_text(asset_id),
                                      normalized_text(actor)) if part)


def page_size_of(raw: Any, default: int) -> Optional[int]:
    """`maxItems` clamped to 1..MAX_LISTING_PAGE_SIZE, or None when it is not an integer."""
    try:
        size = int(raw if raw is not None else default)
    except (TypeError, ValueError):
        return None
    return max(1, min(size, MAX_LISTING_PAGE_SIZE))


def encode_token(key: Dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(key, default=str).encode("utf-8")).decode("utf-8")


def decode_token(token: str, key_attributes: Iterable[str]) -> Optional[Dict[str, str]]:
    """The key a token carries, or None when it is malformed — not Base64 JSON, not an object, or
    without a string value for one of `key_attributes` (a key from another listing would otherwise
    reach DynamoDB and fail the request as an internal error)."""
    try:
        decoded = json.loads(base64.b64decode(token, validate=True).decode("utf-8"))
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    if any(not isinstance(decoded.get(name), str) for name in key_attributes):
        return None
    return {name: decoded[name] for name in key_attributes}


def query_page(table, query_kwargs: Dict[str, Any], max_items: int, key_attributes: Iterable[str],
               start_key: Optional[Dict[str, Any]] = None, filtered: bool = False,
               accept: Optional[Callable[[Dict[str, Any]], bool]] = None,
               read_budget: int = DEFAULT_READ_BUDGET,
               read_limit: Optional[int] = None,
               decision_budget: Optional[float] = None,
               clock: Optional[Callable[[], float]] = None,
               prepare: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
               ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], int]:
    """`(items, next_key, reads)`: up to `max_items` items of the query that pass `accept`, the key
    to resume after the last one (None when the query is exhausted), and the queries spent.

    Without a filter the query reads exactly `max_items` items; with one (`filtered`, or an
    `accept` callable) each read evaluates up to FILTERED_READ_LIMIT items and the page is filled
    across reads until it is full, the query is exhausted, or `read_budget` reads were spent. A page
    cut short by the budget still returns a token, so the client reads on with the same filter. The
    token of a full page names the last item returned (its `key_attributes`), not the read's
    LastEvaluatedKey, so no matching item between the two is skipped. `read_limit` replaces the
    per-read item count for an `accept` that drops a known, small number of items (a page plus
    that number fills in one read). `prepare`, when given, is called with each read's items before
    any of them is decided (a decision that needs a row per item notes them there, so that they are
    read together, `assetAccess.AssetAccess.expect`).

    `accept` is also bounded in time: once the calls to it took `decision_budget` seconds in all
    (DEFAULT_DECISION_BUDGET_SECONDS when None; measured with `clock`, `time.monotonic` when
    None), the page ends after the item just decided, accepted or not, and the token names that
    item. At least one item is decided per read, so every request moves on."""
    key_attributes = tuple(key_attributes)
    narrowing = filtered or accept is not None
    budget = DEFAULT_DECISION_BUDGET_SECONDS if decision_budget is None else decision_budget
    clock = clock or time.monotonic
    items: List[Dict[str, Any]] = []
    reads = 0
    deciding = 0.0
    last_key = start_key
    while True:
        kwargs = dict(query_kwargs)
        if read_limit is not None:
            kwargs["Limit"] = read_limit
        else:
            kwargs["Limit"] = max(max_items, FILTERED_READ_LIMIT) if narrowing else max_items
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        response = table.query(**kwargs)
        reads += 1
        page = response.get("Items", [])
        if prepare is not None and page:
            prepare(page)
        # Continue on the key's presence: the value form loops forever against a reader that
        # answers every query with the same key.
        has_more = "LastEvaluatedKey" in response
        evaluated_key = response["LastEvaluatedKey"] if has_more else None
        for position, item in enumerate(page):
            last_of_query = not has_more and position == len(page) - 1
            if accept is not None:
                started = clock()
                accepted = accept(item)
                deciding += clock() - started
                if not accepted:
                    if deciding >= budget and not last_of_query:
                        return items, _item_key(item, key_attributes), reads
                    continue
            items.append(item)
            if len(items) == max_items or (accept is not None and deciding >= budget):
                if last_of_query:
                    return items, None, reads
                return items, _item_key(item, key_attributes), reads
        if "LastEvaluatedKey" not in response:
            return items, None, reads
        last_key = evaluated_key
        if reads >= read_budget:
            return items, last_key, reads


def merge_query_page(table, partitions: List[Tuple[str, Optional[Dict[str, Any]]]],
                     query_kwargs_for: Callable[[str], Dict[str, Any]], max_items: int,
                     key_attributes: Iterable[str], sort_attribute: str, filtered: bool = False,
                     accept: Optional[Callable[[Dict[str, Any]], bool]] = None,
                     read_budget: int = DEFAULT_READ_BUDGET,
                     decision_budget: Optional[float] = None,
                     clock: Optional[Callable[[], float]] = None,
                     prepare: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
                     ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Optional[Dict[str, Any]]]],
                                int]:
    """`(items, positions, reads)`: up to `max_items` items, newest first by `sort_attribute`,
    merged from one newest-first query per partition, as `query_page` pages one query.

    `partitions` lists `(partition, start_key)` in a fixed order (ties on `sort_attribute` go to the
    earlier partition): `start_key` is where the partition resumes, None for its newest item.
    `query_kwargs_for(partition)` gives that partition's query. The answer's `positions` maps every
    partition with items left to the key of the last item taken from it (its `key_attributes`), or
    to its `start_key` when none was; None when every partition is exhausted.

    Each partition is read once (`max(MERGE_MIN_READ, per read / partitions)` items, the per-read
    count of `query_page`), then again only when the merge needs its next item: at most
    `len(partitions) + read_budget - 1` reads, and about `read_budget` reads' worth of items. A page
    the budget ends early still returns `positions`. `accept`, `decision_budget`, `clock` and
    `prepare` are as in `query_page`."""
    key_attributes = tuple(key_attributes)
    narrowing = filtered or accept is not None
    per_read = max(max_items, FILTERED_READ_LIMIT) if narrowing else max_items
    first_read = per_read if len(partitions) <= 1 else max(
        MERGE_MIN_READ, -(-per_read // len(partitions)))
    reads_allowed = len(partitions) + max(read_budget, 1) - 1
    budget = DEFAULT_DECISION_BUDGET_SECONDS if decision_budget is None else decision_budget
    clock = clock or time.monotonic
    streams = [{"partition": partition, "buffer": deque(), "cursor": start, "more": True,
                "resume": start} for partition, start in partitions]
    items: List[Dict[str, Any]] = []
    reads = 0
    deciding = 0.0

    def read(stream, limit):
        nonlocal reads
        kwargs = dict(query_kwargs_for(stream["partition"]), Limit=limit)
        if stream["cursor"]:
            kwargs["ExclusiveStartKey"] = stream["cursor"]
        response = table.query(**kwargs)
        reads += 1
        page = response.get("Items", [])
        if prepare is not None and page:
            prepare(page)
        stream["buffer"].extend(page)
        # Continue on the key's presence, as `query_page` does.
        if "LastEvaluatedKey" in response:
            stream["cursor"] = response["LastEvaluatedKey"]
        else:
            stream["more"] = False

    def positions():
        left = {stream["partition"]: stream["resume"] for stream in streams
                if stream["buffer"] or stream["more"]}
        return left or None

    for stream in streams:
        read(stream, first_read)
    while True:
        # The newest item can be chosen only once every partition with items left has one at hand.
        for stream in streams:
            while not stream["buffer"] and stream["more"]:
                if reads >= reads_allowed:
                    return items, positions(), reads
                read(stream, per_read)
        ready = [stream for stream in streams if stream["buffer"]]
        if not ready:
            return items, None, reads
        newest = max(ready, key=lambda stream: str(stream["buffer"][0].get(sort_attribute, "")))
        item = newest["buffer"].popleft()
        newest["resume"] = _item_key(item, key_attributes)
        accepted = True
        if accept is not None:
            started = clock()
            accepted = accept(item)
            deciding += clock() - started
        if accepted:
            items.append(item)
        if len(items) == max_items or (accept is not None and deciding >= budget):
            return items, positions(), reads


def _item_key(item: Dict[str, Any], key_attributes: Tuple[str, ...]) -> Dict[str, Any]:
    """The key a page ending at `item` resumes after: its `key_attributes` (the table key and the
    keys of the index read)."""
    return {name: item[name] for name in key_attributes if name in item}


def count_items(table, query_kwargs: Dict[str, Any]) -> Tuple[int, int]:
    """`(count, reads)` of a query with `Select=COUNT`: no items are returned, and DynamoDB pages
    the count at 1 MB of evaluated data, so the reads grow with the index entries under the key
    condition, not with the item sizes returned."""
    total = 0
    reads = 0
    kwargs = dict(query_kwargs, Select="COUNT")
    while True:
        response = table.query(**kwargs)
        reads += 1
        total += response.get("Count", 0)
        if "LastEvaluatedKey" not in response:
            return total, reads
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def query_ids(table, query_kwargs: Dict[str, Any], attribute: str = "assetId") -> Tuple[List[str], int]:
    """`(values, reads)`: `attribute` of every item a query returns, read to exhaustion and
    projected on that attribute alone. DynamoDB pages it at 1 MB of evaluated data, as it pages a
    `Select=COUNT` query of the same key condition (`count_items`), so the two make the same reads;
    this one also says which items were counted."""
    values: List[str] = []
    reads = 0
    kwargs = dict(query_kwargs, ProjectionExpression="#queried",
                  ExpressionAttributeNames={"#queried": attribute})
    while True:
        response = table.query(**kwargs)
        reads += 1
        values.extend(item[attribute] for item in response.get("Items", [])
                      if isinstance(item.get(attribute), str))
        if "LastEvaluatedKey" not in response:
            return values, reads
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
