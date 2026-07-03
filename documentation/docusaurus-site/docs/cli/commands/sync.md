---
sidebar_label: Sync
title: Sync Commands
---

# Sync Commands

Synchronize files between a local directory and an asset, transferring only the differences. Sync compares each file's size and modified timestamp (similar to `aws s3 sync`) and supports separate upstream (`push`) and downstream (`pull`) directions, dry-run previews, gitignore-style exclusion patterns, and explicit safeguards for modifying or deleting files.

---

## sync file push

Push local file changes up to an asset. New local files are uploaded; with `--allow-modify`, changed files are also uploaded; with `--allow-delete`, asset files that no longer exist locally are archived (or permanently deleted with `--permanent-delete --confirm`).

```bash
vamscli sync file push [LOCAL_DIRECTORY] [OPTIONS]
```

| Option               | Type    | Required | Description                                                                          |
| -------------------- | ------- | -------- | ------------------------------------------------------------------------------------ |
| `LOCAL_DIRECTORY`    | PATH    | Yes      | Local directory to sync from (may also be supplied via `--json-input`)               |
| `-d`, `--database`   | TEXT    | Yes      | Database ID                                                                          |
| `-a`, `--asset`      | TEXT    | Yes      | Asset ID                                                                             |
| `--asset-location`   | TEXT    | No       | Asset directory to sync against (default: `/`, the whole asset)                      |
| `--dryrun`           | FLAG    | No       | Report what would change without transferring or deleting anything                   |
| `--allow-modify`     | FLAG    | No       | Upload files that exist in VAMS but differ locally (default: only add missing files) |
| `--allow-delete`     | FLAG    | No       | Archive VAMS files that no longer exist locally                                      |
| `--permanent-delete` | FLAG    | No       | Permanently delete instead of archive (requires `--allow-delete` and `--confirm`)    |
| `--confirm`          | FLAG    | No       | Confirm permanent deletion of VAMS files                                             |
| `--size-only`        | FLAG    | No       | Compare files by size only, ignoring modified timestamps                             |
| `--conflict-check`   | FLAG    | No       | Check changed files against remote revision history and skip conflicting pushes      |
| `--ignore-file`      | PATH    | No       | Ignore-pattern file to use instead of `.vamsignore` in the sync directory            |
| `--no-ignore`        | FLAG    | No       | Disable ignore-pattern processing entirely                                           |
| `--version-comment`  | TEXT    | No       | Create an asset version with this comment after a successful push (1-256 characters) |
| `--parallel-uploads` | INTEGER | No       | Maximum parallel part uploads (default: 10)                                          |
| `--retry-attempts`   | INTEGER | No       | Retry attempts per part (default: 3)                                                 |
| `--hide-progress`    | FLAG    | No       | Hide the upload progress display                                                     |
| `--json-input`       | TEXT    | No       | JSON parameters as a string or `@file` path; overrides matching command-line options |
| `--json-output`      | FLAG    | No       | Output raw JSON response (implies `--hide-progress`)                                 |

```bash
# Preview what a push would change
vamscli sync file push ./models -d my-db -a my-asset --dryrun

# Upload only new files (safest default)
vamscli sync file push ./models -d my-db -a my-asset

# Also upload changed files
vamscli sync file push ./models -d my-db -a my-asset --allow-modify

# Full mirror: upload new + changed, archive files removed locally
vamscli sync file push ./models -d my-db -a my-asset --allow-modify --allow-delete

# Permanently delete removed files instead of archiving
vamscli sync file push ./models -d my-db -a my-asset --allow-delete --permanent-delete --confirm

# Sync against a subdirectory of the asset and snapshot a version afterward
vamscli sync file push ./textures -d my-db -a my-asset --asset-location /textures --allow-modify --version-comment "Texture refresh"
```

:::note[Change Detection]
A file is considered changed when its size differs, or (unless `--size-only` is set) when the source side's modified timestamp is newer than the destination's by more than two seconds. The remote timestamp is the time the file's current version was written to Amazon S3, so a freshly pushed file always reports a newer remote timestamp than the local original — pull uses the same rule in the opposite direction, and downloaded files keep the remote timestamp locally so repeated syncs stay stable.
:::

:::warning[Deletion Safeguards]
Without `--allow-delete`, remote files missing locally are only reported, never removed. With `--allow-delete`, they are archived (recoverable via `vamscli file unarchive`). Permanent deletion additionally requires `--permanent-delete` and `--confirm`, and prompts interactively outside `--json-output` mode.
:::

:::tip[Ignore Patterns]
When a `.vamsignore` file exists in the sync directory root (or a file is supplied via `--ignore-file`), its patterns exclude matching files on both sides of the comparison. Patterns use gitignore syntax: `*.log`, `build/`, `**/temp`, and negation with `!important.log`. Add `.vamsignore` itself to the patterns to keep it out of the asset.
:::

:::note[Files Excluded from Sync]
Preview companion files (`.previewFile.*`) and files without a file extension never participate in sync — the file listing API does not return preview companions as items, and upload requires every file name to contain an extension. These are reported in the plan as unsupported.
:::

:::tip[Conflict Checking]
With `--conflict-check`, each changed file is compared against the remote file's revision history (per-version size and timestamp) before transferring. A local file that exactly matches an older remote version is an outdated copy — pushing it would revert newer remote work, so it is skipped with a `remote-newer` conflict. A local file matching no known version that is also older than the remote current version indicates independent edits on both sides and is skipped with `both-modified`. Conflicted files are reported in the plan for manual resolution (pull the remote version first, or re-apply the local change). This check makes one file-info API call per changed file, so it slows large syncs with many modifications.
:::

---

## sync file pull

Pull asset file changes down to a local directory. New remote files are downloaded; with `--allow-modify`, changed files are also downloaded; with `--allow-delete --confirm`, local files that no longer exist in VAMS are deleted. Downloaded files keep the remote modified timestamp so later syncs detect changes correctly.

```bash
vamscli sync file pull [LOCAL_DIRECTORY] [OPTIONS]
```

| Option                 | Type    | Required | Description                                                                            |
| ---------------------- | ------- | -------- | -------------------------------------------------------------------------------------- |
| `LOCAL_DIRECTORY`      | PATH    | Yes      | Local directory to sync into (may also be supplied via `--json-input`)                 |
| `-d`, `--database`     | TEXT    | Yes      | Database ID                                                                            |
| `-a`, `--asset`        | TEXT    | Yes      | Asset ID                                                                               |
| `--asset-location`     | TEXT    | No       | Asset directory to sync against (default: `/`, the whole asset)                        |
| `--dryrun`             | FLAG    | No       | Report what would change without transferring or deleting anything                     |
| `--allow-modify`       | FLAG    | No       | Download files that exist locally but differ in VAMS (default: only add missing files) |
| `--allow-delete`       | FLAG    | No       | Delete local files that no longer exist in VAMS (requires `--confirm`)                 |
| `--confirm`            | FLAG    | No       | Confirm deletion of local files                                                        |
| `--size-only`          | FLAG    | No       | Compare files by size only, ignoring modified timestamps                               |
| `--conflict-check`     | FLAG    | No       | Check changed files against remote revision history and skip conflicting downloads     |
| `--ignore-file`        | PATH    | No       | Ignore-pattern file to use instead of `.vamsignore` in the sync directory              |
| `--no-ignore`          | FLAG    | No       | Disable ignore-pattern processing entirely                                             |
| `--parallel-downloads` | INTEGER | No       | Maximum parallel downloads (default: 5)                                                |
| `--retry-attempts`     | INTEGER | No       | Retry attempts per file (default: 3)                                                   |
| `--timeout`            | INTEGER | No       | Download timeout per file in seconds (default: 300)                                    |
| `--hide-progress`      | FLAG    | No       | Hide the download progress display                                                     |
| `--json-input`         | TEXT    | No       | JSON parameters as a string or `@file` path; overrides matching command-line options   |
| `--json-output`        | FLAG    | No       | Output raw JSON response (implies `--hide-progress`)                                   |

```bash
# Preview what a pull would change
vamscli sync file pull ./models -d my-db -a my-asset --dryrun

# Download only files missing locally
vamscli sync file pull ./models -d my-db -a my-asset

# Also download files that changed in VAMS
vamscli sync file pull ./models -d my-db -a my-asset --allow-modify

# Full mirror: download new + changed, delete local files removed from VAMS
vamscli sync file pull ./models -d my-db -a my-asset --allow-modify --allow-delete --confirm

# Pull only a subdirectory of the asset
vamscli sync file pull ./textures -d my-db -a my-asset --asset-location /textures
```

:::warning[Local Deletion Safeguards]
Without `--allow-delete`, local files missing from VAMS are only reported. With `--allow-delete`, the `--confirm` flag is required and an interactive prompt is shown outside `--json-output` mode. Local deletion cannot be undone.
:::

:::note[Distributable Assets]
Pull requires the asset to be marked distributable. Non-distributable assets fail with a clear error before any comparison is made.
:::

:::tip[Reliable Downloads]
Pulled files are written to a temporary file, verified against the expected size, atomically moved into place, and stamped with the remote modified timestamp. A failed or interrupted download never leaves a partial file at the destination path. Presigned URLs are generated through the bulk download API (up to 1,500 files per request), so large pulls prepare in a handful of API calls.
:::

:::tip[Conflict Checking]
With `--conflict-check`, each changed file is compared against the remote file's revision history before downloading. A local file that exactly matches any known remote version (current or historical) is just an outdated copy and downloads normally. A local file matching no known version carries local-only modifications, so the download is skipped with a `local-modified` conflict and reported in the plan — push the local change or discard it, then pull again. This check makes one file-info API call per changed file, so it slows large syncs with many modifications.
:::

---

## The Sync Plan

Both commands compute a plan before acting and always include it in the output (`--dryrun` stops after the plan). The plan classifies every file on both sides:

| Category         | Meaning                                                                                                           |
| ---------------- | ----------------------------------------------------------------------------------------------------------------- |
| `transfers`      | Files that will be uploaded (push) or downloaded (pull), each with a reason (`missing`, `size-mismatch`, `newer`) |
| `deletes`        | Files that will be archived/deleted (push) or deleted locally (pull)                                              |
| `unchanged`      | Files identical on both sides                                                                                     |
| `skipped_modify` | Files that differ but were not transferred because `--allow-modify` was not set                                   |
| `skipped_delete` | Delete candidates that were not removed because `--allow-delete` was not set                                      |
| `ignored`        | Files excluded by ignore patterns                                                                                 |
| `unsupported`    | Files that cannot participate in sync (preview companions, no file extension)                                     |
| `conflicts`      | Files skipped by `--conflict-check` (`local-modified`, `remote-newer`, `both-modified`)                           |

In `--json-output` mode the full plan, per-file entries, and execution results (uploads, downloads, deletes, version creation) are returned as a single JSON document.

---

## Related Pages

-   [File Commands](files.md) -- Individual file upload, listing, archive, and delete operations
-   [Asset Commands](assets.md) -- Asset download and export
-   [Automation and Scripting](../automation.md) -- JSON output and CI/CD patterns
