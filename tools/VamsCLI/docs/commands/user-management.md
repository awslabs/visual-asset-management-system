# User Management Commands

This guide covers VamsCLI commands for managing Cognito users in VAMS.

## Table of Contents

-   [Overview](#overview)
-   [Prerequisites](#prerequisites)
-   [Command Structure](#command-structure)
-   [Commands](#commands)
    -   [List Users](#list-users)
    -   [Create User](#create-user)
    -   [Update User](#update-user)
    -   [Delete User](#delete-user)
    -   [Reset Password](#reset-password)
-   [Common Patterns](#common-patterns)
-   [Examples](#examples)

## Overview

The `user cognito` command group provides functionality for managing Cognito users in your VAMS deployment. These commands allow administrators to:

-   List all users in the Cognito user pool
-   Create new users with email and optional phone
-   Update user email addresses and phone numbers
-   Delete users from the user pool
-   Reset user passwords

**Important Notes:**

-   These commands require Cognito authentication to be enabled in your VAMS deployment
-   All commands require proper authentication and authorization
-   Password operations use Cognito's built-in password management
-   Phone numbers must be in E.164 format (e.g., +12345678900)

## Prerequisites

Before using user management commands, ensure:

1. **VamsCLI is configured**: Run `vamscli setup <api-gateway-url>`
2. **You are authenticated**: Run `vamscli auth login -u <username>`
3. **Cognito is enabled**: Your VAMS deployment must have Cognito authentication enabled
4. **You have permissions**: Your user must have admin permissions for user management

## Command Structure

```
vamscli user cognito <command> [options]
```

All user cognito commands support:

-   `--json-output`: Output raw JSON response for programmatic use
-   `--profile <name>`: Use a specific profile (default: "default")
-   `--verbose`: Enable detailed logging

## Commands

### List Users

List all Cognito users in the user pool with pagination support.

```bash
vamscli user cognito list [options]
```

**Options:**

-   `--page-size <number>`: Number of items per page
-   `--max-items <number>`: Maximum total items to fetch (only with --auto-paginate, default: 10000)
-   `--starting-token <token>`: Token for manual pagination
-   `--auto-paginate`: Automatically fetch all items
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Basic listing (uses API defaults)
vamscli user cognito list

# Auto-pagination to fetch all users
vamscli user cognito list --auto-paginate

# Auto-pagination with custom limit
vamscli user cognito list --auto-paginate --max-items 5000

# Manual pagination
vamscli user cognito list --page-size 50
vamscli user cognito list --starting-token "token123" --page-size 50

# JSON output for scripting
vamscli user cognito list --json-output
```

**Response Fields:**

-   `userId`: User's unique identifier (email)
-   `email`: User's email address
-   `phone`: User's phone number (if set)
-   `userStatus`: User status (CONFIRMED, FORCE_CHANGE_PASSWORD, etc.)
-   `enabled`: Whether user account is enabled
-   `mfaEnabled`: Whether MFA is enabled for user
-   `userCreateDate`: When user was created
-   `userLastModifiedDate`: When user was last modified

### Create User

Create a new Cognito user with email and optional phone number.

```bash
vamscli user cognito create -u <user-id> -e <email> [options]
```

**Required Options:**

-   `-u, --user-id <email>`: User ID (must be email format)
-   `-e, --email <email>`: Email address

**Optional Options:**

-   `-p, --phone <phone>`: Phone number in E.164 format (e.g., +12345678900)
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Create user with email only
vamscli user cognito create -u user@example.com -e user@example.com

# Create user with email and phone
vamscli user cognito create -u user@example.com -e user@example.com -p +12345678900

# JSON output for scripting
vamscli user cognito create -u user@example.com -e user@example.com --json-output
```

**Important Notes:**

-   Cognito automatically generates a temporary password
-   The temporary password is returned in the response
-   Users must change their password on first login
-   Email and phone are automatically verified by Cognito

**Response Fields:**

-   `success`: Operation success status
-   `message`: Operation result message
-   `userId`: Created user's ID
-   `operation`: "create"
-   `timestamp`: Operation timestamp

### Update User

Update a Cognito user's email address and/or phone number.

```bash
vamscli user cognito update -u <user-id> [options]
```

**Required Options:**

-   `-u, --user-id <email>`: User ID to update

**Update Options (at least one required):**

-   `-e, --email <email>`: New email address
-   `-p, --phone <phone>`: New phone number in E.164 format

**Other Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Update email only
vamscli user cognito update -u user@example.com -e newemail@example.com

# Update phone only
vamscli user cognito update -u user@example.com -p +12345678900

# Update both email and phone
vamscli user cognito update -u user@example.com -e newemail@example.com -p +12345678900

# JSON output
vamscli user cognito update -u user@example.com -e newemail@example.com --json-output
```

**Important Notes:**

-   At least one field (email or phone) must be provided
-   Updated email and phone are automatically verified by Cognito
-   User can continue using their existing password

### Delete User

Permanently delete a Cognito user from the user pool.

```bash
vamscli user cognito delete -u <user-id> --confirm
```

**Required Options:**

-   `-u, --user-id <email>`: User ID to delete
-   `--confirm`: Confirmation flag (required for safety)

**Other Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Delete user (requires confirmation)
vamscli user cognito delete -u user@example.com --confirm

# JSON output
vamscli user cognito delete -u user@example.com --confirm --json-output
```

**Important Notes:**

-   ⚠️ **This action is permanent and cannot be undone**
-   The `--confirm` flag is required to prevent accidental deletions
-   An additional confirmation prompt will appear for safety
-   All user data and sessions will be permanently removed

### Reset Password

Reset a Cognito user's password, generating a new temporary password.

```bash
vamscli user cognito reset-password -u <user-id> --confirm
```

**Required Options:**

-   `-u, --user-id <email>`: User ID to reset password for
-   `--confirm`: Confirmation flag (required for safety)

**Other Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Reset user password
vamscli user cognito reset-password -u user@example.com --confirm

# JSON output
vamscli user cognito reset-password -u user@example.com --confirm --json-output
```

**Important Notes:**

-   Cognito automatically generates a new temporary password
-   The temporary password is returned in the response
-   User must change the temporary password on next login
-   The `--confirm` flag is required to prevent accidental resets

**Response Fields:**

-   `success`: Operation success status
-   `message`: Operation result message
-   `userId`: User's ID
-   `operation`: "resetPassword"
-   `timestamp`: Operation timestamp

## Common Patterns

### Auto-Pagination for Large User Lists

When you have many users, use auto-pagination to fetch all users efficiently:

```bash
# Fetch all users (up to 10,000 by default)
vamscli user cognito list --auto-paginate

# Fetch all users with custom limit
vamscli user cognito list --auto-paginate --max-items 5000

# Fetch all users with custom page size
vamscli user cognito list --auto-paginate --page-size 50
```

### Manual Pagination for Controlled Fetching

For more control over pagination:

```bash
# Get first page
vamscli user cognito list --page-size 50

# Get next page using token from previous response
vamscli user cognito list --page-size 50 --starting-token "token-from-previous-response"
```

### JSON Output for Scripting

Use `--json-output` for programmatic access:

```bash
# List users and parse with jq
vamscli user cognito list --json-output | jq '.Items[] | {userId, email, status: .userStatus}'


# Check if user exists
vamscli user cognito list --json-output | jq '.Items[] | select(.userId == "user@example.com")'
```

### Bulk User Operations

Create multiple users using a script:

```bash
# Create users from a list
for email in user1@example.com user2@example.com user3@example.com; do
    vamscli user cognito create -u "$email" -e "$email"
done

# Update multiple users
for user in $(vamscli user cognito list --json-output | jq -r '.Items[].userId'); do
    vamscli user cognito update -u "$user" -p "+12345678900"
done
```

### Phone Number Format

Phone numbers must be in E.164 format:

```bash
# Correct formats
vamscli user cognito create -u user@example.com -e user@example.com -p +12345678900  # US
vamscli user cognito create -u user@example.com -e user@example.com -p +442071234567  # UK
vamscli user cognito create -u user@example.com -e user@example.com -p +81312345678  # Japan

# Incorrect formats (will fail validation)
vamscli user cognito create -u user@example.com -e user@example.com -p 1234567890  # Missing +
vamscli user cognito create -u user@example.com -e user@example.com -p +1-234-567-8900  # Has dashes
```

## Examples

### Example 1: Create and Configure New User

```bash
# Create user
vamscli user cognito create -u newuser@example.com -e newuser@example.com -p +12345678900

# Output will include temporary password:
# ✓ Cognito user created successfully!
#   User ID: newuser@example.com
#   Operation: create
#   Message: User created successfully
#   Temporary Password: TempPass123!
#   ⚠️  User must change password on first login
```

### Example 2: List and Filter Users

```bash
# List all users with auto-pagination
vamscli user cognito list --auto-paginate

# List users and filter by status using jq
vamscli user cognito list --json-output | jq '.Items[] | select(.userStatus == "CONFIRMED")'

# Count total users
vamscli user cognito list --auto-paginate --json-output | jq '.totalItems'
```

### Example 3: Update User Contact Information

```bash
# Update email
vamscli user cognito update -u user@example.com -e newemail@example.com

# Update phone
vamscli user cognito update -u user@example.com -p +19876543210

# Update both
vamscli user cognito update -u user@example.com -e newemail@example.com -p +19876543210
```

### Example 4: Reset User Password

```bash
# Reset password with confirmation
vamscli user cognito reset-password -u user@example.com --confirm

# You will be prompted:
# ⚠️  You are about to delete user 'user@example.com'
# This action cannot be undone!
# Are you sure you want to proceed? [y/N]: y

# Output will include new temporary password:
# ✓ Password reset successfully!
#   User ID: user@example.com
#   Operation: resetPassword
#   Message: Password reset successfully
#   Temporary Password: NewTempPass456!
#   ⚠️  User must change password on next login
```

### Example 5: Delete User

```bash
# Delete user with confirmation
vamscli user cognito delete -u user@example.com --confirm

# You will be prompted:
# ⚠️  You are about to delete user 'user@example.com'
# This action cannot be undone!
# Are you sure you want to proceed? [y/N]: y

# Output:
# ✓ Cognito user deleted successfully!
#   User ID: user@example.com
#   Operation: delete
#   Message: User deleted successfully
```

### Example 6: Scripted User Management

```bash
#!/bin/bash
# Script to create multiple users from a CSV file

# CSV format: email,phone
# user1@example.com,+12345678900
# user2@example.com,+12345678901

while IFS=',' read -r email phone; do
    echo "Creating user: $email"

    if [ -n "$phone" ]; then
        result=$(vamscli user cognito create -u "$email" -e "$email" -p "$phone" --json-output)
    else
        result=$(vamscli user cognito create -u "$email" -e "$email" --json-output)
    fi


    echo "Created: $email (temp password saved)"
done < users.csv

echo "All users created. Passwords saved to user_passwords.txt"
```

## Related Commands

-   [`vamscli auth`](setup-auth.md#authentication-commands) - Authentication commands
-   [`vamscli features list`](setup-auth.md#list-features) - Check if Cognito is enabled
-   [`vamscli profile`](setup-auth.md#profile-commands) - Profile management

## See Also

-   [Troubleshooting User Issues](../troubleshooting/user-issues.md)
-   [Setup and Authentication Guide](setup-auth.md)
-   [Global Options](global-options.md)
