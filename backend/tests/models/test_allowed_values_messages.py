# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A validation error that lists allowed values lists them as plain text: `one of: a, b`.

Formatting the tuple or list of allowed values straight into the message showed callers Python
syntax (`one of ('Lambda', 'SQS')`). Each pipeline, template, execution and trigger message that
names its allowed values is checked here, and a source scan keeps the raw form out of the modules
that build them."""

import ast
from pathlib import Path

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.common.workflows.templateTagSchema import TAG_TYPE_ENUM, validate_tags
from backend.backend.models import executions as ex
from backend.backend.models import pipelines as pl
from tests.handlers.workflows.test_workflowTriggerService_isSystem import (
    PLAIN_WF,
    _call,
    _event,
    _message,
)

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def _error_text(build):
    with pytest.raises(ValidationError) as exc:
        build()
    return [error["msg"] for error in exc.value.errors()]


def _assert_plain(messages, values):
    assert any(m.endswith(f"one of: {', '.join(values)}") for m in messages), messages


def _create_pipeline(**overrides):
    body = {"databaseId": "db1", "pipelineName": "P",
            "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"}}}
    body.update(overrides)
    return pl.CreatePipelineRequestModel(**body)


@pytest.mark.unit
class TestPipelineMessages:
    def test_execution_type_on_create(self):
        text = _error_text(lambda: _create_pipeline(executionConfig={"executionType": "Bogus"}))
        _assert_plain(text, pl.PIPELINE_EXECUTION_TYPES)

    def test_execution_type_on_update(self):
        text = _error_text(lambda: pl.UpdatePipelineRequestModel(
            executionConfig={"executionType": "Bogus"}))
        _assert_plain(text, pl.PIPELINE_EXECUTION_TYPES)

    def test_input_file_arity(self):
        text = _error_text(lambda: _create_pipeline(systemConfig={"inputFileArity": "many"}))
        _assert_plain(text, pl.INPUT_FILE_ARITIES)

    def test_wait_for_callback(self):
        text = _error_text(lambda: _create_pipeline(executionConfig={
            "executionType": "Lambda", "lambda": {"resourceId": "fn"}, "waitForCallback": "Maybe"}))
        _assert_plain(text, pl.WAIT_FOR_CALLBACK_VALUES)

    def test_template_config_format_on_create(self):
        text = _error_text(lambda: pl.CreateTemplateRequestModel(
            templateId="template1", templateName="T", configFormat="toml", configBody="x"))
        _assert_plain(text, pl.TEMPLATE_CONFIG_FORMATS)


@pytest.mark.unit
class TestExecutionMessages:
    def test_execute_trigger_type(self):
        text = _error_text(lambda: ex.ExecuteWorkflowRequestV2Model(triggerType="scheduled"))
        _assert_plain(text, ex.EXECUTE_TRIGGER_TYPES)

    def test_detail_metadata_collection(self):
        text = _error_text(lambda: ex.DetailMetadataPageRequestModel(collection="bogus"))
        _assert_plain(text, ex.DETAIL_METADATA_COLLECTIONS)

    def test_detail_files_collection(self):
        text = _error_text(lambda: ex.DetailFilesPageRequestModel(collection="bogus"))
        _assert_plain(text, ex.DETAIL_FILE_COLLECTIONS)


@pytest.mark.unit
def test_template_enum_tag_lists_its_values():
    schema = [{"tagKey": "quality", "type": TAG_TYPE_ENUM, "enumValues": ["low", "high"], "required": True}]

    errors, _ = validate_tags(schema, {"quality": "medium"})

    assert errors == ["tag 'quality': must be one of: low, high"]


@pytest.mark.unit
def test_unsupported_trigger_type_lists_the_supported_types():
    params = {"databaseId": "GLOBAL", "workflowId": "sysw", "triggerType": "bogus"}

    resp, _ = _call(_event("PUT", params, {"enabled": True}), PLAIN_WF)

    assert resp["statusCode"] == 400
    assert _message(resp) == "Unsupported trigger type. Supported types: fileUpload"


_SCANNED = [
    "models/pipelines.py",
    "models/executions.py",
    "common/workflows/templateTagSchema.py",
    "handlers/workflows/workflowTriggerService.py",
]


def _is_join(expr):
    return (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
            and expr.func.attr == "join")


@pytest.mark.unit
@pytest.mark.parametrize("relative", _SCANNED)
def test_no_message_formats_a_collection_directly(relative):
    """An f-string whose text ends in 'one of' or 'types:' and then formats anything but a join."""
    tree = ast.parse((BACKEND / relative).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        for before, value in zip(node.values, node.values[1:]):
            if (isinstance(before, ast.Constant) and isinstance(value, ast.FormattedValue)
                    and str(before.value).rstrip().endswith(("one of", "one of:", "types:"))
                    and not _is_join(value.value)):
                offenders.append(node.lineno)
    assert offenders == [], f"{relative}: lines {offenders}"
