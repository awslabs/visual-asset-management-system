# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The audit action registry: what every API route-method and every non-API handler records.

Each route-method of ``common.apiRoutes.ALL_API_ROUTES`` has an :class:`AuditSpec` in
:data:`AUDIT_ROUTE_ACTIONS` or a reason in :data:`AUDIT_EXEMPT`; ``tests/common/test_audit_registry.py``
fails for a route-method that has neither. ``customLogging.auditLogging.log_action`` looks the action
up here to choose the log group, the ``[type: ...]`` prefix value and the entity type, and the audit
request context (``audit_request``) resolves the route template a record names from the same list.

The core entries are below; the add-on entries are in ``common/addonAuditActions.py`` and a fork's in
``custom/customAuditActions.py``. The lists merge in the order core, add-on, custom; an add-on or
custom entry never replaces a core one (the registry tests fail on a repeated route-method or a
repeated action name that disagrees on group, kind or legacy type).

Adding a route: add its entry here (or in the add-on or custom list), call ``log_action`` from the
handler after the write succeeds (``workflowService.py`` is the reference placement), and remove the
action from ``AUDIT_PENDING_ACTIONS`` if it was listed there.
"""

from typing import Dict, FrozenSet, Optional, Tuple

from common.apiRoutes import (
    ALL_API_ROUTES,
    API_AMPLIFY_CONFIG,
    API_ARCHIVE_ASSET,
    API_ARCHIVE_FILE,
    API_ASSETS,
    API_ASSET_EXPORT,
    API_ASSET_LINKS,
    API_ASSET_LINKS_BY_ID,
    API_ASSET_LINKS_FOR_ASSET,
    API_ASSET_LINKS_SINGLE,
    API_ASSET_LINK_METADATA,
    API_ASSET_METADATA,
    API_ASSET_VERSION_ARCHIVE,
    API_ASSET_VERSION_BY_ID,
    API_ASSET_VERSION_UNARCHIVE,
    API_AUTH_API_KEYS,
    API_AUTH_API_KEY_BY_ID,
    API_AUTH_CONSTRAINTS,
    API_AUTH_CONSTRAINTS_TEMPLATE_IMPORT,
    API_AUTH_CONSTRAINT_BY_ID,
    API_AUTH_CONSTRAINT_PERMISSION_OBJECTS,
    API_AUTH_LOGIN_PROFILE,
    API_AUTH_ROUTES,
    API_AUTH_ROUTES_API,
    API_AUTH_ROUTES_API_ALLOWED,
    API_AUTH_USER_API_KEYS,
    API_AUTH_USER_API_KEY_BY_ID,
    API_AUXILIARY_PREVIEW_ASSETS_STREAM,
    API_BUCKETS,
    API_CHECK_SUBSCRIPTION,
    API_COGNITO_USERS,
    API_COGNITO_USER_BY_ID,
    API_COGNITO_USER_RESET_PASSWORD,
    API_COMMENTS_ASSET,
    API_COMMENTS_ASSET_VERSION,
    API_COMMENTS_ASSET_VERSION_COMMENT,
    API_COPY_FILE,
    API_CREATE_ASSET_VERSION,
    API_CREATE_FOLDER,
    API_DATABASE,
    API_DATABASE_ASSET,
    API_DATABASE_ASSETS,
    API_DATABASE_BY_ID,
    API_DATABASE_METADATA,
    API_DATABASE_PIPELINE,
    API_DATABASE_PIPELINES,
    API_DATABASE_WORKFLOW,
    API_DATABASE_WORKFLOWS,
    API_DELETE_ASSET,
    API_DELETE_ASSET_PREVIEW,
    API_DELETE_AUXILIARY_PREVIEW,
    API_DELETE_FILE,
    API_DOWNLOAD_ASSET,
    API_DOWNLOAD_ASSET_STREAM,
    API_EXECUTE_WORKFLOW,
    API_FILE_INFO,
    API_FILE_METADATA,
    API_GET_ASSET_HISTORY,
    API_GET_ASSET_VERSION,
    API_GET_ASSET_VERSIONS,
    API_INGEST_ASSET,
    API_LIST_FILES,
    API_METADATA_SCHEMA,
    API_METADATA_SCHEMA_BY_ID,
    API_MOVE_FILE,
    API_PIPELINES,
    API_PIPELINE_TEMPLATE,
    API_PIPELINE_TEMPLATES,
    API_PIPELINE_TEMPLATE_TAG_SCHEMA,
    API_REVERT_ASSET_VERSION,
    API_REVERT_FILE_VERSION,
    API_ROLES,
    API_ROLE_BY_ID,
    API_SEARCH,
    API_SEARCH_NLP,
    API_SEARCH_SIMPLE,
    API_SECURE_CONFIG,
    API_SET_PRIMARY_FILE,
    API_SUBSCRIPTIONS,
    API_TAGS,
    API_TAG_BY_ID,
    API_TAG_TYPES,
    API_TAG_TYPE_BY_ID,
    API_UNARCHIVE_ASSET,
    API_UNARCHIVE_FILE,
    API_UNSUBSCRIBE,
    API_UPLOADS,
    API_UPLOAD_COMPLETE,
    API_UPLOAD_COMPLETE_EXTERNAL,
    API_USER_ROLES,
    API_VERSION,
    API_WORKFLOWS,
    API_WORKFLOW_EXECUTION,
    API_WORKFLOW_EXECUTIONS,
    API_WORKFLOW_EXECUTIONS_BY_WORKFLOW,
    API_WORKFLOW_EXECUTIONS_GLOBAL,
    API_WORKFLOW_EXECUTION_DETAILS,
    API_WORKFLOW_EXECUTION_DETAILS_FILES,
    API_WORKFLOW_EXECUTION_DETAILS_METADATA,
    API_WORKFLOW_EXECUTION_DETAILS_RESULTS,
    API_WORKFLOW_EXECUTION_LOGS,
    API_WORKFLOW_EXECUTION_PERMANENT,
    API_WORKFLOW_EXECUTION_RERUN,
    API_WORKFLOW_TRIGGER,
    API_WORKFLOW_TRIGGERS,
)
from common.apiRouteTypes import DELETE, GET, HEAD, POST, PUT
from common.auditActionTypes import *  # noqa: F401,F403
from common.auditActionTypes import (
    GROUP_ACTIONS,
    GROUP_AUTH_CHANGES,
    GROUP_AUTH_OTHER,
    GROUP_FILE_DOWNLOAD,
    GROUP_FILE_DOWNLOAD_STREAMED,
    GROUP_FILE_UPLOAD,
    KIND_DOWNLOAD,
    KIND_SEARCH,
    AuditSpec,
    RouteMethod,
    SystemAction,
    read,
    route_entries,
    spec,
)
from common.addonAuditActions import (
    ADDON_AUDIT_EXEMPT,
    ADDON_AUDIT_ROUTE_ACTIONS,
    ADDON_AUDIT_SECONDARY_ACTIONS,
    ADDON_AUDIT_SYSTEM_ACTIONS,
)
from custom.customAuditActions import (
    CUSTOM_AUDIT_EXEMPT,
    CUSTOM_AUDIT_ROUTE_ACTIONS,
    CUSTOM_AUDIT_SECONDARY_ACTIONS,
    CUSTOM_AUDIT_SYSTEM_ACTIONS,
)

_CHANGES = GROUP_AUTH_CHANGES
_OTHER = GROUP_AUTH_OTHER


def _auth(action: str, entity_type: str, legacy_type: Optional[str] = None) -> AuditSpec:
    """A change to roles, constraints, keys or users: written to the auth changes group."""
    return spec(action, entity_type, group=_CHANGES, legacy_type=legacy_type)


def _auth_read(action: str, entity_type: str, legacy_type: Optional[str] = None) -> AuditSpec:
    """A read of roles, constraints, keys or users: written to the auth other group."""
    return read(action, entity_type, group=_OTHER, legacy_type=legacy_type)


_FILE_UPLOAD_START = spec("fileUploadStart", "file", group=GROUP_FILE_UPLOAD, legacy_function="log_file_upload")
_FILE_UPLOAD = spec("fileUpload", "file", group=GROUP_FILE_UPLOAD, legacy_function="log_file_upload")

CORE_AUDIT_ROUTE_ACTIONS: Dict[RouteMethod, AuditSpec] = {
    # Databases
    **route_entries(API_DATABASE, {
        GET: read("databaseList", "database"),
        POST: spec("databaseCreate", "database"),
    }),
    **route_entries(API_DATABASE_BY_ID, {
        GET: read("databaseGet", "database"),
        PUT: spec("databaseUpdate", "database"),
        DELETE: spec("databaseDelete", "database"),
    }),
    # Assets
    **route_entries(API_ASSETS, {
        GET: read("assetList", "asset"),
        POST: spec("assetCreate", "asset"),
    }),
    **route_entries(API_DATABASE_ASSETS, {GET: read("assetList", "asset")}),
    **route_entries(API_DATABASE_ASSET, {
        GET: read("assetGet", "asset"),
        PUT: spec("assetUpdate", "asset"),
    }),
    **route_entries(API_ARCHIVE_ASSET, {DELETE: spec("assetArchive", "asset")}),
    **route_entries(API_UNARCHIVE_ASSET, {PUT: spec("assetUnarchive", "asset")}),
    **route_entries(API_DELETE_ASSET, {DELETE: spec("assetDelete", "asset")}),
    **route_entries(API_INGEST_ASSET, {POST: spec("assetIngest", "asset")}),
    **route_entries(API_DOWNLOAD_ASSET, {
        POST: spec("fileDownload", "file", KIND_DOWNLOAD, GROUP_FILE_DOWNLOAD, legacy_function="log_file_download"),
    }),
    **route_entries(API_DOWNLOAD_ASSET_STREAM, {
        GET: spec("fileStream", "file", KIND_DOWNLOAD, GROUP_FILE_DOWNLOAD_STREAMED,
                  legacy_function="log_file_download_streamed"),
    }),
    **route_entries(API_AUXILIARY_PREVIEW_ASSETS_STREAM, {
        GET: spec("auxiliaryPreviewStream", "file", KIND_DOWNLOAD, GROUP_FILE_DOWNLOAD_STREAMED,
                  legacy_function="log_file_download_streamed"),
    }),
    **route_entries(API_ASSET_EXPORT, {POST: spec("assetExport", "export", KIND_DOWNLOAD)}),
    **route_entries(API_GET_ASSET_HISTORY, {GET: read("assetHistoryGet", "asset")}),
    # Asset files
    **route_entries(API_LIST_FILES, {GET: read("fileList", "file")}),
    **route_entries(API_FILE_INFO, {GET: read("fileInfoGet", "file")}),
    **route_entries(API_MOVE_FILE, {POST: spec("fileMove", "file")}),
    **route_entries(API_COPY_FILE, {POST: spec("fileCopy", "file")}),
    **route_entries(API_ARCHIVE_FILE, {DELETE: spec("fileArchive", "file")}),
    **route_entries(API_UNARCHIVE_FILE, {POST: spec("fileUnarchive", "file")}),
    **route_entries(API_DELETE_FILE, {DELETE: spec("fileDelete", "file")}),
    **route_entries(API_DELETE_ASSET_PREVIEW, {DELETE: spec("assetPreviewDelete", "file")}),
    **route_entries(API_DELETE_AUXILIARY_PREVIEW, {DELETE: spec("auxiliaryPreviewDelete", "file")}),
    **route_entries(API_REVERT_FILE_VERSION, {POST: spec("fileVersionRevert", "fileVersion")}),
    **route_entries(API_SET_PRIMARY_FILE, {PUT: spec("filePrimarySet", "file")}),
    **route_entries(API_CREATE_FOLDER, {POST: spec("folderCreate", "folder")}),
    # Asset versions
    **route_entries(API_CREATE_ASSET_VERSION, {POST: spec("assetVersionCreate", "assetVersion")}),
    **route_entries(API_REVERT_ASSET_VERSION, {POST: spec("assetVersionRevert", "assetVersion")}),
    **route_entries(API_GET_ASSET_VERSIONS, {GET: read("assetVersionList", "assetVersion")}),
    **route_entries(API_GET_ASSET_VERSION, {GET: read("assetVersionGet", "assetVersion")}),
    **route_entries(API_ASSET_VERSION_BY_ID, {PUT: spec("assetVersionUpdate", "assetVersion")}),
    **route_entries(API_ASSET_VERSION_ARCHIVE, {POST: spec("assetVersionArchive", "assetVersion")}),
    **route_entries(API_ASSET_VERSION_UNARCHIVE, {POST: spec("assetVersionUnarchive", "assetVersion")}),
    # Uploads
    **route_entries(API_UPLOADS, {POST: _FILE_UPLOAD_START}),
    **route_entries(API_UPLOAD_COMPLETE, {POST: _FILE_UPLOAD}),
    **route_entries(API_UPLOAD_COMPLETE_EXTERNAL, {
        POST: spec("pipelineOutputUpload", "file", group=GROUP_FILE_UPLOAD, legacy_function="log_file_upload"),
    }),
    # Comments
    **route_entries(API_COMMENTS_ASSET, {GET: read("commentList", "comment")}),
    **route_entries(API_COMMENTS_ASSET_VERSION, {GET: read("commentList", "comment")}),
    **route_entries(API_COMMENTS_ASSET_VERSION_COMMENT, {
        GET: read("commentGet", "comment"),
        POST: spec("commentCreate", "comment"),
        PUT: spec("commentUpdate", "comment"),
        DELETE: spec("commentDelete", "comment"),
    }),
    # Metadata: one action per verb; entity.type names the metadata's target (asset link, asset,
    # file or database).
    **{
        key: value
        for route, target in ((API_ASSET_LINK_METADATA, "assetLink"), (API_ASSET_METADATA, "asset"),
                              (API_FILE_METADATA, "file"), (API_DATABASE_METADATA, "database"))
        for key, value in route_entries(route, {
            GET: read("metadataGet", target),
            POST: spec("metadataCreate", target),
            PUT: spec("metadataUpdate", target),
            DELETE: spec("metadataDelete", target),
        }).items()
    },
    # Metadata schemas
    **route_entries(API_METADATA_SCHEMA, {
        GET: read("metadataSchemaList", "metadataSchema"),
        POST: spec("metadataSchemaCreate", "metadataSchema"),
        PUT: spec("metadataSchemaUpdate", "metadataSchema"),
    }),
    **route_entries(API_METADATA_SCHEMA_BY_ID, {
        GET: read("metadataSchemaGet", "metadataSchema"),
        DELETE: spec("metadataSchemaDelete", "metadataSchema"),
    }),
    # Pipelines
    **route_entries(API_PIPELINES, {GET: read("pipelineList", "pipeline")}),
    **route_entries(API_DATABASE_PIPELINES, {
        GET: read("pipelineList", "pipeline"),
        POST: spec("pipelineCreate", "pipeline"),
    }),
    **route_entries(API_DATABASE_PIPELINE, {
        GET: read("pipelineGet", "pipeline"),
        PUT: spec("pipelineUpdate", "pipeline"),
        DELETE: spec("pipelineArchive", "pipeline"),
    }),
    **route_entries(API_PIPELINE_TEMPLATES, {
        GET: read("pipelineTemplateList", "pipelineTemplate"),
        POST: spec("pipelineTemplateCreate", "pipelineTemplate"),
    }),
    **route_entries(API_PIPELINE_TEMPLATE, {
        GET: read("pipelineTemplateGet", "pipelineTemplate"),
        PUT: spec("pipelineTemplateUpdate", "pipelineTemplate"),
        DELETE: spec("pipelineTemplateDelete", "pipelineTemplate"),
    }),
    **route_entries(API_PIPELINE_TEMPLATE_TAG_SCHEMA, {
        GET: read("pipelineTemplateTagSchemaGet", "pipelineTemplate"),
        PUT: spec("pipelineTemplateTagSchemaUpdate", "pipelineTemplate"),
    }),
    # Workflows
    **route_entries(API_WORKFLOWS, {GET: read("workflowList", "workflow")}),
    **route_entries(API_DATABASE_WORKFLOWS, {
        GET: read("workflowList", "workflow"),
        POST: spec("workflowCreate", "workflow"),
    }),
    **route_entries(API_DATABASE_WORKFLOW, {
        GET: read("workflowGet", "workflow"),
        PUT: spec("workflowUpdate", "workflow"),
        DELETE: spec("workflowArchive", "workflow"),
    }),
    **route_entries(API_WORKFLOW_TRIGGERS, {GET: read("workflowTriggerList", "workflowTrigger")}),
    **route_entries(API_WORKFLOW_TRIGGER, {
        GET: read("workflowTriggerGet", "workflowTrigger"),
        PUT: spec("workflowTriggerSet", "workflowTrigger"),
        DELETE: spec("workflowTriggerDelete", "workflowTrigger"),
    }),
    **route_entries(API_WORKFLOW_EXECUTIONS, {GET: read("workflowExecutionList", "execution")}),
    **route_entries(API_WORKFLOW_EXECUTIONS_BY_WORKFLOW, {GET: read("workflowExecutionList", "execution")}),
    **route_entries(API_WORKFLOW_EXECUTIONS_GLOBAL, {GET: read("workflowExecutionList", "execution")}),
    **route_entries(API_EXECUTE_WORKFLOW, {POST: spec("workflowExecute", "execution")}),
    **route_entries(API_WORKFLOW_EXECUTION, {DELETE: spec("workflowExecutionAbort", "execution")}),
    **route_entries(API_WORKFLOW_EXECUTION_DETAILS, {GET: read("workflowExecutionGet", "execution")}),
    **route_entries(API_WORKFLOW_EXECUTION_DETAILS_METADATA, {
        GET: read("workflowExecutionMetadataGet", "execution"),
    }),
    **route_entries(API_WORKFLOW_EXECUTION_DETAILS_FILES, {GET: read("workflowExecutionFilesGet", "execution")}),
    **route_entries(API_WORKFLOW_EXECUTION_DETAILS_RESULTS, {
        GET: read("workflowExecutionResultsGet", "execution"),
    }),
    **route_entries(API_WORKFLOW_EXECUTION_LOGS, {GET: read("workflowExecutionLogsGet", "execution")}),
    **route_entries(API_WORKFLOW_EXECUTION_RERUN, {POST: spec("workflowExecutionRerun", "execution")}),
    **route_entries(API_WORKFLOW_EXECUTION_PERMANENT, {
        DELETE: spec("workflowExecutionPermanentDelete", "execution"),
    }),
    # Asset links
    **route_entries(API_ASSET_LINKS, {POST: spec("assetLinkCreate", "assetLink")}),
    **route_entries(API_ASSET_LINKS_SINGLE, {GET: read("assetLinkGet", "assetLink")}),
    **route_entries(API_ASSET_LINKS_BY_ID, {
        PUT: spec("assetLinkUpdate", "assetLink"),
        DELETE: spec("assetLinkDelete", "assetLink"),
    }),
    **route_entries(API_ASSET_LINKS_FOR_ASSET, {GET: read("assetLinkList", "assetLink")}),
    # Subscriptions
    **route_entries(API_SUBSCRIPTIONS, {
        GET: read("subscriptionList", "subscription"),
        POST: spec("subscriptionCreate", "subscription"),
        PUT: spec("subscriptionUpdate", "subscription"),
        DELETE: spec("subscriptionDelete", "subscription"),
    }),
    **route_entries(API_CHECK_SUBSCRIPTION, {POST: read("subscriptionCheck", "subscription")}),
    **route_entries(API_UNSUBSCRIBE, {DELETE: spec("subscriptionUnsubscribe", "subscription")}),
    # Tags and tag types
    **route_entries(API_TAGS, {
        GET: read("tagList", "tag"),
        POST: spec("tagCreate", "tag"),
        PUT: spec("tagUpdate", "tag"),
    }),
    **route_entries(API_TAG_BY_ID, {DELETE: spec("tagDelete", "tag")}),
    **route_entries(API_TAG_TYPES, {
        GET: read("tagTypeList", "tagType"),
        POST: spec("tagTypeCreate", "tagType"),
        PUT: spec("tagTypeUpdate", "tagType"),
    }),
    **route_entries(API_TAG_TYPE_BY_ID, {DELETE: spec("tagTypeDelete", "tagType")}),
    # Roles, user roles and constraints
    **route_entries(API_ROLES, {
        GET: _auth_read("roleList", "role"),
        POST: _auth("roleCreate", "role"),
        PUT: _auth("roleUpdate", "role"),
    }),
    **route_entries(API_ROLE_BY_ID, {DELETE: _auth("roleDelete", "role")}),
    **route_entries(API_USER_ROLES, {
        GET: _auth_read("userRoleList", "userRole"),
        POST: _auth("userRoleCreate", "userRole"),
        PUT: _auth("userRoleUpdate", "userRole"),
        DELETE: _auth("userRoleDelete", "userRole"),
    }),
    **route_entries(API_AUTH_CONSTRAINTS, {GET: _auth_read("constraintList", "constraint")}),
    **route_entries(API_AUTH_CONSTRAINT_BY_ID, {
        GET: _auth_read("constraintGet", "constraint"),
        POST: _auth("constraintCreate", "constraint", legacy_type="constraintCreateUpdate"),
        PUT: _auth("constraintUpdate", "constraint", legacy_type="constraintCreateUpdate"),
        DELETE: _auth("constraintDelete", "constraint"),
    }),
    **route_entries(API_AUTH_CONSTRAINTS_TEMPLATE_IMPORT, {
        POST: _auth("constraintTemplateImport", "constraint", legacy_type="constraintsTemplateImport"),
    }),
    **route_entries(API_AUTH_LOGIN_PROFILE, {
        GET: _auth_read("loginProfileGet", "user", legacy_type="userProfileGet"),
        POST: spec("loginProfileUpdate", "user", group=_OTHER, legacy_type="userProfileUpdate"),
    }),
    # API keys: an administrator's routes and a user's own-key routes record the same actions.
    **{
        key: value
        for routes in ((API_AUTH_API_KEYS, API_AUTH_API_KEY_BY_ID),
                       (API_AUTH_USER_API_KEYS, API_AUTH_USER_API_KEY_BY_ID))
        for key, value in {
            **route_entries(routes[0], {
                GET: _auth_read("apiKeyList", "apiKey"),
                POST: _auth("apiKeyCreate", "apiKey"),
            }),
            **route_entries(routes[1], {
                GET: _auth_read("apiKeyGet", "apiKey"),
                PUT: _auth("apiKeyUpdate", "apiKey"),
                DELETE: _auth("apiKeyDelete", "apiKey"),
            }),
        }.items()
    },
    # Amazon Cognito users
    **route_entries(API_COGNITO_USERS, {
        GET: _auth_read("cognitoUserList", "user"),
        POST: _auth("cognitoUserCreate", "user"),
    }),
    **route_entries(API_COGNITO_USER_BY_ID, {
        PUT: _auth("cognitoUserUpdate", "user"),
        DELETE: _auth("cognitoUserDelete", "user"),
    }),
    **route_entries(API_COGNITO_USER_RESET_PASSWORD, {POST: _auth("cognitoUserPasswordReset", "user")}),
    # Search: the request (index, filters, page) is recorded and the hit count, never the hits.
    **route_entries(API_SEARCH, {POST: spec("search", "search", KIND_SEARCH)}),
    **route_entries(API_SEARCH_SIMPLE, {POST: spec("search", "search", KIND_SEARCH)}),
    **route_entries(API_SEARCH_NLP, {POST: spec("search", "search", KIND_SEARCH)}),
}

_HEAD_REASON = "returns object headers only, no content; the GET of the same file is recorded"
_PROBE_REASON = ("permission probe: answers which routes the caller may use; each denial is recorded "
                 "as an authorization record with the probe marker")

CORE_AUDIT_EXEMPT: Dict[RouteMethod, str] = {
    (API_SECURE_CONFIG.path, GET): "deployment configuration read, the same for every caller",
    (API_AMPLIFY_CONFIG.path, GET): "unauthenticated sign-in configuration, served before sign-in",
    (API_VERSION.path, GET): "unauthenticated version string",
    (API_BUCKETS.path, GET): "deployment configuration read: the buckets a database may use",
    (API_DOWNLOAD_ASSET_STREAM.path, HEAD): _HEAD_REASON,
    (API_AUXILIARY_PREVIEW_ASSETS_STREAM.path, HEAD): _HEAD_REASON,
    (API_AUTH_ROUTES.path, POST): _PROBE_REASON,
    (API_AUTH_ROUTES_API.path, GET): "static listing of the API routes the deployment serves",
    (API_AUTH_ROUTES_API_ALLOWED.path, GET): _PROBE_REASON,
    (API_AUTH_CONSTRAINT_PERMISSION_OBJECTS.path, GET): "static listing of the permission object types",
    (API_SEARCH.path, GET): "search index field mapping, the same for every caller",
}

# Actions recorded inside a request in addition to the route's own action.
CORE_AUDIT_SECONDARY_ACTIONS: Tuple[AuditSpec, ...] = (
    # A pipeline change refreshes the workflows that use it.
    spec("workflowPipelineRefresh", "workflow"),
    # A workflow change redeploys its state machine.
    spec("workflowRedeploy", "workflow"),
    # DELETE /workflows/executions/{executionId} on an execution group stops every member.
    spec("workflowExecutionGroupAbort", "executionGroup"),
)

# Actions recorded by handlers that serve no API route. The actor is the system; onBehalfOf names
# the user an action ran for when the handler knows it.
CORE_AUDIT_SYSTEM_ACTIONS: Tuple[SystemAction, ...] = (
    SystemAction("handlers.assets.sqsUploadFileLarge", _FILE_UPLOAD),
    SystemAction("handlers.indexing.sqsBucketSync", spec("assetSync", "asset")),
    SystemAction("handlers.indexing.sqsBucketSync", spec("fileSync", "file")),
    SystemAction("handlers.indexing.crReindexer", spec("searchReindex", "system")),
    SystemAction("handlers.osVectorSearch.vectorReindexer", spec("searchReindex", "system")),
    SystemAction("handlers.workflows.sfn.processWorkflowExecutionOutput", spec("workflowOutputIngest", "execution")),
    SystemAction("handlers.workflows.sfn.processWorkflowExecutionOutput", spec("workflowExecutionEnd", "execution")),
    SystemAction("handlers.workflows.sfn.handleExecutionError", spec("workflowExecutionEnd", "execution")),
    SystemAction("handlers.workflows.importGlobalPipelineWorkflow", spec("pipelineImport", "pipeline")),
    SystemAction("handlers.auth.apiOriginVerifyRotation", _auth("originVerifyRotate", "system")),
)

# Handlers that serve no API route and record no action, with the reason.
AUDIT_SYSTEM_EXEMPT: Dict[str, str] = {
    "handlers.osSemanticSearch.osAssetIndexer": "search index upkeep derived from a recorded change",
    "handlers.osSemanticSearch.osFileIndexer": "search index upkeep derived from a recorded change",
    "handlers.osVectorSearch.vectorIndexer": "vector index upkeep derived from a recorded change or analysis run",
    "handlers.osVectorSearch.systemWorkflowLauncher": "each launch is an executeWorkflow call, which records it",
    "handlers.indexing.snsQueuing": "fans a recorded change out to the indexers",
    "handlers.sendEmail.sendEmail": "delivers the notification of a recorded subscription",
}

# Lambda entrypoints that do not open the request audit context, with the reason. Every other
# module under handlers/ that defines a lambda_handler (or lambda_handler_<name>) decorates it with
# customLogging.auditLogging.audited_handler or enters audit_request around its work.
AUDIT_ENTRYPOINT_EXEMPT: Dict[str, str] = {
    "handlers.auth.apiGatewayAuthorizerRest": ("the API authorizer: runs before the request reaches a "
                                               "handler and writes its own authorization records"),
    "handlers.auth.pretokengenv1": "Amazon Cognito sign-in trigger: sign-in is recorded by the identity provider",
    "handlers.auth.pretokengenv2": "Amazon Cognito sign-in trigger: sign-in is recorded by the identity provider",
}


def _merge_route_actions() -> Dict[RouteMethod, AuditSpec]:
    merged: Dict[RouteMethod, AuditSpec] = dict(CORE_AUDIT_ROUTE_ACTIONS)
    for lane in (ADDON_AUDIT_ROUTE_ACTIONS, CUSTOM_AUDIT_ROUTE_ACTIONS):
        for key, value in lane.items():
            # A later lane never replaces an earlier entry; the registry tests report the repeat.
            merged.setdefault(key, value)
    return merged


def _merge_exempt() -> Dict[RouteMethod, str]:
    merged: Dict[RouteMethod, str] = dict(CORE_AUDIT_EXEMPT)
    for lane in (ADDON_AUDIT_EXEMPT, CUSTOM_AUDIT_EXEMPT):
        for key, value in lane.items():
            merged.setdefault(key, value)
    return merged


AUDIT_ROUTE_ACTIONS: Dict[RouteMethod, AuditSpec] = _merge_route_actions()
AUDIT_EXEMPT: Dict[RouteMethod, str] = _merge_exempt()
AUDIT_SECONDARY_ACTIONS: Tuple[AuditSpec, ...] = (
    CORE_AUDIT_SECONDARY_ACTIONS + ADDON_AUDIT_SECONDARY_ACTIONS + CUSTOM_AUDIT_SECONDARY_ACTIONS
)
AUDIT_SYSTEM_ACTIONS: Tuple[SystemAction, ...] = (
    CORE_AUDIT_SYSTEM_ACTIONS + ADDON_AUDIT_SYSTEM_ACTIONS + CUSTOM_AUDIT_SYSTEM_ACTIONS
)


def _actions_by_name() -> Dict[str, AuditSpec]:
    """Every registered action by its name. Entries that share a name share group, kind and legacy
    type (the registry tests hold that); the first one's entity type is the action's default."""
    actions: Dict[str, AuditSpec] = {}
    for audit_spec in (list(AUDIT_ROUTE_ACTIONS.values()) + list(AUDIT_SECONDARY_ACTIONS)
                       + [system.spec for system in AUDIT_SYSTEM_ACTIONS]):
        actions.setdefault(audit_spec.action, audit_spec)
    return actions


AUDIT_ACTIONS: Dict[str, AuditSpec] = _actions_by_name()

# Registered actions no handler records yet. Each is removed when its handler starts calling
# log_action; tests/common/test_audit_registry.py fails for an action listed here that a handler
# already records, and for an action that is neither recorded nor listed. The list empties as the
# handlers are covered.
AUDIT_PENDING_ACTIONS: FrozenSet[str] = frozenset({
    "apiKeyGet", "apiKeyList", "cognitoUserList", "constraintGet", "constraintList", "roleList", "userRoleList",
})


def audit_spec_for_route(route_path: Optional[str], method: Optional[str]) -> Optional[AuditSpec]:
    """The spec registered for a route template and method, or None."""
    if not route_path or not method:
        return None
    return AUDIT_ROUTE_ACTIONS.get((route_path, method.upper()))


def audit_spec_for_action(action: Optional[str]) -> Optional[AuditSpec]:
    """The spec registered for an action name, or None."""
    if not action:
        return None
    return AUDIT_ACTIONS.get(action)


def _literal_segment_count(path: str) -> int:
    return sum(1 for segment in path.split("/") if segment and not segment.startswith("{"))


# Route templates ordered so the most specific template is tried first: a literal segment wins over
# a parameter (/auth/constraints/permissionObjects before /auth/constraints/{constraintId}).
_ROUTES_BY_SPECIFICITY = tuple(sorted(
    ALL_API_ROUTES,
    key=lambda route: (-_literal_segment_count(route.path), "+}" in route.path, -len(route.path)),
))

_KNOWN_TEMPLATES = frozenset(route.path for route in ALL_API_ROUTES)


def resolve_route_template(path: Optional[str], template_hint: Optional[str] = None) -> Optional[str]:
    """The API route template a request was served under, or None when it matches no route.

    ``template_hint`` is the template API Gateway reports (a REST event's ``resource``, an HTTP API
    event's ``routeKey`` path); it is used when it names a registered route. Otherwise the concrete
    path is matched against ``ALL_API_ROUTES``, most specific template first. The concrete path is
    never returned: it carries caller-chosen names and ids, and records group by template.
    """
    if template_hint and template_hint in _KNOWN_TEMPLATES:
        return template_hint
    if not path:
        return None
    for route in _ROUTES_BY_SPECIFICITY:
        if route.matches(path):
            return route.path
    return None
