# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The `isSystem` contract in the OpenAPI spec and the two API reference pages.

Response schemas carry the field read-only; no request schema gains it (the models ignore the key, and a
documented request field would promise what the API silently drops). Every guarded operation's 400 names
the system rule, and the reference pages quote the refusal messages the handlers send — the same literals
the CLI and the live smoke suite match on, imported from the module that owns them."""

import os
import pathlib

import pytest
import yaml

from backend.backend.common.workflows import systemRecords as sr

REPO = pathlib.Path(__file__).resolve().parents[3]
SPEC_PATH = REPO / "documentation" / "VAMS_API.yaml"
DOCS = REPO / "documentation" / "docusaurus-site" / "docs" / "api"

RESPONSE_SCHEMAS = ("pipelineV2", "workflowV2")
REQUEST_SCHEMAS = ("createPipelineRequest", "updatePipelineRequest",
                   "createWorkflowRequest", "updateWorkflowRequest",
                   "createPipelineTemplateRequest", "updatePipelineTemplateRequest",
                   "setWorkflowTriggerRequest")
GUARDED_OPERATIONS = (
    ("/database/{databaseId}/pipelines/{pipelineId}", "put"),
    ("/database/{databaseId}/pipelines/{pipelineId}", "delete"),
    ("/database/{databaseId}/pipelines/{pipelineId}/templates", "post"),
    ("/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}", "put"),
    ("/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}", "delete"),
    ("/database/{databaseId}/workflows/{workflowId}", "put"),
    ("/database/{databaseId}/workflows/{workflowId}", "delete"),
    ("/database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}", "put"),
    ("/database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}", "delete"),
)


@pytest.fixture(scope="module")
def spec():
    with open(SPEC_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _page(name):
    with open(DOCS / name, encoding="utf-8") as handle:
        return handle.read()


@pytest.mark.unit
class TestOpenApiIsSystem:
    @pytest.mark.parametrize("schema_name", RESPONSE_SCHEMAS)
    def test_response_schemas_carry_the_field_read_only(self, spec, schema_name):
        field = spec["components"]["schemas"][schema_name]["properties"]["isSystem"]
        assert field["type"] == "boolean"
        assert field["readOnly"] is True
        assert field["default"] is False
        assert "isSystem" not in spec["components"]["schemas"][schema_name].get("required", [])

    @pytest.mark.parametrize("schema_name", REQUEST_SCHEMAS)
    def test_no_request_schema_gains_the_field(self, spec, schema_name):
        assert "isSystem" not in spec["components"]["schemas"][schema_name]["properties"]

    @pytest.mark.parametrize("path,method", GUARDED_OPERATIONS)
    def test_every_guarded_operation_names_the_system_rule_on_400(self, spec, path, method):
        description = spec["paths"][path][method]["responses"]["400"]["description"]
        assert "system" in description.lower(), f"{method.upper()} {path}: {description}"

    def test_control_an_unguarded_read_does_not(self, spec):
        description = spec["paths"]["/database/{databaseId}/pipelines/{pipelineId}"]["get"]["responses"]["400"]["description"]
        assert "system" not in description.lower()


@pytest.mark.unit
class TestReferencePages:
    def test_pipelines_page_documents_the_field_and_quotes_the_refusals(self):
        page = _page("pipelines.md")
        assert page.count('"isSystem": false') >= 2  # the list item and the single-pipeline example
        for literal in (sr.SYSTEM_PIPELINE_READONLY_MESSAGE, sr.SYSTEM_PIPELINE_ARCHIVE_MESSAGE,
                        sr.SYSTEM_TEMPLATE_LOCKED_MESSAGE):
            assert literal in page, literal
        assert "re-asserts" in page  # deploy wins: the enabled switch is a pause
        assert "`isSystem` is not a request field" in page

    def test_workflows_page_documents_the_field_and_quotes_the_refusals(self):
        page = _page("workflows.md")
        assert '"isSystem": false' in page
        for literal in (sr.SYSTEM_WORKFLOW_READONLY_MESSAGE, sr.SYSTEM_WORKFLOW_ARCHIVE_MESSAGE,
                        sr.SYSTEM_TRIGGER_LOCKED_MESSAGE):
            assert literal in page, literal
        assert "re-asserts" in page
        assert "`inputFileFilters` and `defaultTemplateIds`, when sent, must equal the stored trigger" in page


def test_spec_path_is_inside_the_repository():
    assert os.path.basename(str(SPEC_PATH)) == "VAMS_API.yaml" and SPEC_PATH.is_file()
