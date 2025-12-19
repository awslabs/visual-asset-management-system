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
-   **Zero-byte File Support**: Properly handles empty files (created during upload completion)
-   **Rate Limit Handling**: Automatic retry with exponential backoff for 429 throttling
-   **Large File Asynchronous Processing**: Automatic detection and notification when large files require additional processing time

### Upload Limits (Backend v2.2+)

**Per-Sequence Limits (automatically managed):**

-   **Files per sequence**: Maximum 50 files per API request
-   **Total parts per sequence**: Maximum 200 parts across all files per API request
-   **Sequence size**: Maximum 3GB per upload sequence

**Per-File Limits:**

-   **Parts per file**: Maximum 200 parts per individual file
-   **Part size**: Maximum 5GB per part (S3 limit)
-   **Preview file size**: Maximum 5MB per preview file

**Global Limits:**

-   **Total files**: Unlimited (automatically batched into multiple sequences)
-   **Rate limiting**: 20 upload initializations per user per minute
-   **Zero-byte files**: Supported and handled automatically

**Important**: The limits above are **per-sequence**, not per-upload. VamsCLI automatically creates multiple sequences when needed, allowing you to upload unlimited files. For example:

-   200 files with limit of 50 per sequence → 4 sequences created automatically
-   All sequences are processed efficiently using parallel pipeline architecture

### Multi-Sequence Upload Architecture

VamsCLI uses an advanced **parallel pipeline architecture** for optimal upload performance:

**3-Stage Pipeline:**

1. **Initialization**: All sequences initialized in parallel (concurrent API calls)
2. **Upload**: All parts from all sequences uploaded in parallel (shared pool respecting max_parallel limit)
3. **Completion**: Sequences completed as their parts finish (can overlap with stage 2)

**Benefits:**

-   ✅ **Massive speedup** for multi-sequence uploads (4× faster for 4 sequences)
-   ✅ **No idle time** between sequences
-   ✅ **Overlapped I/O** - completions happen while uploads continue
-   ✅ **Automatic batching** - unlimited files handled transparently

**Example: Uploading 200 files**

```bash
# Single command uploads all 200 files
vamscli file upload -d my-db -a my-asset /path/to/200/files/*.gltf

# VamsCLI automatically:
# 1. Creates 4 sequences (50 files each)
# 2. Initializes all 4 sequences in parallel
# 3. Uploads all parts from all sequences concurrently
# 4. Completes sequences as they finish
# Result: Much faster than sequential processing!
```

### Large Upload Handling

VamsCLI automatically handles large uploads by:

1. **Validates individual file constraints** (e.g., parts per file limit)
2. **Creates multiple sequences** automatically when per-sequence limits are reached
3. **Shows progress** for multi-sequence uploads with sequence count
4. **Handles rate limiting** with automatic retries and backoff
5. **Provides helpful error messages** only for unrecoverable errors (e.g., individual file too large)

**Only individual file constraint violations will cause errors:**

```bash
❌ File 'huge-file.bin' requires 250 parts, but maximum is 200 parts per file.
   File size: 37.5GB

💡 Tip: Individual files cannot exceed 200 parts. Consider compressing very large files before upload.
```

**Multi-sequence uploads are handled automatically:**

```bash
✅ Upload Summary:
  Files: 200 (200 regular, 0 preview)
  Total Size: 5.2GB
  Sequences: 4
  Parts: 156

📋 Multi-sequence upload: Your files will be uploaded in 4 separate requests
   This is handled automatically.
```

### Large File Asynchronous Processing

When uploading very large files, VAMS may need to perform additional processing after the upload completes. VamsCLI automatically detects this situation and provides appropriate feedback:

**Large File Processing Notification:**

```bash
✅ Upload completed successfully!

📋 Large File Processing:
   Your upload contains large files that will undergo separate asynchronous processing.
   This may take some time, so files may take longer to appear in the asset.
   You can check the asset files later using: vamscli file list -d my-db -a my-asset

Results:
  Successful files: 1/1
  Total size: 2.5GB
  Duration: 5m 23s
  Average speed: 8.2MB/s
```

**What this means:**

-   **Upload Success**: Your files have been successfully uploaded to VAMS
-   **Additional Processing**: Large files require extra processing time on the backend
-   **Delayed Visibility**: Files may not immediately appear in asset listings
-   **Automatic Processing**: No action required - processing happens automatically
-   **Check Later**: Use `vamscli file list` to verify files appear after processing completes

**When this occurs:**

-   Very large individual files (typically multi-gigabyte files)
-   Files that require intensive processing (complex 3D models, high-resolution images)
-   Uploads during high system load periods
-   Files that trigger additional validation or conversion processes

**Recommended workflow:**

```bash
# Upload large files
vamscli file upload -d my-db -a my-asset large-model.gltf

# If large file processing is indicated, wait and check later
# (Processing time varies based on file size and complexity)

# Check if files have appeared (retry periodically)
vamscli file list -d my-db -a my-asset

# Get detailed file information once processing completes
vamscli file info -d my-db -a my-asset -p "/large-model.gltf"
```

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

List files in an asset with filtering, pagination, and performance optimization support.

**Required Options:**

-   `-d, --database`: Database ID (required)
-   `-a, --asset`: Asset ID (required)

**Filtering Options:**

-   `--prefix`: Filter files by prefix
-   `--include-archived`: Include archived files

**Performance Options:**

-   `--basic`: Skip expensive lookups for faster listing (skips version checks, preview file processing, and metadata lookups)

**Pagination Options:**

Choose one pagination mode:

**Manual Pagination:**

-   `--page-size`: Number of items per page (passed to API, uses API defaults if not specified)
-   `--starting-token`: Token for pagination (get next page)

**Auto-Pagination:**

-   `--auto-paginate`: Automatically fetch all items (default: up to 10,000 total)
-   `--max-items`: Maximum total items to fetch (only with `--auto-paginate`, default: 10,000)
-   `--page-size`: Number of items per page (optional, passed to API)

**Input/Output Options:**

-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output API response as JSON

**Examples:**

**Basic Listing:**

```bash
# List all files (uses API defaults: 200 items in full mode, 1500 in basic mode)
vamscli file list -d my-db -a my-asset

# Fast listing with basic mode
vamscli file list -d my-db -a my-asset --basic

# List files with prefix filter
vamscli file list -d my-db -a my-asset --prefix "models/"

# Include archived files
vamscli file list -d my-db -a my-asset --include-archived
```

**Auto-Pagination (Recommended for Complete Listings):**

```bash
# Automatically fetch all items (default: up to 10,000)
vamscli file list -d my-db -a my-asset --auto-paginate

# Auto-paginate with custom limit (fetch up to 5,000 items)
vamscli file list -d my-db -a my-asset --auto-paginate --max-items 5000

# Auto-paginate with custom page size (controls items per API request)
vamscli file list -d my-db -a my-asset --auto-paginate --page-size 500

# Auto-paginate with basic mode (fastest for large directories)
vamscli file list -d my-db -a my-asset --auto-paginate --basic

# Auto-paginate with filters
vamscli file list -d my-db -a my-asset --auto-paginate --prefix "models/" --include-archived

# Auto-paginate with JSON output
vamscli file list -d my-db -a my-asset --auto-paginate --json-output
```

**Manual Pagination:**

```bash
# Get first page with custom page size
vamscli file list -d my-db -a my-asset --page-size 200

# Get next page using token from previous response
vamscli file list -d my-db -a my-asset --starting-token "token123" --page-size 200

# Manual pagination with filters
vamscli file list -d my-db -a my-asset --page-size 100 --prefix "models/"
```

**JSON Input:**

```bash
# List with JSON input
vamscli file list --json-input '{"database_id": "my-db", "asset_id": "my-asset", "prefix": "models/", "basic": true}'

# Auto-paginate with JSON input
vamscli file list --json-input '{"database_id": "my-db", "asset_id": "my-asset", "auto_paginate": true, "max_items": 5000}'
```

**Parameter Behavior:**

| Parameter          | Auto-Paginate Mode               | Manual Mode                      | Passed to API? |
| ------------------ | -------------------------------- | -------------------------------- | -------------- |
| `--page-size`      | ✅ Optional (controls page size) | ✅ Optional (controls page size) | ✅ YES         |
| `--max-items`      | ✅ Controls total limit          | ❌ Ignored (shows warning)       | ❌ NO          |
| `--starting-token` | ❌ Not allowed                   | ✅ Optional (next page)          | ✅ YES         |
| `--basic`          | ✅ Optional                      | ✅ Optional                      | ✅ YES         |

**Performance Comparison:**

| Mode                      | Speed        | Use Case                                                                    |
| ------------------------- | ------------ | --------------------------------------------------------------------------- |
| **Full mode**             | Standard     | When you need complete metadata (version IDs, preview files, primary types) |
| **Basic mode**            | ~100x faster | Quick directory scans, file counting, existence checks                      |
| **Auto-paginate**         | Automatic    | Get complete listings without manual token management                       |
| **Auto-paginate + Basic** | Fastest      | Large directories (1000+ files) where metadata isn't needed                 |

**When to Use Basic Mode:**

✅ **Use `--basic` when:**

-   Scanning large directories (1000+ files)
-   Checking file existence
-   Counting files
-   Automated scripts that don't need full metadata
-   Performance is critical

❌ **Don't use `--basic` when:**

-   You need version IDs
-   You need to see preview files
-   You need primary type metadata
-   You need archive status verification
-   You're working with small file sets (<100 files)

**Pagination Modes:**

| Mode              | Best For          | Max Items                       | Token Management |
| ----------------- | ----------------- | ------------------------------- | ---------------- |
| **Default**       | Quick checks      | API defaults (200 or 1500)      | None             |
| **Manual**        | Controlled paging | API-determined per page         | Manual           |
| **Auto-paginate** | Complete listings | CLI-controlled (default 10,000) | Automatic        |

**Auto-Pagination Output:**

```bash
$ vamscli file list -d my-db -a my-asset --auto-paginate

Auto-paginated: Retrieved 2,543 items in 3 page(s)

Found 2,543 file(s):

  📄 /model1.gltf (1024 bytes) [primary]
  📄 /model2.gltf (2048 bytes)
  📁 /textures/
  ...
```

**Important Notes:**

-   **`--max-items`** only works with `--auto-paginate` and controls the CLI-side aggregation limit (NOT passed to API)
-   **`--page-size`** works in both modes and is passed to the API to control items per request
-   Auto-pagination default limit is 10,000 items to prevent excessive API calls
-   Use `--max-items` with `--auto-paginate` to customize the total item limit

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
