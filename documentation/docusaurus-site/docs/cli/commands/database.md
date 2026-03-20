---
sidebar_label: Database
title: Database Commands
---

# Database Commands

Manage VAMS databases and Amazon S3 bucket configurations. Databases are logical containers for organizing assets.

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
vamscli database list --json-output
```

---

## database get

Get details for a specific database.

```bash
vamscli database get [OPTIONS]
```

| Option                | Type | Required | Description                         |
| --------------------- | ---- | -------- | ----------------------------------- |
| `-d`, `--database-id` | TEXT | Yes      | Database ID to retrieve             |
| `--show-deleted`      | Flag | No       | Include deleted databases in search |
| `--json-output`       | Flag | No       | Output raw JSON response            |

Output includes database ID, description, creation date, asset count, default bucket information, metadata restriction status, and file upload extension restrictions.

```bash
vamscli database get -d my-database
vamscli database get -d my-database --json-output
```

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
| `--default-bucket-id`                   | TEXT | No          | Default bucket ID (prompts if not provided)                               |
| `--restrict-metadata-outside-schemas`   | Flag | No          | Restrict metadata to defined schemas only                                 |
| `--restrict-file-uploads-to-extensions` | TEXT | No          | Comma-separated list of allowed file extensions (e.g., `.pdf,.docx,.jpg`) |
| `--json-input`                          | TEXT | No          | JSON input file path or JSON string                                       |
| `--json-output`                         | Flag | No          | Output raw JSON response                                                  |

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

### Examples

```bash
vamscli database create -d my-database --description "My Database"
vamscli database create -d my-database --description "My Database" --default-bucket-id "bucket-uuid"
vamscli database create -d my-database --description "My Database" --restrict-metadata-outside-schemas
vamscli database create -d my-database --description "My Database" --restrict-file-uploads-to-extensions ".pdf,.docx,.jpg"
vamscli database create -d my-database --json-input @database-config.json --json-output
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
At least one field must be provided for update. The flags `--restrict-metadata-outside-schemas` and `--no-restrict-metadata-outside-schemas` are mutually exclusive, as are `--restrict-file-uploads-to-extensions` and `--clear-file-extensions`.
:::

```bash
vamscli database update -d my-database --description "Updated description"
vamscli database update -d my-database --restrict-metadata-outside-schemas
vamscli database update -d my-database --no-restrict-metadata-outside-schemas
vamscli database update -d my-database --restrict-file-uploads-to-extensions ".pdf,.png"
vamscli database update -d my-database --clear-file-extensions
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

:::warning[Deletion Prerequisites]
The database must not contain any active assets, workflows, or pipelines. Requires explicit `--confirm` flag and an interactive confirmation prompt.
:::

```bash
vamscli database delete -d my-database --confirm
```

---

## database list-buckets

List available Amazon S3 bucket configurations for use with databases.

```bash
vamscli database list-buckets [OPTIONS]
```

| Option             | Type    | Required | Description                                                |
| ------------------ | ------- | -------- | ---------------------------------------------------------- |
| `--page-size`      | INTEGER | No       | Number of items per page                                   |
| `--max-items`      | INTEGER | No       | Maximum total items to fetch (only with `--auto-paginate`) |
| `--starting-token` | TEXT    | No       | Token for manual pagination                                |
| `--auto-paginate`  | Flag    | No       | Automatically fetch all items                              |
| `--json-output`    | Flag    | No       | Output raw JSON response                                   |

Output includes bucket ID, bucket name, and base assets prefix.

```bash
vamscli database list-buckets
vamscli database list-buckets --auto-paginate
vamscli database list-buckets --json-output
```

---

## Workflow Examples

### Database with restrictions

```bash
# Create a database with strict metadata schema enforcement
vamscli database create -d schema-enforced-db \
  --description "Schema-Enforced Database" \
  --default-bucket-id "bucket-uuid" \
  --restrict-metadata-outside-schemas

# Create a database that only accepts specific file types
vamscli database create -d documents-db \
  --description "Documents Database" \
  --default-bucket-id "bucket-uuid" \
  --restrict-file-uploads-to-extensions ".pdf,.docx,.xlsx"
```

### Automation with JSON

```bash
vamscli database create -d automated-db --json-input @database-config.json --json-output
vamscli database list --json-output > current-databases.json
```

## Related Pages

-   [Asset Commands](assets.md)
-   [File Commands](files.md)
-   [Metadata Commands](metadata.md)
