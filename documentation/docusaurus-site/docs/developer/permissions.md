# Permissions: Developer Reference

This page provides technical details on VAMS permission patterns, constraint templates, and JSON examples for developers, integrators, and advanced administrators who configure access control programmatically.

For an introduction to how the authorization system works, see [Permissions Model](../concepts/permissions-model.md). For web UI instructions on managing roles, constraints, and user assignments, see the [User Guide: Permissions](../user-guide/permissions.md).

---

## Common permission patterns

The following patterns cover the most common access configurations. VAMS provides pre-built permission templates for each of these patterns.

The valid fields per object type, the criteria operators, the permissions, and the permission types can be retrieved from `GET /auth/constraints/permissionObjects` or `vamscli role constraint permission-objects`. This is the same mapping the backend validates constraints against.

### Database administrator

Full access to a single database including asset CRUD, pipeline and workflow management, metadata schema management, and the ability to update or delete the database itself. Cannot create new databases.

**Constraint summary (14 constraints):**

| #   | Constraint                | Object Type      | Permissions            | Scope                                                   |
| --- | ------------------------- | ---------------- | ---------------------- | ------------------------------------------------------- |
| 1   | Self-service API keys     | `api`            | GET, PUT, POST, DELETE | `/auth/user/api-keys` (own keys only)                   |
| 2   | Web routes                | `web`            | GET                    | Standard pages + `/assetIngestion`                      |
| 3   | API routes                | `api`            | GET, PUT, POST, DELETE | All non-admin routes (excludes `/tags`, `/tag-types`)   |
| 4   | API routes (tags GET)     | `api`            | GET                    | Read-only on `/tags`, `/tag-types`                      |
| 5   | Database entity           | `database`       | GET, PUT, DELETE       | Scoped to specific database (no POST = no create)       |
| 6   | Assets                    | `asset`          | GET, PUT, POST, DELETE | Scoped to specific database (includes permanent delete) |
| 7   | Pipelines (scoped)        | `pipeline`       | GET, PUT, POST, DELETE | Scoped to specific database (full management)           |
| 8   | Pipelines (GLOBAL)        | `pipeline`       | GET                    | `databaseId equals GLOBAL` (view + execute)             |
| 9   | Workflows (scoped)        | `workflow`       | GET, PUT, POST, DELETE | Scoped to specific database (full management)           |
| 10  | Workflows (GLOBAL)        | `workflow`       | GET                    | `databaseId equals GLOBAL` (view + execute)             |
| 11  | Metadata schemas (scoped) | `metadataSchema` | GET, PUT, POST, DELETE | Scoped to specific database (full management)           |
| 12  | Metadata schemas (GLOBAL) | `metadataSchema` | GET                    | `databaseId equals GLOBAL` (view only)                  |
| 13  | Tags                      | `tag`            | GET                    | Global (read-only)                                      |
| 14  | Tag types                 | `tagType`        | GET                    | Global (read-only)                                      |

**Key design decisions:**

-   **No database creation** -- The database entity constraint grants GET + PUT + DELETE but **not POST**, preventing new database creation even though the API route constraint allows POST on `/database` (needed for asset operations using `/database/\{id\}/...` sub-paths).
-   **Scoped + GLOBAL pattern** -- Two separate constraints per entity type: one scoped with full CRUD for management, one GLOBAL with GET for viewing and executing shared resources. On a `pipeline` or `workflow` object, POST authorizes **creating** the entity, not executing it; the right to execute comes from Tier 1 on the execute route, and Tier 2 confirms it with `GET` on the workflow and on each pipeline the workflow references. Adding POST to a GLOBAL constraint therefore grants global pipeline and workflow creation without adding any execution capability.
-   **Metadata schema GLOBAL = GET only** -- Global schema access is read-only to prevent accidentally creating schemas in the GLOBAL scope.
-   **Tags read-only** -- Since tags and tag types are shared across all databases, the recommended approach is to limit database-scoped roles to GET-only access.

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

Uses the `GLOBAL` keyword (not a wildcard) to match only shared global pipelines. `GET` is the only permission needed — executing a workflow that references a GLOBAL pipeline authorizes that pipeline with `GET`:

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
        }
    ]
}
```

:::warning[POST on a pipeline or workflow object means create]
GLOBAL pipelines and workflows are shared by every database, so managing them is reserved for the global administrator role. Granting POST on a GLOBAL `pipeline` or `workflow` constraint lets the role create global pipelines and workflows; it does not add any execution capability.
:::

### Database user

Standard working access within a specific database. Can view all data, create and update assets, upload files, archive (soft delete) assets, and execute workflows. Cannot permanently delete assets, create or delete pipelines/workflows/metadata schemas, modify the database itself, or use asset ingestion.

**Constraint summary (17 constraints):**

| #   | Constraint                   | Object Type      | Permissions            | Scope                                                                                        |
| --- | ---------------------------- | ---------------- | ---------------------- | -------------------------------------------------------------------------------------------- |
| 1   | Self-service API keys        | `api`            | GET, PUT, POST, DELETE | `/auth/user/api-keys` (own keys only)                                                        |
| 2   | Web routes                   | `web`            | GET                    | Standard pages (excludes `/assetIngestion`)                                                  |
| 3   | API routes (GET)             | `api`            | GET                    | Broad read access                                                                            |
| 4   | API routes (POST)            | `api`            | POST                   | Asset operations + workflow execution (excludes `/ingest-asset`, `/metadataschema`, `/tags`) |
| 5   | API routes (PUT)             | `api`            | PUT                    | Asset updates only (excludes `/pipelines`, `/workflows`, `/metadataschema`, `/tags`)         |
| 6   | API routes (DELETE)          | `api`            | DELETE                 | Archive paths + `/workflows/executions` (abort) + standard non-asset deletes                 |
| 7   | API routes (executions deny) | `api`            | GET, DELETE (deny)     | Paths ending in `/logs` and `/permanent` (admin-only execution routes)                       |
| 8   | Database entity              | `database`       | GET                    | Scoped to specific database (read-only)                                                      |
| 9   | Assets                       | `asset`          | GET, PUT, POST, DELETE | Scoped to specific database (DELETE needed for archive; permanent delete blocked at Tier 1)  |
| 10  | Pipelines (scoped)           | `pipeline`       | GET                    | Scoped to specific database (view + execute)                                                 |
| 11  | Pipelines (GLOBAL)           | `pipeline`       | GET                    | `databaseId equals GLOBAL` (view + execute)                                                  |
| 12  | Workflows (scoped)           | `workflow`       | GET                    | Scoped to specific database (view + execute)                                                 |
| 13  | Workflows (GLOBAL)           | `workflow`       | GET                    | `databaseId equals GLOBAL` (view + execute)                                                  |
| 14  | Metadata schemas (scoped)    | `metadataSchema` | GET                    | Scoped to specific database (view only)                                                      |
| 15  | Metadata schemas (GLOBAL)    | `metadataSchema` | GET                    | `databaseId equals GLOBAL` (view only)                                                       |
| 16  | Tags                         | `tag`            | GET                    | Global (read-only)                                                                           |
| 17  | Tag types                    | `tagType`        | GET                    | Global (read-only)                                                                           |

**Key design decisions:**

-   **Archive vs. permanent delete (two-tier enforcement)** -- The asset entity constraint grants DELETE at Tier 2 because both archive and permanent delete require DELETE on the asset entity. The differentiation happens at Tier 1 API routes: the DELETE API constraint uses the `contains` operator to only match paths containing `archiveAsset` or `archiveFile`, blocking permanent delete paths.
-   **Everyday execution vs. administrative execution routes** -- The Database User can execute workflows, list executions, view execution details, page an execution's detail metadata (`/workflows/executions/\{executionId\}/details/metadata`), abort executions, and re-run executions. Two execution routes are withheld and reserved for administrators: the detailed execution **logs** route (`/workflows/executions/\{executionId\}/logs`, which exposes full CloudWatch logs) and the execution **permanent delete** route (`/workflows/executions/\{executionId\}/permanent`). The template grants the broad `/workflows` prefix on POST (execute + re-run) and on DELETE `/workflows/executions` (abort), then layers an explicit `deny` API constraint on paths ending in `/logs` (GET) and `/permanent` (DELETE) — a `deny` overrides the broad `allow`, so those two routes remain admin-only.
-   **API route method separation** -- Unlike the admin (which uses a single API constraint with all methods), the user has four separate `allow` API constraints, one per HTTP method, each allowing a different route subset, plus the `deny` constraint above.
-   **Execute is a Tier-2 `GET`** -- The pipeline and workflow entity constraints grant `GET` only. Executing a workflow authorizes the workflow object with `GET` and each pipeline the workflow references with `GET`; the right to execute is granted at Tier 1 by POST on `/workflows`. Granting POST on a `pipeline` or `workflow` object would instead let the role create pipelines and workflows, which the Tier 1 route constraint already permits — so the entity constraint is what withholds creation.
-   **Tier 2 as a safety net** -- Even though PUT on `/database` is allowed at Tier 1 (needed for asset operations using `/database/\{id\}/assets/...` sub-paths), Tier 2 blocks it because the database entity constraint only grants GET.

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

| Capability                                           | Admin                        | User                         |
| ---------------------------------------------------- | ---------------------------- | ---------------------------- |
| View database, assets, pipelines, workflows, schemas | Yes                          | Yes                          |
| Create and update assets                             | Yes                          | Yes                          |
| Upload files                                         | Yes                          | Yes                          |
| Archive (soft delete) assets                         | Yes                          | Yes                          |
| **Permanent delete** assets                          | **Yes**                      | **No** (Tier 1 blocks)       |
| Update or delete the database                        | **Yes**                      | **No**                       |
| Create new databases                                 | No                           | No                           |
| Create or delete pipelines (scoped)                  | **Yes**                      | **No**                       |
| Create or delete workflows (scoped)                  | **Yes**                      | **No**                       |
| Create or delete metadata schemas (scoped)           | **Yes**                      | **No**                       |
| View and execute GLOBAL pipelines and workflows      | Yes                          | Yes                          |
| View GLOBAL metadata schemas                         | Yes                          | Yes                          |
| Asset ingestion                                      | **Yes**                      | **No**                       |
| Tag management (create, modify, delete)              | No (manage via broader role) | No (manage via broader role) |
| Tag viewing                                          | Yes                          | Yes                          |

### Database read-only

View-only access scoped to a single database. Can browse assets, view files, and read metadata but cannot modify anything.

**Key constraints (12 constraints):**

-   `web` -- Allow GET on viewing pages only (no `/upload`, no `/metadataschema`)
-   `api` -- Allow GET on all read routes; allow POST only on `/auth/routes`, `/search`, `/check-subscription`; allow self-service API key management on `/auth/user/api-keys`
-   `api` (deny) -- Deny GET on paths ending in `/logs`, withholding the detailed execution-logs route that the broad `/workflows` GET allow would otherwise reach
-   `database` -- Allow GET where `databaseId equals \{DATABASE_ID\}`
-   `asset` -- Allow GET where `databaseId equals \{DATABASE_ID\}`
-   `pipeline`, `workflow`, `metadataSchema` -- Allow GET where `databaseId equals \{DATABASE_ID\}`
-   `tag`, `tagType` -- Allow GET globally

Key differences from the admin and user roles: web routes are a narrower set of pages, and the UI respects the lack of write permissions. API routes only allow `GET` method, plus `POST` on non-mutating operations. Data constraints have only `GET` permission on all object types.

### Global read-only

View-only access across all databases (12 constraints). Same as database read-only, except the entity constraints match every database with `databaseId contains .*` instead of scoping to one `databaseId`.

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
        { "field": "tags", "id": "tag-match", "operator": "is_one_of", "value": "locked" }
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

Even though the admin role has full CRUD on assets, this deny constraint matches any asset whose `tags` list includes "locked". When a user attempts to PUT, POST, or DELETE a locked asset, Casbin finds the deny rule and blocks the operation. GET (viewing) is still permitted.

**Important notes:**

-   The `tags` field holds a **list** of values, so it takes the list operators `is_one_of` and `is_not_one_of`. If an asset has tags `["locked", "reviewed"]`, `is_one_of` with value `locked` matches. Supply a JSON array as the `value` to match any of several tags in one criterion.
-   You can stack multiple deny constraints for different tag values (for example, one for "locked" and another for "approved").
-   Deny constraints can be applied to any role.
-   The deny applies to the data entity operation (Tier 2). The user can still call the API endpoint (Tier 1), but the operation is denied when Casbin evaluates the asset entity.

**Example: Deny archived asset deletion**

```json
{
    "name": "deny-archived-asset-delete",
    "description": "Prevent users from deleting assets tagged as archived",
    "objectType": "asset",
    "criteriaAnd": [
        { "field": "tags", "id": "tag1", "operator": "is_one_of", "value": "archived" }
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

:::warning[List-valued fields take list operators]
A constraint that pairs a pattern-matching operator (`equals`, `contains`, `does_not_contain`, `starts_with`, `ends_with`) with a list-valued field such as `tags` is rejected with a `400` when it is saved. Those operators compile to a regular-expression match, which cannot compare a list, so the rule would deny every asset for the role rather than only the tagged ones. Use `is_one_of` or `is_not_one_of`.
:::

:::tip
Deny-by-tag is useful for restricting access to sensitive assets across roles. Because deny always overrides allow, you can add this constraint to any role to block tagged assets regardless of other permissions.
:::

---

## Permission templates

VAMS includes pre-built permission templates that you can import to quickly set up common access patterns. Templates are JSON files with variable placeholders (such as `\{\{DATABASE_ID\}\}` and `\{\{ROLE_NAME\}\}`) that are replaced with actual values during import.

### Available templates

| Template           | File                      | Variables                  | Description                                                        |
| ------------------ | ------------------------- | -------------------------- | ------------------------------------------------------------------ |
| Database Admin     | `database-admin.json`     | `DATABASE_ID`, `ROLE_NAME` | Full management of a specific database (14 constraints)            |
| Database User      | `database-user.json`      | `DATABASE_ID`, `ROLE_NAME` | Standard user access with archive-only delete (17 constraints)     |
| Database Read-Only | `database-readonly.json`  | `DATABASE_ID`, `ROLE_NAME` | View-only access to a specific database (12 constraints)           |
| Global Read-Only   | `global-readonly.json`    | `ROLE_NAME`                | Read-only access across all databases (12 constraints)             |
| Deny Tagged Assets | `deny-tagged-assets.json` | `ROLE_NAME`, `TAG_VALUE`   | Overlay: deny editing of assets with a specific tag (1 constraint) |

Templates are located in the `documentation/permissionsTemplates/` directory.

### Applying templates

You can apply templates using the CLI tool or the `POST /auth/constraintsTemplateImport` API endpoint.

**Using the CLI tool:**

```bash
# Apply the database-admin template with variable substitution
python tools/PermissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/database-admin.json \
    --role-name my-project-admin \
    --variables '{"DATABASE_ID": "my-project-db"}' --dry-run

# Apply the database-user template
python tools/PermissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/database-user.json \
    --role-name my-project-user \
    --variables '{"DATABASE_ID": "my-project-db"}' --dry-run

# Stack multiple deny constraints
python tools/PermissionsSetup/apply_template.py \
    --template documentation/permissionsTemplates/deny-tagged-assets.json \
    --role-name my-project-admin --var TAG_VALUE=locked

python tools/PermissionsSetup/apply_template.py \
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
    "message": "Successfully imported 14 constraints from template 'Database Admin' for role 'my-project-admin'",
    "constraintsCreated": 14,
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

-   `groupPermissions` use `action` and `type` (template format) instead of `permission` and `permissionType` (API format).
-   No `identifier`, `groupId`, or permission `id` fields are needed -- the API generates these automatically.
-   Variable placeholders (`\{\{VARIABLE\}\}`) are replaced with values from `variableValues`.

:::note[Templates create constraints only]
The template import API creates constraints but does not create roles or assign users to roles. You must create the role and assign users separately using the `/roles` and `/user-roles` API endpoints.
:::

---

## Related topics

-   [Permissions Model](../concepts/permissions-model.md) -- Core authorization concepts, object types, constraint fields, operators, and route references
-   [User Guide: Permissions](../user-guide/permissions.md) -- Web UI instructions for managing roles, constraints, and user assignments
-   [CLI Permissions Commands](../cli/commands/permissions.md) -- Command-line interface for managing roles, constraints, and user-role assignments
-   [Auth API Reference](../api/auth.md) -- API endpoints for authentication, constraints, and template import
