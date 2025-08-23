# Global Options and Usage Patterns

This document covers VamsCLI global options, JSON input/output patterns, error handling, and best practices.

## Global Options

VamsCLI supports global options that can be used with any command:

### `--version`

Show version information.

```bash
vamscli --version
```

### `--profile`

Profile name to use for the command (default: "default").

```bash
vamscli --profile production auth status
vamscli --profile staging assets list
```

## Token Override Authentication

Token override options are now part of the `auth login` command for better organization. See the [Setup and Authentication Guide](setup-auth.md) for complete details.

**Token Override Options (available in `auth login` command):**

-   `--user-id`: User ID for token override authentication (required with --token-override)
-   `--token-override`: Override token for external authentication
-   `--expires-at`: Token expiration time (Unix timestamp, ISO 8601, or +seconds)

**Examples:**

```bash
vamscli auth login --user-id john.doe@example.com --token-override "your_token"
vamscli auth login --user-id john.doe@example.com --token-override "token123" --expires-at "+3600"
```

## JSON Input/Output Patterns

All commands support JSON input and output for automation and scripting.

### JSON Input

**From String:**

```bash
vamscli assets create --json-input '{"databaseId": "my-db", "assetName": "My Asset"}'
```

**From File:**

```bash
vamscli assets create --json-input @asset-data.json
```

**Example JSON Input File (asset-data.json):**

```json
{
    "databaseId": "my-database",
    "assetId": "my-asset",
    "assetName": "My Asset",
    "description": "Asset description",
    "tags": ["tag1", "tag2"],
    "isDistributable": true
}
```

### JSON Output

Add `--json-output` to any command to get machine-readable JSON response:

```bash
vamscli assets get my-asset -d my-database --json-output
```

### JSON Input/Output Examples

**Asset Creation with JSON:**

```bash
# Create asset-config.json
cat > asset-config.json << EOF
{
  "databaseId": "my-database",
  "assetName": "Automated Asset",
  "description": "Created via automation",
  "tags": ["automated", "batch-created"],
  "isDistributable": true
}
EOF

# Create asset using JSON config
vamscli assets create --json-input @asset-config.json --json-output
```

**File Upload with JSON:**

```bash
# Create upload-config.json
cat > upload-config.json << EOF
{
  "database_id": "my-db",
  "asset_id": "my-asset",
  "files": ["/path/to/model.gltf", "/path/to/texture.png"],
  "asset_location": "/models/",
  "parallel_uploads": 5,
  "hide_progress": true
}
EOF

# Upload files using JSON config
vamscli file upload --json-input @upload-config.json --json-output
```

**Database Management with JSON:**

```bash
# Create database-config.json
cat > database-config.json << EOF
{
  "databaseId": "automated-db",
  "description": "Automated Database Creation",
  "defaultBucketId": "550e8400-e29b-41d4-a716-446655440000"
}
EOF

# Create database using JSON config
vamscli database create --json-input @database-config.json --json-output
```

## Command Help System

Every command and subcommand provides detailed help:

```bash
# Main help
vamscli --help

# Command group help
vamscli auth --help
vamscli assets --help
vamscli file --help

# Specific command help
vamscli auth login --help
vamscli assets create --help
vamscli file upload --help
```

## Common Usage Patterns

### Basic Workflow

```bash
# 1. Setup
vamscli setup https://your-api-gateway.execute-api.region.amazonaws.com

# 2. Authenticate
vamscli auth login -u your-username@example.com

# 3. Create asset
vamscli assets create -d my-database --name "My Asset" --description "Description"

# 4. Upload files
vamscli file upload -d my-database -a my-asset /path/to/files/
```

### Automation Workflow

```bash
# Setup with JSON output for parsing
vamscli setup https://api.example.com --json-output

# Authenticate with saved credentials
vamscli auth login -u user@example.com --save-credentials

# Create asset with JSON input
vamscli assets create --json-input @asset-config.json --json-output

# Upload files with JSON automation
vamscli file upload --json-input @upload-config.json --json-output --hide-progress
```

### Token Override Workflow

```bash
# Method 1: Use auth login with token override
vamscli auth login --user-id user@example.com --token-override "external_token" --expires-at "+7200"

# Use commands normally after login
vamscli assets list -d my-database

# Method 2: Use auth set-override command
vamscli auth set-override -u user@example.com --token "external_token" --expires-at "+7200"

# Clear override when done
vamscli auth clear-override
```

### Multi-Profile Workflow

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

## Error Handling

All commands provide comprehensive error handling with user-friendly messages:

### Common Error Types

**Setup Required:**

```
✗ Setup Required: Configuration not found. Please run 'vamscli setup <api-gateway-url>' first.
```

**Authentication Required:**

```
✗ Authentication failed: Not authenticated. Please run 'vamscli auth login' to authenticate.
```

**API Unavailable:**

```
✗ API Unavailable: VAMS API is not currently available. Please check your network connection.
```

**Version Mismatch:**

```
⚠️  Version mismatch detected:
   CLI version: 2.2.0
   API version: 2.1.0
   This may cause compatibility issues.
```

### Debug Mode

For detailed error information, add `--debug` to any command:

```bash
vamscli --debug assets create -d my-db --name "Test"
```

## Exit Codes

VamsCLI uses standard exit codes:

-   **0**: Success
-   **1**: General error (authentication, API, validation, etc.)
-   **2**: Command line usage error (invalid arguments, missing options)

## Command Completion

VamsCLI commands can be interrupted safely with Ctrl+C:

```
Operation cancelled by user.
```

For file uploads, interrupting will abort the current upload sequence and clean up temporary resources.

## Best Practices

### For Interactive Use

1. **Use descriptive names**: Choose clear asset and database IDs
2. **Check status regularly**: Use `vamscli auth status` to verify authentication
3. **Use help extensively**: Every command has detailed help with examples
4. **Save credentials**: Use `--save-credentials` for convenience

### For Automation

1. **Use JSON input/output**: Enables scripting and automation
2. **Hide progress displays**: Use `--hide-progress` for cleaner output
3. **Handle exit codes**: Check command exit codes in scripts
4. **Use override tokens**: For external authentication systems

### For File Uploads

1. **Organize files**: Use directory uploads for multiple files
2. **Use asset locations**: Organize files within assets using `--asset-location`
3. **Monitor progress**: Default progress display shows detailed status
4. **Handle large files**: CLI automatically optimizes chunking for large files
5. **Validate preview files**: Ensure base files exist for `.previewFile.` uploads

## Scripting Examples

### Bash Automation Script

```bash
#!/bin/bash

# VamsCLI Automation Script
set -e  # Exit on any error

# Configuration
API_GATEWAY="https://your-api-gateway.execute-api.region.amazonaws.com"
USERNAME="user@example.com"
DATABASE_ID="automation-db"
PROFILE="automation"

# Setup and authentication
echo "Setting up VamsCLI..."
vamscli setup "$API_GATEWAY" --profile "$PROFILE" --json-output

echo "Authenticating..."
vamscli auth login -u "$USERNAME" --profile "$PROFILE" --save-credentials

# Create database if it doesn't exist
echo "Creating database..."
vamscli database create -d "$DATABASE_ID" --description "Automation Database" --profile "$PROFILE" --json-output || echo "Database may already exist"

# Create assets from configuration files
for config_file in assets/*.json; do
    echo "Creating asset from $config_file..."
    vamscli assets create --json-input "@$config_file" --profile "$PROFILE" --json-output
done

# Upload files for each asset
for asset_dir in uploads/*/; do
    asset_name=$(basename "$asset_dir")
    echo "Uploading files for asset: $asset_name"
    vamscli file upload -d "$DATABASE_ID" -a "$asset_name" --directory "$asset_dir" --recursive --profile "$PROFILE" --hide-progress --json-output
done

echo "Automation complete!"
```

### PowerShell Automation Script

```powershell
# VamsCLI Automation Script
$ErrorActionPreference = "Stop"

# Configuration
$ApiGateway = "https://your-api-gateway.execute-api.region.amazonaws.com"
$Username = "user@example.com"
$DatabaseId = "automation-db"
$Profile = "automation"

# Setup and authentication
Write-Host "Setting up VamsCLI..."
vamscli setup $ApiGateway --profile $Profile --json-output

Write-Host "Authenticating..."
vamscli auth login -u $Username --profile $Profile --save-credentials

# Create database if it doesn't exist
Write-Host "Creating database..."
try {
    vamscli database create -d $DatabaseId --description "Automation Database" --profile $Profile --json-output
} catch {
    Write-Host "Database may already exist"
}

# Create assets from configuration files
Get-ChildItem "assets\*.json" | ForEach-Object {
    Write-Host "Creating asset from $($_.Name)..."
    vamscli assets create --json-input "@$($_.FullName)" --profile $Profile --json-output
}

# Upload files for each asset
Get-ChildItem "uploads\*" -Directory | ForEach-Object {
    $assetName = $_.Name
    Write-Host "Uploading files for asset: $assetName"
    vamscli file upload -d $DatabaseId -a $assetName --directory $_.FullName --recursive --profile $Profile --hide-progress --json-output
}

Write-Host "Automation complete!"
```

### Python Integration Example

```python
#!/usr/bin/env python3
"""
VamsCLI Python Integration Example
"""

import subprocess
import json
import sys
from pathlib import Path

class VamsCLI:
    def __init__(self, profile="default"):
        self.profile = profile

    def run_command(self, command, check_exit_code=True):
        """Run a VamsCLI command and return the result."""
        cmd = ["vamscli"] + command + ["--profile", self.profile, "--json-output"]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=check_exit_code)
            if result.stdout:
                return json.loads(result.stdout)
            return None
        except subprocess.CalledProcessError as e:
            print(f"Command failed: {' '.join(cmd)}")
            print(f"Error: {e.stderr}")
            if check_exit_code:
                sys.exit(1)
            return None

    def setup(self, api_gateway_url):
        """Setup VamsCLI with API Gateway URL."""
        return self.run_command(["setup", api_gateway_url])

    def login(self, username, save_credentials=True):
        """Authenticate with VamsCLI."""
        cmd = ["auth", "login", "-u", username]
        if save_credentials:
            cmd.append("--save-credentials")
        return self.run_command(cmd)

    def create_asset(self, database_id, asset_name, description, tags=None):
        """Create an asset."""
        asset_data = {
            "databaseId": database_id,
            "assetName": asset_name,
            "description": description
        }
        if tags:
            asset_data["tags"] = tags

        # Write to temporary file
        config_file = Path("temp_asset_config.json")
        with open(config_file, "w") as f:
            json.dump(asset_data, f)

        try:
            return self.run_command(["assets", "create", "--json-input", f"@{config_file}"])
        finally:
            config_file.unlink(missing_ok=True)

    def upload_files(self, database_id, asset_id, files, asset_location="/"):
        """Upload files to an asset."""
        upload_data = {
            "database_id": database_id,
            "asset_id": asset_id,
            "files": files,
            "asset_location": asset_location,
            "hide_progress": True
        }

        # Write to temporary file
        config_file = Path("temp_upload_config.json")
        with open(config_file, "w") as f:
            json.dump(upload_data, f)

        try:
            return self.run_command(["file", "upload", "--json-input", f"@{config_file}"])
        finally:
            config_file.unlink(missing_ok=True)

# Example usage
def main():
    # Initialize VamsCLI
    vams = VamsCLI(profile="automation")

    # Setup and authenticate
    vams.setup("https://your-api-gateway.execute-api.region.amazonaws.com")
    vams.login("user@example.com")

    # Create asset
    result = vams.create_asset(
        database_id="my-database",
        asset_name="Python Created Asset",
        description="Asset created via Python script",
        tags=["python", "automated"]
    )

    if result:
        print(f"Asset created successfully: {result}")

        # Upload files
        files = ["/path/to/model.gltf", "/path/to/texture.png"]
        upload_result = vams.upload_files("my-database", "python-created-asset", files)

        if upload_result:
            print(f"Files uploaded successfully: {upload_result}")

if __name__ == "__main__":
    main()
```

## Configuration Management

### Environment-Specific Configurations

```bash
# Development environment
vamscli setup https://dev-api.example.com --profile development
vamscli auth login -u dev-user@example.com --profile development

# Staging environment
vamscli setup https://staging-api.example.com --profile staging
vamscli auth login -u staging-user@example.com --profile staging

# Production environment
vamscli setup https://prod-api.example.com --profile production
vamscli auth login -u prod-user@example.com --profile production

# Use environment-specific profiles
vamscli assets list --profile development
vamscli assets list --profile staging
vamscli assets list --profile production
```

### Configuration Backup and Restore

```bash
# Backup current configuration
vamscli profile list --json-output > profile-backup.json

# Document current setup for each profile
vamscli profile info default --json-output > default-profile.json
vamscli profile info production --json-output > production-profile.json

# After system restore, recreate profiles
vamscli setup https://api.example.com --profile default
vamscli setup https://prod-api.example.com --profile production

# Re-authenticate
vamscli auth login -u user@example.com --profile default
vamscli auth login -u user@example.com --profile production
```

## Performance Optimization

### File Upload Optimization

```bash
# For slow connections
vamscli file upload --parallel-uploads 3 <files>

# For fast connections
vamscli file upload --parallel-uploads 15 <files>

# Increase retry attempts for unreliable connections
vamscli file upload --retry-attempts 5 <files>

# Use force skip for automation
vamscli file upload --force-skip <files>
```

### General Performance

1. **Use JSON Output**: Faster parsing for automation
2. **Hide Progress**: Reduces terminal overhead
3. **Batch Operations**: Group related operations together
4. **Profile Management**: Use appropriate profiles for different environments

## Security Considerations

### Token Management

```bash
# Use override tokens for external authentication
vamscli auth login --user-id user@example.com --token-override "secure_token" --expires-at "+3600"

# Or use set-override command
vamscli auth set-override -u user@example.com --token "secure_token" --expires-at "+3600"

# Clear tokens when done
vamscli auth clear-override

# Use temporary tokens for sensitive operations
vamscli auth login --user-id user@example.com --token-override "temp_token"
vamscli assets delete sensitive-asset --confirm
vamscli auth clear-override
```

### Profile Security

```bash
# Use separate profiles for different security contexts
vamscli setup https://secure-api.example.com --profile secure
vamscli auth login -u secure-user@example.com --profile secure

# Limit profile usage to specific operations
vamscli assets list --profile secure  # Only use secure profile when needed
```

### Credential Management

```bash
# Save credentials only when appropriate
vamscli auth login -u user@example.com --save-credentials  # For development
vamscli auth login -u user@example.com  # For production (no save)

# Regular credential refresh
vamscli auth refresh  # Refresh tokens when needed
vamscli auth status   # Check token status regularly
```
