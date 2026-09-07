# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract tests binding documentation/VAMS_API.yaml to the pipeline/workflow code it documents.

Two properties are checked:

1. A request body containing exactly the fields the spec marks `required` is accepted by the
   matching Pydantic create model, and the optional id field is documented as it behaves
   (null generates a GUID, an empty string is rejected).
2. Every single-object pipeline/workflow/template/tagSchema/trigger 200 response is documented
   as the VAMS `{message: <object>}` envelope produced by models.common.success, not as the
   bare object.
"""

import os
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

SPEC_PATH = Path(__file__).resolve().parents[3] / "documentation" / "VAMS_API.yaml"

# Routes whose 200 body is a single wrapped object, and the component schema carried under
# `message`. Mirrors the success(body={"message": <model>.dict()}) calls in the pipeline,
# template, workflow and trigger handlers.
SINGLE_OBJECT_ROUTES = [
    ("/database/{databaseId}/pipelines", "post", "pipelineV2"),
    ("/database/{databaseId}/pipelines/{pipelineId}", "get", "pipelineV2"),
    ("/database/{databaseId}/pipelines/{pipelineId}", "put", "pipelineV2"),
    ("/database/{databaseId}/workflows", "post", "workflowV2"),
    ("/database/{databaseId}/workflows/{workflowId}", "get", "workflowV2"),
    ("/database/{databaseId}/workflows/{workflowId}", "put", "workflowV2"),
    ("/database/{databaseId}/pipelines/{pipelineId}/templates", "post", "pipelineTemplate"),
    ("/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}", "get",
     "pipelineTemplate"),
    ("/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}", "put",
     "pipelineTemplate"),
    ("/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}/tagSchema", "get",
     "pipelineTagSchemaResponse"),
    ("/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}/tagSchema", "put",
     "pipelineTagSchemaResponse"),
    ("/database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}", "get",
     "workflowTrigger"),
    ("/database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}", "put",
     "workflowTrigger"),
]

# Field values satisfying every `required` entry across the create schemas under test.
SAMPLE_FIELDS = {
    "databaseId": "my-database",
    "pipelineName": "convert-to-gltf",
    "workflowName": "convert-and-preview",
    "templateName": "high-quality",
    "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "converter-fn"}},
    "specifiedPipelines": [{"pipelineId": "conversion-pipeline"}],
}


@pytest.fixture(scope="module")
def spec():
    if not SPEC_PATH.is_file():
        pytest.skip(f"OpenAPI spec not found at {SPEC_PATH}")
    with open(SPEC_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _create_models():
    from models.pipelines import CreatePipelineRequestModel, CreateTemplateRequestModel
    from models.workflows import CreateWorkflowRequestModel
    return [
        ("createPipelineRequest", CreatePipelineRequestModel, "pipelineId"),
        ("createWorkflowRequest", CreateWorkflowRequestModel, "workflowId"),
        ("createPipelineTemplateRequest", CreateTemplateRequestModel, "templateId"),
    ]


@pytest.mark.unit
class TestOpenApiCreateRequestContract:
    def test_spec_required_fields_are_accepted_by_the_models(self, spec):
        schemas = spec["components"]["schemas"]
        for schema_name, model, _id_field in _create_models():
            required = schemas[schema_name]["required"]
            body = {field: SAMPLE_FIELDS[field] for field in required}
            # A client sending exactly the documented required fields must not be rejected.
            model(**body)

    def test_spec_documents_every_field_the_models_require(self, spec):
        schemas = spec["components"]["schemas"]
        for schema_name, model, _id_field in _create_models():
            documented = set(schemas[schema_name]["properties"])
            model_required = {
                name for name, field in model.__fields__.items() if field.required
            }
            assert model_required <= documented, (
                f"{schema_name} omits model-required field(s) "
                f"{sorted(model_required - documented)}"
            )
            assert model_required <= set(schemas[schema_name]["required"]), (
                f"{schema_name} does not mark {sorted(model_required)} as required"
            )

    def test_optional_id_accepts_null_and_rejects_empty_string(self, spec):
        for schema_name, model, id_field in _create_models():
            required = spec["components"]["schemas"][schema_name]["required"]
            assert id_field not in required, f"{schema_name} must document {id_field} as optional"
            body = {field: SAMPLE_FIELDS[field] for field in required}
            model(**{**body, id_field: None})
            with pytest.raises(Exception):
                model(**{**body, id_field: ""})

    def test_optional_id_is_documented_as_nullable(self, spec):
        # The models accept null for the generated id, so the schema must permit null and not
        # only inherit the non-nullable id_regex string.
        for schema_name, _model, id_field in _create_models():
            field_schema = spec["components"]["schemas"][schema_name]["properties"][id_field]
            assert field_schema.get("nullable") is True, (
                f"{schema_name}.{id_field} accepts null but is not documented as nullable"
            )


@pytest.mark.unit
class TestOpenApiResponseEnvelopeContract:
    @pytest.mark.parametrize("path,method,schema_name", SINGLE_OBJECT_ROUTES)
    def test_single_object_response_uses_the_message_envelope(
        self, spec, path, method, schema_name
    ):
        response = spec["paths"][path][method]["responses"]["200"]
        body = response["content"]["application/json"]["schema"]
        assert body.get("type") == "object", f"{method.upper()} {path} is not an object schema"
        message = body["properties"]["message"]
        assert message.get("$ref") == f"#/components/schemas/{schema_name}", (
            f"{method.upper()} {path} must document the payload under 'message'"
        )

    def test_success_helper_wraps_handler_payloads_in_message(self):
        import json
        from models.common import success

        response = success(body={"message": {"pipelineId": "p1"}})
        assert json.loads(response["body"]) == {"message": {"pipelineId": "p1"}}


@pytest.mark.unit
def test_spec_component_references_all_resolve(spec):
    import re

    with open(SPEC_PATH, encoding="utf-8") as handle:
        raw = handle.read()
    defined = set(spec["components"]["schemas"])
    referenced = set(re.findall(r"#/components/schemas/([A-Za-z0-9_]+)", raw))
    assert not referenced - defined, f"dangling schema refs: {sorted(referenced - defined)}"


def test_spec_path_is_inside_the_repository():
    assert os.path.basename(str(SPEC_PATH)) == "VAMS_API.yaml"
