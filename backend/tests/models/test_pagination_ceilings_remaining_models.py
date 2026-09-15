# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The pagination fields left uncapped by the databases/metadata pass now carry a ceiling.

`GetTagsRequestModel`, `GetTagTypesRequestModel`, `GetMetadataSchemasRequestModel`,
`ListAssetFilesRequestModel` and `GetAssetsRequestModel` declared `ge=1` with no `le=`, while
`models/databases.py`, `models/metadata.py`, `models/roleConstraints.py`, `models/user.py` and
`models/assetHistory.py` all carry one — so the omission was an inconsistency, not a policy.

`le=` alone is not the whole fix, for two reasons the arbiter established:

* The reachable amplification is not the single-scan listings (DynamoDB bounds one Scan response at
  1 MB whatever `Limit` says) but the handlers that feed a caller-supplied `maxItems` into a boto3
  paginator's `PaginationConfig` and call `build_full_result()`, which accumulates pages until the
  budget is spent.
* The tag listing falls back to raw query parameters when its request model REJECTS the request
  (`tagService.get_tags_handler`), so a `le=` bound that produces a `ValidationError` routes the
  oversized value onto the un-modelled path. The budget clamp is what covers that.

Those budgets are asserted against the running handlers beside each handler's own tests —
`tests/handlers/tags/test_tagService_paginator_budget.py` and
`tests/handlers/comments/test_commentService_paginator_budget.py` — because a handler module is
importable only from under `tests/handlers/`. The structural pass at the bottom of this file is what
catches a NEW budget site added without a bound.

The bounds are asserted by introspecting `field_info` rather than by reading the declaration: Pydantic
v1 silently collects an unrecognized `Field()` keyword into `FieldInfo.extra` instead of raising, so a
v2 spelling would leave the field unconstrained while the source looks correct.

`validate_pagination_info` deliberately gains no ceiling of its own — it runs AHEAD of the request
model, so clamping there would hide an over-ceiling value from the model and turn a 400 into a 200
carrying a quietly reduced page. That property is pinned by
`tests/handlers/databases/test_pagination_ceiling_answers_400.py::TestTheSharedHelperAppliesNoCeilingOfItsOwn`.
"""

import ast
import pathlib

import pytest
from pydantic import ValidationError

from backend.backend.models.tag import (
    GetTagsRequestModel,
    GetTagTypesRequestModel,
    MAX_TAG_LIST_MAX_ITEMS,
    MAX_TAG_LIST_PAGE_SIZE,
    MAX_TAG_TOKEN_LENGTH,
)
from backend.backend.models.metadataSchema import (
    GetMetadataSchemasRequestModel,
    MAX_SCHEMA_LIST_MAX_ITEMS,
    MAX_SCHEMA_LIST_PAGE_SIZE,
)
from backend.backend.models.assetsV3 import (
    GetAssetsRequestModel,
    GetAssetVersionsRequestModel,
    ListAssetFilesRequestModel,
    MAX_ASSET_LIST_MAX_ITEMS,
    MAX_ASSET_LIST_PAGE_SIZE,
    MAX_PAGINATION_TOKEN_LENGTH,
    MAX_VERSION_LIST_MAX_ITEMS,
    MAX_VERSION_LIST_PAGE_SIZE,
)
from backend.backend.common.dynamodb import (
    MAX_PAGINATION_MAX_ITEMS,
    MAX_PAGINATION_PAGE_SIZE,
)

BACKEND_SRC = pathlib.Path(__file__).resolve().parents[2] / "backend"

# (model, maxItems ceiling, pageSize ceiling)
CAPPED_MODELS = [
    (GetTagsRequestModel, MAX_TAG_LIST_MAX_ITEMS, MAX_TAG_LIST_PAGE_SIZE),
    (GetTagTypesRequestModel, MAX_TAG_LIST_MAX_ITEMS, MAX_TAG_LIST_PAGE_SIZE),
    (GetMetadataSchemasRequestModel, MAX_SCHEMA_LIST_MAX_ITEMS, MAX_SCHEMA_LIST_PAGE_SIZE),
    (ListAssetFilesRequestModel, MAX_ASSET_LIST_MAX_ITEMS, MAX_ASSET_LIST_PAGE_SIZE),
    (GetAssetsRequestModel, MAX_ASSET_LIST_MAX_ITEMS, MAX_ASSET_LIST_PAGE_SIZE),
    (GetAssetVersionsRequestModel, MAX_VERSION_LIST_MAX_ITEMS, MAX_VERSION_LIST_PAGE_SIZE),
]

MODEL_IDS = [model.__name__ for model, _, _ in CAPPED_MODELS]


@pytest.mark.unit
class TestPaginationBoundsAreLive:
    @pytest.mark.parametrize("model,max_items,page_size", CAPPED_MODELS, ids=MODEL_IDS)
    def test_the_le_bound_is_a_real_constraint(self, model, max_items, page_size):
        """Introspected, not read: an unknown Field() kwarg would be inert and look correct."""
        fields = model.__fields__
        assert fields["maxItems"].field_info.le == max_items
        assert fields["pageSize"].field_info.le == page_size
        # Nothing was swallowed into `extra`, which is where a v2 spelling would land.
        assert not fields["maxItems"].field_info.extra
        assert not fields["pageSize"].field_info.extra

    @pytest.mark.parametrize("model,max_items,page_size", CAPPED_MODELS, ids=MODEL_IDS)
    def test_over_the_ceiling_is_rejected(self, model, max_items, page_size):
        with pytest.raises(ValidationError):
            model(maxItems=max_items + 1)
        with pytest.raises(ValidationError):
            model(pageSize=page_size + 1)

    @pytest.mark.parametrize("model,max_items,page_size", CAPPED_MODELS, ids=MODEL_IDS)
    def test_at_the_ceiling_still_parses(self, model, max_items, page_size):
        """Control: the bound must be inclusive, or it rejects a request accepted yesterday."""
        assert model(maxItems=max_items).maxItems == max_items
        assert model(pageSize=page_size).pageSize == page_size

    @pytest.mark.parametrize("model,max_items,page_size", CAPPED_MODELS, ids=MODEL_IDS)
    def test_the_floor_is_untouched(self, model, max_items, page_size):
        with pytest.raises(ValidationError):
            model(maxItems=0)
        with pytest.raises(ValidationError):
            model(pageSize=0)

    def test_the_reproduction_value_from_the_report_is_rejected(self):
        for model in (GetTagsRequestModel, GetAssetsRequestModel, ListAssetFilesRequestModel,
                      GetMetadataSchemasRequestModel):
            with pytest.raises(ValidationError):
                model(pageSize=100000000)


@pytest.mark.unit
class TestStartingTokenIsLengthBounded:
    """The databases and tag models bounded startingToken; the assetsV3 models did not."""

    ASSET_MODELS = [
        (ListAssetFilesRequestModel, MAX_PAGINATION_TOKEN_LENGTH),
        (GetAssetsRequestModel, MAX_PAGINATION_TOKEN_LENGTH),
        (GetAssetVersionsRequestModel, MAX_PAGINATION_TOKEN_LENGTH),
        (GetTagsRequestModel, MAX_TAG_TOKEN_LENGTH),
        (GetTagTypesRequestModel, MAX_TAG_TOKEN_LENGTH),
    ]

    @pytest.mark.parametrize(
        "model,limit", ASSET_MODELS, ids=[m.__name__ for m, _ in ASSET_MODELS]
    )
    def test_max_length_is_a_real_constraint(self, model, limit):
        assert model.__fields__["startingToken"].field_info.max_length == limit

    @pytest.mark.parametrize(
        "model,limit", ASSET_MODELS, ids=[m.__name__ for m, _ in ASSET_MODELS]
    )
    def test_an_oversized_token_is_rejected_and_a_sized_one_accepted(self, model, limit):
        with pytest.raises(ValidationError):
            model(startingToken="x" * (limit + 1))
        assert model(startingToken="x" * limit).startingToken == "x" * limit


# ---------------------------------------------------------------------------------------------
# Structural pass: no PaginationConfig budget is fed a raw caller value
# ---------------------------------------------------------------------------------------------

PAGINATOR_BUDGET_MODULES = [
    "handlers/tags/tagService.py",
    "handlers/comments/commentService.py",
]

BUDGET_KEYS = ("MaxItems", "PageSize")


def _unbounded_budget_entries(source: str) -> list:
    """Return `(line, key)` for each PaginationConfig budget whose value is not min()-bounded."""
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant) or key.value not in BUDGET_KEYS:
                continue
            # A literal budget is fine — it is not caller-supplied.
            if isinstance(value, ast.Constant):
                continue
            bounded = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "min"
            )
            if not bounded:
                offenders.append((key.lineno, key.value))
    return offenders


UNBOUNDED_SNIPPET = """
config = {
    "MaxItems": int(queryParams["maxItems"]),
    "PageSize": int(queryParams["pageSize"]),
}
"""

BOUNDED_SNIPPET = """
config = {
    "MaxItems": min(int(queryParams["maxItems"]), MAX_PAGINATION_MAX_ITEMS),
    "PageSize": min(int(queryParams["pageSize"]), MAX_PAGINATION_PAGE_SIZE),
    "StartingToken": queryParams.get("startingToken"),
}
"""


@pytest.mark.unit
class TestPaginationConfigBudgetsAreBoundedInSource:
    def test_detector_flags_an_unbounded_budget(self):
        """Positive control: the shape this pass forbids must actually be detected."""
        assert _unbounded_budget_entries(UNBOUNDED_SNIPPET) == [(3, "MaxItems"), (4, "PageSize")]

    def test_detector_accepts_a_bounded_budget(self):
        assert _unbounded_budget_entries(BOUNDED_SNIPPET) == []

    @pytest.mark.parametrize("relative_path", PAGINATOR_BUDGET_MODULES)
    def test_module_bounds_every_caller_supplied_budget(self, relative_path):
        path = BACKEND_SRC / relative_path
        assert path.is_file(), f"{path} does not exist"
        source = path.read_text(encoding="utf-8")
        # Control: the module really does build a PaginationConfig, so an empty offender list is not
        # an empty corpus.
        assert "PaginationConfig" in source
        offenders = _unbounded_budget_entries(source)
        assert offenders == [], (
            f"{relative_path} feeds a caller-supplied value into a PaginationConfig budget without "
            "bounding it (backend/CLAUDE.md Rule 15): "
            + "; ".join(f"line {line}: {key}" for line, key in offenders)
        )
