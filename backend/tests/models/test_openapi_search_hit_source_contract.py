# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract tests binding documentation/VAMS_API.yaml's `searchHitSource` to the indexers.

A search hit's `_source` is whatever the indexer wrote, passed through verbatim: the response
model carries the envelope with `extra='allow'` and applies no model to `_source`. So nothing in
the request path reads this schema, and a property naming a key no index writes cannot fail --
it is served to every caller as a documented response field that is permanently absent, with no
error to attribute the blank to. The spec is the artifact a generated client is built from, which
makes it the copy that has to be true.

`SearchHitSourceModel` in models/search.py documents the same shape and is pinned against the
same two indexer models by `test_search_response_shards_contract.py`. That check says nothing
about this file: the two are separate copies of one list, and the property `num_size` outlived
its removal from the search field lists in both of them independently.

The comparison is against `FileDocumentModel` and `AssetDocumentModel` in models/indexing.py --
what an indexed document is built from -- rather than against the index mapping. The mapping
declares `MD_` and `AB_` as `flat_object` fields, and an indexed document carries them as whole
objects set under `extra='allow'` rather than as declared model fields, so neither side of a
mapping comparison enumerates the metadata the schema deliberately leaves to
`additionalProperties`.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

SPEC_PATH = Path(__file__).resolve().parents[3] / "documentation" / "VAMS_API.yaml"


@pytest.fixture(scope="module")
def spec():
    if not SPEC_PATH.is_file():
        pytest.skip(f"OpenAPI spec not found at {SPEC_PATH}")
    with open(SPEC_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def hit_source(spec):
    return spec["components"]["schemas"]["searchHitSource"]


@pytest.fixture(scope="module")
def indexed_keys():
    from models.indexing import AssetDocumentModel, FileDocumentModel

    return set(AssetDocumentModel.__fields__) | set(FileDocumentModel.__fields__)


@pytest.mark.unit
def test_every_documented_source_key_is_one_an_index_writes(hit_source, indexed_keys):
    """A property no indexer writes is a response field the caller can never receive."""
    documented = set(hit_source["properties"])

    assert documented - indexed_keys == set(), (
        "these searchHitSource properties are documented but written by neither index, so a "
        "client coding to them reads nothing and has no error to attribute it to: "
        f"{sorted(documented - indexed_keys)}"
    )


@pytest.mark.unit
def test_the_comparison_spans_both_index_models(hit_source, indexed_keys):
    """Positive control: an empty properties dict or a failed import would pass the check above.

    Names a key only the file index writes and one only the asset index writes, on both sides of
    the comparison, so neither set can have collapsed to a single model or to nothing.
    """
    documented = set(hit_source["properties"])

    assert {"str_rectype", "num_filesize", "str_assettype"} <= indexed_keys
    assert {"str_rectype", "num_filesize", "str_assettype"} <= documented
    assert len(documented) > 20


@pytest.mark.unit
def test_the_schema_stays_open_and_requires_only_the_discriminator(hit_source):
    """What makes the property list safe to correct, and able to carry the keys it omits.

    The indexer also writes the `MD_` metadata object and, on a file, the `AB_` attribute object,
    each holding whatever keys the record carries. Neither is enumerated in the property list;
    `additionalProperties: true` is what lets them through, and it is also why removing a property
    breaks no validating client. The schema's own `description` names them, so a client reading the
    spec is not left to infer that the two objects exist.
    Requiring only the record type keeps the schema satisfiable by both an asset hit and a file
    hit, whose remaining keys barely overlap.
    """
    assert hit_source.get("additionalProperties") is True
    assert hit_source.get("required") == ["str_rectype"]
