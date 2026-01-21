# Database Administration Commands

This document covers VamsCLI database management and bucket configuration commands.

## Database Management Commands

VamsCLI provides comprehensive database management capabilities for VAMS, including creation, updates, deletion, and bucket configuration management.

### `vamscli database list`

List all databases in the VAMS system.

**Options:**

-   `--show-deleted`: Include deleted databases
-   `--page-size`: Number of items per page
-   `--max-items`: Maximum total items to fetch (only with --auto-paginate, default: 10000)
-   `--starting-token`: Token for pagination (manual pagination)
-   `--auto-paginate`: Automatically fetch all items
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Basic listing (uses API defaults)
vamscli database list

# Include deleted databases
vamscli database list --show-deleted

# Auto-pagination to fetch all items (default: up to 10,000)
vamscli database list --auto-paginate

# Auto-pagination with custom limit
vamscli database list --auto-paginate --max-items 5000

# Auto-pagination with custom page size
vamscli database list --auto-paginate --page-size 500

# Manual pagination with page size
vamscli database list --page-size 200
vamscli database list --starting-token "token123" --page-size 200

# JSON output for automation
vamscli database list --json-output
```

**Pagination Features:**

-   **Auto-Pagination**: Use `--auto-paginate` to automatically fetch all items up to the limit
-   **Manual Pagination**: Use `--page-size` and `--starting-token` for manual page-by-page control
-   **CLI-Side Limit**: `--max-items` is a CLI-side limit (not passed to API) that controls total items fetched
-   **API-Side Control**: `--page-size` is passed to the API to control items per request
-   **Default Limit**: Auto-pagination defaults to 10,000 items maximum
-   **Progress Display**: Shows progress during auto-pagination in CLI mode

**Pagination Restrictions:**

-   Cannot use `--auto-paginate` with `--starting-token` (choose one pagination mode)
-   `--max-items` only applies with `--auto-paginate` (warning shown if used without it)
-   When using manual pagination, use the `NextToken` from the response as `--starting-token` for the next page

### `vamscli database create`

Create a new database in VAMS.

**Required Options:**

-   `-d, --database-id`: Database ID to create (required)

**Options:**

-   `--description`: Database description (required unless using --json-input)
-   `--default-bucket-id`: Default bucket ID (optional - will prompt if not provided)
-   `--restrict-metadata-outside-schemas`: Restrict metadata to defined schemas only (flag)
-   `--restrict-file-uploads-to-extensions`: Comma-separated list of allowed file extensions (e.g., ".pdf,.docx,.jpg")
-   `--json-input`: JSON input file path or JSON string with all database data
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Create database with bucket selection prompt
vamscli database create -d my-database --description "My Database"

# Create database with specific bucket
vamscli database create -d my-database --description "My Database" --default-bucket-id "bucket-uuid"

# Create with metadata restriction enabled
vamscli database create -d my-database --description "My Database" --default-bucket-id "bucket-uuid" --restrict-metadata-outside-schemas

# Create with file extension restrictions
vamscli database create -d my-database --description "My Database" --default-bucket-id "bucket-uuid" --restrict-file-uploads-to-extensions ".pdf,.docx,.jpg"

# Create with both restrictions
vamscli database create -d my-database --description "My Database" --default-bucket-id "bucket-uuid" --restrict-metadata-outside-schemas --restrict-file-uploads-to-extensions ".pdf,.png"

# Create with JSON input string
vamscli database create -d my-database --json-input '{"description":"Test Database","defaultBucketId":"bucket-uuid","restrictMetadataOutsideSchemas":true}'

# Create with JSON input from file
vamscli database create -d my-database --json-input @database-config.json --json-output
```

**JSON Input Format:**

```json
{
    "databaseId": "my-database",
    "description": "Database description",
    "defaultBucketId": "550e8400-e29b-41d4-a716-446655440000",
    "restrictMetadataOutsideSchemas": true,
    "restrictFileUploadsToExtensions": ".pdf,.docx,.jpg"
}
```

**Configuration Options:**

-   **Metadata Restriction**: When enabled, only metadata fields defined in the database schema can be added to assets
-   **File Extension Restriction**: When set, only files with the specified extensions can be uploaded to assets in this database
    -   Use comma-separated list with dots (e.g., ".pdf,.docx,.jpg")
    -   Special value ".all" bypasses restrictions
    -   Empty string or omitted means no restrictions

**Features:**

-   Interactive bucket selection if no bucket ID provided
-   Validates database ID format (lowercase, alphanumeric, hyphens, underscores)
-   Validates bucket existence before creation
-   Comprehensive error handling
-   Backend validates file extension format

### `vamscli database update`

Update an existing database in VAMS.

**Required Options:**

-   `-d, --database-id`: Database ID to update (required)

**Options:**

-   `--description`: New database description
-   `--default-bucket-id`: New default bucket ID
-   `--restrict-metadata-outside-schemas`: Enable metadata restriction to defined schemas
-   `--no-restrict-metadata-outside-schemas`: Disable metadata restriction
-   `--restrict-file-uploads-to-extensions`: Set allowed file extensions (e.g., ".pdf,.docx,.jpg")
-   `--clear-file-extensions`: Clear file extension restrictions
-   `--json-input`: JSON input file path or JSON string with update data
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Update description
vamscli database update -d my-database --description "Updated description"

# Update default bucket
vamscli database update -d my-database --default-bucket-id "new-bucket-uuid"

# Enable metadata restriction
vamscli database update -d my-database --restrict-metadata-outside-schemas

# Disable metadata restriction
vamscli database update -d my-database --no-restrict-metadata-outside-schemas

# Set file extension restrictions
vamscli database update -d my-database --restrict-file-uploads-to-extensions ".pdf,.png"

# Clear file extension restrictions
vamscli database update -d my-database --clear-file-extensions

# Update multiple fields
vamscli database update -d my-database --description "New desc" --default-bucket-id "new-bucket" --restrict-metadata-outside-schemas

# Update only configuration fields
vamscli database update -d my-database --restrict-metadata-outside-schemas --restrict-file-uploads-to-extensions ".pdf,.docx"

# Update with JSON input
vamscli database update -d my-database --json-input '{"description":"Updated","restrictMetadataOutsideSchemas":true}'
```

**JSON Input Format:**

```json
{
    "databaseId": "my-database",
    "description": "Updated description",
    "defaultBucketId": "550e8400-e29b-41d4-a716-446655440000",
    "restrictMetadataOutsideSchemas": false,
    "restrictFileUploadsToExtensions": ".pdf,.docx"
}
```

**Configuration Options:**

-   **Metadata Restriction**: Control whether metadata can be added outside defined schemas
    -   Use `--restrict-metadata-outside-schemas` to enable
    -   Use `--no-restrict-metadata-outside-schemas` to disable
    -   Cannot use both flags simultaneously
-   **File Extension Restriction**: Control which file types can be uploaded
    -   Use `--restrict-file-uploads-to-extensions` to set allowed extensions
    -   Use `--clear-file-extensions` to remove restrictions
    -   Cannot use both flags simultaneously
    -   Format: comma-separated list with dots (e.g., ".pdf,.docx,.jpg")

**Update Requirements:**

-   At least one field must be provided for update
-   Can update traditional fields (description, bucket) or new configuration fields independently
-   All fields are optional - update only what you need

### `vamscli database get`

Get details for a specific database.

**Required Options:**

-   `-d, --database-id`: Database ID to retrieve (required)

**Options:**

-   `--show-deleted`: Include deleted databases in search
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Get database details
vamscli database get -d my-database

# Include deleted databases
vamscli database get -d my-database --show-deleted

# JSON output for automation
vamscli database get -d my-database --json-output
```

**Output includes:**

-   Database ID and description
-   Creation date and asset count
-   Default bucket information
-   S3 bucket name and base prefix
-   Metadata restriction status (True/False)
-   File upload extension restrictions (comma-separated list or "(none)")

**Example Output:**

```
Database Details:
  ID: my-database
  Description: My Database
  Date Created: 2024-01-01T00:00:00Z
  Asset Count: 5
  Default Bucket ID: 550e8400-e29b-41d4-a716-446655440000
  Bucket Name: my-bucket
  Base Assets Prefix: assets/
  Restrict Metadata Outside Schemas: True
  Restrict File Uploads To Extensions: .pdf,.docx,.jpg
```

### `vamscli database delete`

Delete a database from VAMS.

⚠️ **WARNING: This action will delete the database!** ⚠️

**Required Options:**

-   `-d, --database-id`: Database ID to delete (required)
-   `--confirm`: Confirm database deletion (required for safety)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Delete database (requires confirmation)
vamscli database delete -d my-database --confirm

# Delete with JSON output
vamscli database delete -d my-database --confirm --json-output
```

**Safety Features:**

-   Requires explicit `--confirm` flag
-   Interactive confirmation prompt
-   Clear warnings about deletion
-   Prevents deletion if database contains active resources

**Prerequisites:**
The database must not contain any:

-   Active assets
-   Active workflows
-   Active pipelines

## Bucket Management Commands

### `vamscli database list-buckets`

List available S3 bucket configurations for use with databases.

**Options:**

-   `--page-size`: Number of items per page
-   `--max-items`: Maximum total items to fetch (only with --auto-paginate, default: 10000)
-   `--starting-token`: Token for pagination (manual pagination)
-   `--auto-paginate`: Automatically fetch all items
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Basic listing (uses API defaults)
vamscli database list-buckets

# Auto-pagination to fetch all items (default: up to 10,000)
vamscli database list-buckets --auto-paginate

# Auto-pagination with custom limit
vamscli database list-buckets --auto-paginate --max-items 5000

# Manual pagination with page size
vamscli database list-buckets --page-size 200
vamscli database list-buckets --starting-token "token123" --page-size 200

# JSON output for automation
vamscli database list-buckets --json-output
```

**Pagination Features:**

-   **Auto-Pagination**: Use `--auto-paginate` to automatically fetch all items up to the limit
-   **Manual Pagination**: Use `--page-size` and `--starting-token` for manual page-by-page control
-   **CLI-Side Limit**: `--max-items` is a CLI-side limit (not passed to API) that controls total items fetched
-   **API-Side Control**: `--page-size` is passed to the API to control items per request
-   **Default Limit**: Auto-pagination defaults to 10,000 items maximum
-   **Progress Display**: Shows progress during auto-pagination in CLI mode

**Pagination Restrictions:**

-   Cannot use `--auto-paginate` with `--starting-token` (choose one pagination mode)
-   `--max-items` only applies with `--auto-paginate` (warning shown if used without it)
-   When using manual pagination, use the `NextToken` from the response as `--starting-token` for the next page

**Output includes:**

-   Bucket ID (UUID)
-   Bucket name (S3 bucket name)
-   Base assets prefix (path within bucket)

## Database Management Features

### Database Creation

-   **Bucket Integration**: Automatic bucket selection with interactive prompts
-   **Validation**: Database ID format validation and uniqueness checking
-   **Bucket Verification**: Validates bucket existence before database creation

### Database Updates

-   **Flexible Updates**: Update description, bucket configuration, or both
-   **Validation**: Ensures bucket exists before updating configuration
-   **Atomic Operations**: Updates are applied atomically

### Database Deletion

-   **Safety Checks**: Prevents deletion of databases with active resources
-   **Confirmation Required**: Multiple confirmation steps for safety
-   **Dependency Validation**: Checks for assets, workflows, and pipelines

### Bucket Management

-   **Configuration Listing**: View all available S3 bucket configurations
-   **Pagination Support**: Handle large numbers of bucket configurations
-   **Integration**: Seamless integration with database creation/updates

## Database Management Workflow Examples

### Basic Database Operations

```bash
# List available buckets first
vamscli database list-buckets

# Create a new database (will prompt for bucket selection)
vamscli database create -d my-new-database --description "My New Database"

# Or create with specific bucket
vamscli database create -d my-new-database --description "My New Database" --default-bucket-id "bucket-uuid"

# List all databases
vamscli database list

# Get details for specific database
vamscli database get -d my-new-database

# Update database description
vamscli database update -d my-new-database --description "Updated description"

# Update default bucket
vamscli database update -d my-new-database --default-bucket-id "new-bucket-uuid"
```

### Database Lifecycle Management

```bash
# Create database for different environments
vamscli database create -d production-db --description "Production Database" --default-bucket-id "prod-bucket"
vamscli database create -d staging-db --description "Staging Database" --default-bucket-id "staging-bucket"
vamscli database create -d development-db --description "Development Database" --default-bucket-id "dev-bucket"

# List all databases to verify
vamscli database list

# Update database configurations as needed
vamscli database update -d production-db --description "Production Database - Updated"

# When database is no longer needed (ensure it's empty first)
vamscli database delete -d development-db --confirm
```

### Automation with JSON

```bash
# Create database configuration file
cat > database-config.json << EOF
{
  "databaseId": "automated-db",
  "description": "Automated Database Creation",
  "defaultBucketId": "550e8400-e29b-41d4-a716-446655440000"
}
EOF

# Create database using JSON config
vamscli database create -d automated-db --json-input @database-config.json --json-output

# Update database using JSON
vamscli database update -d automated-db --json-input '{"description":"Updated via automation"}' --json-output

# Get database info for verification
vamscli database get -d automated-db --json-output
```

### Multi-Environment Database Setup

```bash
# List available buckets to see options
vamscli database list-buckets

# Create databases for different environments with appropriate buckets
vamscli database create -d prod-assets --description "Production Assets Database" --default-bucket-id "prod-bucket-id"
vamscli database create -d staging-assets --description "Staging Assets Database" --default-bucket-id "staging-bucket-id"
vamscli database create -d dev-assets --description "Development Assets Database" --default-bucket-id "dev-bucket-id"

# Verify all databases were created
vamscli database list

# Get details for each database to confirm configuration
vamscli database get -d prod-assets
vamscli database get -d staging-assets
vamscli database get -d dev-assets
```

### Database Maintenance Workflow

```bash
# Regular maintenance: list all databases and check status
vamscli database list

# Check specific database details
vamscli database get -d my-database

# Update database description for better organization
vamscli database update -d my-database --description "Updated: Production 3D Models Database"

# If bucket configuration needs to change
vamscli database list-buckets  # Check available buckets
vamscli database update -d my-database --default-bucket-id "new-bucket-id"

# For databases that are no longer needed:
# 1. First ensure database is empty (no assets, workflows, pipelines)
# 2. Then delete
vamscli database delete -d old-database --confirm
```

### Bucket Configuration Management

```bash
# List all available bucket configurations
vamscli database list-buckets

# Create databases using different bucket configurations
vamscli database create -d models-db --description "3D Models Database" --default-bucket-id "models-bucket-id"
vamscli database create -d textures-db --description "Textures Database" --default-bucket-id "textures-bucket-id"
vamscli database create -d archives-db --description "Archive Database" --default-bucket-id "archive-bucket-id"

# Verify bucket assignments
vamscli database get -d models-db
vamscli database get -d textures-db
vamscli database get -d archives-db

# Update bucket assignments if needed
vamscli database update -d archives-db --default-bucket-id "new-archive-bucket-id"
```

### Database Migration Preparation

```bash
# Before migrating databases, document current configuration
vamscli database list --json-output > current-databases.json

# Get detailed information for each database
vamscli database get -d database1 --json-output > database1-config.json
vamscli database get -d database2 --json-output > database2-config.json

# List bucket configurations for reference
vamscli database list-buckets --json-output > bucket-configs.json

# After migration, recreate databases using saved configurations
# (This would be part of a larger migration script)
```

### Database Configuration Management

```bash
# Create a database with strict metadata schema enforcement
vamscli database create -d schema-enforced-db \
  --description "Schema-Enforced Database" \
  --default-bucket-id "bucket-uuid" \
  --restrict-metadata-outside-schemas

# Create a database that only accepts specific file types
vamscli database create -d documents-db \
  --description "Documents Database" \
  --default-bucket-id "bucket-uuid" \
  --restrict-file-uploads-to-extensions ".pdf,.docx,.xlsx"

# Create a database with both restrictions for compliance
vamscli database create -d compliance-db \
  --description "Compliance Database" \
  --default-bucket-id "bucket-uuid" \
  --restrict-metadata-outside-schemas \
  --restrict-file-uploads-to-extensions ".pdf,.docx"

# Update existing database to add restrictions
vamscli database update -d existing-db --restrict-metadata-outside-schemas
vamscli database update -d existing-db --restrict-file-uploads-to-extensions ".jpg,.png,.gif"

# Remove restrictions from a database
vamscli database update -d existing-db --no-restrict-metadata-outside-schemas
vamscli database update -d existing-db --clear-file-extensions

# Verify configuration changes
vamscli database get -d existing-db
```

### Use Cases for Database Restrictions

**Metadata Schema Enforcement:**

```bash
# For databases requiring strict metadata compliance
vamscli database create -d regulated-assets \
  --description "Regulated Assets Database" \
  --default-bucket-id "bucket-uuid" \
  --restrict-metadata-outside-schemas

# This ensures all metadata follows predefined schemas
# Useful for: compliance, data governance, standardization
```

**File Type Control:**

```bash
# For databases with specific file type requirements
vamscli database create -d cad-models \
  --description "CAD Models Database" \
  --default-bucket-id "bucket-uuid" \
  --restrict-file-uploads-to-extensions ".step,.stp,.iges,.igs"

# For document management databases
vamscli database create -d documents \
  --description "Documents Database" \
  --default-bucket-id "bucket-uuid" \
  --restrict-file-uploads-to-extensions ".pdf,.docx,.xlsx,.pptx"

# For image databases
vamscli database create -d images \
  --description "Images Database" \
  --default-bucket-id "bucket-uuid" \
  --restrict-file-uploads-to-extensions ".jpg,.jpeg,.png,.gif,.svg"
```

**Combined Restrictions:**

```bash
# For highly controlled databases
vamscli database create -d secure-assets \
  --description "Secure Assets Database" \
  --default-bucket-id "bucket-uuid" \
  --restrict-metadata-outside-schemas \
  --restrict-file-uploads-to-extensions ".pdf,.docx"

# This provides both metadata and file type control
# Useful for: security, compliance, data quality
```

## Database Administration Best Practices

### Database Naming Conventions

-   Use lowercase letters, numbers, hyphens, and underscores only
-   Choose descriptive names that indicate purpose (e.g., `prod-3d-models`, `staging-textures`)
-   Include environment indicators when managing multiple environments

### Bucket Management

-   Review available bucket configurations before creating databases
-   Choose appropriate buckets based on data type and access patterns
-   Consider geographic location and compliance requirements

### Database Lifecycle

-   Create databases with clear descriptions for easy identification
-   Regularly review database usage and clean up unused databases
-   Ensure databases are empty before deletion
-   Document database purposes and configurations

### Security Considerations

-   Verify bucket permissions and access controls
-   Use appropriate bucket configurations for sensitive data
-   Regularly audit database access and usage patterns
-   Follow organizational data governance policies

### Automation and Monitoring

-   Use JSON input/output for automated database management
-   Implement monitoring for database creation and deletion
-   Create scripts for consistent database setup across environments
-   Document database configurations for disaster recovery
