---
sidebar_label: Files
title: File Commands
---

# File Commands

Manage files within assets, including upload, list, info, folder creation, move, copy, archive, unarchive, delete, revert, and primary type management.

---

## file upload

Upload files to an asset with intelligent chunking, progress monitoring, and retry logic.

```bash
vamscli file upload [FILES_OR_DIRECTORY] [OPTIONS]
```

| Option               | Type    | Required    | Description                                                                        |
| -------------------- | ------- | ----------- | ---------------------------------------------------------------------------------- |
| `FILES_OR_DIRECTORY` | PATH    | Conditional | File paths or single directory (optional if using `--directory` or `--json-input`) |
| `-d`, `--database`   | TEXT    | Yes         | Database ID                                                                        |
| `-a`, `--asset`      | TEXT    | Yes         | Asset ID                                                                           |
| `--directory`        | PATH    | No          | Directory to upload (mutually exclusive with file arguments)                       |
| `--asset-preview`    | Flag    | No          | Upload as asset preview (single file only)                                         |
| `--asset-location`   | TEXT    | No          | Base asset location (default: `/`)                                                 |
| `--recursive`        | Flag    | No          | Include subdirectories when uploading a directory                                  |
| `--parallel-uploads` | INTEGER | No          | Max parallel uploads (default: 10)                                                 |
| `--retry-attempts`   | INTEGER | No          | Retry attempts per part (default: 3)                                               |
| `--force-skip`       | Flag    | No          | Auto-skip failed parts after retries                                               |
| `--hide-progress`    | Flag    | No          | Hide upload progress display                                                       |
| `--json-input`       | TEXT    | No          | JSON input with all parameters                                                     |
| `--json-output`      | Flag    | No          | Output raw JSON response                                                           |

### Examples

```bash
# Single file
vamscli file upload -d my-db -a my-asset /path/to/file.gltf

# Multiple files
vamscli file upload -d my-db -a my-asset file1.jpg file2.png file3.obj

# Directory upload (recursive)
vamscli file upload -d my-db -a my-asset --directory /path/to/models --recursive

# Asset preview upload
vamscli file upload -d my-db -a my-asset --asset-preview preview.jpg

# Custom location
vamscli file upload -d my-db -a my-asset --asset-location /models/v2/ file.gltf
```

:::note[Upload Limits]
Files are automatically batched into sequences. Per-sequence limits: 50 files, 200 parts, 3 GB. Per-file: 200 parts maximum. Multi-sequence uploads are handled transparently.
:::

:::tip[File Extension Restrictions]
Databases can restrict uploads to specific file types. Preview uploads and `.previewFile.` auxiliary files are excluded from validation. Use `vamscli database get -d my-db` to see the restrictions.
:::

---

## file list

List files in an asset with filtering, pagination, and performance optimization.

```bash
vamscli file list [OPTIONS]
```

| Option               | Type    | Required | Description                               |
| -------------------- | ------- | -------- | ----------------------------------------- |
| `-d`, `--database`   | TEXT    | Yes      | Database ID                               |
| `-a`, `--asset`      | TEXT    | Yes      | Asset ID                                  |
| `--prefix`           | TEXT    | No       | Filter files by prefix                    |
| `--include-archived` | Flag    | No       | Include archived files                    |
| `--asset-version-id` | TEXT    | No       | Filter by specific asset version          |
| `--basic`            | Flag    | No       | Skip expensive lookups for faster listing |
| `--page-size`        | INTEGER | No       | Items per page                            |
| `--max-items`        | INTEGER | No       | Max total items (with `--auto-paginate`)  |
| `--starting-token`   | TEXT    | No       | Pagination token                          |
| `--auto-paginate`    | Flag    | No       | Fetch all items automatically             |
| `--json-output`      | Flag    | No       | Output raw JSON response                  |

```bash
vamscli file list -d my-db -a my-asset
vamscli file list -d my-db -a my-asset --basic --auto-paginate
vamscli file list -d my-db -a my-asset --prefix "models/"
vamscli file list -d my-db -a my-asset --asset-version-id ver-123 --basic
```

:::tip[Performance]
Use `--basic` mode for large directories (1000+ files). It skips version checks, preview file processing, and metadata lookups, running approximately 100x faster.
:::

---

## file info

Get detailed information about a specific file, including version history.

```bash
vamscli file info -d <DB> -a <ASSET> -p <PATH> [--include-versions] [--json-output]
```

---

## file create-folder

Create a folder in an asset. The path must end with `/`.

```bash
vamscli file create-folder -d my-db -a my-asset -p "/models/subfolder/"
```

---

## file move

Move a file within an asset.

```bash
vamscli file move -d my-db -a my-asset --source "/old/path.gltf" --dest "/new/path.gltf"
```

---

## file copy

Copy a file within an asset, to another asset, or across databases.

```bash
vamscli file copy [OPTIONS]
```

| Option             | Type | Required | Description                                       |
| ------------------ | ---- | -------- | ------------------------------------------------- |
| `-d`, `--database` | TEXT | Yes      | Source database ID                                |
| `-a`, `--asset`    | TEXT | Yes      | Source asset ID                                   |
| `--source`         | TEXT | Yes      | Source file path                                  |
| `--dest`           | TEXT | Yes      | Destination file path                             |
| `--dest-asset`     | TEXT | No       | Destination asset ID (for cross-asset copy)       |
| `--dest-database`  | TEXT | No       | Destination database ID (for cross-database copy) |

```bash
vamscli file copy -d my-db -a my-asset --source "/file.gltf" --dest "/copy.gltf"
vamscli file copy -d my-db -a my-asset --source "/file.gltf" --dest "/file.gltf" --dest-asset other-asset
vamscli file copy -d my-db -a my-asset --source "/file.gltf" --dest "/file.gltf" --dest-asset other-asset --dest-database other-db
```

---

## file archive

Archive a file or files under a prefix (soft delete, recoverable).

```bash
vamscli file archive -d my-db -a my-asset -p "/file.gltf"
vamscli file archive -d my-db -a my-asset -p "/folder/" --prefix
```

---

## file unarchive

Restore a previously archived file.

```bash
vamscli file unarchive -d my-db -a my-asset -p "/file.gltf"
```

---

## file delete

Permanently delete a file or files under a prefix.

```bash
vamscli file delete -d my-db -a my-asset -p "/file.gltf" --confirm
vamscli file delete -d my-db -a my-asset -p "/folder/" --prefix --confirm
```

:::warning
Requires the `--confirm` flag. This action cannot be undone.
:::

---

## file revert

Revert a file to a previous version.

```bash
vamscli file revert -d my-db -a my-asset -p "/file.gltf" -v "version-id-123"
```

---

## file set-primary

Set or remove primary type metadata for a file.

```bash
vamscli file set-primary [OPTIONS]
```

| Option             | Type | Required | Description                                                         |
| ------------------ | ---- | -------- | ------------------------------------------------------------------- |
| `-d`, `--database` | TEXT | Yes      | Database ID                                                         |
| `-a`, `--asset`    | TEXT | Yes      | Asset ID                                                            |
| `-p`, `--path`     | TEXT | Yes      | File path                                                           |
| `--type`           | TEXT | Yes      | Primary type: `primary`, `lod1`-`lod5`, `other`, or empty to remove |
| `--type-other`     | TEXT | No       | Custom type when type is `other`                                    |

```bash
vamscli file set-primary -d my-db -a my-asset -p "/model.gltf" --type "primary"
vamscli file set-primary -d my-db -a my-asset -p "/lod.gltf" --type "lod1"
vamscli file set-primary -d my-db -a my-asset -p "/model.gltf" --type ""
```

---

## file delete-preview

Delete the asset preview file.

```bash
vamscli file delete-preview -d my-db -a my-asset
```

---

## file delete-auxiliary

Delete auxiliary preview asset files.

```bash
vamscli file delete-auxiliary -d my-db -a my-asset -p "/file.gltf"
```

---

## Related Pages

-   [Asset Commands](assets.md)
-   [Metadata Commands](metadata.md)
-   [Automation and Scripting](../automation.md)
