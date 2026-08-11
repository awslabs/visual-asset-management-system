---
sidebar_label: Setup and Authentication
title: Setup and Authentication Commands
---

# Setup and Authentication Commands

This page documents VamsCLI commands for initial setup, authentication, profile management, and feature switch inspection.

:::note[JSON output and profile selection]
Every command on this page accepts `--json-output` to emit a machine-readable JSON response instead of formatted text. The global `--profile <name>` option selects which profile a command runs against (default: `default`). These are omitted from individual Options tables unless they affect a command's behavior.
:::

## setup

Configure VamsCLI to connect to a VAMS deployment. The command fetches the Amplify configuration from the provided URL and extracts the Amazon API Gateway endpoint, AWS Region, and Amazon Cognito settings.

```bash
vamscli setup <BASE_URL> [OPTIONS]
```

### Options

| Option                 | Type | Required | Description                                                                        |
| ---------------------- | ---- | -------- | ---------------------------------------------------------------------------------- |
| `BASE_URL`             | TEXT | Yes      | VAMS deployment URL (Amazon CloudFront, ALB, Amazon API Gateway, or custom domain) |
| `--force`, `-f`        | Flag | No       | Overwrite existing configuration                                                   |
| `--skip-version-check` | Flag | No       | Skip CLI/API version mismatch confirmation prompts                                 |
| `--json-output`        | Flag | No       | Output raw JSON response                                                           |

### What setup does

1. Validates the base URL format (accepts any HTTP/HTTPS URL).
2. If the base URL is a direct Amazon API Gateway `execute-api` endpoint with no path, appends the REST API stage segment (`/api`) so the bootstrap calls resolve. A front (Amazon CloudFront or ALB) absorbs the stage, so a fronted or custom-domain URL is used unchanged.
3. Checks API version compatibility using the base URL.
4. Fetches Amplify configuration from `<base-url>/api/amplify-config`.
5. Extracts the Amazon API Gateway URL from the `api` field in the response (this value already includes the stage path).
6. Stores both the original base URL and extracted API Gateway URL locally.
7. Sets the profile as active when configuration is saved.
8. Clears existing authentication profiles (with `--force`).

### Examples

```bash
# Setup with Amazon CloudFront distribution
vamscli setup https://d1234567890.cloudfront.net

# Setup with custom domain
vamscli setup https://vams.mycompany.com

# Setup with ALB
vamscli setup https://my-alb-123456789.us-west-2.elb.amazonaws.com

# Setup directly against the Amazon API Gateway endpoint
# (the CLI appends the REST API stage path automatically)
vamscli setup https://abcdef1234.execute-api.us-west-2.amazonaws.com

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

:::note[Amazon API Gateway stage path]
The VAMS backend is an Amazon API Gateway REST API served under the fixed stage path `/api`. When you point `setup` at a front (Amazon CloudFront, ALB, or a custom domain), the front maps `/api/*` onto the stage and you use the front's URL as-is. When you point `setup` directly at the `execute-api` endpoint, the CLI appends the stage segment automatically, so use the bare endpoint URL (for example `https://abcdef1234.execute-api.us-west-2.amazonaws.com`) — do not add `/api` yourself.
:::

:::warning[Re-run setup after a deployment endpoint change]
If a profile was configured against a VAMS deployment whose backend used the previous Amazon API Gateway HTTP API, the stored Amazon API Gateway URL no longer points to a valid endpoint after the deployment is updated to the REST API. Re-run `vamscli setup <BASE_URL> --force` for that profile to fetch the current endpoint. Profiles that target a Amazon CloudFront/ALB/custom-domain front are unaffected as long as the front URL is unchanged.
:::

---

## auth login

Authenticate with VAMS using Amazon Cognito or a token override. Token override supplies a pre-generated token directly instead of having the CLI sign you in. It is used mostly for external identity provider authentication, but any valid pre-generated token works (including an Amazon Cognito token obtained outside VAMS).

```bash
vamscli auth login [OPTIONS]
```

### Options

| Option                 | Type | Required    | Description                                                                              |
| ---------------------- | ---- | ----------- | ---------------------------------------------------------------------------------------- |
| `-u`, `--username`     | TEXT | Conditional | Username for Amazon Cognito authentication                                               |
| `-p`, `--password`     | TEXT | No          | Password (prompts securely if not provided)                                              |
| `--new-password`       | TEXT | No          | New password to set when Amazon Cognito requires a password change                       |
| `--save-credentials`   | Flag | No          | Save credentials for automatic re-authentication                                         |
| `--user-id`            | TEXT | Conditional | User ID for token override authentication                                                |
| `--token-override`     | TEXT | Conditional | Pre-generated token to use directly, mostly for external IDP auth (requires `--user-id`) |
| `--expires-at`         | TEXT | No          | Token expiration time (Unix timestamp, ISO 8601, or `+seconds`)                          |
| `--skip-version-check` | Flag | No          | Skip version mismatch confirmation prompts                                               |
| `--json-output`        | Flag | No          | Output raw JSON response                                                                 |

### Amazon Cognito examples

```bash
vamscli auth login -u john.doe@example.com
vamscli auth login -u john.doe@example.com -p mypassword
vamscli auth login -u john.doe@example.com --save-credentials

# First login when Amazon Cognito forces a password change
vamscli auth login -u john.doe@example.com -p temporary-password --new-password new-password
```

:::note[Forced Password Change on Login]
Amazon Cognito can require a password change before the first successful sign-in (for example, for a newly created account). In interactive mode, VamsCLI prompts for the new password when one is required. With `--json-output`, supply the new password using `--new-password`; if a change is required and `--new-password` is not provided, the command returns an error rather than prompting.
:::

### Token override examples

```bash
vamscli auth login --user-id john.doe@example.com --token-override "eyJhbGciOiJIUzI1NiIs..."
vamscli auth login --user-id john.doe@example.com --token-override "token123" --expires-at "+3600"
vamscli auth login --user-id john.doe@example.com --token-override "token123" --expires-at "2025-12-31T23:59:59Z"
```

### Token override expiration formats

-   **Unix timestamp:** `1735689599`
-   **ISO 8601:** `2025-12-31T23:59:59Z`
-   **Relative:** `+3600` (3600 seconds from now)

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

## auth change-password

Change an Amazon Cognito user's password **when you know your current password**. The command signs in with the current password and sets a new one. It also satisfies a forced password change when Amazon Cognito requires one (for example, on a new account's first sign-in).

:::tip[change-password vs. forgot-password]
Use `auth change-password` when you **know** your current password and want to set a new one. If you have **forgotten** your password, use [`auth forgot-password`](#auth-forgot-password) instead, which resets it with a verification code emailed by Amazon Cognito.
:::

```bash
vamscli auth change-password [OPTIONS]
```

### Options

| Option             | Type | Required    | Description                                   |
| ------------------ | ---- | ----------- | --------------------------------------------- |
| `-u`, `--username` | TEXT | Yes         | Username for Amazon Cognito authentication    |
| `--old-password`   | TEXT | Conditional | Current password (prompts if not provided)    |
| `--new-password`   | TEXT | Conditional | New password to set (prompts if not provided) |
| `--json-output`    | Flag | No          | Output raw JSON response                      |

### Examples

```bash
vamscli auth change-password -u john.doe@example.com
vamscli auth change-password -u john.doe@example.com --old-password old --new-password new
vamscli auth change-password -u john.doe@example.com --old-password old --new-password new --json-output
```

:::note[Amazon Cognito Only]
This command is available only for deployments that use Amazon Cognito authentication. In interactive mode, VamsCLI prompts for any password not provided on the command line (the new password is confirmed). With `--json-output`, both `--old-password` and `--new-password` are required.
:::

---

## auth forgot-password

Reset a forgotten Amazon Cognito password **when you do not know your current password**, using an emailed verification code. This is a self-service flow that does not require knowing the current password. It is available only for deployments that use Amazon Cognito authentication.

:::tip[forgot-password vs. change-password]
Use `auth forgot-password` when you have **forgotten** your password and need to reset it. If you **know** your current password and simply want to change it, use [`auth change-password`](#auth-change-password) instead.
:::

```bash
vamscli auth forgot-password [OPTIONS]
```

### Options

| Option             | Type | Required    | Description                                                |
| ------------------ | ---- | ----------- | ---------------------------------------------------------- |
| `-u`, `--username` | TEXT | Yes         | Username for Amazon Cognito authentication                 |
| `--code`           | TEXT | Conditional | Verification code emailed by Amazon Cognito (confirm step) |
| `--new-password`   | TEXT | Conditional | New password to set (confirm step)                         |
| `--json-output`    | Flag | No          | Output raw JSON response                                   |

### How it works

The reset is a two-step flow handled by a single command:

1. **Request a code** — run with `--username` only. Amazon Cognito emails a verification code to the user's verified email or phone.
2. **Confirm the reset** — run again with `--code` and `--new-password` to set the new password.

In interactive mode, after the code is requested VamsCLI prompts for the verification code and new password, completing both steps in one invocation. With `--json-output`, prompts are not possible: provide `--code` and `--new-password` together to confirm, or neither to only request a code.

### Examples

```bash
# Step 1: request a verification code
vamscli auth forgot-password -u john.doe@example.com

# Step 2: confirm with the emailed code and a new password
vamscli auth forgot-password -u john.doe@example.com --code 123456 --new-password new-password

# JSON output (request a code only)
vamscli auth forgot-password -u john.doe@example.com --json-output
```

:::note[After a Reset]
A successful reset does not sign you in. Authenticate afterward with `vamscli auth login` using the new password.
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

| Option            | Type | Required | Description                                                     |
| ----------------- | ---- | -------- | --------------------------------------------------------------- |
| `-u`, `--user-id` | TEXT | Yes      | User ID associated with the override token                      |
| `--token`         | TEXT | Yes      | Override token to use for authentication                        |
| `--expires-at`    | TEXT | No       | Token expiration time (Unix timestamp, ISO 8601, or `+seconds`) |
| `--json-output`   | Flag | No       | Output raw JSON response                                        |

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

## auth routes list

List all available VAMS API routes with their HTTP methods and categories from the deployment's master route definitions. Useful when authoring API authorization constraints (`route__path` values).

```bash
vamscli auth routes list [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | FLAG | No       | Output raw JSON response |

```bash
vamscli auth routes list
vamscli auth routes list --json-output
```

---

## auth routes allowed

List the VAMS API routes (and the HTTP methods on each) the current user is authorized to call, based on the user's authorization constraints.

```bash
vamscli auth routes allowed [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | FLAG | No       | Output raw JSON response |

```bash
vamscli auth routes allowed
vamscli auth routes allowed --json-output
```

---

## features list

List all enabled feature switches for the current profile.

```bash
vamscli features list
```

Output includes total count, list of enabled feature names, and last updated timestamp.

### Available feature switches

| Feature                         | Description                                                                   |
| ------------------------------- | ----------------------------------------------------------------------------- |
| `GOVCLOUD`                      | GovCloud-specific functionality (also set for EU Sovereign Cloud deployments) |
| `ALLOWUNSAFEEVAL`               | Allow unsafe eval operations                                                  |
| `LOCATIONSERVICES`              | Location-based services and mapping                                           |
| `ALBDEPLOY`                     | Application Load Balancer deployment mode                                     |
| `NOOPENSEARCH`                  | Disable Amazon OpenSearch Service functionality                               |
| `AUTHPROVIDER_COGNITO`          | Amazon Cognito authentication provider                                        |
| `AUTHPROVIDER_COGNITO_SAML`     | Amazon Cognito SAML authentication provider                                   |
| `AUTHPROVIDER_EXTERNALOAUTHIDP` | External OAuth identity provider                                              |

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

## features example-govcloud

Demonstration command that runs only when the `GOVCLOUD` feature switch is enabled. It illustrates how feature-gated commands behave; it performs no operations.

```bash
vamscli features example-govcloud [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli features example-govcloud
vamscli features example-govcloud --json-output
```

:::note[Feature-Gated]
If `GOVCLOUD` is not enabled for the environment, the command exits with an error such as `GovCloud features are not enabled for this environment.`
:::

---

## features example-location

Demonstration command that runs only when the `LOCATIONSERVICES` feature switch is enabled. It illustrates how feature-gated commands behave; it performs no operations.

```bash
vamscli features example-location [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli features example-location
vamscli features example-location --json-output
```

:::note[Feature-Gated]
If `LOCATIONSERVICES` is not enabled for the environment, the command exits with an error such as `Location services are not enabled for this environment.`
:::

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

Subsequent commands, including `vamscli auth login`, use the profile selected here. Pass
`--profile <PROFILE_NAME>` before a command group to target a different profile for a single
invocation without changing the active one:

```bash
vamscli profile switch production          # every later command targets production
vamscli --profile staging assets list      # this one command targets staging
```

:::note
`--profile` is a global option, so it precedes the command group
(`vamscli --profile staging assets list`, not `vamscli assets list --profile staging`).
:::

---

## profile delete

Delete a profile and all its configuration. The default profile cannot be deleted.

```bash
vamscli profile delete <PROFILE_NAME> [--force]
```

| Option          | Type | Required | Description                         |
| --------------- | ---- | -------- | ----------------------------------- |
| `PROFILE_NAME`  | TEXT | Yes      | Name of the profile to delete       |
| `--force`, `-f` | Flag | No       | Force deletion without confirmation |
| `--json-output` | Flag | No       | Output raw JSON response            |

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

-   [Installation and Profile Management](../installation.md)
-   [Getting Started](../getting-started.md)
-   [Automation and Scripting](../automation.md)
