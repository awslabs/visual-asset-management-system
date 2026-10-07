# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Request/response models of POST /search/nlp: defaults, bounds, extra='ignore', aliases."""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

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
