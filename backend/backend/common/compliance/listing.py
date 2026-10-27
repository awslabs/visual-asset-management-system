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

Text filters match `assetSearchText` (state rows) or `searchText` (audit entries), a lowercased
copy of the fields a person searches by, written with the row (`asset_name_attributes`,
`audit_search_text`). The `assetNameSort` attribute is the key of the asset-state table's
`DatabaseAssetNameIndex` (databaseId, assetNameSort), which lists a database's assets by name.
"""

import base64
import json
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# Upper bound of `maxItems` on every compliance listing.
MAX_LISTING_PAGE_SIZE = 200
# Queries one listing request may spend filling a page (a selective filter can match few items).
DEFAULT_READ_BUDGET = 5
# Items a filtered query evaluates per read (DynamoDB also stops at 1 MB): enough that a selective
# filter fills a page in a few reads, few enough that one read stays small.
FILTERED_READ_LIMIT = 500

INVALID_PAGINATION_TOKEN_MESSAGE = "Invalid pagination token"

# The asset-state table's index of a database's assets by name.
DATABASE_ASSET_NAME_INDEX = "DatabaseAssetNameIndex"
DATABASE_ASSET_NAME_INDEX_KEYS = ("databaseId", "assetId", "assetNameSort")
BASE_STATE_KEYS = ("databaseId", "assetId")
COMPLIANCE_STATE_INDEX_KEYS = ("databaseId", "assetId", "complianceState")


def normalized_text(value: Any) -> str:
    """The lowercased, trimmed form a text filter is matched in."""
    return str(value or "").strip().lower()


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
               read_budget: int = DEFAULT_READ_BUDGET) -> Tuple[List[Dict[str, Any]],
                                                                Optional[Dict[str, Any]], int]:
    """`(items, next_key, reads)`: up to `max_items` items of the query that pass `accept`, the key
    to resume after the last one (None when the query is exhausted), and the queries spent.

    Without a filter the query reads exactly `max_items` items; with one (`filtered`, or an
    `accept` callable) each read evaluates up to FILTERED_READ_LIMIT items and the page is filled
    across reads until it is full, the query is exhausted, or `read_budget` reads were spent. A page
    cut short by the budget still returns a token, so the client reads on with the same filter. The
    token of a full page names the last item returned (its `key_attributes`), not the read's
    LastEvaluatedKey, so no matching item between the two is skipped."""
    key_attributes = tuple(key_attributes)
    narrowing = filtered or accept is not None
    items: List[Dict[str, Any]] = []
    reads = 0
    last_key = start_key
    while True:
        kwargs = dict(query_kwargs)
        kwargs["Limit"] = max(max_items, FILTERED_READ_LIMIT) if narrowing else max_items
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        response = table.query(**kwargs)
        reads += 1
        page = response.get("Items", [])
        # Continue on the key's presence: the value form loops forever against a reader that
        # answers every query with the same key.
        has_more = "LastEvaluatedKey" in response
        evaluated_key = response["LastEvaluatedKey"] if has_more else None
        for position, item in enumerate(page):
            if accept is not None and not accept(item):
                continue
            items.append(item)
            if len(items) == max_items:
                exhausted = not has_more and position == len(page) - 1
                if exhausted:
                    return items, None, reads
                return items, {name: item[name] for name in key_attributes if name in item}, reads
        if "LastEvaluatedKey" not in response:
            return items, None, reads
        last_key = evaluated_key
        if reads >= read_budget:
            return items, last_key, reads


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
