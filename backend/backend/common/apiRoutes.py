# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Master definition of every VAMS API endpoint route.

This module is the single source of truth on the backend for the API surface:
each API Gateway route is defined once here as an :class:`ApiRoute` constant
and collected into category groups and the :data:`ALL_API_ROUTES` master list.

Consumers:
  * **Lambda handlers** dispatch requests by matching the incoming
    ``event['requestContext']['http']['path']`` against these constants via
    :meth:`ApiRoute.matches` instead of hard-coding path fragments
    (``path.endswith('/listFiles')`` etc.). Template matching is exact, so a
    route change here is picked up by every dispatcher automatically.
  * **handlers/auth/routes.py** serves this list through
    ``GET /auth/routes/api`` (full list) and ``GET /auth/routes/api/allowed``
    (Casbin-filtered per user).

The route templates MUST match the routes registered with API Gateway in the
CDK api builder stacks (``infra/lib/nestedStacks/apiLambda/``).

Adding or changing a route checklist:
  1. Update the ApiRoute constant here AND add it to the appropriate category
     group array so it is included in ALL_API_ROUTES (and therefore served by
     the GET /auth/routes/api listing).
  2. Register/Update the route attachment in the CDK api builder stacks.
  3. Update ``documentation/VAMS_API.yaml`` and the CLI constants if exposed.
"""

import re
from functools import lru_cache
from typing import NamedTuple, Tuple

# HTTP methods VAMS routes use.
GET = "GET"
POST = "POST"
PUT = "PUT"
DELETE = "DELETE"
HEAD = "HEAD"


@lru_cache(maxsize=None)
def _route_regex(path_template: str) -> "re.Pattern":
    """Compile an API Gateway route template into an exact-match regex.

    ``{param}`` matches exactly one path segment; ``{param+}`` (greedy proxy)
    matches the remainder of the path. All other characters are literal.
    """
    parts = []
    for segment in path_template.split("/"):
        if segment.startswith("{") and segment.endswith("+}"):
            parts.append(".+")
        elif segment.startswith("{") and segment.endswith("}"):
            parts.append("[^/]+")
        else:
            parts.append(re.escape(segment))
    return re.compile("^" + "/".join(parts) + "$")


class ApiRoute(NamedTuple):
    """One API endpoint: route template, allowed methods, logical category.

    ``internal=True`` marks routes that are not registered with API Gateway
    (cross-Lambda invocation only) -- they participate in handler dispatch but
    are excluded from the public route listing.
    ``unauthenticated=True`` marks routes served without the custom Lambda
    authorizer (no Casbin enforcement applies).
    """

    path: str
    methods: Tuple[str, ...]
    category: str
    internal: bool = False
    unauthenticated: bool = False

    def matches(self, path: str) -> bool:
        """Return True if ``path`` (a concrete request path) matches this route."""
        return _route_regex(self.path).fullmatch(path) is not None


# ---------------------------------------------------------------------------
# Config / public (unauthenticated routes carry unauthenticated=True)
# ---------------------------------------------------------------------------
API_SECURE_CONFIG = ApiRoute("/secure-config", (GET,), "config")
API_AMPLIFY_CONFIG = ApiRoute("/api/amplify-config", (GET,), "config", unauthenticated=True)
API_VERSION = ApiRoute("/api/version", (GET,), "config", unauthenticated=True)

CONFIG_ROUTES: Tuple[ApiRoute, ...] = (API_SECURE_CONFIG, API_AMPLIFY_CONFIG, API_VERSION)

# ---------------------------------------------------------------------------
# Databases
# ---------------------------------------------------------------------------
API_DATABASE = ApiRoute("/database", (GET, POST), "databases")
API_DATABASE_BY_ID = ApiRoute("/database/{databaseId}", (GET, PUT, DELETE), "databases")
API_BUCKETS = ApiRoute("/buckets", (GET,), "databases")

DATABASE_ROUTES: Tuple[ApiRoute, ...] = (API_DATABASE, API_DATABASE_BY_ID, API_BUCKETS)

# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------
API_ASSETS = ApiRoute("/assets", (GET, POST), "assets")
API_DATABASE_ASSETS = ApiRoute("/database/{databaseId}/assets", (GET,), "assets")
API_DATABASE_ASSET = ApiRoute("/database/{databaseId}/assets/{assetId}", (GET, PUT), "assets")
API_ARCHIVE_ASSET = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/archiveAsset", (DELETE,), "assets"
)
API_UNARCHIVE_ASSET = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/unarchiveAsset", (PUT,), "assets"
)
API_DELETE_ASSET = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/deleteAsset", (DELETE,), "assets"
)
API_INGEST_ASSET = ApiRoute("/ingest-asset", (POST,), "assets")
API_DOWNLOAD_ASSET = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/download", (POST,), "assets"
)
API_DOWNLOAD_ASSET_STREAM = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}", (GET, HEAD), "assets"
)
API_AUXILIARY_PREVIEW_ASSETS_STREAM = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}",
    (GET, HEAD),
    "assets",
)
API_ASSET_EXPORT = ApiRoute("/database/{databaseId}/assets/{assetId}/export", (POST,), "assets")
API_GET_ASSET_HISTORY = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/assetHistory", (GET,), "assetHistory"
)

ASSET_ROUTES: Tuple[ApiRoute, ...] = (
    API_ASSETS,
    API_DATABASE_ASSETS,
    API_DATABASE_ASSET,
    API_ARCHIVE_ASSET,
    API_UNARCHIVE_ASSET,
    API_DELETE_ASSET,
    API_INGEST_ASSET,
    API_DOWNLOAD_ASSET,
    API_DOWNLOAD_ASSET_STREAM,
    API_AUXILIARY_PREVIEW_ASSETS_STREAM,
    API_ASSET_EXPORT,
    API_GET_ASSET_HISTORY,
)

# ---------------------------------------------------------------------------
# Asset files
# ---------------------------------------------------------------------------
API_LIST_FILES = ApiRoute("/database/{databaseId}/assets/{assetId}/listFiles", (GET,), "assetFiles")
API_FILE_INFO = ApiRoute("/database/{databaseId}/assets/{assetId}/fileInfo", (GET,), "assetFiles")
API_MOVE_FILE = ApiRoute("/database/{databaseId}/assets/{assetId}/moveFile", (POST,), "assetFiles")
API_COPY_FILE = ApiRoute("/database/{databaseId}/assets/{assetId}/copyFile", (POST,), "assetFiles")
API_ARCHIVE_FILE = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/archiveFile", (DELETE,), "assetFiles"
)
API_UNARCHIVE_FILE = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/unarchiveFile", (POST,), "assetFiles"
)
API_DELETE_FILE = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/deleteFile", (DELETE,), "assetFiles"
)
API_DELETE_ASSET_PREVIEW = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/deleteAssetPreview", (DELETE,), "assetFiles"
)
API_DELETE_AUXILIARY_PREVIEW = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/deleteAuxiliaryPreviewAssetFiles",
    (DELETE,),
    "assetFiles",
)
API_REVERT_FILE_VERSION = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/revertFileVersion/{versionId}", (POST,), "assetFiles"
)
API_SET_PRIMARY_FILE = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/setPrimaryFile", (PUT,), "assetFiles"
)
API_CREATE_FOLDER = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/createFolder", (POST,), "assetFiles"
)

ASSET_FILE_ROUTES: Tuple[ApiRoute, ...] = (
    API_LIST_FILES,
    API_FILE_INFO,
    API_MOVE_FILE,
    API_COPY_FILE,
    API_ARCHIVE_FILE,
    API_UNARCHIVE_FILE,
    API_DELETE_FILE,
    API_DELETE_ASSET_PREVIEW,
    API_DELETE_AUXILIARY_PREVIEW,
    API_REVERT_FILE_VERSION,
    API_SET_PRIMARY_FILE,
    API_CREATE_FOLDER,
)

# ---------------------------------------------------------------------------
# Asset versions
# ---------------------------------------------------------------------------
API_CREATE_ASSET_VERSION = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/createVersion", (POST,), "assetVersions"
)
API_REVERT_ASSET_VERSION = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/revertAssetVersion/{assetVersionId}",
    (POST,),
    "assetVersions",
)
API_GET_ASSET_VERSIONS = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/getVersions", (GET,), "assetVersions"
)
API_GET_ASSET_VERSION = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/getVersion/{assetVersionId}", (GET,), "assetVersions"
)
API_ASSET_VERSION_BY_ID = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}",
    (PUT,),
    "assetVersions",
)
API_ASSET_VERSION_ARCHIVE = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/archive",
    (POST,),
    "assetVersions",
)
API_ASSET_VERSION_UNARCHIVE = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/unarchive",
    (POST,),
    "assetVersions",
)

ASSET_VERSION_ROUTES: Tuple[ApiRoute, ...] = (
    API_CREATE_ASSET_VERSION,
    API_REVERT_ASSET_VERSION,
    API_GET_ASSET_VERSIONS,
    API_GET_ASSET_VERSION,
    API_ASSET_VERSION_BY_ID,
    API_ASSET_VERSION_ARCHIVE,
    API_ASSET_VERSION_UNARCHIVE,
)

# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------
API_UPLOADS = ApiRoute("/uploads", (POST,), "uploads")
API_UPLOAD_COMPLETE = ApiRoute("/uploads/{uploadId}/complete", (POST,), "uploads")
# Cross-Lambda invocation only (processWorkflowExecutionOutput -> uploadFile);
# not registered with API Gateway.
API_UPLOAD_COMPLETE_EXTERNAL = ApiRoute(
    "/uploads/{uploadId}/complete/external", (POST,), "uploads", internal=True
)

UPLOAD_ROUTES: Tuple[ApiRoute, ...] = (
    API_UPLOADS,
    API_UPLOAD_COMPLETE,
    API_UPLOAD_COMPLETE_EXTERNAL,
)

# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------
API_COMMENTS_ASSET = ApiRoute("/comments/assets/{assetId}", (GET,), "comments")
API_COMMENTS_ASSET_VERSION = ApiRoute(
    "/comments/assets/{assetId}/assetVersionId/{assetVersionId}", (GET,), "comments"
)
API_COMMENTS_ASSET_VERSION_COMMENT = ApiRoute(
    "/comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}",
    (GET, POST, PUT, DELETE),
    "comments",
)

COMMENT_ROUTES: Tuple[ApiRoute, ...] = (
    API_COMMENTS_ASSET,
    API_COMMENTS_ASSET_VERSION,
    API_COMMENTS_ASSET_VERSION_COMMENT,
)

# ---------------------------------------------------------------------------
# Metadata (unified service)
# ---------------------------------------------------------------------------
API_ASSET_LINK_METADATA = ApiRoute(
    "/asset-links/{assetLinkId}/metadata", (GET, POST, PUT, DELETE), "metadata"
)
API_ASSET_METADATA = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/metadata", (GET, POST, PUT, DELETE), "metadata"
)
API_FILE_METADATA = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/metadata/file", (GET, POST, PUT, DELETE), "metadata"
)
API_DATABASE_METADATA = ApiRoute(
    "/database/{databaseId}/metadata", (GET, POST, PUT, DELETE), "metadata"
)

METADATA_ROUTES: Tuple[ApiRoute, ...] = (
    API_ASSET_LINK_METADATA,
    API_ASSET_METADATA,
    API_FILE_METADATA,
    API_DATABASE_METADATA,
)

# ---------------------------------------------------------------------------
# Metadata schema
# ---------------------------------------------------------------------------
API_METADATA_SCHEMA = ApiRoute("/metadataschema", (GET, POST, PUT), "metadataSchema")
API_METADATA_SCHEMA_BY_ID = ApiRoute(
    "/database/{databaseId}/metadataSchema/{metadataSchemaId}", (GET, DELETE), "metadataSchema"
)

METADATA_SCHEMA_ROUTES: Tuple[ApiRoute, ...] = (API_METADATA_SCHEMA, API_METADATA_SCHEMA_BY_ID)

# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------
API_PIPELINES = ApiRoute("/pipelines", (GET, PUT), "pipelines")
API_DATABASE_PIPELINES = ApiRoute("/database/{databaseId}/pipelines", (GET,), "pipelines")
API_DATABASE_PIPELINE = ApiRoute(
    "/database/{databaseId}/pipelines/{pipelineId}", (GET, DELETE), "pipelines"
)

PIPELINE_ROUTES: Tuple[ApiRoute, ...] = (
    API_PIPELINES,
    API_DATABASE_PIPELINES,
    API_DATABASE_PIPELINE,
)

# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------
API_WORKFLOWS = ApiRoute("/workflows", (GET, PUT), "workflows")
API_DATABASE_WORKFLOWS = ApiRoute("/database/{databaseId}/workflows", (GET,), "workflows")
API_DATABASE_WORKFLOW = ApiRoute(
    "/database/{databaseId}/workflows/{workflowId}", (GET, DELETE), "workflows"
)
API_WORKFLOW_EXECUTIONS = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/workflows/executions", (GET,), "workflows"
)
API_WORKFLOW_EXECUTIONS_BY_WORKFLOW = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/workflows/executions/{workflowId}",
    (GET,),
    "workflows",
)
API_EXECUTE_WORKFLOW = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/workflows/{workflowId}", (POST,), "workflows"
)

WORKFLOW_ROUTES: Tuple[ApiRoute, ...] = (
    API_WORKFLOWS,
    API_DATABASE_WORKFLOWS,
    API_DATABASE_WORKFLOW,
    API_WORKFLOW_EXECUTIONS,
    API_WORKFLOW_EXECUTIONS_BY_WORKFLOW,
    API_EXECUTE_WORKFLOW,
)

# ---------------------------------------------------------------------------
# Asset links
# ---------------------------------------------------------------------------
API_ASSET_LINKS = ApiRoute("/asset-links", (POST,), "assetLinks")
API_ASSET_LINKS_SINGLE = ApiRoute("/asset-links/single/{assetLinkId}", (GET,), "assetLinks")
API_ASSET_LINKS_UPDATE = ApiRoute("/asset-links/{assetLinkId}", (PUT,), "assetLinks")
API_ASSET_LINKS_DELETE = ApiRoute("/asset-links/{assetLinkId}", (DELETE,), "assetLinks")
API_ASSET_LINKS_FOR_ASSET = ApiRoute(
    "/database/{databaseId}/assets/{assetId}/asset-links", (GET,), "assetLinks"
)

ASSET_LINK_ROUTES: Tuple[ApiRoute, ...] = (
    API_ASSET_LINKS,
    API_ASSET_LINKS_SINGLE,
    API_ASSET_LINKS_UPDATE,
    API_ASSET_LINKS_DELETE,
    API_ASSET_LINKS_FOR_ASSET,
)

# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------
API_SUBSCRIPTIONS = ApiRoute("/subscriptions", (GET, POST, PUT, DELETE), "subscriptions")
API_CHECK_SUBSCRIPTION = ApiRoute("/check-subscription", (POST,), "subscriptions")
API_UNSUBSCRIBE = ApiRoute("/unsubscribe", (DELETE,), "subscriptions")

SUBSCRIPTION_ROUTES: Tuple[ApiRoute, ...] = (
    API_SUBSCRIPTIONS,
    API_CHECK_SUBSCRIPTION,
    API_UNSUBSCRIBE,
)

# ---------------------------------------------------------------------------
# Tags / tag types
# ---------------------------------------------------------------------------
API_TAGS = ApiRoute("/tags", (GET, POST, PUT), "tags")
API_TAG_BY_ID = ApiRoute("/tags/{tagId}", (DELETE,), "tags")
API_TAG_TYPES = ApiRoute("/tag-types", (GET, POST, PUT), "tagTypes")
API_TAG_TYPE_BY_ID = ApiRoute("/tag-types/{tagTypeId}", (DELETE,), "tagTypes")

TAG_ROUTES: Tuple[ApiRoute, ...] = (API_TAGS, API_TAG_BY_ID)
TAG_TYPE_ROUTES: Tuple[ApiRoute, ...] = (API_TAG_TYPES, API_TAG_TYPE_BY_ID)

# ---------------------------------------------------------------------------
# Roles / user roles
# ---------------------------------------------------------------------------
API_ROLES = ApiRoute("/roles", (GET, POST, PUT), "roles")
API_ROLE_BY_ID = ApiRoute("/roles/{roleId}", (DELETE,), "roles")
API_USER_ROLES = ApiRoute("/user-roles", (GET, POST, PUT, DELETE), "userRoles")

ROLE_ROUTES: Tuple[ApiRoute, ...] = (API_ROLES, API_ROLE_BY_ID)
USER_ROLE_ROUTES: Tuple[ApiRoute, ...] = (API_USER_ROLES,)

# ---------------------------------------------------------------------------
# Auth (constraints, routes, login profile, API keys, Cognito users)
# ---------------------------------------------------------------------------
API_AUTH_CONSTRAINTS = ApiRoute("/auth/constraints", (GET,), "auth")
API_AUTH_CONSTRAINT_BY_ID = ApiRoute(
    "/auth/constraints/{constraintId}", (GET, POST, PUT, DELETE), "auth"
)
API_AUTH_CONSTRAINTS_TEMPLATE_IMPORT = ApiRoute("/auth/constraintsTemplateImport", (POST,), "auth")
API_AUTH_CONSTRAINT_PERMISSION_OBJECTS = ApiRoute(
    "/auth/constraints/permissionObjects", (GET,), "auth"
)
API_AUTH_ROUTES = ApiRoute("/auth/routes", (POST,), "auth")
API_AUTH_ROUTES_API = ApiRoute("/auth/routes/api", (GET,), "auth")
API_AUTH_ROUTES_API_ALLOWED = ApiRoute("/auth/routes/api/allowed", (GET,), "auth")
API_AUTH_LOGIN_PROFILE = ApiRoute("/auth/loginProfile/{userId}", (GET, POST), "auth")
API_AUTH_API_KEYS = ApiRoute("/auth/api-keys", (GET, POST), "apiKeys")
API_AUTH_API_KEY_BY_ID = ApiRoute("/auth/api-keys/{apiKeyId}", (GET, PUT, DELETE), "apiKeys")
API_AUTH_USER_API_KEYS = ApiRoute("/auth/user/api-keys", (GET, POST), "apiKeys")
API_AUTH_USER_API_KEY_BY_ID = ApiRoute(
    "/auth/user/api-keys/{apiKeyId}", (GET, PUT, DELETE), "apiKeys"
)
API_COGNITO_USERS = ApiRoute("/user/cognito", (GET, POST), "users")
API_COGNITO_USER_BY_ID = ApiRoute("/user/cognito/{userId}", (PUT, DELETE), "users")
API_COGNITO_USER_RESET_PASSWORD = ApiRoute(
    "/user/cognito/{userId}/resetPassword", (POST,), "users"
)

AUTH_ROUTES: Tuple[ApiRoute, ...] = (
    API_AUTH_CONSTRAINTS,
    API_AUTH_CONSTRAINT_BY_ID,
    API_AUTH_CONSTRAINTS_TEMPLATE_IMPORT,
    API_AUTH_CONSTRAINT_PERMISSION_OBJECTS,
    API_AUTH_ROUTES,
    API_AUTH_ROUTES_API,
    API_AUTH_ROUTES_API_ALLOWED,
    API_AUTH_LOGIN_PROFILE,
)
API_KEY_ROUTES: Tuple[ApiRoute, ...] = (
    API_AUTH_API_KEYS,
    API_AUTH_API_KEY_BY_ID,
    API_AUTH_USER_API_KEYS,
    API_AUTH_USER_API_KEY_BY_ID,
)
USER_ROUTES: Tuple[ApiRoute, ...] = (
    API_COGNITO_USERS,
    API_COGNITO_USER_BY_ID,
    API_COGNITO_USER_RESET_PASSWORD,
)

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
API_SEARCH = ApiRoute("/search", (GET, POST), "search")
API_SEARCH_SIMPLE = ApiRoute("/search/simple", (POST,), "search")

SEARCH_ROUTES: Tuple[ApiRoute, ...] = (API_SEARCH, API_SEARCH_SIMPLE)

# ---------------------------------------------------------------------------
# Add-ons
# ---------------------------------------------------------------------------
API_ADDON_PHYSNA_VIEWER = ApiRoute("/addon/physna/viewer", (GET,), "addons")

ADDON_ROUTES: Tuple[ApiRoute, ...] = (API_ADDON_PHYSNA_VIEWER,)

# ---------------------------------------------------------------------------
# Master list and lookups
# ---------------------------------------------------------------------------
ALL_API_ROUTES: Tuple[ApiRoute, ...] = (
    CONFIG_ROUTES
    + DATABASE_ROUTES
    + ASSET_ROUTES
    + ASSET_FILE_ROUTES
    + ASSET_VERSION_ROUTES
    + UPLOAD_ROUTES
    + COMMENT_ROUTES
    + METADATA_ROUTES
    + METADATA_SCHEMA_ROUTES
    + PIPELINE_ROUTES
    + WORKFLOW_ROUTES
    + ASSET_LINK_ROUTES
    + SUBSCRIPTION_ROUTES
    + TAG_ROUTES
    + TAG_TYPE_ROUTES
    + ROLE_ROUTES
    + USER_ROLE_ROUTES
    + AUTH_ROUTES
    + API_KEY_ROUTES
    + USER_ROUTES
    + SEARCH_ROUTES
    + ADDON_ROUTES
)


def get_public_api_routes() -> Tuple[ApiRoute, ...]:
    """Return all externally reachable API routes (excludes internal cross-call routes)."""
    return tuple(route for route in ALL_API_ROUTES if not route.internal)
