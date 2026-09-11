# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pagination ceilings for the asset-version listing request model.

`GET /database/{databaseId}/assets/{assetId}/getVersions` parses its query string straight into
`GetAssetVersionsRequestModel` (assetVersions.py, `parse(query_params, ...)`) with no handler-side
`validate_pagination_info` step, and feeds `pageSize` to the version query's DynamoDB `Limit`.
Without an upper bound the caller chooses the size of the work the Lambda takes on, so the
ceilings are asserted here alongside every value a live consumer sends, which must keep parsing.

`maxItems` is bounded for contract consistency rather than for effect: the handler reads only
`pageSize` and `startingToken`, and `vamscli asset-version list` deliberately never sends
`maxItems` (it is a client-side accumulation limit there), so its ceiling is reachable only
through a hand-built request and is provable only here.
"""

from pathlib import Path

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

SPEC_PATH = Path(__file__).resolve().parents[3] / "documentation" / "VAMS_API.yaml"

# The listing operation and the shared query-parameter components it documents its bounds with.
VERSIONS_LIST_PATH = "/database/{databaseId}/assets/{assetId}/getVersions"
SHARED_PARAMETERS = {"maxItems": "maxItemsParam", "pageSize": "pageSizeParam"}


def _model():
    import models.assetsV3 as m
    return m.GetAssetVersionsRequestModel


def _ceilings():
    import models.assetsV3 as m
    return {"maxItems": m.MAX_VERSION_LIST_MAX_ITEMS, "pageSize": m.MAX_VERSION_LIST_PAGE_SIZE}


@pytest.mark.unit
class TestAssetVersionListingPaginationCeilings:
    @pytest.mark.parametrize("field", sorted(SHARED_PARAMETERS))
    def test_rejects_a_value_one_above_the_ceiling(self, field):
        ceiling = _ceilings()[field]
        with pytest.raises((ValidationError, ValueError)):
            _model()(**{field: ceiling + 1})

    @pytest.mark.parametrize("field", sorted(SHARED_PARAMETERS))
    def test_rejects_the_unbounded_value_from_the_finding(self, field):
        with pytest.raises((ValidationError, ValueError)):
            _model()(**{field: 10 ** 9})

    @pytest.mark.parametrize("field", sorted(SHARED_PARAMETERS))
    def test_bounds_are_declared_and_live(self, field):
        # Pydantic 1.10.13 collects an unrecognized Field kwarg into FieldInfo.extra instead of
        # raising, so a misspelled bound reads as a constraint and enforces nothing. The value is
        # read off the parsed field, and `extra` is asserted empty so nothing was swallowed.
        info = _model().__fields__[field].field_info
        assert info.ge == 1
        assert info.le == _ceilings()[field]
        assert not info.extra

    @pytest.mark.parametrize("field", sorted(SHARED_PARAMETERS))
    def test_the_ceiling_admits_the_fields_own_default(self, field):
        # A ceiling below a field's own default would 400 every request that omits the parameter.
        assert _model().__fields__[field].default <= _ceilings()[field]


@pytest.mark.unit
class TestAssetVersionListingPaginationAcceptedValues:
    """Positive controls: every value a client legitimately sends today still parses."""

    def test_accepts_absent_pagination_parameters(self):
        model = _model()()
        assert model.maxItems == 1000
        assert model.pageSize == 1000
        assert model.showArchived is False

    @pytest.mark.parametrize("field", sorted(SHARED_PARAMETERS))
    def test_accepts_the_value_at_the_ceiling(self, field):
        ceiling = _ceilings()[field]
        assert getattr(_model()(**{field: ceiling}), field) == ceiling

    @pytest.mark.parametrize("field", sorted(SHARED_PARAMETERS))
    @pytest.mark.parametrize("value", [1, 100, 200, 500, 1000])
    def test_accepts_the_range_live_consumers_send(self, field, value):
        # 100 is what all three web call sites (AssetVersionService.ts) and the MCP server's
        # default page size send; 200/500 appear in the `vamscli asset-version list` examples.
        assert getattr(_model()(**{field: value}), field) == value

    @pytest.mark.parametrize("field", sorted(SHARED_PARAMETERS))
    def test_still_rejects_the_zero_and_negative_floor(self, field):
        for value in (0, -1):
            with pytest.raises((ValidationError, ValueError)):
                _model()(**{field: value})


@pytest.mark.unit
class TestAssetVersionListingOpenApiContract:
    """The enforced ceiling and the published maximum are read from both sides, not restated.

    Widening one without the other is the failure this catches: a spec maximum above the model's
    `le` documents a request the API answers 400 to, and one below it hides values the API accepts.
    """

    @pytest.fixture(scope="class")
    def spec(self):
        yaml = pytest.importorskip("yaml")
        if not SPEC_PATH.is_file():
            pytest.skip(f"OpenAPI spec not found at {SPEC_PATH}")
        with open(SPEC_PATH, encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    @staticmethod
    def _referenced_components(operation):
        return {
            parameter["$ref"].rsplit("/", 1)[-1]
            for parameter in operation.get("parameters", [])
            if isinstance(parameter, dict) and "$ref" in parameter
        }

    def test_the_listing_documents_its_bounds_with_the_shared_components(self, spec):
        referenced = self._referenced_components(spec["paths"][VERSIONS_LIST_PATH]["get"])
        assert set(SHARED_PARAMETERS.values()) <= referenced, (
            f"GET {VERSIONS_LIST_PATH} does not reference "
            f"{sorted(SHARED_PARAMETERS.values())}, so its pagination bounds are documented by "
            f"something else: {sorted(referenced)}"
        )

    @pytest.mark.parametrize("field,component", sorted(SHARED_PARAMETERS.items()))
    def test_the_documented_maximum_is_the_enforced_ceiling(self, spec, field, component):
        parameter = spec["components"]["parameters"][component]
        assert parameter["name"] == field
        assert parameter["schema"]["minimum"] == 1
        assert parameter["schema"]["maximum"] == _ceilings()[field], (
            f"{component} documents maximum {parameter['schema'].get('maximum')} while the "
            f"asset-version listing refuses anything above {_ceilings()[field]}"
        )

    @pytest.mark.parametrize("field,component", sorted(SHARED_PARAMETERS.items()))
    def test_any_documented_default_is_the_model_default(self, spec, field, component):
        # These components are shared by ~20 operations whose model defaults differ, so most
        # declare no `default` at all. One that does declare a default has to be the real one:
        # a generated client sends a documented default verbatim.
        schema = spec["components"]["parameters"][component]["schema"]
        if "default" not in schema:
            return
        assert schema["default"] == _model().__fields__[field].default, (
            f"{component} documents default {schema['default']}, but the asset-version listing "
            f"defaults {field} to {_model().__fields__[field].default}"
        )

    def test_the_listing_documents_the_rejection(self, spec):
        """The ceiling is served as a 400, so the operation has to document one."""
        responses = spec["paths"][VERSIONS_LIST_PATH]["get"]["responses"]
        assert "400" in responses, (
            f"GET {VERSIONS_LIST_PATH} documents no 400 response, but an oversized pageSize or "
            f"maxItems is refused with one: {sorted(responses)}"
        )

    @pytest.mark.parametrize("component", sorted(SHARED_PARAMETERS.values()))
    def test_the_components_are_still_shared(self, spec, component):
        """Control: the bound above is the API-wide one, not a component private to this path.

        Repointing the listing at its own widened component would satisfy every assertion above
        while the enforced value drifted from what the rest of the API documents.
        """
        users = [
            f"{verb.upper()} {path}"
            for path, operations in spec["paths"].items()
            for verb, operation in operations.items()
            if isinstance(operation, dict)
            and component in self._referenced_components(operation)
        ]
        assert len(users) > 1, (
            f"{component} is referenced only by {users}, so it is no longer the component the "
            "rest of the API shares"
        )
