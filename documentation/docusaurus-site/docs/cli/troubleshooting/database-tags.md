---
sidebar_label: Database and Tags
title: Database and Tag Troubleshooting
---

# Database and Tag Troubleshooting

This page covers issues encountered when managing databases, bucket configurations, metadata schemas, tags, and tag types with the VamsCLI.

---

## Database Issues

### Database Not Found

A database operation reports that the target database does not exist.

**Symptoms:**

-   `✗ Database Not Found: Database 'my-database' not found`
-   `vamscli database get`, `update`, or `delete` fails with a not-found error

**Cause:**

The database ID is incorrect, the database has been deleted, or the active profile's role does not have access to the database.

**Resolution:**

1. List the databases visible to your profile and confirm the exact ID:

    ```bash
    vamscli database list
    ```

2. Include deleted databases when checking whether the ID still exists:

    ```bash
    vamscli database get -d my-database --show-deleted
    ```

3. Confirm you are using the intended profile, and re-authenticate if needed:

    ```bash
    vamscli auth status
    vamscli --profile production database list
    ```

:::note
Database IDs are case-sensitive and must match exactly. If the database is missing entirely, you may lack the permission to view it — contact your administrator.
:::

### Database Already Exists

Creating a database fails because the ID is taken.

**Symptoms:**

-   `✗ Database Already Exists`
-   `vamscli database create` reports a conflict

**Cause:**

A database with the same ID already exists. Database IDs are unique across the deployment.

**Resolution:**

Choose a different ID, or modify the existing database with `vamscli database update`. Use `vamscli database list` to review the databases already in use.

### Invalid Database ID or Data

The database ID or one of its configuration fields fails validation.

**Symptoms:**

-   `✗ Invalid Database Data: Extension '.pdf' must start with a dot`
-   `✗ Invalid Database Data: Extension list contains empty values`
-   Validation errors on the database ID format

**Cause:**

The database ID uses unsupported characters, or the `--restrict-file-uploads-to-extensions` value is malformed. Extensions must be a comma-separated list where every entry begins with a dot and contains no surrounding spaces or empty values.

**Resolution:**

1. Use only lowercase letters, numbers, hyphens, and underscores for the database ID (for example, `my-database`, `prod_assets_db`).
2. Format file extensions as a comma-separated list with leading dots and no spaces:

    ```bash
    vamscli database update -d my-database --restrict-file-uploads-to-extensions ".pdf,.docx,.jpg"
    ```

    Valid: `.pdf,.docx,.jpg`. Invalid: `pdf,docx` (missing dots), `.pdf, .docx` (space after comma), `.pdf,,.docx` (empty value).

3. Use `.all` as the value to allow any extension.

:::tip
Inspect the current configuration with `vamscli database get -d my-database` before updating to confirm the stored values.
:::

### Conflicting Update Flags

A `database update` command is rejected because two mutually exclusive flags were passed together.

**Symptoms:**

-   `✗ Cannot use both --restrict-file-uploads-to-extensions and --clear-file-extensions`
-   `✗ Cannot use both --restrict-metadata-outside-schemas and --no-restrict-metadata-outside-schemas`
-   `At least one field must be provided for update`

**Cause:**

The `update` command accepts paired flags that set or clear a setting, and they cannot be combined. The command also requires at least one field to change.

**Resolution:**

Choose a single flag per setting and pass exactly one field to update:

```bash
# Set or clear file extension restrictions (one or the other)
vamscli database update -d my-database --restrict-file-uploads-to-extensions ".pdf,.docx"
vamscli database update -d my-database --clear-file-extensions

# Enable or disable metadata restriction (one or the other)
vamscli database update -d my-database --restrict-metadata-outside-schemas
vamscli database update -d my-database --no-restrict-metadata-outside-schemas
```

### Database Deletion Failed

A database cannot be deleted because it still holds resources.

**Symptoms:**

-   `✗ Database Deletion Error: Cannot delete database that contains active resources`
-   `Confirmation required for database deletion`

**Cause:**

The database still contains assets, workflows, or pipelines, or the required `--confirm` flag was omitted.

**Resolution:**

1. Pass `--confirm` to authorize the deletion:

    ```bash
    vamscli database delete -d my-database --confirm
    ```

2. If the deletion is blocked by active resources, remove or complete them first. Check for remaining assets with:

    ```bash
    vamscli assets list -d my-database
    ```

:::warning
Database deletion cannot be undone. In interactive mode, the CLI prompts for a second confirmation; with `--json-output`, the `--confirm` flag alone is sufficient.
:::

### Bucket Configuration Issues

Creating or updating a database with a bucket ID fails.

**Symptoms:**

-   `✗ Bucket Not Found`
-   Database creation prompts for a bucket but none are available

**Cause:**

The supplied `--default-bucket-id` does not match an available bucket configuration, or no bucket configurations exist for the deployment.

**Resolution:**

List the available bucket configurations and use a valid bucket ID:

```bash
vamscli database list-buckets
vamscli database create -d my-database --description "My Database" --default-bucket-id "bucket-uuid"
```

:::note
When `--default-bucket-id` is omitted in interactive mode, the CLI prompts you to choose from available buckets. With `--json-output`, the bucket ID is required because interactive selection is unavailable.
:::

---

## Metadata Schema Issues

### Schema or Database Not Found

`metadata-schema get` reports that the schema or its database does not exist.

**Symptoms:**

-   `✗ Metadata Schema Not Found`
-   `✗ Database Not Found`

**Cause:**

The schema ID or database ID is incorrect, the schema has been removed, or the role lacks access.

**Resolution:**

1. List the schemas for the database to find the correct ID:

    ```bash
    vamscli metadata-schema list -d my-database
    ```

2. Retrieve the schema with the verified ID:

    ```bash
    vamscli metadata-schema get -d my-database -s schema-123
    ```

3. Confirm the database exists with `vamscli database list`.

### No Schemas Returned

`metadata-schema list` returns no results.

**Symptoms:**

-   `No metadata schemas found.`

**Cause:**

The applied filters exclude all schemas, no schemas have been defined yet, or the role cannot view them.

**Resolution:**

1. List all schemas with no filters to confirm any exist:

    ```bash
    vamscli metadata-schema list
    ```

2. Narrow the results by database or entity type once you confirm the schemas are present:

    ```bash
    vamscli metadata-schema list -d my-database -e assetMetadata
    ```

### Invalid Entity Type Filter

The `-e` / `--entity-type` filter is rejected.

**Symptoms:**

-   `✗ Invalid value for '-e' / '--entity-type'`

**Cause:**

The entity type value is not one of the supported choices. The filter accepts `databaseMetadata`, `assetMetadata`, `fileMetadata`, `fileAttribute`, and `assetLinkMetadata`. The value is matched case-insensitively, but it must still be one of these recognized names.

**Resolution:**

Use one of the supported entity types:

```bash
vamscli metadata-schema list -e assetMetadata
vamscli metadata-schema list -e fileMetadata
vamscli metadata-schema list -e fileAttribute
```

:::note
The VamsCLI exposes `list` and `get` for metadata schemas. Creating, editing, and deleting schemas is performed through the VAMS web interface.
:::

---

## Tag and Tag Type Issues

### Tag Type Must Exist Before Tags

Creating a tag fails because its tag type is missing.

**Symptoms:**

-   `✗ Tag Type Not Found: Tag type 'priority' not found`

**Cause:**

Every tag references a tag type, and the tag type must be created first.

**Resolution:**

Create the tag type, then the tag:

```bash
vamscli tag-type create --tag-type-name "priority" --description "Priority levels"
vamscli tag create --tag-name "urgent" --description "Urgent priority" --tag-type-name "priority"
```

Confirm the available tag types with `vamscli tag-type list`.

### Tag or Tag Type Scope Conflict

A tag or tag type name is rejected even though listing does not show it.

**Symptoms:**

-   `A global tag already uses this name.`
-   `Tag already exists in this scope.`

**Cause:**

Names are unique **per database**, so the same name may exist in several databases. Across scopes the
rule is asymmetric: a database-scoped create is rejected when a GLOBAL entry of that name exists,
while a GLOBAL create over a name a database already uses succeeds and reports a warning.
`vamscli tag list` without `--scope all` does not show every scope, so a conflicting entry can be
invisible in the default listing.

**Resolution:**

-   Run `vamscli tag list --scope all` (or `vamscli tag-type list --scope all`) to see every scope
    and find the conflicting entry.
-   Choose a different name, or delete the conflicting entry with the matching `--database` value
    (omit `--database` to target the GLOBAL entry).

### Warning: This Name Is Also Used by a Database-Specific Entry

A `tag create` or `tag-type create` without `--database` succeeds and prints a warning line.

**Symptoms:**

```
✓ Tag(s) created successfully!
  Message: Tag Status created successfully
  Warning: This name is also used by a database-specific tag. Asset forms will list both entries
  until the database-specific tag is removed.
```

**Cause:**

The global entry was created for a name a database already uses. Both entries exist, so an asset in
that database lists both in its tag picker.

**Resolution:**

-   Run `vamscli tag list --scope all` to find the database-specific entry.
-   Delete it with `vamscli tag delete --tag-name <name> --database <databaseId> --confirm` once the
    global entry covers the same meaning. Assets already carrying the name keep it.

---

### GLOBAL Must Be Capitalized

A tag or tag type operation using the global sentinel is rejected as invalid.

**Symptoms:**

-   `databaseId is invalid. GLOBAL must be capitalized for this field is used.`

**Cause:**

`GLOBAL` is the reserved scope sentinel and is matched exactly. A lower-case or mixed-case value
such as `global` is rejected rather than silently normalized, which would otherwise create a second
partition that no listing resolves.

**Resolution:**

-   Pass `--database GLOBAL` in upper case, or omit `--database` entirely — a tag with no
    `--database` is created as GLOBAL.
-   A database itself can never be named `GLOBAL`; the name is reserved.

---

### Referenced Database Does Not Exist

Creating a database-scoped tag or tag type fails on the database reference.

**Symptoms:**

-   `Referenced database does not exist.`

**Cause:**

A tag or tag type may only be scoped to a database that exists. The value passed to `--database`
did not match any database.

**Resolution:**

-   List databases with `vamscli database list` and pass an exact `databaseId`.
-   Create the database first, then scope the tag or tag type to it.

---
### Tag or Tag Type Already Exists

Creating a tag or tag type fails with a conflict.

**Symptoms:**

-   `✗ Tag Already Exists`
-   `✗ Tag Type Already Exists`

**Cause:**

A tag or tag type with the same name already exists.

**Resolution:**

Choose a different name, or update the existing entry:

```bash
vamscli tag list
vamscli tag update --tag-name "urgent" --description "Updated description"

vamscli tag-type list
vamscli tag-type update --tag-type-name "priority" --description "Updated description"
```

### Tag Type In Use

A tag type cannot be deleted.

**Symptoms:**

-   `✗ Tag Type In Use: Cannot delete tag type that is currently in use by a tag`

**Cause:**

One or more tags still reference the tag type. A tag type can only be deleted once no tags depend on it.

**Resolution:**

1. Find the tags that use the tag type:

    ```bash
    vamscli tag list --tag-type priority
    ```

2. Reassign or delete those tags, then delete the tag type:

    ```bash
    vamscli tag update --tag-name "urgent" --tag-type-name "severity"
    vamscli tag delete urgent --confirm
    vamscli tag-type delete priority --confirm
    ```

### Tag or Tag Type Not Found

An update or delete targets a name that does not exist.

**Symptoms:**

-   `✗ Tag Not Found: Tag 'urgent' not found`
-   `✗ Tag Type Not Found: Tag type 'priority' not found`

**Cause:**

The name is misspelled or the entry was already removed.

**Resolution:**

List the current entries to confirm the exact name (names are matched exactly):

```bash
vamscli tag list
vamscli tag-type list --show-tags
```

### Missing Required Fields or Confirmation

A tag or tag type command is rejected for missing input.

**Symptoms:**

-   `✗ Invalid Tag Data: TagName, description and tagTypeName are required`
-   `All options (--tag-name, --description, --tag-type-name) are required when not using --json-input`
-   `Confirmation required for tag deletion`

**Cause:**

When individual options are used instead of `--json-input`, all required fields must be supplied. Delete operations also require the `--confirm` flag.

**Resolution:**

1. Provide every required field when creating a tag:

    ```bash
    vamscli tag create --tag-name "urgent" --description "Urgent priority" --tag-type-name "priority"
    ```

2. Pass `--confirm` on delete commands. The tag name is a positional argument:

    ```bash
    vamscli tag delete urgent --confirm
    vamscli tag-type delete priority --confirm
    ```

:::tip
For bulk operations, supply a JSON payload with `--json-input` (a file path or an inline JSON string) instead of individual options.
:::

---

## Permission and Authorization Issues

### Not Authorized for Database or Tag Operations

A database, tag, or tag type operation is denied.

**Symptoms:**

-   `✗ Database Permission Error: Not authorized to access database`
-   `✗ Tag Permission Error: Not authorized to manage tags`

**Cause:**

The active profile's role lacks the API-level or object-level permission required for the operation. VAMS enforces authorization at both tiers, and both must allow the action.

**Resolution:**

1. Confirm authentication and the active profile:

    ```bash
    vamscli auth status
    ```

2. Retry with the correct profile, or re-authenticate:

    ```bash
    vamscli --profile production tag list
    vamscli auth login
    ```

3. If the role is genuinely missing the permission, request the appropriate role or constraint from your administrator. See [Permissions](../commands/permissions.md).

---

## Diagnostic Tips

When an operation behaves unexpectedly, add `--verbose` to surface detailed error information, API requests and responses, and timing:

```bash
vamscli --verbose database get -d my-database
vamscli --verbose tag create --tag-name "urgent" --description "Urgent priority" --tag-type-name "priority"
```

Use `--json-output` to capture machine-readable output for scripting and to inspect exact response fields and pagination tokens:

```bash
vamscli database list --json-output
vamscli tag-type list --show-tags --json-output
```

:::warning[Terminal encoding]
The VamsCLI prints Unicode status indicators such as `✓` and `✗`. On Windows, use a UTF-8 capable terminal or set `PYTHONIOENCODING=utf-8` to avoid encoding errors.
:::

---

## Related Pages

-   [Database Commands](../commands/database.md)
-   [Tag Commands](../commands/tags.md)
-   [Metadata Commands](../commands/metadata.md)
-   [General CLI Troubleshooting](./general.md)
