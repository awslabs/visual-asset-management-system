# VamsCLI - Visual Asset Management System Command Line Interface

VamsCLI is a command-line interface for the Visual Asset Management System (VAMS), providing easy access to your VAMS deployment running on AWS. It supports authentication, configuration management, and comprehensive asset and file operations through a simple and intuitive command-line interface.

## Features

-   **Easy Setup**: Simple configuration with any VAMS base URL (CloudFront, ALB, API Gateway, or custom domain)
-   **Secure Authentication**: AWS Cognito integration with MFA support and override token system
-   **Feature Switches**: Automatic detection and management of backend-controlled feature flags
-   **Comprehensive Logging**: Automatic error logging to file with verbose mode for detailed output
-   **Asset Management**: Create, update, and manage assets with comprehensive metadata support
-   **File Upload System**: Advanced file upload with chunking, progress monitoring, and retry logic
-   **Version Checking**: Automatic version compatibility checking
-   **Cross-Platform**: Works on Windows, macOS, and Linux
-   **Profile Management**: Secure credential storage and management
-   **JSON Support**: Full JSON input/output for automation and scripting

## Quick Installation

```bash
git clone https://github.com/awslabs/visual-asset-management-system.git
cd visual-asset-management-system/tools/VamsCLI
pip install .
```

For detailed installation options, see the [Installation Guide](docs/INSTALLATION.md).

## Quick Start

### 1. Setup

Configure VamsCLI with your VAMS base URL (supports any deployment type):

```bash
# Setup with CloudFront distribution
vamscli setup https://d1234567890.cloudfront.net

# Setup with custom domain
vamscli setup https://vams.mycompany.com

# Setup with ALB
vamscli setup https://my-alb-123456789.us-west-2.elb.amazonaws.com

# Setup with API Gateway directly
vamscli setup https://abc123.execute-api.us-west-2.amazonaws.com

# Setup specific profiles for different environments
vamscli --profile production setup https://prod-vams.example.com
vamscli --profile staging setup https://staging-vams.example.com
```

### 2. Authentication

Authenticate with your VAMS system:

```bash
# Authenticate with default profile
vamscli auth login -u your-username@example.com

# Authenticate with specific profiles
vamscli --profile production auth login -u your-username@example.com
vamscli --profile staging auth login -u your-username@example.com
```

### 3. Basic Usage

```bash
# Check authentication status (now includes feature switches info)
vamscli auth status

# Check available feature switches
vamscli features list

# Check if specific features are enabled
vamscli features check GOVCLOUD
vamscli features check LOCATIONSERVICES

# Search for assets and files (requires OpenSearch)
vamscli search assets -q "training model" -d my-database
vamscli search files --file-ext "gltf" --asset-type "3d-model"
vamscli search mapping  # View available search fields

# Create tag types for organization
vamscli tag-type create --tag-type-name "priority" --description "Priority levels" --required
vamscli tag-type create --tag-type-name "category" --description "Asset categories"

# Create tags
vamscli tag create --tag-name "urgent" --description "Urgent priority" --tag-type-name "priority"
vamscli tag create --tag-name "model" --description "3D models" --tag-type-name "category"

# Manage roles
vamscli role list --auto-paginate
vamscli role create -r admin --description "Administrator role" --mfa-required
vamscli role update -r admin --description "Updated description"
vamscli role delete -r old-role --confirm

# Manage user role assignments
vamscli role user list --auto-paginate
vamscli role user create -u user@example.com --role-name admin --role-name viewer
vamscli role user update -u user@example.com --role-name admin --role-name editor
vamscli role user delete -u user@example.com --confirm

# Manage role constraints for fine-grained access control
vamscli role constraint list --auto-paginate
vamscli role constraint get -c my-constraint
vamscli role constraint create -c db-access --json-input constraint.json
vamscli role constraint update -c db-access --name "Updated Name"
vamscli role constraint delete -c old-constraint --confirm

# Import constraints from a permission template
# See documentation/PermissionsGuide.md for template details and constraint design
vamscli role constraint template import -j ./documentation/permissionsTemplates/database-admin.json
vamscli role constraint template import -j ./my-template.json --json-output

# Manage Cognito users (requires Cognito enabled)
vamscli user cognito list --auto-paginate
vamscli user cognito create -u user@example.com -e user@example.com -p +12345678900
vamscli user cognito update -u user@example.com -e newemail@example.com
vamscli user cognito reset-password -u user@example.com --confirm
vamscli user cognito delete -u user@example.com --confirm

# Create an asset with tags
vamscli assets create -d my-database --name "My Asset" --description "Asset description" --tags urgent --tags model

# Upload files to an asset
vamscli file upload -d my-database -a my-asset /path/to/file.gltf

# Execute workflows on assets
vamscli workflow list -d my-database
vamscli workflow execute -d my-database -a my-asset -w workflow-123 --workflow-database-id global
vamscli workflow list-executions -d my-database -a my-asset

# Download files from an asset
vamscli assets download /local/path -d my-database -a my-asset

# Download specific files or get shareable links
vamscli assets download /local/path -d my-database -a my-asset --file-key "/model.gltf"
vamscli assets download -d my-database -a my-asset --shareable-links-only

# Export comprehensive asset data (new in v2.2+, auto-pagination enabled by default)
vamscli assets export -d my-database -a my-asset --json-output > export.json
vamscli assets export -d my-database -a my-asset --file-extensions .gltf --generate-presigned-urls
vamscli assets export -d my-database -a my-asset --no-fetch-relationships  # Single asset only
vamscli assets export -d my-database -a my-asset --fetch-entire-subtrees  # Full tree
vamscli assets export -d my-database -a my-asset --no-auto-paginate --max-assets 100  # Manual pagination

# Create asset versions for tracking changes
vamscli asset-version create -d my-database -a my-asset --comment "Initial version"

# Upload updated files and create new version
vamscli file upload -d my-database -a my-asset /path/to/updated-file.gltf
vamscli asset-version create -d my-database -a my-asset --comment "Updated model with fixes"

# List all versions with pagination support
vamscli asset-version list -d my-database -a my-asset
vamscli asset-version list -d my-database -a my-asset --auto-paginate  # Fetch all versions
vamscli asset-version list -d my-database -a my-asset --page-size 200  # Manual pagination

# Get version details
vamscli asset-version get -d my-database -a my-asset -v 1

# Revert to previous version if needed
vamscli asset-version revert -d my-database -a my-asset -v 1 --comment "Reverting to stable version"

# Check metadata schema for validation rules
vamscli metadata-schema get -d my-database

# Add metadata to assets (unified v2.2+ API with bulk operations)
vamscli metadata asset update -d my-database -a my-asset --json-input '[
  {"metadataKey": "title", "metadataValue": "My 3D Model", "metadataValueType": "string"},
  {"metadataKey": "category", "metadataValue": "architecture", "metadataValueType": "string"},
  {"metadataKey": "properties", "metadataValue": "{\"polygons\": 50000}", "metadataValueType": "object"}
]'

# Add metadata to files
vamscli metadata file update -d my-database -a my-asset -f file-uuid --json-input '[
  {"metadataKey": "lod_level", "metadataValue": "high", "metadataValueType": "string"},
  {"metadataKey": "optimized", "metadataValue": "true", "metadataValueType": "boolean"}
]'

# List metadata
vamscli metadata asset list -d my-database -a my-asset
vamscli metadata file list -d my-database -a my-asset -f file-uuid

# Update metadata (upsert mode - keeps existing keys)
vamscli metadata asset update -d my-database -a my-asset --json-input '[
  {"metadataKey": "title", "metadataValue": "Updated 3D Model", "metadataValueType": "string"},
  {"metadataKey": "version", "metadataValue": "2", "metadataValueType": "number"}
]'

# Replace all metadata (replace mode - removes unlisted keys)
vamscli metadata asset update -d my-database -a my-asset --update-type replace_all --json-input '[
  {"metadataKey": "title", "metadataValue": "New Asset", "metadataValueType": "string"}
]'

# Delete specific metadata keys
vamscli metadata asset delete -d my-database -a my-asset --json-input '["old_field", "deprecated"]'

# Add metadata to asset links
vamscli metadata asset-link update --asset-link-id link-uuid --json-input '[
  {"metadataKey": "relationship_type", "metadataValue": "parent-child", "metadataValueType": "string"}
]'

# Add metadata to databases
vamscli metadata database update -d my-database --json-input '[
  {"metadataKey": "project", "metadataValue": "Downtown Complex", "metadataValueType": "string"}
]'

# Create relationships between assets
vamscli asset-links create --from-asset-id my-asset --from-database-id my-database --to-asset-id related-asset --to-database-id my-database --relationship-type related --tags "related-files"

# Create parent-child hierarchy
vamscli asset-links create --from-asset-id my-asset --from-database-id my-database --to-asset-id child-asset --to-database-id my-database --relationship-type parentChild --tags "lod-hierarchy"

# List all asset links for an asset
vamscli asset-links list -d my-database --asset-id my-asset --tree-view

# List tags and tag types
vamscli tag list --tag-type priority
vamscli tag-type list --show-tags

# Archive an asset (soft delete)
vamscli assets archive my-asset -d my-database --reason "No longer needed"

# Permanently delete an asset (requires confirmation)
vamscli assets delete my-asset -d my-database --confirm --reason "Project cancelled"

# Combine multiple GLBs from asset hierarchy into single GLB (industry spatial)
vamscli industry spatial glbassetcombine -d my-database -a root-asset-id
vamscli industry spatial glbassetcombine -d my-database -a root-asset-id --asset-create-name "Combined Model"

# Import PLM XML files with parallel processing (industry engineering)
vamscli industry engineering plm plmxml import -d my-database --plmxml-dir /path/to/plmxml
vamscli industry engineering plm plmxml import -d my-database --plmxml-dir /path/to/plmxml --upload-xml
vamscli industry engineering plm plmxml import -d my-database --plmxml-dir /path/to/plmxml --upload-xml --max-workers 20

# Get help for any command
vamscli --help
vamscli tag --help
vamscli tag-type --help
vamscli industry spatial --help
vamscli industry engineering plm --help
```

## Available Commands

VamsCLI provides eighteen main command groups:

-   **`vamscli setup`** - Configure CLI with API Gateway URL
-   **`vamscli auth`** - Authentication and session management
-   **`vamscli features`** - Feature switches management and checking
-   **`vamscli search`** - Search assets and files using OpenSearch (requires OpenSearch enabled)
-   **`vamscli assets`** - Asset creation, updates, management, and comprehensive data export
-   **`vamscli asset-version`** - Asset version management and tracking
-   **`vamscli asset-links`** - Asset relationship management and linking
-   **`vamscli file`** - File upload and management operations
-   **`vamscli workflow`** - Workflow execution and monitoring
-   **`vamscli metadata`** - Metadata management for assets and files
-   **`vamscli metadata-schema`** - Metadata schema management and validation rules
-   **`vamscli database`** - Database creation and management
-   **`vamscli tag`** - Tag creation and management for asset organization
-   **`vamscli tag-type`** - Tag type creation and management
-   **`vamscli role`** - Role creation and management for access control
-   **`vamscli user`** - User management for Cognito (requires Cognito enabled)
-   **`vamscli industry`** - Industry-specific commands (spatial data processing, PLM import)
-   **`vamscli profile`** - Profile management for multiple environments

For complete command documentation, see the [Commands Documentation](docs/commands/) directory.

## Multi-Profile Support

VamsCLI supports multiple profiles to manage different VAMS environments or user accounts on the same machine:

```bash
# Setup different environments with flexible URLs
vamscli --profile production setup https://prod-vams.example.com
vamscli --profile staging setup https://staging-vams.example.com

# Authenticate to each environment
vamscli auth login -u user@example.com --profile production
vamscli auth login -u user@example.com --profile staging

# Use different profiles
vamscli assets list --profile production
vamscli file upload -d my-db -a my-asset file.gltf --profile staging

# Manage profiles
vamscli profile list
vamscli profile switch production
```

**Profile Features:**

-   **Complete Isolation**: Each profile has separate configuration and authentication
-   **Automatic Migration**: Existing installations automatically migrate to "default" profile
-   **Active Profile Tracking**: Remembers last used profile across sessions
-   **Backward Compatibility**: All existing commands work without changes

## Documentation

VamsCLI provides comprehensive documentation organized by functional area:

### Command Documentation

-   **[Setup and Authentication](docs/commands/setup-auth.md)** - Setup, authentication, and profile management
-   **[Search Operations](docs/commands/search-operations.md)** - Search assets and files using OpenSearch
-   **[Asset Management](docs/commands/asset-management.md)** - Asset operations, versioning, and relationships
-   **[File Operations](docs/commands/file-operations.md)** - File upload, organization, and management
-   **[Workflow Management](docs/commands/workflow-management.md)** - Workflow execution and monitoring
-   **[Metadata Management](docs/commands/metadata-management.md)** - Metadata operations for assets and files
-   **[Database Administration](docs/commands/database-admin.md)** - Database and bucket management
-   **[Tag Management](docs/commands/tag-management.md)** - Tag and tag type operations
-   **[Role Management](docs/commands/role-management.md)** - Role creation and management for access control
-   **[User Management](docs/commands/user-management.md)** - Cognito user management (requires Cognito enabled)
-   **[Industry Spatial](docs/commands/industry-spatial.md)** - Spatial data processing (GLB combining)
-   **[PLM Commands](docs/commands/plm-commands.md)** - Product Lifecycle Management (PLM XML import)
-   **[Global Options](docs/commands/global-options.md)** - JSON patterns, automation, and best practices

### Troubleshooting Documentation

-   **[Setup and Authentication Issues](docs/troubleshooting/setup-auth-issues.md)** - Setup, authentication, and profile problems
-   **[Search Issues](docs/troubleshooting/search-issues.md)** - Search functionality and OpenSearch problems
-   **[Asset and File Issues](docs/troubleshooting/asset-file-issues.md)** - Asset and file operation problems
-   **[Database and Tag Issues](docs/troubleshooting/database-tag-issues.md)** - Database and tag management problems
-   **[Role Management Issues](docs/troubleshooting/role-issues.md)** - Role management problems and solutions
-   **[User Management Issues](docs/troubleshooting/user-issues.md)** - Cognito user management problems
-   **[Network and Configuration Issues](docs/troubleshooting/network-config-issues.md)** - Network, SSL, and proxy issues
-   **[General Troubleshooting](docs/troubleshooting/general-troubleshooting.md)** - Debug mode, performance, and recovery

### Additional Documentation

-   **[Installation Guide](docs/INSTALLATION.md)** - Detailed installation methods and setup
-   **[Authentication Guide](docs/AUTHENTICATION.md)** - Authentication system details
-   **[Development Guide](docs/DEVELOPMENT.md)** - Development and contribution guidelines

## Logging and Verbose Mode

VamsCLI provides comprehensive logging and debugging capabilities:

### Automatic Error Logging

**All errors and warnings are automatically logged to file:**

-   **Windows**: `%APPDATA%\vamscli\logs\vamscli.log`
-   **macOS**: `~/Library/Application Support/vamscli/logs/vamscli.log`
-   **Linux**: `~/.config/vamscli/logs/vamscli.log`

**What Gets Logged:**

-   All command invocations with timing
-   All exceptions with full stack traces
-   All API requests and responses
-   All warnings
-   Configuration details

**Log Rotation**: Automatic rotation at 10MB with 5 backup files.

### Verbose Mode

For detailed console output, use the `--verbose` flag:

```bash
# Run any command with verbose output
vamscli --verbose assets list
vamscli --verbose auth login -u user@example.com
vamscli --verbose --profile production database list
```

**Verbose Mode Shows:**

-   Configuration details (profile, API gateway, CLI version)
-   API request/response details with timing
-   Full stack traces for errors
-   Command execution duration
-   Warning messages

**Example:**

```bash
$ vamscli --verbose assets get my-db my-asset

📋 Using profile: default
📋 API Gateway: https://api.example.com
📋 CLI Version: 2.2.0

→ API Request: GET /database/my-db/assets/my-asset
← API Response: 200 (0.23s)

Asset Details: ...

✓ Command completed successfully in 0.25s
```

For more details, see the [Global Options Documentation](docs/commands/global-options.md) and [General Troubleshooting Guide](docs/troubleshooting/general-troubleshooting.md).

## Global Options

VamsCLI supports global options for enhanced functionality:

-   **`--version`** - Show version information
-   **`--verbose`** - Enable verbose output with detailed error information, API requests/responses, and timing
-   **`--profile`** - Profile name to use for the command

## Environment Variables

VamsCLI supports several environment variables for customizing behavior:

### Retry Configuration for Rate Limiting

VamsCLI automatically handles API rate limiting (HTTP 429 errors) with exponential backoff. You can customize the retry behavior:

| Variable                            | Default | Description                                            |
| ----------------------------------- | ------- | ------------------------------------------------------ |
| `VAMS_CLI_MAX_RETRY_ATTEMPTS`       | 5       | Maximum number of retry attempts for 429 errors        |
| `VAMS_CLI_INITIAL_RETRY_DELAY`      | 1.0     | Initial delay in seconds before first retry            |
| `VAMS_CLI_MAX_RETRY_DELAY`          | 60.0    | Maximum delay in seconds between retries               |
| `VAMS_CLI_RETRY_BACKOFF_MULTIPLIER` | 2.0     | Multiplier for exponential backoff (1.0-5.0)           |
| `VAMS_CLI_RETRY_JITTER`             | 0.1     | Jitter percentage to prevent thundering herd (0.0-0.5) |

**Example Usage:**

```bash
# For high-traffic environments, be more patient with retries
export VAMS_CLI_MAX_RETRY_ATTEMPTS=8
export VAMS_CLI_INITIAL_RETRY_DELAY=2.0
export VAMS_CLI_MAX_RETRY_DELAY=180.0

# For development environments, fail faster
export VAMS_CLI_MAX_RETRY_ATTEMPTS=3
export VAMS_CLI_INITIAL_RETRY_DELAY=0.5
export VAMS_CLI_MAX_RETRY_DELAY=30.0

# Run commands with custom retry settings
vamscli assets list
```

**Retry Behavior:**

-   **Automatic Handling**: All API calls automatically retry on 429 errors
-   **Exponential Backoff**: Delays increase exponentially with each retry
-   **Server Respect**: Honors server-provided `Retry-After` headers
-   **Progress Indication**: Shows retry progress for delays longer than 1 second
-   **Jitter**: Adds randomness to prevent multiple clients retrying simultaneously

## Token Override Authentication

For external authentication systems, use the token override options with the `auth login` command:

```bash
# Token override authentication (moved to auth login command)
vamscli auth login --user-id john.doe@example.com --token-override "your_token"
vamscli auth login --user-id john.doe@example.com --token-override "your_token" --expires-at "+3600"
```

Token override options are now part of the `auth login` command for better organization:

-   **`--user-id`** - User ID for token override authentication (required with --token-override)
-   **`--token-override`** - Override token for external authentication
-   **`--expires-at`** - Token expiration time (Unix timestamp, ISO 8601, or +seconds)

## JSON Support

All commands support JSON input and output for automation:

```bash
# JSON input from string
vamscli assets create --json-input '{"databaseId": "my-db", "assetName": "My Asset"}'

# JSON input from file
vamscli assets create --json-input @asset-data.json

# JSON output for automation
vamscli assets list --json-output
```

## Automation and Integration

VamsCLI is designed for automation with features like:

-   **JSON Input/Output**: All commands support JSON for scripting
-   **Exit Codes**: Standard exit codes for script integration
-   **Bulk Operations**: Optimized for large-scale operations
-   **CI/CD Ready**: Perfect for continuous integration workflows
-   **Multi-Profile Support**: Manage multiple environments seamlessly

## Support

For support and questions:

1. Check the [Command Documentation](docs/commands/) and [Troubleshooting Guides](docs/troubleshooting/)
2. Search existing [issues](https://github.com/awslabs/visual-asset-management-system/issues)
3. Create a new issue if needed

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](../../LICENSE) file for details.

## Contributing

We welcome contributions!!! Please see the [Development Guide](docs/DEVELOPMENT.md) for details on how to contribute to this project.
