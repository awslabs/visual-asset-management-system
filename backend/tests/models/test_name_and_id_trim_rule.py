# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every name field on a request model either trims its surrounding whitespace or says why not.

`test_no_dead_field_kwargs.py` states the free-text half of this as a rule and the name/id half as a
hand-written inventory of the models that trim. An inventory goes stale in silence: a model added
after it was written, or a name field added to a model it does not list, joins the untrimmed side
with nothing failing. This file states the name half as the same kind of partition — derived from the
parsed models, with the untrimmed side enumerated and reasoned.

Why it matters for these fields specifically: a name field with no `regex` accepts any whitespace,
so ' Prod ' and 'Prod' are two stored rows that render identically, and `pipelineName` / `category` /
`workflowName` are ABAC constraint fields (surfaced as `name` / `category` on the Tier-2 Casbin
object), so a grant written for the clean spelling does not cover the padded one. The Casbin rule
builder anchors with `\\Z`, so the padded value fails closed rather than borrowing the grant — the
harm is the near-duplicate entity, not an escalation.
"""

import importlib
import pkgutil

import pytest

import models

# The rule covers NAME-shaped fields. An id is deliberately out of scope: `id_pattern` and
# `userid_pattern` carry no whitespace class, so a padded id is refused by the field's own regex
# rather than stored as a near-duplicate, and the id trims that do exist only accept a spelling the
# rule would otherwise reject. A name field is the opposite case — `object_name_pattern` admits `\s`,
# and several name fields carry no regex at all.
_NAME_SUFFIXES = ("Name",)
_NAME_EXACT = ("name", "identifier", "category")

# Name request fields that do NOT trim, each with the reason. Everything else matching the shapes
# above must trim, which is what makes this a rule rather than a list of survivors: a newly added
# name field fails here until it is given a trim or named below.
NO_TRIM_NAME = {
    # A search filter, not a stored name: the value is matched against indexed documents and never
    # written, so trimming it would change which documents a caller's query selects.
    "models/search.py::SimpleSearchRequestModel.assetName": "search filter, not a stored value",
    # Open asymmetries. Each is a stored entity name whose padded spelling is a near-duplicate row;
    # they are recorded here so the partition holds while they stand, and removing a trim's absence
    # from this map is what the `stale` assertion below then requires.
    "models/metadataSchema.py::CreateMetadataSchemaRequestModel.schemaName":
        "neither create nor update trims schemaName",
    "models/metadataSchema.py::UpdateMetadataSchemaRequestModel.schemaName":
        "neither create nor update trims schemaName",
}


def _request_model_classes():
    """Every `*RequestModel` class declared in `backend/models/`, as (module, class) triples."""
    for module_info in pkgutil.iter_modules(models.__path__):
        try:
            module = importlib.import_module(f"models.{module_info.name}")
        except Exception:
            # A model module that cannot import under the test env is covered by its own suite.
            continue
        for name in dir(module):
            candidate = getattr(module, name)
            fields = getattr(candidate, "__fields__", None)
            if (isinstance(fields, dict)
                    and getattr(candidate, "__module__", "") == module.__name__
                    and name.endswith("RequestModel")):
                yield module_info.name, name, candidate


def _name_fields():
    """(trimmed, untrimmed) keys for every string name field on a request model."""
    trimmed, untrimmed = [], []
    for module_name, class_name, cls in _request_model_classes():
        for field_name, field in cls.__fields__.items():
            # A `Field()` carrying length/regex constraints is re-typed to a ConstrainedStr
            # subclass, so identity against `str` misses exactly the constrained fields.
            outer = field.outer_type_
            if not (isinstance(outer, type) and issubclass(outer, str)):
                continue
            if not (field_name.endswith(_NAME_SUFFIXES)
                    or field_name in _NAME_EXACT):
                continue
            key = f"models/{module_name}.py::{class_name}.{field_name}"
            (trimmed if getattr(field, "pre_validators", None) else untrimmed).append(key)
    return sorted(trimmed), sorted(untrimmed)


@pytest.mark.unit
class TestEveryNameFieldEitherTrimsOrIsDeclaredNotTo:
    def test_the_walk_finds_both_sides(self):
        """Non-vacuity. `assert untrimmed == []` is satisfied by a walk that found nothing, so both
        sides have to be populated before the partition below means anything."""
        trimmed, untrimmed = _name_fields()
        assert len(trimmed) >= 25, f"the walk found only {len(trimmed)} trimmed name field(s)"
        assert untrimmed, "no untrimmed name field was found, so the partition is vacuous"

    def test_the_partition_is_complete(self):
        trimmed, untrimmed = _name_fields()
        undeclared = sorted(set(untrimmed) - set(NO_TRIM_NAME))
        assert undeclared == [], (
            "these name request fields neither trim nor are declared in NO_TRIM_NAME. "
            "Wire common.validators.trim_name as a pre=True validator, or add the field to that map "
            "with the reason it keeps its whitespace:\n  " + "\n  ".join(undeclared))
        stale = sorted(set(NO_TRIM_NAME) & set(trimmed))
        assert stale == [], (
            "these fields are declared as deliberately untrimmed but now trim; remove them from "
            "NO_TRIM_NAME:\n  " + "\n  ".join(stale))


@pytest.mark.unit
class TestThePipelineAndWorkflowNamesTrim:
    """The ABAC constraint fields, asserted on the parsed value rather than on the declaration —
    `pre=True` is what puts the trimmed string in front of the length check and in the stored row."""

    def test_a_pipeline_name_and_category_trim_on_create_and_update(self):
        from models.pipelines import CreatePipelineRequestModel, UpdatePipelineRequestModel

        created = CreatePipelineRequestModel(
            databaseId=" mydb ", pipelineName="  Prod  Pipe  ", category=" conversion ")
        assert (created.databaseId, created.pipelineName, created.category) == (
            "mydb", "Prod  Pipe", "conversion")

        updated = UpdatePipelineRequestModel(pipelineName="  Prod Pipe  ", category="  conversion ")
        assert (updated.pipelineName, updated.category) == ("Prod Pipe", "conversion")

    def test_a_workflow_name_and_category_trim_on_create_and_update(self):
        from models.workflows import CreateWorkflowRequestModel, UpdateWorkflowRequestModel

        created = CreateWorkflowRequestModel(
            databaseId=" mydb ", workflowName="  Nightly  Run  ", category=" batch ",
            specifiedPipelines=[{"pipelineId": " pipe1 "}])
        assert (created.databaseId, created.workflowName, created.category) == (
            "mydb", "Nightly  Run", "batch")
        assert created.specifiedPipelines[0].pipelineId == "pipe1"

        updated = UpdateWorkflowRequestModel(workflowName=" Nightly Run ", category=" batch ")
        assert (updated.workflowName, updated.category) == ("Nightly Run", "batch")

    def test_a_control_character_is_still_refused_rather_than_trimmed_away(self):
        """The trim must not turn an existing rejection into a silent normalization.

        `.strip()` removes a trailing newline, tab or NEL, so a trim declared ahead of the
        control-character rule would make the exact value that rule exists to refuse parse cleanly.
        The rejection is declared first, which is what keeps these loud.
        """
        from aws_lambda_powertools.utilities.parser import ValidationError
        from models.pipelines import (CreatePipelineRequestModel, CreateTemplateRequestModel,
                                      UpdatePipelineRequestModel, UpdateTemplateRequestModel)
        from models.workflows import CreateWorkflowRequestModel, UpdateWorkflowRequestModel

        for char in ("\n", "\r", "\t", "\x85"):
            with pytest.raises(ValidationError):
                CreatePipelineRequestModel(databaseId="mydb", pipelineName=f"Prod{char}")
            with pytest.raises(ValidationError):
                UpdatePipelineRequestModel(category=f"conversion{char}")
            with pytest.raises(ValidationError):
                CreateTemplateRequestModel(
                    templateName=f"T{char}", configFormat="yaml", configBody="a: 1")
            with pytest.raises(ValidationError):
                UpdateTemplateRequestModel(templateName=f"T{char}")
            with pytest.raises(ValidationError):
                CreateWorkflowRequestModel(
                    databaseId="mydb", workflowName=f"W{char}",
                    specifiedPipelines=[{"pipelineId": "pipe1"}])
            with pytest.raises(ValidationError):
                UpdateWorkflowRequestModel(workflowName=f"W{char}")

    def test_a_template_name_trims_on_create_and_update(self):
        from models.pipelines import CreateTemplateRequestModel, UpdateTemplateRequestModel

        created = CreateTemplateRequestModel(
            templateId=" tmpl1 ", templateName="  My Template  ", configFormat="yaml",
            configBody="a: 1")
        assert (created.templateId, created.templateName) == ("tmpl1", "My Template")
        assert UpdateTemplateRequestModel(templateName=" My Template ").templateName == "My Template"

    def test_a_whitespace_only_pipeline_name_is_refused(self):
        """Trimming leaves the empty string, which min_length=1 then rejects — untrimmed, '   ' is a
        pipeline whose name renders as nothing."""
        from aws_lambda_powertools.utilities.parser import ValidationError
        from models.pipelines import CreatePipelineRequestModel

        with pytest.raises(ValidationError):
            CreatePipelineRequestModel(databaseId="mydb", pipelineName="   ")

    def test_a_clean_name_is_returned_verbatim(self):
        """CONTROL: the trim must be a no-op on an unpadded value, including its interior spaces."""
        from models.pipelines import CreatePipelineRequestModel
        from models.workflows import CreateWorkflowRequestModel

        pipeline = CreatePipelineRequestModel(
            databaseId="mydb", pipelineName="Prod Pipe", category="conversion")
        assert (pipeline.pipelineName, pipeline.category) == ("Prod Pipe", "conversion")
        workflow = CreateWorkflowRequestModel(
            databaseId="mydb", workflowName="Nightly Run", category="batch",
            specifiedPipelines=[{"pipelineId": "pipe1"}])
        assert (workflow.workflowName, workflow.category) == ("Nightly Run", "batch")

    def test_the_description_on_these_models_still_keeps_its_whitespace(self):
        """CONTROL on the scope of the change: pipeline/template/workflow `description` is the
        deliberate free-text carve-out recorded in NO_TRIM_FREE_TEXT, and must not start trimming as
        a side effect of the name trims."""
        from models.pipelines import CreatePipelineRequestModel, CreateTemplateRequestModel
        from models.workflows import CreateWorkflowRequestModel

        assert CreatePipelineRequestModel(
            databaseId="mydb", pipelineName="P", description="  text  ").description == "  text  "
        assert CreateTemplateRequestModel(
            templateName="T", configFormat="yaml", configBody="a: 1",
            description="  text  ").description == "  text  "
        assert CreateWorkflowRequestModel(
            databaseId="mydb", workflowName="W", description="  text  ",
            specifiedPipelines=[{"pipelineId": "pipe1"}]).description == "  text  "
