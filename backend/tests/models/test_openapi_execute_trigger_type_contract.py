# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract binding the trigger-type vocabularies in code to the two API documentation sources.

`documentation/VAMS_API.yaml` and `docs/api/workflows.md` are independent sources of truth
(documentation/CLAUDE.md), and the trigger type has TWO vocabularies -- the execute request's lowercase
form and the execution row's stored form -- that are never copied onto each other. So the execute body's
enum must equal the request vocabulary and carry no stored value, while every list filter and the
execution row must name every stored value. Both documents are read from the repository, so adding a
value to the model without documenting it turns this red.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from backend.backend.models.executions import EXECUTE_TRIGGER_TYPES, TRIGGER_TYPES  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = REPO_ROOT / "documentation" / "VAMS_API.yaml"
DOC_PATH = REPO_ROOT / "documentation" / "docusaurus-site" / "docs" / "api" / "workflows.md"

EXECUTE_PATH = "/workflows/{workflowDatabaseId}/{workflowId}/execute"
LIST_PATHS = ("/database/{databaseId}/assets/{assetId}/workflows/executions", "/workflows/executions")


@pytest.fixture(scope="module")
def spec():
    with open(SPEC_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _execute_trigger_property(spec):
    schema = spec["paths"][EXECUTE_PATH]["post"]["requestBody"]["content"]["application/json"]["schema"]
    return schema["properties"]["triggerType"]


def _query_param(operation, name):
    matches = [p for p in operation.get("parameters", [])
               if p.get("in") == "query" and p.get("name") == name]
    assert len(matches) == 1, f"{name} query parameter declared {len(matches)} times"
    return matches[0]


@pytest.mark.unit
class TestOpenApiTriggerTypes:
    def test_the_execute_body_enum_is_the_request_vocabulary(self, spec):
        assert _execute_trigger_property(spec)["enum"] == list(EXECUTE_TRIGGER_TYPES)

    def test_the_execute_body_enum_carries_no_stored_form(self, spec):
        assert not set(_execute_trigger_property(spec)["enum"]) & set(TRIGGER_TYPES)

    @pytest.mark.parametrize("path", LIST_PATHS)
    def test_each_list_filter_names_every_stored_value(self, spec, path):
        description = _query_param(spec["paths"][path]["get"], "triggerType").get("description", "")
        for stored in TRIGGER_TYPES:
            assert stored in description, (path, stored, description)

    def test_the_execution_row_names_every_stored_value(self, spec):
        description = spec["components"]["schemas"]["executionListRow"]["properties"]["triggerType"].get(
            "description", "")
        for stored in TRIGGER_TYPES:
            assert stored in description, stored


@pytest.mark.unit
class TestWorkflowsApiPage:
    def test_the_page_names_every_stored_value_in_a_code_span(self):
        text = DOC_PATH.read_text(encoding="utf-8")
        for stored in TRIGGER_TYPES:
            assert f"`{stored}`" in text, stored

    def test_the_page_names_every_request_value_in_a_code_span(self):
        text = DOC_PATH.read_text(encoding="utf-8")
        for request_form in EXECUTE_TRIGGER_TYPES:
            assert f"`{request_form}`" in text, request_form
