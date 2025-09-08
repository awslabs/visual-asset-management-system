# VamsCLI - Visual Asset Management System Command Line Interface

VamsCLI is a command-line interface for the Visual Asset Management System (VAMS), providing easy access to your VAMS deployment running on AWS. It supports authentication, configuration management, and comprehensive asset and file operations through a simple and intuitive command-line interface.

## Features

-   **Easy Setup**: Simple configuration with your API Gateway URL
-   **Secure Authentication**: AWS Cognito integration with MFA support and override token system
-   **Feature Switches**: Automatic detection and management of backend-controlled feature flags
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

Configure VamsCLI with your VAMS API Gateway URL:

```bash
# Setup default profile
vamscli setup https://your-api-gateway-url.execute-api.region.amazonaws.com

# Setup specific profiles for different environments
vamscli --profile production setup https://prod-api.example.com
vamscli --profile staging setup https://staging-api.example.com
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

# Create an asset with tags
vamscli assets create -d my-database --name "My Asset" --description "Asset description" --tags urgent --tags model

# Upload files to an asset
vamscli file upload -d my-database -a my-asset /path/to/file.gltf

# Download files from an asset
vamscli assets download /local/path -d my-database -a my-asset

# Download specific files or get shareable links
vamscli assets download /local/path -d my-database -a my-asset --file-key "/model.gltf"
vamscli assets download -d my-database -a my-asset --shareable-links-only

# Create asset versions for tracking changes
vamscli asset-version create -d my-database -a my-asset --comment "Initial version"

# Upload updated files and create new version
vamscli file upload -d my-database -a my-asset /path/to/updated-file.gltf
vamscli asset-version create -d my-database -a my-asset --comment "Updated model with fixes"

# List all versions and get version details
vamscli asset-version list -d my-database -a my-asset
vamscli asset-version get -d my-database -a my-asset -v 1

# Revert to previous version if needed
vamscli asset-version revert -d my-database -a my-asset -v 1 --comment "Reverting to stable version"

# Check metadata schema for validation rules
vamscli metadata-schema get -d my-database

# Add metadata to assets and files
vamscli metadata create -d my-database -a my-asset --json-input '{"title": "My 3D Model", "category": "architecture", "properties": {"polygons": 50000}}'
vamscli metadata create -d my-database -a my-asset --file-path "/models/file.gltf" --json-input '{"lod_level": "high", "optimized": true}'

# Get metadata
vamscli metadata get -d my-database -a my-asset
vamscli metadata get -d my-database -a my-asset --file-path "/models/file.gltf"

# Update metadata
vamscli metadata update -d my-database -a my-asset --json-input '{"title": "Updated 3D Model", "version": 2}'

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

# Get help for any command
vamscli --help
vamscli tag --help
vamscli tag-type --help
```

## Available Commands

VamsCLI provides fourteen main command groups:

-   **`vamscli setup`** - Configure CLI with API Gateway URL
-   **`vamscli auth`** - Authentication and session management
-   **`vamscli features`** - Feature switches management and checking
-   **`vamscli search`** - Search assets and files using OpenSearch (requires OpenSearch enabled)
-   **`vamscli assets`** - Asset creation, updates, and management
-   **`vamscli asset-version`** - Asset version management and tracking
-   **`vamscli asset-links`** - Asset relationship management and linking
-   **`vamscli asset-links-metadata`** - Metadata management for asset links
-   **`vamscli file`** - File upload and management operations
-   **`vamscli metadata`** - Metadata management for assets and files
-   **`vamscli metadata-schema`** - Metadata schema management and validation rules
-   **`vamscli database`** - Database creation and management
-   **`vamscli tag`** - Tag creation and management for asset organization
-   **`vamscli tag-type`** - Tag type creation and management
-   **`vamscli profile`** - Profile management for multiple environments

For complete command documentation, see the [Commands Documentation](docs/commands/) directory.

## Multi-Profile Support

VamsCLI supports multiple profiles to manage different VAMS environments or user accounts on the same machine:

```bash
# Setup different environments
vamscli setup https://prod-api.example.com --profile production
vamscli setup https://staging-api.example.com --profile staging

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
-   **[Metadata Management](docs/commands/metadata-management.md)** - Metadata operations for assets and files
-   **[Database Administration](docs/commands/database-admin.md)** - Database and bucket management
-   **[Tag Management](docs/commands/tag-management.md)** - Tag and tag type operations
-   **[Global Options](docs/commands/global-options.md)** - JSON patterns, automation, and best practices

### Troubleshooting Documentation

-   **[Setup and Authentication Issues](docs/troubleshooting/setup-auth-issues.md)** - Setup, authentication, and profile problems
-   **[Search Issues](docs/troubleshooting/search-issues.md)** - Search functionality and OpenSearch problems
-   **[Asset and File Issues](docs/troubleshooting/asset-file-issues.md)** - Asset and file operation problems
-   **[Database and Tag Issues](docs/troubleshooting/database-tag-issues.md)** - Database and tag management problems
-   **[Network and Configuration Issues](docs/troubleshooting/network-config-issues.md)** - Network, SSL, and proxy issues
-   **[General Troubleshooting](docs/troubleshooting/general-troubleshooting.md)** - Debug mode, performance, and recovery

### Additional Documentation

-   **[Installation Guide](docs/INSTALLATION.md)** - Detailed installation methods and setup
-   **[Authentication Guide](docs/AUTHENTICATION.md)** - Authentication system details
-   **[Development Guide](docs/DEVELOPMENT.md)** - Development and contribution guidelines

## Global Options

VamsCLI supports global options for enhanced functionality:

-   **`--version`** - Show version information
-   **`--profile`** - Profile name to use for the command

## Environment Variables

VamsCLI supports several environment variables for customizing behavior:

### Retry Configuration for Rate Limiting

VamsCLI automatically handles API rate limiting (HTTP 429 errors) with exponential backoff. You can customize the retry behavior:

| Variable | Default | Description |
|----------|---------|-------------|
| `VAMS_CLI_MAX_RETRY_ATTEMPTS` | 5 | Maximum number of retry attempts for 429 errors |
| `VAMS_CLI_INITIAL_RETRY_DELAY` | 1.0 | Initial delay in seconds before first retry |
| `VAMS_CLI_MAX_RETRY_DELAY` | 60.0 | Maximum delay in seconds between retries |
| `VAMS_CLI_RETRY_BACKOFF_MULTIPLIER` | 2.0 | Multiplier for exponential backoff (1.0-5.0) |
| `VAMS_CLI_RETRY_JITTER` | 0.1 | Jitter percentage to prevent thundering herd (0.0-0.5) |

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

- **Automatic Handling**: All API calls automatically retry on 429 errors
- **Exponential Backoff**: Delays increase exponentially with each retry
- **Server Respect**: Honors server-provided `Retry-After` headers
- **Progress Indication**: Shows retry progress for delays longer than 1 second
- **Jitter**: Adds randomness to prevent multiple clients retrying simultaneously

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
