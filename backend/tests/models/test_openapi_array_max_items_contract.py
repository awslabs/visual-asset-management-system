# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract tests binding the `maxItems` of arrays in documentation/VAMS_API.yaml to the backend.

A `maxItems` in the spec is a promise. On a request array it is the count above which the API
answers `400`; a client generated from the spec enforces it before the request leaves, so a value
lower than the API's turns a legal request into a client-side error and a higher one hides the
point where the API refuses. On a response array it is the most entries one response carries.

Each array below has a bound that can be read off the code mechanically, so the expected value is
read from there rather than restated:

* MODEL_FIELD_BOUNDS: request arrays whose Pydantic field carries `max_items`. The bound is read off
  the field, so a change to either the model or the spec that is not mirrored fails here.
* CONSTANT_BOUNDS: arrays bounded by a model/common constant instead of a field: a request list a
  root validator counts, a list another model's bound limits (ingest forwards its files to the
  upload models), a page size a list response is served at, and a fixed set the response is a
  subset of.

Arrays bounded only in handler code (page caps in services, single-entry warning lists, tree
caps) and arrays the API does not bound are documented in the spec but not pinned here; reading
them would import the handlers. A positive control asserts every pinned path really is an array,
so a renamed schema cannot leave a mapping that checks nothing.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

SPEC_PATH = Path(__file__).resolve().parents[3] / "documentation" / "VAMS_API.yaml"

C = "components/schemas/"
RB = "/requestBody/content/application~1json/schema/properties/"
R200 = "/responses/200/content/application~1json/schema/properties/"
DOWNLOAD = "paths/~1database~1{databaseId}~1assets~1{assetId}~1download/post"
EXPORT = "paths/~1database~1{databaseId}~1assets~1{assetId}~1export/post"
EXECUTE = "paths/~1workflows~1{workflowDatabaseId}~1{workflowId}~1execute/post"


def _component(dotted):
    """'schema.field.sub' -> components/schemas/schema/properties/field/properties/sub"""
    return C + dotted.replace(".", "/properties/")


def _model_field_bounds():
    """Spec pointer -> (model class, field name) for request arrays bounded by Field(max_items)."""
    from models.assetExport import AssetExportRequestModel
    from models.assetLinks import CreateAssetLinkRequestModel, UpdateAssetLinkRequestModel
    from models.assetsV3 import (
        CompleteUploadRequestModel,
        InitializeUploadRequestModel,
        UploadFileCompletionModel,
    )
    from models.executions import ExecuteWorkflowRequestV2Model, PipelineExecutionParameters
    from models.metadata import (
        CreateAssetLinkMetadataRequestModel,
        CreateAssetMetadataRequestModel,
        CreateDatabaseMetadataRequestModel,
        CreateFileMetadataRequestModel,
        DeleteAssetLinkMetadataRequestModel,
        DeleteAssetMetadataRequestModel,
        DeleteDatabaseMetadataRequestModel,
        DeleteFileMetadataRequestModel,
        UpdateAssetLinkMetadataRequestModel,
        UpdateAssetMetadataRequestModel,
        UpdateDatabaseMetadataRequestModel,
        UpdateFileMetadataRequestModel,
    )
    from models.metadataSchema import MetadataSchemaFieldModel, MetadataSchemaFieldsModel
    from models.pipelines import SetTagSchemaRequestModel, TemplateTagFieldModel
    from models.roleConstraints import (
        ConstraintCriteriaModel,
        CreateConstraintRequestModel,
        CreateUserRolesRequestModel,
        ImportConstraintsTemplateRequestModel,
        TemplateConstraintDefinition,
        UpdateUserRolesRequestModel,
    )
    from models.search import SearchRequestModel, SimpleSearchRequestModel
    from models.workflows import CreateWorkflowRequestModel, UpdateWorkflowRequestModel

    bounds = {
        EXPORT + RB + "fileExtensions": (AssetExportRequestModel, "fileExtensions"),
        EXECUTE + RB + "inputFiles": (ExecuteWorkflowRequestV2Model, "inputFiles"),
        EXECUTE + RB + "metadataSourceAssets": (ExecuteWorkflowRequestV2Model, "metadataSourceAssets"),
        EXECUTE + RB + "pipelineExecutionParameters/additionalProperties/properties/templateTags":
            (PipelineExecutionParameters, "templateTags"),
        _component("constraintCriteria.value") + "/oneOf/1": (ConstraintCriteriaModel, "value"),
        _component("importConstraintsTemplateRequest.variables"):
            (ImportConstraintsTemplateRequestModel, "variables"),
        _component("importConstraintsTemplateRequest.constraints"):
            (ImportConstraintsTemplateRequestModel, "constraints"),
        _component("createUserRolesRequest.roleName"): (CreateUserRolesRequestModel, "roleName"),
        _component("updateUserRolesRequest.roleName"): (UpdateUserRolesRequestModel, "roleName"),
        _component("metadataSchemaFieldDefinition.dependsOnFieldKeyName"):
            (MetadataSchemaFieldModel, "dependsOnFieldKeyName"),
        _component("metadataSchemaFieldDefinition.controlledListKeys"):
            (MetadataSchemaFieldModel, "controlledListKeys"),
        _component("metadataSchemaFields.fields"): (MetadataSchemaFieldsModel, "fields"),
        _component("pipelineTemplateTagField.enumValues"): (TemplateTagFieldModel, "enumValues"),
        _component("pipelineTagSchema.fields"): (SetTagSchemaRequestModel, "fields"),
        _component("createWorkflowRequest.specifiedPipelines"): (CreateWorkflowRequestModel, "specifiedPipelines"),
        _component("updateWorkflowRequest.specifiedPipelines"): (UpdateWorkflowRequestModel, "specifiedPipelines"),
        _component("initializeUploadRequest.files"): (InitializeUploadRequestModel, "files"),
        _component("uploadFileCompletion.parts"): (UploadFileCompletionModel, "parts"),
        _component("completeUploadRequest.files"): (CompleteUploadRequestModel, "files"),
        _component("createAssetLinkRequest.tags"): (CreateAssetLinkRequestModel, "tags"),
        _component("updateAssetLinkRequest.tags"): (UpdateAssetLinkRequestModel, "tags"),
        _component("simpleSearchRequest.entityTypes"): (SimpleSearchRequestModel, "entityTypes"),
        _component("simpleSearchRequest.tags"): (SimpleSearchRequestModel, "tags"),
        _component("searchRequest.tokens"): (SearchRequestModel, "tokens"),
        _component("searchRequest.filters"): (SearchRequestModel, "filters"),
        _component("searchRequest.sort"): (SearchRequestModel, "sort"),
        _component("searchRequest.entityTypes"): (SearchRequestModel, "entityTypes"),
    }
    for field in ("criteriaAnd", "criteriaOr", "groupPermissions", "userPermissions"):
        bounds[_component("createConstraintRequest." + field)] = (CreateConstraintRequestModel, field)
    for field in ("criteriaAnd", "criteriaOr", "groupPermissions"):
        bounds[_component("importConstraintsTemplateRequest.constraints") + "/items/properties/" + field] = (
            TemplateConstraintDefinition, field)
    for entity, create, update, delete in (
        ("AssetLink", CreateAssetLinkMetadataRequestModel, UpdateAssetLinkMetadataRequestModel,
         DeleteAssetLinkMetadataRequestModel),
        ("Asset", CreateAssetMetadataRequestModel, UpdateAssetMetadataRequestModel,
         DeleteAssetMetadataRequestModel),
        ("File", CreateFileMetadataRequestModel, UpdateFileMetadataRequestModel,
         DeleteFileMetadataRequestModel),
        ("Database", CreateDatabaseMetadataRequestModel, UpdateDatabaseMetadataRequestModel,
         DeleteDatabaseMetadataRequestModel),
    ):
        bounds[_component(f"create{entity}MetadataRequest.metadata")] = (create, "metadata")
        bounds[_component(f"update{entity}MetadataRequest.metadata")] = (update, "metadata")
        bounds[_component(f"delete{entity}MetadataRequest.metadataKeys")] = (delete, "metadataKeys")
    return bounds


def _constant_bounds():
    """Spec pointer -> (expected maxItems, where it comes from) for arrays bounded by a constant."""
    from backend.backend.common.constants import (
        CONSTRAINT_OPERATOR_LABELS,
        CONSTRAINT_PERMISSION_LABELS,
        CONSTRAINT_PERMISSION_TYPE_LABELS,
    )
    from models import assetExport, assetHistory, assetLinks, assetsV3, authRoutes, databases, metadata
    from models import metadataSchema, pipelines, roleConstraints, search, workflows

    history_page = assetHistory.GetAssetHistoryRequestModel.__fields__["pageSize"].field_info.le
    search_size = search.SearchRequestModel.__fields__["size"].field_info.le
    filter_patterns = pipelines.MAX_INPUT_FILE_FILTER_PATTERNS
    pipelines_per_workflow = workflows.MAX_SPECIFIED_PIPELINES
    bounds = {
        # Request arrays a root validator counts.
        DOWNLOAD + RB + "keys": assetsV3.MAX_KEYS_PER_DOWNLOAD_REQUEST,
        _component("auth_request.routes"): authRoutes.MAX_WEB_ROUTES_PER_REQUEST,
        _component("pipelineInputFileFilters.allow"): filter_patterns,
        _component("pipelineInputFileFilters.exclude"): filter_patterns,
        _component("createPipelineTemplateRequest.tagSchema"): pipelines.MAX_TAG_SCHEMA_FIELDS,
        _component("updatePipelineTemplateRequest.tagSchema"): pipelines.MAX_TAG_SCHEMA_FIELDS,
        # Ingest forwards its files to the upload initialize/complete models.
        _component("ingestAssetInitializeRequest.files"): assetsV3.MAX_FILES_PER_UPLOAD_REQUEST,
        _component("ingestAssetCompleteRequest.files"): assetsV3.MAX_FILES_PER_UPLOAD_REQUEST,
        # Responses with one entry per bounded request entry.
        DOWNLOAD + R200 + "files": assetsV3.MAX_KEYS_PER_DOWNLOAD_REQUEST,
        _component("auth_response.allowedRoutes"): authRoutes.MAX_WEB_ROUTES_PER_REQUEST,
        _component("importConstraintsTemplateResponse.constraintIds"): roleConstraints.MAX_CONSTRAINTS_PER_TEMPLATE,
        _component("initializeUploadResponse.files"): assetsV3.MAX_FILES_PER_UPLOAD_REQUEST,
        _component("ingestAssetInitializeResponse.files"): assetsV3.MAX_FILES_PER_UPLOAD_REQUEST,
        _component("completeUploadResponse.fileResults"): assetsV3.MAX_FILES_PER_UPLOAD_REQUEST,
        _component("ingestAssetCompleteResponse.fileResults"): assetsV3.MAX_FILES_PER_UPLOAD_REQUEST,
        _component("uploadFileResponse.partUploadUrls"): assetsV3.MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST,
        _component("bulkMetadataOperationResponse.successfulItems"): metadata.MAX_METADATA_ITEMS_PER_REQUEST,
        _component("bulkMetadataOperationResponse.failedItems"): metadata.MAX_METADATA_ITEMS_PER_REQUEST,
        # Stored values bounded by the same request bound on every write path.
        _component("pipelineTagSchemaResponse.fields"): pipelines.MAX_TAG_SCHEMA_FIELDS,
        _component("pipelineTemplate.tagSchema"): pipelines.MAX_TAG_SCHEMA_FIELDS,
        _component("workflowV2.specifiedPipelines"): pipelines_per_workflow,
        _component("assetLinkModel.tags"): assetLinks.MAX_TAGS_PER_ASSET_LINK,
        # Union of the workflow's own filter list and its pipelines' lists.
        _component("workflowV2.aggregateWorkflowPipelineInputFileFilters.allow"):
            pipelines_per_workflow * filter_patterns,
        _component("workflowV2.aggregateWorkflowPipelineInputFileFilters.exclude"):
            (pipelines_per_workflow + 1) * filter_patterns,
        # List responses served at most one page of this size.
        "paths/~1assets/get" + R200 + "Items": assetsV3.MAX_ASSET_LIST_PAGE_SIZE,
        "paths/~1database~1{databaseId}~1assets/get" + R200 + "Items": assetsV3.MAX_ASSET_LIST_PAGE_SIZE,
        "paths/~1database/get" + R200 + "Items": databases.MAX_LIST_PAGE_SIZE,
        _component("getBucketsResponse.Items"): databases.MAX_LIST_PAGE_SIZE,
        EXPORT + R200 + "assets": assetExport.MAX_ASSETS_PER_EXPORT_PAGE,
        EXPORT + R200 + "assets/items/properties/files": assetExport.MAX_FILES_PER_EXPORT_PAGE,
        _component("assetVersionsListResponse.versions"): assetsV3.MAX_VERSION_LIST_PAGE_SIZE,
        _component("getAssetHistoryResponse.Items"): history_page,
        _component("getMetadataSchemasResponse.Items"): metadataSchema.MAX_SCHEMA_LIST_PAGE_SIZE,
        _component("getConstraintsResponse.message.Items"): roleConstraints.MAX_CONSTRAINT_LIST_PAGE_SIZE,
        _component("getRolesResponse.message.Items"): roleConstraints.MAX_LIST_MAX_ITEMS,
        _component("getUserRolesResponse.message.Items"): roleConstraints.MAX_LIST_MAX_ITEMS,
        _component("searchHits.hits"): search_size,
        # Fixed sets the response lists.
        _component("getConstraintPermissionObjectsResponse.operators"): len(CONSTRAINT_OPERATOR_LABELS),
        _component("getConstraintPermissionObjectsResponse.permissions"): len(CONSTRAINT_PERMISSION_LABELS),
        _component("getConstraintPermissionObjectsResponse.permissionTypes"): len(CONSTRAINT_PERMISSION_TYPE_LABELS),
    }
    for entity in ("assetLink", "asset", "file", "database"):
        bounds[_component(f"{entity}MetadataModel.metadataSchemaControlledListKeys")] = (
            metadataSchema.MAX_CONTROLLED_LIST_KEYS)
        bounds[_component(f"get{entity[0].upper()}{entity[1:]}MetadataResponse.metadata")] = (
            metadata.MAX_METADATA_PAGE_SIZE)
    return bounds


@pytest.fixture(scope="module")
def spec():
    if not SPEC_PATH.is_file():
        pytest.skip(f"OpenAPI spec not found at {SPEC_PATH}")
    with open(SPEC_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _node(spec, pointer):
    """Resolve a '/'-separated pointer (with '~1' for '/') against the loaded spec."""
    node = spec
    for part in pointer.split("/"):
        part = part.replace("~1", "/")
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


@pytest.mark.unit
class TestOpenApiArrayMaxItemsContract:
    @pytest.mark.parametrize("pointer", sorted(_model_field_bounds()))
    def test_request_array_max_items_matches_the_model_field(self, spec, pointer):
        model, field = _model_field_bounds()[pointer]
        bound = model.__fields__[field].field_info.max_items
        # Positive control: the mapping names a bounded field, so a dropped max_items fails here
        # rather than comparing the spec against None.
        assert bound is not None, f"{model.__name__}.{field} carries no max_items"
        node = _node(spec, pointer)
        assert node.get("type") == "array", pointer
        assert node.get("maxItems") == bound, (
            f"{pointer}: spec maxItems {node.get('maxItems')} != {model.__name__}.{field} max_items {bound}")

    @pytest.mark.parametrize("pointer", sorted(_constant_bounds()))
    def test_array_max_items_matches_the_backend_constant(self, spec, pointer):
        expected = _constant_bounds()[pointer]
        assert isinstance(expected, int) and expected > 0, pointer
        node = _node(spec, pointer)
        assert node.get("type") == "array", pointer
        assert node.get("maxItems") == expected, (
            f"{pointer}: spec maxItems {node.get('maxItems')} != backend bound {expected}")

    def test_the_two_mappings_do_not_overlap(self):
        assert not set(_model_field_bounds()) & set(_constant_bounds())
