# Asset and File Operation Issues

This document covers common issues with asset management, file operations, and asset versioning in VamsCLI.

## Asset Management Issues

### Asset Not Found

**Error:**

```
✗ Asset Error: Asset 'my-asset' not found in database 'my-database'
```

**Solutions:**

1. Verify the asset ID is correct
2. Check the database ID is correct
3. Use `vamscli assets list -d my-database` to see available assets
4. Ensure you have permission to access the asset

### Asset Already Exists

**Error:**

```
✗ Asset Error: Asset already exists
```

**Solutions:**

1. Use a different asset ID
2. Use `vamscli assets update` to modify the existing asset
3. Check existing assets: `vamscli assets list -d my-database`

### Database Not Found

**Error:**

```
✗ Database Error: Database 'my-database' not found
```

**Solutions:**

1. Verify the database ID is correct
2. Check if you have permission to access the database
3. Contact your administrator about database access

## File Upload Issues

### File Extension Validation Error

**Error:**

```
✗ File Extension Validation Error: Database has file extension restrictions: .glb,.gltf

The following files do not meet the restriction:
  - /document.pdf (extension: .pdf)
  - /readme.txt (extension: .txt)

The database 'my-db' restricts uploads to specific file types.
Please check the allowed extensions and try again.
```

**Cause:**

The database is configured with `restrictFileUploadsToExtensions` to limit which file types can be uploaded.

**Solutions:**

1. **Check database restrictions:**

    ```bash
    vamscli database get -d my-db
    # Look for: restrictFileUploadsToExtensions: .glb,.gltf
    ```

2. **Upload only allowed file types:**

    ```bash
    # Only upload files with allowed extensions
    vamscli file upload -d my-db -a my-asset model.glb texture.gltf
    ```

3. **Convert files to allowed formats:**

    - Convert files to one of the allowed extensions
    - Use appropriate conversion tools for your file type

4. **Contact administrator to modify restrictions:**

    - If you need to upload other file types, contact your database administrator
    - They can update the database configuration to allow additional extensions

5. **Use .all wildcard (administrator only):**
    ```bash
    # Administrator can set to allow all file types
    vamscli database update my-db --restrict-file-uploads-to-extensions ".all"
    ```

**Important Notes:**

-   Extension validation is **case-insensitive** (.GLB and .glb are treated the same)
-   **Asset preview uploads** (`--asset-preview`) skip extension validation
-   **Preview auxiliary files** (`.previewFile.` in filename) skip extension validation
-   Validation checks **all files** before upload and reports **all violations** at once
-   Empty or missing `restrictFileUploadsToExtensions` means all file types are allowed

**Example Workflow:**

```bash
# 1. Check what extensions are allowed
vamscli database get -d my-db

# 2. Filter your files to only allowed extensions
# If restrictions are: .glb,.gltf

# 3. Upload only allowed files
vamscli file upload -d my-db -a my-asset model.glb model.gltf

# 4. Preview files are always allowed regardless of restrictions
vamscli file upload -d my-db -a my-asset --asset-preview thumbnail.jpg
```

### File Not Found

**Error:**

```
❌ File not found: /path/to/file.gltf
```

**Solutions:**

1. Verify the file path is correct
2. Check file permissions
3. Ensure the file exists and is readable

### Preview File Too Large

**Error:**

```
❌ Preview file preview.jpg exceeds maximum size of 5MB (actual size: 8.2MB)
```

**Solutions:**

1. Reduce the file size to under 5MB
2. Use image compression tools
3. Use a different image format

### Invalid Preview File Extension

**Error:**

```
❌ Preview file preview.txt has unsupported extension '.txt'. Allowed extensions: .png, .jpg, .jpeg, .svg, .gif
```

**Solutions:**

1. Convert the file to a supported format (.png, .jpg, .jpeg, .svg, .gif)
2. Use a different file for the preview

### Preview File Missing Base File

**Error:**

```
❌ Preview files missing base files: model.previewFile.jpg
```

**Solutions:**

1. Ensure the base file (model.jpg) exists in the upload
2. Upload the base file first, then the preview file
3. Check the file naming convention

### Upload Sequence Failed

**Error:**

```
❌ Upload failed: No files were successfully uploaded
```

**Solutions:**

1. Check your network connection
2. Verify file permissions and accessibility
3. Try uploading files individually
4. Use `--retry-attempts` to increase retry count
5. Check file sizes and formats

### Large File Processing Delays

**Situation:**

```
✅ Upload completed successfully!

📋 Large File Processing:
   Your upload contains large files that will undergo separate asynchronous processing.
   This may take some time, so files may take longer to appear in the asset.
   You can check the asset files later using: vamscli file list -d my-db -a my-asset
```

**What this means:**

-   Your files have been successfully uploaded to VAMS
-   Large files require additional processing time on the backend
-   Files may not immediately appear in asset listings
-   No action is required - processing happens automatically

**If files don't appear after extended time:**

1. **Wait for processing**: Large files can take several minutes to hours depending on size and complexity
2. **Check periodically**: Use `vamscli file list -d <database> -a <asset>` to check if files have appeared
3. **Verify upload success**: Confirm the upload completed successfully (exit code 0)
4. **Check system status**: Contact your administrator if files don't appear after several hours
5. **Re-upload if necessary**: If processing fails, you may need to re-upload the files

**Typical processing times:**

-   Small to Medium files (< 1GB): Immediate
-   Medium to Large files (1GB - 10GB): 0-2 minutes
-   Very large files (10GB - 5TB): 2 - 15 minutes

**Best practices for large files:**

-   Upload during off-peak hours when possible
-   Consider compressing files before upload if appropriate
-   Split very large uploads into smaller batches
-   Monitor system resources and network stability

## Asset Version Issues

### Asset Version Not Found

**Error:**

```
✗ Asset Version Not Found: Asset version '999' not found
```

**Solutions:**

1. Verify the version ID is correct
2. Use `vamscli asset-version list -d <database> -a <asset>` to see available versions
3. Check if you have permission to access the asset version
4. Ensure the asset exists: `vamscli assets get -d <database> <asset>`

### Asset Version Operation Failed

**Error:**

```
✗ Asset Version Operation Error: Asset version creation failed: No valid files found
```

**Solutions:**

1. Ensure the asset has files before creating a version
2. Upload files first: `vamscli file upload -d <database> -a <asset> <files>`
3. Check if files are archived: `vamscli file list -d <database> -a <asset> --include-archived`
4. Verify file permissions and accessibility

### Asset Version Revert Failed

**Error:**

```
✗ Asset Version Revert Error: Asset version revert failed: Target version has no accessible files
```

**Solutions:**

1. Check if target version files still exist: `vamscli asset-version get -d <database> -a <asset> -v <version>`
2. Files may have been permanently deleted from S3
3. Try reverting to a different version
4. Check file status indicators in version details

### Invalid Asset Version Data

**Error:**

```
✗ Invalid Asset Version Data: Invalid version data: Comment is required
```

**Solutions:**

1. Ensure all required fields are provided (comment is always required)
2. Check JSON input format is correct
3. Verify file array format for specific file versions:
    ```json
    {
        "useLatestFiles": false,
        "comment": "Version comment",
        "files": [{ "relativeKey": "file.obj", "versionId": "abc123", "isArchived": false }]
    }
    ```

### Version Creation with Specific Files Failed

**Error:**

```
✗ Invalid Version Data: Either useLatestFiles must be true or a list of files must be provided
```

**Solutions:**

1. Use `--use-latest-files` flag (default behavior)
2. Or provide specific files with `--files` option
3. Don't use both options together
4. Ensure files JSON is properly formatted

### Skipped Files During Version Operations

**Warning:**

```
Skipped Files (2):
  - deleted-file.obj
  - missing-texture.png

Skipped files may have been permanently deleted or are no longer accessible.
```

**Solutions:**

1. This is informational - the operation still succeeded
2. Skipped files were permanently deleted or are no longer accessible
3. Check file status: `vamscli file info -d <database> -a <asset> -p <file-path>`
4. Consider if missing files need to be re-uploaded

## Metadata Management Issues

### Metadata Not Found

**Error:**

```
✗ Asset Error: Asset 'my-asset' not found in database 'my-database'
```

**Solutions:**

1. Verify the asset ID and database ID are correct
2. Check if the asset exists: `vamscli assets get -d <database> <asset-id>`
3. Ensure you have permission to access the asset
4. For file metadata, verify the file path is correct

### Invalid Metadata Data

**Error:**

```
✗ Invalid Asset Data: Invalid metadata data: metadata version 1 requires string keys and values
```

**Solutions:**

1. Ensure all metadata keys are strings
2. For complex values, use JSON strings: `'{"key": "value"}'`
3. Check JSON input format is correct
4. Verify no null or undefined values in metadata

### JSON Input Parsing Error

**Error:**

```
✗ Invalid JSON input: Expecting ',' delimiter: line 1 column 15 (char 14)
```

**Solutions:**

1. Validate JSON syntax: `echo '{"test": "value"}' | python -m json.tool`
2. Check for missing quotes, commas, or brackets
3. Use single quotes around JSON strings in bash: `'{"key": "value"}'`
4. For file input, verify file exists and contains valid JSON

### Interactive Metadata Input Issues

**Error:**

```
Error processing input: Expecting value: line 1 column 1 (char 0)
```

**Solutions:**

1. Enter valid JSON for complex values: `{"key": "value"}`
2. Use plain strings for simple values: `My Asset Title`
3. For arrays, use JSON format: `["item1", "item2"]`
4. For numbers, enter numeric values: `42` or `3.14`
5. For booleans, use: `true` or `false`

### File Path Metadata Issues

**Error:**

```
✗ File not found: File '/models/file.gltf' not found in asset
```

**Solutions:**

1. Verify the file path is correct (case-sensitive)
2. Use `vamscli file list -d <database> -a <asset>` to see available files
3. Check if file is archived: `vamscli file list -d <database> -a <asset> --include-archived`
4. Ensure file path starts with `/` for absolute paths within asset

### Metadata Deletion Confirmation

**Error:**

```
Operation cancelled.
```

**Solutions:**

1. This is normal when you choose 'n' for deletion confirmation
2. Use 'y' to confirm deletion when prompted
3. For automation, use JSON input to skip interactive prompts
4. Remember that metadata deletion is permanent and cannot be undone

## Asset Links Issues

### Asset Link Not Found

**Error:**

```
✗ Asset Link Not Found: Asset link '12345678-1234-1234-1234-123456789012' not found
```

**Solutions:**

1. Verify the asset link ID is correct
2. Use `vamscli asset-links list -d <database> --asset-id <asset>` to see available asset links
3. Check if you have permission to access the linked assets
4. Ensure the asset link hasn't been deleted

### Asset Link Already Exists

**Error:**

```
✗ Asset Link Already Exists: A relationship already exists between these assets
```

**Solutions:**

1. Check existing relationships: `vamscli asset-links list -d <database> --asset-id <asset>`
2. Update the existing link instead: `vamscli asset-links update --asset-link-id <id> --tags <new-tags>`
3. Delete the existing link first: `vamscli asset-links delete --asset-link-id <id>`
4. Use different assets for the relationship

### Cycle Detection Error

**Error:**

```
✗ Cycle Detection Error: Creating this parent-child relationship would create a cycle in the asset hierarchy
```

**Solutions:**

1. Review the asset hierarchy: `vamscli asset-links list -d <database> --asset-id <asset> --tree-view`
2. Use `related` relationship type instead of `parentChild`
3. Restructure the asset hierarchy to avoid cycles
4. Create the relationship in the opposite direction if appropriate

### Asset Link Permission Error

**Error:**

```
✗ Asset Link Permission Error: Not authorized to create asset link: You need permissions on both assets
```

**Solutions:**

1. Ensure you have permissions on both the source and target assets
2. Contact your administrator for asset permissions
3. Check asset access: `vamscli assets get -d <database> <asset-id>`
4. Verify both assets exist and are accessible

### Asset Link Validation Error

**Error:**

```
✗ Asset Link Validation Error: Invalid asset link data: One or both assets do not exist
```

**Solutions:**

1. Verify both assets exist:
    ```bash
    vamscli assets get -d <from-database> <from-asset>
    vamscli assets get -d <to-database> <to-asset>
    ```
2. Check asset IDs and database IDs are correct
3. Ensure assets are not archived (unless using `--show-archived`)
4. Create missing assets before creating the link

### Invalid Relationship Type

**Error:**

```
✗ Invalid Relationship Type: Invalid relationship type 'invalid'. Must be one of: related, parentChild
```

**Solutions:**

1. Use `related` for bidirectional relationships
2. Use `parentChild` for hierarchical relationships
3. Check spelling and case sensitivity
4. Refer to documentation for relationship type details

### Asset Link Update Failed

**Error:**

```
✗ Asset Link Validation Error: Invalid update data: At least one field must be provided for update
```

**Solutions:**

1. Provide tags to update: `--tags tag1 --tags tag2`
2. Use JSON input with update data: `--json-input '{"tags":["new-tag"]}'`
3. Currently only tags can be updated on asset links
4. To change relationship type or assets, delete and recreate the link

### Asset Links List Permission Error

**Error:**

```
✗ Asset Link Permission Error: Not authorized to view asset links: You need permissions on the asset
```

**Solutions:**

1. Ensure you have permissions on the target asset
2. Check asset access: `vamscli assets get -d <database> <asset-id>`
3. Contact your administrator for asset permissions
4. Verify the asset exists and is accessible

## File Management Issues

### File Operation Permission Error

**Error:**

```
✗ File Permission Error: Not authorized to perform file operation
```

**Solutions:**

1. Ensure you have permissions on the asset
2. Check if the asset is archived
3. Verify file exists: `vamscli file info -d <database> -a <asset> -p <file-path>`
4. Contact your administrator for asset permissions

### File Not Found in Asset

**Error:**

```
✗ File Not Found: File '/path/to/file.gltf' not found in asset
```

**Solutions:**

1. Verify the file path is correct (case-sensitive)
2. Use `vamscli file list -d <database> -a <asset>` to see available files
3. Check if file is archived: `vamscli file list -d <database> -a <asset> --include-archived`
4. Ensure the file was uploaded successfully

### File Move/Copy Failed

**Error:**

```
✗ File Operation Error: File move/copy operation failed
```

**Solutions:**

1. Verify source file exists
2. Check destination path is valid
3. Ensure you have permissions on both source and destination
4. For cross-asset copy, verify destination asset exists

### File Archive/Delete Failed

**Error:**

```
✗ File Operation Error: File archive/delete operation failed
```

**Solutions:**

1. Verify the file exists and is accessible
2. Check if file is already archived (for archive operations)
3. Use `--confirm` flag for delete operations
4. Ensure you have appropriate permissions

## Performance Issues

### Slow File Uploads

**Solutions:**

1. Reduce parallel uploads: `--parallel-uploads 5`
2. Check your network bandwidth
3. Try uploading smaller batches of files
4. Use `--hide-progress` to reduce terminal overhead

### Memory Issues with Large Files

**Solutions:**

1. VamsCLI automatically chunks large files
2. Reduce parallel uploads for very large files
3. Ensure sufficient disk space for temporary files
4. Close other applications to free memory

### Upload Timeout Issues

**Solutions:**

1. Increase retry attempts: `--retry-attempts 5`
2. Reduce parallel uploads for unstable connections
3. Check network stability
4. Try uploading during off-peak hours

## Troubleshooting Workflows

### Asset Operation Troubleshooting

```bash
# 1. Verify asset exists
vamscli assets get -d <database> <asset-id>

# 2. Check asset permissions
vamscli assets list -d <database>

# 3. Check asset status (archived, etc.)
vamscli assets get -d <database> <asset-id> --show-archived

# 4. Try operation with debug mode
vamscli --debug assets <operation> <parameters>
```

### File Operation Troubleshooting

```bash
# 1. List files in asset
vamscli file list -d <database> -a <asset>

# 2. Check specific file info
vamscli file info -d <database> -a <asset> -p <file-path>

# 3. Include archived files if needed
vamscli file list -d <database> -a <asset> --include-archived

# 4. Try operation with debug mode
vamscli --debug file <operation> <parameters>
```

### Asset Version Troubleshooting

```bash
# 1. List all versions
vamscli asset-version list -d <database> -a <asset>

# 2. Check specific version details
vamscli asset-version get -d <database> -a <asset> -v <version>

# 3. Check file status in version
vamscli file info -d <database> -a <asset> -p <file-path> --include-versions

# 4. Try operation with debug mode
vamscli --debug asset-version <operation> <parameters>
```

### Metadata Troubleshooting

```bash
# 1. Check if asset exists
vamscli assets get -d <database> <asset-id>

# 2. Get current metadata
vamscli metadata get -d <database> -a <asset>

# 3. For file metadata, verify file exists
vamscli file list -d <database> -a <asset>
vamscli file info -d <database> -a <asset> -p <file-path>

# 4. Test JSON input separately
echo '{"title": "test"}' | python -m json.tool

# 5. Try operation with debug mode
vamscli --debug metadata <operation> <parameters>
```

### Asset Links Troubleshooting

```bash
# 1. List existing asset links
vamscli asset-links list -d <database> --asset-id <asset>

# 2. Check asset link details
vamscli asset-links get --asset-link-id <link-id>

# 3. Verify both assets exist
vamscli assets get -d <from-database> <from-asset>
vamscli assets get -d <to-database> <to-asset>

# 4. Check for cycles in parent-child relationships
vamscli asset-links list -d <database> --asset-id <asset> --tree-view

# 5. Try operation with debug mode
vamscli --debug asset-links <operation> <parameters>
```

## Common Error Patterns

### Permission-Related Errors

Most permission errors can be resolved by:

1. Verifying you have access to the required resources
2. Checking if resources are archived or deleted
3. Contacting your administrator for permission grants
4. Ensuring you're using the correct profile for the environment

### Resource Not Found Errors

Most "not found" errors can be resolved by:

1. Double-checking resource IDs and names
2. Using list commands to see available resources
3. Checking if resources are archived or deleted
4. Verifying you're in the correct database/environment

### Validation Errors

Most validation errors can be resolved by:

1. Checking required parameters are provided
2. Verifying parameter formats and values
3. Using JSON input for complex data structures
4. Referring to command help for parameter requirements

### Operation Failed Errors

Most operation failures can be resolved by:

1. Using debug mode to get detailed error information
2. Checking network connectivity
3. Verifying resource states and dependencies
4. Retrying the operation after addressing underlying issues

## Advanced Troubleshooting

### Enable Verbose Logging

For maximum debugging information:

```bash
# Set environment variable for detailed logging
export VAMSCLI_DEBUG=1
vamscli --debug <command>
```

### Validate JSON Input

Test JSON input separately:

```bash
# Validate JSON syntax
echo '{"test": "value"}' | python -m json.tool

# Test JSON file
python -m json.tool < input.json
```

### Check Resource Dependencies

```bash
# For asset operations, check database exists
vamscli database get -d <database-id>

# For file operations, check asset exists
vamscli assets get -d <database> <asset-id>

# For asset links, check both assets exist
vamscli assets get -d <from-database> <from-asset>
vamscli assets get -d <to-database> <to-asset>
```

## Recovery Procedures

### Asset Recovery

```bash
# If asset appears missing, check if archived
vamscli assets get -d <database> <asset-id> --show-archived

# If asset is archived, it can still be accessed with --show-archived flag
vamscli assets list -d <database> --show-archived
```

### File Recovery

```bash
# Check if files are archived
vamscli file list -d <database> -a <asset> --include-archived

# Unarchive files if needed
vamscli file unarchive -d <database> -a <asset> -p <file-path>

# Check file version history
vamscli file info -d <database> -a <asset> -p <file-path> --include-versions

# Revert to previous version if needed
vamscli file revert -d <database> -a <asset> -p <file-path> -v <version-id>
```

### Version Recovery

```bash
# List all versions to find target
vamscli asset-version list -d <database> -a <asset>

# Get details of target version
vamscli asset-version get -d <database> -a <asset> -v <version>

# Revert to previous version
vamscli asset-version revert -d <database> -a <asset> -v <version> --comment "Recovery revert"
```

## Frequently Asked Questions

### Q: Why can't I find my asset?

**A:** Check if it's archived with `--show-archived`, verify the database ID, and ensure you have permissions.

### Q: Why did my file upload fail?

**A:** Check file permissions, network connectivity, file size limits, and try with fewer parallel uploads.

### Q: Why can't I create an asset version?

**A:** Ensure the asset has files uploaded first, and verify you have permissions on the asset.

### Q: Why can't I create an asset link?

**A:** Verify both assets exist, you have permissions on both, and the relationship won't create cycles.

### Q: Why are some files skipped during version operations?

**A:** Files may have been permanently deleted from S3 or are no longer accessible. This is normal and the operation continues.

### Q: How do I recover from a failed operation?

**A:** Use debug mode to understand the failure, check resource states, and address underlying issues before retrying.

### Q: Why do I get "cycle detection" errors?

**A:** Parent-child relationships cannot create cycles. Use `--tree-view` to visualize the hierarchy and restructure as needed.

## Asset Download Issues

### Download Attempts to Download Folders

**Problem:** Download command tries to download folder objects as files, or you see errors about downloading "/" or folder paths

**Cause:** In older versions, folder objects in the asset file list were not being filtered properly

**Solution:**

-   Ensure you're using the latest version of VamsCLI (v2.2.0 or later)
-   Folder objects are now automatically filtered - only actual files are downloaded
-   When using `--file-key "/"`, all files at the root level are downloaded (the "/" folder object itself is ignored)
-   When using `--recursive`, all files in the folder tree are downloaded (folder objects are ignored)
-   If you still see errors about downloading folders, this indicates a bug that should be reported

**Examples of correct behavior:**

```bash
# Download all files from root (filters out "/" folder object)
vamscli assets download /local/path -d my-db -a my-asset --file-key "/"

# Download all files from asset (filters out all folder objects)
vamscli assets download /local/path -d my-db -a my-asset

# Download folder recursively (filters out folder objects)
vamscli assets download /local/path -d my-db -a my-asset --file-key "/models/" --recursive
```

### Asset Not Distributable

**Error:**

```
✗ Asset not distributable: Asset not distributable
```

**Solutions:**

1. Check asset distributable status: `vamscli assets get -d <database> <asset-id>`
2. Update asset to be distributable: `vamscli assets update <asset-id> -d <database> --distributable`
3. Contact your administrator if you need access to non-distributable assets
4. Verify you have the correct asset ID

### Asset Preview Not Found

**Error:**

```
✗ Asset preview not available: Asset preview not found
```

**Solutions:**

1. Check if asset has a preview: `vamscli assets get -d <database> <asset-id>`
2. Upload a preview file: `vamscli file upload -d <database> -a <asset> --asset-preview preview.jpg`
3. Don't use `--asset-preview` flag if asset has no preview
4. Use regular file download instead

### No Files to Download

**Error:**

```
✗ Asset 'my-asset' currently has no files to download
```

**Solutions:**

1. Upload files to the asset first: `vamscli file upload -d <database> -a <asset> <files>`
2. Check if files are archived: `vamscli file list -d <database> -a <asset> --include-archived`
3. Verify you have permission to see the asset's files
4. Use a different asset that contains files

### File Not Found for Download

**Error:**

```
✗ File '/model.gltf' not found in asset
```

**Solutions:**

1. Check available files: `vamscli file list -d <database> -a <asset>`
2. Verify file path is correct (case-sensitive)
3. Check if file is archived: `vamscli file list -d <database> -a <asset> --include-archived`
4. Ensure file path starts with `/` for absolute paths within asset

### Download Tree Traversal Failed

**Error:**

```
✗ Failed to download asset tree: No assets found in the tree
```

**Solutions:**

1. Check asset links exist: `vamscli asset-links list -d <database> --asset-id <asset> --tree-view`
2. Verify you have permissions on child assets
3. Reduce tree depth if some assets are inaccessible
4. Check if child assets exist and are not archived

### Filename Conflicts in Flattened Download

**Error:**

```
✗ Filename conflicts detected in flattened download: model.gltf, texture.jpg
```

**Solutions:**

1. Don't use `--flatten-download-tree` when files have same names
2. Use structured download to maintain folder hierarchy
3. Download files individually to avoid conflicts
4. Rename conflicting files in the asset before downloading

### Download Timeout

**Error:**

```
✗ Download failed with status 408: Request timeout
```

**Solutions:**

1. Increase timeout: `--timeout 600` (10 minutes)
2. Reduce parallel downloads: `--parallel-downloads 3`
3. Check network connectivity and stability
4. Try downloading smaller files first
5. Increase retry attempts: `--retry-attempts 5`

### Download Network Error

**Error:**

```
✗ Download failed: Network connection error
```

**Solutions:**

1. Check internet connectivity
2. Verify firewall settings allow HTTPS connections
3. Try with fewer parallel downloads
4. Check if proxy settings are needed
5. Retry the operation after network issues are resolved

### Presigned URL Expired

**Error:**

```
✗ Download failed with status 403: Request has expired
```

**Solutions:**

1. Regenerate download URLs (they expire after 24 hours)
2. Don't save shareable links for extended periods
3. Re-run the download command to get fresh URLs
4. For automation, generate URLs just before use

### Download Progress Issues

**Error:**

```
Progress display not updating or showing incorrect information
```

**Solutions:**

1. Use `--hide-progress` to disable progress display
2. Check terminal compatibility with progress bars
3. Reduce update frequency by using fewer parallel downloads
4. Use `--json-output` for automation instead of progress display

### Asset Tree Download Folder Creation Failed

**Error:**

```
✗ Failed to create asset folder: Permission denied
```

**Solutions:**

1. Check local directory permissions
2. Ensure parent directory exists and is writable
3. Use a different local path with appropriate permissions
4. Run with elevated permissions if necessary

## Download Troubleshooting Workflows

### Basic Download Troubleshooting

```bash
# 1. Verify asset exists and is distributable
vamscli assets get -d <database> <asset-id>

# 2. Check available files
vamscli file list -d <database> -a <asset>

# 3. Test shareable links first
vamscli assets download -d <database> -a <asset> --shareable-links-only

# 4. Try single file download
vamscli assets download /local/path -d <database> -a <asset> --file-key "/specific-file.gltf"

# 5. Use debug mode for detailed error information
vamscli --debug assets download /local/path -d <database> -a <asset>
```

### Asset Preview Download Troubleshooting

```bash
# 1. Check if asset has preview
vamscli assets get -d <database> <asset-id>

# 2. Test preview shareable link
vamscli assets download -d <database> -a <asset> --asset-preview --shareable-links-only

# 3. Try actual preview download
vamscli assets download /local/path -d <database> -a <asset> --asset-preview

# 4. Upload preview if missing
vamscli file upload -d <database> -a <asset> --asset-preview preview.jpg
```

### Asset Tree Download Troubleshooting

```bash
# 1. Check asset links structure
vamscli asset-links list -d <database> --asset-id <asset> --tree-view

# 2. Test with smaller tree depth
vamscli assets download /local/path -d <database> -a <asset> --asset-link-children-tree-depth 1

# 3. Check permissions on child assets
vamscli assets get -d <database> <child-asset-id>

# 4. Try shareable links for tree first
vamscli assets download -d <database> -a <asset> --asset-link-children-tree-depth 2 --shareable-links-only
```

### Performance Troubleshooting

```bash
# 1. Test with single download
vamscli assets download /local/path -d <database> -a <asset> --parallel-downloads 1

# 2. Increase timeout for large files
vamscli assets download /local/path -d <database> -a <asset> --timeout 600

# 3. Test network with small file first
vamscli assets download /local/path -d <database> -a <asset> --file-key "/small-file.txt"

# 4. Use progress monitoring
vamscli assets download /local/path -d <database> -a <asset> --parallel-downloads 3
```

### Q: How do I handle large file uploads?

**A:** VamsCLI automatically handles large files with chunking. Reduce parallel uploads and increase retry attempts for better reliability.

### Q: How do I download files from assets?

**A:** Use `vamscli assets download /local/path -d <database> -a <asset>` for whole assets, or specify `--file-key` for individual files. Use `--shareable-links-only` to get URLs without downloading.

### Q: Why can't I download from an asset?

**A:** Check if the asset is distributable with `vamscli assets get -d <database> <asset-id>`. Only distributable assets can be downloaded.

### Q: How do I handle download timeouts?

**A:** Increase timeout with `--timeout 600`, reduce parallel downloads with `--parallel-downloads 3`, and increase retry attempts with `--retry-attempts 5`.

### Q: How do I download asset trees?

**A:** Use `--asset-link-children-tree-depth <number>` to traverse asset link hierarchies. Check the tree structure first with `vamscli asset-links list --tree-view`.
