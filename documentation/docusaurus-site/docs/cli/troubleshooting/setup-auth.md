---
sidebar_label: Setup and Authentication
title: Setup and Authentication Troubleshooting
---

# Setup and Authentication Troubleshooting

This page covers issues encountered while configuring VamsCLI, signing in, managing profiles, and connecting to the VAMS API.

---

## Setup Issues

### Invalid Base URL

**Symptoms:**

-   `vamscli setup` fails with `Invalid base URL. Please provide a valid HTTP/HTTPS URL.`

**Cause:**

The URL passed to `vamscli setup` is missing a scheme or host, or uses a scheme other than `http`/`https`.

**Resolution:**

Pass a complete HTTP or HTTPS URL for any VAMS entry point — a CloudFront distribution, Application Load Balancer, custom domain, or Amazon API Gateway endpoint. VamsCLI fetches the Amplify configuration from this URL and extracts the API Gateway URL automatically.

```bash
vamscli setup https://d1234567890.cloudfront.net
vamscli setup https://vams.example.com
vamscli setup https://abc123.execute-api.us-west-2.amazonaws.com
```

When targeting the Amazon API Gateway `execute-api` endpoint directly, pass the bare endpoint URL — VamsCLI appends the REST API stage path (`/api`) for you. A `403 Missing Authentication Token` on the first setup call usually means the endpoint URL is otherwise incorrect (wrong API id or Region) rather than a missing stage. Through a CloudFront/ALB/custom-domain front, use the front's URL unchanged.

### Failed to Fetch Amplify Configuration

**Symptoms:**

-   Setup fails while fetching the Amplify configuration, or reports `No 'api' field found in amplify configuration response`.

**Cause:**

The base URL does not resolve to a running VAMS deployment, or the `/api/amplify-config` endpoint is unreachable from your network.

**Resolution:**

1. Confirm the base URL is correct and points to a deployed VAMS environment.
2. Verify the deployment is running and the `/api/amplify-config` endpoint is reachable.
3. Check your network connection and any firewall or proxy that may block the request.

### Configuration Already Exists

**Symptoms:**

-   Setup reports `Configuration already exists for profile '<name>'. Use --force to overwrite.`

**Resolution:**

Re-run setup with `--force` (or `-f`) to overwrite the existing configuration for the profile.

```bash
vamscli setup https://vams.example.com --force
```

### Version Mismatch

**Symptoms:**

-   Setup or login warns that the VamsCLI version and the VAMS API version differ.

**Cause:**

The installed VamsCLI version does not match the deployed VAMS API version, which can cause compatibility problems.

**Resolution:**

1. Update VamsCLI or the VAMS deployment so the versions align. See the [installation guide](../installation.md) for upgrade steps.
2. To proceed despite the warning in interactive mode, confirm the prompt.
3. To suppress the confirmation prompt in scripts, pass `--skip-version-check` to `vamscli setup` or `vamscli auth login`.

:::tip
The minimum supported VAMS API version is `2.2`. Connecting to an older deployment is not supported.
:::

---

## Authentication Issues

VamsCLI supports two authentication paths: Amazon Cognito (username and password, including MFA and forced password changes) and override tokens for deployments that use an external identity provider.

### Cognito Authentication Failed

**Symptoms:**

-   `vamscli auth login` reports a Cognito authentication error such as invalid credentials or an unconfirmed account.

**Cause:**

The username or password is incorrect, or the Amazon Cognito account has not been confirmed or activated.

**Resolution:**

1. Verify the username (typically an email address) and password.
2. Confirm the account is active and confirmed in the VAMS web interface.
3. If the password is forgotten, reset it with `vamscli auth forgot-password` (see below).

### Cognito Not Configured

**Symptoms:**

-   Login fails with a message stating that Cognito authentication is not configured and that the deployment uses external authentication.

**Cause:**

The deployment uses an external identity provider rather than Amazon Cognito, so username and password login is unavailable.

**Resolution:**

Use an override token instead. Obtain a valid token from your identity provider and supply it with `--user-id` and `--token-override`, or store it with `vamscli auth set-override`.

```bash
vamscli auth login --user-id user@example.com --token-override "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

### MFA Code Rejected

**Symptoms:**

-   Login fails after entering a multi-factor authentication (MFA) code.

**Cause:**

The MFA code is incorrect or has expired, or the device clock used for time-based one-time passwords (TOTP) is out of sync.

**Resolution:**

1. Enter the current 6-digit code from your authenticator app or the code sent by SMS.
2. For TOTP, ensure your device clock is synchronized so generated codes are valid.

### Forced Password Change on First Login

**Symptoms:**

-   A new account's first login reports that a password change is required.

**Cause:**

Amazon Cognito issues a `NEW_PASSWORD_REQUIRED` challenge for new accounts.

**Resolution:**

Provide the new password with `--new-password`. In interactive mode you are prompted for it if omitted; with `--json-output`, `--new-password` is required.

```bash
vamscli auth login -u user@example.com -p temp-password --new-password new-password
```

:::note
You can also complete a forced change with `vamscli auth change-password`, which signs in with the current password and sets the new one in a single step.
:::

### Changing or Resetting a Cognito Password

**Symptoms:**

-   You need to rotate a password you already know, or recover an account whose password is lost.

**Resolution:**

-   If you know your current password, use `vamscli auth change-password`. In interactive mode you are prompted for any password not supplied; with `--json-output`, both `--old-password` and `--new-password` are required.

    ```bash
    vamscli auth change-password -u user@example.com
    ```

-   If you have forgotten your current password, use the two-step self-service `vamscli auth forgot-password`. Run it with only `--username` to email a verification code, then run it again with `--code` and `--new-password` to confirm.

    ```bash
    # Step 1: request a code
    vamscli auth forgot-password -u user@example.com

    # Step 2: confirm with the emailed code
    vamscli auth forgot-password -u user@example.com --code 123456 --new-password new-password
    ```

:::note
Password changes and resets are available only for deployments that use Amazon Cognito. They are not supported when the deployment uses external authentication.
:::

### Token Refresh Failed

**Symptoms:**

-   `vamscli auth refresh` reports that the refresh token is missing or expired.

**Cause:**

The stored refresh token has expired, or no refresh token is present. Override tokens do not support refresh.

**Resolution:**

1. Re-authenticate with `vamscli auth login -u <username>`.
2. To avoid repeated logins in automation, sign in with `--save-credentials` so VamsCLI can re-authenticate automatically.

### Override Token Expired or Rejected

**Symptoms:**

-   API calls fail with an override token error indicating the token has expired, or login with `--token-override` is rejected.

**Cause:**

Override tokens are used directly and are never refreshed. VamsCLI performs a pre-flight expiry check and fails immediately once a token is expired or rejected by the API.

**Resolution:**

1. Set a fresh token: `vamscli auth set-override -u user@example.com --token "<new-token>"`.
2. For a single command, pass the token inline: `vamscli auth login --user-id user@example.com --token-override "<new-token>"`.
3. To return to Amazon Cognito authentication, clear the override: `vamscli auth clear-override`.

:::tip
Override tokens accept an optional `--expires-at` value as a Unix timestamp, an ISO 8601 timestamp, or a relative `+seconds` value (for example, `+3600`). Setting it lets VamsCLI warn you before the token lapses.
:::

### Not Authenticated

**Symptoms:**

-   A command fails with a message stating you are not authenticated.

**Resolution:**

1. Sign in with `vamscli auth login -u <username>`.
2. Check the current state with `vamscli auth status`, which reports the token type, expiration, and enabled feature switches.

---

## Profile Issues

VamsCLI supports multiple named profiles so you can target several VAMS environments. The active profile is used unless you pass the global `--profile` option before the command.

### Invalid Profile Name

**Symptoms:**

-   A profile command fails with `Invalid profile name ...`.

**Cause:**

Profile names must be 3 to 50 characters using only letters, numbers, hyphens, and underscores. The names `help`, `version`, and `list` are reserved.

**Resolution:**

Choose a name that satisfies these rules.

```bash
vamscli setup https://prod-vams.example.com --profile production
```

### Profile Does Not Exist

**Symptoms:**

-   Switching to or using a profile fails with `Profile '<name>' does not exist or is not configured.`

**Resolution:**

1. List configured profiles: `vamscli profile list`.
2. Create the profile by running setup for it: `vamscli setup <base-url> --profile <name>`.
3. Switch to an existing profile: `vamscli profile switch <name>`.

### Cannot Delete the Default Profile

**Symptoms:**

-   `vamscli profile delete default` fails with `Cannot delete the default profile`.

**Cause:**

The `default` profile is the fallback profile and cannot be removed.

**Resolution:**

Create and switch to another profile if you need a different default target, but leave the `default` profile in place.

---

## Configuration Management

### Configuration File Locations

Each profile stores `config.json`, `auth_profile.json`, and an optional `credentials.json` in a platform-specific directory:

| Platform | Location                                 |
| -------- | ---------------------------------------- |
| Windows  | `%APPDATA%\vamscli\`                     |
| macOS    | `~/Library/Application Support/vamscli/` |
| Linux    | `~/.config/vamscli/`                     |

### Corrupted or Missing Configuration

**Symptoms:**

-   A command reports `Setup Required` or fails to load configuration.

**Resolution:**

1. Re-run setup with `--force` to rewrite the configuration: `vamscli setup <base-url> --force`.
2. For a specific profile, include `--profile`: `vamscli setup <base-url> --profile <name> --force`.

### Resetting VamsCLI Configuration

To start over completely, remove the configuration directory and run setup again.

```bash
# macOS / Linux
rm -rf ~/.config/vamscli

# Then reconfigure and sign in
vamscli setup https://vams.example.com
vamscli auth login -u user@example.com
```

On Windows, delete the `%APPDATA%\vamscli` directory, then run `vamscli setup` again.

:::warning
Removing the configuration directory deletes all profiles, saved tokens, and stored credentials. Note your base URLs before resetting.
:::

---

## API Connectivity Issues

### API Unavailable or Not Responding

**Symptoms:**

-   Commands report that the VAMS API is unavailable or not responding.

**Cause:**

The deployment is unreachable, the base URL is incorrect, or a network device is blocking the connection.

**Resolution:**

1. Confirm the configured API Gateway URL: `vamscli profile info <name>` (or `vamscli profile current`).
2. Verify the deployment is running and reachable from your network.
3. Retry after a short delay; transient throttling and timeouts are retried automatically with exponential backoff.

### TLS Certificate Errors

**Symptoms:**

-   API calls fail with a TLS or certificate verification error.

**Cause:**

The system trust store is missing required certificates, or a corporate proxy intercepts TLS traffic.

**Resolution:**

1. Ensure the operating system certificate store is up to date.
2. If you are behind a TLS-inspecting proxy, work with your network administrator to trust the required certificate authority.

:::note
For corporate proxy configuration during installation and at runtime, see [Network and Configuration Troubleshooting](./network-config.md).
:::

---

## Diagnosing Problems

### Verbose Output

For detailed diagnostics, run any command with the global `--verbose` flag. Verbose output includes full error detail, API request and response information, and timing.

```bash
vamscli --verbose auth login -u user@example.com
vamscli --verbose setup https://vams.example.com
```

### Checking Status

Use these commands to inspect the current state before troubleshooting further:

```bash
# Installed VamsCLI version
vamscli --version

# Authentication status, token expiration, and enabled feature switches
vamscli auth status

# Active profile and its configuration
vamscli profile current
```

---

## Related Pages

-   [Setup and Authentication Commands](../commands/setup-and-auth.md) — full reference for `setup`, `auth`, and `profile` commands
-   [General CLI Troubleshooting](./general.md) — installation, encoding, and general command issues
-   [Network and Configuration Troubleshooting](./network-config.md) — proxy, network, and configuration-file issues
