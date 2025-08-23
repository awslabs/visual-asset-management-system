# File Operations Commands

This document covers VamsCLI file management commands for uploading, organizing, and managing files within assets.

## File Upload Commands

### `vamscli file upload`

Upload files to an asset in VAMS with advanced chunking, progress monitoring, and retry logic.

**Arguments:**

-   `files_or_directory`: List of file paths OR single directory path (optional if using --directory or --json-input)

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)

**File Source Options (mutually exclusive):**

-   `--directory`: Directory to upload (mutually exclusive with file arguments)

**Upload Configuration:**

-   `--asset-preview`: Upload as asset preview (single file only)
-   `--asset-location`: Base asset location (default: "/")
-   `--recursive`: Include subdirectories when uploading directory

**Performance Options:**

-   `--parallel-uploads`: Max parallel uploads (default: 10)
-   `--retry-attempts`: Retry attempts per part (default: 3)
-   `--force-skip`: Auto-skip failed parts after retries

**Input/Output Options:**

-   `--json-input`: JSON input with all parameters (file path with @ prefix or JSON string)
-   `--json-output`: Output API response as JSON
-   `--hide-progress`: Hide upload progress display

**Examples:**

**Single File Upload:**

```bash
vamscli file upload -d my-db -a my-asset /path/to/file.gltf
```

**Multiple Files:**

```bash
vamscli file upload -d my-db -a my-asset file1.jpg file2.png file3.obj
```

**Directory Upload:**

```bash
# Upload directory (non-recursive)
vamscli file upload -d my-db -a my-asset --directory /path/to/models

# Upload directory recursively
vamscli file upload -d my-db -a my-asset --directory /path/to/models --recursive
```

**Asset Preview Upload:**

```bash
vamscli file upload -d my-db -a my-asset --asset-preview preview.jpg
```

**Custom Asset Location:**

```bash
vamscli file upload -d my-db -a my-asset --asset-location /models/v2/ file.gltf
```

**Performance Tuning:**

```bash
vamscli file upload -d my-db -a my-asset --parallel-uploads 5 --retry-attempts 5 file.gltf
```

**JSON Input:**

```bash
# JSON string
vamscli file upload --json-input '{"database_id": "my-db", "asset_id": "my-asset", "files": ["/path/to/file.gltf"]}'

# JSON from file
vamscli file upload --json-input @upload-config.json --json-output
```

**Automation (no progress display):**

```bash
vamscli file upload -d my-db -a my-asset --hide-progress --json-output file.gltf
```

### Upload Features

-   **Intelligent Chunking**: Files automatically split into optimal chunks (150MB for <15GB files, 1GB for larger files)
-   **Sequence Management**: Files grouped into sequences to optimize performance and avoid timeouts
-   **Preview File Support**: Supports `.previewFile.` files with automatic validation of base files
-   **Progress Monitoring**: Real-time progress display with speed, ETA, and per-file status
-   **Retry Logic**: Configurable retry attempts with exponential backoff
-   **Parallel Uploads**: Concurrent part uploads with configurable limits
-   **Error Handling**: Comprehensive error handling with detailed failure reporting

### File Size Limits

-   Regular files: No limit (automatically chunked)
-   Preview files: 5MB maximum
-   Sequence limit: 3GB per upload sequence (automatically managed)

### Supported Upload Types

-   `assetFile`: Regular asset files (default)
-   `assetPreview`: Asset preview images (.png, .jpg, .jpeg, .svg, .gif)

## File Organization Commands

### `vamscli file create-folder`

Create a folder in an asset.

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)
-   `-p, --path`: Folder path to create (must end with /) (required)

**Input/Output Options:**

-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output API response as JSON

**Examples:**

```bash
# Create a folder
vamscli file create-folder -d my-db -a my-asset -p "/models/subfolder/"

# Create with JSON input
vamscli file create-folder --json-input '{"database_id": "my-db", "asset_id": "my-asset", "folder_path": "/models/"}'
```

### `vamscli file list`

List files in an asset with filtering and pagination support.

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)

**Filtering Options:**

-   `--prefix`: Filter files by prefix
-   `--include-archived`: Include archived files

**Pagination Options:**

-   `--max-items`: Maximum number of items to return (default: 1000)
-   `--page-size`: Number of items per page (default: 100)
-   `--starting-token`: Token for pagination

**Input/Output Options:**

-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output API response as JSON

**Examples:**

```bash
# List all files
vamscli file list -d my-db -a my-asset

# List files with prefix filter
vamscli file list -d my-db -a my-asset --prefix "models/"

# Include archived files
vamscli file list -d my-db -a my-asset --include-archived

# List with pagination
vamscli file list -d my-db -a my-asset --page-size 50 --starting-token "token123"

# List with JSON input
vamscli file list --json-input '{"database_id": "my-db", "asset_id": "my-asset", "prefix": "models/"}'
```

### `vamscli file info`

Get detailed information about a specific file, including version history.

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)
-   `-p, --path`: File path to get info for (required)

**Options:**

-   `--include-versions`: Include version history
-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output API response as JSON

**Examples:**

```bash
# Get file info
vamscli file info -d my-db -a my-asset -p "/model.gltf"

# Get file info with version history
vamscli file info -d my-db -a my-asset -p "/model.gltf" --include-versions

# Get info with JSON input
vamscli file info --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/model.gltf"}'
```

## File Management Commands

### `vamscli file move`

Move a file within an asset.

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)
-   `--source`: Source file path (required)
-   `--dest`: Destination file path (required)

**Input/Output Options:**

-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output API response as JSON

**Examples:**

```bash
# Move a file
vamscli file move -d my-db -a my-asset --source "/old/path.gltf" --dest "/new/path.gltf"

# Move with JSON input
vamscli file move --json-input '{"database_id": "my-db", "asset_id": "my-asset", "source": "/old.gltf", "dest": "/new.gltf"}'
```

### `vamscli file copy`

Copy a file within an asset or to another asset.

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)
-   `--source`: Source file path (required)
-   `--dest`: Destination file path (required)

**Options:**

-   `--dest-asset`: Destination asset ID (for cross-asset copy)
-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output API response as JSON

**Examples:**

```bash
# Copy within same asset
vamscli file copy -d my-db -a my-asset --source "/file.gltf" --dest "/copy.gltf"

# Copy to another asset
vamscli file copy -d my-db -a my-asset --source "/file.gltf" --dest "/file.gltf" --dest-asset other-asset

# Copy with JSON input
vamscli file copy --json-input '{"database_id": "my-db", "asset_id": "my-asset", "source": "/file.gltf", "dest": "/copy.gltf"}'
```

### `vamscli file archive`

Archive a file or files under a prefix (soft delete).

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)
-   `-p, --path`: File path to archive (required)

**Options:**

-   `--prefix`: Archive all files under the path as a prefix
-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output API response as JSON

**Examples:**

```bash
# Archive a single file
vamscli file archive -d my-db -a my-asset -p "/file.gltf"

# Archive all files under a prefix
vamscli file archive -d my-db -a my-asset -p "/folder/" --prefix

# Archive with JSON input
vamscli file archive --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/file.gltf"}'
```

### `vamscli file unarchive`

Unarchive a previously archived file.

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)
-   `-p, --path`: File path to unarchive (required)

**Input/Output Options:**

-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output API response as JSON

**Examples:**

```bash
# Unarchive a file
vamscli file unarchive -d my-db -a my-asset -p "/file.gltf"

# Unarchive with JSON input
vamscli file unarchive --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/file.gltf"}'
```

### `vamscli file delete`

Permanently delete a file or files under a prefix.

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)
-   `-p, --path`: File path to delete (required)
-   `--confirm`: Confirm permanent deletion (required for safety)

**Options:**

-   `--prefix`: Delete all files under the path as a prefix
-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output API response as JSON

**Examples:**

```bash
# Delete a single file (requires confirmation)
vamscli file delete -d my-db -a my-asset -p "/file.gltf" --confirm

# Delete all files under a prefix
vamscli file delete -d my-db -a my-asset -p "/folder/" --prefix --confirm

# Delete with JSON input
vamscli file delete --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/file.gltf", "confirm": true}'
```

## File Versioning Commands

### `vamscli file revert`

Revert a file to a previous version.

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)
-   `-p, --path`: File path to revert (required)
-   `-v, --version`: Version ID to revert to (required)

**Input/Output Options:**

-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output API response as JSON

**Examples:**

```bash
# Revert a file to a specific version
vamscli file revert -d my-db -a my-asset -p "/file.gltf" -v "version-id-123"

# Revert with JSON input
vamscli file revert --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/file.gltf", "version_id": "version-123"}'
```

## File Metadata Commands

### `vamscli file set-primary`

Set or remove primary type metadata for a file.

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)
-   `-p, --path`: File path to set primary type for (required)
-   `--type`: Primary type (required)

**Primary Type Values:**

-   `""`: Remove primary type metadata
-   `"primary"`: Mark as primary file
-   `"lod1"` to `"lod5"`: Level of detail variants
-   `"other"`: Custom type (requires --type-other)

**Options:**

-   `--type-other`: Custom primary type when type is "other"
-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output API response as JSON

**Examples:**

```bash
# Set primary type
vamscli file set-primary -d my-db -a my-asset -p "/file.gltf" --type "primary"

# Set LOD type
vamscli file set-primary -d my-db -a my-asset -p "/file.gltf" --type "lod1"

# Set custom primary type
vamscli file set-primary -d my-db -a my-asset -p "/file.gltf" --type "other" --type-other "custom-type"

# Remove primary type
vamscli file set-primary -d my-db -a my-asset -p "/file.gltf" --type ""

# Set with JSON input
vamscli file set-primary --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/file.gltf", "primary_type": "primary"}'
```

## Preview File Management Commands

### `vamscli file delete-preview`

Delete the asset preview file.

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)

**Input/Output Options:**

-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output API response as JSON

**Examples:**

```bash
# Delete asset preview
vamscli file delete-preview -d my-db -a my-asset

# Delete with JSON input
vamscli file delete-preview --json-input '{"database_id": "my-db", "asset_id": "my-asset"}'
```

### `vamscli file delete-auxiliary`

Delete auxiliary preview asset files.

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)
-   `-p, --path`: File path prefix for auxiliary files to delete (required)

**Input/Output Options:**

-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output API response as JSON

**Examples:**

```bash
# Delete auxiliary files for a specific path
vamscli file delete-auxiliary -d my-db -a my-asset -p "/file.gltf"

# Delete with JSON input
vamscli file delete-auxiliary --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/file.gltf"}'
```

## File Management Features

### File Organization

-   **Folder Management**: Create and organize files in folder structures
-   **Path-based Operations**: Use file paths for precise file targeting
-   **Prefix Operations**: Bulk operations on files under specific prefixes
-   **Cross-asset Operations**: Copy files between assets in the same database

### File Lifecycle

-   **Archiving**: Soft delete files (recoverable)
-   **Permanent Deletion**: Hard delete files (requires confirmation)
-   **Version Management**: Revert files to previous versions
-   **Metadata Management**: Set primary type classifications

### File Versioning

Files uploaded that already exist in the VAMS solution will update the existing file contents and create a new file version. This allows you to maintain version history while updating file content.

## File Management Workflow Examples

### Basic File Operations

```bash
# Upload files to asset
vamscli file upload -d my-db -a my-asset /path/to/models/ --recursive

# List files to see what was uploaded
vamscli file list -d my-db -a my-asset

# Get detailed info about a specific file
vamscli file info -d my-db -a my-asset -p "/model.gltf" --include-versions

# Create organization folders
vamscli file create-folder -d my-db -a my-asset -p "/textures/"
vamscli file create-folder -d my-db -a my-asset -p "/materials/"

# Move files to organize them
vamscli file move -d my-db -a my-asset --source "/texture.png" --dest "/textures/texture.png"
```

### Asset Lifecycle Management

```bash
# Archive old files (soft delete)
vamscli file archive -d my-db -a my-asset -p "/old-version/" --prefix

# Copy important files to backup asset
vamscli file copy -d my-db -a my-asset --source "/important.gltf" --dest "/important.gltf" --dest-asset backup-asset

# Set primary file metadata
vamscli file set-primary -d my-db -a my-asset -p "/main-model.gltf" --type "primary"
vamscli file set-primary -d my-db -a my-asset -p "/lod1-model.gltf" --type "lod1"

# Revert file to previous version if needed
vamscli file revert -d my-db -a my-asset -p "/model.gltf" -v "previous-version-id"
```

### Cleanup Operations

```bash
# Delete auxiliary preview files
vamscli file delete-auxiliary -d my-db -a my-asset -p "/model.gltf"

# Delete asset preview
vamscli file delete-preview -d my-db -a my-asset

# Permanently delete unwanted files (requires confirmation)
vamscli file delete -d my-db -a my-asset -p "/unwanted.tmp" --confirm

# Bulk delete files under prefix (requires confirmation)
vamscli file delete -d my-db -a my-asset -p "/temp/" --prefix --confirm
```

### Advanced Upload Scenarios

```bash
# Upload large files with custom settings
vamscli file upload -d my-db -a my-asset --parallel-uploads 3 --retry-attempts 5 large-model.gltf

# Upload directory structure preserving organization
vamscli file upload -d my-db -a my-asset --directory /project/assets --recursive --asset-location /project-v2/

# Upload with preview file
vamscli file upload -d my-db -a my-asset --asset-preview thumbnail.jpg

# Batch upload with JSON configuration
cat > upload-config.json << EOF
{
  "database_id": "my-db",
  "asset_id": "my-asset",
  "files": ["/path/to/model.gltf", "/path/to/texture.png"],
  "asset_location": "/models/",
  "parallel_uploads": 5
}
EOF

vamscli file upload --json-input @upload-config.json --json-output
```

### File Organization Workflow

```bash
# Create organized folder structure
vamscli file create-folder -d my-db -a my-asset -p "/models/"
vamscli file create-folder -d my-db -a my-asset -p "/textures/"
vamscli file create-folder -d my-db -a my-asset -p "/materials/"

# Upload files to specific locations
vamscli file upload -d my-db -a my-asset --asset-location /models/ model.gltf
vamscli file upload -d my-db -a my-asset --asset-location /textures/ texture.png
vamscli file upload -d my-db -a my-asset --asset-location /materials/ material.mtl

# Set file metadata for organization
vamscli file set-primary -d my-db -a my-asset -p "/models/model.gltf" --type "primary"
vamscli file set-primary -d my-db -a my-asset -p "/models/lod1.gltf" --type "lod1"
vamscli file set-primary -d my-db -a my-asset -p "/models/lod2.gltf" --type "lod2"

# List organized files
vamscli file list -d my-db -a my-asset --prefix "/models/"
vamscli file list -d my-db -a my-asset --prefix "/textures/"
```
