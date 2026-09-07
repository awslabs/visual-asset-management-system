"""
Constants for VamsCLI
"""

# CLI Configuration
CLI_NAME = "vamscli"

# API Endpoints
API_VERSION = "/api/version"
API_AMPLIFY_CONFIG = "/api/amplify-config"
API_AUTH_LOGIN_PROFILE = "/auth/loginProfile/{userId}"
API_ASSETS = "/assets"
API_DATABASE_ASSETS = "/database/{databaseId}/assets"
API_DATABASE_ASSET = "/database/{databaseId}/assets/{assetId}"
API_UPLOADS = "/uploads"
API_UPLOADS_COMPLETE = "/uploads/{uploadId}/complete"

# File Management API Endpoints
API_CREATE_FOLDER = "/database/{databaseId}/assets/{assetId}/createFolder"
API_LIST_FILES = "/database/{databaseId}/assets/{assetId}/listFiles"
API_FILE_INFO = "/database/{databaseId}/assets/{assetId}/fileInfo"
API_MOVE_FILE = "/database/{databaseId}/assets/{assetId}/moveFile"
API_COPY_FILE = "/database/{databaseId}/assets/{assetId}/copyFile"
API_ARCHIVE_FILE = "/database/{databaseId}/assets/{assetId}/archiveFile"
API_UNARCHIVE_FILE = "/database/{databaseId}/assets/{assetId}/unarchiveFile"
API_DELETE_ASSET_PREVIEW = "/database/{databaseId}/assets/{assetId}/deleteAssetPreview"
API_DELETE_AUXILIARY_PREVIEW = "/database/{databaseId}/assets/{assetId}/deleteAuxiliaryPreviewAssetFiles"
API_DELETE_FILE = "/database/{databaseId}/assets/{assetId}/deleteFile"
API_REVERT_FILE_VERSION = "/database/{databaseId}/assets/{assetId}/revertFileVersion/{versionId}"
API_SET_PRIMARY_FILE = "/database/{databaseId}/assets/{assetId}/setPrimaryFile"

# Asset Management API Endpoints
API_ARCHIVE_ASSET = "/database/{databaseId}/assets/{assetId}/archiveAsset"
API_UNARCHIVE_ASSET = "/database/{databaseId}/assets/{assetId}/unarchiveAsset"
API_DELETE_ASSET = "/database/{databaseId}/assets/{assetId}/deleteAsset"
API_DOWNLOAD_ASSET = "/database/{databaseId}/assets/{assetId}/download"
API_ASSET_EXPORT = "/database/{databaseId}/assets/{assetId}/export"
API_GET_ASSET_HISTORY = "/database/{databaseId}/assets/{assetId}/assetHistory"

# Asset Version API Endpoints
API_CREATE_ASSET_VERSION = "/database/{databaseId}/assets/{assetId}/createVersion"
API_REVERT_ASSET_VERSION = "/database/{databaseId}/assets/{assetId}/revertAssetVersion/{assetVersionId}"
API_GET_ASSET_VERSIONS = "/database/{databaseId}/assets/{assetId}/getVersions"
API_GET_ASSET_VERSION = "/database/{databaseId}/assets/{assetId}/getVersion/{assetVersionId}"
API_ASSET_VERSION_BY_ID = "/database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}"
API_ASSET_VERSION_ARCHIVE = "/database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/archive"
API_ASSET_VERSION_UNARCHIVE = "/database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/unarchive"

# Database Management API Endpoints
API_DATABASE = "/database"
API_DATABASE_BY_ID = "/database/{databaseId}"
API_BUCKETS = "/buckets"

# Tag Management API Endpoints
API_TAGS = "/tags"
API_TAG_DELETE = "/tags/{tagId}"
API_TAG_TYPES = "/tag-types"
API_TAG_TYPE_DELETE = "/tag-types/{tagTypeId}"

# Asset Links API Endpoints
API_ASSET_LINKS = "/asset-links"
API_ASSET_LINKS_SINGLE = "/asset-links/single/{assetLinkId}"
API_ASSET_LINKS_UPDATE = "/asset-links/{assetLinkId}"
API_ASSET_LINKS_DELETE = "/asset-links/{assetLinkId}"
API_ASSET_LINKS_FOR_ASSET = "/database/{databaseId}/assets/{assetId}/asset-links"

# Asset Links Metadata API Endpoints (New unified API)
# One collection route carrying all four verbs; an individual key is addressed in the request body
# (`metadata` entries to upsert, `metadataKeys` to delete), not in the path.
API_ASSET_LINK_METADATA = "/asset-links/{assetLinkId}/metadata"

# Metadata API Endpoints (New unified API)
API_ASSET_METADATA = "/database/{databaseId}/assets/{assetId}/metadata"
API_FILE_METADATA = "/database/{databaseId}/assets/{assetId}/metadata/file"
API_DATABASE_METADATA = "/database/{databaseId}/metadata"

# Comments API Endpoints
# The composite sort key `assetVersionId:commentId` is two values joined by a colon inside a single
# path segment, so the third constant is not `str.format`-able: format() reads everything after the
# colon as a format spec and raises. `build_comment_path()` in utils/api_client.py substitutes it.
API_COMMENTS_ASSET = "/comments/assets/{assetId}"
API_COMMENTS_ASSET_VERSION = "/comments/assets/{assetId}/assetVersionId/{assetVersionId}"
API_COMMENTS_ASSET_VERSION_COMMENT = (
    "/comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}"
)

# Subscription API Endpoints
# DELETE /subscriptions removes the whole subscription record (and its SNS topic); DELETE
# /unsubscribe removes one subscriber from it. Two operations on the same record, not aliases.
API_SUBSCRIPTIONS = "/subscriptions"
API_CHECK_SUBSCRIPTION = "/check-subscription"
API_UNSUBSCRIBE = "/unsubscribe"

# The only eventName and entityName the subscription endpoints accept; both are validated against
# a fixed list server-side, and /check-subscription hard-codes this pair.
SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE = "Asset Version Change"
SUBSCRIPTION_ENTITY_ASSET = "Asset"

# Cognito User Management API Endpoints
API_COGNITO_USERS = "/user/cognito"
API_COGNITO_USER_BY_ID = "/user/cognito/{userId}"
API_COGNITO_USER_RESET_PASSWORD = "/user/cognito/{userId}/resetPassword"

# Role Management API Endpoints
API_ROLES = "/roles"
API_ROLE_BY_ID = "/roles/{roleId}"

# Constraint Management API Endpoints
API_CONSTRAINTS = "/auth/constraints"
API_CONSTRAINT_BY_ID = "/auth/constraints/{constraintId}"
API_CONSTRAINTS_TEMPLATE_IMPORT = "/auth/constraintsTemplateImport"
API_AUTH_CONSTRAINT_PERMISSION_OBJECTS = "/auth/constraints/permissionObjects"

# Auth Routes API Endpoints
API_AUTH_ROUTES_API = "/auth/routes/api"
API_AUTH_ROUTES_API_ALLOWED = "/auth/routes/api/allowed"

# API Key Management API Endpoints
API_AUTH_API_KEYS = "/auth/api-keys"
API_AUTH_API_KEY = "/auth/api-keys/{apiKeyId}"
# User-level (self-service) API key endpoints: scoped to the requesting
# user's own keys; expiration required
API_AUTH_USER_API_KEYS = "/auth/user/api-keys"
API_AUTH_USER_API_KEY = "/auth/user/api-keys/{apiKeyId}"

# User Role Management API Endpoints
API_USER_ROLES = "/user-roles"

# Legacy Metadata API Endpoints (deprecated)
API_METADATA = "/database/{databaseId}/assets/{assetId}/metadata"

# Metadata Schema API Endpoints
# A database's schemas are read from the collection route with a databaseId filter; the API defines
# no path-scoped /metadataschema/{databaseId} variant. The collection route also carries POST
# (create) and PUT (update, keyed on a metadataSchemaId in the body), so those share this constant.
API_METADATA_SCHEMA_LIST = "/metadataschema"  # GET with filters, POST create, PUT update
API_METADATA_SCHEMA_BY_ID = "/database/{databaseId}/metadataSchema/{metadataSchemaId}"  # GET, DELETE

# Search API Endpoints
API_SEARCH = "/search"
API_SEARCH_SIMPLE = "/search/simple"
API_SEARCH_MAPPING = "/search"

# Pipeline API Endpoints
API_PIPELINES = "/pipelines"
API_DATABASE_PIPELINES = "/database/{databaseId}/pipelines"
API_DATABASE_PIPELINE = "/database/{databaseId}/pipelines/{pipelineId}"
API_PIPELINE_TEMPLATES = "/database/{databaseId}/pipelines/{pipelineId}/templates"
API_PIPELINE_TEMPLATE = "/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}"
API_PIPELINE_TEMPLATE_TAG_SCHEMA = (
    "/database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}/tagSchema"
)

# Workflow API Endpoints
API_WORKFLOWS = "/workflows"
API_DATABASE_WORKFLOWS = "/database/{databaseId}/workflows"
API_DATABASE_WORKFLOW = "/database/{databaseId}/workflows/{workflowId}"
API_WORKFLOW_TRIGGERS = "/database/{databaseId}/workflows/{workflowId}/triggers"
API_WORKFLOW_TRIGGER = "/database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}"

# Workflow execution API Endpoints
# Asset-scoped execution list (kept for per-asset history views).
API_WORKFLOW_EXECUTIONS = "/database/{databaseId}/assets/{assetId}/workflows/executions"
# Asset-less multi-file execute (path uses workflowDatabaseId, not databaseId).
API_EXECUTE_WORKFLOW = "/workflows/{workflowDatabaseId}/{workflowId}/execute"
# Execution operations (keyed on executionId; executions may span multiple assets).
API_WORKFLOW_EXECUTIONS_GLOBAL = "/workflows/executions"
API_WORKFLOW_EXECUTION = "/workflows/executions/{executionId}"
API_WORKFLOW_EXECUTION_DETAILS = "/workflows/executions/{executionId}/details"
API_WORKFLOW_EXECUTION_DETAILS_METADATA = "/workflows/executions/{executionId}/details/metadata"
API_WORKFLOW_EXECUTION_LOGS = "/workflows/executions/{executionId}/logs"
API_WORKFLOW_EXECUTION_RERUN = "/workflows/executions/{executionId}/rerun"
API_WORKFLOW_EXECUTION_PERMANENT = "/workflows/executions/{executionId}/permanent"

# Workflow execution list page-size cap (Step Functions throttling on the asset-scoped list).
MAX_WORKFLOW_EXECUTION_PAGE_SIZE = 50
# Global execution list page-size cap (the handler clamps larger values to this).
MAX_GLOBAL_EXECUTION_PAGE_SIZE = 100
# Page ceiling for --auto-paginate on the execution list (rows are filtered after the page limit).
MAX_EXECUTION_AUTO_PAGINATE_PAGES = 200
# Paged execution-detail metadata: the collections the endpoint serves, and its page-size cap
# (the handler clamps larger values rather than rejecting them).
EXECUTION_DETAIL_METADATA_COLLECTIONS = ('input', 'inputDatabase', 'output')
MAX_EXECUTION_DETAIL_METADATA_PAGE_SIZE = 500

# Upload Configuration
DEFAULT_CHUNK_SIZE_SMALL = 150 * 1024 * 1024  # 150MB
DEFAULT_CHUNK_SIZE_LARGE = 1024 * 1024 * 1024  # 1GB
MAX_FILE_SIZE_SMALL_CHUNKS = 15 * 1024 * 1024 * 1024  # 15GB
MAX_SEQUENCE_SIZE = 3 * 1024 * 1024 * 1024  # 3GB
MAX_PREVIEW_FILE_SIZE = 5 * 1024 * 1024  # 5MB
DEFAULT_PARALLEL_UPLOADS = 10
DEFAULT_RETRY_ATTEMPTS = 3

# New Backend Upload Limits (v2.2+)
MAX_FILES_PER_REQUEST = 50  # Maximum files per upload request
MAX_TOTAL_PARTS_PER_REQUEST = 200  # Maximum total parts across all files
MAX_PARTS_PER_FILE = 200  # Maximum parts per individual file
MAX_PART_SIZE = 5 * 1024 * 1024 * 1024  # 5GB maximum part size (S3 limit)
MAX_UPLOADS_PER_USER_PER_MINUTE = 20  # Rate limit for upload initialization

# Download Configuration
DEFAULT_PARALLEL_DOWNLOADS = 5
DEFAULT_DOWNLOAD_RETRY_ATTEMPTS = 3
DEFAULT_DOWNLOAD_TIMEOUT = 300  # 5 minutes per file
MAX_DOWNLOAD_KEYS_PER_REQUEST = 1500  # Backend cap per bulk presigned-URL request

# Asset Export Configuration. Both pairs mirror the backend request model
# (backend/backend/models/assetExport.py); the file budget bounds one page, not the export.
DEFAULT_EXPORT_MAX_ASSETS = 100
MAX_EXPORT_MAX_ASSETS = 1000
DEFAULT_EXPORT_MAX_FILES = 2000
MAX_EXPORT_MAX_FILES = 10000

# Sync Configuration
DEFAULT_IGNORE_FILE_NAME = ".vamsignore"
SYNC_MTIME_TOLERANCE_SECONDS = 2  # Filesystem timestamp granularity tolerance (FAT32 = 2s)

# File Extensions
ALLOWED_PREVIEW_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.svg', '.gif']

# Profile Configuration
PROFILE_DIR_NAME = "vamscli"
PROFILES_SUBDIR = "profiles"
ACTIVE_PROFILE_FILE = "active_profile.json"
CONFIG_FILE_NAME = "config.json"
AUTH_FILE_NAME = "auth_profile.json"
CREDENTIALS_FILE_NAME = "credentials.json"
DEFAULT_PROFILE_NAME = "default"

# Logging Configuration
LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "vamscli.log"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(profile)s] [%(command)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Profile validation
PROFILE_NAME_MIN_LENGTH = 3
PROFILE_NAME_MAX_LENGTH = 50
RESERVED_PROFILE_NAMES = ["help", "version", "list"]

# Authentication and API Configuration
# Seconds to wait for the TCP connection to the API Gateway endpoint.
DEFAULT_TIMEOUT = 30
# Seconds to wait between bytes of a response. Sized above API_GATEWAY_MAX_TIMEOUT_SECONDS (300, the
# ceiling getConfig() allows for app.api.apiGatewayRest.apiGatewayTimeoutTime), so a deployment that
# raised its integration timeout is never cut off by the client while a stalled or black-holed socket
# still ends rather than hanging the command forever.
DEFAULT_READ_TIMEOUT = 310
MAX_AUTH_RETRIES = 3
MINIMUM_API_VERSION = "2.2"
API_LOGIN_PROFILE = "/auth/loginProfile"
API_SECURE_CONFIG = "/secure-config"

# Retry Configuration for 429 Throttling
DEFAULT_MAX_RETRY_ATTEMPTS = 5
DEFAULT_INITIAL_RETRY_DELAY = 1.0
DEFAULT_MAX_RETRY_DELAY = 60.0
DEFAULT_RETRY_BACKOFF_MULTIPLIER = 2.0
DEFAULT_RETRY_JITTER = 0.1

# Feature Switch Constants
# One per member of VAMS_APP_FEATURES (infra/common/vamsAppFeatures.ts), which is what a deployment
# publishes through /secure-config. A missing member is a gate the CLI cannot name.
FEATURE_GOVCLOUD = "GOVCLOUD"
FEATURE_ALLOWUNSAFEEVAL = "ALLOWUNSAFEEVAL"
FEATURE_LOCATIONSERVICES = "LOCATIONSERVICES"
FEATURE_ALBDEPLOY = "ALBDEPLOY"
FEATURE_CLOUDFRONTDEPLOY = "CLOUDFRONTDEPLOY"
FEATURE_NOOPENSEARCH = "NOOPENSEARCH"
FEATURE_AUTHPROVIDER_COGNITO = "AUTHPROVIDER_COGNITO"
FEATURE_AUTHPROVIDER_COGNITO_SAML = "AUTHPROVIDER_COGNITO_SAML"
FEATURE_AUTHPROVIDER_COGNITO_OIDC = "AUTHPROVIDER_COGNITO_OIDC"
FEATURE_AUTHPROVIDER_EXTERNALOAUTHIDP = "AUTHPROVIDER_EXTERNALOAUTHIDP"
FEATURE_PHYSNA_ADDON = "PHYSNA_ADDON"
FEATURE_DEADLINECLOUD_PIPELINES = "DEADLINECLOUD_PIPELINES"

# Legacy constants for backward compatibility
CONFIG_FILE = CONFIG_FILE_NAME
AUTH_PROFILE_FILE = AUTH_FILE_NAME
CREDENTIALS_FILE = CREDENTIALS_FILE_NAME


def get_config_dir():
    """Get the configuration directory path."""
    import os
    import platform
    from pathlib import Path
    
    system = platform.system()
    
    if system == "Windows":
        config_dir = Path(os.environ.get("APPDATA", "")) / PROFILE_DIR_NAME
    elif system == "Darwin":  # macOS
        config_dir = Path.home() / "Library" / "Application Support" / PROFILE_DIR_NAME
    else:  # Linux and other Unix-like systems
        config_dir = Path.home() / ".config" / PROFILE_DIR_NAME
    
    return config_dir


def get_profile_dir(profile_name: str = DEFAULT_PROFILE_NAME):
    """Get the profile-specific directory path."""
    return get_config_dir() / PROFILES_SUBDIR / profile_name


def validate_profile_name(profile_name: str) -> bool:
    """Validate profile name format."""
    import re
    
    if not profile_name:
        return False
    
    if len(profile_name) < PROFILE_NAME_MIN_LENGTH or len(profile_name) > PROFILE_NAME_MAX_LENGTH:
        return False
    
    if profile_name.lower() in RESERVED_PROFILE_NAMES:
        return False
    
    # Allow alphanumeric, hyphens, and underscores
    if not re.match(r'^[a-zA-Z0-9_-]+$', profile_name):
        return False
    
    return True