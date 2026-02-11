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
API_DELETE_ASSET = "/database/{databaseId}/assets/{assetId}/deleteAsset"
API_DOWNLOAD_ASSET = "/database/{databaseId}/assets/{assetId}/download"
API_ASSET_EXPORT = "/database/{databaseId}/assets/{assetId}/export"

# Asset Version API Endpoints
API_CREATE_ASSET_VERSION = "/database/{databaseId}/assets/{assetId}/createVersion"
API_REVERT_ASSET_VERSION = "/database/{databaseId}/assets/{assetId}/revertAssetVersion/{assetVersionId}"
API_GET_ASSET_VERSIONS = "/database/{databaseId}/assets/{assetId}/getVersions"
API_GET_ASSET_VERSION = "/database/{databaseId}/assets/{assetId}/getVersion/{assetVersionId}"

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
API_ASSET_LINKS_DELETE = "/asset-links/{relationId}"
API_ASSET_LINKS_FOR_ASSET = "/database/{databaseId}/assets/{assetId}/asset-links"

# Asset Links Metadata API Endpoints (New unified API)
API_ASSET_LINK_METADATA = "/asset-links/{assetLinkId}/metadata"

# Metadata API Endpoints (New unified API)
API_ASSET_METADATA = "/database/{databaseId}/assets/{assetId}/metadata"
API_FILE_METADATA = "/database/{databaseId}/assets/{assetId}/metadata/file"
API_DATABASE_METADATA = "/database/{databaseId}/metadata"

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

# User Role Management API Endpoints
API_USER_ROLES = "/user-roles"

# Legacy Metadata API Endpoints (deprecated)
API_ASSET_LINKS_METADATA = "/asset-links/{assetLinkId}/metadata"
API_ASSET_LINKS_METADATA_KEY = "/asset-links/{assetLinkId}/metadata/{metadataKey}"
API_METADATA = "/database/{databaseId}/assets/{assetId}/metadata"

# Metadata Schema API Endpoints
API_METADATA_SCHEMA = "/metadataschema/{databaseId}"  # Legacy endpoint
API_METADATA_SCHEMA_LIST = "/metadataschema"  # GET with filters
API_METADATA_SCHEMA_BY_ID = "/database/{databaseId}/metadataSchema/{metadataSchemaId}"  # GET single schema

# Search API Endpoints
API_SEARCH = "/search"
API_SEARCH_SIMPLE = "/search/simple"
API_SEARCH_MAPPING = "/search"

# Workflow API Endpoints
API_WORKFLOWS = "/workflows"
API_DATABASE_WORKFLOWS = "/database/{databaseId}/workflows"
API_WORKFLOW_EXECUTIONS = "/database/{databaseId}/assets/{assetId}/workflows/executions"
API_EXECUTE_WORKFLOW = "/database/{databaseId}/assets/{assetId}/workflows/{workflowId}"

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
DEFAULT_TIMEOUT = 30
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
FEATURE_GOVCLOUD = "GOVCLOUD"
FEATURE_ALLOWUNSAFEEVAL = "ALLOWUNSAFEEVAL"
FEATURE_LOCATIONSERVICES = "LOCATIONSERVICES"
FEATURE_ALBDEPLOY = "ALBDEPLOY"
FEATURE_NOOPENSEARCH = "NOOPENSEARCH"
FEATURE_AUTHPROVIDER_COGNITO = "AUTHPROVIDER_COGNITO"
FEATURE_AUTHPROVIDER_COGNITO_SAML = "AUTHPROVIDER_COGNITO_SAML"
FEATURE_AUTHPROVIDER_EXTERNALOAUTHIDP = "AUTHPROVIDER_EXTERNALOAUTHIDP"

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