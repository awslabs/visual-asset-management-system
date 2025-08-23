# VamsCLI Authentication Guide

This guide provides detailed information about VamsCLI's authentication system, including AWS Cognito integration and override token support.

## Authentication Overview

VamsCLI supports two authentication methods with full multi-profile support:

1. **AWS Cognito Authentication** - Primary authentication method using AWS Cognito User Pools
2. **Override Token Authentication** - External token support for integration with other authentication systems

**Profile Support**: Each profile maintains completely separate authentication state, allowing you to authenticate to different VAMS environments or as different users on the same machine.

## AWS Cognito Authentication

### How It Works

VamsCLI uses AWS Cognito User Pools for secure authentication with the following flow:

1. **Initial Authentication**: User provides username and password
2. **SRP Authentication**: Secure Remote Password protocol for secure login
3. **Challenge Handling**: Automatic handling of MFA and password challenges
4. **Token Management**: Secure storage and automatic refresh of tokens
5. **Profile Integration**: Calls `/auth/loginProfile/{userId}` to refresh user profile

### Supported Features

-   **Multi-Factor Authentication (MFA)**: Automatic handling of SMS and TOTP challenges
-   **Password Challenges**: New password requirements and password resets
-   **Token Refresh**: Automatic token refresh using refresh tokens
-   **Secure Storage**: Tokens stored securely in OS-appropriate locations
-   **Session Management**: Maintains authentication state across CLI sessions

### Authentication Commands

#### Login

```bash
vamscli auth login -u john.doe@example.com
vamscli auth login -u john.doe@example.com -p mypassword
vamscli auth login -u john.doe@example.com --save-credentials
```

**Options:**

-   `-u, --username`: Username (email address) - required
-   `-p, --password`: Password (prompts securely if not provided)
-   `--save-credentials`: Save username/password for automatic re-authentication

**Authentication Flow:**

1. Validates username format
2. Prompts for password if not provided
3. Initiates SRP authentication with Cognito
4. Handles any authentication challenges (MFA, new password, etc.)
5. Stores authentication tokens securely
6. Calls login profile API to refresh user profile
7. Optionally saves credentials for future use

#### Status Check

```bash
vamscli auth status
```

**Shows:**

-   Authentication type (Cognito or Override)
-   User ID
-   Token validity and expiration
-   Saved credentials status
-   Refresh token availability

#### Token Refresh

```bash
vamscli auth refresh
```

**Requirements:**

-   Valid refresh token must be stored
-   Only works with Cognito authentication
-   Automatically called when tokens expire during API calls

#### Logout

```bash
vamscli auth logout
```

**Actions:**

-   Removes stored authentication tokens
-   Deletes saved credentials
-   Clears all session data

### MFA Support

VamsCLI automatically handles MFA challenges:

#### SMS MFA

When SMS MFA is required, VamsCLI will:

1. Display the phone number where the code was sent
2. Prompt for the MFA code
3. Submit the code to complete authentication

#### TOTP MFA

For Time-based One-Time Password (TOTP) MFA:

1. VamsCLI prompts for the TOTP code from your authenticator app
2. Submits the code to complete authentication

### Password Challenges

VamsCLI handles various password challenges:

#### New Password Required

When a new password is required:

1. VamsCLI prompts for a new password
2. Validates password meets requirements
3. Updates the password in Cognito
4. Continues with authentication

#### Password Reset

For password reset scenarios:

1. VamsCLI guides through the reset process
2. Prompts for verification code
3. Allows setting new password

## Override Token Authentication

### Overview

Override tokens allow integration with external authentication systems that are not natively supported by VamsCLI. This is useful for:

-   Custom authentication providers
-   External identity systems
-   Temporary access tokens
-   Service-to-service authentication

### Setting Override Tokens

#### Persistent Override Token

```bash
vamscli auth set-override -u user@example.com --token "your_token"
vamscli auth set-override -u user@example.com --token "your_token" --expires-at "2024-12-31T23:59:59Z"
```

**Options:**

-   `-u, --user-id`: User ID associated with the token (required)
-   `--token`: The override token (required)
-   `--expires-at`: Token expiration time (optional)

#### One-Time Override Token

```bash
vamscli -u user@example.com --token-override "temp_token" <command>
vamscli -u user@example.com --token-override "temp_token" --expires-at "+3600" <command>
```

**Global Options:**

-   `-u, --user-id`: User ID for the token
-   `--token-override`: The override token
-   `--expires-at`: Token expiration time

### Override Token Features

-   **Direct Usage**: Tokens are used directly in API requests without modification
-   **Expiration Tracking**: Optional expiration time tracking with warnings
-   **Profile Integration**: Calls `/auth/loginProfile/{userId}` to validate token and refresh profile
-   **No Refresh**: Override tokens cannot be automatically refreshed
-   **Secure Storage**: Tokens stored securely when using persistent mode

### Expiration Time Formats

Override tokens support flexible expiration time formats:

#### Unix Timestamp

```bash
vamscli auth set-override -u user@example.com --token "token" --expires-at "1735689599"
```

#### ISO 8601 Format

```bash
vamscli auth set-override -u user@example.com --token "token" --expires-at "2024-12-31T23:59:59Z"
```

#### Relative Time

```bash
vamscli auth set-override -u user@example.com --token "token" --expires-at "+3600"
```

### Clearing Override Tokens

```bash
vamscli auth clear-override
```

This removes the override token and returns to Cognito authentication mode.

## Token Storage and Security

### Storage Locations

Authentication data is stored in OS-appropriate secure locations:

-   **Windows**: `%APPDATA%\vamscli\auth_profile.json`
-   **macOS**: `~/Library/Application Support/vamscli/auth_profile.json`
-   **Linux**: `~/.config/vamscli/auth_profile.json`

### Security Features

-   **Secure Storage**: Uses OS-appropriate secure storage mechanisms
-   **Token Encryption**: Sensitive data is protected
-   **HTTPS Only**: All authentication communication uses HTTPS
-   **No Plaintext Passwords**: Passwords are not stored unless explicitly requested
-   **Automatic Cleanup**: Expired tokens are automatically removed

### Token Types Stored

#### Cognito Tokens

-   **Access Token**: Used for API authentication
-   **Refresh Token**: Used for automatic token renewal
-   **ID Token**: Contains user identity information
-   **Token Metadata**: Expiration times and token type information

#### Override Tokens

-   **Override Token**: The external token provided
-   **User ID**: Associated user identifier
-   **Expiration Time**: Optional expiration tracking
-   **Token Metadata**: Source and type information

## Authentication Validation

### Automatic Validation

VamsCLI automatically validates authentication:

1. **Pre-flight Checks**: Validates tokens before API calls
2. **Expiration Checking**: Checks token expiration before use
3. **Automatic Refresh**: Refreshes Cognito tokens when needed
4. **Profile Validation**: Calls login profile API to validate tokens

### Manual Validation

Check authentication status manually:

```bash
vamscli auth status
```

This shows:

-   Current authentication method
-   Token validity
-   Expiration information
-   User profile status

## Error Handling

### Common Authentication Errors

#### Setup Required

```
✗ Setup Required: Configuration not found. Please run 'vamscli setup <api-gateway-url>' first.
```

**Solution**: Run the setup command with your API Gateway URL.

#### Authentication Failed

```
✗ Authentication failed: Invalid username or password
```

**Solutions:**

-   Verify username and password are correct
-   Check if account is confirmed and active
-   Try password reset if needed

#### Token Expired

```
✗ Authentication failed: Token has expired
```

**Solutions:**

-   Run `vamscli auth refresh` to refresh tokens
-   Run `vamscli auth login` to re-authenticate
-   For override tokens, provide a new token

#### Override Token Issues

```
✗ Override Token Error: Override token has expired
```

**Solutions:**

-   Provide a new override token with `vamscli auth set-override`
-   Use one-time override with `--token-override`
-   Clear override and use Cognito: `vamscli auth clear-override`

### MFA Errors

#### Invalid MFA Code

```
✗ Authentication failed: Invalid MFA code
```

**Solutions:**

-   Ensure you're entering the correct code
-   Wait for a new code if using TOTP
-   Check SMS messages for SMS MFA

#### MFA Setup Required

```
✗ Authentication failed: MFA setup required
```

**Solution**: Complete MFA setup in the VAMS web interface before using the CLI.

## Best Practices

### For Cognito Authentication

1. **Use Strong Passwords**: Follow your organization's password policy
2. **Enable MFA**: Use MFA for enhanced security
3. **Save Credentials Carefully**: Only use `--save-credentials` on trusted systems
4. **Regular Status Checks**: Use `vamscli auth status` to monitor token status
5. **Logout When Done**: Use `vamscli auth logout` on shared systems

### For Override Tokens

1. **Set Expiration Times**: Always set expiration times when possible
2. **Use Minimal Scope**: Ensure tokens have minimal required permissions
3. **Rotate Regularly**: Replace tokens regularly for security
4. **Clear When Done**: Use `vamscli auth clear-override` when finished
5. **Monitor Expiration**: Check status regularly to avoid expired token issues

### For Automation

1. **Use Override Tokens**: Better for automated systems than Cognito
2. **Handle Expiration**: Implement token renewal in automation scripts
3. **Use JSON Output**: Parse JSON responses for status information
4. **Error Handling**: Check exit codes and handle authentication failures
5. **Secure Storage**: Store tokens securely in automation environments

## Integration Examples

### CI/CD Pipeline

```bash
#!/bin/bash
# Set override token from environment variable
vamscli auth set-override -u "$VAMS_USER_ID" --token "$VAMS_TOKEN" --expires-at "+3600"

# Use CLI commands
vamscli assets create -d "$DATABASE_ID" --json-input @asset-config.json --json-output

# Clear token when done
vamscli auth clear-override
```

### Automated Scripts

```python
import subprocess
import json
import os

# Set override token
subprocess.run([
    'vamscli', 'auth', 'set-override',
    '-u', os.environ['VAMS_USER_ID'],
    '--token', os.environ['VAMS_TOKEN'],
    '--expires-at', '+7200'
])

# Use CLI with JSON output
result = subprocess.run([
    'vamscli', 'assets', 'list', '--json-output'
], capture_output=True, text=True)

if result.returncode == 0:
    assets = json.loads(result.stdout)
    print(f"Found {len(assets)} assets")
else:
    print(f"Error: {result.stderr}")
```

## Multi-Profile Authentication

### Profile-Based Authentication

Each profile maintains completely separate authentication state:

```bash
# Authenticate to different environments
vamscli auth login -u user@example.com --profile production
vamscli auth login -u user@example.com --profile staging

# Check status for specific profiles
vamscli auth status --profile production
vamscli auth status --profile staging

# Set override tokens for different profiles
vamscli auth set-override -u user@example.com --token "prod_token" --profile production
vamscli auth set-override -u user@example.com --token "staging_token" --profile staging
```

### Profile Authentication Features

-   **Complete Isolation**: Each profile has separate tokens and credentials
-   **Independent Expiration**: Tokens expire independently per profile
-   **Profile-Specific Validation**: Login profile API called per profile
-   **Separate Credential Storage**: Saved credentials are profile-specific

### Profile Authentication Examples

#### Multi-Environment Authentication

```bash
# Setup and authenticate to production
vamscli setup https://prod-api.example.com --profile production
vamscli auth login -u user@example.com --profile production

# Setup and authenticate to staging
vamscli setup https://staging-api.example.com --profile staging
vamscli auth login -u user@example.com --profile staging

# Use different profiles
vamscli assets list --profile production  # Uses production auth
vamscli assets list --profile staging     # Uses staging auth
```

#### Multi-User Authentication

```bash
# Setup profiles for different users
vamscli setup https://api.example.com --profile alice
vamscli setup https://api.example.com --profile bob

# Each user authenticates with their profile
vamscli auth login -u alice@example.com --profile alice
vamscli auth login -u bob@example.com --profile bob

# Users work with their own profiles
vamscli assets create -d my-db --name "Alice Asset" --profile alice
vamscli assets create -d my-db --name "Bob Asset" --profile bob
```

## Troubleshooting

For authentication troubleshooting, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

For general command usage, see [COMMANDS.md](COMMANDS.md).
