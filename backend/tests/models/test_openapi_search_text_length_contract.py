# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""documentation/VAMS_API.yaml bounds the free-text search fields at the model's limit.

`models.search.MAX_SEARCH_TEXT_LENGTH` is the maxLength the request models apply to the
general query and the metadata text of both search routes. The spec is what a generated
client validates against, so a lower bound there rejects requests the API accepts.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

SPEC_PATH = Path(__file__).resolve().parents[3] / "documentation" / "VAMS_API.yaml"

TEXT_FIELDS = [
    ("simpleSearchRequest", "query"),
    ("simpleSearchRequest", "metadataValue"),
    ("searchRequest", "query"),
    ("searchRequest", "metadataQuery"),
]


@pytest.fixture(scope="module")
def schemas():
    if not SPEC_PATH.is_file():
        pytest.skip(f"OpenAPI spec not found at {SPEC_PATH}")
    with open(SPEC_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)["components"]["schemas"]


@pytest.mark.unit
def test_model_limit_is_what_the_fields_enforce():
    """Control: the request models apply the constant the spec is compared with."""
    from models.search import MAX_SEARCH_TEXT_LENGTH, SearchRequestModel, SimpleSearchRequestModel

    assert SimpleSearchRequestModel.__fields__["query"].field_info.max_length == MAX_SEARCH_TEXT_LENGTH
    assert SimpleSearchRequestModel.__fields__["metadataValue"].field_info.max_length == MAX_SEARCH_TEXT_LENGTH
    assert SearchRequestModel.__fields__["query"].field_info.max_length == MAX_SEARCH_TEXT_LENGTH
    assert SearchRequestModel.__fields__["metadataQuery"].field_info.max_length == MAX_SEARCH_TEXT_LENGTH


@pytest.mark.unit
@pytest.mark.parametrize("schema,field", TEXT_FIELDS)
def test_spec_max_length_matches_the_model(schemas, schema, field):
    from models.search import MAX_SEARCH_TEXT_LENGTH

    assert schemas[schema]["properties"][field]["maxLength"] == MAX_SEARCH_TEXT_LENGTH
