---
sidebar_label: Users
title: User Management Troubleshooting
---

# User Management Troubleshooting

This page covers common problems encountered when managing Amazon Cognito users with the VamsCLI `user cognito` commands, along with their causes and resolutions.

---

## Authentication Provider Issues

### Amazon Cognito Not Enabled

The `user cognito` commands operate against the Amazon Cognito user pool and require the Cognito authentication provider to be enabled in the deployment.

**Symptoms:**

-   Commands fail with a `Cognito Operation Error` referencing that Cognito is not enabled
-   The CLI reports that the Cognito authentication provider is unavailable

**Cause:**

The VAMS deployment was not configured with the Amazon Cognito authentication provider, so the user management API routes are not active.

**Resolution:**

1. Confirm whether Cognito is enabled for your environment:

    ```bash
    vamscli features list
    ```

    Look for `AUTHPROVIDER_COGNITO` in the enabled features.

2. Check your authentication configuration:

    ```bash
    vamscli auth status
    ```

3. If Cognito is not enabled, contact your VAMS administrator to enable the Amazon Cognito authentication provider in the deployment configuration.

:::note
The `user cognito` command group is feature-gated. When Amazon Cognito is the configured provider for a deployment, these commands manage users directly in the Amazon Cognito user pool.
:::

---

## User Lookup and Creation Issues

### User Not Found

**Symptoms:**

-   `user cognito update`, `delete`, or `reset-password` fails with a `User Not Found` error for the supplied user ID

**Cause:**

The specified user does not exist in the Amazon Cognito user pool, or the user ID does not match exactly. User IDs are case-sensitive and use email format.

**Resolution:**

1. List users to confirm the exact user ID:

    ```bash
    vamscli user cognito list
    ```

2. For large user pools, fetch all users so the target is not missed by a single page:

    ```bash
    vamscli user cognito list --auto-paginate
    ```

3. Verify you are operating against the intended profile, since each profile targets a different deployment:

    ```bash
    vamscli --profile \{profile-name\} user cognito list
    ```

### User Already Exists

**Symptoms:**

-   `user cognito create` fails with a `User Already Exists` error

**Cause:**

A user with the supplied user ID already exists in the Amazon Cognito user pool.

**Resolution:**

-   To modify the existing user instead of creating a new one, use the update command:

    ```bash
    vamscli user cognito update -u user@example.com -e newemail@example.com
    ```

-   To replace the user, delete it first, then re-create it:

    ```bash
    vamscli user cognito delete -u user@example.com --confirm
    vamscli user cognito create -u user@example.com -e user@example.com
    ```

---

## Data Validation Issues

### Invalid Phone Number Format

**Symptoms:**

-   `user cognito create` or `user cognito update` fails with an `Invalid User Data` error stating that the phone number must be in E.164 format

**Cause:**

The value passed to `-p`/`--phone` is not in the E.164 international format that Amazon Cognito requires.

**Resolution:**

Supply the phone number in E.164 format: a leading `+`, the country code, then the subscriber number, with no spaces, dashes, or parentheses (up to 15 digits total).

```bash
# United States (+1)
vamscli user cognito create -u user@example.com -e user@example.com -p +12345678900

# United Kingdom (+44)
vamscli user cognito create -u user@example.com -e user@example.com -p +442071234567

# Japan (+81)
vamscli user cognito create -u user@example.com -e user@example.com -p +81312345678
```

Avoid formats that include separators or omit the `+` prefix, such as `12345678900`, `+1-234-567-8900`, `+1 234 567 8900`, or `+1(234)567-8900`.

:::tip
To convert an existing number, remove every character except the leading `+` and the digits, then confirm the country code is present.
:::

### Invalid Email Format

**Symptoms:**

-   `user cognito create` or `user cognito update` fails with an `Invalid User Data` error referencing the email format

**Cause:**

The address passed to `-e`/`--email` does not meet standard email format requirements.

**Resolution:**

Provide a standard `user@domain.com` address. Check for a missing `@`, a missing domain, stray spaces, or invalid characters.

```bash
vamscli user cognito create -u user@example.com -e user@example.com
vamscli user cognito create -u john.doe@company.com -e john.doe@company.com
```

---

## Permission and Confirmation Issues

### Access Forbidden

**Symptoms:**

-   User management commands fail with an access-forbidden or permission-denied message

**Cause:**

Your account lacks the permissions required for user management operations, which typically require administrative privileges.

**Resolution:**

1. Confirm you are authenticated:

    ```bash
    vamscli auth status
    ```

2. Re-authenticate if your session may have expired:

    ```bash
    vamscli auth login -u \{your-username\}
    ```

3. If you use an override token, ensure it carries the required permissions:

    ```bash
    vamscli auth set-override --token \{new-token\}
    ```

4. If the problem persists, ask your VAMS administrator to verify your role grants user management permissions.

### Missing Confirmation Flag

**Symptoms:**

-   `user cognito delete` or `user cognito reset-password` exits with a message that confirmation is required

**Cause:**

Destructive operations require the `--confirm` flag as a safeguard against accidental deletions and password resets.

**Resolution:**

Add `--confirm` to the command:

```bash
# Delete a user
vamscli user cognito delete -u user@example.com --confirm

# Reset a user's password
vamscli user cognito reset-password -u user@example.com --confirm
```

:::note
In interactive mode, `user cognito delete` also prompts for a final confirmation after the `--confirm` flag. In `--json-output` mode the interactive prompt is skipped, so `--confirm` alone authorizes the operation.
:::

---

## Debugging Tips

When a `user cognito` command behaves unexpectedly, the following steps help isolate the cause.

-   **Run in verbose mode** to see the underlying API request and response details, including the exact validation error returned:

    ```bash
    vamscli --verbose user cognito list
    ```

-   **Use JSON output** to inspect the precise field names and values in an API response:

    ```bash
    vamscli user cognito list --json-output
    ```

-   **Verify configuration and features** to rule out environment-level causes:

    ```bash
    vamscli auth status
    vamscli features list
    ```

:::info
Most user management failures fall into one of four patterns: the Amazon Cognito provider is not enabled (`features list` lacks `AUTHPROVIDER_COGNITO`), authentication or permissions (resolve with `auth status` and `auth login`), data validation (E.164 phone and standard email format), or a missing `--confirm` flag on a destructive command.
:::

---

## Related Pages

-   [User and API Key Commands](../commands/users-and-keys.md)
-   [Setup and Authentication Troubleshooting](./setup-auth.md)
-   [General CLI Troubleshooting](./general.md)
