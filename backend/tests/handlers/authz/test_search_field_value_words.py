# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A field filter or metadata value of several words asks for every word, in that field (#1282).

The field-scoped values are sent as query_string text. query_string splits its text at unescaped
whitespace and, with default_operator OR, a record matched when any word did; a word after the first
also lost its field and was searched in every field. ``vamscli search simple --asset-name "gearbox
zzz*"`` therefore returned the asset named "gearbox". The model below reads the query_string forms the
builders emit as OpenSearch reads them, over the two field kinds the indexes map: analyzed text
(``str_*``, ``list_tags``: lower-cased words) and keyword values (the flat-object ``MD_`` / ``AB_``
values and a metadata key path: one term per whole value). The general keyword query keeps its
any-word match (#1273).
"""

import re

import pytest

from backend.tests.handlers.authz.search_authz_harness import load_search_module

ASSET = {
    "str_assetname": "gearbox housing",
    "list_tags": ["red car"],
    "MD_": {"Plant": "Line 3", "ProjectCode": "SBXC-325"},
}
FILE = {
    "str_key": "/operator_notes.txt",
    "str_assetname": "pump-manual",
    "MD_": {
        "genai_title": "Operator Notes - Night Shift 2026-10-29",
        "genai_keywords": "operator notes, night shift, maintenance log, conveyor belt",
    },
}


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_field_value_words")


######################## A query_string model ########################


def _tokens(text):
    """query_string tokens: split at unescaped whitespace outside quotes and parentheses."""
    tokens, current, depth, quoted, i = [], "", 0, False, 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            current += text[i:i + 2]
            i += 2
            continue
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        if ch.isspace() and not quoted and depth == 0:
            if current:
                tokens.append(current)
            current = ""
        else:
            current += ch
        i += 1
    if current:
        tokens.append(current)
    return tokens


def _field_split(token):
    """(field, value) of ``field:value`` at the first unescaped colon outside quotes, else (None, token)."""
    i, quoted = 0, False
    while i < len(token):
        ch = token[i]
        if ch == "\\":
            i += 2
            continue
        if ch == '"':
            quoted = not quoted
        if ch == "(" and not quoted:
            return None, token
        if ch == ":" and not quoted:
            return token[:i], token[i + 1:]
        i += 1
    return None, token


def _unescape(term):
    return re.sub(r"\\(.)", r"\1", term)


def _pattern(term, lower):
    out, i = [], 0
    while i < len(term):
        ch = term[i]
        if ch == "\\" and i + 1 < len(term):
            out.append(re.escape(term[i + 1].lower() if lower else term[i + 1]))
            i += 2
            continue
        out.append("(?s:.*)" if ch == "*" else "(?s:.)" if ch == "?" else re.escape(ch.lower() if lower else ch))
        i += 1
    return re.compile("".join(out))


def _values(document, field):
    """(kind, values) of ``field`` in ``document``: 'keyword' values or 'text' words."""
    if field.endswith(".keyword"):
        value = document.get(field[: -len(".keyword")])
        return "keyword", [v for v in (value if isinstance(value, list) else [value]) if v is not None]
    if field.startswith(("MD_.", "AB_.")):
        root, key = field.split(".", 1)
        metadata = document.get(root) or {}
        if key == "_value":
            return "keyword", [str(v) for v in metadata.values()]
        return "keyword", [str(metadata[key])] if key in metadata else []
    value = document.get(field)
    texts = value if isinstance(value, list) else [value] if value is not None else []
    return "text", texts


def _words(text):
    return [w for w in re.split(r"[^0-9a-z]+", text.lower()) if w]


def _term_matches(term, document, field):
    kind, values = _values(document, field)
    quoted = term.startswith('"') and term.endswith('"') and len(term) >= 2
    if kind == "keyword":
        if quoted:
            return _unescape(term[1:-1]) in values
        return any(_pattern(term, lower=False).fullmatch(v) for v in values)
    words_by_value = [_words(v) for v in values]
    if quoted:
        phrase = _words(_unescape(term[1:-1]))
        return any(phrase == ws[i:i + len(phrase)] for ws in words_by_value for i in range(len(ws)))
    if "*" in term or "?" in term:
        regex = _pattern(term, lower=True)
        return any(regex.fullmatch(w) for ws in words_by_value for w in ws)
    wanted = _words(_unescape(term))
    # A term the analyzer splits matches when any of its words does (default_operator OR inside it)
    return any(w in ws for ws in words_by_value for w in wanted)


def _all_fields(document):
    fields = []
    for key, value in document.items():
        if key in ("MD_", "AB_"):
            fields.extend(f"{key}.{k}" for k in value)
        else:
            fields.append(key)
    return fields


def _matches(text, document, fields=None):
    """Whether query_string ``text`` (default_operator OR) matches ``document``. An unscoped term is
    searched in ``fields``, or in every field when the clause names none, as OpenSearch does."""
    tokens = _tokens(text)
    operands = [t for t in tokens if t not in ("AND", "OR")]
    every = "AND" in tokens
    if every:
        assert "OR" not in tokens, "mixed operators are not modelled"

    def operand(token, default_fields):
        field, value = _field_split(token)
        scope = [field] if field else default_fields
        if value.startswith("(") and value.endswith(")"):
            return _matches(value[1:-1], document, scope)
        return any(_term_matches(value, document, f) for f in scope)

    default_fields = fields or _all_fields(document)
    results = [operand(t, default_fields) for t in operands]
    return all(results) if every else any(results)


def _clause_matches(clause, document):
    if "term" in clause:
        (field, value), = clause["term"].items()
        return _term_matches(f'"{value}"', document, field)
    body = clause["query_string"]
    assert body.get("default_operator", "OR") == "OR"
    return _matches(body["query"], document, body.get("fields"))


def _unscoped_terms(text):
    """Terms of a field-scoped query_string that carry no field and so would search every field."""
    return [t for t in _tokens(text) if t not in ("AND", "OR") and _field_split(t)[0] is None]


######################## Builders ########################


def _simple_clauses(module, index_type="asset", **request):
    builder = module.SimpleSearchQueryBuilder(module.DatabaseAccessManager())
    clause = builder._build_simple_query_clause(module.SimpleSearchRequestModel(**request), ["db1"], index_type)
    return clause["bool"].get("must", [])


def _field_clause(module, field, value):
    return module.SimpleSearchQueryBuilder(module.DatabaseAccessManager())._build_field_query(field, value)


def _metadata_clause(module, metadata_query, mode="both", index_type="file"):
    builder = module.DualIndexQueryBuilder(module.DatabaseAccessManager())
    return builder._build_metadata_search_query(metadata_query, mode, index_type)


@pytest.mark.unit
class TestSimpleSearchFieldFilters:
    def test_a_word_the_field_lacks_excludes_the_record(self, search_module):
        clause = _field_clause(search_module, "str_assetname", "gearbox zzz*")
        assert not _clause_matches(clause, ASSET), clause
        assert _unscoped_terms(clause["query_string"]["query"]) == []

    def test_every_word_present_matches(self, search_module):
        assert _clause_matches(_field_clause(search_module, "str_assetname", "gear* hous*"), ASSET)
        assert _clause_matches(_field_clause(search_module, "str_assetname", "housing gear*"), ASSET)

    def test_the_request_only_matches_records_holding_every_word(self, search_module):
        """Through the simple search builder: the asset name filter is a required clause."""
        must = _simple_clauses(search_module, assetName="gearbox zzz*")
        assert len(must) == 1 and not _clause_matches(must[0], ASSET)
        assert _clause_matches(_simple_clauses(search_module, assetName="gearbox*")[0], ASSET)

    def test_a_tag_of_several_words(self, search_module):
        assert _clause_matches(_field_clause(search_module, "list_tags", "red ca*"), ASSET)
        assert not _clause_matches(_field_clause(search_module, "list_tags", "red bus*"), ASSET)

    def test_a_file_key_of_several_words(self, search_module):
        must = _simple_clauses(search_module, "file", fileKey="operator zzz*")
        assert not _clause_matches(must[0], FILE)
        assert _clause_matches(_simple_clauses(search_module, "file", fileKey="operator note*")[0], FILE)

    def test_the_query_text(self, search_module):
        assert _field_clause(search_module, "str_assetname", "gearbox  zzz*")["query_string"]["query"] == \
            "str_assetname:(gearbox AND zzz*)"
        assert _field_clause(search_module, "str_assetname", "gear*")["query_string"]["query"] == \
            "str_assetname:gear*"

    def test_operator_words_are_literal(self, search_module):
        query = _field_clause(search_module, "str_assetname", "gear* OR NOT AND")["query_string"]["query"]
        assert query == 'str_assetname:(gear* AND "OR" AND "NOT" AND "AND")'

    def test_a_value_without_wildcards_is_still_an_exact_match(self, search_module):
        assert _field_clause(search_module, "str_assetname", "gearbox housing") == {
            "term": {"str_assetname.keyword": "gearbox housing"}}


@pytest.mark.unit
class TestSimpleSearchMetadataValue:
    def _clause(self, module, value, index_type="file"):
        (clause,) = [c for c in _simple_clauses(module, index_type, metadataValue=value)
                     if "query_string" in c and "fields" in c["query_string"]]
        return clause

    def test_a_contains_search_needs_every_word(self, search_module):
        assert _clause_matches(self._clause(search_module, "maintenance log"), FILE)
        assert not _clause_matches(self._clause(search_module, "zzz Operator"), FILE)

    def test_a_pattern_of_several_words_is_matched_against_each_whole_value(self, search_module):
        assert _clause_matches(self._clause(search_module, "Line 3*", "asset"), ASSET)
        assert not _clause_matches(self._clause(search_module, "Line 9*", "asset"), ASSET)
        assert self._clause(search_module, "Line 3*", "asset")["query_string"]["query"] == "Line\\ 3*"

    def test_one_word_is_unchanged(self, search_module):
        assert self._clause(search_module, "night")["query_string"]["query"] == "*night*"
        assert self._clause(search_module, "Train*")["query_string"]["query"] == "Train*"


@pytest.mark.unit
class TestMetadataQuery:
    def test_a_field_value_pattern_of_several_words_stays_on_its_field(self, search_module):
        clause = _metadata_clause(search_module, "Plant:Line 3*", index_type="asset")
        assert _clause_matches(clause, ASSET)
        assert _unscoped_terms(clause["query_string"]["query"]) == []
        assert not _clause_matches(_metadata_clause(search_module, "Plant:Other Line*", index_type="asset"), ASSET)

    def test_a_quoted_exact_value_is_unchanged(self, search_module):
        clause = _metadata_clause(search_module, "Plant:Line 3", index_type="asset")
        assert clause["query_string"]["query"] == 'MD_.Plant:"Line 3"'
        assert _clause_matches(clause, ASSET)

    def test_value_mode_needs_every_word(self, search_module):
        assert _clause_matches(_metadata_clause(search_module, "maintenance log", "value"), FILE)
        assert not _clause_matches(_metadata_clause(search_module, "zzz Operator", "value"), FILE)

    def test_value_mode_pattern_is_matched_against_each_whole_value(self, search_module):
        assert _clause_matches(_metadata_clause(search_module, "Operator Notes*", "value"), FILE)
        assert not _clause_matches(_metadata_clause(search_module, "Operator Zzz*", "value"), FILE)

    def test_both_mode_pattern_and_exact_value(self, search_module):
        assert _clause_matches(_metadata_clause(search_module, "Operator Notes*", "both"), FILE)
        assert not _clause_matches(_metadata_clause(search_module, "Night Notes*", "both"), FILE)
        assert _metadata_clause(search_module, "Line 3", "both", "asset")["query_string"]["query"] == '"Line 3"'


@pytest.mark.unit
class TestTheGeneralQueryStillMatchesAnyWord:
    def test_any_word(self, search_module):
        builder = search_module.DualIndexQueryBuilder(search_module.DatabaseAccessManager())
        general = builder._build_general_search_query("zzz gearbox", True, "asset")
        assert [c["query_string"]["query"] for c in general["bool"]["should"]] == ["*zzz* *gearbox*"] * 2
