# Zip Downloads

The Zip Downloads API packages files, folders, or a whole asset into one archive in the background and returns a short-lived download URL when the archive is ready. Each request creates a **job** that belongs to the user who made it. The archive is built by the `utility-zip-archive` system pipeline on AWS Batch, which streams the selected file versions from Amazon S3 into a zip (ZIP64), tar, or tar.gz archive without staging them on disk, so an archive can reach the 5 TiB Amazon S3 object limit.

Download as Zip is enabled per deployment with `app.pipelines.useZipDownloads.enabled` (see [Configuration Reference](../deployment/configuration-reference.md#zip-downloads-apppipelinesusezipdownloads)). The routes exist in every deployment; while the feature is disabled each of them answers `404` with the message `Zip downloads are not enabled for this deployment`, after the route authorization check.

## Concepts

-   **Ownership** -- A job is keyed by the authenticated caller's user ID, never by a value in the request. Create, get, cancel and delete reach only the caller's own jobs: a job that belongs to another user, or one named under another asset's path, answers the same `404 Zip download not found` as a job that does not exist. Each such lookup is audited.
-   **Shared view** -- [List Every User's Zip Downloads for an Asset](#list-every-users-zip-downloads-for-an-asset) lists every user's jobs for one asset to a caller whose role grants that route and who may read the asset. [Get Zip Download URL](#get-zip-download-url) also signs another user's finished archive on that asset when the caller may download from the asset (the same permissions a file download needs) and every file in the archive is still downloadable. Only the requester can cancel or delete a job.
-   **Authorization** -- Every route requires API access to the route (Tier 1) and asset `GET` permission on the asset (Tier 2), the same asset action a file download requires. The asset permission is checked again on every call, so a job on an asset the caller can no longer read is hidden from lists and refuses its URL. Such a job can also no longer be cancelled or deleted by its owner: a running job ends with its run (at most 12 hours) and still counts toward the owner's active-job limit until then, and its archive is removed when the job expires.
-   **The whole job is refused** -- Create refuses the job when any selected file is archived, a deleted version, in the GLACIER or DEEP_ARCHIVE storage class, or has an extension or media type on the executable blocklist, and when the selection exceeds the deployment's file count or total size limit. Files under folder and whole-asset entries are checked within a bounded pre-scan; the Zip Archive job checks every member again before it writes the first byte, and a refusal there ends the job `FAILED` with a reason naming the asset-relative path.
-   **Retention** -- A job expires at its `expiresAt`, 7 days after the job was created (a fixed retention). From then on it is no longer listed, every route answers `404` for it (so its archive can no longer be downloaded), and the table's time to live removes the record. The archive object is removed separately, by the auxiliary bucket's lifecycle rule 7 days after the archive was written; Amazon S3 applies that rule asynchronously, so the object can remain for up to a day or two after `expiresAt`. An archive of an asset that is later archived or permanently deleted can no longer be downloaded (the URL route refuses it) and also remains until the lifecycle rule removes it.
-   **Status** -- `QUEUED` -> `RUNNING` -> `SUCCEEDED`, `FAILED`, or `CANCELLED`. A terminal status is final. A job whose Zip Archive run ended outside VAMS (for example, stopped from the AWS Step Functions console) gets its final status when the job is next listed or read; once a job has written no progress for five minutes, a read checks its run at most once every five minutes.

| Status      | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `QUEUED`    | The job is recorded and its Zip Archive run is starting.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `RUNNING`   | The Zip Archive job is resolving the selection or writing the archive. `progress` reports files and bytes written; `bytesDone` also moves while a large file is being written (at least every 64 MiB or 30 seconds).                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `SUCCEEDED` | The archive is ready. Request a URL with [Get Zip Download URL](#get-zip-download-url).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `FAILED`    | The job did not produce an archive. `errorMessage` gives a user-safe reason. A run aborted from the workflow executions views ends `FAILED` with `Cancelled from workflow executions` and `cancelledBy` `execution`, unless its archive had already finished uploading, in which case the job is `SUCCEEDED`. A run that timed out ends with `The archive job timed out`; one whose archive job was stopped another way (for example, terminated in the AWS Batch console) ends with `The archive job was stopped`; one whose archive job stopped without warning (out of memory, host loss) ends with `The archive job stopped responding`. |
| `CANCELLED` | The caller cancelled the job with [Cancel or Delete Zip Download](#cancel-or-delete-zip-download) before it finished.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |

A job's status follows its Zip Archive run. When the run ends in a way the archive job could not record itself (an abort from the workflow executions views, a timeout, or a failure the job did not report), the job shows its final status the next time it is read: [Get Zip Download](#get-zip-download), either list, or [Get Zip Download URL](#get-zip-download-url).

---

## Endpoints

### Create Zip Download

`POST /database/{databaseId}/assets/{assetId}/download/zip`

Creates a job for the selected files, folders, or the whole asset of a distributable, unarchived asset and starts its Zip Archive run.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Request Body:**

```json
{
    "selection": [
        { "type": "file", "relativePath": "/models/building.ifc", "versionId": "abc123" },
        { "type": "folder", "relativePath": "/textures/" }
    ],
    "archiveFormat": "zip",
    "compression": "store",
    "archiveFileName": "building-package"
}
```

To archive the whole asset at an asset version:

```json
{
    "selection": [{ "type": "asset", "relativePath": "/" }],
    "assetVersionIdAlias": "release-1",
    "archiveFormat": "tar.gz"
}
```

| Field                 | Type   | Required | Description                                                                                                                                                                                                                                                            |
| --------------------- | ------ | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `selection`           | array  | Yes      | 1 to 1,000 entries. Each entry has a `type` (`file`, `folder`, or `asset`), an asset-relative `relativePath` beginning with `/`, and for a `file` entry an optional `versionId`. Exact duplicates are collapsed.                                                       |
| `assetVersionId`      | string | No       | Archive the file versions recorded in this asset version. Mutually exclusive with `assetVersionIdAlias` and with any per-file `versionId`.                                                                                                                             |
| `assetVersionIdAlias` | string | No       | Asset version alias, resolved to an asset version ID as for `assetVersionId`.                                                                                                                                                                                          |
| `archiveFormat`       | string | No       | `zip` (default), `tar`, or `tar.gz`.                                                                                                                                                                                                                                   |
| `compression`         | string | No       | `store` (default) or `deflate`. `deflate` applies to `zip` only. Most 3D formats are compressed already, so `store` is usually as small and faster.                                                                                                                    |
| `archiveFileName`     | string | No       | Name of the archive file. Any directory part is removed, characters other than letters, digits, space, and `._()-` become `_`, the name is cut to 200 characters, and the extension for the format is added. Defaults to `<asset name>-<YYYYMMDD-HHMMSS>.<extension>`. |

Selection rules:

-   A `folder` entry ends with `/` and selects every current file under that folder. An `asset` entry has the path `/`, selects every current file of the asset, and must be the only entry.
-   Paths with backslashes, control characters, or empty, `.`, or `..` segments are refused.
-   Without a version, each file is archived at its latest version. A per-file `versionId` pins that file. An asset version pin (`assetVersionId` or `assetVersionIdAlias`) resolves every selected file, folder, or the whole asset to the file versions recorded in that asset version, and every resolved file is checked before the job is created.
-   Error messages never echo a requested version ID or alias.

**Response:** the created job (see [Job Fields](#job-fields)), with `status` `QUEUED`.

```json
{
    "requestId": "0mh3k2q9x-4f1c8a7e2b9d4c6e8f0a1b2c3d4e5f60",
    "databaseId": "my-database",
    "assetId": "x1234abcd",
    "assetName": "Building",
    "status": "QUEUED",
    "archiveFormat": "zip",
    "compression": "store",
    "archiveFileName": "building-package.zip",
    "archiveSizeBytes": null,
    "selectionSummary": { "files": 1, "folders": 1, "wholeAsset": false },
    "selectionPreview": [
        { "type": "file", "relativePath": "/models/building.ifc", "versionId": "abc123" },
        { "type": "folder", "relativePath": "/textures/" }
    ],
    "assetVersionId": null,
    "progress": { "filesDone": 0, "filesTotal": 0, "bytesDone": 0, "bytesTotal": 0 },
    "errorMessage": null,
    "cancelledBy": null,
    "createdAt": "2026-11-06T10:15:30Z",
    "startedAt": "2026-11-06T10:15:31Z",
    "completedAt": null,
    "expiresAt": "2026-11-13T10:15:30Z"
}
```

**Error Responses:**

| Status | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `400`  | Invalid selection or parameters, conflicting version pins, `deflate` with a non-zip format, asset not distributable or archived, a selected file that is archived (`The selection includes an archived or deleted item. ...`), a selected file or folder that does not exist or has a blocked file type, a selection over the limits, or `The archive job could not be started for this selection` (the Zip Archive workflow refused the run before starting it, for example because it is disabled; no job is recorded). The asset is quarantined without an exception and the deployment sets `app.compliance.quarantineBlocksDownload` (`message` "Asset is quarantined and cannot be downloaded"). |
| `403`  | Not authorized for this route.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `404`  | `Asset not found`: the asset does not exist or the caller may not read it (both answer alike), or Download as Zip is not enabled for this deployment.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `410`  | A selected file version is archived or deleted.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `429`  | The caller already has `app.pipelines.useZipDownloads.maxActiveRequestsPerUser` jobs that are `QUEUED` or `RUNNING`. The count is read before the job is written, so concurrent creates can briefly exceed it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `500`  | Internal server error, or `The archive job could not be started` (the run could not be started for another reason and may exist; the job is then recorded as `FAILED`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `503`  | `The archive job could not be started right now. Try again in a moment.`: a transient condition (the workflow's deployment being brought up to date, the execution limit, a throttle). Send the request again. A throttled launch also records the job as `FAILED`; otherwise no job is recorded.                                                                                                                                                                                                                                                                                                                                                                                                      |

---

### List Zip Downloads for an Asset

`GET /database/{databaseId}/assets/{assetId}/download/zip`

Lists the caller's jobs for one asset, newest first.

### List Every User's Zip Downloads for an Asset

`GET /database/{databaseId}/assets/{assetId}/download/zip-all-users`

Lists every user's unexpired jobs for one asset, newest first (the web Download Queue's **Show all users**). The route is separate from the caller's own asset list, so a role can be granted or denied it on its own; the caller also needs asset `GET` permission on the asset. A caller without it, and a missing asset, get the same `404 Asset not found`, and the refusal is audited. Each item adds:

| Field         | Type    | Description                                                                                                                                                                                                                                                                                                                                                                           |
| ------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `requestedBy` | string  | The requester's user ID.                                                                                                                                                                                                                                                                                                                                                              |
| `isOwn`       | boolean | Whether the caller requested the job.                                                                                                                                                                                                                                                                                                                                                 |
| `canDownload` | boolean | The API's decision whether [Get Zip Download URL](#get-zip-download-url) would sign this archive for the caller: the job is `SUCCEEDED`, the caller may call the route, and the asset is not refused by the quarantine block (`false` for every row while the asset is quarantined without an exception and `app.compliance.quarantineBlocksDownload` is on). The route checks again. |
| `canRemove`   | boolean | Whether the caller may cancel or delete the job; only its requester may.                                                                                                                                                                                                                                                                                                              |

Another user's job carries its `selectionSummary` but an empty `selectionPreview`. Jobs created before this route existed are returned only in their requester's own lists, until they expire.

### List All Zip Downloads

`GET /download/zip`

Lists the caller's jobs across every asset, newest first. A job on an asset the caller can no longer read is left out.

**Query Parameters (all three list routes):**

| Parameter       | Type    | Default | Description                                                                        |
| --------------- | ------- | ------- | ---------------------------------------------------------------------------------- |
| `maxItems`      | integer | `100`   | Maximum jobs returned in one page (1 to 1,000).                                    |
| `pageSize`      | integer | `100`   | Jobs read per page (1 to 1,000). The smaller of `maxItems` and `pageSize` applies. |
| `startingToken` | string  | --      | `NextToken` from a previous page of the same listing.                              |

**Response:**

```json
{
    "Items": [
        {
            "requestId": "0mh3k2q9x-4f1c8a7e2b9d4c6e8f0a1b2c3d4e5f60",
            "status": "RUNNING",
            "archiveFileName": "building-package.zip",
            "progress": {
                "filesDone": 120,
                "filesTotal": 450,
                "bytesDone": 7516192768,
                "bytesTotal": 28991029248
            }
        }
    ],
    "NextToken": "eyJ1c2VySWQiOi..."
}
```

Each item carries every [job field](#job-fields); the example shows a subset. A page can hold fewer items than requested when expired jobs or jobs on unreadable assets are left out, so follow `NextToken` until it is absent. A list call also brings up to date a job whose Zip Archive run already ended.

**Error Responses:**

| Status | Description                                                                                                                                                              |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `400`  | Invalid parameters, or a `startingToken` that does not belong to this listing.                                                                                           |
| `403`  | Not authorized for this route.                                                                                                                                           |
| `404`  | `Asset not found` on the asset route (the asset does not exist or the caller may not read it; both answer alike), or Download as Zip is not enabled for this deployment. |
| `500`  | Internal server error.                                                                                                                                                   |

---

### Get Zip Download

`GET /database/{databaseId}/assets/{assetId}/download/zip/{requestId}`

Returns one of the caller's jobs with its current status and progress.

**Error Responses:**

| Status | Description                                                                                       |
| ------ | ------------------------------------------------------------------------------------------------- |
| `400`  | Invalid path parameters.                                                                          |
| `403`  | Not authorized.                                                                                   |
| `404`  | `Zip download not found` (also for another user's or an expired job), or the feature is disabled. |
| `500`  | Internal server error.                                                                            |

---

### Get Zip Download URL

`POST /database/{databaseId}/assets/{assetId}/download/zip/{requestId}/url`

Issues a presigned Amazon S3 URL for a finished archive. The URL is issued only when the job is `SUCCEEDED`, the caller still holds asset `GET` permission, the asset is still distributable and not archived, and the archive is within its retention. For another user's job on this asset, the caller must also be allowed to download from the asset (`POST /database/{databaseId}/assets/{assetId}/download`, the file download route), and every file in the archive must still be present, not archived, and not a blocked type at the time of the request. Each URL issued for another user's archive is audited with both user IDs. The URL downloads the archive as an attachment under its `archiveFileName`. It is valid for `expiresIn` seconds and is never written to the logs; request a new URL for each download.

**Response:**

```json
{
    "url": "https://<auxiliary-bucket>.s3.<region>.amazonaws.com/vams.preparedDownloads/...?X-Amz-...",
    "expiresIn": 900,
    "archiveFileName": "building-package.zip",
    "archiveSizeBytes": 28991029248
}
```

**Error Responses:**

| Status | Description                                                                                                                                                                                                                                                                                                                                                                   |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid path parameters, the asset is not distributable or is archived, or (another user's job) a file in the archive is archived, deleted, or blocked, with the message a refused create gives. The asset is quarantined without an exception and the deployment sets `app.compliance.quarantineBlocksDownload` (`message` "Asset is quarantined and cannot be downloaded"). |
| `403`  | Not authorized.                                                                                                                                                                                                                                                                                                                                                               |
| `404`  | `Zip download not found`, the caller can no longer read the asset or (another user's job) may not download from it, or the feature is disabled.                                                                                                                                                                                                                               |
| `409`  | `The archive is not ready for download` (the job is not `SUCCEEDED`).                                                                                                                                                                                                                                                                                                         |
| `410`  | `This zip download has expired` (the archive no longer exists).                                                                                                                                                                                                                                                                                                               |
| `500`  | Internal server error.                                                                                                                                                                                                                                                                                                                                                        |

---

### Cancel or Delete Zip Download

`DELETE /database/{databaseId}/assets/{assetId}/download/zip/{requestId}`

Cancels a job that is `QUEUED` or `RUNNING`: the job becomes `CANCELLED`, its Zip Archive run is aborted, and any partial upload is discarded. Deletes a job that is `SUCCEEDED`, `FAILED`, or `CANCELLED`: the archive and the job record are removed. Once the cancel is recorded, the job ends `CANCELLED` even when its run finishes at the same moment. A cancel never deletes a job: when the job finished before the cancel was recorded (for example, its archive finished uploading just as the cancel arrived), the job keeps its outcome and the call answers `"action": "ended"` with the job's final `status`; delete it with a second call if you no longer need it.

Send `intent=cancel` when the call is meant as a cancel (the web app, the CLI and the MCP cancel tool always do). The job is then never deleted, even when it finished before the call arrived, for example while a confirm dialog was open: a `SUCCEEDED` or `FAILED` job is kept and answered `"action": "ended"`, and a `CANCELLED` job answers `"action": "cancelled"`. Without `intent` the call behaves as described above.

**Query Parameters:**

| Parameter | Type   | Required | Description                                                                                                     |
| --------- | ------ | -------- | --------------------------------------------------------------------------------------------------------------- |
| `intent`  | string | No       | `cancel`: never delete the job; a job that has already finished is kept. Any other value is refused with `400`. |

**Response:**

```json
{
    "message": "Zip download cancelled",
    "action": "cancelled"
}
```

A terminal job returns `{"message": "Zip download deleted", "action": "deleted"}` (never with `intent=cancel`). A cancel that reached a job which had already finished returns `{"message": "The zip download finished before it could be cancelled", "action": "ended", "status": "SUCCEEDED"}`. `status` is present only with `ended`: it is the job's final status, `SUCCEEDED` or `FAILED`, or an empty string when another call deleted the job while the cancel ran.

**Error Responses:**

| Status | Description                                                  |
| ------ | ------------------------------------------------------------ |
| `400`  | Invalid path parameters, or an `intent` other than `cancel`. |
| `403`  | Not authorized.                                              |
| `404`  | `Zip download not found`, or the feature is disabled.        |
| `500`  | Internal server error.                                       |

---

## Job Fields

| Field              | Type    | Description                                                                                                                                                           |
| ------------------ | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `requestId`        | string  | Job identifier: a time-sortable prefix and a random suffix.                                                                                                           |
| `databaseId`       | string  | Database of the asset.                                                                                                                                                |
| `assetId`          | string  | Asset the job archives.                                                                                                                                               |
| `assetName`        | string  | The asset's name when the job was created.                                                                                                                            |
| `status`           | string  | `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, or `CANCELLED`.                                                                                                           |
| `archiveFormat`    | string  | `zip`, `tar`, or `tar.gz`.                                                                                                                                            |
| `compression`      | string  | `store` or `deflate`.                                                                                                                                                 |
| `archiveFileName`  | string  | Final archive file name, including its extension.                                                                                                                     |
| `archiveSizeBytes` | integer | Size of the finished archive; `null` until the job succeeds.                                                                                                          |
| `selectionSummary` | object  | Counts of the requested entries: `files`, `folders`, and `wholeAsset`.                                                                                                |
| `selectionPreview` | array   | The first 20 requested entries (`type`, `relativePath`, `versionId`).                                                                                                 |
| `assetVersionId`   | string  | The asset version the job is pinned to (an alias is recorded as its version ID), or `null`.                                                                           |
| `progress`         | object  | `filesDone`, `filesTotal`, `bytesDone`, `bytesTotal`. The totals are `0` until the Zip Archive job has resolved the selection, unless create knew the full file list. |
| `errorMessage`     | string  | User-safe reason for a `FAILED` job, or `null`.                                                                                                                       |
| `cancelledBy`      | string  | `user` (cancelled through this API), `execution` (the Zip Archive run was aborted from the workflow executions views), or `null`.                                     |
| `createdAt`        | string  | ISO 8601 creation time.                                                                                                                                               |
| `startedAt`        | string  | ISO 8601 time the Zip Archive run was started, or `null`.                                                                                                             |
| `completedAt`      | string  | ISO 8601 time the job reached a terminal status, or `null`.                                                                                                           |
| `expiresAt`        | string  | ISO 8601 time the job expires (`createdAt` plus 7 days); afterwards it is not listed and cannot be downloaded. See Retention above.                                   |

A job never exposes the archive's storage location or the workflow run that produced it. Only the all-users list names the requester (`requestedBy`).

## Related resources

-   [Assets API](assets.md#download-asset) -- Presigned URLs for individual files
-   [Zip Downloads user guide](../user-guide/zip-downloads.md) -- Download as Zip in the web interface
-   [Zip Download Commands](../cli/commands/zip-downloads.md) -- The `vamscli zip-download` commands
-   [Zip Archive Pipeline](../pipelines/zip-archive.md) -- The system pipeline that builds the archives
