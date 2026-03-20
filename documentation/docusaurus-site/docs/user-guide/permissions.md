# Permissions

VAMS uses a two-tier authorization system to control access to both the user interface and the underlying data. Every request must pass both tiers before access is granted. This defense-in-depth approach ensures that even if a user can reach an API endpoint, they can only interact with the specific resources they are authorized to access.

## How authorization works

### Tier 1: API and web route authorization

Tier 1 controls which API endpoints a user can call and which pages they can see in the web interface. This is configured through constraints on the `api` and `web` object types.

- **API constraints** determine which backend API routes (for example, `/assets`, `/database`, `/search`) a role can access, and which HTTP methods (GET, PUT, POST, DELETE) are allowed.
- **Web constraints** determine which frontend pages (for example, `/assets`, `/databases`, `/upload`) are visible in the navigation and accessible by URL.

### Tier 2: Data entity authorization

Tier 2 controls which specific data entities a user can access. Even if Tier 1 allows a user to call the assets API, Tier 2 determines which databases and assets they can actually see or modify.

Entity-level constraints use criteria fields such as `databaseId`, `assetName`, `assetType`, and `tags` to filter access to specific resources.

:::warning
Both tiers must allow access for a request to succeed. A user with Tier 1 API access to `/assets` but no Tier 2 asset constraints will receive empty results or 403 Forbidden responses.
:::


## Roles

A role is a named collection of constraints that can be assigned to users. Roles themselves do not grant any permissions -- they act as containers for constraints.

### Default roles

VAMS is deployed with two default roles:

| Role | Description |
|---|---|
| **Admin** | Full access to all features, all databases, and all administrative functions including user management, role management, and constraint management. |
| **ReadOnly** | Read-only access to view assets, databases, pipelines, and workflows across all databases. Cannot create, modify, or delete any data. |

### Creating a custom role

1. Navigate to **Roles** from the left navigation menu.
2. Click **Create Role**.
3. Enter a **Role Name** (3-64 characters, letters, numbers, hyphens, and underscores).
4. Enter a **Description** (4-256 characters).
5. Optionally enable **MFA Required** -- when checked, this role is only active for users who have authenticated with multi-factor authentication.
6. Click **Create**.

:::note
A newly created role has no constraints and grants no permissions. You must add constraints to the role before it provides any access.
:::


<!-- Screenshot needed: Create Role form with MFA Required toggle -->

## Constraints

Constraints are the rules within a role that define what a user can or cannot do. Each constraint targets a specific **object type** and specifies **actions** (HTTP methods) and **criteria** (filtering conditions).

### Constraint components

| Component | Description |
|---|---|
| **Name** | Unique identifier for the constraint (3-64 characters). |
| **Description** | Human-readable description of what this constraint allows or denies. |
| **Object Type** | The type of resource this constraint applies to (see [Object types](#object-types)). |
| **Group Permissions** | One or more action/effect pairs that define what HTTP methods are allowed or denied. |
| **Criteria (AND)** | Conditions that must ALL be true for the constraint to match. |
| **Criteria (OR)** | Conditions where ANY one must be true for the constraint to match. |

### Object types

| Object type | Controls access to |
|---|---|
| `database` | Database entities (create, read, update, delete databases) |
| `asset` | Asset entities (create, read, update, delete assets and files) |
| `api` | Backend API routes |
| `web` | Frontend web pages |
| `tag` | Tag entities |
| `tagType` | Tag type entities |
| `role` | Role entities (manage roles) |
| `userRole` | User-role assignment entities (assign roles to users) |
| `pipeline` | Pipeline entities |
| `workflow` | Workflow entities |
| `metadataSchema` | Metadata schema entities |

### Criteria fields

Each object type supports specific criteria fields for fine-grained filtering:

| Object type | Available criteria fields |
|---|---|
| `database` | `databaseId` |
| `asset` | `databaseId`, `assetName`, `assetType`, `tags` |
| `api` | `route__path` |
| `web` | `route__path` |
| `tag` | `tagName` |
| `tagType` | `tagTypeName` |
| `role` | `roleName` |
| `userRole` | `roleName`, `userId` |
| `pipeline` | `databaseId`, `pipelineId`, `pipelineType`, `pipelineExecutionType` |
| `workflow` | `databaseId`, `workflowId` |
| `metadataSchema` | `databaseId`, `metadataSchemaName`, `metadataSchemaEntityType` |

### Criteria operators

| Operator | Description | Example |
|---|---|---|
| `equals` | Exact match | `databaseId equals my-database` |
| `contains` | Substring or regex match | `tagName contains .*` (matches all) |
| `does_not_contain` | Inverse substring match | `assetType does_not_contain draft` |
| `starts_with` | Prefix match | `route__path starts_with /assets` |
| `ends_with` | Suffix match | `assetName ends_with .final` |
| `is_one_of` | Matches any value in a list | `databaseId is_one_of db1,db2,db3` |
| `is_not_one_of` | Does not match any value in a list | `assetType is_not_one_of draft,test` |

### The GLOBAL keyword

Use `GLOBAL` as a criteria value for `databaseId` to match resources that are not scoped to any specific database. For example, GLOBAL pipelines and GLOBAL workflows use `databaseId equals GLOBAL`.

### Allow vs. deny effects

Each group permission in a constraint specifies an **effect**:

- **Allow** -- Grants access for the specified action.
- **Deny** -- Explicitly blocks access for the specified action, overriding any allow rules.

The authorization engine evaluates all constraints for a user. If any constraint produces a **deny** result, access is blocked regardless of any allow results. This means deny always wins.

### Creating a constraint

1. Navigate to **Constraints** from the left navigation menu.
2. Click **Create Constraint**.
3. Fill in the constraint details:
    - **Name** and **Description**.
    - **Object Type** -- Select the resource type this constraint targets.
    - **Group Permissions** -- Add one or more action/effect pairs (for example, GET/allow, PUT/allow).
    - **Criteria (AND)** -- Add conditions that must all be true.
    - **Criteria (OR)** -- Add conditions where any one must be true.
4. Assign the constraint to one or more roles in the **Role Group Permissions** section.
5. Optionally assign to specific users in the **User Permissions** section.
6. Click **Create**.

<!-- Screenshot needed: Create Constraint form showing object type selector, criteria fields, and group permissions -->

## Assigning roles to users

1. Navigate to **User Roles** from the left navigation menu.
2. Click **Create User Role**.
3. Select a **User** from the dropdown.
4. Select a **Role** to assign.
5. Click **Create**.

A user can have multiple roles assigned. The effective permissions are the union of all constraints from all assigned roles, with deny effects taking precedence.

<!-- Screenshot needed: User Roles listing page showing users and their assigned roles -->

## Common permission patterns

The following patterns cover the most common access configurations. VAMS provides pre-built permission templates for each of these patterns.

### Database administrator

Full access to a single database including asset CRUD, pipeline and workflow management, metadata schema management, and the ability to update or delete the database itself. Cannot create new databases.

**Constraint summary (13 constraints):**

| # | Constraint | Object Type | Permissions | Scope |
|---|---|---|---|---|
| 1 | Web routes | `web` | GET | Standard pages + `/assetIngestion` |
| 2 | API routes | `api` | GET, PUT, POST, DELETE | All non-admin routes (excludes `/tags`, `/tag-types`) |
| 3 | API routes (tags GET) | `api` | GET | Read-only on `/tags`, `/tag-types` |
| 4 | Database entity | `database` | GET, PUT, DELETE | Scoped to specific database (no POST = no create) |
| 5 | Assets | `asset` | GET, PUT, POST, DELETE | Scoped to specific database (includes permanent delete) |
| 6 | Pipelines (scoped) | `pipeline` | GET, PUT, POST, DELETE | Scoped to specific database (full management) |
| 7 | Pipelines (GLOBAL) | `pipeline` | GET, POST | `databaseId equals GLOBAL` (view + execute) |
| 8 | Workflows (scoped) | `workflow` | GET, PUT, POST, DELETE | Scoped to specific database (full management) |
| 9 | Workflows (GLOBAL) | `workflow` | GET, POST | `databaseId equals GLOBAL` (view + execute) |
| 10 | Metadata schemas (scoped) | `metadataSchema` | GET, PUT, POST, DELETE | Scoped to specific database (full management) |
| 11 | Metadata schemas (GLOBAL) | `metadataSchema` | GET | `databaseId equals GLOBAL` (view only) |
| 12 | Tags | `tag` | GET | Global (read-only) |
| 13 | Tag types | `tagType` | GET | Global (read-only) |

**Key design decisions:**

- **No database creation** -- The database entity constraint grants GET + PUT + DELETE but **not POST**, preventing new database creation even though the API route constraint allows POST on `/database` (needed for asset operations using `/database/\{id\}/...` sub-paths).
- **Scoped + GLOBAL pattern** -- Two separate constraints per entity type: one scoped with full CRUD for management, one GLOBAL with GET + POST for viewing and executing shared resources.
- **Metadata schema GLOBAL = GET only** -- Global schema access is read-only to prevent accidentally creating schemas in the GLOBAL scope.
- **Tags read-only** -- Since tags and tag types are shared across all databases, the recommended approach is to limit database-scoped roles to GET-only access.

**Example constraint: Database entity (no POST = no create)**

```json
{
    "name": "my-project-admin-database",
    "description": "Allow read, update, and delete of my-project-db (no create)",
    "objectType": "database",
    "criteriaAnd": [
        { "field": "databaseId", "id": "db1", "operator": "equals", "value": "my-project-db" }
    ],
    "groupPermissions": [
        {
            "groupId": "my-project-admin",
            "id": "db-get",
            "permission": "GET",
            "permissionType": "allow"
        },
        {
            "groupId": "my-project-admin",
            "id": "db-put",
            "permission": "PUT",
            "permissionType": "allow"
        },
        {
            "groupId": "my-project-admin",
            "id": "db-delete",
            "permission": "DELETE",
            "permissionType": "allow"
        }
    ]
}
```

**Example constraint: GLOBAL pipeline view + execute**

Uses the `GLOBAL` keyword (not a wildcard) to match only shared global pipelines:

```json
{
    "name": "my-project-admin-pipelines-global",
    "description": "Allow viewing and executing GLOBAL pipelines",
    "objectType": "pipeline",
    "criteriaAnd": [
        { "field": "databaseId", "id": "pipe-global1", "operator": "equals", "value": "GLOBAL" }
    ],
    "groupPermissions": [
        {
            "groupId": "my-project-admin",
            "id": "pipe-global-get",
            "permission": "GET",
            "permissionType": "allow"
        },
        {
            "groupId": "my-project-admin",
            "id": "pipe-global-post",
            "permission": "POST",
            "permissionType": "allow"
        }
    ]
}
```

### Database user

Standard working access within a specific database. Can view all data, create and update assets, upload files, archive (soft delete) assets, and execute workflows. Cannot permanently delete assets, create or delete pipelines/workflows/metadata schemas, modify the database itself, or use asset ingestion.

**Constraint summary (15 constraints):**

| # | Constraint | Object Type | Permissions | Scope |
|---|---|---|---|---|
| 1 | Web routes | `web` | GET | Standard pages (excludes `/assetIngestion`) |
| 2 | API routes (GET) | `api` | GET | Broad read access |
| 3 | API routes (POST) | `api` | POST | Asset operations + workflow execution (excludes `/ingest-asset`, `/metadataschema`, `/tags`) |
| 4 | API routes (PUT) | `api` | PUT | Asset updates only (excludes `/pipelines`, `/workflows`, `/metadataschema`, `/tags`) |
| 5 | API routes (DELETE) | `api` | DELETE | Archive paths only (`archiveAsset`, `archiveFile`) + standard non-asset deletes |
| 6 | Database entity | `database` | GET | Scoped to specific database (read-only) |
| 7 | Assets | `asset` | GET, PUT, POST, DELETE | Scoped to specific database (DELETE needed for archive; permanent delete blocked at Tier 1) |
| 8 | Pipelines (scoped) | `pipeline` | GET, POST | Scoped to specific database (view + execute) |
| 9 | Pipelines (GLOBAL) | `pipeline` | GET, POST | `databaseId equals GLOBAL` (view + execute) |
| 10 | Workflows (scoped) | `workflow` | GET, POST | Scoped to specific database (view + execute) |
| 11 | Workflows (GLOBAL) | `workflow` | GET, POST | `databaseId equals GLOBAL` (view + execute) |
| 12 | Metadata schemas (scoped) | `metadataSchema` | GET | Scoped to specific database (view only) |
| 13 | Metadata schemas (GLOBAL) | `metadataSchema` | GET | `databaseId equals GLOBAL` (view only) |
| 14 | Tags | `tag` | GET | Global (read-only) |
| 15 | Tag types | `tagType` | GET | Global (read-only) |

**Key design decisions:**

- **Archive vs. permanent delete (two-tier enforcement)** -- The asset entity constraint grants DELETE at Tier 2 because both archive and permanent delete require DELETE on the asset entity. The differentiation happens at Tier 1 API routes: the DELETE API constraint uses the `contains` operator to only match paths containing `archiveAsset` or `archiveFile`, blocking permanent delete paths.
- **API route method separation** -- Unlike the admin (which uses a single API constraint with all methods), the user has 4 separate API constraints, one per HTTP method, each allowing different route subsets.
- **Tier 2 as a safety net** -- Even though PUT on `/database` is allowed at Tier 1 (needed for asset operations using `/database/\{id\}/assets/...` sub-paths), Tier 2 blocks it because the database entity constraint only grants GET.

**Example constraint: API routes DELETE (archive only)**

This constraint prevents permanent asset deletion while allowing archive operations:

```json
{
    "name": "my-project-user-api-routes-delete",
    "description": "Allow DELETE for archive operations only",
    "objectType": "api",
    "criteriaOr": [
        {
            "field": "route__path",
            "id": "api-del1",
            "operator": "contains",
            "value": "archiveAsset"
        },
        {
            "field": "route__path",
            "id": "api-del2",
            "operator": "contains",
            "value": "archiveFile"
        },
        {
            "field": "route__path",
            "id": "api-del3",
            "operator": "starts_with",
            "value": "/unsubscribe"
        },
        {
            "field": "route__path",
            "id": "api-del4",
            "operator": "starts_with",
            "value": "/subscriptions"
        },
        {
            "field": "route__path",
            "id": "api-del5",
            "operator": "starts_with",
            "value": "/asset-links"
        },
        {
            "field": "route__path",
            "id": "api-del6",
            "operator": "starts_with",
            "value": "/comments"
        }
    ],
    "groupPermissions": [
        {
            "groupId": "my-project-user",
            "id": "api-delete",
            "permission": "DELETE",
            "permissionType": "allow"
        }
    ]
}
```

The `contains` operator on `archiveAsset` matches `/database/\{id\}/assets/\{id\}/archiveAsset` but does **not** match `/database/\{id\}/assets/\{id\}/deleteAsset`. This is the Tier 1 enforcement that distinguishes archive from permanent delete.

**Example constraint: Asset entity (DELETE for archive, protected by Tier 1)**

```json
{
    "name": "my-project-user-assets",
    "description": "Allow create, update, and archive access to assets in my-project-db",
    "objectType": "asset",
    "criteriaAnd": [
        { "field": "databaseId", "id": "asset-db1", "operator": "equals", "value": "my-project-db" }
    ],
    "groupPermissions": [
        {
            "groupId": "my-project-user",
            "id": "asset-get",
            "permission": "GET",
            "permissionType": "allow"
        },
        {
            "groupId": "my-project-user",
            "id": "asset-put",
            "permission": "PUT",
            "permissionType": "allow"
        },
        {
            "groupId": "my-project-user",
            "id": "asset-post",
            "permission": "POST",
            "permissionType": "allow"
        },
        {
            "groupId": "my-project-user",
            "id": "asset-delete",
            "permission": "DELETE",
            "permissionType": "allow"
        }
    ]
}
```

:::note
DELETE is granted at Tier 2 because archive operations require it. Permanent delete is blocked at Tier 1 (see the API routes DELETE constraint above).
:::

### Database Admin vs. Database User comparison

| Capability | Admin | User |
|---|---|---|
| View database, assets, pipelines, workflows, schemas | Yes | Yes |
| Create and update assets | Yes | Yes |
| Upload files | Yes | Yes |
| Archive (soft delete) assets | Yes | Yes |
| **Permanent delete** assets | **Yes** | **No** (Tier 1 blocks) |
| Update or delete the database | **Yes** | **No** |
| Create new databases | No | No |
| Create or delete pipelines (scoped) | **Yes** | **No** |
| Create or delete workflows (scoped) | **Yes** | **No** |
| Create or delete metadata schemas (scoped) | **Yes** | **No** |
| View and execute GLOBAL pipelines and workflows | Yes | Yes |
| View GLOBAL metadata schemas | Yes | Yes |
| Asset ingestion | **Yes** | **No** |
| Tag management (create, modify, delete) | No (manage via broader role) | No (manage via broader role) |
| Tag viewing | Yes | Yes |

### Database read-only

View-only access scoped to a single database. Can browse assets, view files, and read metadata but cannot modify anything.

**Key constraints:**

- `web` -- Allow GET on viewing pages only (no `/upload`)
- `api` -- Allow GET on all read routes; allow POST only on `/auth/routes`, `/search`, `/check-subscription`
- `database` -- Allow GET where `databaseId equals \{DATABASE_ID\}`
- `asset` -- Allow GET where `databaseId equals \{DATABASE_ID\}`

Key differences from the admin and user roles: web routes are the same set of pages, but the UI respects the lack of write permissions. API routes only allow `GET` method, plus `POST` on non-mutating operations. Data constraints have only `GET` permission on all object types.

```bash
python tools/permissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/database-readonly.json \
    --role-name my-project-viewer \
    --variables '{"DATABASE_ID": "my-project-db"}' --dry-run
```

### Global read-only

View-only access across all databases. Same as database read-only but without the `databaseId` filter on entity constraints.

### Multi-database access

To give a user access to multiple databases, use `criteriaOr` instead of `criteriaAnd` for the `databaseId` field:

```json
{
    "name": "multi-db-editor-assets",
    "description": "Access to assets across finance and operations databases",
    "objectType": "asset",
    "criteriaOr": [
        { "field": "databaseId", "id": "db1", "operator": "equals", "value": "finance-db" },
        { "field": "databaseId", "id": "db2", "operator": "equals", "value": "operations-db" }
    ],
    "groupPermissions": [
        {
            "groupId": "multi-db-editor",
            "id": "asset-get",
            "permission": "GET",
            "permissionType": "allow"
        },
        {
            "groupId": "multi-db-editor",
            "id": "asset-put",
            "permission": "PUT",
            "permissionType": "allow"
        },
        {
            "groupId": "multi-db-editor",
            "id": "asset-post",
            "permission": "POST",
            "permissionType": "allow"
        }
    ]
}
```

Alternatively, use the `starts_with` operator with a naming convention:

```json
{
    "criteriaAnd": [
        { "field": "databaseId", "id": "db1", "operator": "starts_with", "value": "team-alpha-" }
    ]
}
```

This matches any database whose ID starts with `team-alpha-` (for example, `team-alpha-prod`, `team-alpha-staging`).

:::warning
Do not use `criteriaAnd` with multiple `databaseId equals` conditions. A single entity can only have one `databaseId` value, so multiple equals conditions in `criteriaAnd` will never match simultaneously. Use `criteriaOr` instead.
:::

### Deny by tag

Block modification of assets with specific tags. This pattern uses a deny constraint to override any allow rules. The Casbin policy effect ensures deny always wins.

**Example: Deny editing of tagged assets**

```json
{
    "name": "my-project-admin-deny-tagged-locked",
    "description": "Deny editing of assets tagged with 'locked'",
    "objectType": "asset",
    "criteriaAnd": [
        { "field": "tags", "id": "tag-match", "operator": "contains", "value": "locked" }
    ],
    "groupPermissions": [
        {
            "groupId": "my-project-admin",
            "id": "deny-put",
            "permission": "PUT",
            "permissionType": "deny"
        },
        {
            "groupId": "my-project-admin",
            "id": "deny-post",
            "permission": "POST",
            "permissionType": "deny"
        },
        {
            "groupId": "my-project-admin",
            "id": "deny-delete",
            "permission": "DELETE",
            "permissionType": "deny"
        }
    ]
}
```

Even though the admin role has full CRUD on assets, this deny constraint matches any asset whose `tags` field contains "locked". When a user attempts to PUT, POST, or DELETE a locked asset, Casbin finds the deny rule and blocks the operation. GET (viewing) is still permitted.

**Important notes:**

- The `tags` field is checked as a string match. If an asset has tags `["locked", "reviewed"]`, the `contains` operator with value `locked` will match.
- You can stack multiple deny constraints for different tag values (for example, one for "locked" and another for "approved").
- Deny constraints can be applied to any role.
- The deny applies to the data entity operation (Tier 2). The user can still call the API endpoint (Tier 1), but the operation is denied when Casbin evaluates the asset entity.

**Example: Deny archived asset deletion**

```json
{
    "name": "deny-archived-asset-delete",
    "description": "Prevent users from deleting assets tagged as archived",
    "objectType": "asset",
    "criteriaAnd": [
        { "field": "tags", "id": "tag1", "operator": "contains", "value": "archived" }
    ],
    "groupPermissions": [
        {
            "groupId": "my-project-user",
            "id": "deny-del",
            "permission": "DELETE",
            "permissionType": "deny"
        }
    ]
}
```

:::tip
Deny-by-tag is useful for restricting access to sensitive assets across roles. Because deny always overrides allow, you can add this constraint to any role to block tagged assets regardless of other permissions.
:::


## Permission templates

VAMS includes pre-built permission templates that you can import to quickly set up common access patterns. Templates are JSON files with variable placeholders (such as `\{\{DATABASE_ID\}\}` and `\{\{ROLE_NAME\}\}`) that are replaced with actual values during import.

### Available templates

| Template | File | Variables | Description |
|---|---|---|---|
| Database Admin | `database-admin.json` | `DATABASE_ID`, `ROLE_NAME` | Full management of a specific database (13 constraints) |
| Database User | `database-user.json` | `DATABASE_ID`, `ROLE_NAME` | Standard user access with archive-only delete (15 constraints) |
| Database Read-Only | `database-readonly.json` | `DATABASE_ID`, `ROLE_NAME` | View-only access to a specific database (10 constraints) |
| Global Read-Only | `global-readonly.json` | `ROLE_NAME` | Read-only access across all databases (10 constraints) |
| Deny Tagged Assets | `deny-tagged-assets.json` | `ROLE_NAME`, `TAG_VALUE` | Overlay: deny editing of assets with a specific tag (1 constraint) |

Templates are located in the `documentation/permissionsTemplates/` directory.

### Applying templates

You can apply templates using the CLI tool or the `POST /auth/constraintsTemplateImport` API endpoint.

**Using the CLI tool:**

```bash
# Apply the database-admin template with variable substitution
python tools/permissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/database-admin.json \
    --role-name my-project-admin \
    --variables '{"DATABASE_ID": "my-project-db"}' --dry-run

# Apply the database-user template
python tools/permissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/database-user.json \
    --role-name my-project-user \
    --variables '{"DATABASE_ID": "my-project-db"}' --dry-run

# Stack multiple deny constraints
python tools/permissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/deny-tagged-assets.json \
    --role-name my-project-admin --var TAG_VALUE=locked

python tools/permissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/deny-tagged-assets.json \
    --role-name my-project-admin --var TAG_VALUE=approved
```

**Using the API directly:**

```bash
curl -X POST https://your-api/auth/constraintsTemplateImport \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "template": {
      "name": "Database Admin",
      "description": "Administrative access for my-project-db",
      "version": "1.0"
    },
    "variables": [
      {"name": "DATABASE_ID", "required": true, "description": "The databaseId to scope permissions to"},
      {"name": "ROLE_NAME", "required": true, "description": "The role name to create"}
    ],
    "variableValues": {
      "DATABASE_ID": "my-project-db",
      "ROLE_NAME": "my-project-admin"
    },
    "constraints": [ ... ]
  }'
```

The response includes the count and IDs of all created constraints:

```json
{
    "success": true,
    "message": "Successfully imported 13 constraints from template 'Database Admin' for role 'my-project-admin'",
    "constraintsCreated": 13,
    "constraintIds": ["uuid-1", "uuid-2", "..."],
    "timestamp": "2024-01-01T00:00:00.000000"
}
```

:::tip
You can post the entire contents of a JSON template file as the request body. Just add the `variableValues` field with your specific values.
:::

### Template JSON format

Templates are self-describing JSON files containing metadata, variable definitions, and constraint definitions:

```json
{
    "metadata": {
        "name": "Database Admin",
        "description": "Administrative access for a specific database",
        "version": "1.0"
    },
    "variables": [
        {
            "name": "DATABASE_ID",
            "required": true,
            "description": "The databaseId to scope permissions to"
        },
        {
            "name": "ROLE_NAME",
            "required": true,
            "description": "The role name to create"
        }
    ],
    "constraints": [
        {
            "name": "{{ROLE_NAME}}-web-routes",
            "description": "Allow navigation to all standard pages for {{ROLE_NAME}}",
            "objectType": "web",
            "criteriaAnd": [],
            "criteriaOr": [
                { "field": "route__path", "operator": "starts_with", "value": "/assets" }
            ],
            "groupPermissions": [{ "action": "GET", "type": "allow" }]
        }
    ]
}
```

Key differences between the template format and the constraint creation API format:

- `groupPermissions` use `action` and `type` (template format) instead of `permission` and `permissionType` (API format).
- No `identifier`, `groupId`, or permission `id` fields are needed -- the API generates these automatically.
- Variable placeholders (`\{\{VARIABLE\}\}`) are replaced with values from `variableValues`.

:::note[Templates create constraints only]
The template import API creates constraints but does not create roles or assign users to roles. You must create the role and assign users separately using the `/roles` and `/user-roles` API endpoints.
:::

## MFA-required roles

Roles can be configured to require multi-factor authentication (MFA). When a role has MFA required:

- The role's constraints are only active when the user has authenticated with MFA in their current session.
- If the user has not completed MFA, the role is effectively ignored during authorization.
- This is useful for elevating privileges that should only be available during high-assurance sessions.

## Web route permissions

Web route constraints control which pages appear in the navigation menu and are accessible by URL. If a user does not have a `web` constraint that allows GET access to a specific route path, that page is hidden from the navigation and returns a 403 error if accessed directly.

The landing page routes (`/` and `*`) are always allowed regardless of constraints, ensuring all users can reach the landing information page.

Common web routes:

| Route path | Page |
|---|---|
| `/assets` | Assets listing |
| `/assets/:assetId` | Asset detail |
| `/databases` | Databases listing |
| `/databases/:databaseId/assets` | Database assets listing |
| `/databases/:databaseId/assets/:assetId` | Asset detail (database-scoped) |
| `/databases/:databaseId/assets/:assetId/download` | Asset download |
| `/databases/:databaseId/assets/:assetId/file` | File viewer |
| `/databases/:databaseId/assets/:assetId/file/*` | File viewer (nested path) |
| `/databases/:databaseId/assets/:assetId/uploads` | Modify asset uploads |
| `/databases/:databaseId/pipelines` | Database pipeline listing |
| `/databases/:databaseId/workflows` | Database workflow listing |
| `/databases/:databaseId/workflows/:workflowId` | Workflow detail |
| `/databases/:databaseId/workflows/create` | Create workflow |
| `/pipelines` | Pipeline listing |
| `/pipelines/:pipelineName` | Pipeline detail |
| `/workflows` | Workflow listing |
| `/workflows/create` | Create workflow |
| `/search` | Search and discovery page |
| `/search/:databaseId/assets` | Database-scoped search |
| `/upload` | Asset upload page |
| `/upload/:databaseId` | Database-scoped upload |
| `/metadataschema` | Metadata schema management |
| `/metadataschema/:databaseId` | Database metadata schemas |
| `/assetIngestion` | Asset ingestion page |
| `/auth/roles` | Role management (admin) |
| `/auth/constraints` | Constraint management (admin) |
| `/auth/userroles` | User role assignment (admin) |
| `/auth/cognitousers` | Amazon Cognito user management (admin) |
| `/auth/api-keys` | API key management (admin) |
| `/auth/tags` | Tag management |
| `/auth/subscriptions` | Subscription management |

## Troubleshooting: why can't I access a resource?

If you receive a 403 Forbidden error or cannot see expected data, check both authorization tiers:

1. **Check Tier 1 (API/web routes):**
    - Does the user's role have a `web` constraint allowing GET access to the page route?
    - Does the user's role have an `api` constraint allowing the appropriate HTTP method on the API route?

2. **Check Tier 2 (entity access):**
    - Does the user's role have a constraint on the correct object type (for example, `asset`, `database`)?
    - Does the criteria match the specific resource? Check `databaseId`, `assetName`, or other filter fields.
    - Is there a **deny** constraint that overrides the allow?

3. **Check role assignment:**
    - Is the role assigned to the user on the **User Roles** page?
    - If the role requires MFA, has the user authenticated with MFA?

:::tip
Administrators can view a user's effective permissions by examining all roles assigned to the user and the constraints within each role. The **Constraints** page lists all constraints organized by role.
:::
