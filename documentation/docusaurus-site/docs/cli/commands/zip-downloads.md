---
sidebar_label: Zip Downloads
title: Zip Download Commands
---

# Zip Download Commands

Package files, folders, or a whole asset into one zip, tar, or tar.gz archive built in the background, then download it. Each zip download is a job owned by the signed-in user; the commands list and change only your own jobs. See [Zip Downloads](../../user-guide/zip-downloads.md) for the rules that apply to every job.

The `zip-download` commands require a deployment with Download as Zip enabled (`app.zipDownloads.enabled`). On a deployment without it, every command fails with `Zip downloads are not available for this deployment`. The CLI reads this from the profile's feature switches first (`vamscli features list`), and from the API's answer otherwise.

---

## zip-download create

Create a zip download job for files, folders, or the whole asset.

```bash
vamscli zip-download create -d my-db -a my-asset --whole-asset
vamscli zip-download create -d my-db -a my-asset --file /models/building.ifc --folder /textures/
vamscli zip-download create -d my-db -a my-asset --file /models/building.ifc@abc123
vamscli zip-download create -d my-db -a my-asset --whole-asset --asset-version-alias release-1 --format tar.gz
vamscli zip-download create -d my-db -a my-asset --folder /scans/ --wait --download-to ./downloads/
```

| Option                    | Description                                                                                                                             |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `-d, --database-id`       | Database ID (required)                                                                                                                  |
| `-a, --asset-id`          | Asset ID (required)                                                                                                                     |
| `--file PATH[@VERSIONID]` | Add a file by its asset-relative path; append `@<versionId>` to pin that file version. Repeatable                                       |
| `--folder PATH`           | Add every current file under a folder. Repeatable                                                                                       |
| `--whole-asset`           | Archive the whole asset. Cannot be combined with `--file` or `--folder`                                                                 |
| `--asset-version-id`      | Archive the file versions recorded in this asset version. Cannot be combined with `--asset-version-alias` or a `@VERSIONID` on `--file` |
| `--asset-version-alias`   | Asset version alias, resolved as for `--asset-version-id`                                                                               |
| `--format`                | `zip` (default), `tar`, or `tar.gz`                                                                                                     |
| `--compression`           | `store` (default) or `deflate`; `deflate` applies to `zip` only                                                                         |
| `--name`                  | Archive file name; the extension for the format is added                                                                                |
| `--wait`                  | Wait until the job reaches a terminal status, showing progress                                                                          |
| `--timeout`               | Seconds to wait with `--wait` or `--download-to` (default 3600)                                                                         |
| `--download-to PATH`      | Wait, then download the archive into this file or directory once the job succeeds                                                       |
| `--overwrite`             | Replace an existing local file with `--download-to`                                                                                     |

A selection holds at most 1,000 entries; select a folder rather than its files one by one to stay within it. The whole job is refused when a selected file is archived, a deleted version, in an archive storage class, or has a blocked file type, and when the selection exceeds the deployment's limits. `--json-output` prints the created job.

---

## zip-download list

List your zip downloads, newest first: for one asset with `-d` and `-a`, or across every asset without them.

```bash
vamscli zip-download list
vamscli zip-download list -d my-db -a my-asset
vamscli zip-download list --page-size 50 --starting-token <token> --json-output
```

| Option              | Description                                     |
| ------------------- | ----------------------------------------------- |
| `-d, --database-id` | Database ID (with `-a`, list one asset's jobs)  |
| `-a, --asset-id`    | Asset ID (with `-d`)                            |
| `--max-items`       | Maximum jobs returned (1 to 1,000, default 100) |
| `--page-size`       | Jobs read per page (1 to 1,000, default 100)    |
| `--starting-token`  | `NextToken` from a previous page                |

Jobs past their retention and jobs on assets you can no longer read are not listed. `--json-output` prints `{"Items": [...], "NextToken": ...}`.

---

## zip-download get

Show one job with its status, progress, and any failure reason.

```bash
vamscli zip-download get -d my-db -a my-asset -r 0mh3k2q9x-4f1c8a7e2b9d4c6e8f0a1b2c3d4e5f60
```

| Option              | Description            |
| ------------------- | ---------------------- |
| `-d, --database-id` | Database ID (required) |
| `-a, --asset-id`    | Asset ID (required)    |
| `-r, --request-id`  | Job ID (required)      |

---

## zip-download download

Download a finished archive to a local file. The command requests a short-lived URL and streams the archive with progress; the URL itself is never printed.

```bash
vamscli zip-download download -d my-db -a my-asset -r <requestId> -o ./building.zip
vamscli zip-download download -d my-db -a my-asset -r <requestId> --wait --timeout 3600 -o ./downloads/
```

| Option              | Description                                                        |
| ------------------- | ------------------------------------------------------------------ |
| `-d, --database-id` | Database ID (required)                                             |
| `-a, --asset-id`    | Asset ID (required)                                                |
| `-r, --request-id`  | Job ID (required)                                                  |
| `-o, --output PATH` | File, or an existing directory to save the archive into (required) |
| `--wait`            | Wait for a queued or running job to finish before downloading      |
| `--timeout`         | Seconds to wait with `--wait` (default 3600)                       |
| `--overwrite`       | Replace an existing local file                                     |

A job that has not succeeded fails with `The archive is not ready for download`; an expired job fails with `This zip download has expired`.

---

## zip-download cancel

Cancel a queued or running job. Its Zip Archive run is stopped and nothing is kept.

```bash
vamscli zip-download cancel -d my-db -a my-asset -r <requestId> --confirm
```

| Option              | Description                   |
| ------------------- | ----------------------------- |
| `-d, --database-id` | Database ID (required)        |
| `-a, --asset-id`    | Asset ID (required)           |
| `-r, --request-id`  | Job ID (required)             |
| `--confirm`         | Confirm the cancel (required) |

A job that has already finished is refused; delete it instead. A cancel never deletes a job: the command sends the cancel intent (`intent=cancel`), so if the job finishes between that check and the cancel, it is kept with its outcome. The command then warns that the job finished and, with `--json-output`, returns `"action": "ended"` with the job's final `status` (`SUCCEEDED` or `FAILED`). The MCP `cancel_zip_download` tool behaves the same way.

---

## zip-download delete

Delete a finished job (succeeded, failed, or cancelled) together with its archive.

```bash
vamscli zip-download delete -d my-db -a my-asset -r <requestId> --confirm
```

| Option              | Description                   |
| ------------------- | ----------------------------- |
| `-d, --database-id` | Database ID (required)        |
| `-a, --asset-id`    | Asset ID (required)           |
| `-r, --request-id`  | Job ID (required)             |
| `--confirm`         | Confirm the delete (required) |

A queued or running job is refused; cancel it first.

---

## Related

-   [Zip Downloads API](../../api/zip-downloads.md)
-   [Asset Commands](assets.md) -- `assets download` for individual files
