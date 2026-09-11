# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract tests binding documentation/VAMS_API.yaml to the metadata GET pagination bounds.

The four metadata GETs serve `min(pageSize, maxItems)` records and refuse either parameter above
`MAX_METADATA_PAGE_SIZE`, which the request models carry as their `le=` bound. That bound and both
defaults are the API-wide ones the shared `maxItemsParam` / `pageSizeParam` components document, so
the four operations reference those components rather than metadata-specific copies.

Keeping them shared is what makes the spec usable. A client generated from it enforces `maximum`
before the request leaves, so a component documenting a value the API refuses turns a legal request
into a client-side error, and one documenting no maximum hides the value above which the API answers
`400`. Metadata-specific components would say the same thing in a second place, free to drift.

The bound and the defaults are read off the request models here rather than restated, so a change to
either side that is not mirrored in the other fails. Two positive controls guard the shape of that
claim: the shared components must still be shared (referenced by operations beyond these four, so
repointing cannot be satisfied by renaming a component), and the metadata-specific components must be
absent (so the spec documents one bound rather than two).
"""

from pathlib import Path

import pytest

from backend.backend.models.metadata import (
    DEFAULT_METADATA_MAX_ITEMS,
    DEFAULT_METADATA_PAGE_SIZE,
    MAX_METADATA_PAGE_SIZE,
    GetAssetLinkMetadataRequestModel,
    GetAssetMetadataRequestModel,
    GetDatabaseMetadataRequestModel,
    GetFileMetadataRequestModel,
)

SPEC_PATH = Path(__file__).resolve().parents[3] / "documentation" / "VAMS_API.yaml"

yaml = pytest.importorskip("yaml")

# The metadata GET routes, as apiRoutes.py registers them, paired with the request model each
# one validates its query string against.
METADATA_GETS = {
    "/asset-links/{assetLinkId}/metadata": GetAssetLinkMetadataRequestModel,
    "/database/{databaseId}/assets/{assetId}/metadata": GetAssetMetadataRequestModel,
    "/database/{databaseId}/assets/{assetId}/metadata/file": GetFileMetadataRequestModel,
    "/database/{databaseId}/metadata": GetDatabaseMetadataRequestModel,
}

# The API-wide pagination components the four operations reference, per query parameter.
SHARED_PARAMETERS = {"maxItems": "maxItemsParam", "pageSize": "pageSizeParam"}

# Components that would document the same bound a second time. Their absence is asserted.
METADATA_SPECIFIC_PARAMETERS = ("metadataMaxItemsParam", "metadataPageSizeParam")

# The model default each parameter carries, so the spec is checked against the value the API really
# applies rather than against a number repeated here.
MODEL_DEFAULTS = {"maxItems": DEFAULT_METADATA_MAX_ITEMS, "pageSize": DEFAULT_METADATA_PAGE_SIZE}


@pytest.fixture(scope="module")
def spec():
    if not SPEC_PATH.is_file():
        pytest.skip(f"OpenAPI spec not found at {SPEC_PATH}")
    with open(SPEC_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _parameter_component_names(operation):
    """The component names an operation's parameter list references."""
    return {
        parameter["$ref"].rsplit("/", 1)[-1]
        for parameter in operation.get("parameters", [])
        if isinstance(parameter, dict) and "$ref" in parameter
    }


@pytest.mark.unit
@pytest.mark.parametrize("path", sorted(METADATA_GETS))
def test_each_metadata_get_uses_the_shared_pagination_components(spec, path):
    """A metadata GET documents its bounds through the API-wide components."""
    referenced = _parameter_component_names(spec["paths"][path]["get"])

    assert set(SHARED_PARAMETERS.values()) <= referenced, (
        f"GET {path} does not reference {sorted(SHARED_PARAMETERS.values())}, so its pagination "
        f"bounds are documented by something else: {sorted(referenced)}"
    )


@pytest.mark.unit
def test_no_metadata_specific_pagination_component_exists(spec):
    """One bound, documented once.

    A second component naming the same limit is free to drift from the models and from the shared
    component, and neither the paths above nor the model bounds below would show it.
    """
    declared = set(spec["components"]["parameters"])
    present = sorted(set(METADATA_SPECIFIC_PARAMETERS) & declared)
    assert not present, (
        f"{present} declare the metadata pagination bounds a second time; the metadata GETs "
        f"reference {sorted(SHARED_PARAMETERS.values())}"
    )

    for path, operations in spec["paths"].items():
        for verb, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            assert not _parameter_component_names(operation) & set(METADATA_SPECIFIC_PARAMETERS), (
                f"{verb.upper()} {path} references a metadata-specific pagination component"
            )


@pytest.mark.unit
@pytest.mark.parametrize("parameter,component", sorted(SHARED_PARAMETERS.items()))
def test_the_documented_maximum_is_the_enforced_ceiling(spec, parameter, component):
    """The spec's maximum is the models' le=, read from both sides rather than restated."""
    schema = spec["components"]["parameters"][component]["schema"]

    assert spec["components"]["parameters"][component]["name"] == parameter
    assert schema["maximum"] == MAX_METADATA_PAGE_SIZE, (
        f"{component} documents maximum {schema.get('maximum')} while the metadata GETs refuse "
        f"anything above {MAX_METADATA_PAGE_SIZE}"
    )
    assert schema["minimum"] == 1

    for model in METADATA_GETS.values():
        field_info = model.__fields__[parameter].field_info
        assert field_info.le == schema["maximum"], (
            f"{model.__name__}.{parameter} bounds at {field_info.le}, which is not the "
            f"{schema['maximum']} {component} documents"
        )


@pytest.mark.unit
@pytest.mark.parametrize("parameter,component", sorted(SHARED_PARAMETERS.items()))
def test_the_documented_default_is_the_model_default(spec, parameter, component):
    """A generated client sends a documented default verbatim, so it has to be the real one.

    `pageSizeParam` declares its default in prose rather than as a `default:` key, because the
    component is shared with operations whose own default differs; the number in its description is
    still what a reader acts on, so it is checked as text.
    """
    documented = spec["components"]["parameters"][component]
    model_default = MODEL_DEFAULTS[parameter]

    for model in METADATA_GETS.values():
        assert model.__fields__[parameter].default == model_default, (
            f"{model.__name__}.{parameter} defaults to {model.__fields__[parameter].default}, not "
            f"the {model_default} models/metadata.py declares"
        )

    if "default" in documented["schema"]:
        assert documented["schema"]["default"] == model_default, (
            f"{component} documents default {documented['schema']['default']}, not the "
            f"{model_default} the metadata GETs apply"
        )
    else:
        assert f"Default is {model_default}" in documented["description"], (
            f"{component} declares no default and its description does not name the "
            f"{model_default} the metadata GETs apply: {documented['description']!r}"
        )


@pytest.mark.unit
@pytest.mark.parametrize("path", sorted(METADATA_GETS))
def test_each_metadata_get_documents_the_rejection(spec, path):
    """The ceiling is served as a 400, so every one of these operations documents one."""
    responses = spec["paths"][path]["get"]["responses"]
    assert "400" in responses, (
        f"GET {path} documents no 400 response, but an oversized pageSize or maxItems is "
        f"refused with one: {sorted(responses)}"
    )


@pytest.mark.unit
@pytest.mark.parametrize("component", sorted(SHARED_PARAMETERS.values()))
def test_the_shared_pagination_components_are_still_shared(spec, component):
    """Positive control: the four operations were repointed, not given renamed copies.

    A component referenced only by these four would satisfy every assertion above while being a
    metadata-specific component under an API-wide name, which is the state this item removed.
    """
    users = [
        f"{verb.upper()} {path}"
        for path, operations in spec["paths"].items()
        for verb, operation in operations.items()
        if isinstance(operation, dict) and component in _parameter_component_names(operation)
    ]
    metadata_users = {f"GET {path}" for path in METADATA_GETS}
    assert metadata_users <= set(users), (
        f"{component} is not referenced by every metadata GET: {sorted(users)}"
    )
    assert set(users) - metadata_users, (
        f"{component} is referenced only by the metadata GETs ({sorted(users)}), so it is not the "
        "API-wide component they were pointed at"
    )
