# Troubleshooting User Management Issues

This guide helps resolve common issues with Cognito user management commands.

## Table of Contents

-   [Common Issues](#common-issues)
    -   [Cognito Not Enabled](#cognito-not-enabled)
    -   [User Not Found](#user-not-found)
    -   [User Already Exists](#user-already-exists)
    -   [Invalid Phone Number Format](#invalid-phone-number-format)
    -   [Invalid Email Format](#invalid-email-format)
    -   [Permission Denied](#permission-denied)
    -   [Missing Confirmation Flag](#missing-confirmation-flag)
-   [Debugging Tips](#debugging-tips)
-   [Getting Help](#getting-help)

## Common Issues

### Cognito Not Enabled

**Error Message:**

```
✗ Cognito Operation Error: Cognito not enabled
Cognito authentication provider is not enabled
```

**Cause:**
Your VAMS deployment does not have Cognito authentication enabled.

**Solution:**

1. Check if Cognito is enabled in your deployment:

    ```bash
    vamscli features list
    ```

    Look for `AUTHPROVIDER_COGNITO` in the enabled features list.

2. If Cognito is not enabled, contact your VAMS administrator to enable it in the deployment configuration.

3. Verify your VAMS deployment was configured with Cognito:
    ```bash
    vamscli auth status
    ```
    Check the authentication provider information.

**Related Commands:**

-   `vamscli features list` - Check enabled features
-   `vamscli auth status` - Check authentication configuration

---

### User Not Found

**Error Message:**

```
✗ User Not Found: User 'user@example.com' not found
```

**Cause:**
The specified user does not exist in the Cognito user pool.

**Solution:**

1. List all users to verify the user ID:

    ```bash
    vamscli user cognito list
    ```

2. Check for typos in the user ID (user IDs are case-sensitive)

3. If using auto-pagination, ensure you're fetching all users:

    ```bash
    vamscli user cognito list --auto-paginate
    ```

4. Verify you're using the correct profile:
    ```bash
    vamscli --profile <profile-name> user cognito list
    ```

**Related Commands:**

-   `vamscli user cognito list` - List all users
-   `vamscli profile list` - List available profiles

---

### User Already Exists

**Error Message:**

```
✗ User Already Exists: User already exists
```

**Cause:**
A user with the specified user ID already exists in the Cognito user pool.

**Solution:**

1. List existing users to confirm:

    ```bash
    vamscli user cognito list
    ```

2. If you need to update the existing user instead:

    ```bash
    vamscli user cognito update -u user@example.com -e newemail@example.com
    ```

3. If you need to replace the user, delete the existing user first:
    ```bash
    vamscli user cognito delete -u user@example.com --confirm
    vamscli user cognito create -u user@example.com -e user@example.com
    ```

**Related Commands:**

-   `vamscli user cognito list` - List existing users
-   `vamscli user cognito update` - Update existing user
-   `vamscli user cognito delete` - Delete existing user

---

### Invalid Phone Number Format

**Error Message:**

```
✗ Invalid User Data: phone must be in E.164 format (e.g., +12345678900)
```

**Cause:**
The phone number is not in the required E.164 international format.

**Solution:**

E.164 format requirements:

-   Must start with `+`
-   Followed by country code (1-3 digits)
-   Followed by subscriber number (up to 15 total digits)
-   No spaces, dashes, or parentheses

**Correct Examples:**

```bash
# United States (+1)
vamscli user cognito create -u user@example.com -e user@example.com -p +12345678900

# United Kingdom (+44)
vamscli user cognito create -u user@example.com -e user@example.com -p +442071234567

# Japan (+81)
vamscli user cognito create -u user@example.com -e user@example.com -p +81312345678

# Germany (+49)
vamscli user cognito create -u user@example.com -e user@example.com -p +4930123456
```

**Incorrect Examples:**

```bash
# Missing + prefix
vamscli user cognito create -u user@example.com -e user@example.com -p 12345678900

# Contains dashes
vamscli user cognito create -u user@example.com -e user@example.com -p +1-234-567-8900

# Contains spaces
vamscli user cognito create -u user@example.com -e user@example.com -p "+1 234 567 8900"

# Contains parentheses
vamscli user cognito create -u user@example.com -e user@example.com -p +1(234)567-8900
```

**Phone Number Conversion:**
If you have phone numbers in other formats, convert them:

-   Remove all non-digit characters except the leading `+`
-   Ensure country code is included
-   Verify total length is 11-16 characters (including `+`)

---

### Invalid Email Format

**Error Message:**

```
✗ Invalid User Data: Invalid email format
```

**Cause:**
The email address does not meet validation requirements.

**Solution:**

1. Ensure email follows standard format: `user@domain.com`

2. Check for common issues:

    - Missing `@` symbol
    - Missing domain
    - Invalid characters
    - Extra spaces

3. Verify email length (3-256 characters)

**Correct Examples:**

```bash
vamscli user cognito create -u user@example.com -e user@example.com
vamscli user cognito create -u john.doe@company.com -e john.doe@company.com
vamscli user cognito create -u admin+test@example.org -e admin+test@example.org
```

---

### Permission Denied

**Error Message:**

```
✗ Access forbidden. You do not have permission to perform this action.
```

**Cause:**
Your user account does not have the required permissions for user management operations.

**Solution:**

1. Verify you're authenticated:

    ```bash
    vamscli auth status
    ```

2. Check your user's permissions with your VAMS administrator

3. User management operations typically require admin-level permissions

4. Try re-authenticating:

    ```bash
    vamscli auth login -u <your-username>
    ```

5. If using an override token, ensure it has the correct permissions:
    ```bash
    vamscli auth set-override --token <new-token>
    ```

**Related Commands:**

-   `vamscli auth status` - Check authentication status
-   `vamscli auth login` - Re-authenticate

---

### Missing Confirmation Flag

**Error Message:**

```
✗ Confirmation required for user deletion
User deletion requires explicit confirmation!
```

or

```
✗ Confirmation required for password reset
Password reset requires explicit confirmation!
```

**Cause:**
Destructive operations (delete, reset-password) require the `--confirm` flag for safety.

**Solution:**

Add the `--confirm` flag to your command:

```bash
# For delete operations
vamscli user cognito delete -u user@example.com --confirm

# For password reset operations
vamscli user cognito reset-password -u user@example.com --confirm
```

**Why This Is Required:**

-   Prevents accidental user deletions
-   Prevents accidental password resets
-   Provides an additional confirmation prompt for safety
-   Follows security best practices

---

## Debugging Tips

### Enable Verbose Mode

Get detailed information about API calls and responses:

```bash
vamscli --verbose user cognito list
```

Verbose mode shows:

-   API request details (method, URL, headers)
-   API response details (status code, body)
-   Request timing information
-   Detailed error messages

### Check Feature Switches

Verify Cognito is enabled:

```bash
vamscli features list
```

Look for `AUTHPROVIDER_COGNITO` in the output.

### Verify Authentication

Check your authentication status:

```bash
vamscli auth status
```

This shows:

-   Current profile
-   Authentication status
-   User ID
-   Token expiration
-   Enabled features

### Test with JSON Output

Use JSON output to see exact API responses:

```bash
vamscli user cognito list --json-output | jq '.'
```

This helps identify:

-   Exact field names and values
-   Response structure
-   Error details

### Check API Version

Ensure your VAMS API version supports user management:

```bash
vamscli setup --check-version
```

User management commands require VAMS version 2.2 or higher.

### Review Logs

Check VamsCLI logs for detailed error information:

**Linux/macOS:**

```bash
cat ~/.config/vamscli/logs/vamscli.log
```

**Windows:**

```powershell
type $env:APPDATA\vamscli\logs\vamscli.log
```

Logs include:

-   All API requests and responses
-   Authentication attempts
-   Error stack traces
-   Timing information

## Getting Help

If you continue to experience issues:

1. **Check Documentation:**

    - [User Management Commands](../commands/user-management.md)
    - [Setup and Authentication](../commands/setup-auth.md)
    - [General Troubleshooting](general-troubleshooting.md)

2. **Enable Verbose Mode:**

    ```bash
    vamscli --verbose user cognito <command>
    ```

3. **Check Logs:**
   Review the VamsCLI log file for detailed error information

4. **Verify Configuration:**

    ```bash
    vamscli auth status
    vamscli features list
    ```

5. **Contact Support:**
    - Provide the output from `vamscli --verbose` commands
    - Include relevant log file excerpts
    - Describe the expected vs. actual behavior

## Common Error Patterns

### Pattern 1: Setup/Authentication Issues

If you see errors about configuration or authentication:

```bash
# Check setup
vamscli auth status

# Re-authenticate if needed
vamscli auth login -u <username>

# Verify API connectivity
vamscli setup --check-version
```

### Pattern 2: Feature Not Available

If commands fail with "Cognito not enabled":

```bash
# Check enabled features
vamscli features list

# Verify with administrator that Cognito is configured
# Contact VAMS administrator to enable Cognito if needed
```

### Pattern 3: Data Validation Errors

If you see validation errors:

```bash
# For phone numbers: Use E.164 format (+12345678900)
# For emails: Use standard email format (user@domain.com)
# For user IDs: Use email format

# Test with verbose mode to see exact validation errors
vamscli --verbose user cognito create -u user@example.com -e user@example.com
```

### Pattern 4: Permission Issues

If you see "Access forbidden" or "Permission denied":

```bash
# Check your authentication
vamscli auth status

# Re-authenticate
vamscli auth login -u <username>

# Contact administrator to verify your permissions
```

## Related Documentation

-   [User Management Commands](../commands/user-management.md)
-   [Setup and Authentication Issues](setup-auth-issues.md)
-   [General Troubleshooting](general-troubleshooting.md)
-   [Network and Configuration Issues](network-config-issues.md)
