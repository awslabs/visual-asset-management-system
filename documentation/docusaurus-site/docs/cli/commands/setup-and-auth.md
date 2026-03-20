---
sidebar_label: Setup and Authentication
title: Setup and Authentication Commands
---

# Setup and Authentication Commands

This page documents VamsCLI commands for initial setup, authentication, profile management, and feature switch inspection.

## setup

Configure VamsCLI to connect to a VAMS deployment. The command fetches the Amplify configuration from the provided URL and extracts the Amazon API Gateway endpoint, AWS Region, and Amazon Cognito settings.

```bash
vamscli setup <BASE_URL> [OPTIONS]
```

### Options

| Option | Type | Required | Description |
|---|---|---|---|
| `BASE_URL` | TEXT | Yes | VAMS deployment URL (Amazon CloudFront, ALB, Amazon API Gateway, or custom domain) |
| `--force`, `-f` | Flag | No | Overwrite existing configuration |
| `--skip-version-check` | Flag | No | Skip CLI/API version mismatch confirmation prompts |

### What setup does

1. Validates the base URL format (accepts any HTTP/HTTPS URL).
2. Checks API version compatibility using the base URL.
3. Fetches Amplify configuration from `<base-url>/api/amplify-config`.
4. Extracts the Amazon API Gateway URL from the `api` field in the response.
5. Stores both the original base URL and extracted API Gateway URL locally.
6. Sets the profile as active when configuration is saved.
7. Clears existing authentication profiles (with `--force`).

### Examples

```bash
# Setup with Amazon CloudFront distribution
vamscli setup https://d1234567890.cloudfront.net

# Setup with custom domain
vamscli setup https://vams.mycompany.com

# Setup with ALB
vamscli setup https://my-alb-123456789.us-west-2.elb.amazonaws.com

# Setup specific profiles for different environments
vamscli --profile production setup https://prod-vams.example.com
vamscli --profile development setup https://dev-vams.example.com

# Force overwrite existing configuration
vamscli setup https://vams.example.com --force

# Skip version mismatch confirmation (useful for automation)
vamscli setup https://vams.example.com --skip-version-check
```

:::tip[Profile-Specific Behavior]
Configuration is saved to `~/.config/vamscli/profiles/\{profile_name\}/`. Each profile maintains separate configuration and authentication. The profile becomes active after successful setup.
:::

---

## auth login

Authenticate with VAMS using Amazon Cognito or token override.

```bash
vamscli auth login [OPTIONS]
```

### Options

| Option | Type | Required | Description |
|---|---|---|---|
| `-u`, `--username` | TEXT | Conditional | Username for Amazon Cognito authentication |
| `-p`, `--password` | TEXT | No | Password (prompts securely if not provided) |
| `--save-credentials` | Flag | No | Save credentials for automatic re-authentication |
| `--user-id` | TEXT | Conditional | User ID for token override authentication |
| `--token-override` | TEXT | Conditional | Override token for external authentication (requires `--user-id`) |
| `--expires-at` | TEXT | No | Token expiration time (Unix timestamp, ISO 8601, or `+seconds`) |
| `--skip-version-check` | Flag | No | Skip version mismatch confirmation prompts |

### Amazon Cognito examples

```bash
vamscli auth login -u john.doe@example.com
vamscli auth login -u john.doe@example.com -p mypassword
vamscli auth login -u john.doe@example.com --save-credentials
```

### Token override examples

```bash
vamscli auth login --user-id john.doe@example.com --token-override "eyJhbGciOiJIUzI1NiIs..."
vamscli auth login --user-id john.doe@example.com --token-override "token123" --expires-at "+3600"
vamscli auth login --user-id john.doe@example.com --token-override "token123" --expires-at "2025-12-31T23:59:59Z"
```

### Token override expiration formats

- **Unix timestamp:** `1735689599`
- **ISO 8601:** `2025-12-31T23:59:59Z`
- **Relative:** `+3600` (3600 seconds from now)

:::note[Authentication Type Detection]
VamsCLI automatically detects the authentication type based on the Amplify configuration. If `cognitoUserPoolId` is configured, Amazon Cognito authentication is available. If it is not configured, only token override authentication is available.
:::

:::warning[External Authentication]
If your VAMS deployment uses external authentication (no Amazon Cognito), you must use token override:

```bash
vamscli auth login --user-id user@example.com --token-override "your-external-token"
```
:::

---

## auth logout

Remove stored authentication tokens and saved credentials.

```bash
vamscli auth logout
```

---

## auth status

Display current authentication status, token information, and feature switches.

```bash
vamscli auth status
```

Output includes authentication type, user ID, token validity, expiration information, feature switch count, and enabled features.

---

## auth refresh

Refresh authentication tokens using a stored refresh token. Only works with Amazon Cognito authentication.

```bash
vamscli auth refresh
```

---

## auth set-override

Set an override token for external authentication systems.

```bash
vamscli auth set-override [OPTIONS]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `-u`, `--user-id` | TEXT | Yes | User ID associated with the override token |
| `--token` | TEXT | Yes | Override token to use for authentication |
| `--expires-at` | TEXT | No | Token expiration time |

```bash
vamscli auth set-override -u john.doe@example.com --token "eyJhbGciOiJIUzI1NiIs..."
vamscli auth set-override -u john.doe@example.com --token "token123" --expires-at "+3600"
```

---

## auth clear-override

Clear the current override token and return to Amazon Cognito authentication.

```bash
vamscli auth clear-override
```

---

## features list

List all enabled feature switches for the current profile.

```bash
vamscli features list
```

Output includes total count, list of enabled feature names, and last updated timestamp.

### Available feature switches

| Feature | Description |
|---|---|
| `GOVCLOUD` | GovCloud-specific functionality |
| `ALLOWUNSAFEEVAL` | Allow unsafe eval operations |
| `LOCATIONSERVICES` | Location-based services and mapping |
| `ALBDEPLOY` | Application Load Balancer deployment mode |
| `NOOPENSEARCH` | Disable Amazon OpenSearch Service functionality |
| `AUTHPROVIDER_COGNITO` | Amazon Cognito authentication provider |
| `AUTHPROVIDER_COGNITO_SAML` | Amazon Cognito SAML authentication provider |
| `AUTHPROVIDER_EXTERNALOAUTHIDP` | External OAuth identity provider |

---

## features check

Check if a specific feature switch is enabled.

```bash
vamscli features check <FEATURE_NAME>
```

```bash
vamscli features check GOVCLOUD
vamscli features check LOCATIONSERVICES
vamscli features check AUTHPROVIDER_COGNITO
```

---

## profile list

List all available profiles with their status, API Gateway URLs, and authentication status.

```bash
vamscli profile list
```

---

## profile switch

Switch to a different profile. The profile must exist and be configured.

```bash
vamscli profile switch <PROFILE_NAME>
```

---

## profile delete

Delete a profile and all its configuration. The default profile cannot be deleted.

```bash
vamscli profile delete <PROFILE_NAME> [--force]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `PROFILE_NAME` | TEXT | Yes | Name of the profile to delete |
| `--force`, `-f` | Flag | No | Force deletion without confirmation |

---

## profile info

Show detailed information about a specific profile, including Amplify configuration, authentication type, and token expiration.

```bash
vamscli profile info <PROFILE_NAME>
```

---

## profile current

Show the currently active profile name and status.

```bash
vamscli profile current
```

---

## Workflow Examples

### Multi-environment setup

```bash
# Setup different environments
vamscli --profile production setup https://prod-vams.example.com
vamscli --profile staging setup https://staging-vams.example.com

# Authenticate to each environment
vamscli auth login -u user@example.com --profile production
vamscli auth login -u user@example.com --profile staging

# Use different profiles for operations
vamscli assets list --profile production
vamscli file upload -d my-db -a my-asset file.gltf --profile staging

# Manage profiles
vamscli profile list
vamscli profile switch production
```

### Token override workflow

```bash
# Set override token
vamscli auth login --user-id user@example.com --token-override "external_token" --expires-at "+7200"

# Use commands normally
vamscli assets list -d my-database

# Clear override when done
vamscli auth clear-override
```

## Related Pages

- [Installation and Profile Management](../installation.md)
- [Getting Started](../getting-started.md)
- [Automation and Scripting](../automation.md)
