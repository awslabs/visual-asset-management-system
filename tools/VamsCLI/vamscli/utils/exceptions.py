"""Custom exceptions for VamsCLI."""


class VamsCLIError(Exception):
    """Base exception for VamsCLI errors."""
    pass


# =============================================================================
# GLOBAL INFRASTRUCTURE EXCEPTIONS
# These are handled by the global exception handler in main.py
# =============================================================================

class GlobalInfrastructureError(VamsCLIError):
    """Base exception for global infrastructure errors handled in main.py."""
    pass


class SetupRequiredError(GlobalInfrastructureError):
    """Raised when setup is required before running commands."""
    pass


class AuthenticationError(GlobalInfrastructureError):
    """Raised when authentication fails."""
    pass


class APIUnavailableError(GlobalInfrastructureError):
    """Raised when the VAMS API is unavailable or incompatible."""
    pass


class ProfileError(GlobalInfrastructureError):
    """Raised when profile operations fail."""
    pass


class InvalidProfileNameError(GlobalInfrastructureError):
    """Raised when profile name is invalid."""
    pass


class ConfigurationError(GlobalInfrastructureError):
    """Raised when there's a configuration issue."""
    pass


class OverrideTokenError(GlobalInfrastructureError):
    """Raised when there's an issue with override tokens."""
    pass


class TokenExpiredError(GlobalInfrastructureError):
    """Raised when authentication token has expired."""
    pass


class PermissionDeniedError(GlobalInfrastructureError):
    """Raised when user lacks permissions for the requested action (legitimate 403)."""
    pass


class VersionMismatchError(GlobalInfrastructureError):
    """Raised when CLI and API versions don't match."""
    pass


class RetryExhaustedError(GlobalInfrastructureError):
    """Raised when all retry attempts have been exhausted."""
    pass


class RateLimitExceededError(GlobalInfrastructureError):
    """Raised when API rate limit is exceeded (HTTP 429)."""
    pass


# =============================================================================
# COMMAND-SPECIFIC BUSINESS LOGIC EXCEPTIONS
# These are handled by individual commands
# =============================================================================

class BusinessLogicError(VamsCLIError):
    """Base exception for command-specific business logic errors."""
    pass


# General API and validation errors
class APIError(BusinessLogicError):
    """Raised when API calls fail."""
    pass


class ProfileNotFoundError(BusinessLogicError):
    """Raised when required profile is not found."""
    pass


# Asset-related business logic exceptions
class AssetError(BusinessLogicError):
    """Base class for asset-related errors."""
    pass


class AssetNotFoundError(AssetError):
    """Raised when an asset is not found."""
    pass


class AssetAlreadyExistsError(AssetError):
    """Raised when trying to create an asset that already exists"""
    pass


class AssetAlreadyArchivedError(AssetError):
    """Raised when trying to archive an asset that is already archived"""
    pass


class AssetDeletionError(AssetError):
    """Raised when asset deletion operations fail"""
    pass


class InvalidAssetDataError(AssetError):
    """Raised when asset data is invalid"""
    pass


# Database-related business logic exceptions
class DatabaseError(BusinessLogicError):
    """Base class for database-related errors."""
    pass


class DatabaseNotFoundError(DatabaseError):
    """Raised when a database is not found"""
    pass


class DatabaseAlreadyExistsError(DatabaseError):
    """Raised when trying to create a database that already exists"""
    pass


class DatabaseDeletionError(DatabaseError):
    """Raised when database deletion fails due to dependencies"""
    pass


class InvalidDatabaseDataError(DatabaseError):
    """Raised when database data is invalid"""
    pass


class BucketNotFoundError(DatabaseError):
    """Raised when a bucket is not found"""
    pass


# File-related business logic exceptions
class FileError(BusinessLogicError):
    """Base class for file-related errors."""
    pass


class FileUploadError(FileError):
    """Raised when file upload operations fail"""
    pass


class InvalidFileError(FileError):
    """Raised when a file is invalid for upload"""
    pass


class FileTooLargeError(FileError):
    """Raised when a file exceeds size limits"""
    pass


class PreviewFileError(FileError):
    """Raised when preview file validation fails"""
    pass


class UploadSequenceError(FileError):
    """Raised when upload sequence processing fails"""
    pass


class PartUploadError(FileError):
    """Raised when individual part upload fails"""
    pass


class FileNotFoundError(FileError):
    """Raised when a file is not found"""
    pass


class FileOperationError(FileError):
    """Raised when file operations fail"""
    pass


class InvalidPathError(FileError):
    """Raised when file paths are invalid"""
    pass


class FilePermissionError(FileError):
    """Raised when user lacks permissions for file operations"""
    pass


class FileAlreadyExistsError(FileError):
    """Raised when trying to create a file that already exists"""
    pass


class FileArchivedError(FileError):
    """Raised when trying to operate on archived files"""
    pass


class InvalidVersionError(FileError):
    """Raised when file version is invalid"""
    pass


class FileDownloadError(FileError):
    """Raised when file download operations fail."""
    pass


class DownloadError(FileError):
    """Raised when individual download operations fail."""
    pass


class AssetDownloadError(FileError):
    """Raised when asset download operations fail."""
    pass


class PreviewNotFoundError(FileError):
    """Raised when asset preview is not found."""
    pass


class AssetNotDistributableError(FileError):
    """Raised when trying to download from non-distributable asset."""
    pass


class DownloadTreeError(FileError):
    """Raised when asset tree traversal fails."""
    pass


# Tag-related business logic exceptions
class TagError(BusinessLogicError):
    """Base class for tag-related errors."""
    pass


class TagNotFoundError(TagError):
    """Raised when a tag is not found."""
    pass


class TagAlreadyExistsError(TagError):
    """Raised when trying to create a tag that already exists."""
    pass


class InvalidTagDataError(TagError):
    """Raised when tag data is invalid."""
    pass


class TagTypeNotFoundError(TagError):
    """Raised when a tag type is not found."""
    pass


class TagTypeAlreadyExistsError(TagError):
    """Raised when trying to create a tag type that already exists."""
    pass


class TagTypeInUseError(TagError):
    """Raised when trying to delete a tag type that is currently in use by tags."""
    pass


class InvalidTagTypeDataError(TagError):
    """Raised when tag type data is invalid."""
    pass


# Asset version-related business logic exceptions
class AssetVersionError(BusinessLogicError):
    """Base class for asset version errors."""
    pass


class AssetVersionNotFoundError(AssetVersionError):
    """Raised when an asset version is not found."""
    pass


class AssetVersionOperationError(AssetVersionError):
    """Raised when asset version operations fail."""
    pass


class InvalidAssetVersionDataError(AssetVersionError):
    """Raised when asset version data is invalid."""
    pass


class AssetVersionRevertError(AssetVersionError):
    """Raised when asset version revert operations fail."""
    pass


# Asset link-related business logic exceptions
class AssetLinkError(BusinessLogicError):
    """Base class for asset link errors."""
    pass


class AssetLinkNotFoundError(AssetLinkError):
    """Raised when an asset link is not found."""
    pass


class AssetLinkValidationError(AssetLinkError):
    """Raised when asset link validation fails."""
    pass


class AssetLinkPermissionError(AssetLinkError):
    """Raised when user lacks permissions for asset link operations."""
    pass


class CycleDetectionError(AssetLinkError):
    """Raised when creating an asset link would create a cycle."""
    pass


class AssetLinkAlreadyExistsError(AssetLinkError):
    """Raised when trying to create an asset link that already exists."""
    pass


class InvalidRelationshipTypeError(AssetLinkError):
    """Raised when an invalid relationship type is specified."""
    pass


class AssetLinkOperationError(AssetLinkError):
    """Raised when asset link operations fail."""
    pass


# Search-related business logic exceptions
class SearchError(BusinessLogicError):
    """Base class for search errors."""
    pass


class SearchDisabledError(SearchError):
    """Raised when search functionality is disabled (NOOPENSEARCH feature enabled)."""
    pass


class SearchUnavailableError(SearchError):
    """Raised when search service is unavailable."""
    pass


class InvalidSearchParametersError(SearchError):
    """Raised when search parameters are invalid."""
    pass


class SearchQueryError(SearchError):
    """Raised when search query execution fails."""
    pass


class SearchMappingError(SearchError):
    """Raised when search mapping retrieval fails."""
    pass


# Profile-related business logic exceptions (for command-specific profile operations)
class ProfileAlreadyExistsError(BusinessLogicError):
    """Raised when trying to create a profile that already exists"""
    pass


# Workflow-related business logic exceptions
class WorkflowError(BusinessLogicError):
    """Base class for workflow-related errors."""
    pass


class WorkflowNotFoundError(WorkflowError):
    """Raised when a workflow is not found."""
    pass


class WorkflowExecutionError(WorkflowError):
    """Raised when workflow execution fails."""
    pass


class WorkflowAlreadyRunningError(WorkflowError):
    """Raised when workflow is already running on the specified file."""
    pass


class InvalidWorkflowDataError(WorkflowError):
    """Raised when workflow data is invalid."""
    pass


# Cognito user-related business logic exceptions
class CognitoUserError(BusinessLogicError):
    """Base class for Cognito user errors."""
    pass


class CognitoUserNotFoundError(CognitoUserError):
    """Raised when a Cognito user is not found."""
    pass


class CognitoUserAlreadyExistsError(CognitoUserError):
    """Raised when trying to create a user that already exists."""
    pass


class InvalidCognitoUserDataError(CognitoUserError):
    """Raised when Cognito user data is invalid."""
    pass


class CognitoUserOperationError(CognitoUserError):
    """Raised when Cognito user operations fail."""
    pass


# Role-related business logic exceptions
class RoleError(BusinessLogicError):
    """Base class for role-related errors."""
    pass


class RoleNotFoundError(RoleError):
    """Raised when a role is not found."""
    pass


class RoleAlreadyExistsError(RoleError):
    """Raised when trying to create a role that already exists."""
    pass


class RoleDeletionError(RoleError):
    """Raised when role deletion fails due to dependencies."""
    pass


class InvalidRoleDataError(RoleError):
    """Raised when role data is invalid."""
    pass


# Constraint-related business logic exceptions
class ConstraintError(BusinessLogicError):
    """Base class for constraint-related errors."""
    pass


class ConstraintNotFoundError(ConstraintError):
    """Raised when a constraint is not found."""
    pass


class ConstraintAlreadyExistsError(ConstraintError):
    """Raised when trying to create a constraint that already exists."""
    pass


class ConstraintDeletionError(ConstraintError):
    """Raised when constraint deletion fails due to dependencies."""
    pass


class InvalidConstraintDataError(ConstraintError):
    """Raised when constraint data is invalid."""
    pass


class TemplateImportError(ConstraintError):
    """Raised when constraint template import fails."""
    pass


# User role-related business logic exceptions
class UserRoleError(BusinessLogicError):
    """Base class for user role-related errors."""
    pass


class UserRoleNotFoundError(UserRoleError):
    """Raised when a user role is not found."""
    pass


class UserRoleAlreadyExistsError(UserRoleError):
    """Raised when trying to create a user role that already exists."""
    pass


class UserRoleDeletionError(UserRoleError):
    """Raised when user role deletion fails."""
    pass


class InvalidUserRoleDataError(UserRoleError):
    """Raised when user role data is invalid."""
    pass
