# Authorization API

The Authorization API provides endpoints for managing permission constraints, roles, user-role assignments, Cognito user management, and API keys. These resources control access to all VAMS functionality through a two-tier Casbin ABAC/RBAC authorization model.

:::info[Two-tier authorization]
VAMS enforces authorization at two levels:

- **Tier 1 (API-level)**: Controls which API routes a role can access.
- **Tier 2 (Object-level)**: Controls which data entities (databases, assets, pipelines, etc.) a role can access.

Both tiers must allow access for a request to succeed.
:::


---

## Constraints

Constraints define the authorization policies that determine what actions users and groups can perform on specific resource types.

### List constraints

Retrieves all permission constraints.

```
GET /auth/constraints
```

#### Response

```json
{
  "message": {
    "Items": [
      {
        "constraintId": "constraint-abc123#group#admin-role",
        "name": "Admin Full Access",
        "description": "Full access to all resources",
        "objectType": "asset",
        "criteriaAnd": "[{\"field\": \"databaseId\", \"value\": \"*\", \"operator\": \"equals\"}]",
        "criteriaOr": "[]",
        "groupPermissions": "[{\"groupId\": \"admin-role\", \"permission\": \"Read/Write\", \"permissionType\": \"allow\"}]",
        "userPermissions": "[]",
        "dateCreated": "2026-03-15T10:30:00",
        "dateModified": "2026-03-15T10:30:00"
      }
    ]
  }
}
```

#### Error responses

| Status | Description                     |
|--------|---------------------------------|
| `403`  | Not authorized                  |
| `500`  | Internal server error           |

---

### Get a constraint

Retrieves a specific constraint by ID.

```
GET /auth/constraints/{constraintId}
```

#### Path parameters

| Parameter      | Type   | Required | Description              |
|---------------|--------|----------|--------------------------|
| `constraintId` | string | Yes      | Constraint identifier    |

---

### Create a constraint

Creates a new permission constraint.

```
POST /auth/constraints/{constraintId}
```

#### Path parameters

| Parameter      | Type   | Required | Description                     |
|---------------|--------|----------|---------------------------------|
| `constraintId` | string | Yes      | Unique constraint identifier    |

#### Request body

| Field              | Type   | Required | Description                                                           |
|--------------------|--------|----------|-----------------------------------------------------------------------|
| `name`             | string | Yes      | Human-readable name for the constraint                                |
| `description`      | string | No       | Description of the constraint's purpose                               |
| `objectType`       | string | Yes      | Resource type this constraint applies to (e.g., `asset`, `database`, `pipeline`, `workflow`, `api`, `web`, `tag`, `tagType`, `role`, `userRole`, `metadataSchema`, `apiKey`) |
| `criteriaAnd`      | array  | No       | AND criteria for matching resources                                   |
| `criteriaOr`       | array  | No       | OR criteria for matching resources                                    |
| `groupPermissions` | array  | Yes      | Permissions granted to roles/groups                                   |
| `userPermissions`  | array  | No       | Permissions granted to specific users                                 |

Each entry in `groupPermissions`:

| Field            | Type   | Required | Description                                               |
|-----------------|--------|----------|-----------------------------------------------------------|
| `groupId`        | string | Yes      | Role/group name                                           |
| `permission`     | string | Yes      | Permission level (`Read`, `Read/Write`)                   |
| `permissionType` | string | Yes      | `allow` or `deny`                                         |

Each entry in `criteriaAnd` or `criteriaOr`:

| Field      | Type   | Required | Description                                      |
|-----------|--------|----------|--------------------------------------------------|
| `field`    | string | Yes      | Field to match (e.g., `databaseId`, `assetType`) |
| `value`    | string | Yes      | Value to match against (supports `*` wildcard)   |
| `operator` | string | Yes      | Comparison operator (`equals`, `contains`, etc.) |

#### Request body example

```json
{
  "name": "Database Reader",
  "description": "Read-only access to assets in the production database",
  "objectType": "asset",
  "criteriaAnd": [
    {
      "field": "databaseId",
      "value": "production-db",
      "operator": "equals"
    }
  ],
  "criteriaOr": [],
  "groupPermissions": [
    {
      "groupId": "viewer-role",
      "permission": "Read",
      "permissionType": "allow"
    }
  ],
  "userPermissions": []
}
```

#### Response

```json
{
  "message": "Constraint created successfully"
}
```

---

### Update a constraint

Updates an existing constraint.

```
PUT /auth/constraints/{constraintId}
```

#### Path parameters

| Parameter      | Type   | Required | Description              |
|---------------|--------|----------|--------------------------|
| `constraintId` | string | Yes      | Constraint identifier    |

#### Request body

Same structure as [Create a constraint](#create-a-constraint).

---

### Delete a constraint

Deletes a permission constraint.

```
DELETE /auth/constraints/{constraintId}
```

#### Path parameters

| Parameter      | Type   | Required | Description              |
|---------------|--------|----------|--------------------------|
| `constraintId` | string | Yes      | Constraint identifier    |

#### Response

```json
{
  "message": "Constraint deleted successfully"
}
```

---

### Import constraint template

Imports a constraint configuration from a JSON template file. This is useful for bulk-provisioning permissions.

```
POST /auth/constraintsTemplateImport
```

#### Request body

The request body should contain the full constraint template JSON. See the permission templates in `documentation/permissionsTemplates/` for examples.

#### Response

```json
{
  "message": "Template imported successfully"
}
```

---

## Roles

Roles are named groups that can be assigned to users. Constraints reference roles via `groupPermissions` to grant or deny access.

### List roles

Retrieves all roles.

```
GET /roles
```

#### Response

```json
{
  "message": {
    "Items": [
      {
        "roleName": "admin",
        "description": "Full administrative access",
        "dateCreated": "2026-03-15T10:30:00"
      }
    ]
  }
}
```

---

### Create a role

Creates a new role.

```
POST /roles
```

#### Request body

| Field         | Type   | Required | Description                     |
|--------------|--------|----------|---------------------------------|
| `roleName`    | string | Yes      | Unique role name                |
| `description` | string | No       | Role description                |

#### Request body example

```json
{
  "roleName": "viewer",
  "description": "Read-only access to assets and databases"
}
```

#### Response

```json
{
  "message": "Role created successfully"
}
```

---

### Update a role

Updates an existing role.

```
PUT /roles
```

#### Request body

Same structure as [Create a role](#create-a-role).

---

### Delete a role

Deletes a role.

```
DELETE /roles/{roleId}
```

#### Path parameters

| Parameter | Type   | Required | Description              |
|----------|--------|----------|--------------------------|
| `roleId`  | string | Yes      | Role name to delete      |

:::warning
Deleting a role does not automatically remove user-role assignments referencing this role. Clean up user-role assignments before or after deleting the role.
:::


#### Response

```json
{
  "message": "Role deleted successfully"
}
```

---

## User-role assignments

User-role assignments link users to roles. A user can have multiple roles, and each role's constraints combine to determine the user's effective permissions.

### List user-role assignments

Retrieves all user-role assignments.

```
GET /user-roles
```

#### Response

```json
{
  "message": {
    "Items": [
      {
        "userId": "user@example.com",
        "roleName": "admin"
      }
    ]
  }
}
```

---

### Assign a role to a user

Creates a new user-role assignment.

```
POST /user-roles
```

#### Request body

| Field      | Type   | Required | Description              |
|-----------|--------|----------|--------------------------|
| `userId`   | string | Yes      | User identifier          |
| `roleName` | string | Yes      | Role name to assign      |

#### Request body example

```json
{
  "userId": "user@example.com",
  "roleName": "viewer"
}
```

#### Response

```json
{
  "message": "User role assignment created successfully"
}
```

---

### Update a user-role assignment

Updates an existing user-role assignment.

```
PUT /user-roles
```

#### Request body

Same structure as [Assign a role to a user](#assign-a-role-to-a-user).

---

### Remove a role from a user

Removes a user-role assignment.

```
DELETE /user-roles
```

#### Request body

| Field      | Type   | Required | Description                     |
|-----------|--------|----------|---------------------------------|
| `userId`   | string | Yes      | User identifier                 |
| `roleName` | string | Yes      | Role name to remove             |

#### Request body example

```json
{
  "userId": "user@example.com",
  "roleName": "viewer"
}
```

#### Response

```json
{
  "message": "User role assignment deleted successfully"
}
```

---

## Cognito user management

These endpoints manage users in the Amazon Cognito user pool. They are only available when Cognito authentication is enabled in the deployment.

:::note[Cognito required]
These endpoints return an error if Cognito is not enabled in the deployment configuration (`app.authProvider.useCognito.enabled`).
:::


### List Cognito users

```
GET /user/cognito
```

#### Response

```json
{
  "message": {
    "users": [
      {
        "userId": "user@example.com",
        "email": "user@example.com",
        "status": "CONFIRMED",
        "enabled": true,
        "dateCreated": "2026-03-15T10:30:00Z"
      }
    ]
  }
}
```

---

### Create a Cognito user

```
POST /user/cognito
```

#### Request body

| Field      | Type   | Required | Description                     |
|-----------|--------|----------|---------------------------------|
| `userId`   | string | Yes      | Username for the new user       |
| `email`    | string | Yes      | Email address                   |

#### Request body example

```json
{
  "userId": "newuser@example.com",
  "email": "newuser@example.com"
}
```

---

### Update a Cognito user

```
PUT /user/cognito/{userId}
```

#### Path parameters

| Parameter | Type   | Required | Description              |
|----------|--------|----------|--------------------------|
| `userId`  | string | Yes      | User identifier          |

#### Request body

| Field    | Type    | Required | Description                     |
|---------|---------|----------|---------------------------------|
| `email`  | string  | No       | Updated email address           |
| `enabled`| boolean | No       | Enable or disable the user      |

---

### Delete a Cognito user

```
DELETE /user/cognito/{userId}
```

#### Path parameters

| Parameter | Type   | Required | Description              |
|----------|--------|----------|--------------------------|
| `userId`  | string | Yes      | User identifier to delete|

---

### Reset a user's password

Sends a password reset to the specified Cognito user.

```
POST /user/cognito/{userId}/resetPassword
```

#### Path parameters

| Parameter | Type   | Required | Description              |
|----------|--------|----------|--------------------------|
| `userId`  | string | Yes      | User identifier          |

#### Response

```json
{
  "message": "Password reset initiated successfully"
}
```

---

## API keys

API keys provide programmatic access to VAMS without requiring interactive authentication.

### List API keys

```
GET /auth/api-keys
```

#### Response

```json
{
  "message": {
    "Items": [
      {
        "apiKeyId": "key-abc123",
        "name": "CI/CD Pipeline Key",
        "userId": "service-account@example.com",
        "enabled": true,
        "dateCreated": "2026-03-15T10:30:00Z",
        "expiresAt": "2027-03-15T10:30:00Z"
      }
    ]
  }
}
```

:::note
The API key secret value is only returned once during creation and cannot be retrieved afterwards.
:::


---

### Get a specific API key

```
GET /auth/api-keys/{apiKeyId}
```

#### Path parameters

| Parameter  | Type   | Required | Description              |
|-----------|--------|----------|--------------------------|
| `apiKeyId` | string | Yes      | API key identifier       |

---

### Create an API key

```
POST /auth/api-keys
```

#### Request body

| Field      | Type   | Required | Description                     |
|-----------|--------|----------|---------------------------------|
| `name`     | string | Yes      | Display name for the API key    |
| `userId`   | string | Yes      | User to associate the key with  |
| `expiresAt`| string | No       | Expiration date (ISO 8601)      |

#### Request body example

```json
{
  "name": "CI/CD Pipeline Key",
  "userId": "service-account@example.com",
  "expiresAt": "2027-03-15T10:30:00Z"
}
```

#### Response

```json
{
  "message": {
    "apiKeyId": "key-abc123",
    "apiKeySecret": "vams_ak_xxxxxxxxxxxxxxxxxxxxxxxxxx",
    "name": "CI/CD Pipeline Key",
    "userId": "service-account@example.com"
  }
}
```

:::warning[Store the secret securely]
The `apiKeySecret` value is only returned during creation. Store it securely -- it cannot be retrieved again.
:::


---

### Update an API key

```
PUT /auth/api-keys/{apiKeyId}
```

#### Path parameters

| Parameter  | Type   | Required | Description              |
|-----------|--------|----------|--------------------------|
| `apiKeyId` | string | Yes      | API key identifier       |

#### Request body

| Field      | Type    | Required | Description                     |
|-----------|---------|----------|---------------------------------|
| `name`     | string  | No       | Updated display name            |
| `enabled`  | boolean | No       | Enable or disable the key       |
| `expiresAt`| string  | No       | Updated expiration date         |

---

### Delete an API key

```
DELETE /auth/api-keys/{apiKeyId}
```

#### Path parameters

| Parameter  | Type   | Required | Description              |
|-----------|--------|----------|--------------------------|
| `apiKeyId` | string | Yes      | API key identifier       |

#### Response

```json
{
  "message": "API key deleted successfully"
}
```

---

## Related resources

- [Assets API](assets.md) -- Resources protected by these authorization policies
- [Pipelines API](pipelines.md) -- Pipeline access controlled by constraints
- [Workflows API](workflows.md) -- Workflow access controlled by constraints
