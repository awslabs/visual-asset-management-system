# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A metadata value or field filter typed in one pair of double quotes is searched as that exact value
(#1299).

``vamscli search assets --metadata-query '"SBXC-325"'`` found nothing while the unquoted value found the
five assets holding it: the builders escaped the caller's quotes and so searched for a value that starts
and ends with a quote character. A value wrapped in exactly one pair of double quotes is now the exact
value inside them, on every entry point: the ``both`` and ``value`` modes of ``metadataQuery``, the value
of a ``key:value`` term, the simple search ``metadataValue`` and the simple search field filters
(``assetName``, ``assetId``, ``assetType``, ``fileKey``, ``tags``). A quoted value of several words
matches the whole value, and ``*`` and ``?`` inside the quotes are literal characters. An operator word
or a ``:`` inside the quotes belongs to the value. An unbalanced quote, a quote inside a value and empty
quotes stay literal characters, as before.

Each search runs through the real query builder and is read with the model of how OpenSearch answers it
(``query_string_oracle``), on both engines, over the records a provisioned domain was probed with plus a
few records holding literal ``*``, ``?``, ``"``, ``:`` and operator words.
"""

import pytest

from backend.tests.handlers.authz import query_string_oracle as oracle
from backend.tests.handlers.authz.search_authz_harness import load_search_module

ASSETS = dict(oracle.PROBE_ASSETS)
ASSETS.update({
    "lit/star": {"str_assetname": "grade-star", "MD_": {"Grade": "A*"}},
    "lit/ab": {"str_assetname": "grade-ab", "MD_": {"Grade": "AB"}},
    "lit/question": {"str_assetname": "code-q", "MD_": {"Code": "X?1"}},
    "lit/xy1": {"str_assetname": "code-xy1", "MD_": {"Code": "XY1"}},
    "lit/quote": {"str_assetname": "quote-note", "MD_": {"Note": '"PRJ-325'}},
    "lit/colon": {"str_assetname": "doc-ref", "MD_": {"Ref": "DOC:42"}},
    "lit/route": {"str_assetname": "route-map", "MD_": {"Route": "north AND south"}},
    "lit/tagged": {"str_assetname": "pump housing", "str_assettype": "3d model", "str_assetid": "x-asset-1",
                   "list_tags": ["red car", "blue*"], "MD_": {}},
})
FILES = oracle.PROBE_FILES

PRJ_325 = {"fz-a/cad-part", "fz-a/operator-notes", "fz-b/delete-me"}
TRAIN = {"fz-a/gearbox", "fz-a/operator-notes", "fz-b/gear-photo"}
PDF = "pump-manual/centrifugal_pump_manual.pdf"
TXT = "operator-notes/operator_notes.txt"


@pytest.fixture(scope="module")
def search_module():
    return load_search_module("search_under_test_quoted_values")


@pytest.fixture(params=oracle.ENGINES)
def engine(request, search_module, monkeypatch):
    monkeypatch.setattr(search_module, "opensearch_type", request.param)
    return request.param


def _found(clauses, records, engine):
    return {name for name, document in records.items()
            if all(oracle.clause_matches(clause, document, engine) for clause in clauses)}


def _metadata(module, query, mode, index_type):
    builder = module.DualIndexQueryBuilder(module.DatabaseAccessManager())
    return [builder._build_metadata_search_query(query, mode, index_type)]


def _simple(module, index_type, **request):
    builder = module.SimpleSearchQueryBuilder(module.DatabaseAccessManager())
    clause = builder._build_simple_query_clause(module.SimpleSearchRequestModel(**request), ["db1"], index_type)
    return clause["bool"]["must"]


@pytest.mark.unit
class TestAQuotedMetadataQueryValueIsThatExactValue:
    @pytest.mark.parametrize("query, mode, expected", [
        ('"PRJ-325"', "both", PRJ_325),                                         # the issue's search
        ('"PRJ-325"', "value", PRJ_325),
        ('  "PRJ-325"  ', "both", PRJ_325),
        ('"gearbox housing inspection"', "both", {"fz-a/gearbox"}),             # several words: whole value
        ('"gearbox housing inspection"', "value", {"fz-a/gearbox"}),
        ('"housing inspection"', "value", set()),                               # not a whole value
        ('"Train*"', "both", set()),                                            # `*` is literal
        ('"Train*"', "value", set()),
        ('"A*"', "both", {"lit/star"}),
        ('"A*"', "value", {"lit/star"}),
        ('"X?1"', "both", {"lit/question"}),
        ('"DOC:42"', "both", {"lit/colon"}),                                    # a ':' inside the quotes
        ('"north AND south"', "both", {"lit/route"}),                           # an operator word inside
        ('"north AND south"', "value", {"lit/route"}),
        ('"north AND south" OR "PRJ-999"', "both", {"lit/route", "fz-b/checklist"}),
        ('"PRJ-325" AND TrainSet:"Train-Alpha"', "both", {"fz-a/operator-notes"}),
        ('"PRJ-325" OR "PRJ-999"', "value", PRJ_325 | {"fz-b/checklist"}),
    ])
    def test_assets(self, search_module, engine, query, mode, expected):
        assert _found(_metadata(search_module, query, mode, "asset"), ASSETS, engine) == expected

    @pytest.mark.parametrize("query, mode, expected", [
        ('"maintenance log"', "both", {PDF}),       # the text file's keywords only contain the words
        ('"maintenance log"', "value", {PDF}),
        ('"Operator Notes"', "both", {TXT}),
        ('"PDF"', "both", {PDF}),                   # a file attribute value
        ('"maint*"', "both", set()),
    ])
    def test_files(self, search_module, engine, query, mode, expected):
        assert _found(_metadata(search_module, query, mode, "file"), FILES, engine) == expected


@pytest.mark.unit
class TestAQuotedKeyValueTermIsThatKeysExactValue:
    @pytest.mark.parametrize("query, expected", [
        ('ProjectCode:"PRJ-325"', PRJ_325),
        ('ProjectCode: "PRJ-325"', PRJ_325),
        ('MD_str_ProjectCode:"PRJ-325"', PRJ_325),
        ('Inspection:"gearbox housing review"', {"fz-b/gearbox-stl"}),
        ('TrainSet:"Train*"', set()),
        ('Grade:"A*"', {"lit/star"}),
        ('Code:"X?1"', {"lit/question"}),
        ('Route:"north AND south"', {"lit/route"}),
        ('TrainSet:"PRJ-325"', set()),                  # another key's value
    ])
    def test_assets(self, search_module, engine, query, expected):
        assert _found(_metadata(search_module, query, "both", "asset"), ASSETS, engine) == expected

    @pytest.mark.parametrize("query, expected", [
        ('category:"maintenance log"', {PDF}),
        ('docType:"Operator Notes"', {TXT}),
        ('docType:"Oper*"', set()),
    ])
    def test_files(self, search_module, engine, query, expected):
        assert _found(_metadata(search_module, query, "both", "file"), FILES, engine) == expected


@pytest.mark.unit
class TestAQuotedSimpleMetadataValueIsThatExactValue:
    @pytest.mark.parametrize("value, expected", [
        ('"PRJ-325"', PRJ_325),
        ('"gearbox housing inspection"', {"fz-a/gearbox"}),
        ('"housing inspection"', set()),                # the unquoted words find the gearbox
        ('"Train*"', set()),
        ('"A*"', {"lit/star"}),
        ('"X?1"', {"lit/question"}),
    ])
    def test_assets(self, search_module, engine, value, expected):
        assert _found(_simple(search_module, "asset", metadataValue=value), ASSETS, engine) == expected

    @pytest.mark.parametrize("value, expected", [('"maintenance log"', {PDF}), ('"PDF"', {PDF}),
                                                 ('"TXT"', {TXT})])
    def test_files(self, search_module, engine, value, expected):
        assert _found(_simple(search_module, "file", metadataValue=value), FILES, engine) == expected


@pytest.mark.unit
class TestAQuotedSimpleFieldFilterIsThatExactValue:
    @pytest.mark.parametrize("request_fields, expected", [
        ({"assetName": '"gearbox"'}, {"fz-a/gearbox"}),
        ({"assetName": '"pump housing"'}, {"lit/tagged"}),
        ({"assetName": '"gear*"'}, set()),
        ({"assetId": '"x-asset-1"'}, {"lit/tagged"}),
        ({"assetType": '"3d model"'}, {"lit/tagged"}),
        ({"tags": ['"red car"']}, {"lit/tagged"}),
        ({"tags": ['"blue*"']}, {"lit/tagged"}),
        ({"tags": ['"red"']}, set()),
    ])
    def test_assets(self, search_module, engine, request_fields, expected):
        assert _found(_simple(search_module, "asset", **request_fields), ASSETS, engine) == expected

    @pytest.mark.parametrize("request_fields, expected", [
        ({"fileKey": '"/operator_notes.txt"'}, {TXT}),
        ({"assetName": '"pump-manual"'}, {PDF}),
        ({"fileKey": '"/operator*"'}, set()),
    ])
    def test_files(self, search_module, engine, request_fields, expected):
        assert _found(_simple(search_module, "file", **request_fields), FILES, engine) == expected

    def test_a_field_without_a_keyword_subfield_is_an_exact_phrase(self, search_module):
        builder = search_module.SimpleSearchQueryBuilder(search_module.DatabaseAccessManager())
        clause = builder._build_field_query("str_x", '"a* b"', use_keyword=False)
        assert clause["query_string"]["query"] == 'str_x:"a\\* b"'


@pytest.mark.unit
class TestOtherQuotesStayLiteral:
    """An unbalanced quote, a quote inside a value and empty quotes are characters of the value, as
    before the quote rule."""

    @pytest.mark.parametrize("query, mode, expected", [
        ('"PRJ-325', "both", {"lit/quote"}),
        ('"PRJ-325', "value", {"lit/quote"}),
        ('Note:"PRJ-325', "both", {"lit/quote"}),
        ('PRJ-325"', "both", set()),
        ('"PRJ"-325"', "both", set()),
        ('""', "both", set()),
    ])
    def test_metadata_query(self, search_module, engine, query, mode, expected):
        assert _found(_metadata(search_module, query, mode, "asset"), ASSETS, engine) == expected

    @pytest.mark.parametrize("value, expected", [('"PRJ-325', {"lit/quote"}), ('PRJ-325"', set())])
    def test_simple_metadata_value(self, search_module, engine, value, expected):
        assert _found(_simple(search_module, "asset", metadataValue=value), ASSETS, engine) == expected

    def test_the_quote_helper(self, search_module):
        quoted = search_module.FieldClassifier.quoted_exact_value
        assert quoted('"PRJ-325"') == "PRJ-325"
        assert quoted(' "Line 3" ') == "Line 3"
        assert quoted('"A*"') == "A*"
        for literal in ('"PRJ-325', 'PRJ-325"', '"a"b"', '""', '" "', '"', "PRJ-325", 'a"b'):
            assert quoted(literal) is None, literal

    @pytest.mark.parametrize("mode", ["both", "value", "key"])
    @pytest.mark.parametrize("text", [
        "a AND b", "a OR b OR c", "a AND b OR c", "x AND  AND y", " AND ", "a", 'a" AND "b', '"a AND b',
        'a AND b"', '"a"b" AND c', '"a" AND b', 'a:b:"x" AND y', '"a" x AND y"', 'k:"v AND w',
    ])
    def test_a_query_without_a_quoted_value_spanning_a_separator_splits_as_before(
            self, search_module, mode, text):
        operator = "AND" if " AND " in text else "OR" if " OR " in text else None
        expected = (operator, text.split(f" {operator} ") if operator else [text])
        assert search_module.FieldClassifier.split_metadata_query(text, mode) == expected

    @pytest.mark.parametrize("text, mode, expected", [
        ('"north AND south"', "both", (None, ['"north AND south"'])),
        ('"north AND south"', "value", (None, ['"north AND south"'])),
        ('"north AND south"', "key", ("AND", ['"north', 'south"'])),
        ('"north AND south" OR "PRJ-999"', "both", ("OR", ['"north AND south"', '"PRJ-999"'])),
        ('x OR "a AND b"', "both", ("OR", ["x", '"a AND b"'])),
        ('Route:"north AND south"', "both", (None, ['Route:"north AND south"'])),
        ('Route:"north AND south"', "value", ("AND", ['Route:"north', 'south"'])),
        ('a AND Route: "x OR y"', "both", ("AND", ["a", 'Route: "x OR y"'])),
    ])
    def test_a_separator_inside_a_quoted_value_belongs_to_it(self, search_module, text, mode, expected):
        assert search_module.FieldClassifier.split_metadata_query(text, mode) == expected


@pytest.mark.unit
class TestTheQuotedBodiesNameTheRoots:
    def test_exact_phrases_on_the_roots_on_both_engines(self, search_module, engine):
        (clause,) = _metadata(search_module, '"A*"', "value", "file")
        assert clause["query_string"]["query"] == '"A\\*"'
        assert clause["query_string"]["fields"] == ["MD_", "AB_"]
        (clause,) = _metadata(search_module, 'Grade:"A*"', "both", "asset")
        assert clause["query_string"]["query"] == 'MD_.Grade:"A\\*"'
        (clause,) = [c for c in _simple(search_module, "asset", metadataValue='"A*"')]
        assert clause["query_string"]["fields"] == ["MD_"]
        assert _simple(search_module, "asset", assetName='"A*"') == [{"term": {"str_assetname.keyword": "A*"}}]
