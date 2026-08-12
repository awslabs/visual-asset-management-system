---
sidebar_label: Files
title: File Commands
---

# File Commands

Manage files within assets, including upload, listing, folder creation, move, copy, archive, unarchive, permanent deletion, version revert, primary type metadata, and preview management.

---

## file upload

Upload files or a directory to an asset with automatic chunking, multi-sequence batching, progress monitoring, and retry logic.

```bash
vamscli file upload [FILES_OR_DIRECTORY] [OPTIONS]
```

| Option               | Type    | Required    | Description                                                                                 |
| -------------------- | ------- | ----------- | ------------------------------------------------------------------------------------------- |
| `FILES_OR_DIRECTORY` | PATH    | Conditional | One or more file paths or a single directory (omit when using `--directory`/`--json-input`) |
| `-d`, `--database`   | TEXT    | Yes         | Database ID                                                                                 |
| `-a`, `--asset`      | TEXT    | Yes         | Asset ID                                                                                    |
| `--directory`        | PATH    | No          | Directory to upload (mutually exclusive with file arguments)                                |
| `--asset-preview`    | FLAG    | No          | Upload as the asset preview (single file only; not valid with a directory)                  |
| `--asset-location`   | TEXT    | No          | Base asset location (default: `/`)                                                          |
| `--recursive`        | FLAG    | No          | Include subdirectories when uploading a directory                                           |
| `--parallel-uploads` | INTEGER | No          | Maximum parallel part uploads (default: 10)                                                 |
| `--retry-attempts`   | INTEGER | No          | Retry attempts per part (default: 3)                                                        |
| `--force-skip`       | FLAG    | No          | Auto-skip failed parts after retries are exhausted                                          |
| `--hide-progress`    | FLAG    | No          | Hide the upload progress display                                                            |
| `--json-input`       | TEXT    | No          | JSON parameters as a string or `@file` path; overrides matching command-line options        |
| `--json-output`      | FLAG    | No          | Output raw JSON response (implies `--hide-progress`)                                        |

```bash
# Single file
vamscli file upload -d my-db -a my-asset /path/to/file.gltf

# Multiple files
vamscli file upload -d my-db -a my-asset file1.jpg file2.png file3.obj

# Directory upload (recursive)
vamscli file upload -d my-db -a my-asset --directory /path/to/models --recursive

# Asset preview upload
vamscli file upload -d my-db -a my-asset --asset-preview preview.jpg

# Custom asset location
vamscli file upload -d my-db -a my-asset --asset-location /models/v2/ file.gltf

# JSON input from a file, machine-readable output
vamscli file upload --json-input @upload-config.json --json-output
```

:::note[Upload Limits]
Files are split into parts and grouped into upload sequences. Each sequence is limited to 50 files, 200 total parts, and 3 GB; an individual file is limited to 200 parts with a 5 GB maximum part size. VamsCLI creates additional sequences automatically, so the total number of files is unbounded (for example, 200 files become 4 sequences of 50). The backend rate-limits upload initialization to 20 sequences per user per minute, which VamsCLI handles with exponential backoff. Files are chunked at 150 MB for files under 15 GB and at 1 GB for larger files. Zero-byte files are supported and created at completion.
:::

:::warning[Per-File Part Limit]
Only individual-file constraint violations stop an upload. A file requiring more than 200 parts is rejected; compress very large files before uploading.
:::

:::tip[File Extension Restrictions]
A database can restrict uploads to specific extensions (`restrictFileUploadsToExtensions`). When set, VamsCLI validates every file before upload and reports all violations at once. Asset preview uploads and `.previewFile.` auxiliary files are exempt; an empty list or `.all` allows any extension. Use `vamscli database get -d my-db` to view the restrictions.
:::

:::note[Large-File Asynchronous Processing]
Very large uploads may complete successfully but undergo additional backend processing before files appear in the asset. VamsCLI reports this in the result; re-run `vamscli file list` to confirm the files once processing finishes.
:::

---

## file list

List files in an asset with prefix filtering, archived inclusion, asset-version snapshots, pagination, and a fast basic mode.

```bash
vamscli file list [OPTIONS]
```

| Option               | Type    | Required | Description                                                             |
| -------------------- | ------- | -------- | ----------------------------------------------------------------------- |
| `-d`, `--database`   | TEXT    | Yes      | Database ID                                                             |
| `-a`, `--asset`      | TEXT    | Yes      | Asset ID                                                                |
| `--prefix`           | TEXT    | No       | Filter files by prefix                                                  |
| `--include-archived` | FLAG    | No       | Include archived files                                                  |
| `--asset-version-id` | TEXT    | No       | Return the file list from a specific asset version snapshot             |
| `--basic`            | FLAG    | No       | Skip expensive lookups for faster listing                               |
| `--page-size`        | INTEGER | No       | Items per page (passed to the API)                                      |
| `--starting-token`   | TEXT    | No       | Token for manual pagination (mutually exclusive with `--auto-paginate`) |
| `--auto-paginate`    | FLAG    | No       | Automatically fetch all items (default limit: 10,000)                   |
| `--max-items`        | INTEGER | No       | Maximum total items to fetch; applies only with `--auto-paginate`       |
| `--json-input`       | TEXT    | No       | JSON parameters as a string or `@file` path                             |
| `--json-output`      | FLAG    | No       | Output raw JSON response                                                |

```bash
vamscli file list -d my-db -a my-asset
vamscli file list -d my-db -a my-asset --basic --auto-paginate
vamscli file list -d my-db -a my-asset --prefix "models/"
vamscli file list -d my-db -a my-asset --asset-version-id ver-123 --basic
vamscli file list -d my-db -a my-asset --auto-paginate --max-items 5000 --page-size 500
vamscli file list -d my-db -a my-asset --starting-token "token123" --page-size 200
```

:::tip[Basic Mode Performance]
`--basic` skips version checks, preview file processing, and metadata lookups, running approximately 100x faster. Use it for large directories (1000+ files), file counting, and existence checks. The API default page size is 200 in full mode and 1500 in basic mode.
:::

:::note[Pagination Modes]
`--auto-paginate` and `--starting-token` cannot be combined. `--max-items` is a CLI-side aggregation limit (default 10,000) applied only in auto-paginate mode and is never sent to the API; supplying it in manual mode prints a warning and ignores it. `--page-size` is passed to the API in both modes.
:::

Each file entry shows its relative path, size, primary type, and change source on the main line, with the current-version creation date, version ID, Amazon S3 ETag, storage class, and preview file listed as indented detail sub-lines. Version-mismatch and permanently-deleted files are flagged. Fields skipped in `--basic` mode (such as version ID and preview file) are omitted. In manual pagination, the response includes a next token to retrieve the following page.

---

## file info

Get detailed information about a single file, optionally including its version history.

```bash
vamscli file info [OPTIONS]
```

| Option               | Type | Required | Description                                 |
| -------------------- | ---- | -------- | ------------------------------------------- |
| `-d`, `--database`   | TEXT | Yes      | Database ID                                 |
| `-a`, `--asset`      | TEXT | Yes      | Asset ID                                    |
| `-p`, `--path`       | TEXT | Yes      | File path to inspect                        |
| `--include-versions` | FLAG | No       | Include version history in the output       |
| `--json-input`       | TEXT | No       | JSON parameters as a string or `@file` path |
| `--json-output`      | FLAG | No       | Output raw JSON response                    |

```bash
vamscli file info -d my-db -a my-asset -p "/model.gltf"
vamscli file info -d my-db -a my-asset -p "/model.gltf" --include-versions
```

With `--include-versions`, each version lists its version ID, current/previous status, last-modified timestamp, size, associated asset versions, and any change-tracking fields (change source, user, workflow, and originating file path).

---

## file create-folder

Create a folder in an asset. A trailing `/` is appended if omitted.

```bash
vamscli file create-folder [OPTIONS]
```

| Option             | Type | Required | Description                                 |
| ------------------ | ---- | -------- | ------------------------------------------- |
| `-d`, `--database` | TEXT | Yes      | Database ID                                 |
| `-a`, `--asset`    | TEXT | Yes      | Asset ID                                    |
| `-p`, `--path`     | TEXT | Yes      | Folder path to create (must end with `/`)   |
| `--json-input`     | TEXT | No       | JSON parameters as a string or `@file` path |
| `--json-output`    | FLAG | No       | Output raw JSON response                    |

```bash
vamscli file create-folder -d my-db -a my-asset -p "/models/subfolder/"
```

---

## file move

Move a file within an asset.

```bash
vamscli file move [OPTIONS]
```

| Option             | Type | Required | Description                                 |
| ------------------ | ---- | -------- | ------------------------------------------- |
| `-d`, `--database` | TEXT | Yes      | Database ID                                 |
| `-a`, `--asset`    | TEXT | Yes      | Asset ID                                    |
| `--source`         | TEXT | Yes      | Source file path                            |
| `--dest`           | TEXT | Yes      | Destination file path                       |
| `--json-input`     | TEXT | No       | JSON parameters as a string or `@file` path |
| `--json-output`    | FLAG | No       | Output raw JSON response                    |

```bash
vamscli file move -d my-db -a my-asset --source "/old/path.gltf" --dest "/new/path.gltf"
```

---

## file copy

Copy a file within an asset, to another asset, or across databases.

```bash
vamscli file copy [OPTIONS]
```

| Option             | Type | Required | Description                                                           |
| ------------------ | ---- | -------- | --------------------------------------------------------------------- |
| `-d`, `--database` | TEXT | Yes      | Source database ID                                                    |
| `-a`, `--asset`    | TEXT | Yes      | Source asset ID                                                       |
| `--source`         | TEXT | Yes      | Source file path                                                      |
| `--dest`           | TEXT | Yes      | Destination file path                                                 |
| `--dest-asset`     | TEXT | No       | Destination asset ID (for cross-asset copy)                           |
| `--dest-database`  | TEXT | No       | Destination database ID (for cross-database copy; defaults to source) |
| `--json-input`     | TEXT | No       | JSON parameters as a string or `@file` path                           |
| `--json-output`    | FLAG | No       | Output raw JSON response                                              |

```bash
vamscli file copy -d my-db -a my-asset --source "/file.gltf" --dest "/copy.gltf"
vamscli file copy -d my-db -a my-asset --source "/file.gltf" --dest "/file.gltf" --dest-asset other-asset
vamscli file copy -d my-db -a my-asset --source "/file.gltf" --dest "/file.gltf" --dest-asset other-asset --dest-database other-db
```

---

## file archive

Archive a file or all files under a prefix (soft delete, recoverable).

```bash
vamscli file archive [OPTIONS]
```

| Option             | Type | Required | Description                                  |
| ------------------ | ---- | -------- | -------------------------------------------- |
| `-d`, `--database` | TEXT | Yes      | Database ID                                  |
| `-a`, `--asset`    | TEXT | Yes      | Asset ID                                     |
| `-p`, `--path`     | TEXT | Yes      | File path to archive                         |
| `--prefix`         | FLAG | No       | Archive all files under the path as a prefix |
| `--json-input`     | TEXT | No       | JSON parameters as a string or `@file` path  |
| `--json-output`    | FLAG | No       | Output raw JSON response                     |

```bash
vamscli file archive -d my-db -a my-asset -p "/file.gltf"
vamscli file archive -d my-db -a my-asset -p "/folder/" --prefix
```

---

## file unarchive

Restore a previously archived file.

```bash
vamscli file unarchive [OPTIONS]
```

| Option             | Type | Required | Description                                 |
| ------------------ | ---- | -------- | ------------------------------------------- |
| `-d`, `--database` | TEXT | Yes      | Database ID                                 |
| `-a`, `--asset`    | TEXT | Yes      | Asset ID                                    |
| `-p`, `--path`     | TEXT | Yes      | File path to unarchive                      |
| `--json-input`     | TEXT | No       | JSON parameters as a string or `@file` path |
| `--json-output`    | FLAG | No       | Output raw JSON response                    |

```bash
vamscli file unarchive -d my-db -a my-asset -p "/file.gltf"
```

---

## file delete

Permanently delete a file or all files under a prefix.

```bash
vamscli file delete [OPTIONS]
```

| Option             | Type | Required | Description                                 |
| ------------------ | ---- | -------- | ------------------------------------------- |
| `-d`, `--database` | TEXT | Yes      | Database ID                                 |
| `-a`, `--asset`    | TEXT | Yes      | Asset ID                                    |
| `-p`, `--path`     | TEXT | Yes      | File path to delete                         |
| `--prefix`         | FLAG | No       | Delete all files under the path as a prefix |
| `--confirm`        | FLAG | Yes      | Confirm permanent deletion                  |
| `--json-input`     | TEXT | No       | JSON parameters as a string or `@file` path |
| `--json-output`    | FLAG | No       | Output raw JSON response                    |

```bash
vamscli file delete -d my-db -a my-asset -p "/file.gltf" --confirm
vamscli file delete -d my-db -a my-asset -p "/folder/" --prefix --confirm
```

:::danger[Permanent Deletion]
Requires the `--confirm` flag. Without it, the command exits with an error (a JSON error object in `--json-output` mode). This action cannot be undone.
:::

---

## file revert

Revert a file to a previous version. Creates a new version with the reverted content.

```bash
vamscli file revert [OPTIONS]
```

| Option             | Type | Required | Description                                 |
| ------------------ | ---- | -------- | ------------------------------------------- |
| `-d`, `--database` | TEXT | Yes      | Database ID                                 |
| `-a`, `--asset`    | TEXT | Yes      | Asset ID                                    |
| `-p`, `--path`     | TEXT | Yes      | File path to revert                         |
| `-v`, `--version`  | TEXT | Yes      | Version ID to revert to                     |
| `--json-input`     | TEXT | No       | JSON parameters as a string or `@file` path |
| `--json-output`    | FLAG | No       | Output raw JSON response                    |

```bash
vamscli file revert -d my-db -a my-asset -p "/file.gltf" -v "version-id-123"
```

---

## file set-primary

Set or remove the primary type metadata for a file.

```bash
vamscli file set-primary [OPTIONS]
```

| Option             | Type | Required    | Description                                                            |
| ------------------ | ---- | ----------- | ---------------------------------------------------------------------- |
| `-d`, `--database` | TEXT | Yes         | Database ID                                                            |
| `-a`, `--asset`    | TEXT | Yes         | Asset ID                                                               |
| `-p`, `--path`     | TEXT | Yes         | File path                                                              |
| `--type`           | TEXT | Yes         | One of `primary`, `lod1`–`lod5`, `other`, or an empty string to remove |
| `--type-other`     | TEXT | Conditional | Custom primary type; required when `--type` is `other`                 |
| `--json-input`     | TEXT | No          | JSON parameters as a string or `@file` path                            |
| `--json-output`    | FLAG | No          | Output raw JSON response                                               |

```bash
vamscli file set-primary -d my-db -a my-asset -p "/model.gltf" --type "primary"
vamscli file set-primary -d my-db -a my-asset -p "/lod.gltf" --type "lod1"
vamscli file set-primary -d my-db -a my-asset -p "/model.gltf" --type "other" --type-other "custom-type"
vamscli file set-primary -d my-db -a my-asset -p "/model.gltf" --type ""
```

---

## file delete-preview

Delete the asset preview file.

```bash
vamscli file delete-preview [OPTIONS]
```

| Option             | Type | Required | Description                                 |
| ------------------ | ---- | -------- | ------------------------------------------- |
| `-d`, `--database` | TEXT | Yes      | Database ID                                 |
| `-a`, `--asset`    | TEXT | Yes      | Asset ID                                    |
| `--json-input`     | TEXT | No       | JSON parameters as a string or `@file` path |
| `--json-output`    | FLAG | No       | Output raw JSON response                    |

```bash
vamscli file delete-preview -d my-db -a my-asset
```

---

## file delete-auxiliary

Delete auxiliary preview asset files under a path prefix.

```bash
vamscli file delete-auxiliary [OPTIONS]
```

| Option             | Type | Required | Description                                    |
| ------------------ | ---- | -------- | ---------------------------------------------- |
| `-d`, `--database` | TEXT | Yes      | Database ID                                    |
| `-a`, `--asset`    | TEXT | Yes      | Asset ID                                       |
| `-p`, `--path`     | TEXT | Yes      | File path prefix for auxiliary files to delete |
| `--json-input`     | TEXT | No       | JSON parameters as a string or `@file` path    |
| `--json-output`    | FLAG | No       | Output raw JSON response                       |

```bash
vamscli file delete-auxiliary -d my-db -a my-asset -p "/file.gltf"
```

---

## Related Pages

-   [Asset Commands](assets.md)
-   [Metadata Commands](metadata.md)
-   [Automation and Scripting](../automation.md)
