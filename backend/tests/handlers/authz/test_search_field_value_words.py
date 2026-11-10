# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A field filter or metadata value of several words asks for every word, in that field (#1282).

The field-scoped values are sent as query_string text. query_string splits its text at unescaped
whitespace and, with default_operator OR, a record matched when any word did; a word after the first
also lost its field and was searched in every field. ``vamscli search simple --asset-name "gearbox
zzz*"`` therefore returned the asset named "gearbox". The model below reads the query_string forms the
builders emit as OpenSearch reads them (``query_string_oracle``, on both engines), over the two field
kinds the indexes map: analyzed text (``str_*``, ``list_tags``: lower-cased words) and flat_object
values (``MD_`` / ``AB_``: one term per whole value). The general keyword query keeps its any-word
match (#1273).
"""

import pytest

from backend.tests.handlers.authz import query_string_oracle as oracle
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


######################## The query_string model ########################


@pytest.fixture(autouse=True, params=oracle.ENGINES)
def engine(request, search_module, monkeypatch):
    """Every test runs against both engines' handling of flat_object queries (query_string_oracle)."""
    monkeypatch.setattr(search_module, "opensearch_type", request.param)
    return request.param


ENGINE = {}


@pytest.fixture(autouse=True)
def _current_engine(engine):
    ENGINE["name"] = engine


def _clause_matches(clause, document):
    return oracle.clause_matches(clause, document, ENGINE["name"])


_unscoped_terms = oracle.unscoped_terms


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
