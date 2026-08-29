# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-071 (MEDIUM): the search response must carry the `_shards` it declares.

Pydantic 1.10 drops a leading-underscore annotation from ``__fields__`` entirely --
``ModelMetaclass`` skips any annotation failing ``is_valid_field(name)``, and with the
default ``underscore_attrs_are_private=False`` such a name is neither a field nor a private
attribute. ``SearchResponseModel`` declared ``_shards: Dict[str, int]`` under
``extra='ignore'``, so the shard tally that ``search_dual_index`` actively computes and sums
across the asset and file indexes was discarded at parse time and never reached the client.

The v1 way to model a leading-underscore JSON key is a legal field name plus
``alias``. The field is therefore ``shards`` with ``alias="_shards"``, and both handlers
serialize with ``dict(by_alias=True)``.

``SearchHitModel``'s envelope keys (``_index``, ``_id``, ``_score``, ``_source``,
``_index_type``) cannot be modelled that way -- they are the verbatim OpenSearch hit and
are carried through by ``extra='allow'``. Declaring them produced annotations that validated
nothing, which is why ``SearchHitSourceModel`` was never applied to any payload; it stays as
documentation of the ``_source`` shape.
"""

import pytest

from aws_lambda_powertools.utilities.parser import parse

from backend.backend.models.search import SearchHitModel, SearchResponseModel

OS_RESPONSE = {
    "took": 7,
    "timed_out": False,
    "_shards": {"total": 4, "successful": 4, "skipped": 0, "failed": 0},
    "hits": {
        "total": {"value": 1, "relation": "eq"},
        "max_score": 2.5,
        "hits": [
            {
                "_index": "vams-file",
                "_id": "file-1",
                "_score": 2.5,
                "_index_type": "file",
                "_source": {
                    "str_rectype": "file",
                    "str_databaseid": "db1",
                    "str_key": "/folder/part.glb",
                },
            }
        ],
    },
}


@pytest.mark.unit
class TestShardsSurvivesTheResponseModel:
    def test_the_shard_tally_is_a_real_field(self):
        assert "shards" in SearchResponseModel.__fields__
        assert SearchResponseModel.__fields__["shards"].field_info.alias == "_shards"

    def test_no_underscore_annotation_is_left_declared_but_dropped(self):
        """A leading-underscore declaration would be silently absent from __fields__."""
        declared = set(getattr(SearchResponseModel, "__annotations__", {}))
        assert not [name for name in declared if name.startswith("_")], (
            "an underscore-prefixed annotation is declared but cannot become a field"
        )

    def test_the_tally_round_trips_under_the_opensearch_key(self):
        parsed = parse(OS_RESPONSE, model=SearchResponseModel)
        assert parsed.shards == {"total": 4, "successful": 4, "skipped": 0, "failed": 0}

        serialized = parsed.dict(by_alias=True)
        assert serialized["_shards"] == OS_RESPONSE["_shards"], (
            "the shard tally the service computes is dropped from the response"
        )

    def test_the_field_name_is_not_what_ships(self):
        """Negative control for the alias: `shards` must not be the wire key."""
        serialized = parse(OS_RESPONSE, model=SearchResponseModel).dict(by_alias=True)
        assert "shards" not in serialized


@pytest.mark.unit
class TestHitEnvelopeIsCarriedThroughVerbatim:
    def test_the_hit_envelope_keys_reach_the_client(self):
        parsed = parse(OS_RESPONSE, model=SearchResponseModel)
        hit = parsed.dict(by_alias=True)["hits"]["hits"][0]
        for key in ("_index", "_id", "_score", "_index_type", "_source"):
            assert key in hit, f"{key} was stripped from the hit"
        assert hit["_source"]["str_key"] == "/folder/part.glb"

    def test_no_inert_underscore_annotation_remains_on_the_hit_model(self):
        declared = set(getattr(SearchHitModel, "__annotations__", {}))
        assert not [name for name in declared if name.startswith("_")]
        assert SearchHitModel.__config__.extra.value == "allow"
