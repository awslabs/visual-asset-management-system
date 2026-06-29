---
sidebar_label: Database
title: Database Commands
---

# Database Commands

Manage VAMS databases and Amazon S3 bucket configurations. Databases are logical containers for organizing assets.

:::note[Pagination]
List commands (`database list`, `database list-buckets`) share a common pagination model. `--page-size` is passed to the API to control items per request. `--auto-paginate` fetches all pages up to `--max-items` (default: 10000); `--max-items` is a CLI-side limit and is ignored (with a warning) unless `--auto-paginate` is set. For manual paging, use the `NextToken` from a response as the next `--starting-token`. `--auto-paginate` and `--starting-token` cannot be combined.
:::

---

## database list

List all databases in the VAMS system.

```bash
vamscli database list [OPTIONS]
```

| Option             | Type    | Required | Description                                                                |
| ------------------ | ------- | -------- | -------------------------------------------------------------------------- |
| `--show-deleted`   | Flag    | No       | Include deleted databases                                                  |
| `--page-size`      | INTEGER | No       | Number of items per page                                                   |
| `--max-items`      | INTEGER | No       | Maximum total items to fetch (only with `--auto-paginate`, default: 10000) |
| `--starting-token` | TEXT    | No       | Token for manual pagination                                                |
| `--auto-paginate`  | Flag    | No       | Automatically fetch all items                                              |
| `--json-output`    | Flag    | No       | Output raw JSON response                                                   |

```bash
vamscli database list
vamscli database list --show-deleted
vamscli database list --auto-paginate
vamscli database list --auto-paginate --max-items 5000
vamscli database list --page-size 200 --starting-token "token123"
vamscli database list --json-output
```

---

## database get

Get details for a specific database, including metadata, bucket information, and asset count.

```bash
vamscli database get [OPTIONS]
```

| Option                | Type | Required | Description                         |
| --------------------- | ---- | -------- | ----------------------------------- |
| `-d`, `--database-id` | TEXT | Yes      | Database ID to retrieve             |
| `--show-deleted`      | Flag | No       | Include deleted databases in search |
| `--json-output`       | Flag | No       | Output raw JSON response            |

Output includes database ID, description, creation date, asset count, default bucket ID, bucket name and base assets prefix, metadata restriction status, and file upload extension restrictions.

```bash
vamscli database get -d my-database
vamscli database get -d my-database --show-deleted
vamscli database get -d my-database --json-output
```

:::tip[Database not found]
If a database is not found, try `--show-deleted` to include deleted databases in the search.
:::

---

## database create

Create a new database in VAMS.

```bash
vamscli database create [OPTIONS]
```

| Option                                  | Type | Required    | Description                                                               |
| --------------------------------------- | ---- | ----------- | ------------------------------------------------------------------------- |
| `-d`, `--database-id`                   | TEXT | Yes         | Database ID to create                                                     |
| `--description`                         | TEXT | Conditional | Database description (required unless using `--json-input`)               |
| `--default-bucket-id`                   | TEXT | Conditional | Default bucket ID (prompts interactively if omitted)                      |
| `--restrict-metadata-outside-schemas`   | Flag | No          | Restrict metadata to fields defined in the database schema                |
| `--restrict-file-uploads-to-extensions` | TEXT | No          | Comma-separated list of allowed file extensions (e.g., `.pdf,.docx,.jpg`) |
| `--json-input`                          | TEXT | No          | JSON input file path or JSON string with all database data                |
| `--json-output`                         | Flag | No          | Output raw JSON response                                                  |

:::note
When `--default-bucket-id` is omitted, the CLI lists available buckets and prompts for a selection. In `--json-output` mode this prompt is unavailable, so `--default-bucket-id` is required. Use `vamscli database list-buckets` to find a bucket ID.
:::

### Configuration fields

-   **Metadata restriction** — when `--restrict-metadata-outside-schemas` is set, only metadata fields defined in the database schema can be added to assets.
-   **File extension restriction** — `--restrict-file-uploads-to-extensions` accepts a comma-separated list of extensions with leading dots (e.g., `.pdf,.docx,.jpg`). The special value `.all` bypasses the restriction; an empty or omitted value applies no restriction.

### JSON input format

```json
{
    "databaseId": "my-database",
    "description": "Database description",
    "defaultBucketId": "550e8400-e29b-41d4-a716-446655440000",
    "restrictMetadataOutsideSchemas": true,
    "restrictFileUploadsToExtensions": ".pdf,.docx,.jpg"
}
```

`--json-input` accepts either a JSON string or a path to a JSON file. The `-d`/`--database-id` option always overrides the `databaseId` in the JSON payload.

### Examples

```bash
vamscli database create -d my-database --description "My Database"
vamscli database create -d my-database --description "My Database" --default-bucket-id "bucket-uuid"
vamscli database create -d my-database --description "My Database" --restrict-metadata-outside-schemas
vamscli database create -d my-database --description "My Database" --restrict-file-uploads-to-extensions ".pdf,.docx,.jpg"
vamscli database create -d my-database --json-input database-config.json --default-bucket-id "bucket-uuid" --json-output
```

---

## database update

Update an existing database in VAMS.

```bash
vamscli database update [OPTIONS]
```

| Option                                   | Type | Required | Description                         |
| ---------------------------------------- | ---- | -------- | ----------------------------------- |
| `-d`, `--database-id`                    | TEXT | Yes      | Database ID to update               |
| `--description`                          | TEXT | No       | New database description            |
| `--default-bucket-id`                    | TEXT | No       | New default bucket ID               |
| `--restrict-metadata-outside-schemas`    | Flag | No       | Enable metadata restriction         |
| `--no-restrict-metadata-outside-schemas` | Flag | No       | Disable metadata restriction        |
| `--restrict-file-uploads-to-extensions`  | TEXT | No       | Set allowed file extensions         |
| `--clear-file-extensions`                | Flag | No       | Clear file extension restrictions   |
| `--json-input`                           | TEXT | No       | JSON input file path or JSON string |
| `--json-output`                          | Flag | No       | Output raw JSON response            |

:::note
At least one updatable field must be provided (otherwise the command errors). The flags `--restrict-metadata-outside-schemas` and `--no-restrict-metadata-outside-schemas` are mutually exclusive, as are `--restrict-file-uploads-to-extensions` and `--clear-file-extensions`. The `-d`/`--database-id` option overrides any `databaseId` in a `--json-input` payload.
:::

```bash
vamscli database update -d my-database --description "Updated description"
vamscli database update -d my-database --default-bucket-id "new-bucket-uuid"
vamscli database update -d my-database --restrict-metadata-outside-schemas
vamscli database update -d my-database --no-restrict-metadata-outside-schemas
vamscli database update -d my-database --restrict-file-uploads-to-extensions ".pdf,.png"
vamscli database update -d my-database --clear-file-extensions
vamscli database update -d my-database --json-input '{"description":"Updated","restrictMetadataOutsideSchemas":true}'
```

---

## database delete

Delete a database from VAMS.

```bash
vamscli database delete [OPTIONS]
```

| Option                | Type | Required | Description               |
| --------------------- | ---- | -------- | ------------------------- |
| `-d`, `--database-id` | TEXT | Yes      | Database ID to delete     |
| `--confirm`           | Flag | Yes      | Confirm database deletion |
| `--json-output`       | Flag | No       | Output raw JSON response  |

:::warning[Deletion prerequisites]
The database must not contain any active assets, workflows, or pipelines before it can be deleted. The `--confirm` flag is required; in CLI mode an additional interactive confirmation prompt must be answered. In `--json-output` mode the prompt is skipped and `--confirm` alone proceeds.
:::

```bash
vamscli database delete -d my-database --confirm
vamscli database delete -d my-database --confirm --json-output
```

---

## database list-buckets

List available Amazon S3 bucket configurations for use with databases.

```bash
vamscli database list-buckets [OPTIONS]
```

| Option             | Type    | Required | Description                                                                |
| ------------------ | ------- | -------- | -------------------------------------------------------------------------- |
| `--page-size`      | INTEGER | No       | Number of items per page                                                   |
| `--max-items`      | INTEGER | No       | Maximum total items to fetch (only with `--auto-paginate`, default: 10000) |
| `--starting-token` | TEXT    | No       | Token for manual pagination                                                |
| `--auto-paginate`  | Flag    | No       | Automatically fetch all items                                              |
| `--json-output`    | Flag    | No       | Output raw JSON response                                                   |

Output includes bucket ID, bucket name, and base assets prefix. Use a returned bucket ID with `database create --default-bucket-id` or `database update --default-bucket-id`.

```bash
vamscli database list-buckets
vamscli database list-buckets --auto-paginate
vamscli database list-buckets --page-size 200 --starting-token "token123"
vamscli database list-buckets --json-output
```

---

## Workflow Examples

### Databases with restrictions

```bash
# Enforce metadata schemas (for compliance and data governance)
vamscli database create -d schema-enforced-db \
  --description "Schema-Enforced Database" \
  --default-bucket-id "bucket-uuid" \
  --restrict-metadata-outside-schemas

# Accept only specific file types (e.g., CAD models)
vamscli database create -d cad-models \
  --description "CAD Models Database" \
  --default-bucket-id "bucket-uuid" \
  --restrict-file-uploads-to-extensions ".step,.stp,.iges,.igs"

# Combine both controls, then remove them later
vamscli database update -d existing-db --restrict-metadata-outside-schemas
vamscli database update -d existing-db --no-restrict-metadata-outside-schemas --clear-file-extensions
```

### Automation with JSON

```bash
# Capture current state for migration or backup
vamscli database list --json-output > current-databases.json
vamscli database get -d my-database --json-output > my-database-config.json
vamscli database list-buckets --json-output > bucket-configs.json

# Create from a saved JSON config (bucket ID required in JSON output mode)
vamscli database create -d automated-db --json-input database-config.json --default-bucket-id "bucket-uuid" --json-output
```

## Related Pages

-   [Asset Commands](assets.md)
-   [File Commands](files.md)
-   [Metadata Commands](metadata.md)
-   [Search Commands](search.md)
