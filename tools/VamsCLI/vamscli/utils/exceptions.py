"""Custom exceptions for VamsCLI."""


class VamsCLIError(Exception):
    """Base exception for VamsCLI errors."""
    pass


class ConfigurationError(VamsCLIError):
    """Raised when there's a configuration issue."""
    pass


class AuthenticationError(VamsCLIError):
    """Raised when authentication fails."""
    pass


class APIError(VamsCLIError):
    """Raised when API calls fail."""
    pass


class VersionMismatchError(VamsCLIError):
    """Raised when CLI and API versions don't match."""
    pass


class SetupRequiredError(VamsCLIError):
    """Raised when setup is required before running commands."""
    pass


class ProfileNotFoundError(VamsCLIError):
    """Raised when required profile is not found."""
    pass


class OverrideTokenError(VamsCLIError):
    """Raised when there's an issue with override tokens."""
    pass


class APIUnavailableError(VamsCLIError):
    """Raised when the VAMS API is unavailable or incompatible."""
    pass


class AssetNotFoundError(VamsCLIError):
    """Raised when an asset is not found."""
    pass


class AssetAlreadyExistsError(VamsCLIError):
    """Raised when trying to create an asset that already exists"""
    pass


class AssetAlreadyArchivedError(VamsCLIError):
    """Raised when trying to archive an asset that is already archived"""
    pass


class AssetDeletionError(VamsCLIError):
    """Raised when asset deletion operations fail"""
    pass


class DatabaseNotFoundError(VamsCLIError):
    """Raised when a database is not found"""
    pass


class DatabaseAlreadyExistsError(VamsCLIError):
    """Raised when trying to create a database that already exists"""
    pass


class DatabaseDeletionError(VamsCLIError):
    """Raised when database deletion fails due to dependencies"""
    pass


class BucketNotFoundError(VamsCLIError):
    """Raised when a bucket is not found"""
    pass


class InvalidDatabaseDataError(VamsCLIError):
    """Raised when database data is invalid"""
    pass


class InvalidAssetDataError(VamsCLIError):
    """Raised when asset data is invalid"""
    pass


class FileUploadError(VamsCLIError):
    """Raised when file upload operations fail"""
    pass


class InvalidFileError(VamsCLIError):
    """Raised when a file is invalid for upload"""
    pass


class FileTooLargeError(VamsCLIError):
    """Raised when a file exceeds size limits"""
    pass


class PreviewFileError(VamsCLIError):
    """Raised when preview file validation fails"""
    pass


class UploadSequenceError(VamsCLIError):
    """Raised when upload sequence processing fails"""
    pass


class PartUploadError(VamsCLIError):
    """Raised when individual part upload fails"""
    pass


class ProfileError(VamsCLIError):
    """Raised when profile operations fail"""
    pass


class InvalidProfileNameError(VamsCLIError):
    """Raised when profile name is invalid"""
    pass


class ProfileAlreadyExistsError(VamsCLIError):
    """Raised when trying to create a profile that already exists"""
    pass


class FileNotFoundError(VamsCLIError):
    """Raised when a file is not found"""
    pass


class FileOperationError(VamsCLIError):
    """Raised when file operations fail"""
    pass


class InvalidPathError(VamsCLIError):
    """Raised when file paths are invalid"""
    pass


class FilePermissionError(VamsCLIError):
    """Raised when user lacks permissions for file operations"""
    pass


class FileAlreadyExistsError(VamsCLIError):
    """Raised when trying to create a file that already exists"""
    pass


class FileArchivedError(VamsCLIError):
    """Raised when trying to operate on archived files"""
    pass


class InvalidVersionError(VamsCLIError):
    """Raised when file version is invalid"""
    pass


class TagNotFoundError(VamsCLIError):
    """Raised when a tag is not found."""
    pass


class TagAlreadyExistsError(VamsCLIError):
    """Raised when trying to create a tag that already exists."""
    pass


class TagTypeNotFoundError(VamsCLIError):
    """Raised when a tag type is not found."""
    pass


class TagTypeAlreadyExistsError(VamsCLIError):
    """Raised when trying to create a tag type that already exists."""
    pass


class TagTypeInUseError(VamsCLIError):
    """Raised when trying to delete a tag type that is currently in use by tags."""
    pass


class InvalidTagDataError(VamsCLIError):
    """Raised when tag data is invalid."""
    pass


class InvalidTagTypeDataError(VamsCLIError):
    """Raised when tag type data is invalid."""
    pass


class AssetVersionError(VamsCLIError):
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


class AssetLinkError(VamsCLIError):
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


class FileDownloadError(VamsCLIError):
    """Raised when file download operations fail."""
    pass


class DownloadError(VamsCLIError):
    """Raised when individual download operations fail."""
    pass


class AssetDownloadError(VamsCLIError):
    """Raised when asset download operations fail."""
    pass


class PreviewNotFoundError(VamsCLIError):
    """Raised when asset preview is not found."""
    pass


class AssetNotDistributableError(VamsCLIError):
    """Raised when trying to download from non-distributable asset."""
    pass


class DownloadTreeError(VamsCLIError):
    """Raised when asset tree traversal fails."""
    pass


class SearchError(VamsCLIError):
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
