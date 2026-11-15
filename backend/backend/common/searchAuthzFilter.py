# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""OpenSearch filters that bound a caller's asset GET permissions on the search indexes.

Search authorizes each hit with ``CasbinEnforcer.enforce`` after OpenSearch answers, so totals and
aggregation buckets computed by OpenSearch would otherwise count documents the caller cannot read.
This module turns the caller's asset GET policy lines (``CasbinEnforcer.effective_object_policies``)
into two filters per index:

* ``upper`` admits at least every document the caller may read. Search fetches hits with it, so no
  readable record is withheld; ``enforce()`` still decides each fetched hit.
* ``lower`` admits only documents the caller may read. Totals and aggregations that cannot be exact
  are computed with it, so a count can come out low but never counts a record the caller may not read.

When every criterion translates, the two are the same clause and ``exact`` is True.

What the per-hit check decides, and so what the filters reproduce (``handlers/authz`` and
``handlers/search/search.py`` ``_is_hit_authorized``):

* The effect is ``some(allow) && !some(deny)``; a line holds when all its AND criteria hold and, when
  it has OR criteria, one of them does; a multi-value criterion holds when any value does, and a
  negating operator holds when none does.
* Constraint values are Python regular expressions for ``equals`` (``^v\\Z``), ``contains``
  (``(?s:.*)v(?s:.*)``), ``starts_with`` (``^v.*``) and ``ends_with`` (``(?s:.*)v\\Z``), and literal
  substrings (or list elements, on ``tags``) for ``is_one_of``. A value made of literal characters,
  backslash-escaped metacharacters and ``.`` (any character but a line break), optionally followed
  by ``.*``, is translated; any other pattern is left to the per-hit check. So ``.obj`` is the regular
  expression it is for Casbin (it also matches ``xobj``), and ``\\.obj`` is the literal ``.obj``.
* A field the hit document does not carry, or carries as null, is compared as ``""`` (``tags``
  missing is ``[]``, ``tags`` null is ``""``). Keyword matching is case-sensitive, as Casbin is.
* An archived asset is allowed only when the policy allows it both on its database id and on
  ``<databaseId>#deleted``. Asset documents hold the database id without the suffix and an
  ``bool_archived`` flag. Database ids match ``[-_a-zA-Z0-9]{3,63}``, so they hold no ``#`` and no
  line break.
* A file hit is decided on its parent asset row, read from the asset table: file documents copy the
  asset's name and tags only when the file itself is indexed, and carry no asset type, so on the file
  index only the database id criteria translate and the parent's archive state is unknown.
* A criterion Casbin cannot evaluate (an invalid pattern, a pattern operator on the ``tags`` list)
  raises inside ``enforce()``, which then denies the whole request, so a policy holding one has no
  lower bound beyond ``match_none``.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

MATCH_ALL: Dict[str, Any] = {"match_all": {}}
MATCH_NONE: Dict[str, Any] = {"match_none": {}}

ARCHIVED_SUFFIX = "#deleted"

ASSET_INDEX = "asset"
FILE_INDEX = "file"

# Asset object fields as each index carries them. A field mapped to None is decided from data the
# index does not hold (the file index: the parent asset row).
_INDEX_FIELDS = {
    ASSET_INDEX: {
        "databaseId": "str_databaseid.keyword",
        "assetName": "str_assetname.keyword",
        "assetType": "str_assettype.keyword",
        "tags": "list_tags.keyword",
    },
    FILE_INDEX: {
        "databaseId": "str_databaseid.keyword",
        "assetName": None,
        "assetType": None,
        "tags": None,
    },
}
_LIST_FIELDS = ("tags",)
_DATABASE_FIELD = "databaseId"

_REGEX_PATTERNS = {
    "equals": lambda v: "^" + v + r"\Z",
    "contains": lambda v: "(?s:.*)" + v + "(?s:.*)",
    "starts_with": lambda v: "^" + v + ".*",
    "ends_with": lambda v: "(?s:.*)" + v + r"\Z",
}
_POSITIVE_OPERATOR = {
    "equals": "equals",
    "contains": "contains",
    "does_not_contain": "contains",
    "starts_with": "starts_with",
    "ends_with": "ends_with",
    "is_one_of": "is_one_of",
    "is_not_one_of": "is_one_of",
}
_NEGATING_OPERATORS = ("does_not_contain", "is_not_one_of")

# Characters with a meaning in a Python regular expression. A value without any is a literal.
_REGEX_METACHARACTERS = frozenset(".^$*+?{}[]\\|()")

# Leaf clauses a request's filters may hold before translation gives up (upper match_all, lower
# match_none). Keeps a role set with very many values within OpenSearch's bool clause limit (1024).
MAX_LEAF_CLAUSES = 800

# A Lucene regexp matching a value that holds a line break (Lucene's '.' matches any character).
_HAS_LINE_BREAK = ".*\n.*"
# Lucene regexp for one character other than a line break: Python's '.' without the DOTALL flag.
_NOT_LINE_BREAK = "[^\n]"


@dataclass(frozen=True)
class SearchAuthzFilter:
    """The bounds of one index's readable documents. ``exact`` means upper and lower are equal."""

    upper: Dict[str, Any]
    lower: Dict[str, Any]
    exact: bool

    @property
    def unrestricted(self) -> bool:
        return self.exact and self.upper == MATCH_ALL


# ---------------------------------------------------------------------------------------------
# Clause construction
# ---------------------------------------------------------------------------------------------
def _and(clauses: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    kept = []
    for clause in clauses:
        if clause == MATCH_NONE:
            return MATCH_NONE
        if clause != MATCH_ALL:
            kept.append(clause)
    if not kept:
        return MATCH_ALL
    if len(kept) == 1:
        return kept[0]
    return {"bool": {"filter": kept}}


def _or(clauses: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    kept = []
    for clause in clauses:
        if clause == MATCH_ALL:
            return MATCH_ALL
        if clause != MATCH_NONE:
            kept.append(clause)
    if not kept:
        return MATCH_NONE
    if len(kept) == 1:
        return kept[0]
    return {"bool": {"should": kept, "minimum_should_match": 1}}


def _not(clause: Dict[str, Any]) -> Dict[str, Any]:
    if clause == MATCH_ALL:
        return MATCH_NONE
    if clause == MATCH_NONE:
        return MATCH_ALL
    return {"bool": {"must_not": [clause]}}


def _term(field: str, value: str) -> Dict[str, Any]:
    return {"term": {field: value}}


def _prefix(field: str, value: str) -> Dict[str, Any]:
    return MATCH_ALL if value == "" else {"prefix": {field: value}}


def _wildcard_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")


def _contains(field: str, value: str) -> Dict[str, Any]:
    return MATCH_ALL if value == "" else {"wildcard": {field: f"*{_wildcard_escape(value)}*"}}


def _ends_with(field: str, value: str) -> Dict[str, Any]:
    return MATCH_ALL if value == "" else {"wildcard": {field: f"*{_wildcard_escape(value)}"}}


def _regexp(field: str, pattern: str) -> Dict[str, Any]:
    return {"regexp": {field: pattern}}


def _lucene_pattern(tokens: List[Optional[str]]) -> str:
    """A Lucene regexp body for ``tokens``: each character literal (escaped unless alphanumeric), each
    None one character other than a line break."""
    return "".join(
        _NOT_LINE_BREAK if token is None else (token if token.isalnum() and token.isascii() else "\\" + token)
        for token in tokens
    )


def _no_line_break(field: str) -> Dict[str, Any]:
    return _not({"regexp": {field: _HAS_LINE_BREAK}})


def _has_value(field: str) -> Dict[str, Any]:
    """The field holds a non-empty string (Casbin sees missing, null and "" alike as "")."""
    return {"bool": {"filter": [{"exists": {"field": field}}], "must_not": [_term(field, "")]}}


@dataclass(frozen=True)
class _Bound:
    upper: Dict[str, Any]
    lower: Dict[str, Any]
    exact: bool
    poisoned: bool = False


def _exact(clause: Dict[str, Any]) -> _Bound:
    return _Bound(clause, clause, True)


_UNKNOWN = _Bound(MATCH_ALL, MATCH_NONE, False)
_POISONED = _Bound(MATCH_ALL, MATCH_NONE, False, True)


def _bound_and(bounds: List[_Bound]) -> _Bound:
    return _Bound(
        _and(b.upper for b in bounds), _and(b.lower for b in bounds),
        all(b.exact for b in bounds), any(b.poisoned for b in bounds),
    )


def _bound_or(bounds: List[_Bound]) -> _Bound:
    return _Bound(
        _or(b.upper for b in bounds), _or(b.lower for b in bounds),
        all(b.exact for b in bounds), any(b.poisoned for b in bounds),
    )


def _bound_not(bound: _Bound) -> _Bound:
    return _Bound(_not(bound.lower), _not(bound.upper), bound.exact, bound.poisoned)


# ---------------------------------------------------------------------------------------------
# One criterion value
# ---------------------------------------------------------------------------------------------
def _pattern_tokens(value: str) -> Optional[Tuple[List[Optional[str]], bool]]:
    """``(tokens, trailing '.*')`` for a value of literal characters, escaped metacharacters and '.',
    optionally ending in '.*'; a token is a character, or None for '.'. None for any other pattern."""
    tokens: List[Optional[str]] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\":
            if i + 1 < len(value) and value[i + 1] in _REGEX_METACHARACTERS:
                tokens.append(value[i + 1])
                i += 2
                continue
            return None
        if ch == "." and value[i + 1:] == "*":
            return tokens, True
        if ch == ".":
            tokens.append(None)
        elif ch in _REGEX_METACHARACTERS:
            return None
        else:
            tokens.append(ch)
        i += 1
    return tokens, False


def _value_form(value: str) -> Tuple[Optional[str], Any]:
    """("literal", v), ("any", "") for '.*', ("prefix", L) for a literal L followed by '.*',
    ("pattern", (tokens, trailing '.*')) for a value holding '.', or (None, value) for any other
    pattern. Escaped metacharacters are part of the literal."""
    parsed = _pattern_tokens(value)
    if parsed is None:
        return None, value
    tokens, trailing = parsed
    if any(token is None for token in tokens):
        return "pattern", (tokens, trailing)
    literal = "".join(tokens)
    if not trailing:
        return "literal", literal
    return ("any", "") if literal == "" else ("prefix", literal)


def _holds(operator: str, value: str, entity: Any) -> bool:
    """The Casbin verdict of one positive operator and value on ``entity``. Raises as Casbin does."""
    if operator in _REGEX_PATTERNS:
        return re.match(_REGEX_PATTERNS[operator](value), entity) is not None
    return value in entity


def _string_core(field: str, operator: str, value: str) -> Optional[Dict[str, Any]]:
    """The clause for a non-empty field value satisfying one positive operator and value, or None."""
    if operator == "is_one_of":
        return _contains(field, value)
    form, literal = _value_form(value)
    if form is None:
        return None
    if form == "pattern":
        tokens, trailing = literal
        body = _lucene_pattern(tokens)
        # A trailing '.*' inside a terminated match is a run of characters other than a line break
        tail = _NOT_LINE_BREAK + "*" if trailing else ""
        if operator == "equals":
            return _regexp(field, body + tail)
        if operator == "contains":
            return _regexp(field, ".*" + body + ".*")
        if operator == "starts_with":
            return _regexp(field, body + ".*")
        if operator == "ends_with":
            return _regexp(field, ".*" + body + tail)
        return None
    if operator == "equals":
        if form == "literal":
            return MATCH_NONE if literal == "" else _term(field, literal)
        if form == "any":
            return _no_line_break(field)
        if "\n" in literal:
            return None
        return _and([_prefix(field, literal), _no_line_break(field)])
    if operator == "contains":
        return _contains(field, literal)
    if operator == "starts_with":
        return _prefix(field, literal)
    if operator == "ends_with":
        if form == "prefix":
            return None
        return _ends_with(field, literal)
    return None


def _archived_database_core(field: str, operator: str, value: str) -> Optional[Dict[str, Any]]:
    """The clause, on the live database id X, for ``X#deleted`` satisfying one positive operator and
    value. X is a non-empty id without '#' or a line break, so the suffix is the only '#' in the
    compared string."""
    if operator == "is_one_of":
        form, literal = "literal", value
    else:
        form, literal = _value_form(value)
    if form is None or form == "pattern":
        return None
    if form == "any":
        return MATCH_ALL

    def starts_with(prefix: str) -> Dict[str, Any]:
        if "#" not in prefix:
            return _prefix(field, prefix)
        head, tail = prefix.split("#", 1)
        return _term(field, head) if ARCHIVED_SUFFIX.startswith("#" + tail) and head else MATCH_NONE

    def contains(text: str) -> Dict[str, Any]:
        if text in ARCHIVED_SUFFIX:
            return MATCH_ALL
        if "#" not in text:
            return _contains(field, text)
        head, tail = text.split("#", 1)
        if "#" in tail or not ARCHIVED_SUFFIX.startswith("#" + tail):
            return MATCH_NONE
        return _ends_with(field, head)

    def ends_with(text: str) -> Dict[str, Any]:
        if ARCHIVED_SUFFIX.endswith(text):
            return MATCH_ALL
        if not text.endswith(ARCHIVED_SUFFIX):
            return MATCH_NONE
        head = text[: -len(ARCHIVED_SUFFIX)]
        return MATCH_NONE if "#" in head else _ends_with(field, head)

    if operator == "equals":
        if form == "literal":
            if not literal.endswith(ARCHIVED_SUFFIX):
                return MATCH_NONE
            head = literal[: -len(ARCHIVED_SUFFIX)]
            return _term(field, head) if head and "#" not in head else MATCH_NONE
        # 'L.*' on a string without a line break is 'starts with L'
        return starts_with(literal)
    if operator == "starts_with":
        return starts_with(literal)
    if operator in ("contains", "is_one_of"):
        return contains(literal)
    if operator == "ends_with":
        return None if form == "prefix" else ends_with(literal)
    return None


def _value_bound(index_type: str, field: str, operator: str, value: str, archived_form: bool) -> _Bound:
    """The bound for one positive operator and one value of a criterion on ``field``."""
    if operator in _REGEX_PATTERNS:
        try:
            re.compile(_REGEX_PATTERNS[operator](value))
        except re.error:
            return _POISONED

    if field not in _INDEX_FIELDS[index_type]:
        # A field the hit document never carries: Casbin compares the field's default value.
        from common.constants import PERMISSION_CONSTRAINT_FIELDS
        default = PERMISSION_CONSTRAINT_FIELDS.get(field, "")
        try:
            return _exact(MATCH_ALL if _holds(operator, value, "" if default is None else default) else MATCH_NONE)
        except (re.error, TypeError):
            return _POISONED

    keyword = _INDEX_FIELDS[index_type][field]

    if field in _LIST_FIELDS:
        if operator in _REGEX_PATTERNS:
            # re.match on a list raises; only a null tags value (compared as "") would not.
            return _POISONED
        if keyword is None or value == "":
            # An empty element is in "" (null tags) but not in [] (no tags); the index cannot tell.
            return _UNKNOWN
        return _exact(_term(keyword, value))

    if keyword is None:
        return _UNKNOWN

    if field == _DATABASE_FIELD:
        # Every document a search reads passed the database pre-filter, so it holds a non-empty id.
        if archived_form:
            core = _archived_database_core(keyword, operator, value)
        else:
            core = _string_core(keyword, operator, value)
        return _UNKNOWN if core is None else _exact(core)

    core = _string_core(keyword, operator, value)
    if core is None:
        return _UNKNOWN
    holds_on_empty = _holds(operator, value, "")
    if core == MATCH_ALL and holds_on_empty:
        return _exact(MATCH_ALL)
    clause = _and([_has_value(keyword), core])
    if holds_on_empty:
        clause = _or([clause, _not(_has_value(keyword))])
    return _exact(clause)


def _criterion_bound(index_type: str, criterion: Dict[str, Any], archived_form: bool) -> _Bound:
    operator = criterion["operator"]
    positive = _POSITIVE_OPERATOR[operator]
    raw = criterion["value"]
    values = [str(entry) for entry in raw] if isinstance(raw, list) else [str(raw)]
    group = _bound_or([
        _value_bound(index_type, criterion["field"], positive, value, archived_form) for value in values
    ])
    return _bound_not(group) if operator in _NEGATING_OPERATORS else group


def _line_bound(index_type: str, line: Dict[str, Any], archived_form: bool) -> _Bound:
    parts = [_criterion_bound(index_type, c, archived_form) for c in line.get("criteriaAnd") or []]
    criteria_or = line.get("criteriaOr") or []
    if criteria_or:
        parts.append(_bound_or([_criterion_bound(index_type, c, archived_form) for c in criteria_or]))
    return _bound_and(parts)


def _policy_bound(index_type: str, lines: List[Dict[str, Any]], archived_form: bool) -> _Bound:
    allow = _bound_or([_line_bound(index_type, l, archived_form) for l in lines if l["effect"] == "allow"])
    deny = _bound_or([_line_bound(index_type, l, archived_form) for l in lines if l["effect"] == "deny"])
    bound = _bound_and([allow, _bound_not(deny)])
    if bound.poisoned:
        return _Bound(bound.upper, MATCH_NONE, False, True)
    return bound


def _same(a: _Bound, b: _Bound) -> bool:
    return (a.exact and b.exact
            and json.dumps(a.upper, sort_keys=True) == json.dumps(b.upper, sort_keys=True))


def _leaf_count(clause: Any) -> int:
    if isinstance(clause, dict):
        if "bool" in clause:
            return sum(_leaf_count(c) for key in ("filter", "should", "must", "must_not")
                       for c in clause["bool"].get(key, []))
        return 1
    return 0


def _index_filter(index_type: str, lines: List[Dict[str, Any]]) -> SearchAuthzFilter:
    live = _policy_bound(index_type, lines, archived_form=False)
    archived = _policy_bound(index_type, lines, archived_form=True)
    if _same(live, archived):
        bound = live
    elif index_type == ASSET_INDEX:
        # Live documents need the live form; archived ones need both forms.
        is_archived = _exact(_term("bool_archived", True))
        bound = _bound_and([live, _bound_or([_bound_not(is_archived), archived])])
    else:
        # The parent asset row decides; it may be live (live form) or archived (both forms).
        bound = _Bound(live.upper, _and([live.lower, archived.lower]), False)
    if _leaf_count(bound.upper) + _leaf_count(bound.lower) > MAX_LEAF_CLAUSES:
        return SearchAuthzFilter(MATCH_ALL, MATCH_NONE, False)
    exact = bound.exact and bound.upper == bound.lower
    return SearchAuthzFilter(bound.upper, bound.lower, exact)


def build_asset_search_authz_filters(lines: List[Dict[str, Any]]) -> Dict[str, SearchAuthzFilter]:
    """Filters for the asset and file search indexes from a caller's asset GET policy lines
    (``CasbinEnforcer.effective_object_policies("asset", "GET")``)."""
    return {index_type: _index_filter(index_type, lines) for index_type in (ASSET_INDEX, FILE_INDEX)}
