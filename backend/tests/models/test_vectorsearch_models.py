# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Request/response models of POST /search/nlp: defaults, bounds, extra='ignore', aliases."""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from models.common import validation_error_message
from models.vectorsearch import (
    ENTITY_TYPES,
    WARNING_CODES,
    NlpSearchHitModel,
    NlpSearchRequestModel,
    NlpWarningModel,
    SegmentRefModel,
)


class TestNlpSearchRequestModel:
    def test_defaults(self):
        m = NlpSearchRequestModel(query="red tractor")
        assert m.entityTypes == ["file"] and m.databaseIds == []
        assert m.includeArchived is False and m.includeSegments is True
        assert m.fileClasses == [] and m.fileExtensions == []
        assert m.size == 25 and m.enrich is True
        assert m.opensearch_only_fields() == []

    def test_extra_keys_are_ignored(self):
        m = NlpSearchRequestModel(query="x", unknownField=1)
        assert not hasattr(m, "unknownField")

    @pytest.mark.parametrize("query", ["", "q" * 1001])
    def test_query_bounds(self, query):
        with pytest.raises(ValidationError):
            NlpSearchRequestModel(query=query)

    @pytest.mark.parametrize("length", [1001, 5000])
    def test_an_over_long_query_is_refused_in_plain_words(self, length):
        with pytest.raises(ValidationError) as caught:
            NlpSearchRequestModel(query="q" * length)
        assert validation_error_message(caught.value) == "The query is %d characters; the limit is 1000." % length

    def test_a_query_at_the_limit_is_accepted(self):
        assert len(NlpSearchRequestModel(query="q" * 1000).query) == 1000

    @pytest.mark.parametrize("query", ["", " ", "   ", "\t\n ", "\u00a0\u2003"])
    def test_a_blank_query_is_refused_in_plain_words(self, query):
        with pytest.raises(ValidationError) as caught:
            NlpSearchRequestModel(query=query)
        assert validation_error_message(caught.value) == "The query must not be blank."

    def test_an_over_long_blank_query_reports_the_length_first(self):
        with pytest.raises(ValidationError) as caught:
            NlpSearchRequestModel(query=" " * 1001)
        assert validation_error_message(caught.value) == "The query is 1001 characters; the limit is 1000."

    def test_a_query_with_surrounding_spaces_is_kept_as_sent(self):
        assert NlpSearchRequestModel(query="  red tractor ").query == "  red tractor "

    @pytest.mark.parametrize("size", [0, 101])
    def test_size_bounds(self, size):
        with pytest.raises(ValidationError):
            NlpSearchRequestModel(query="x", size=size)

    def test_entity_types_single_kind(self):
        assert NlpSearchRequestModel(query="x", entityTypes=["asset"]).entityTypes == ["asset"]
        for bad in (["file", "asset"], ["folder"], []):
            with pytest.raises(ValidationError):
                NlpSearchRequestModel(query="x", entityTypes=bad)

    def test_database_ids_capped_and_validated(self):
        with pytest.raises(ValidationError):
            NlpSearchRequestModel(query="x", databaseIds=[f"db{i}" for i in range(101)])
        with pytest.raises(ValidationError):
            NlpSearchRequestModel(query="x", databaseIds=["not valid!"])
        assert NlpSearchRequestModel(query="x", databaseIds=["db-one", "db_two"]).databaseIds == ["db-one", "db_two"]

    def test_classes_and_extensions_normalised(self):
        m = NlpSearchRequestModel(query="x", fileClasses=["Video", "video"], fileExtensions=[".GLB", "glb"])
        assert m.fileClasses == ["video"] and m.fileExtensions == ["glb"]

    def test_opensearch_only_fields_named(self):
        m = NlpSearchRequestModel(query="x", tags=["a"], metadataQuery="b")
        assert m.opensearch_only_fields() == ["metadataQuery", "tags"]

    def test_metadata_search_mode_is_closed(self):
        with pytest.raises(ValidationError):
            NlpSearchRequestModel(query="x", metadataSearchMode="fuzzy")


class TestResponseModels:
    def test_hit_aliases_round_trip(self):
        hit = NlpSearchHitModel(
            _id="db#asset#a/b.glb#v1",
            _score=0.9,
            _index_type="file",
            _source={"str_databaseid": "db"},
            _vector={
                "distance": 0.1,
                "embeddingModelId": "amazon.titan-embed-text-v2:0",
                "sourceModalities": ["text"],
                "indexedAt": "2026-09-13T00:00:00Z",
                "fileClass": "mesh",
                "segmentHits": 0,
                "bestSegment": None,
            },
        )
        out = hit.dict(by_alias=True)
        assert out["_index"] == "vams-vectors"
        assert set(out) == {"_index", "_id", "_score", "_index_type", "_source", "_vector"}
        assert out["_vector"]["bestSegment"] is None

    def test_segment_ref_shape(self):
        ref = SegmentRefModel(
            segmentKey="t0000083456", segmentKind="videoTime", segmentLabel="00:01:23", segmentStartMs=83456, segmentEndMs=88456
        )
        assert set(ref.dict()) == {"segmentKey", "segmentKind", "segmentLabel", "segmentStartMs", "segmentEndMs"}

    def test_closed_sets(self):
        assert WARNING_CODES == (
            "truncated:window",
            "truncated:targets",
            "databases:none_accessible",
            "opensearch:fields_ignored",
            "opensearch:enrichment_failed",
            "segments:window_full",
        )
        assert ENTITY_TYPES == ("file", "asset")
        with pytest.raises(ValidationError):
            NlpWarningModel(code="made:up", message="x")


class TestOpenSearchConstraintBounds:
    """The OpenSearch-only constraints are typed and bounded as /search types them (A3): a malformed or
    oversized constraint is a 400 at the boundary, never a failure inside the OpenSearch step."""

    def test_a_filter_is_a_search_filter_model(self):
        from models.search import SearchFilterModel

        m = NlpSearchRequestModel(query="x", filters=[{"query_string": {"query": "str_assettype:x"}}])
        assert isinstance(m.filters[0], SearchFilterModel)

    @pytest.mark.parametrize("bad", [
        [{"bogus": 1}],
        [{"query_string": {"query": "a", "boost": "9"}}],
        [{"query_string": {"query": "q" * 5001}}],
    ])
    def test_a_malformed_filter_is_refused(self, bad):
        with pytest.raises(ValidationError):
            NlpSearchRequestModel(query="x", filters=bad)

    def test_filters_are_bounded_in_number(self):
        from models.search import MAX_SEARCH_FILTERS

        one = {"query_string": {"query": "a"}}
        assert len(NlpSearchRequestModel(query="x", filters=[one] * MAX_SEARCH_FILTERS).filters) == MAX_SEARCH_FILTERS
        with pytest.raises(ValidationError):
            NlpSearchRequestModel(query="x", filters=[one] * (MAX_SEARCH_FILTERS + 1))

    def test_geo_search_is_a_geo_search_model(self):
        from models.search import GeoSearchModel

        m = NlpSearchRequestModel(query="x", geoSearch={"point": {"lat": 1.0, "lon": 2.0, "radiusMeters": 10}})
        assert isinstance(m.geoSearch, GeoSearchModel)

    @pytest.mark.parametrize("bad", [{}, {"point": {"lat": 91, "lon": 0}}, {"geoJson": "not a shape"}])
    def test_a_malformed_geo_search_is_refused(self, bad):
        with pytest.raises(ValidationError):
            NlpSearchRequestModel(query="x", geoSearch=bad)

    def test_tags_are_bounded_in_number_and_length(self):
        from models.search import MAX_SEARCH_TAGS

        assert len(NlpSearchRequestModel(query="x", tags=["t"] * MAX_SEARCH_TAGS).tags) == MAX_SEARCH_TAGS
        with pytest.raises(ValidationError):
            NlpSearchRequestModel(query="x", tags=["t"] * (MAX_SEARCH_TAGS + 1))
        with pytest.raises(ValidationError) as caught:
            NlpSearchRequestModel(query="x", tags=["t" * 257])
        assert "t" * 257 not in validation_error_message(caught.value)

    def test_file_extensions_are_bounded_in_number_and_length(self):
        from models.vectorsearch import MAX_FILE_EXTENSIONS

        assert len(NlpSearchRequestModel(query="x", fileExtensions=[f"e{i}" for i in range(MAX_FILE_EXTENSIONS)]).fileExtensions) == MAX_FILE_EXTENSIONS
        with pytest.raises(ValidationError):
            NlpSearchRequestModel(query="x", fileExtensions=[f"e{i}" for i in range(MAX_FILE_EXTENSIONS + 1)])
        with pytest.raises(ValidationError):
            NlpSearchRequestModel(query="x", fileExtensions=["e" * 257])


class TestFileClasses:
    """fileClasses accepts the classifier's ids only (A13), so an unknown class is a clear 400 for every
    client instead of an empty answer."""

    def test_every_classifier_id_is_accepted(self):
        from models.vectorsearch import FILE_CLASSES

        assert NlpSearchRequestModel(query="x", fileClasses=list(FILE_CLASSES)).fileClasses == list(FILE_CLASSES)

    @pytest.mark.parametrize("bad", ["3d-model", "model", "pdf"])
    def test_an_unknown_class_is_refused_without_echoing_it(self, bad):
        with pytest.raises(ValidationError) as caught:
            NlpSearchRequestModel(query="x", fileClasses=["mesh", bad])
        message = validation_error_message(caught.value)
        assert bad not in message
        assert "mesh" in message and "usd" in message and "cad" in message


class TestResponseModelsIgnoreExtraKeys:
    """Every response model declares extra='ignore' itself (A15, backend Rule 8), rather than leaning on
    the library default, which a base-class change could move."""

    @pytest.mark.parametrize("model_name", [
        "SegmentRefModel", "NlpVectorInfoModel", "NlpSearchHitModel", "NlpWarningModel", "NlpSummaryModel",
        "NlpTermsBucketModel", "NlpTermsAggregationModel", "NlpSearchResponseModel",
    ])
    def test_extra_is_declared_ignore(self, model_name):
        import models.vectorsearch as module

        config = getattr(module, model_name).__config__
        assert "extra" in vars(config) and config.extra == "ignore"
