# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The server's own check of every filter on a parsed, redacted row.

Logs Insights prefilters are coarse substring matches over the raw record text, so a forged
``[user: admin]`` in an asset name, or ``"databaseId": "victim"`` in a request body, can pull a row into
the candidates. This module decides: a row matches only when each filter holds on the row's parsed
facts, which come from the prefix and the payload's documented top-level keys (``parsers``). Nested keys
and free text can never satisfy an ID, user or outcome filter.

Semantics: a filter that names several values matches any of them; different filters must all match.
"""

from typing import Iterable, List, Optional, Tuple

from common.auditSearch.containsFilter import MatchBudget, row_matches_terms
from common.auditSearch.queryBuilder import SearchFilters, action_aliases
from common.auditSearch.rowModel import PARSE_PARSED, PARSE_PARTIAL, AuditRow


def _in(value: Optional[str], allowed: Iterable[str]) -> bool:
    return value is not None and value in tuple(allowed)


def _action_matches(row: AuditRow, actions: Tuple[str, ...]) -> bool:
    if row.action is not None:
        return row.action in actions
    # A record written before the canonical names carries only its [type: ...] value.
    legacy_values = {alias for action in actions for alias in action_aliases(action)}
    return row.legacyType is not None and row.legacyType in legacy_values


def row_matches(row: AuditRow, filters: SearchFilters, budget: Optional[MatchBudget] = None) -> bool:
    """
    True when the row satisfies every filter.

    A row that could not be read in full (``partial``, ``unparsed``, ``unsupportedVersion``) matches a
    filter only through the facts its prefix gave (group, prefix user, legacy type, the allowed or
    denied flag); it never matches a filter on a payload fact it could not read.
    """
    if row.groupKey not in filters.group_keys:
        return False
    if not filters.include_probes and row.probe:
        return False
    if filters.actions and not _action_matches(row, filters.actions):
        return False
    if filters.entity_types and not _in(row.entity.get("type"), filters.entity_types):
        return False
    if filters.entity_ids and not _in(row.entity.get("id"), filters.entity_ids):
        return False
    if filters.users and not any(_in(candidate, filters.users)
                                 for candidate in (row.user, row.prefixUser, row.onBehalfOf)):
        return False
    if filters.api_key_ids and not _in(row.apiKeyId, filters.api_key_ids):
        return False
    if filters.database_ids and not _in(row.databaseId, filters.database_ids):
        return False
    if filters.asset_ids and not _in(row.assetId, filters.asset_ids):
        return False
    if filters.outcomes and not _in(row.outcome, filters.outcomes):
        return False
    if filters.request_ids and not _in(row.request.get("requestId"), filters.request_ids):
        return False
    if filters.contains:
        if row.parseStatus not in (PARSE_PARSED, PARSE_PARTIAL) and not row.prefixUser:
            return False
        return row_matches_terms(row, filters.contains, budget)
    return True


def recheck(rows: Iterable[AuditRow], filters: SearchFilters, budget: Optional[MatchBudget] = None) -> List[AuditRow]:
    """The rows that match, in order. Stops matching when the budget's deadline passes
    (``budget.exceeded`` is then set and the remaining rows are not returned).

    It returns the rows only, never a count of the rows it dropped. The Logs Insights prefilter matches
    the whole raw record, including ``changes``, ``previous`` and the values the viewer shows as
    ``<redacted>``; the recheck then drops a row whose visible fields do not match. A count of those
    rows would tell a caller how many records contain a ``contains`` term in a place the viewer hides,
    and a caller could read a hidden value out of it one guess at a time. A caller must not derive such a
    count (``len(candidates) - len(kept)``) into a response, a header, an error text or a metric
    dimension; the API pages end by visible matches only. ``budget.exceeded`` depends on the work done,
    not on what a hidden field holds, and may be reported.
    """
    return [row for row in rows if row_matches(row, filters, budget)]
