---
sidebar_label: Setup and Authentication
title: Setup and Authentication Commands
---

# Setup and Authentication Commands

This page documents VamsCLI commands for initial setup, authentication, profile management, and feature switch inspection.

:::note[JSON output and profile selection]
Every command on this page accepts `--json-output` to emit a machine-readable JSON response instead of formatted text. The global `--profile <name>` option selects which profile a command runs against; when it is omitted, the command uses the profile selected by [`profile switch`](#profile-switch), and the `default` profile only when no selection has been made. These are omitted from individual Options tables unless they affect a command's behavior.
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
Configuration is saved to `~/.config/vamscli/profiles/{profile_name}/`. Each profile maintains separate configuration and authentication. The profile becomes active after successful setup.
:::

:::note[Amazon API Gateway stage path]
The VAMS backend is an Amazon API Gateway REST API served under the fixed stage path `/api`. When you point `setup` at a front (Amazon CloudFront, ALB, or a custom domain), the front maps `/api/*` onto the stage and you use the front's URL as-is. When you point `setup` directly at the `execute-api` endpoint, the CLI appends the stage segment automatically, so use the bare endpoint URL (for example `https://abcdef1234.execute-api.us-west-2.amazonaws.com`) — do not add `/api` yourself.
:::

:::warning[Re-run setup after a deployment endpoint change]
A profile stores the Amazon API Gateway URL it was set up against. If the deployment's API Gateway identifier or invoke URL changes — which an update that replaces the API does — the stored URL stops resolving to a valid endpoint and every request from that profile fails. Re-run `vamscli setup <BASE_URL> --force` for the profile to pick up the current endpoint. A profile that targets an Amazon CloudFront, ALB, or custom-domain front is unaffected as long as the front's URL is unchanged.
:::

---

## auth login

Authenticate with VAMS using Amazon Cognito or a token override. Token override supplies a pre-generated token directly instead of having the CLI sign you in. Use it for any identity that does not have a password in the Amazon Cognito user pool — an external OAuth identity provider, or a user pool federated to SAML or OIDC — and for any other valid pre-generated token (including an Amazon Cognito token obtained outside VAMS).

```bash
vamscli auth login [OPTIONS]
```

### Options

| Option                   | Type | Required    | Description                                                                          |
| ------------------------ | ---- | ----------- | ------------------------------------------------------------------------------------ |
| `-u`, `--username`       | TEXT | Conditional | Username for Amazon Cognito authentication                                           |
| `-p`, `--password`       | TEXT | No          | Password, passed on the command line. Discouraged — see the note below               |
| `--password-stdin`       | Flag | No          | Read the password from stdin. The recommended non-interactive form                   |
| `--new-password`         | TEXT | No          | New password to set when Amazon Cognito requires a password change. Discouraged      |
| `--new-password-stdin`   | Flag | No          | Read the new password from stdin. The recommended non-interactive form               |
| `--save-credentials`     | Flag | No          | Save credentials for automatic re-authentication                                     |
| `--user-id`              | TEXT | Conditional | User ID for token override authentication                                            |
| `--token-override`       | TEXT | Conditional | Pre-generated token, mostly for external IDP auth (requires `--user-id`). Discouraged |
| `--token-override-stdin` | Flag | Conditional | Read the pre-generated token from stdin (requires `--user-id`). The recommended form  |
| `--expires-at`           | TEXT | No          | Token expiration time (Unix timestamp, ISO 8601, or `+seconds`)                      |
| `--skip-version-check`   | Flag | No          | Skip version mismatch confirmation prompts                                           |
| `--json-output`          | Flag | No          | Output raw JSON response                                                             |

:::warning[Credentials on the command line are readable by other local accounts]
Every argument of a running process appears in the OS process table — `/proc/<pid>/cmdline` and `ps -ef` on Linux, the command-line column of Task Manager on Windows. A credential supplied with `-p` or `--token-override` is therefore readable for the lifetime of the command by any other account on the machine, including one with no VAMS access.

Use `--password-stdin`, `--new-password-stdin` or `--token-override-stdin` for non-interactive logins, or omit the credential entirely to be prompted. `-p`, `--new-password` and `--token-override` continue to work for existing scripts and integrations, but are discouraged.

A process has one stdin, so `--password-stdin` and `--new-password-stdin` share it: with both set, stdin carries two newline-separated values in this order - the current password, then the new one. `--token-override-stdin` cannot share stdin with a password and is rejected in combination with either.
:::

### Amazon Cognito examples

```bash
vamscli auth login -u john.doe@example.com
vamscli auth login -u john.doe@example.com --save-credentials

# Non-interactive: the password is read from stdin, never from the command line
cat password.txt | vamscli auth login -u john.doe@example.com --password-stdin
printenv VAMS_PASSWORD | vamscli auth login -u john.doe@example.com --password-stdin --json-output

# Discouraged: the password is visible in the OS process table
vamscli auth login -u john.doe@example.com -p mypassword

# First login when Amazon Cognito forces a password change: two lines on stdin,
# the temporary password first, then the new one
printf '%s
%s
' "$TEMP_PASSWORD" "$NEW_PASSWORD" | vamscli auth login -u john.doe@example.com --password-stdin --new-password-stdin --json-output

# Discouraged: both passwords are visible in the OS process table
vamscli auth login -u john.doe@example.com -p temporary-password --new-password new-password
```

Only a trailing carriage return and line feed are stripped from a piped credential, so a password ending in a space is preserved. The bytes are decoded as UTF-8.

:::note[Forced Password Change on Login]
Amazon Cognito can require a password change before the first successful sign-in (for example, for a newly created account). In interactive mode, VamsCLI prompts for the new password when one is required. With `--json-output`, supply the new password using `--new-password-stdin` (or `--new-password`); if a change is required and neither is provided, the command returns an error rather than prompting.
:::

### Token override examples

```bash
# Recommended: the token is read from stdin, never from the command line
echo "$VAMS_TOKEN" | vamscli auth login --user-id john.doe@example.com --token-override-stdin
echo "$VAMS_TOKEN" | vamscli auth login --user-id john.doe@example.com --token-override-stdin --expires-at "+3600"

# Discouraged: the token is visible in the OS process table
vamscli auth login --user-id john.doe@example.com --token-override "eyJhbGciOiJIUzI1NiIs..."
vamscli auth login --user-id john.doe@example.com --token-override "token123" --expires-at "2025-12-31T23:59:59Z"
```

### Token override expiration formats

-   **Unix timestamp:** `1735689599`
-   **ISO 8601:** `2025-12-31T23:59:59Z`
-   **Relative:** `+3600` (3600 seconds from now)

:::note[Authentication Type Detection]
VamsCLI automatically detects the authentication type based on the Amplify configuration. If `cognitoUserPoolId` is configured, Amazon Cognito authentication is available. If it is not configured, only token override authentication is available.
:::

:::warning[Federated and external authentication]
Username/password sign-in works only for users that exist natively in the Amazon Cognito user pool. Use token override in all of these cases:

-   **No Amazon Cognito** (`useExternalOAuthIdp`) — the deployment has no user pool to sign in to.
-   **Amazon Cognito federated to SAML** (`useCognito.useSaml`) — a federated user has no password in the pool.
-   **Amazon Cognito federated to OIDC** (`useCognito.useOidc`) — same as SAML; the identity lives in the external provider.

```bash
vamscli auth login --user-id user@example.com --token-override "your-token"
```

Obtain the token from the identity provider or from the VAMS web application after signing in through the federated login button. Token override validates the token against the VAMS API rather than against a specific issuer, so a token from any of these providers works. A user pool with federation enabled still accepts username/password for its **native** users (for example the initial administrator), so an administrator can keep using `--username`.
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

| Option                  | Type | Required    | Description                                                            |
| ----------------------- | ---- | ----------- | ---------------------------------------------------------------------- |
| `-u`, `--username`      | TEXT | Yes         | Username for Amazon Cognito authentication                             |
| `--old-password`        | TEXT | Conditional | Current password (prompts if not provided). Discouraged — see below    |
| `--old-password-stdin`  | Flag | No          | Read the current password from stdin instead of the command line       |
| `--new-password`        | TEXT | Conditional | New password to set (prompts if not provided). Discouraged — see below |
| `--new-password-stdin`  | Flag | No          | Read the new password from stdin instead of the command line           |
| `--json-output`         | Flag | No          | Output raw JSON response                                               |

### Examples

```bash
vamscli auth change-password -u john.doe@example.com

# Recommended non-interactive form: neither password reaches the process table.
# With both stdin flags, stdin carries two lines - current password, then new.
printf '%s\n%s\n' "$OLD_PASSWORD" "$NEW_PASSWORD" | vamscli auth change-password -u john.doe@example.com --old-password-stdin --new-password-stdin --json-output
echo "$NEW_PASSWORD" | vamscli auth change-password -u john.doe@example.com --new-password-stdin

# Discouraged: the passwords are visible in the OS process table
vamscli auth change-password -u john.doe@example.com --old-password old --new-password new
vamscli auth change-password -u john.doe@example.com --old-password old --new-password new --json-output
```

:::warning[Passwords on the Command Line]
A password passed as an option value is published by the OS process table — `/proc/<pid>/cmdline` and `ps -ef` on Linux, the command-line column in Task Manager on Windows — to any other local account, and is recorded in shell history and in CI job logs. Use `--old-password-stdin` and `--new-password-stdin` for any scripted invocation.
:::

:::note[Amazon Cognito Only]
This command is available only for deployments that use Amazon Cognito authentication. In interactive mode, VamsCLI prompts for any password not provided on the command line (the new password is confirmed). With `--json-output`, both passwords must be supplied up front, by option or on stdin.
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

| Option                 | Type | Required    | Description                                                             |
| ---------------------- | ---- | ----------- | ----------------------------------------------------------------------- |
| `-u`, `--username`     | TEXT | Yes         | Username for Amazon Cognito authentication                              |
| `--code`               | TEXT | Conditional | Verification code emailed by Amazon Cognito (confirm step)              |
| `--new-password`       | TEXT | Conditional | New password to set (confirm step). Discouraged — see the warning below |
| `--new-password-stdin` | Flag | No          | Read the new password from stdin instead of the command line            |
| `--json-output`        | Flag | No          | Output raw JSON response                                                |

### How it works

The reset is a two-step flow handled by a single command:

1. **Request a code** — run with `--username` only. Amazon Cognito emails a verification code to the user's verified email or phone.
2. **Confirm the reset** — run again with `--code` and `--new-password` to set the new password.

In interactive mode, after the code is requested VamsCLI prompts for the verification code and new password, completing both steps in one invocation. With `--json-output`, prompts are not possible: provide `--code` and `--new-password` together to confirm, or neither to only request a code.

### Examples

```bash
# Step 1: request a verification code
vamscli auth forgot-password -u john.doe@example.com

# Step 2 (recommended): the password never reaches the process table
echo "$NEW_PASSWORD" | vamscli auth forgot-password -u john.doe@example.com --code 123456 --new-password-stdin

# Step 2, discouraged: the password is visible in the OS process table
vamscli auth forgot-password -u john.doe@example.com --code 123456 --new-password new-password

# JSON output (request a code only)
vamscli auth forgot-password -u john.doe@example.com --json-output
```

:::warning[Passwords on the Command Line]
`--new-password` is readable from the OS process table by any other local account and is recorded in shell history and CI logs. Use `--new-password-stdin` for any scripted invocation.
:::

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

| Option            | Type | Required    | Description                                                                 |
| ----------------- | ---- | ----------- | --------------------------------------------------------------------------- |
| `-u`, `--user-id` | TEXT | Yes         | User ID associated with the override token                                   |
| `--token`         | TEXT | Conditional | Override token to use for authentication. Discouraged — see the warning below |
| `--token-stdin`   | Flag | Conditional | Read the override token from stdin instead of the command line               |
| `--expires-at`    | TEXT | No          | Token expiration time (Unix timestamp, ISO 8601, or `+seconds`)             |
| `--json-output`   | Flag | No          | Output raw JSON response                                                    |

Exactly one of `--token` or `--token-stdin` is required.

```bash
# Recommended: the token never reaches the process table
echo "$VAMS_TOKEN" | vamscli auth set-override -u john.doe@example.com --token-stdin
echo "$VAMS_TOKEN" | vamscli auth set-override -u john.doe@example.com --token-stdin --expires-at "+3600"

# Discouraged: the token is visible in the OS process table
vamscli auth set-override -u john.doe@example.com --token "eyJhbGciOiJIUzI1NiIs..."
vamscli auth set-override -u john.doe@example.com --token "token123" --expires-at "+3600"
```

:::warning[Tokens on the Command Line]
An override token is a bearer credential. Passed as an option value it is readable from the OS process table by any other local account and is recorded in shell history and CI logs. Use `--token-stdin` for any scripted invocation.
:::

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
| `CLOUDFRONTDEPLOY`              | Amazon CloudFront deployment mode                                             |
| `NOOPENSEARCH`                  | Disable Amazon OpenSearch Service functionality                               |
| `AUTHPROVIDER_COGNITO`          | Amazon Cognito authentication provider                                        |
| `AUTHPROVIDER_COGNITO_SAML`     | Amazon Cognito user pool federated to a SAML identity provider                |
| `AUTHPROVIDER_COGNITO_OIDC`     | Amazon Cognito user pool federated to an OIDC identity provider               |
| `AUTHPROVIDER_EXTERNALOAUTHIDP` | External OAuth identity provider                                              |
| `PHYSNA_ADDON`                  | Physna add-on frontend features                                               |
| `DEADLINECLOUD_PIPELINES`       | AWS Deadline Cloud pipeline execution type                                    |

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

Naming a profile that does not exist is reported as an error and exits non-zero, so a cleanup script cannot read a typo'd name as "already removed".

---

## profile info

Show detailed information about a specific profile, including Amplify configuration, authentication type, and token expiration.

```bash
vamscli profile info <PROFILE_NAME>
```

Naming a profile that does not exist is reported as an error and exits non-zero.

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
