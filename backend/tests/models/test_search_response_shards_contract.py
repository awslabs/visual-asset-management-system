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

``by_alias=True`` is a whole-tree switch, not a per-field one: it renames every aliased field
reachable from ``SearchResponseModel``. It is safe here only because ``shards`` is the single
aliased field in that tree, so ``TestTheAliasIsTheOnlyOneInTheResponseTree`` states that as an
inventory over the tree rather than over a hardcoded class list -- an alias added to any nested
response model would otherwise rename a wire key for every client with nothing failing.

Because ``SearchHitSourceModel`` validates nothing, documentation is the whole of what it does,
and nothing else in the request path would notice the documentation going stale.
``TestTheDocumentedSourceShapeIsTheIndexedShape`` therefore states it against the document
models an indexed record is written from.
"""

import pytest

from aws_lambda_powertools.utilities.parser import BaseModel, ValidationError, parse

from backend.backend.models.indexing import AssetDocumentModel, FileDocumentModel
from backend.backend.models.search import (
    SearchHitModel,
    SearchHitSourceModel,
    SearchResponseModel,
)

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

    def test_the_tally_is_required_so_it_cannot_go_missing_unnoticed(self):
        """A dropped annotation is silent; a required field is not.

        The declaration this replaced was absent from ``__fields__``, so a payload with no
        ``_shards`` parsed cleanly and the key simply never reached the client. The single
        producer -- ``search_dual_index`` -- seeds the tally unconditionally before summing
        each index into it, so requiring it costs no legitimate payload.
        """
        without_tally = {key: value for key, value in OS_RESPONSE.items() if key != "_shards"}
        with pytest.raises(ValidationError):
            parse(without_tally, model=SearchResponseModel)

    def test_the_field_name_also_populates_the_tally(self):
        """`allow_population_by_field_name` keeps the model constructible in-process.

        An internal caller writes the pydantic-legal name; the wire key stays the alias.
        """
        response = SearchResponseModel(
            took=1,
            timed_out=False,
            shards={"total": 1, "successful": 1, "skipped": 0, "failed": 0},
            hits={"total": {"value": 0, "relation": "eq"}, "hits": []},
        )
        assert response.dict(by_alias=True)["_shards"]["successful"] == 1


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


def _aliased_fields(model, seen=None):
    """Every (model, field, alias) reachable from `model` whose alias differs from its name."""
    seen = set() if seen is None else seen
    if model in seen:
        return
    seen.add(model)
    for name, field in model.__fields__.items():
        if field.alias != name:
            yield (model.__name__, name, field.alias)
        nested = [field.type_] + [sub.type_ for sub in (field.sub_fields or [])]
        for candidate in nested:
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                yield from _aliased_fields(candidate, seen)


@pytest.mark.unit
class TestTheAliasIsTheOnlyOneInTheResponseTree:
    def test_the_shard_tally_is_the_only_aliased_field(self):
        """The whole-tree `by_alias=True` may rename exactly one key.

        Stated over the tree rather than over a class list, so a nested response model added
        later is covered without an edit here.
        """
        assert sorted(set(_aliased_fields(SearchResponseModel))) == [
            ("SearchResponseModel", "shards", "_shards")
        ], "by_alias=True would rename another wire key for every search client"

    def test_every_other_key_still_ships_under_its_own_name(self):
        """Positive control for the switch: only `_shards` moved."""
        payload = dict(
            OS_RESPONSE,
            aggregations={
                "str_fileext": {"doc_count": 1, "buckets": [{"key": "glb", "doc_count": 1}]}
            },
            aggregationTotal=1,
        )
        serialized = parse(payload, model=SearchResponseModel).dict(by_alias=True)

        assert sorted(serialized) == [
            "_shards",
            "aggregationTotal",
            "aggregations",
            "hits",
            "timed_out",
            "took",
        ]
        assert sorted(serialized["hits"]) == ["hits", "max_score", "total"]
        assert serialized["aggregations"]["str_fileext"]["buckets"][0]["key"] == "glb"


@pytest.mark.unit
class TestTheDocumentedSourceShapeIsTheIndexedShape:
    """`SearchHitSourceModel` is documentation, so the documentation has to be true.

    It is the type of no field and is imported by no handler, so a name that drifts from the
    indexer costs nothing at import, fails no request, and reads as a documented response key
    a client will never receive.
    """

    def test_every_documented_key_is_one_an_index_actually_writes(self):
        written = set(AssetDocumentModel.__fields__) | set(FileDocumentModel.__fields__)
        documented = set(SearchHitSourceModel.__fields__)

        assert documented - written == set(), (
            "these `_source` keys are documented but written by neither index"
        )

    def test_the_comparison_is_against_a_populated_shape(self):
        """Positive control: an empty or wrongly-read field set would pass the check above.

        Names both a key only the file index writes and one only the asset index writes, so
        the union is confirmed to span both rather than resolving to one of them.
        """
        written = set(AssetDocumentModel.__fields__) | set(FileDocumentModel.__fields__)

        assert {"str_rectype", "num_filesize", "str_assettype"} <= written
        assert len(SearchHitSourceModel.__fields__) > 10
