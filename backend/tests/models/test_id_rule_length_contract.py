# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The identifier rule's length bounds, as the models declare and apply them.

An ID-rule field is held to `common.validators.id_pattern` (`^[-_a-zA-Z0-9]{3,63}$`) whatever
`max_length` its `Field()` declares, either through `regex=id_pattern` on the field or through the
model's root validator dispatching the value to the `ID` rule. A declared ceiling above the pattern's
own never takes effect, because the pattern refuses the value first. It is still the figure a reader
of the model sees and the one an API reference copies, and a page stating `databaseId` as 4-256
characters or `executionGroupId` as at most 64 invites a value the API refuses with a 400.

The bounds are measured from the pattern rather than restated. A field that declares the pattern
declares no length the pattern cannot reach, and the fields the reference pages document accept
exactly the range those pages state.
"""

import importlib
import pkgutil
import re

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

import models
from common.validators import id_pattern

_BOUNDS = re.fullmatch(r"\^\[[^\]]+\]\{(\d+),(\d+)\}\$", id_pattern)
assert _BOUNDS, f"id_pattern no longer has the ^[class]{{min,max}}$ shape measured here: {id_pattern}"
ID_MIN, ID_MAX = int(_BOUNDS.group(1)), int(_BOUNDS.group(2))

_BUCKET_ID = "11111111-2222-3333-4444-555555555555"


def _model(module_name, class_name):
    return getattr(importlib.import_module(module_name), class_name)


def _database_id_min():
    """The databaseId minimum POST /database adds on top of the ID rule's own."""
    from models.databases import CreateDatabaseRequestModel

    return CreateDatabaseRequestModel.__fields__["databaseId"].field_info.min_length


DB_MIN = _database_id_min()


def _model_classes():
    """Every model class declared in `backend/models/`, as (module, class name, class) triples."""
    for module_info in pkgutil.iter_modules(models.__path__):
        try:
            module = importlib.import_module(f"models.{module_info.name}")
        except Exception:
            # A model module that cannot import under test env is covered by its own suite.
            continue
        for name in dir(module):
            candidate = getattr(module, name)
            fields = getattr(candidate, "__fields__", None)
            if isinstance(fields, dict) and getattr(candidate, "__module__", "") == module.__name__:
                yield module_info.name, name, candidate


def _field_messages(model, field_name, value):
    """The field-level error messages `value` draws from `field_name`'s own declaration."""
    field = model.__fields__[field_name]
    _, errors = field.validate(value, {}, loc=field_name, cls=model)
    if errors is None:
        return []
    return [error["msg"] for error in ValidationError([errors], model).errors()]


@pytest.mark.unit
def test_the_measured_bounds_are_the_id_rule():
    """Positive control: the figures every assertion below compares against."""
    assert (ID_MIN, ID_MAX) == (3, 63)
    assert DB_MIN == 4


@pytest.mark.unit
def test_a_field_declaring_the_id_pattern_declares_its_ceiling():
    """A `Field()` that carries `regex=id_pattern` declares no length the pattern cannot reach."""
    declared = [
        (f"models/{module_name}.py::{class_name}.{field_name}",
         field.field_info.min_length, field.field_info.max_length)
        for module_name, class_name, cls in _model_classes()
        for field_name, field in cls.__fields__.items()
        if getattr(field.field_info, "regex", None) == id_pattern
    ]
    assert declared, "no model field declares regex=id_pattern, so this rule measures nothing"
    unreachable = [
        entry for entry in declared
        if (entry[2] is not None and entry[2] > ID_MAX) or (entry[1] is not None and entry[1] < ID_MIN)
    ]
    assert not unreachable, (
        f"these fields declare a length id_pattern ({ID_MIN}-{ID_MAX}) never lets through, which is "
        f"the figure a reference page then copies: {unreachable}"
    )


@pytest.mark.unit
@pytest.mark.parametrize("module_name,class_name,field_name", [
    ("models.databases", "CreateDatabaseRequestModel", "databaseId"),
    ("models.physnaViewer", "PhysnaViewerRequestModel", "databaseId"),
])
def test_one_character_over_the_ceiling_is_refused_for_its_length(
    module_name, class_name, field_name
):
    """The refusal a caller reads names the ceiling the reference pages state."""
    model = _model(module_name, class_name)
    assert _field_messages(model, field_name, "a" * ID_MAX) == []
    messages = _field_messages(model, field_name, "a" * (ID_MAX + 1))
    assert any(f"at most {ID_MAX} characters" in message for message in messages), messages


# (module, model, field, which minimum is documented, the other fields a valid request carries)
EFFECTIVE_RANGE_CASES = [
    ("models.databases", "CreateDatabaseRequestModel", "databaseId", "db",
     {"description": "A description", "defaultBucketId": _BUCKET_ID}),
    ("models.assetsV3", "CreateAssetRequestModel", "databaseId", "db",
     {"assetName": "Landing Gear", "description": "A description", "isDistributable": True}),
    ("models.assetsV3", "CopyFileRequestModel", "destinationDatabaseId", "db",
     {"sourcePath": "/a.txt", "destinationPath": "/b.txt"}),
    ("models.executions", "ExecuteWorkflowRequestV2Model", "executionGroupId", "id", {}),
    ("models.executions", "RerunExecutionRequestModel", "executionGroupId", "id", {}),
    ("models.pipelines", "CreateTemplateRequestModel", "templateId", "id",
     {"templateName": "contract-probe"}),
    ("models.workflows", "SpecifiedPipelineInput", "defaultTemplateId", "id",
     {"pipelineId": "pipeline-1"}),
]


@pytest.mark.unit
@pytest.mark.parametrize("module_name,class_name,field_name,floor,body", EFFECTIVE_RANGE_CASES)
def test_the_whole_model_accepts_exactly_the_documented_range(
    module_name, class_name, field_name, floor, body
):
    """Measured through the whole model, so the root validator's ID check is part of the result."""
    model = _model(module_name, class_name)
    low = DB_MIN if floor == "db" else ID_MIN
    for length in (low, ID_MAX):
        model(**{**body, field_name: "a" * length})
    for length in (low - 1, ID_MAX + 1):
        with pytest.raises(ValidationError):
            model(**{**body, field_name: "a" * length})
