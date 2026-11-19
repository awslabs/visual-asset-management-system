# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Metadata value searches that are not exact or a prefix find their records on a provisioned domain too
(#1285, #1282, #1273).

The builders named the ``._value`` / ``._valueAndPath`` subfields for such a pattern only on OpenSearch
Serverless and the root (``MD_``) or the key (``MD_.<key>``) on a provisioned domain, on the belief that
provisioned engines rewrite every wildcard query on a flat_object field. A provisioned OpenSearch 3.5 domain
does not: ``MD_:*325`` matched nothing there while ``MD_._value:*325`` matched the three records holding
``PRJ-325``. Suffix and inner wildcards, ``key:value`` patterns that are not a prefix, contains searches
and the keyword query over metadata values all found nothing on a provisioned domain.

The model (``query_string_oracle``) is first held to the counts the live domain answered
(``PROVISIONED_3_5_PROBES``). Each case below then runs one search request of the live check through the
real query builder and reads the result with the model, on both engines, over the probed records.
"""

import pytest

from backend.tests.handlers.authz import query_string_oracle as oracle
from backend.tests.handlers.authz.search_authz_harness import load_search_module

ASSETS = oracle.PROBE_ASSETS
FILES = oracle.PROBE_FILES

PRJ_325 = {"fz-a/cad-part", "fz-a/operator-notes", "fz-b/delete-me"}
TRAIN = {"fz-a/gearbox", "fz-a/operator-notes", "fz-b/gear-photo"}
TRAIN_ALPHA = {"fz-a/operator-notes", "fz-b/gear-photo"}
PDF = "pump-manual/centrifugal_pump_manual.pdf"
TXT = "operator-notes/operator_notes.txt"
CSV = "sensor-log/sensor_readings.csv"


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_metadata_patterns")


@pytest.fixture(params=oracle.ENGINES)
def engine(request, search_module, monkeypatch):
    monkeypatch.setattr(search_module, "opensearch_type", request.param)
    return request.param


def _found(clause, records, engine):
    return {name for name, document in records.items() if oracle.clause_matches(clause, document, engine)}


def _metadata(module, query, mode, index_type):
    builder = module.DualIndexQueryBuilder(module.DatabaseAccessManager())
    return builder._build_metadata_search_query(query, mode, index_type)


def _simple_value(module, value, index_type):
    builder = module.SimpleSearchQueryBuilder(module.DatabaseAccessManager())
    request = module.SimpleSearchRequestModel(metadataValue=value)
    must = builder._build_simple_query_clause(request, ["db1"], index_type)["bool"]["must"]
    (clause,) = [c for c in must if "query_string" in c and "fields" in c["query_string"]]
    return clause


def _general(module, query, index_type, simple=False, include_metadata=True):
    if simple:
        builder = module.SimpleSearchQueryBuilder(module.DatabaseAccessManager())
        request = module.SimpleSearchRequestModel(query=query)
        return builder._build_simple_query_clause(request, ["db1"], index_type)["bool"]["must"][0]
    builder = module.DualIndexQueryBuilder(module.DatabaseAccessManager())
    return builder._build_general_search_query(query, include_metadata, index_type)


@pytest.mark.unit
class TestTheModelAgreesWithTheProvisionedDomain:
    @pytest.mark.parametrize("index_type, query, hits", oracle.PROVISIONED_3_5_PROBES)
    def test_each_probe_matches_as_many_records_as_on_the_live_domain(self, index_type, query, hits):
        records = ASSETS if index_type == "asset" else FILES
        clause = {"query_string": {"query": query}}
        assert len(_found(clause, records, "provisioned")) == hits


@pytest.mark.unit
class TestAssetMetadataQuery:
    @pytest.mark.parametrize("query, mode, expected", [
        ("PRJ-325", "both", PRJ_325),                                       # A1 exact
        ("Train*", "both", TRAIN),                                          # A2 prefix
        ("*325", "both", PRJ_325),                                          # A3 suffix
        ("Tr*n-A*", "both", TRAIN_ALPHA),                                   # A4 inner wildcard
        ("ProjectCode:PRJ-325", "both", PRJ_325),                           # A5 key:value exact
        ("TrainSet:Train*", "both", TRAIN),                                 # A6 key:value prefix
        ("TrainSet:*Beta", "both", {"fz-a/gearbox"}),                       # A7 key:value suffix
        ("TrainSet:Tr?in-Alpha", "both", TRAIN_ALPHA),                      # A8 key:value ?
        ("PRJ-325", "value", PRJ_325),                                      # A9 value mode, plain
        ("Train*", "value", TRAIN),                                         # A10 value mode, prefix
        ("*325", "value", PRJ_325),                                         # A11 value mode, suffix
        ("MD_str_ProjectCode:PRJ-325", "both", PRJ_325),                    # A12 type-prefixed key
        ("gearbox housing*", "both", {"fz-a/gearbox", "fz-b/gearbox-stl"}),  # W5
        ("gearbox housing i*", "both", {"fz-a/gearbox"}),                   # W6
        ("*housing insp*", "both", {"fz-a/gearbox"}),                       # W7 inner, with a space
        ("Inspection:gearbox housing r*", "both", {"fz-b/gearbox-stl"}),    # W8
        ("Inspection:*housing review", "both", {"fz-b/gearbox-stl"}),
        ("gearbox zzz*", "both", set()),                                    # W9
        ("gearbox housing inspection", "both", {"fz-a/gearbox"}),           # W12 exact whole value
        ("*325 OR TrainSet:*Beta", "both", PRJ_325 | {"fz-a/gearbox"}),
        ("*325 AND TrainSet:*Alpha", "both", {"fz-a/operator-notes"}),
    ])
    def test_finds_the_records(self, search_module, engine, query, mode, expected):
        assert _found(_metadata(search_module, query, mode, "asset"), ASSETS, engine) == expected

    @pytest.mark.parametrize("query, mode", [
        ("*Code*", "both"), ("*Code*", "value"), ("Project*", "value"), ("*Set", "both"),
        ("TrainSet:*325", "both"), ("ProjectCode:*Beta", "both"), ("Code", "value"),
    ])
    def test_a_key_name_or_another_keys_value_does_not_match(self, search_module, engine, query, mode):
        assert _found(_metadata(search_module, query, mode, "asset"), ASSETS, engine) == set()


@pytest.mark.unit
class TestFileMetadataQuery:
    @pytest.mark.parametrize("query, mode, expected", [
        ("Operator Notes", "both", {TXT}),          # F1 exact
        ("maint*", "both", {PDF}),                  # F2 prefix
        ("*log", "both", {PDF}),                    # F3 suffix
        ("*ator No*", "both", {TXT}),               # F4 inner, with a space
        ("category:maintenance log", "both", {PDF}),  # F5 key:value exact
        ("docType:Oper*", "both", {TXT}),           # F6 key:value prefix
        ("docType:*Notes", "both", {TXT}),          # F7 key:value suffix
        # F8: the text file's genai_keywords hold the word too
        ("maintenance", "value", {PDF, TXT}),
        ("*log", "value", {PDF}),
    ])
    def test_finds_the_files(self, search_module, engine, query, mode, expected):
        assert _found(_metadata(search_module, query, mode, "file"), FILES, engine) == expected


@pytest.mark.unit
class TestSimpleSearchMetadataValue:
    @pytest.mark.parametrize("value, expected", [
        ("PRJ-325", PRJ_325),                                   # S1
        ("Train*", TRAIN),                                      # S2
        ("*325", PRJ_325),                                      # S3
        ("housing inspection", {"fz-a/gearbox"}),               # W10 every word
        ("gearbox zzz*", set()),                                # W11
        ("gearbox housing inspection", {"fz-a/gearbox"}),       # every word, here one whole value
        ("Tr?in-Beta", {"fz-a/gearbox"}),
    ])
    def test_finds_the_assets(self, search_module, engine, value, expected):
        assert _found(_simple_value(search_module, value, "asset"), ASSETS, engine) == expected

    @pytest.mark.parametrize("value, expected", [("maintenance", {PDF, TXT}), ("*ator No*", {TXT})])
    def test_finds_the_files(self, search_module, engine, value, expected):
        assert _found(_simple_value(search_module, value, "file"), FILES, engine) == expected


@pytest.mark.unit
class TestTheKeywordQueryReachesMetadataValues:
    @pytest.mark.parametrize("simple", [False, True])
    @pytest.mark.parametrize("query, expected", [
        ("maintenance log", {PDF, TXT, CSV}),   # K1: any word; the CSV by its asset name sensor-log
        ("Operator Notes", {TXT}),              # K2
        ("maintenance", {PDF, TXT}),            # K3
        ("maintenance zzzqq", {PDF, TXT}),      # K5 any word
    ])
    def test_files(self, search_module, engine, simple, query, expected):
        assert _found(_general(search_module, query, "file", simple), FILES, engine) == expected

    def test_without_metadata_only_the_name_matches(self, search_module, engine):
        """K8: the same words with metadata left out of the keyword search."""
        clause = _general(search_module, "maintenance log", "file", include_metadata=False)
        assert _found(clause, FILES, engine) == {CSV}

    @pytest.mark.parametrize("simple", [False, True])
    @pytest.mark.parametrize("query, expected", [("PRJ-325", PRJ_325), ("Beta", {"fz-a/gearbox"}),
                                                 ("ProjectCode", set())])
    def test_assets(self, search_module, engine, simple, query, expected):
        assert _found(_general(search_module, query, "asset", simple), ASSETS, engine) == expected


@pytest.mark.unit
class TestTheFieldsNamedAreTheSameOnBothEngines:
    def test_the_query_text_and_fields(self, search_module, engine):
        assert _metadata(search_module, "*325", "both", "asset")["query_string"]["fields"] == ["MD_._value"]
        assert _metadata(search_module, "PRJ-325", "both", "asset")["query_string"]["fields"] == ["MD_"]
        assert _metadata(search_module, "Train*", "value", "file")["query_string"]["fields"] == ["MD_", "AB_"]
        assert _metadata(search_module, "TrainSet:*Beta", "both", "asset")["query_string"]["query"] == \
            "MD_._valueAndPath:MD_.TrainSet\\=*Beta"
        assert _metadata(search_module, "TrainSet:Train*", "both", "asset")["query_string"]["query"] == \
            "MD_.TrainSet:Train*"
        general = _general(search_module, "maintenance", "file")["bool"]["should"][0]["query_string"]["fields"]
        assert general[-2:] == ["MD_._value", "AB_._value"]
