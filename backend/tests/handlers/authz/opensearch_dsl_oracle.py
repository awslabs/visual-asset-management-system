# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A small in-memory evaluator for the OpenSearch query DSL the search handler builds, and a fake
search manager answering from it.

Documents are ``_source`` dicts. A ``<field>.keyword`` reference reads ``<field>`` from the source, as
the keyword subfield holds the field's exact value; a null or absent field holds no value and an array
holds one value per element. Supported: ``match_all``, ``match_none``, ``bool`` (``must``, ``filter``,
``should`` with ``minimum_should_match``, ``must_not``), ``term``, ``terms``, ``prefix``, ``wildcard``,
``exists`` and the ``regexp`` subset the permission filters emit. Anything else raises, so a filter shape
this evaluator does not know fails the test rather than being read as a match.
"""

import copy
import re

from backend.backend.common import searchAuthzFilter as saf


def _values(source, field):
    name = field[: -len(".keyword")] if field.endswith(".keyword") else field
    value = source.get(name)
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _wildcard_regex(pattern):
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append("(?s:.*)" if ch == "*" else "(?s:.)" if ch == "?" else re.escape(ch))
        i += 1
    return re.compile("".join(out))


def _lucene_regex(pattern):
    """The Lucene regexp subset the permission filters emit -- escaped characters, unescaped
    alphanumerics and line breaks, '.', '*' and the class '[^\\n]' -- as a Python pattern for fullmatch.
    Lucene's '.' matches any character, a line break included. Anything else raises."""
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if pattern.startswith(saf._NOT_LINE_BREAK, i):
            out.append("[^\n]")
            i += len(saf._NOT_LINE_BREAK)
            continue
        if ch == ".":
            out.append("(?s:.)")
        elif ch == "*":
            out.append("*")
        elif ch.isalnum() or ch == "\n":
            out.append(re.escape(ch))
        else:
            raise AssertionError(f"unsupported regexp {pattern!r}")
        i += 1
    return re.compile("".join(out))


def matches(clause, source):
    (kind, body), = clause.items()
    if kind == "match_all":
        return True
    if kind == "match_none":
        return False
    if kind == "bool":
        unknown = set(body) - {"must", "filter", "should", "must_not", "minimum_should_match"}
        if unknown:
            raise AssertionError(f"unsupported bool keys {unknown}")
        if not all(matches(c, source) for c in body.get("must", []) + body.get("filter", [])):
            return False
        if any(matches(c, source) for c in body.get("must_not", [])):
            return False
        should = body.get("should", [])
        if should:
            required = body.get("minimum_should_match",
                                0 if (body.get("must") or body.get("filter")) else 1)
            return sum(matches(c, source) for c in should) >= required
        return True
    if kind == "term":
        (field, value), = body.items()
        return value in _values(source, field)
    if kind == "terms":
        (field, values), = body.items()
        return any(v in _values(source, field) for v in values)
    if kind == "prefix":
        (field, value), = body.items()
        return any(isinstance(v, str) and v.startswith(value) for v in _values(source, field))
    if kind == "wildcard":
        (field, pattern), = body.items()
        regex = _wildcard_regex(pattern)
        return any(isinstance(v, str) and regex.fullmatch(v) for v in _values(source, field))
    if kind == "exists":
        return bool(_values(source, body["field"]))
    if kind == "regexp":
        (field, pattern), = body.items()
        regex = _lucene_regex(pattern)
        return any(isinstance(v, str) and regex.fullmatch(v) for v in _values(source, field))
    raise AssertionError(f"unsupported clause {kind}")


class FakeSearchManager:
    """Answers search_dual_index from in-memory asset and file sources, evaluating each query's
    clause, size and aggregations, and merging the two answers as DualIndexSearchManager does."""

    def __init__(self, search_module, asset_sources, file_sources, refuse_first=False):
        self._module = search_module
        self.indexes = {"asset": asset_sources, "file": file_sources}
        self.queries = []
        self._refuse_next = refuse_first

    def is_available(self):
        return True

    def _answer(self, query, index_type):
        sources = self.indexes[index_type]
        matched = [s for s in sources if matches(query.get("query", {"match_all": {}}), s)]
        hits = [{"_id": s["_id"], "_score": 1.0, "_index_type": index_type,
                 "_source": {k: v for k, v in s.items() if k != "_id"}}
                for s in matched[: query.get("size", 10)]]
        aggregations = {}
        for name, aggregation in (query.get("aggs") or {}).items():
            within = [s for s in matched if matches(aggregation["filter"], s)]
            result = {"doc_count": len(within)}
            for sub_name, sub in (aggregation.get("aggs") or {}).items():
                counts = {}
                for s in within:
                    for value in _values(s, sub["terms"]["field"]):
                        counts[value] = counts.get(value, 0) + 1
                result[sub_name] = {"buckets": [{"key": k, "doc_count": c} for k, c in counts.items()]}
            aggregations[name] = result
        return {"took": 1, "timed_out": False,
                "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
                "hits": {"hits": hits, "total": {"value": len(matched), "relation": "eq"}},
                "aggregations": aggregations}

    def search_dual_index(self, asset_query, file_query, entity_types):
        self.queries.append((copy.deepcopy(asset_query), copy.deepcopy(file_query)))
        if self._refuse_next:
            self._refuse_next = False
            raise self._module.VAMSGeneralErrorResponse("Error executing search query")
        manager = self._module.DualIndexSearchManager.__new__(self._module.DualIndexSearchManager)
        manager.client = type("_Client", (), {})()
        manager.asset_index, manager.file_index = "asset", "file"
        answers = {"asset": self._answer(asset_query, "asset"), "file": self._answer(file_query, "file")}
        manager.client.search = lambda body, index: answers[index]
        manager.is_available = lambda: True
        return manager.search_dual_index(asset_query, file_query, entity_types)
