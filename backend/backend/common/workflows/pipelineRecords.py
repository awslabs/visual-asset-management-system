# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pure helpers for the pipeline V2 storage data model.

This module has NO AWS or environment dependencies so it can be imported and unit-tested in
isolation, mirroring common/workflows/executionRecords.py. It centralizes:
  - clean composite-key construction for the database-scoped pipeline + template tables
  - record-dict builders for the pipeline, template, and template-tag-schema rows
  - the pipeline system-config / execution-config default shapes

Tables (see storageBuilder-nestedStack.ts):
  - PipelineStorageTableV2:              PK databaseId, SK pipelineId
  - PipelineTemplatesStorageTable:       PK pipelineDatabaseId:pipelineId, SK templateId
  - PipelineTemplateTagSchemaStorageTable: PK tagSchemaId, SK pipelineDatabaseId:pipelineId:templateId
"""

import json
import uuid

from common.workflows.executionRecords import METADATA_INPUT_DEFAULTS, iso_now, pipeline_composite_key

# Pipeline record schema version (record-shape version, distinct from a workflow's aslSchemaVersion).
PIPELINE_SCHEMA_VERSION = 1
TEMPLATE_SCHEMA_VERSION = 1
TAG_SCHEMA_VERSION = 1

# Constant PK for the global (cross-database) pipeline-list GSI (query, not table scan).
ALL_PIPELINES_LIST_PARTITION = "pipeline"

# Inline-vs-S3 discriminator values for the hybrid template body storage.
BODY_STORAGE_INLINE = "inline"
BODY_STORAGE_S3 = "s3"


def new_guid() -> str:
    """Generate a VAMS pipeline/template/tag-schema GUID (32 hex chars)."""
    return uuid.uuid4().hex


def apply_pipeline_constraint_fields(obj, pipeline_record):
    """Surface the pipeline ABAC constraint fields (name, pipelineExecutionType) on a Tier-2 Casbin
    object built from a pipeline record. The execution type is stored structurally under
    executionConfig.executionType; ABAC rules reference the flat `pipelineExecutionType` field, so it
    is mapped up here — otherwise a constraint on pipelineExecutionType evaluates against an empty
    value and silently never matches. Mutates and returns obj."""
    obj.setdefault("name", pipeline_record.get("pipelineName", ""))
    obj["pipelineExecutionType"] = (
        pipeline_record.get("executionConfig") or {}).get("executionType", "Lambda")
    return obj


def template_owner_key(pipeline_database_id: str, pipeline_id: str, template_id: str) -> str:
    """Clean 'pipelineDatabaseId:pipelineId:templateId' owner key for a tag schema row."""
    return f"{pipeline_composite_key(pipeline_database_id, pipeline_id)}:{template_id}"


def build_pipeline_system_config(
    input_file_arity="one",
    asset_scope=None,
    metadata_inputs=None,
    require_template=False,
    allow_custom_template_override=False,
    aux_preview_pipeline_suffix="",
    input_file_filters=None,
):
    """Pipeline system-config block (admin-only). Defaults are the most permissive-safe choices.

    - input_file_arity: none | one | multi
    - asset_scope: {crossAssetAllowed, singleAssetOnly, wholeAssetAllowed, folderAllowed} booleans
    - metadata_inputs: {assetMetadata, fileMetadata, fileAttributes, databaseMetadata} booleans
    - input_file_filters: {allow: [...], exclude: [...]} of ext/path/name/wildcard
    """
    return {
        "inputFileArity": input_file_arity or "one",
        "assetScope": asset_scope or {
            "crossAssetAllowed": False,
            "singleAssetOnly": True,
            "wholeAssetAllowed": False,
            "folderAllowed": False,
        },
        "metadataInputs": metadata_inputs or dict(METADATA_INPUT_DEFAULTS),
        "requireTemplate": bool(require_template),
        "allowCustomTemplateOverride": bool(allow_custom_template_override),
        "auxPreviewPipelineSuffix": aux_preview_pipeline_suffix or "",
        "inputFileFilters": input_file_filters or {"allow": [], "exclude": []},
    }


def build_pipeline_execution_config(
    execution_type="Lambda",
    wait_for_callback="Disabled",
    task_timeout="",
    task_heartbeat_timeout="",
    lambda_config=None,
    sqs_config=None,
    event_bridge_config=None,
    deadline_cloud_config=None,
):
    """Typed execution-config block replacing the loose userProvidedResource JSON string."""
    return {
        "executionType": execution_type or "Lambda",
        "waitForCallback": wait_for_callback or "Disabled",
        "taskTimeout": task_timeout or "",
        "taskHeartbeatTimeout": task_heartbeat_timeout or "",
        "lambda": lambda_config or {},
        "sqs": sqs_config or {},
        "eventBridge": event_bridge_config or {},
        "deadlineCloud": deadline_cloud_config or {},
    }


def build_pipeline_record(
    database_id, pipeline_id, pipeline_name, category, description,
    execution_config, system_config,
    enabled=True, archived=False, created_by="", modified_by="",
    date_created="", date_modified="",
):
    """PipelineStorageTableV2 row (database-scoped: PK databaseId, SK pipelineId)."""
    now = iso_now()
    return {
        "databaseId": database_id,  # PK
        "pipelineId": pipeline_id,  # SK
        "databaseId:category": f"{database_id}:{category or ''}",  # GSI PK
        "allListPartition": ALL_PIPELINES_LIST_PARTITION,  # by-date GSI PK (global list)
        "pipelineName": pipeline_name or "",
        "category": category or "",
        "description": description or "",
        "executionConfig": execution_config or build_pipeline_execution_config(),
        "systemConfig": system_config or build_pipeline_system_config(),
        "enabled": bool(enabled),
        "archived": bool(archived),
        "dateCreated": date_created or now,
        "dateModified": date_modified or now,
        "createdBy": created_by or "",
        "modifiedBy": modified_by or "",
        "schemaVersion": PIPELINE_SCHEMA_VERSION,
    }


def build_template_record(
    pipeline_database_id, pipeline_id, template_id, template_name, description,
    config_format="json", allow_custom_edit=False, input_instructions="",
    body_storage=BODY_STORAGE_INLINE, config_body="", web_form_json="",
    config_body_s3_key="", config_body_hash="", web_form_s3_key="", web_form_hash="",
    overrides=None, is_default=False, created_by="", modified_by="", date_created="",
    date_modified="",
):
    """PipelineTemplatesStorageTable row (PK pipelineDatabaseId:pipelineId, SK templateId).

    `overrides` optionally overrides the pipeline's inputFileArity / metadataInputs / assetScope /
    inputFileFilters for executions using this template. `body_storage` selects inline vs S3: when
    inline, config_body/web_form_json carry the content; when s3, the *_s3_key/*_hash fields point
    at the offloaded objects and the content fields are empty.
    """
    now = iso_now()
    return {
        "pipelineDatabaseId:pipelineId": pipeline_composite_key(pipeline_database_id, pipeline_id),  # PK
        "templateId": template_id,  # SK
        "pipelineDatabaseId": pipeline_database_id,
        "pipelineId": pipeline_id,
        "templateName": template_name or "",
        "description": description or "",
        "configFormat": config_format or "json",
        "allowCustomEdit": bool(allow_custom_edit),
        "inputInstructions": input_instructions or "",
        "bodyStorage": body_storage or BODY_STORAGE_INLINE,
        "configBody": config_body or "",
        "webFormJson": web_form_json or "",
        "configBodyS3Key": config_body_s3_key or "",
        "configBodyHash": config_body_hash or "",
        "webFormS3Key": web_form_s3_key or "",
        "webFormHash": web_form_hash or "",
        # Optional per-template overrides of the pipeline defaults (empty {} = no override).
        "overrides": overrides or {},
        # At most one template per pipeline is the default (auto-selected at execute time).
        "isDefault": bool(is_default),
        "dateCreated": date_created or now,
        "dateModified": date_modified or now,
        "createdBy": created_by or "",
        "modifiedBy": modified_by or "",
        "schemaVersion": TEMPLATE_SCHEMA_VERSION,
    }


def build_tag_schema_record(
    pipeline_database_id, pipeline_id, template_id, fields,
    tag_schema_id="", body_storage=BODY_STORAGE_INLINE, fields_s3_key="", fields_hash="",
    created_by="", modified_by="", date_created="", date_modified="",
):
    """PipelineTemplateTagSchemaStorageTable row.

    Mirrors MetadataSchemaStorageTableV2 exactly: one row per template with the tag-field
    definitions stored INLINE as a JSON string under `fields` (PK tagSchemaId UUID, SK owner key).
    `fields` is a list of tag definitions; it is serialized here. When the serialized schema is
    large enough to offload, body_storage='s3' and fields_s3_key/fields_hash point at the object
    while `fields` is emptied.
    """
    now = iso_now()
    fields_json = "" if body_storage == BODY_STORAGE_S3 else json.dumps(fields or [])
    return {
        "tagSchemaId": tag_schema_id or new_guid(),  # PK
        "pipelineDatabaseId:pipelineId:templateId": template_owner_key(
            pipeline_database_id, pipeline_id, template_id),  # SK / GSI PK
        "pipelineDatabaseId": pipeline_database_id,
        "pipelineId": pipeline_id,
        "templateId": template_id,
        "bodyStorage": body_storage or BODY_STORAGE_INLINE,
        "fields": fields_json,
        "fieldsS3Key": fields_s3_key or "",
        "fieldsHash": fields_hash or "",
        "dateCreated": date_created or now,
        "dateModified": date_modified or now,
        "createdBy": created_by or "",
        "modifiedBy": modified_by or "",
        "schemaVersion": TAG_SCHEMA_VERSION,
    }
