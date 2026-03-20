# Authentication

This page documents the authentication and authorization endpoints in the VAMS API. These endpoints handle authentication configuration, route authorization, user login profiles, and user management.

For general authentication concepts, see the [API Overview](overview.md).

---

## Authentication Methods

VAMS supports three authentication methods:

### Cognito JWT

When Cognito is enabled (`app.authProvider.useCognito.enabled`), users authenticate through Amazon Cognito and receive JWT tokens. The ID token is sent in the `Authorization` header:

```
Authorization: Bearer eyJraWQiOiJ...
```

### External OAuth JWT

When an external OAuth identity provider is configured (`app.authProvider.useExternalOAuthIdp`), users authenticate through the external provider. The ID token format follows the same pattern:

```
Authorization: Bearer eyJraWQiOiJ...
```

### API Key

VAMS supports API key authentication for programmatic access. API keys are sent directly in the `Authorization` header:

```
Authorization: vams_ak_abc123...
```

:::info[Token Refresh]
When using Cognito or external OAuth, tokens expire after a configured period. The frontend client automatically refreshes tokens using the refresh token grant. API key tokens do not expire but can be revoked through the API key management endpoints.
:::


---

## Authorization Model

VAMS uses a two-tier authorization system enforced by a custom Lambda authorizer:

1. **Tier 1 (API-level)**: Controls which API routes a user's role can access.
2. **Tier 2 (Object-level)**: Controls which specific data entities (databases, assets, pipelines) a user can access.

Both tiers must allow the request for it to succeed.

---

## Endpoints

### Get Amplify Configuration

`GET /api/amplify-config`

Returns the client-side authentication and application configuration. This endpoint is **unauthenticated** and is used by the frontend to bootstrap the authentication flow.

:::note[No Authentication Required]
This endpoint does not require an `Authorization` header.
:::


**Request Parameters:**

None.

**Response:**

```json
{
    "Auth": {
        "Cognito": {
            "userPoolId": "us-east-1_AbCdEfGhI",
            "userPoolClientId": "1a2b3c4d5e6f7g8h9i0j",
            "identityPoolId": "us-east-1:12345678-abcd-efgh-ijkl-123456789012",
            "loginWith": {
                "email": true
            }
        }
    },
    "API": {
        "REST": {
            "vams": {
                "endpoint": "https://abc123.execute-api.us-east-1.amazonaws.com",
                "region": "us-east-1"
            }
        }
    },
    "Storage": {
        "S3": {
            "bucket": "vams-asset-bucket",
            "region": "us-east-1"
        }
    }
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `500` | Internal server error generating configuration. |

---

### Get API Version

`GET /api/version`

Returns the current VAMS version. This endpoint is **unauthenticated** and can be used for health checks or version verification.

:::note[No Authentication Required]
This endpoint does not require an `Authorization` header.
:::


**Request Parameters:**

None.

**Response:**

```json
{
    "version": "<current-version>"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `500` | Internal server error. |

---

### Get Runtime Configuration

`GET /secure-config`

Returns the runtime configuration for the authenticated user, including enabled feature flags and application settings. The frontend reads this endpoint at startup to determine which features to display.

**Request Parameters:**

None.

**Response:**

```json
{
    "featuresEnabled": [
        "CLOUDFRONTDEPLOY",
        "LOCATIONSERVICES",
        "AUTHPROVIDER_COGNITO"
    ],
    "config": {
        "region": "us-east-1"
    }
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `403` | Not authorized. |
| `500` | Internal server error. |

---

### Get Allowed Web Routes

`POST /auth/routes`

Returns the list of web application routes that the current user is authorized to access. The frontend uses this to conditionally render navigation items and gate route access.

**Request Body:**

```json
{
    "routes": [
        "/databases",
        "/assets",
        "/pipelines",
        "/workflows",
        "/admin/roles",
        "/admin/users"
    ]
}
```

**Response:**

```json
{
    "allowedRoutes": [
        "/databases",
        "/assets",
        "/pipelines",
        "/workflows"
    ]
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid request parameters. |
| `500` | Internal server error. |

---

### Get User Login Profile

`GET /auth/loginProfile/{userId}`

Retrieves the login profile for the specified user, including role assignments and constraint information.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `userId` | path | string | Yes | User identifier. Pattern: `^[\w\-\.\+\@]{3,256}$` |

**Response:**

```json
{
    "userId": "user@example.com",
    "roles": ["admin", "viewer"],
    "constraints": [ ... ]
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `403` | Not authorized to view this user's profile. |
| `500` | Internal server error. |

---

### Update User Login Profile

`POST /auth/loginProfile/{userId}`

Updates the login profile for a user. This is the primary endpoint for refreshing user profiles from JWT claims or organizational-specific logic. The request body is optional and may be overridden by organizational profile settings.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `userId` | path | string | Yes | User identifier. Pattern: `^[\w\-\.\+\@]{3,256}$` |

**Request Body:**

Optional. Body contents may be overridden by internal organizational profile logic.

**Response:**

```json
{
    "message": "Login profile updated"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `403` | Not authorized to update this user's profile. |
| `500` | Internal server error. |

---

## Cognito User Management Endpoints

These endpoints are only available when Cognito authentication is enabled (`app.authProvider.useCognito.enabled`). All endpoints return a `503` status when Cognito is not enabled.

### List Cognito Users

`GET /user/cognito`

Retrieves a paginated list of all users in the Cognito user pool.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `maxItems` | query | integer | No | Maximum number of users to return (1-60, default: 60). |
| `pageSize` | query | integer | No | Number of users per page (1-60, default: 60). |
| `startingToken` | query | string | No | Pagination token from a previous response. |

**Response:**

```json
{
    "users": [
        {
            "userId": "user@example.com",
            "email": "user@example.com",
            "phone": "+15551234567",
            "status": "CONFIRMED",
            "enabled": true,
            "mfaEnabled": false,
            "dateCreated": "2024-01-15T10:30:00Z",
            "dateModified": "2024-06-01T14:22:00Z"
        }
    ],
    "NextToken": "eyJ..."
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid pagination parameters. |
| `403` | Not authorized to list users. |
| `500` | Internal server error. |
| `503` | Cognito user management is not available. |

---

### Create Cognito User

`POST /user/cognito`

Creates a new user in the Cognito user pool. Cognito auto-generates a temporary password and sends a welcome email to the user.

**Request Body:**

```json
{
    "email": "newuser@example.com",
    "phone": "+15551234567"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `email` | string | Yes | User's email address (auto-verified). |
| `phone` | string | No | User's phone number in E.164 format. |

**Response:**

```json
{
    "message": "User created successfully",
    "userId": "newuser@example.com"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or user already exists. |
| `403` | Not authorized to create users. |
| `500` | Internal server error. |
| `503` | Cognito user management is not available. |

---

### Update Cognito User

`PUT /user/cognito/{userId}`

Updates an existing Cognito user's email and/or phone number. Updated attributes are automatically marked as verified.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `userId` | path | string | Yes | User ID (username) to update. |

**Request Body:**

```json
{
    "email": "updated@example.com",
    "phone": "+15559876543"
}
```

At least one field (`email` or `phone`) must be provided.

**Response:**

```json
{
    "message": "User updated successfully",
    "userId": "user@example.com"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or no fields provided. |
| `403` | Not authorized to update users. |
| `404` | User not found. |
| `500` | Internal server error. |
| `503` | Cognito user management is not available. |

---

### Delete Cognito User

`DELETE /user/cognito/{userId}`

Permanently deletes a user from the Cognito user pool.

:::danger[Irreversible Operation]
This operation cannot be undone. The user will be permanently removed from the Cognito user pool.
:::


**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `userId` | path | string | Yes | User ID (username) to delete. |

**Response:**

```json
{
    "message": "User deleted successfully",
    "userId": "user@example.com"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid userId parameter. |
| `403` | Not authorized to delete users. |
| `404` | User not found. |
| `500` | Internal server error. |
| `503` | Cognito user management is not available. |

---

### Reset Cognito User Password

`POST /user/cognito/{userId}/resetPassword`

Resets a user's password using Cognito's built-in password reset. Cognito auto-generates a new temporary password and sends it to the user's email. The user must change the password on next login.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `userId` | path | string | Yes | User ID (username) to reset password for. |

**Request Body:**

```json
{
    "confirmed": true
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `confirmed` | boolean | No | Confirmation flag for the reset operation. |

**Response:**

```json
{
    "message": "Password reset successfully",
    "userId": "user@example.com"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or confirmation not provided. |
| `403` | Not authorized to reset passwords. |
| `404` | User not found. |
| `500` | Internal server error. |
| `503` | Cognito user management is not available. |

---

## API Key Management Endpoints

These endpoints manage API keys for programmatic access to VAMS.

### List API Keys

`GET /auth/api-keys`

Retrieves all API keys for the current user, or all API keys if the user has admin permissions.

**Request Parameters:**

None.

**Response:**

```json
{
    "apiKeys": [
        {
            "apiKeyId": "ak-12345678",
            "userId": "user@example.com",
            "name": "My API Key",
            "enabled": true,
            "dateCreated": "2024-01-15T10:30:00Z",
            "expiresAt": "2025-01-15T10:30:00Z"
        }
    ]
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `403` | Not authorized to list API keys. |
| `500` | Internal server error. |

---

### Create API Key

`POST /auth/api-keys`

Creates a new API key for programmatic access.

**Request Body:**

```json
{
    "name": "My Integration Key",
    "expiresInDays": 365
}
```

**Response:**

```json
{
    "apiKeyId": "ak-12345678",
    "apiKey": "vams_ak_abc123...",
    "name": "My Integration Key",
    "message": "API key created. Store the key securely -- it will not be shown again."
}
```

:::warning[Store the API Key Securely]
The full API key value is only returned once at creation time. It cannot be retrieved again.
:::


**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized to create API keys. |
| `500` | Internal server error. |

---

### Get API Key

`GET /auth/api-keys/{apiKeyId}`

Retrieves details of a specific API key. The full key value is not returned.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `apiKeyId` | path | string | Yes | The API key identifier. |

**Response:**

```json
{
    "apiKeyId": "ak-12345678",
    "userId": "user@example.com",
    "name": "My API Key",
    "enabled": true,
    "dateCreated": "2024-01-15T10:30:00Z",
    "expiresAt": "2025-01-15T10:30:00Z"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `403` | Not authorized to view this API key. |
| `404` | API key not found. |
| `500` | Internal server error. |

---

### Update API Key

`PUT /auth/api-keys/{apiKeyId}`

Updates an API key's properties such as name or enabled status.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `apiKeyId` | path | string | Yes | The API key identifier. |

**Request Body:**

```json
{
    "name": "Updated Key Name",
    "enabled": false
}
```

**Response:**

```json
{
    "message": "API key updated successfully",
    "apiKeyId": "ak-12345678"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized to update this API key. |
| `404` | API key not found. |
| `500` | Internal server error. |

---

### Delete API Key

`DELETE /auth/api-keys/{apiKeyId}`

Permanently deletes an API key, revoking all access associated with it.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `apiKeyId` | path | string | Yes | The API key identifier. |

**Response:**

```json
{
    "message": "API key deleted successfully",
    "apiKeyId": "ak-12345678"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized to delete this API key. |
| `404` | API key not found. |
| `500` | Internal server error. |
