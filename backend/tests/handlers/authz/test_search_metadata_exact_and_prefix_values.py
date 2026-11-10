# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An exact or prefix metadata value search finds the record, on both engines (#1285).

The value searches named the flat_object subfield ``MD_._value``. Both engines read that name as a key
called ``_value``, so an exact or prefix query on it matched nothing; only a pattern with another
wildcard reached the values, and only on OpenSearch Serverless. ``--metadata-query SBXC-325`` therefore
found nothing while ``--metadata-mode value`` (a contains search) found the asset. The builders now name
the root for exact and prefix terms and, for other wildcards, the field each engine answers on
(``query_string_oracle`` models both). A key name never matches as a value.
"""

import pytest

from backend.tests.handlers.authz import query_string_oracle as oracle
from backend.tests.handlers.authz.search_authz_harness import load_search_module

ASSET = {"str_assetname": "a1", "MD_": {"ProjectCode": "SBXC-325", "Plant": "Line 3"}}
FILE = {"str_key": "/part.glb", "MD_": {"genai_title": "Gearbox Housing"}, "AB_": {"format": "GLTF2.0"}}


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_metadata_values")


@pytest.fixture(params=oracle.ENGINES)
def engine(request, search_module, monkeypatch):
    monkeypatch.setattr(search_module, "opensearch_type", request.param)
    return request.param


def _metadata(module, query, mode, index_type):
    builder = module.DualIndexQueryBuilder(module.DatabaseAccessManager())
    return builder._build_metadata_search_query(query, mode, index_type)


def _simple_value(module, value, index_type):
    builder = module.SimpleSearchQueryBuilder(module.DatabaseAccessManager())
    request = module.SimpleSearchRequestModel(metadataValue=value)
    must = builder._build_simple_query_clause(request, ["db1"], index_type)["bool"]["must"]
    (clause,) = [c for c in must if "query_string" in c and "fields" in c["query_string"]]
    return clause


def _general(module, query, index_type, simple=False):
    if simple:
        builder = module.SimpleSearchQueryBuilder(module.DatabaseAccessManager())
        request = module.SimpleSearchRequestModel(query=query)
        return builder._build_simple_query_clause(request, ["db1"], index_type)["bool"]["must"][0]
    builder = module.DualIndexQueryBuilder(module.DatabaseAccessManager())
    return builder._build_general_search_query(query, True, index_type)


def _finds(clause, document, engine):
    return oracle.clause_matches(clause, document, engine)


@pytest.mark.unit
class TestMetadataQueryValues:
    @pytest.mark.parametrize("mode", ["both", "value"])
    @pytest.mark.parametrize("value", ["SBXC*", "SBXC-3*", "*325", "SB?C-325", "*XC*"])
    def test_a_pattern_finds_the_value(self, search_module, engine, mode, value):
        assert _finds(_metadata(search_module, value, mode, "asset"), ASSET, engine)

    def test_an_exact_value_finds_the_record_in_both_mode(self, search_module, engine):
        assert _finds(_metadata(search_module, "SBXC-325", "both", "asset"), ASSET, engine)
        assert _finds(_metadata(search_module, "Line 3", "both", "asset"), ASSET, engine)

    def test_an_exact_value_matches_the_whole_value_only(self, search_module, engine):
        assert not _finds(_metadata(search_module, "SBXC", "both", "asset"), ASSET, engine)
        assert not _finds(_metadata(search_module, "Line", "both", "asset"), ASSET, engine)

    @pytest.mark.parametrize("mode", ["both", "value"])
    @pytest.mark.parametrize("value", ["*Code*", "Project*", "*Plant", "ZZZ*", "*999"])
    def test_a_key_name_or_another_value_does_not_match(self, search_module, engine, mode, value):
        assert not _finds(_metadata(search_module, value, mode, "asset"), ASSET, engine)

    def test_value_mode_contains_search(self, search_module, engine):
        assert _finds(_metadata(search_module, "SBXC", "value", "asset"), ASSET, engine)
        assert not _finds(_metadata(search_module, "Code", "value", "asset"), ASSET, engine)

    def test_a_file_attribute_value(self, search_module, engine):
        assert _finds(_metadata(search_module, "GLTF*", "value", "file"), FILE, engine)
        assert _finds(_metadata(search_module, "GLTF2.0", "both", "file"), FILE, engine)
        assert _finds(_metadata(search_module, "*TF2*", "both", "file"), FILE, engine)


@pytest.mark.unit
class TestMetadataQueryKeyValuePairs:
    @pytest.mark.parametrize("pair", ["ProjectCode:SBXC*", "ProjectCode:*325", "ProjectCode:SB?C-325",
                                      "ProjectCode:SBXC-325", "Plant:Line 3", "Plant:*ine 3"])
    def test_a_pair_finds_its_keys_value(self, search_module, engine, pair):
        clause = _metadata(search_module, pair, "both", "asset")
        assert _finds(clause, ASSET, engine)
        assert oracle.unscoped_terms(clause["query_string"]["query"]) == []

    @pytest.mark.parametrize("pair", ["Plant:*325", "ProjectCode:*999", "Plant:SBXC*", "ProjectCode:SBXC"])
    def test_a_pair_does_not_match_another_keys_value(self, search_module, engine, pair):
        assert not _finds(_metadata(search_module, pair, "both", "asset"), ASSET, engine)

    def test_the_pair_names_the_path_field_only_for_a_serverless_wildcard(self, search_module, engine):
        wildcard = _metadata(search_module, "ProjectCode:*325", "both", "asset")["query_string"]["query"]
        prefix = _metadata(search_module, "ProjectCode:SBXC*", "both", "asset")["query_string"]["query"]
        assert prefix == "MD_.ProjectCode:SBXC*"
        if engine == "serverless":
            assert wildcard == "MD_._valueAndPath:MD_.ProjectCode\\=*325"
        else:
            assert wildcard == "MD_.ProjectCode:*325"


@pytest.mark.unit
class TestSimpleSearchMetadataValue:
    @pytest.mark.parametrize("value", ["SBXC*", "*325", "SB?C-325", "SBXC-325", "SBXC", "Line 3*"])
    def test_finds_the_value(self, search_module, engine, value):
        assert _finds(_simple_value(search_module, value, "asset"), ASSET, engine)

    @pytest.mark.parametrize("value", ["Project*", "*Code*", "Code", "ZZZ*"])
    def test_a_key_name_or_another_value_does_not_match(self, search_module, engine, value):
        assert not _finds(_simple_value(search_module, value, "asset"), ASSET, engine)

    def test_a_file_attribute_value(self, search_module, engine):
        assert _finds(_simple_value(search_module, "GLTF*", "file"), FILE, engine)


@pytest.mark.unit
class TestTheGeneralQueryReachesMetadataValues:
    @pytest.mark.parametrize("simple", [False, True])
    def test_a_word_of_a_metadata_value(self, search_module, engine, simple):
        assert _finds(_general(search_module, "SBXC", "asset", simple), ASSET, engine)
        assert _finds(_general(search_module, "GLTF", "file", simple), FILE, engine)

    @pytest.mark.parametrize("simple", [False, True])
    def test_a_key_name_alone_does_not_match(self, search_module, engine, simple):
        assert not _finds(_general(search_module, "ProjectCode", "asset", simple), ASSET, engine)


@pytest.mark.unit
class TestFieldNames:
    def test_no_exact_or_prefix_term_names_an_internal_subfield(self, search_module, engine):
        for clause in (_metadata(search_module, "SBXC-325", "both", "file"),
                       _metadata(search_module, "SBXC*", "value", "file"),
                       _simple_value(search_module, "SBXC*", "file")):
            assert clause["query_string"]["fields"] == ["MD_", "AB_"]

    def test_wildcard_terms_name_the_field_the_engine_answers_on(self, search_module, engine):
        expected = ["MD_._value", "AB_._value"] if engine == "serverless" else ["MD_", "AB_"]
        for clause in (_metadata(search_module, "*325", "both", "file"),
                       _metadata(search_module, "SBXC", "value", "file"),
                       _simple_value(search_module, "SBXC", "file")):
            assert clause["query_string"]["fields"] == expected
