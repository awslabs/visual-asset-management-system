# Generate VAMS Permission Template

Generate a VAMS permission constraint JSON template file based on user requirements. This skill understands the full VAMS authorization system and creates properly structured JSON templates compatible with `tools/PermissionsSetup/apply_template.py` and the `POST /auth/constraintsTemplateImport` API.

## Instructions

You are generating a VAMS permission template JSON file. The VAMS system uses a two-tier Casbin-based ABAC/RBAC authorization system:

-   **Tier 1**: API route authorization (`api` and `web` objectTypes)
-   **Tier 2**: Data entity authorization (`database`, `asset`, `pipeline`, `workflow`, `metadataSchema`, `tag`, `tagType`, `role`, `userRole`)

Both tiers must allow an action for it to succeed. This creates a defense-in-depth model where Tier 1 controls which API endpoints are reachable and Tier 2 controls which data entities can be acted upon.

### Critical Rules

1. **A `database` constraint only controls the database entity itself.** To restrict assets within a database, you MUST also create an `asset` constraint with `databaseId` criteria. Same for `pipeline`, `workflow`, and `metadataSchema`. Each entity type requires its own constraint.

2. **Every role needs web, API, AND data constraints.** Without all three, users either can't see pages (no web), can't call APIs (no api), or can't access data (no entity constraints). All three layers must be present.

3. **Read-only roles still need POST on non-mutating APIs**: `/auth/routes`, `/search`, `/check-subscription`. These use POST for request bodies but don't mutate data.

4. **Deny always wins.** Casbin policy effect: `e = some(where (p.eft == allow)) && !some(where (p.eft == deny))`. If any deny constraint matches, access is denied regardless of allow rules.

5. **Tags and TagTypes are global** (not database-scoped). The recommended approach for database-scoped roles is to grant GET-only access on both the `tag`/`tagType` entities AND the `/tags` and `/tag-types` API routes. Tag management (PUT/POST/DELETE) should be excluded from database-scoped templates since tags are shared across all databases. Customers can adjust this based on their needs, but this is the recommended default.

6. **Use `equals GLOBAL` for global resources, never wildcards.** Pipelines, workflows, and metadata schemas shared across all databases use the literal `databaseId` value `GLOBAL`. Always use `equals GLOBAL`, never `contains .*` wildcards, to match these resources.

7. **Tier 2 acts as a safety net for Tier 1.** For example, a user template may allow PUT on `/database` at Tier 1 (needed for asset sub-path operations like `/database/{id}/assets/{id}`), but the database entity constraint at Tier 2 only grants GET, effectively blocking PUT on the database entity itself.

8. **Database creation prevention.** To prevent creating new databases, exclude POST from the `database` entity constraint at Tier 2. The API route can still allow POST on `/database` (needed for asset/workflow sub-path operations), but Tier 2 blocks database entity creation.

### Object Types and Their Key Fields

| ObjectType       | Key Fields                                                     | Notes                                                |
| ---------------- | -------------------------------------------------------------- | ---------------------------------------------------- |
| `web`            | `route__path`                                                  | UI page visibility, only needs GET                   |
| `api`            | `route__path`                                                  | API endpoint access, needs relevant HTTP methods     |
| `database`       | `databaseId`                                                   | Database entity only (not assets within it)          |
| `asset`          | `databaseId`, `assetName`, `assetType`, `tags`                 | **Must be constrained separately from database**     |
| `pipeline`       | `databaseId`, `pipelineId`, `pipelineType`                     | Needs scoped + GLOBAL constraints                    |
| `workflow`       | `databaseId`, `workflowId`                                     | Needs scoped + GLOBAL constraints                    |
| `metadataSchema` | `databaseId`, `metadataSchemaEntityType`, `metadataSchemaName` | Needs scoped + GLOBAL constraints                    |
| `tag`            | `tagName`                                                      | Global, use `contains .*` wildcard for all tags      |
| `tagType`        | `tagTypeName`                                                  | Global, use `contains .*` wildcard for all tag types |
| `role`           | `roleName`                                                     | Role management (super admin only)                   |
| `userRole`       | `roleName`, `userId`                                           | User-role assignment (super admin only)              |

### Constraint Operators

| Operator           | Regex Generated | Use Case                                      |
| ------------------ | --------------- | --------------------------------------------- |
| `equals`           | `^value$`       | Exact match (single database, GLOBAL keyword) |
| `contains`         | `.*value.*`     | Substring match, use `.*` for wildcard-all    |
| `starts_with`      | `^value.*`      | Prefix match (database naming conventions)    |
| `ends_with`        | `.*value$`      | Suffix match                                  |
| `does_not_contain` | `!.*value.*`    | Exclusion                                     |

### HTTP Method Permissions

| Permission | Operations                                                                                                                                      |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`      | Read, list, view, download, stream                                                                                                              |
| `PUT`      | Update, modify existing items. **Also used for creating pipelines and workflows** (PUT is the creation method on `/pipelines` and `/workflows`) |
| `POST`     | Create assets, upload files, execute workflows, create metadata schemas                                                                         |
| `DELETE`   | Archive (soft delete), permanent delete, remove                                                                                                 |

### Archive vs Permanent Delete (Two-Tier Enforcement)

Both archive (soft delete) and permanent delete use the DELETE method on the `asset` entity at Tier 2. Differentiation is enforced at Tier 1 API routes:

-   **Archive paths**: `/database/{id}/assets/{id}/archiveAsset`, `/database/{id}/assets/{id}/archiveFile`
-   **Permanent delete paths**: `/database/{id}/assets/{id}/deleteAsset`, `/database/{id}/assets/{id}/deleteFile`

To allow archive but block permanent delete:

-   **Tier 1 (API routes)**: Use `contains` operator to match only `archiveAsset` and `archiveFile` in the DELETE constraint. This blocks `deleteAsset`/`deleteFile` paths since they don't match.
-   **Tier 2 (asset entity)**: Grant DELETE on the asset entity (needed for archive operations to succeed).

Example JSON for archive-only DELETE at Tier 1:

```json
{
    "name": "{{ROLE_NAME}}-api-routes-delete",
    "objectType": "api",
    "criteriaAnd": [],
    "criteriaOr": [
        { "field": "route__path", "operator": "contains", "value": "archiveAsset" },
        { "field": "route__path", "operator": "contains", "value": "archiveFile" },
        { "field": "route__path", "operator": "starts_with", "value": "/unsubscribe" },
        { "field": "route__path", "operator": "starts_with", "value": "/subscriptions" },
        { "field": "route__path", "operator": "starts_with", "value": "/asset-links" },
        { "field": "route__path", "operator": "starts_with", "value": "/comments" }
    ],
    "groupPermissions": [{ "action": "DELETE", "type": "allow" }]
}
```

### GLOBAL Keyword: Scoped vs GLOBAL Constraints

Pipelines, workflows, and metadata schemas shared across all databases use the literal `databaseId` value `GLOBAL`. **Never use `contains .*` wildcards for GLOBAL resources** -- always use `equals GLOBAL`.

For pipelines, workflows, and metadata schemas, create **two separate constraints per entity type**:

1. **Scoped constraint** (databaseId `equals {{DATABASE_ID}}`): Access to entities within the user's specific database.

    - Admins: full CRUD (GET/PUT/POST/DELETE)
    - Users: GET + POST (view + execute)
    - Read-only: GET only

2. **GLOBAL constraint** (databaseId `equals GLOBAL`): Access to shared global entities.
    - Admins and Users: GET + POST (view + execute global resources)
    - Read-only: GET only
    - Note: Even admins typically only get GET + POST on GLOBAL (not full management)

Example JSON pattern for scoped + GLOBAL pipelines:

```json
{
  "name": "{{ROLE_NAME}}-pipelines-scoped",
  "description": "Access to pipelines within the specific database",
  "objectType": "pipeline",
  "criteriaAnd": [
    {"field": "databaseId", "operator": "equals", "value": "{{DATABASE_ID}}"}
  ],
  "criteriaOr": [],
  "groupPermissions": [
    {"action": "GET", "type": "allow"},
    {"action": "POST", "type": "allow"}
  ]
},
{
  "name": "{{ROLE_NAME}}-pipelines-global",
  "description": "View and execute GLOBAL pipelines",
  "objectType": "pipeline",
  "criteriaAnd": [
    {"field": "databaseId", "operator": "equals", "value": "GLOBAL"}
  ],
  "criteriaOr": [],
  "groupPermissions": [
    {"action": "GET", "type": "allow"},
    {"action": "POST", "type": "allow"}
  ]
}
```

Repeat this scoped + GLOBAL pattern for `workflow` and `metadataSchema` entity types. This means each non-readonly database-scoped template typically has 6 constraints just for these 3 entity types (3 scoped + 3 GLOBAL).

### API Route Constraint Strategy

Choose between two strategies based on the access level:

**Single constraint with all methods** (used by `database-admin.json`): When a role needs the same set of API routes for all HTTP methods, use one constraint with GET/PUT/POST/DELETE. Simpler and fewer constraints. Exclude specific routes (like `/tags`, `/tag-types`) that need different method restrictions, and create separate GET-only constraints for those.

**Separate constraints per HTTP method** (used by `database-user.json`): When different HTTP methods need different API route subsets, split into separate constraints per method (api-routes-get, api-routes-post, api-routes-put, api-routes-delete). More constraints but enables fine-grained control. For example:

-   GET: broad read access to most routes
-   POST: only asset creation, workflow execution, and non-mutating queries
-   PUT: only asset/comment/link updates (excludes pipelines, workflows, schemas)
-   DELETE: only archive paths (excludes permanent delete paths)

### Tags and Tag Types: Recommended Approach

Tags and tag types are shared across all databases, so the recommended approach for database-scoped roles is:

-   **API routes**: Grant GET-only on `/tags` and `/tag-types` API routes. Exclude them from any constraint that grants PUT/POST/DELETE.
-   **Entity constraints**: Grant GET-only on `tag` (field: `tagName`, `contains .*`) and `tagType` (field: `tagTypeName`, `contains .*`) entities.
-   Tag management (create/modify/delete) can be granted through a separate broader role if the customer needs it. This is a recommendation, not a hard rule -- customers can configure constraints however they want.

For the admin template, this means:

1. Exclude `/tags` and `/tag-types` from the main api-routes constraint (which grants all methods)
2. Add a separate `api-routes-tags-get` constraint with GET-only for `/tags` and `/tag-types`
3. Tag and tagType entity constraints grant GET only

### Deny Overlay Templates

Overlay templates add DENY constraints to an existing role. They do NOT create web/API route constraints or other data constraints -- they assume the role already exists with base permissions.

Key pattern for deny overlays:

-   Use `type="deny"` on permissions (instead of `type="allow"`)
-   Typically target a specific entity type (e.g., `asset`) with criteria to match the items to deny
-   The `TAG_VALUE` variable pattern allows reuse: apply the same template multiple times with different tag values
-   Deny overlays can use the `tags` field on assets with `contains` operator to match tagged items

Example: deny editing of assets tagged "locked":

```json
{
    "name": "{{ROLE_NAME}}-deny-tagged-{{TAG_VALUE}}",
    "description": "Deny editing of assets tagged with {{TAG_VALUE}}",
    "objectType": "asset",
    "criteriaAnd": [{ "field": "tags", "operator": "contains", "value": "{{TAG_VALUE}}" }],
    "criteriaOr": [],
    "groupPermissions": [
        { "action": "PUT", "type": "deny" },
        { "action": "POST", "type": "deny" },
        { "action": "DELETE", "type": "deny" }
    ]
}
```

### Common Web Routes

Standard user pages: `/assets`, `/databases`, `/pipelines`, `/search`, `/workflows`, `/upload`, `/metadataschema`

Admin-only pages: `/assetIngestion` (restrict from non-admin users)

Auth admin pages (restrict from non-admin): `/auth/constraints`, `/auth/roles`, `/auth/userroles`, `/auth/subscriptions`, `/auth/tags`

### Common API Route Prefixes

Read paths (GET): `/secure-config`, `/amplify-config`, `/auth/routes`, `/asset-links`, `/assets`, `/comments`, `/database`, `/metadata`, `/metadataschema`, `/pipelines`, `/search`, `/subscriptions`, `/check-subscription`, `/tags`, `/tag-types`, `/workflows`, `/buckets`

Write paths (POST/PUT for editors/admins): `/uploads`, `/ingest-asset` (admin only), `/unsubscribe`

Non-mutating POST paths (needed by all roles including read-only): `/auth/routes`, `/search`, `/check-subscription`

Admin paths: `/roles`, `/user-roles`, `/auth/constraints`, `/user/cognito`

### JSON Template Formatting Rules

1. **Use `{{VARIABLE_NAME}}` placeholders** for values substituted server-side. Standard variables are `DATABASE_ID` and `ROLE_NAME`. Additional custom variables can be passed with `--var KEY=VALUE`.

2. **Unique constraint names.** Each constraint `name` should be prefixed with `{{ROLE_NAME}}-` and use descriptive suffixes like `-scoped`, `-global`, `-get`, `-post`, etc. to ensure uniqueness across roles.

3. **Template format uses `action`/`type` for permissions** (not `permission`/`permissionType`). The API converts these to the internal format with auto-generated UUIDs, groupId mapping, etc.

4. **Include both `criteriaAnd` and `criteriaOr` arrays** in each constraint (use empty arrays where not needed).

### Existing Templates (Reference)

Pre-built JSON templates are in `documentation/permissionsTemplates/`:

| Template                  | Constraints | Variables                  | Description                                                                                                                                                                                                                                               |
| ------------------------- | ----------- | -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `database-admin.json`     | 13          | `DATABASE_ID`, `ROLE_NAME` | Admin: full CRUD + permanent delete + pipeline/workflow/schema management + ingestion. No new DB creation. Tags/tag-types GET-only. Separate scoped + GLOBAL constraints.                                                                                 |
| `database-user.json`      | 15          | `DATABASE_ID`, `ROLE_NAME` | User: create/update/archive assets, execute workflows. No permanent delete, no management, no ingestion. Archive-only DELETE enforced at Tier 1. Tags/tag-types GET-only. Separate API constraints per HTTP method. Separate scoped + GLOBAL constraints. |
| `database-readonly.json`  | 10          | `DATABASE_ID`, `ROLE_NAME` | Read-only scoped to a specific database. GET-only on all entities.                                                                                                                                                                                        |
| `global-readonly.json`    | 10          | `ROLE_NAME`                | Read-only across all databases (uses `contains .*` wildcards). No `DATABASE_ID` variable.                                                                                                                                                                 |
| `deny-tagged-assets.json` | 1           | `ROLE_NAME`, `TAG_VALUE`   | Overlay: denies PUT/POST/DELETE on assets with a specific tag. Applied on top of existing roles.                                                                                                                                                          |

### Constraint Count Reference by Role Type

When building a new template, use these as a guide for expected constraint counts:

-   **Admin (database-scoped)**: ~13 constraints (1 web + 2 api + 1 database + 1 asset + 2 pipeline + 2 workflow + 2 metadataSchema + 1 tag + 1 tagType)
-   **User (database-scoped)**: ~15 constraints (1 web + 4 api + 1 database + 1 asset + 2 pipeline + 2 workflow + 2 metadataSchema + 1 tag + 1 tagType)
-   **Read-only (database-scoped)**: ~10 constraints (1 web + 2 api + 1 database + 1 asset + 1 pipeline + 1 workflow + 1 metadataSchema + 1 tag + 1 tagType)
-   **Read-only (global)**: ~10 constraints (same structure but uses `contains .*` wildcards instead of `equals {{DATABASE_ID}}`)
-   **Deny overlay**: 1 constraint per deny rule

## Workflow

When the user describes their permission requirements:

1. **Check if an existing template matches.** If so, point them to it and show the apply command. Read existing templates from `documentation/permissionsTemplates/` to verify current state.

2. **Ask clarifying questions** if needed:

    - Which specific database(s)? Or should it be global?
    - Admin, user, read-only, or custom access level?
    - Should they be able to permanently delete assets or only archive (soft delete)?
    - Should they manage (create/update/delete) pipelines/workflows/schemas, or just view/execute?
    - Should they view/execute GLOBAL pipelines/workflows, or only database-scoped ones?
    - Should they have tag/tag-type management (PUT/POST/DELETE), or just read access? (Recommend GET-only for database-scoped roles)
    - Any deny/exclusion rules needed (e.g., tag-based deny overlays)?
    - Should asset ingestion (`/ingest-asset`, `/assetIngestion` page) be accessible?
    - Should auth admin pages (`/auth/constraints`, `/auth/roles`, etc.) be accessible?

3. **Generate the JSON template** following these structural guidelines:

    - Use `database-admin.json` as reference for admin-level templates (single API constraint with all methods)
    - Use `database-user.json` as reference for user-level templates (separate API constraints per HTTP method)
    - Use `database-readonly.json` as reference for read-only templates
    - Use `deny-tagged-assets.json` as reference for deny overlay templates
    - Always use `{{ROLE_NAME}}` placeholder. Use `{{DATABASE_ID}}` for database-scoped templates. Define any custom variables in the `variables` array.
    - For pipelines, workflows, and metadataSchema: always create separate scoped + GLOBAL constraints (unless it's a global template using `contains .*`)
    - For tags/tag-types: default to GET-only on both API routes and entity constraints
    - For archive-only roles: use `contains archiveAsset`/`archiveFile` at Tier 1 for DELETE, and grant DELETE at Tier 2 on asset entity
    - The `groupPermissions` elements in templates only need `action` and `type` fields. The API automatically generates UUIDs for `id` fields and sets `groupId` to the role name.

4. **Write the file** to `documentation/permissionsTemplates/<descriptive-name>.json`

5. **Validate with dry-run** after writing:

    ```
    python tools/PermissionsSetup/apply_template.py \
        --template documentation/permissionsTemplates/<name>.json \
        --role-name test-role \
        --variables '{"DATABASE_ID": "test-db"}' --dry-run
    ```

    Verify the constraint count matches expectations and no JSON parsing errors occur.
    The dry-run output shows permission summary and criteria counts per constraint.

6. **Show the apply command** to the user:
    ```
    python tools/PermissionsSetup/apply_template.py \
        --template documentation/permissionsTemplates/<name>.json \
        --role-name <role-name> \
        --variables '{"DATABASE_ID": "<db-id>"}' --dry-run
    ```
    For templates with custom variables, include them in the JSON:
    ```
    --variables '{"DATABASE_ID": "<db-id>", "CUSTOM_VAR": "value"}'
    ```
    Or use `--var` for individual variables: `--var TAG_VALUE=locked`
    Or use `--variables-file vars.json` to load from a JSON file.

## User Request

$ARGUMENTS
