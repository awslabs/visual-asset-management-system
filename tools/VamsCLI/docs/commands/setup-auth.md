# Setup and Authentication Commands

This document covers VamsCLI setup, authentication, and profile management commands.

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

## Setup Commands

### `vamscli setup <base-url>`

Configure VamsCLI with your VAMS base URL for the specified profile. Accepts any HTTP/HTTPS URL including CloudFront, ALB, API Gateway, or custom domains.

**Arguments:**

-   `base_url`: VAMS base URL - any HTTP/HTTPS address (required)

**Options:**

-   `--force, -f`: Force setup even if configuration exists
-   `--skip-version-check`: Skip version mismatch confirmation prompts (useful for automation)

**Global Options:**

-   `--profile`: Profile name to use (default: "default")

**Examples:**

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
vamscli --profile development setup https://dev-vams.example.com

# Force overwrite existing configuration
vamscli setup https://vams.example.com --force
vamscli --profile production setup https://new-prod-vams.example.com --force

# Skip version mismatch confirmation (useful for automation)
vamscli setup https://vams.example.com --skip-version-check
vamscli --profile production setup https://prod-vams.example.com --skip-version-check
```

**What it does:**

1. Validates the base URL format (accepts any HTTP/HTTPS URL)
2. Checks API version compatibility using the base URL
3. Fetches Amplify configuration from `<base-url>/api/amplify-config`
4. Extracts the API Gateway URL from the "api" field in the response
5. Validates the extracted API Gateway URL
6. Stores both the original base URL and extracted API Gateway URL locally
7. Sets the profile as active when configuration is saved
8. Clears existing authentication profiles (with `--force`)

**Profile-Specific Behavior:**

-   Configuration is saved to `~/.config/vamscli/profiles/{profile_name}/`
-   Each profile maintains separate configuration and authentication
-   The profile becomes active after successful setup
-   Next steps instructions include profile-specific commands when using non-default profiles

## Authentication Commands

### `vamscli auth login`

Authenticate with VAMS using AWS Cognito or token override.

**Options:**

-   `-u, --username`: Username for Cognito authentication
-   `-p, --password`: Password (will prompt if not provided)
-   `--save-credentials`: Save credentials for automatic re-authentication
-   `--user-id`: User ID for token override authentication
-   `--token-override`: Override token for external authentication (requires --user-id)
-   `--expires-at`: Token expiration time (Unix timestamp, ISO 8601, or +seconds)
-   `--skip-version-check`: Skip version mismatch confirmation prompts (useful for automation)

**Cognito Authentication Examples:**

```bash
vamscli auth login -u john.doe@example.com
vamscli auth login -u john.doe@example.com -p mypassword
vamscli auth login -u john.doe@example.com --save-credentials

# Skip version mismatch confirmation (useful for automation)
vamscli auth login -u john.doe@example.com --skip-version-check
```

**Token Override Authentication Examples:**

```bash
vamscli auth login --user-id john.doe@example.com --token-override "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
vamscli auth login --user-id john.doe@example.com --token-override "token123" --expires-at "+3600"
vamscli auth login --user-id john.doe@example.com --token-override "token123" --expires-at "2024-12-31T23:59:59Z"
```

**Token Override Expiration Formats:**

-   Unix timestamp: `1735689599`
-   ISO 8601: `2024-12-31T23:59:59Z`
-   Relative: `+3600` (3600 seconds from now)

**Features:**

-   **Cognito Authentication**: Handles MFA challenges automatically, supports password reset requirements
-   **Token Override**: Direct token usage for external authentication
-   **Validation**: All tokens validated with login profile API
-   **Feature Switches**: Automatically fetches enabled features after authentication
-   **Secure Storage**: Tokens stored securely in profile-specific files

### `vamscli auth logout`

Remove authentication profile and saved credentials.

```bash
vamscli auth logout
```

**What it does:**

-   Removes stored authentication tokens
-   Deletes saved credentials
-   Clears session data

### `vamscli auth status`

Show current authentication status and token information.

```bash
vamscli auth status
```

**Output includes:**

-   Authentication type (Cognito or Override)
-   User ID
-   Token validity status
-   Expiration information
-   Saved credentials status
-   Feature switches information (count and enabled features)

### `vamscli auth refresh`

Refresh authentication tokens using stored refresh token.

```bash
vamscli auth refresh
```

**Requirements:**

-   Must have valid refresh token
-   Only works with Cognito authentication (not override tokens)

### `vamscli auth set-override`

Set an override token for external authentication systems.

**Options:**

-   `-u, --user-id`: User ID associated with the override token (required)
-   `--token`: Override token to use for authentication (required)
-   `--expires-at`: Token expiration time (optional)

**Examples:**

```bash
vamscli auth set-override -u john.doe@example.com --token "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
vamscli auth set-override -u john.doe@example.com --token "token123" --expires-at "2024-12-31T23:59:59Z"
vamscli auth set-override -u john.doe@example.com --token "token123" --expires-at "+3600"
```

**Features:**

-   Validates token with login profile API
-   Supports expiration time tracking
-   No automatic refresh capability

### `vamscli auth clear-override`

Clear the current override token and return to Cognito authentication.

```bash
vamscli auth clear-override
```

## Feature Switches Commands

VamsCLI automatically fetches and manages feature switches from the backend during authentication. These feature switches control which functionality is available in your VAMS environment.

### `vamscli features list`

List all enabled feature switches for the current profile.

```bash
vamscli features list
```

**Output includes:**

-   Total count of enabled features
-   List of all enabled feature names
-   Last updated timestamp

**Example output:**

```
Enabled Feature Switches:
Total: 3

Features:
  ✓ AUTHPROVIDER_COGNITO
  ✓ GOVCLOUD
  ✓ LOCATIONSERVICES

Last updated: 2024-01-01T12:00:00Z
```

### `vamscli features check <feature-name>`

Check if a specific feature switch is enabled.

**Arguments:**

-   `feature_name`: Name of the feature to check (required)

**Examples:**

```bash
vamscli features check GOVCLOUD
vamscli features check LOCATIONSERVICES
vamscli features check AUTHPROVIDER_COGNITO
```

**Output:**

-   Green checkmark if feature is enabled
-   Red X if feature is disabled

### Available Feature Switches

Based on your VAMS backend configuration, the following features may be available:

-   **`GOVCLOUD`** - GovCloud-specific functionality
-   **`ALLOWUNSAFEEVAL`** - Allow unsafe eval operations
-   **`LOCATIONSERVICES`** - Location-based services and mapping
-   **`ALBDEPLOY`** - Application Load Balancer deployment mode
-   **`NOOPENSEARCH`** - Disable OpenSearch functionality
-   **`AUTHPROVIDER_COGNITO`** - AWS Cognito authentication provider
-   **`AUTHPROVIDER_COGNITO_SAML`** - Cognito SAML authentication provider
-   **`AUTHPROVIDER_EXTERNALOAUTHIDP`** - External OAuth identity provider

### Feature Switch Integration

Feature switches are automatically integrated into the authentication flow:

1. **During Login**: After successful Cognito authentication, CLI fetches feature switches
2. **During Override Token**: After successful token validation, CLI fetches feature switches
3. **Storage**: Feature switches are stored in the authentication profile
4. **Access**: Commands can check features using decorators or utility functions

**Authentication Profile with Feature Switches:**

```json
{
    "access_token": "...",
    "user_id": "user@example.com",
    "expires_at": 1234567890,
    "feature_switches": {
        "raw": "GOVCLOUD,LOCATIONSERVICES,AUTHPROVIDER_COGNITO",
        "enabled": ["GOVCLOUD", "LOCATIONSERVICES", "AUTHPROVIDER_COGNITO"],
        "fetched_at": "2024-01-01T12:00:00Z"
    }
}
```

### Example Commands (Demonstration)

VamsCLI includes example commands that demonstrate feature switch usage:

```bash
# These commands will only work if the required features are enabled
vamscli features example-govcloud      # Requires GOVCLOUD feature
vamscli features example-location      # Requires LOCATIONSERVICES feature
```

If a required feature is not enabled, you'll see an error message like:

```
Error: GovCloud features are not enabled for this environment.
```

## Profile Management Commands

VamsCLI supports multiple profiles to manage different VAMS environments or user accounts on the same machine.

### `vamscli profile list`

List all available profiles with their status and configuration.

```bash
vamscli profile list
```

**Output includes:**

-   Profile names with active indicator
-   API Gateway URLs
-   Authentication status
-   Saved credentials status

**Example output:**

```
Available profiles:

● default (active)
  API Gateway: https://prod-api.example.com
  CLI Version: 2.2.0
  User: john.doe@example.com
  Auth Type: Cognito
  Status: ✓ Authenticated

○ staging
  API Gateway: https://staging-api.example.com
  CLI Version: 2.2.0
  Status: Not authenticated
```

### `vamscli profile switch <profile-name>`

Switch to a different profile.

**Arguments:**

-   `profile_name`: Name of the profile to switch to (required)

**Examples:**

```bash
vamscli profile switch production
vamscli profile switch staging
```

**Requirements:**

-   Profile must exist and be configured
-   Use `vamscli setup --profile <name>` to create new profiles

### `vamscli profile delete <profile-name>`

Delete a profile and all its configuration.

**Arguments:**

-   `profile_name`: Name of the profile to delete (required)

**Options:**

-   `--force, -f`: Force deletion without confirmation

**Examples:**

```bash
vamscli profile delete old-profile
vamscli profile delete test-profile --force
```

**Restrictions:**

-   Cannot delete the "default" profile
-   Requires confirmation unless --force is used
-   If deleting active profile, switches to "default"

### `vamscli profile info <profile-name>`

Show detailed information about a specific profile.

**Arguments:**

-   `profile_name`: Name of the profile to inspect (required)

**Examples:**

```bash
vamscli profile info production
vamscli profile info staging
```

**Output includes:**

-   Profile directory location
-   Configuration details
-   Authentication information
-   Token expiration details

### `vamscli profile current`

Show the currently active profile.

```bash
vamscli profile current
```

**Output includes:**

-   Active profile name
-   API Gateway URL
-   Current authentication status

## Profile Usage Examples

### Multi-Environment Setup

```bash
# Setup different environments with flexible URLs
vamscli --profile production setup https://prod-vams.example.com
vamscli --profile staging setup https://staging-vams.example.com
vamscli --profile development setup https://dev-vams.example.com

# Authenticate to each environment
vamscli auth login -u user@example.com --profile production
vamscli auth login -u user@example.com --profile staging

# Use different profiles for operations
vamscli assets list --profile production
vamscli file upload -d my-db -a my-asset file.gltf --profile staging

# Manage profiles
vamscli profile list
vamscli profile switch production
vamscli profile current
```

### Multi-User Setup

```bash
# Setup for different users on same machine
vamscli setup https://api.example.com --profile alice
vamscli setup https://api.example.com --profile bob

# Each user authenticates with their profile
vamscli auth login -u alice@example.com --profile alice
vamscli auth login -u bob@example.com --profile bob

# Use user-specific profiles
vamscli assets create -d my-db --name "Alice Asset" --profile alice
vamscli assets create -d my-db --name "Bob Asset" --profile bob
```

### Profile Switching Workflow

```bash
# Check current profile
vamscli profile current

# List all profiles
vamscli profile list

# Switch to different environment
vamscli profile switch staging

# Verify switch
vamscli auth status  # Uses staging profile

# Switch back
vamscli profile switch production
```

## Token Override Workflow

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
