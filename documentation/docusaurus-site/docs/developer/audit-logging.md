# Audit Logging

VAMS provides a comprehensive audit logging system that captures security-sensitive operations across all API handlers. All audit events are written to dedicated Amazon CloudWatch Log Groups with a configurable retention period (1 year by default) and optional AWS KMS encryption.

## Overview

The audit logging system focuses on authorization decisions, file operations, and system changes that occur after successful authentication. Sign-in itself is recorded by Amazon Cognito or your external identity provider, not by the VAMS audit system; the API Gateway custom Lambda authorizer records the outcome of each token verification as an authorization event.

:::info[Coverage Note]
Authorization decisions are recorded for every handler, because the entries are emitted from shared Casbin enforcement rather than from each handler. The remaining event types are emitted by the individual handlers, so a handler that does not call the matching function contributes no entry of that type.

Every API route and method is listed in the audit action registry (`backend/backend/common/auditActions.py`) with the action it records, or with the reason it records none, and a unit test fails for a route that is in neither list. Action records ([Action Records](#action-records)) are being added handler by handler; the registry's `AUDIT_PENDING_ACTIONS` lists the actions whose handlers do not write one yet.
:::

## Amazon CloudWatch Log Groups

VAMS creates nine dedicated log groups for different event types. Each log group name includes a unique hash derived from the stack name and account ID to prevent naming conflicts across deployments.

| Log Group                | Name Pattern                                           | Purpose                                                                                                                                |
| ------------------------ | ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| Authentication           | `/aws/vendedlogs/VAMSAuditAuthentication-{hash}`       | Token validation results and session creation determined by a VAMS handler                                                             |
| Authorization            | `/aws/vendedlogs/VAMSAuditAuthorization-{hash}`        | API-level permission checks, and data-level permission checks that fail (only failures are captured at the data tier, for performance) |
| Auth Other               | `/aws/vendedlogs/VAMSAuditAuthOther-{hash}`            | User profile reads and updates, and other authentication-adjacent operations                                                           |
| Auth Changes             | `/aws/vendedlogs/VAMSAuditAuthChanges-{hash}`          | Role, user-role, constraint, API key, and Amazon Cognito user changes                                                                  |
| File Upload              | `/aws/vendedlogs/VAMSAuditFileUpload-{hash}`           | File uploads to Amazon S3, upload validations                                                                                          |
| File Download            | `/aws/vendedlogs/VAMSAuditFileDownload-{hash}`         | Direct file downloads, bulk downloads, presigned URL generation                                                                        |
| File Download (Streamed) | `/aws/vendedlogs/VAMSAuditFileDownloadStreamed-{hash}` | Streaming downloads, large file transfers                                                                                              |
| Actions                  | `/aws/vendedlogs/VAMSAuditActions-{hash}`              | CRUD operations, workflow executions, pipeline runs                                                                                    |
| Errors                   | `/aws/vendedlogs/VAMSAuditErrors-{hash}`               | Application errors, validation failures, system exceptions                                                                             |

### Log Group Configuration

-   **Retention**: 1 year, applied by the `LogRetentionAspect` CDK aspect
-   **Encryption**: AWS KMS encryption when `config.app.useKmsCmkEncryption.enabled` is `true`
-   **Removal Policy**: `DESTROY` (log groups are deleted with stack deletion)

## Log Format

All audit events follow a structured format with bracketed metadata fields followed by a JSON payload. Each entry ends with a `--- [event: {...}]` suffix carrying the masked echo of the triggering API event.

Each record is one line. A value inside a bracketed field (the user ID, the role names, the event's secondary type, the probe endpoint) never carries a raw `[`, `]`, `\`, line break, other control character, or bidirectional or zero-width character: each is written as a `\uXXXX` escape, so a bracket-delimited parse such as `/\[user: (?<user>[^\]]+)\]/` reads the whole value. A user ID made only of the characters VAMS allows in user IDs (letters, digits, `_`, `-`, `.`, `+`, `@`) is written unchanged. The JSON payload and the event echo are ASCII-only JSON, with non-ASCII characters written as JSON `\uXXXX` escapes.

### Authentication Events

```text
[AUTHENTICATION][authenticated: True] [user: john.doe] [roles: ["admin"]] [mfaEnabled: True] {"method": "cognito", "ip": "192.168.1.1"}
```

### Authorization Events

A data-level denial inside a handler records the action and the object that was checked:

```text
[AUTHORIZATION][authorized: False] [user: jane.smith] [roles: ["viewer"]] [mfaEnabled: True] {"action": "DELETE", "obj": {"object__type": "database", "databaseId": "db-123"}} --- [event: {"requestContext": {"authorizer": {"jwt": {"claims": {"sub": "jane.smith"}}}}}]
```

#### Permission Probe Records

The web interface asks which routes the user may use through two probe endpoints, `POST /auth/routes` (web routes) and `GET /auth/routes/api/allowed` (API routes). Each route the user may not use is recorded as one denial, marked with a fixed `[probe: <endpoint>]` segment straight after the record prefix. The server sets the marker; no header, query string or body field can set or clear it. Only these two endpoints write it: decisions made inside other handlers, including the gateway authorizer and the API-level check, are unmarked.

```text
[AUTHORIZATION][authorized: False] [probe: GET /auth/routes/api/allowed] [user: jane.smith] [roles: ["viewer"]] [mfaEnabled: True] {"action": "DELETE", "obj": {"object__type": "api", "route__path": "/database/vams:any"}} --- [event: {"requestContext": {"authorizer": {"jwt": {"claims": {"sub": "jane.smith"}}}}}]
```

To leave probe records out of a query, anchor the pattern on the prefix plus the marker, for example `/^\[AUTHORIZATION\]\[authorized: False\] \[probe: /`. Never match the marker as a bare substring: asset fields, request paths, headers and query strings later in a real denial record are caller-controlled.

Probe records written before the marker existed carry no `[probe: ...]` segment. They can be told apart by their object and event echo:

-   `object__type` `web` is written only by `POST /auth/routes`.
-   An `object__type` `api` record whose event echo is the minimal `{"requestContext": {"authorizer": {"jwt": {"claims": {"sub": ...}}}}}` is a probe, because the API-level check on a real call echoes the full request. Probes of templated routes also carry the `vams:any` path segment; probes of untemplated routes, such as `/database`, do not.
-   Data-level denials carry the same minimal echo, so the echo identifies a probe only together with `object__type` `api`.

### File Operation Events

Uploads and downloads use the `[FILEUPLOAD]`, `[FILEDOWNLOAD]`, and `[FILEDOWNLOAD-STREAMED]` types.

```text
[FILEUPLOAD] [user: john.doe] [roles: ["editor"]] [mfaEnabled: True] {"databaseId": "db-123", "assetId": "asset-456", "filePath": "/data/model.obj", "uploadDenied": false, "customData": {"fileSize": 1024000}}
```

### Error Events

```text
[ERRORS][type: validation_error] [user: john.doe] [roles: ["editor"]] [mfaEnabled: True] {"error": "Invalid asset ID format", "assetId": "invalid-id"}
```

The `[AUTHOTHER]`, `[AUTHCHANGES]`, and `[ACTIONS]` types follow the same shape as `[ERRORS]`, with the caller-supplied `secondary_type` in the `[type: ...]` field.

## Action Records

An action record says who did what, to which data, with what outcome, and what a write changed. It is written by `log_action()` and follows the record contract below, version 1 (`auditVersion: 1`). Within a version keys are only added; renaming or removing a key needs a new version.

### Format

An action record keeps the prefix of the existing records, so the patterns above still match it:

```text
[ACTIONS][type: assetUpdate] [user: john.doe] [roles: ["editor"]] [mfaEnabled: True] {"auditVersion": 1, "recordId": "2f0c...", "action": "assetUpdate", "outcome": "success", "eventTime": "2026-11-12T19:12:03.417Z", "actor": {"userId": "john.doe", "authMethod": "cognito"}, "request": {"requestId": "c3a1...", "method": "PUT", "route": "/database/{databaseId}/assets/{assetId}", "sourceIp": "198.51.100.7", "lambdaRequestId": "9b2e..."}, "entity": {"type": "asset", "id": "asset-456"}, "databaseId": "db-123", "assetId": "asset-456", "changes": {"description": "New text"}, "previous": {"description": "Old text"}} --- [event: {"requestContext": {"requestId": "c3a1...", "http": {"method": "PUT", "path": "/database/db-123/assets/asset-456"}}}]
```

-   The `[type: ...]` value is the action name, or for an action that existed before the contract its earlier type (for example `constraintCreateUpdate` for `constraintCreate` and `constraintUpdate`), so existing queries keep matching.
-   Records in the file upload and download groups carry no `[type: ...]` segment, as their earlier records do not; the action is in the payload.
-   A denial is written to the authorization group with the `[AUTHORIZATION][authorized: False]` prefix and `"outcome": "denied"`.
-   The request echo of an action record is compact: the request ID, method, path and route key, with no headers, query string or body. What a write changed is in the payload instead.

To parse a record, take the payload from the first `{` after `[mfaEnabled: True] ` or `[mfaEnabled: False] ` and decode it with a raw JSON decoder from that index; do not split on `--- [event:`. A record over the per-event size limit ends in ` ...[truncated]` and its JSON does not decode; treat it as partial.

### Payload Keys

A key that does not apply is left out, never written as `null`.

| Key                                                                            | Type    | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------------------------------------------------------------ | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `auditVersion`                                                                 | integer | `1`                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `recordId`                                                                     | string  | A unique ID. Delivery is at least once, so a repeated `recordId` is a duplicate.                                                                                                                                                                                                                                                                                                                                                      |
| `action`                                                                       | string  | The action name from the registry, lower camelCase (`assetCreate`, `fileDownload`, `search`).                                                                                                                                                                                                                                                                                                                                         |
| `outcome`                                                                      | string  | `success`, `failed` (authorized, attempted, failed) or `denied`.                                                                                                                                                                                                                                                                                                                                                                      |
| `reason`                                                                       | string  | On `failed` and `denied` only: a generic category such as `notFound`, `validation`, `conflict`, `internal` or `forbidden`, never error text.                                                                                                                                                                                                                                                                                          |
| `eventTime`                                                                    | string  | UTC time the action completed, ISO 8601 with milliseconds and `Z`.                                                                                                                                                                                                                                                                                                                                                                    |
| `actor`                                                                        | object  | `userId` (the same value as the prefix user); `authMethod` (`cognito`, `externalIdp`, `jwt` when the token's issuer is not known, `apiKey` or `system`); `apiKeyId`; `crossCall`; `onBehalfOf`.                                                                                                                                                                                                                                       |
| `request`                                                                      | object  | `requestId`; `method`; `route` (the route template, never the concrete path; `unmatched` for a path no route matches); `sourceIp` (the address API Gateway reports for the connection: on a deployment behind Amazon CloudFront this is the CloudFront edge address, not the client's; recording the client address is tracked in [#1434](https://github.com/awslabs/visual-asset-management-system/issues/1434)); `lambdaRequestId`. |
| `entity`                                                                       | object  | `type` (for example `asset`, `file`, `metadata`, `workflow`) and `id`.                                                                                                                                                                                                                                                                                                                                                                |
| `databaseId`, `assetId`, `filePath`, `versionId`, `assetVersionId`, and others | string  | The IDs of the data the action touched, including `pipelineId`, `workflowId`, `executionId`, `commentId`, `tagId`, `subscriptionId`, `roleName`, `constraintId`, the compliance `schemaName`, `cascadeId` and `evaluationId`, and the `target*` IDs of a copy or move.                                                                                                                                                                |
| `bytes`, `fileCount`, `itemCount`, `durationMs`                                | integer | Sizes and counts the handler already knows. No AWS call is made to fill them.                                                                                                                                                                                                                                                                                                                                                         |
| `changes`                                                                      | object  | For a write: each field it set, with its new value.                                                                                                                                                                                                                                                                                                                                                                                   |
| `previous`                                                                     | object  | For a write: the prior values of those fields, when the handler had already loaded the record. No extra read is made to fill it.                                                                                                                                                                                                                                                                                                      |
| `requestParameters`                                                            | object  | For a read, search or download: what was asked (IDs, filters, page size). What was returned is never recorded.                                                                                                                                                                                                                                                                                                                        |
| `details`                                                                      | object  | Other facts about the action (flags, trigger type, index name).                                                                                                                                                                                                                                                                                                                                                                       |
| `truncated`                                                                    | boolean | `true` when a value or the record was cut to the size caps.                                                                                                                                                                                                                                                                                                                                                                           |

The keys of the earlier orchestration and authorization records stay alongside these. When an earlier record's own key has the name of a contract key with a different value (an AMI recipe build writes its own `outcome`, such as `in-progress`), the contract value is written and the earlier value moves under `legacy`, for example `"legacy": {"outcome": "in-progress"}`, so every contract key keeps its contract meaning.

### What Reads and Writes Record

-   **A write** records what it wrote or changed: each changed field with its new value in `changes`, and the prior values in `previous` where the handler already held the record.
-   **A read, search or download** records the request: the route, the IDs, the filters and page size in `requestParameters`, and the outcome. The content returned is never recorded. The registry gives each action its kind, and `log_action` drops `changes` and `previous` from a read, search or download record, whatever the caller passes. A read or search record may carry `itemCount`, the number of items or hits returned (a count, never the items); that one field is controlled by `AUDIT_READ_ITEM_COUNT` in `auditLogging.py`.
-   **A bulk operation** records one record per item, all delivered together at the end of the request.
-   **File content** is never recorded.

### Reads Switch

Read records are on by default. `app.auditLogging.recordReads` in `config.json` turns them off ([Configuration reference](../deployment/configuration-reference.md#audit-logging-appauditlogging)):

-   **`true`, or the setting absent** -- every successful get, list and search writes an action record. A deployment upgraded with a `config.json` that has no `auditLogging` block records reads.
-   **`false`** -- successful gets, lists and searches write no action record. Writes, denials, failed reads, downloads and exports are recorded as before, and so are the records the earlier functions write (for example the `userProfileGet` record of a login profile read, and the action records an add-on writes itself).

CDK passes the value to every VAMS Lambda function as the `AUDIT_RECORD_READS` environment variable. Only the value `false` turns reads off, so a function deployed without the variable records them.

Reads are the most frequent API calls, so they make up most of the action records of a read-heavy deployment such as the web app: expect several times the record count and ingestion of writes alone. Use the [Amazon CloudWatch pricing page](https://aws.amazon.com/cloudwatch/pricing/) or the AWS Pricing Calculator to estimate the cost in your Region, and measure your own volume with a Logs Insights query such as `stats count(*) by action` over the actions log group.

### Redaction and Size Caps

-   Every value section (`changes`, `previous`, `requestParameters`, `details`) passes through the same key-name redaction as the request echo (see [Automatic Data Masking](#automatic-data-masking)), so a credential or template body is written as `<redacted>`.
-   A value whose JSON text is over 2 KB is replaced by `{"truncated": true, "sha256": "<hex>", "bytes": <size>, "preview": "<first characters>"}`. The SHA-256 is of the value's UTF-8 bytes for a string, and of its canonical JSON text (sorted keys, no spaces, ASCII) otherwise, so you can confirm a copy of the value you hold matches what was written.
-   A whole action record, prefix, payload and request echo together, is bounded to 32 KB. Past it the request echo is replaced first, by `--- [event: {"truncated": true}]`; then the largest values lose their preview and keep their hash, then whole sections are replaced by `{"truncated": true, "sha256", "fieldCount"}`. The payload then carries `"truncated": true`. A prefix that alone leaves no room (a caller with hundreds of long role names) cuts the record, which ends in ` ...[truncated]` and is read as a partial record.

### Delivery

A handler entrypoint opens the request's audit context with `audit_request(event, context)`. Inside it:

-   The caller's identity is resolved once per request instead of once per record.
-   Successful action records, and the allowed authorization record of the call's API-level route check, are held and written when the handler returns, in one write per audit log group, all groups in parallel, bounded by a 1.5-second deadline. A request that touches three groups costs about one write's time, not three, and a request whose only record is its allowed route check makes one write. A held authorization record is the same record, byte for byte, that an immediate write produces; only its write time changes. An authorization record carries no time of its own, so its CloudWatch `@timestamp` is the time the request finished, not the time of the check; the request time stays in the echoed event's `requestContext.requestTimeEpoch`. Data-level checks record denials only, so no allowed data-level record is held.
-   A timer writes the held records 750 milliseconds before the function times out, so a timing-out request still delivers them. A write never waits past 250 milliseconds before the timeout: the deadline is the shorter of 1.5 seconds and the time left less 250 milliseconds, so the records it could not confirm are written to the function log, and counted, while the function still runs.
-   Denials (authorization denials included), failures and error records are written at once.
-   A record not confirmed by the deadline is kept in the function log as an [undelivered entry](#monitoring-audit-logging-health) with reason `flushDeadlineExceeded`. Its write may still complete, so a record can appear twice. `recordId` identifies the copy of an action record; an authorization record carries no `recordId`, and its copy is the same text: the same `requestContext.requestId` in the echo, the same prefix and the same payload.

When a handler forwards a call to another VAMS function with its caller's authorization (an execute reading the asset's metadata, an ingest creating the asset and its upload), the forwarded call is recorded as the work of that request, not as a separate request by the user: its records carry `actor.crossCall` `true`, `actor.onBehalfOf` naming the user and the caller's `request.requestId`. The marker sets attribution only; the forwarded call is authorized as before.

Outside an audit context (a function that does not open one, or a record written from a worker thread) every record, allowed authorization records included, is written at once as before. Inside `deferred_authorization_audit()` (search's per-hit checks) denials are still collected and written together when the block ends; allowed checks join the request's flush.

If a function stops without returning (out of memory, a crash), records held for it are lost, the allowed authorization record of the call included. Authorization denials are never held for the request's flush: a route or data-level denial is written before the handler continues, and the denials collected inside `deferred_authorization_audit()` are written when that block ends, so a crash inside the block loses them.

### Request Outcomes

Every Lambda entrypoint is decorated with `@audited_handler`, which opens the request's audit context and records the outcome of a request whose handler recorded no record for the route's action:

-   **An error response or an exception** is recorded as the route's action with `"outcome": "failed"` and a generic `reason` taken from the status code: `validation` (400, 405, 415, 422), `notFound` (404, 410), `conflict` (409, 412), `tooLarge` (413), `throttled` (429), `clientError` (any other 4xx) or `internal` (5xx and exceptions). The record carries the ID fields of the request path, never the error text. The error record the response writes to the errors log group is unchanged.
-   **A denial (401, 403)** adds no action record: the authorization record is the denial's record.
-   **A success** adds nothing: the handler records its own action after the write succeeds.

The roles, user roles, constraints, API keys, Amazon Cognito users and login profile routes keep their current authorization-change and auth records; their outcomes move to the record contract with the auth records ([#1434](https://github.com/awslabs/visual-asset-management-system/issues/1434)).

### Action Registry

`backend/backend/common/auditActions.py` maps every route and method of the API (core, add-on and fork routes) to the action it records: its name, audit log group, entity type and kind (`write`, `read`, `search` or `download`). Routes that record nothing are listed with a reason, for example the unauthenticated configuration routes and the permission probes, whose denials are recorded by the probe itself. Handlers that serve no API route (bucket sync, the workflow state machine steps, the search reindexer) are listed with their actions as system actions. The add-on actions are in `common/addonAuditActions.py`, and a fork adds its own in `custom/customAuditActions.py` (see [Customizing VAMS](customizing-vams.md)).

### Coverage

Every action below is recorded today. The kind decides what the record holds: a **write** records what it wrote or changed, a **read**, **search** or **download** the request only. Actions with **(see note)** are the reads of the auth routes, whose records move to the record contract with the auth records ([#1434](https://github.com/awslabs/visual-asset-management-system/issues/1434)); until then a read of those routes writes its earlier auth record where it has one. Add-on actions are registered in `common/addonAuditActions.py` and described with each add-on (see [Add-ons Overview](addons-overview.md)).

| Action                             | Log group                | Entity           | Kind     | Recorded by                                                                                                                                                                                                                   |
| ---------------------------------- | ------------------------ | ---------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `databaseList`                     | Actions                  | database         | read     | `GET /database`                                                                                                                                                                                                               |
| `databaseCreate`                   | Actions                  | database         | write    | `POST /database`                                                                                                                                                                                                              |
| `databaseGet`                      | Actions                  | database         | read     | `GET /database/{databaseId}`                                                                                                                                                                                                  |
| `databaseUpdate`                   | Actions                  | database         | write    | `PUT /database/{databaseId}`                                                                                                                                                                                                  |
| `databaseDelete`                   | Actions                  | database         | write    | `DELETE /database/{databaseId}`                                                                                                                                                                                               |
| `assetList`                        | Actions                  | asset            | read     | `GET /assets`<br />`GET /database/{databaseId}/assets`                                                                                                                                                                        |
| `assetCreate`                      | Actions                  | asset            | write    | `POST /assets`                                                                                                                                                                                                                |
| `assetGet`                         | Actions                  | asset            | read     | `GET /database/{databaseId}/assets/{assetId}`                                                                                                                                                                                 |
| `assetUpdate`                      | Actions                  | asset            | write    | `PUT /database/{databaseId}/assets/{assetId}`                                                                                                                                                                                 |
| `assetArchive`                     | Actions                  | asset            | write    | `DELETE /database/{databaseId}/assets/{assetId}/archiveAsset`                                                                                                                                                                 |
| `assetUnarchive`                   | Actions                  | asset            | write    | `PUT /database/{databaseId}/assets/{assetId}/unarchiveAsset`                                                                                                                                                                  |
| `assetDelete`                      | Actions                  | asset            | write    | `DELETE /database/{databaseId}/assets/{assetId}/deleteAsset`                                                                                                                                                                  |
| `assetIngest`                      | Actions                  | asset            | write    | `POST /ingest-asset`                                                                                                                                                                                                          |
| `fileDownload`                     | File download            | file             | download | `POST /database/{databaseId}/assets/{assetId}/download`                                                                                                                                                                       |
| `fileStream`                       | File download (streamed) | file             | download | `GET /database/{databaseId}/assets/{assetId}/download/stream/{proxy+}`                                                                                                                                                        |
| `auxiliaryPreviewStream`           | File download (streamed) | file             | download | `GET /database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}`                                                                                                                                          |
| `assetExport`                      | Actions                  | export           | download | `POST /database/{databaseId}/assets/{assetId}/export`                                                                                                                                                                         |
| `assetHistoryGet`                  | Actions                  | asset            | read     | `GET /database/{databaseId}/assets/{assetId}/assetHistory`                                                                                                                                                                    |
| `fileList`                         | Actions                  | file             | read     | `GET /database/{databaseId}/assets/{assetId}/listFiles`                                                                                                                                                                       |
| `fileInfoGet`                      | Actions                  | file             | read     | `GET /database/{databaseId}/assets/{assetId}/fileInfo`                                                                                                                                                                        |
| `fileMove`                         | Actions                  | file             | write    | `POST /database/{databaseId}/assets/{assetId}/moveFile`                                                                                                                                                                       |
| `fileCopy`                         | Actions                  | file             | write    | `POST /database/{databaseId}/assets/{assetId}/copyFile`                                                                                                                                                                       |
| `fileArchive`                      | Actions                  | file             | write    | `DELETE /database/{databaseId}/assets/{assetId}/archiveFile`                                                                                                                                                                  |
| `fileUnarchive`                    | Actions                  | file             | write    | `POST /database/{databaseId}/assets/{assetId}/unarchiveFile`                                                                                                                                                                  |
| `fileDelete`                       | Actions                  | file             | write    | `DELETE /database/{databaseId}/assets/{assetId}/deleteFile`                                                                                                                                                                   |
| `assetPreviewDelete`               | Actions                  | file             | write    | `DELETE /database/{databaseId}/assets/{assetId}/deleteAssetPreview`                                                                                                                                                           |
| `auxiliaryPreviewDelete`           | Actions                  | file             | write    | `DELETE /database/{databaseId}/assets/{assetId}/deleteAuxiliaryPreviewAssetFiles`                                                                                                                                             |
| `fileVersionRevert`                | Actions                  | fileVersion      | write    | `POST /database/{databaseId}/assets/{assetId}/revertFileVersion/{versionId}`                                                                                                                                                  |
| `filePrimarySet`                   | Actions                  | file             | write    | `PUT /database/{databaseId}/assets/{assetId}/setPrimaryFile`                                                                                                                                                                  |
| `folderCreate`                     | Actions                  | folder           | write    | `POST /database/{databaseId}/assets/{assetId}/createFolder`                                                                                                                                                                   |
| `assetVersionCreate`               | Actions                  | assetVersion     | write    | `POST /database/{databaseId}/assets/{assetId}/createVersion`                                                                                                                                                                  |
| `assetVersionRevert`               | Actions                  | assetVersion     | write    | `POST /database/{databaseId}/assets/{assetId}/revertAssetVersion/{assetVersionId}`                                                                                                                                            |
| `assetVersionList`                 | Actions                  | assetVersion     | read     | `GET /database/{databaseId}/assets/{assetId}/getVersions`                                                                                                                                                                     |
| `assetVersionGet`                  | Actions                  | assetVersion     | read     | `GET /database/{databaseId}/assets/{assetId}/getVersion/{assetVersionId}`                                                                                                                                                     |
| `assetVersionUpdate`               | Actions                  | assetVersion     | write    | `PUT /database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}`                                                                                                                                                  |
| `assetVersionArchive`              | Actions                  | assetVersion     | write    | `POST /database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/archive`                                                                                                                                         |
| `assetVersionUnarchive`            | Actions                  | assetVersion     | write    | `POST /database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/unarchive`                                                                                                                                       |
| `fileUploadStart`                  | File upload              | file             | write    | `POST /uploads`                                                                                                                                                                                                               |
| `fileUpload`                       | File upload              | file             | write    | `POST /uploads/{uploadId}/complete`<br />(system: sqsUploadFileLarge)                                                                                                                                                         |
| `pipelineOutputUpload`             | File upload              | file             | write    | `POST /uploads/{uploadId}/complete/external`                                                                                                                                                                                  |
| `commentList`                      | Actions                  | comment          | read     | `GET /comments/assets/{assetId}`<br />`GET /comments/assets/{assetId}/assetVersionId/{assetVersionId}`                                                                                                                        |
| `commentGet`                       | Actions                  | comment          | read     | `GET /comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}`                                                                                                                                          |
| `commentCreate`                    | Actions                  | comment          | write    | `POST /comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}`                                                                                                                                         |
| `commentUpdate`                    | Actions                  | comment          | write    | `PUT /comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}`                                                                                                                                          |
| `commentDelete`                    | Actions                  | comment          | write    | `DELETE /comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}`                                                                                                                                       |
| `metadataGet`                      | Actions                  | assetLink        | read     | `GET /asset-links/{assetLinkId}/metadata`<br />`GET /database/{databaseId}/assets/{assetId}/metadata`<br />`GET /database/{databaseId}/assets/{assetId}/metadata/file`<br />`GET /database/{databaseId}/metadata`             |
| `metadataCreate`                   | Actions                  | assetLink        | write    | `POST /asset-links/{assetLinkId}/metadata`<br />`POST /database/{databaseId}/assets/{assetId}/metadata`<br />`POST /database/{databaseId}/assets/{assetId}/metadata/file`<br />`POST /database/{databaseId}/metadata`         |
| `metadataUpdate`                   | Actions                  | assetLink        | write    | `PUT /asset-links/{assetLinkId}/metadata`<br />`PUT /database/{databaseId}/assets/{assetId}/metadata`<br />`PUT /database/{databaseId}/assets/{assetId}/metadata/file`<br />`PUT /database/{databaseId}/metadata`             |
| `metadataDelete`                   | Actions                  | assetLink        | write    | `DELETE /asset-links/{assetLinkId}/metadata`<br />`DELETE /database/{databaseId}/assets/{assetId}/metadata`<br />`DELETE /database/{databaseId}/assets/{assetId}/metadata/file`<br />`DELETE /database/{databaseId}/metadata` |
| `metadataSchemaList`               | Actions                  | metadataSchema   | read     | `GET /metadataschema`                                                                                                                                                                                                         |
| `metadataSchemaCreate`             | Actions                  | metadataSchema   | write    | `POST /metadataschema`                                                                                                                                                                                                        |
| `metadataSchemaUpdate`             | Actions                  | metadataSchema   | write    | `PUT /metadataschema`                                                                                                                                                                                                         |
| `metadataSchemaGet`                | Actions                  | metadataSchema   | read     | `GET /database/{databaseId}/metadataSchema/{metadataSchemaId}`                                                                                                                                                                |
| `metadataSchemaDelete`             | Actions                  | metadataSchema   | write    | `DELETE /database/{databaseId}/metadataSchema/{metadataSchemaId}`                                                                                                                                                             |
| `pipelineList`                     | Actions                  | pipeline         | read     | `GET /pipelines`<br />`GET /database/{databaseId}/pipelines`                                                                                                                                                                  |
| `pipelineCreate`                   | Actions                  | pipeline         | write    | `POST /database/{databaseId}/pipelines`                                                                                                                                                                                       |
| `pipelineGet`                      | Actions                  | pipeline         | read     | `GET /database/{databaseId}/pipelines/{pipelineId}`                                                                                                                                                                           |
| `pipelineUpdate`                   | Actions                  | pipeline         | write    | `PUT /database/{databaseId}/pipelines/{pipelineId}`                                                                                                                                                                           |
| `pipelineArchive`                  | Actions                  | pipeline         | write    | `DELETE /database/{databaseId}/pipelines/{pipelineId}`                                                                                                                                                                        |
| `pipelineTemplateList`             | Actions                  | pipelineTemplate | read     | `GET /database/{databaseId}/pipelines/{pipelineId}/templates`                                                                                                                                                                 |
| `pipelineTemplateCreate`           | Actions                  | pipelineTemplate | write    | `POST /database/{databaseId}/pipelines/{pipelineId}/templates`                                                                                                                                                                |
| `pipelineTemplateGet`              | Actions                  | pipelineTemplate | read     | `GET /database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}`                                                                                                                                                    |
| `pipelineTemplateUpdate`           | Actions                  | pipelineTemplate | write    | `PUT /database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}`                                                                                                                                                    |
| `pipelineTemplateDelete`           | Actions                  | pipelineTemplate | write    | `DELETE /database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}`                                                                                                                                                 |
| `pipelineTemplateTagSchemaGet`     | Actions                  | pipelineTemplate | read     | `GET /database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}/tagSchema`                                                                                                                                          |
| `pipelineTemplateTagSchemaUpdate`  | Actions                  | pipelineTemplate | write    | `PUT /database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}/tagSchema`                                                                                                                                          |
| `workflowList`                     | Actions                  | workflow         | read     | `GET /workflows`<br />`GET /database/{databaseId}/workflows`                                                                                                                                                                  |
| `workflowCreate`                   | Actions                  | workflow         | write    | `POST /database/{databaseId}/workflows`                                                                                                                                                                                       |
| `workflowGet`                      | Actions                  | workflow         | read     | `GET /database/{databaseId}/workflows/{workflowId}`                                                                                                                                                                           |
| `workflowUpdate`                   | Actions                  | workflow         | write    | `PUT /database/{databaseId}/workflows/{workflowId}`                                                                                                                                                                           |
| `workflowArchive`                  | Actions                  | workflow         | write    | `DELETE /database/{databaseId}/workflows/{workflowId}`                                                                                                                                                                        |
| `workflowTriggerList`              | Actions                  | workflowTrigger  | read     | `GET /database/{databaseId}/workflows/{workflowId}/triggers`                                                                                                                                                                  |
| `workflowTriggerGet`               | Actions                  | workflowTrigger  | read     | `GET /database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}`                                                                                                                                                    |
| `workflowTriggerSet`               | Actions                  | workflowTrigger  | write    | `PUT /database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}`                                                                                                                                                    |
| `workflowTriggerDelete`            | Actions                  | workflowTrigger  | write    | `DELETE /database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}`                                                                                                                                                 |
| `workflowExecutionList`            | Actions                  | execution        | read     | `GET /database/{databaseId}/assets/{assetId}/workflows/executions`<br />`GET /database/{databaseId}/assets/{assetId}/workflows/executions/{workflowId}`<br />`GET /workflows/executions`                                      |
| `workflowExecute`                  | Actions                  | execution        | write    | `POST /workflows/{workflowDatabaseId}/{workflowId}/execute`                                                                                                                                                                   |
| `workflowExecutionAbort`           | Actions                  | execution        | write    | `DELETE /workflows/executions/{executionId}`                                                                                                                                                                                  |
| `workflowExecutionGet`             | Actions                  | execution        | read     | `GET /workflows/executions/{executionId}/details`                                                                                                                                                                             |
| `workflowExecutionMetadataGet`     | Actions                  | execution        | read     | `GET /workflows/executions/{executionId}/details/metadata`                                                                                                                                                                    |
| `workflowExecutionFilesGet`        | Actions                  | execution        | read     | `GET /workflows/executions/{executionId}/details/files`                                                                                                                                                                       |
| `workflowExecutionResultsGet`      | Actions                  | execution        | read     | `GET /workflows/executions/{executionId}/details/results`                                                                                                                                                                     |
| `workflowExecutionLogsGet`         | Actions                  | execution        | read     | `GET /workflows/executions/{executionId}/logs`                                                                                                                                                                                |
| `workflowExecutionRerun`           | Actions                  | execution        | write    | `POST /workflows/executions/{executionId}/rerun`                                                                                                                                                                              |
| `workflowExecutionPermanentDelete` | Actions                  | execution        | write    | `DELETE /workflows/executions/{executionId}/permanent`                                                                                                                                                                        |
| `assetLinkCreate`                  | Actions                  | assetLink        | write    | `POST /asset-links`                                                                                                                                                                                                           |
| `assetLinkGet`                     | Actions                  | assetLink        | read     | `GET /asset-links/single/{assetLinkId}`                                                                                                                                                                                       |
| `assetLinkUpdate`                  | Actions                  | assetLink        | write    | `PUT /asset-links/{assetLinkId}`                                                                                                                                                                                              |
| `assetLinkDelete`                  | Actions                  | assetLink        | write    | `DELETE /asset-links/{assetLinkId}`                                                                                                                                                                                           |
| `assetLinkList`                    | Actions                  | assetLink        | read     | `GET /database/{databaseId}/assets/{assetId}/asset-links`                                                                                                                                                                     |
| `subscriptionList`                 | Actions                  | subscription     | read     | `GET /subscriptions`                                                                                                                                                                                                          |
| `subscriptionCreate`               | Actions                  | subscription     | write    | `POST /subscriptions`                                                                                                                                                                                                         |
| `subscriptionUpdate`               | Actions                  | subscription     | write    | `PUT /subscriptions`                                                                                                                                                                                                          |
| `subscriptionDelete`               | Actions                  | subscription     | write    | `DELETE /subscriptions`                                                                                                                                                                                                       |
| `subscriptionCheck`                | Actions                  | subscription     | read     | `POST /check-subscription`                                                                                                                                                                                                    |
| `subscriptionUnsubscribe`          | Actions                  | subscription     | write    | `DELETE /unsubscribe`                                                                                                                                                                                                         |
| `tagList`                          | Actions                  | tag              | read     | `GET /tags`                                                                                                                                                                                                                   |
| `tagCreate`                        | Actions                  | tag              | write    | `POST /tags`                                                                                                                                                                                                                  |
| `tagUpdate`                        | Actions                  | tag              | write    | `PUT /tags`                                                                                                                                                                                                                   |
| `tagDelete`                        | Actions                  | tag              | write    | `DELETE /tags/{tagId}`                                                                                                                                                                                                        |
| `tagTypeList`                      | Actions                  | tagType          | read     | `GET /tag-types`                                                                                                                                                                                                              |
| `tagTypeCreate`                    | Actions                  | tagType          | write    | `POST /tag-types`                                                                                                                                                                                                             |
| `tagTypeUpdate`                    | Actions                  | tagType          | write    | `PUT /tag-types`                                                                                                                                                                                                              |
| `tagTypeDelete`                    | Actions                  | tagType          | write    | `DELETE /tag-types/{tagTypeId}`                                                                                                                                                                                               |
| `roleList` (see note)              | Auth other               | role             | read     | `GET /roles`                                                                                                                                                                                                                  |
| `roleCreate`                       | Auth changes             | role             | write    | `POST /roles`                                                                                                                                                                                                                 |
| `roleUpdate`                       | Auth changes             | role             | write    | `PUT /roles`                                                                                                                                                                                                                  |
| `roleDelete`                       | Auth changes             | role             | write    | `DELETE /roles/{roleId}`                                                                                                                                                                                                      |
| `userRoleList` (see note)          | Auth other               | userRole         | read     | `GET /user-roles`                                                                                                                                                                                                             |
| `userRoleCreate`                   | Auth changes             | userRole         | write    | `POST /user-roles`                                                                                                                                                                                                            |
| `userRoleUpdate`                   | Auth changes             | userRole         | write    | `PUT /user-roles`                                                                                                                                                                                                             |
| `userRoleDelete`                   | Auth changes             | userRole         | write    | `DELETE /user-roles`                                                                                                                                                                                                          |
| `constraintList` (see note)        | Auth other               | constraint       | read     | `GET /auth/constraints`                                                                                                                                                                                                       |
| `constraintGet` (see note)         | Auth other               | constraint       | read     | `GET /auth/constraints/{constraintId}`                                                                                                                                                                                        |
| `constraintCreate`                 | Auth changes             | constraint       | write    | `POST /auth/constraints/{constraintId}`                                                                                                                                                                                       |
| `constraintUpdate`                 | Auth changes             | constraint       | write    | `PUT /auth/constraints/{constraintId}`                                                                                                                                                                                        |
| `constraintDelete`                 | Auth changes             | constraint       | write    | `DELETE /auth/constraints/{constraintId}`                                                                                                                                                                                     |
| `constraintTemplateImport`         | Auth changes             | constraint       | write    | `POST /auth/constraintsTemplateImport`                                                                                                                                                                                        |
| `loginProfileGet`                  | Auth other               | user             | read     | `GET /auth/loginProfile/{userId}`                                                                                                                                                                                             |
| `loginProfileUpdate`               | Auth other               | user             | write    | `POST /auth/loginProfile/{userId}`                                                                                                                                                                                            |
| `apiKeyList` (see note)            | Auth other               | apiKey           | read     | `GET /auth/api-keys`<br />`GET /auth/user/api-keys`                                                                                                                                                                           |
| `apiKeyCreate`                     | Auth changes             | apiKey           | write    | `POST /auth/api-keys`<br />`POST /auth/user/api-keys`                                                                                                                                                                         |
| `apiKeyGet` (see note)             | Auth other               | apiKey           | read     | `GET /auth/api-keys/{apiKeyId}`<br />`GET /auth/user/api-keys/{apiKeyId}`                                                                                                                                                     |
| `apiKeyUpdate`                     | Auth changes             | apiKey           | write    | `PUT /auth/api-keys/{apiKeyId}`<br />`PUT /auth/user/api-keys/{apiKeyId}`                                                                                                                                                     |
| `apiKeyDelete`                     | Auth changes             | apiKey           | write    | `DELETE /auth/api-keys/{apiKeyId}`<br />`DELETE /auth/user/api-keys/{apiKeyId}`                                                                                                                                               |
| `cognitoUserList` (see note)       | Auth other               | user             | read     | `GET /user/cognito`                                                                                                                                                                                                           |
| `cognitoUserCreate`                | Auth changes             | user             | write    | `POST /user/cognito`                                                                                                                                                                                                          |
| `cognitoUserUpdate`                | Auth changes             | user             | write    | `PUT /user/cognito/{userId}`                                                                                                                                                                                                  |
| `cognitoUserDelete`                | Auth changes             | user             | write    | `DELETE /user/cognito/{userId}`                                                                                                                                                                                               |
| `cognitoUserPasswordReset`         | Auth changes             | user             | write    | `POST /user/cognito/{userId}/resetPassword`                                                                                                                                                                                   |
| `search`                           | Actions                  | search           | search   | `POST /search`<br />`POST /search/simple`                                                                                                                                                                                     |
| `workflowPipelineRefresh`          | Actions                  | workflow         | write    | (within a request)                                                                                                                                                                                                            |
| `workflowRedeploy`                 | Actions                  | workflow         | write    | (within a request)                                                                                                                                                                                                            |
| `workflowExecutionGroupAbort`      | Actions                  | executionGroup   | write    | (within a request)                                                                                                                                                                                                            |
| `assetSync`                        | Actions                  | asset            | write    | (system: sqsBucketSync)                                                                                                                                                                                                       |
| `fileSync`                         | Actions                  | file             | write    | (system: sqsBucketSync)                                                                                                                                                                                                       |
| `searchReindex`                    | Actions                  | system           | write    | (system: crReindexer)                                                                                                                                                                                                         |
| `workflowOutputIngest`             | Actions                  | execution        | write    | (system: processWorkflowExecutionOutput)                                                                                                                                                                                      |
| `workflowExecutionEnd`             | Actions                  | execution        | write    | (system: processWorkflowExecutionOutput)<br />(system: handleExecutionError)                                                                                                                                                  |
| `pipelineImport`                   | Actions                  | pipeline         | write    | (system: importGlobalPipelineWorkflow)                                                                                                                                                                                        |
| `originVerifyRotate`               | Auth changes             | system           | write    | (system: apiOriginVerifyRotation)                                                                                                                                                                                             |

What the main records carry, beyond the [payload keys](#payload-keys) every record has:

| Area                              | IDs                                                                                                      | Sizes and counts                                                                                            | Values                                                                                                                                                          |
| --------------------------------- | -------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Databases                         | `databaseId`                                                                                             | `itemCount` on a list                                                                                       | Create: the fields written; update: the fields written and their prior values                                                                                   |
| Assets                            | `databaseId`, `assetId`                                                                                  | `itemCount` on a list or history read                                                                       | Create: the validated request; update: the fields written and their prior values; archive, delete: the flags and reason                                         |
| Files and folders                 | `databaseId`, `assetId`, `filePath`; `target*` on a copy or move                                         | One record per file of a folder operation; `fileCount` on a copy or move                                    | Delete and archive flags; a revert's new and prior `versionId`; a primary type                                                                                  |
| Asset versions                    | `databaseId`, `assetId`, `assetVersionId`                                                                | `fileCount` on a create that names its files                                                                | The comment, alias and archive state written                                                                                                                    |
| Ingest                            | `databaseId`, `assetId`, `uploadId`                                                                      | `fileCount` (initialize: requested; complete: landed); `details.declaredBytes` on initialize, never `bytes` | The asset fields; the file keys in `details.files`                                                                                                              |
| Export                            | `databaseId`, `assetId`                                                                                  | `itemCount` (assets), `fileCount` (files)                                                                   | The export request                                                                                                                                              |
| Comments                          | `databaseId`, `assetId`, `assetVersionId`, `commentId`                                                   | --                                                                                                          | The comment body written; an edit's prior body                                                                                                                  |
| Metadata                          | The target's IDs (`databaseId`, `assetId`, `filePath`, `assetLinkId`)                                    | `itemCount`: entries written, deleted or returned                                                           | One record per target: each entry written (`value`, `valueType`) or `details.deletedKeys`                                                                       |
| Asset links                       | `assetLinkId`; the source as `databaseId`/`assetId`, the target as `target*`                             | --                                                                                                          | The link fields written; an update's prior values                                                                                                               |
| Subscriptions                     | `databaseId`, `assetId`, `subscriptionId`                                                                | `itemCount`: subscribers                                                                                    | The event, entity and subscribers written                                                                                                                       |
| Search                            | --                                                                                                       | `itemCount`: hits returned                                                                                  | The search request (`requestParameters`), never the hits                                                                                                        |
| Workflow output and execution end | `databaseId`, `assetId`, `workflowId`, `executionId`                                                     | `fileCount`, `itemCount` (output write-back); `durationMs` (execution end)                                  | `actor.onBehalfOf` names the user the execution ran for; a trigger-launched run names none                                                                      |
| Execution reads                   | `executionId`; `databaseId` when the run's assets share one database, `assetId` when it ran on one asset | --                                                                                                          | The query asked (`requestParameters`), never what it returned                                                                                                   |
| Bucket sync                       | `databaseId`, `assetId`, `filePath`                                                                      | `bytes` from the storage event                                                                              | `details.operation` (`create`, `restore`, `created`); only objects written to the bucket directly, not through VAMS                                             |
| Add-on syncs (Physna, Garnet)     | `databaseId`, `assetId`, `filePath`                                                                      | One record per entity synced, never one per batch                                                           | `details.operation`; `outcome` `failed` when the entity was not sent. A queue message that names no entity is redriven and logged by the function, not recorded |

## How Audit Logging Works

### Silent Failure Pattern

All audit logging functions implement a silent failure design. If writing an audit event fails, the error is logged to the Lambda function's standard Amazon CloudWatch log stream, but Lambda execution continues without disruption. This ensures that audit logging failures never cause API requests to fail.

```python
# Simplified illustration of the silent failure pattern
def log_authorization(event, authorized, custom_data=None):
    try:
        # Extract user context from JWT claims
        # Format structured log message
        # Write to CloudWatch log group
        pass
    except Exception as e:
        # Log failure locally but do not re-raise
        print(f"Failed to write audit log: {e}")
```

### Infrastructure Integration

Audit log group names are resolved at runtime from AWS Systems Manager Parameter Store, the same mechanism that resolves Amazon DynamoDB table and Amazon S3 bucket names. CDK publishes each name as an SSM parameter under the deployment's resource-name prefix, and `globalLambdaEnvironmentsAndPermissions()` gives every Lambda function the `VAMS_RESOURCE_PARAM_PREFIX` environment variable plus read access to the parameters beneath it. The audit logging module calls `get_log_group_name(ResourceKeys.*)` from `backend/backend/common/resourceNames.py`, which fetches all resource names in one paginated `GetParametersByPath` request and caches them in the module for 60 minutes.

| Resource Key                      | SSM Parameter Key                               | Log Group                |
| --------------------------------- | ----------------------------------------------- | ------------------------ |
| `AUDIT_LOG_AUTHENTICATION`        | `cloudwatchLogGroups/auditAuthentication`       | Authentication events    |
| `AUDIT_LOG_AUTHORIZATION`         | `cloudwatchLogGroups/auditAuthorization`        | Authorization events     |
| `AUDIT_LOG_FILEUPLOAD`            | `cloudwatchLogGroups/auditFileUpload`           | File upload events       |
| `AUDIT_LOG_FILEDOWNLOAD`          | `cloudwatchLogGroups/auditFileDownload`         | File download events     |
| `AUDIT_LOG_FILEDOWNLOAD_STREAMED` | `cloudwatchLogGroups/auditFileDownloadStreamed` | Streamed download events |
| `AUDIT_LOG_AUTHOTHER`             | `cloudwatchLogGroups/auditAuthOther`            | Other auth events        |
| `AUDIT_LOG_AUTHCHANGES`           | `cloudwatchLogGroups/auditAuthChanges`          | Auth change events       |
| `AUDIT_LOG_ACTIONS`               | `cloudwatchLogGroups/auditActions`              | Action events            |
| `AUDIT_LOG_ERRORS`                | `cloudwatchLogGroups/auditErrors`               | Error events             |

The keys are defined in `ResourceKeys` in `backend/backend/common/resourceNames.py` and mirrored in `RESOURCE_PARAM_KEYS.cloudwatchLogGroups` in `infra/common/resourceParamKeys.ts`; the two lists must stay identical.

Each key also accepts an environment variable of the same name as an override. When the variable is set, its value is used directly and no SSM lookup occurs. This is how the test suite and local utilities point the audit functions at a log group of their choosing, and it serves as a break-glass path for a deployed function; the deployment itself sets no `AUDIT_LOG_*` variables.

The `setupSecurityAndLoggingEnvironmentAndPermissions()` CDK security helper grants each Lambda function `logs:CreateLogStream` and `logs:PutLogEvents` on all nine audit log groups, along with read access to the authorization tables.

## Available Logging Functions

The audit logging module is located at `backend/backend/customLogging/auditLogging.py`.

### log_authentication

Log authentication-related events such as token validation results.

```python
from customLogging.auditLogging import log_authentication

log_authentication(
    event=event,
    authenticated=True,
    custom_data={"method": "cognito", "ip": "192.168.1.1"}
)
```

### log_authorization and log_authorization_api

Log authorization decisions. Shared Casbin enforcement calls these on every API-level check and on data-level authorization failures (failures only, for performance), so a handler that enforces through `CasbinEnforcer` needs no call of its own. `log_authorization()` takes the claims dictionary returned by `request_to_claims()`; `log_authorization_api()` takes the full API Gateway event.

```python
from customLogging.auditLogging import log_authorization, log_authorization_api

log_authorization(
    claims_and_roles,
    authorized=False,
    custom_data={
        "action": "DELETE",
        "obj": {"object__type": "database", "databaseId": "db-123"}
    }
)

log_authorization_api(
    event,
    authorized=False,
    custom_data={
        "action": "DELETE",
        "obj": {"object__type": "api", "route__path": "/database/db-123"}
    }
)
```

### log_file_upload

Log file upload events including denied uploads.

```python
from customLogging.auditLogging import log_file_upload

log_file_upload(
    event=event,
    database_id="db-123",
    asset_id="asset-456",
    file_path="/data/model.obj",
    upload_denied=False,
    upload_denied_reason=None,
    custom_data={"fileSize": 1024000, "contentType": "model/obj"}
)
```

### log_file_download, log_file_download_bulk, and log_file_download_streamed

Log file download events, distinguishing between direct downloads and streaming transfers. `log_file_download_bulk()` records one entry per file for a multi-file download and writes them to the file download log group in a single Amazon CloudWatch call.

```python
from customLogging.auditLogging import (
    log_file_download, log_file_download_bulk, log_file_download_streamed
)

log_file_download(
    event=event,
    database_id="db-123",
    asset_id="asset-456",
    file_path="/data/model.obj",
    custom_data={"downloadMethod": "direct"}
)

log_file_download_bulk(
    event=event,
    database_id="db-123",
    asset_id="asset-456",
    file_entries=[{"filePath": "/data/model.obj"}, {"filePath": "/data/texture.png"}],
    custom_data_base={"downloadType": "archive"}
)
```

### log_auth_other, log_auth_changes, log_actions, log_errors

Log additional event types with a `secondary_type` field for categorization.

```python
from customLogging.auditLogging import log_auth_changes, log_actions, log_errors

log_auth_changes(
    event=event,
    secondary_type="role_assignment",
    custom_data={"targetUser": "jane.smith", "role": "editor", "action": "added"}
)

log_actions(
    event=event,
    secondary_type="database_created",
    custom_data={"databaseId": "db-789", "databaseName": "Production Assets"}
)

log_errors(
    event=event,
    secondary_type="validation_error",
    custom_data={"error": "Invalid asset ID format", "assetId": "invalid-id"}
)
```

### log_action and audit_request

Record an action in the [record contract](#action-records), after the write succeeded:

```python
from customLogging.auditLogging import audit_request, log_action

def lambda_handler(event, context):
    with audit_request(event, context):
        ...
        update_asset(asset, body)
        log_action(event, "assetUpdate", database_id=database_id, asset_id=asset_id,
                   changes={"description": body["description"]},
                   previous={"description": asset.get("description")})
```

| Parameter                                              | Description                                                                                        |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| `event`                                                | The Lambda event of the request                                                                    |
| `action`                                               | A registered action name; `None` takes the action registered for the request's route               |
| `outcome`                                              | `success` (default), `failed` or `denied`                                                          |
| `reason`                                               | A generic category on `failed` or `denied`                                                         |
| `database_id`, `asset_id`, `file_path`, `**ids`        | The IDs of the data the action touched (`versionId=`, `workflowId=` and the other payload ID keys) |
| `bytes`, `file_count`, `item_count`, `duration_ms`     | Sizes and counts the handler already knows                                                         |
| `changes`, `previous`, `request_parameters`, `details` | The value sections                                                                                 |
| `on_behalf_of`                                         | The user a system action ran for (an execution's initiator), recorded as `actor.onBehalfOf`        |

`audit_request(event, context, on_behalf_of=None)` is a context manager; `@audited_handler` applies it to an entrypoint and records the [outcome of a failed request](#request-outcomes). `log_action` must receive the same event object the entrypoint received, and a queue, storage or state machine handler passes the invocation's event, never one record of it. `log_actions(event, type, data, outcome="failed")` records an earlier-style record as failed, written at once. Outside it, `log_action` writes each record at once and every other function writes exactly as before. Inside it, the `log_actions`, `log_file_*` and `log_errors` records keep their payload keys and gain the contract keys (see [Payload Keys](#payload-keys) for a legacy key that clashes); the authorization and authentication functions are unchanged.

## Handler Integration Example

The standard pattern: the entrypoint is decorated, the API tier is checked, and the action is recorded after the write succeeded. The authorization record is written by `CasbinEnforcer`, and the outcome of an error response by the decorator.

```python
from customLogging.auditLogging import audited_handler, log_action

@audited_handler
def lambda_handler(event, context):
    claims_and_roles = request_to_claims(event)
    if not CasbinEnforcer(claims_and_roles).enforceAPI(event):
        return authorization_error()
    try:
        request_model = parse(json.loads(event["body"]), model=UpdateWidgetRequestModel)
        previous = update_widget(request_model)
        log_action(event, "widgetUpdate", database_id=request_model.databaseId,
                   changes=request_model.dict(exclude_none=True), previous=previous)
        return success(body={"message": "Widget updated"})
    except ValidationError as v:
        return validation_error(body={"message": validation_error_message(v)}, event=event)
```

## Configuring Log Retention

Log retention is controlled in **one** place. The `LogRetentionAspect` CDK aspect visits every
`AWS::Logs::LogGroup` in the stack and sets its retention period, so a single value applies to the audit
log groups and to every other log group alike. The default is **1 year**.

To change it, edit the `new LogRetentionAspect(...)` call in `infra/lib/core-stack.ts`:

```typescript
Aspects.of(this).add(new LogRetentionAspect(logs.RetentionDays.ONE_YEAR));
```

:::warning[The aspect wins over per-construct settings]
A `retention` property set on an individual log group has **no effect** -- the aspect runs afterwards and
overwrites it. Changing retention by editing a construct produces a template that still carries the
aspect's value, which looks like the edit was ignored. Change it at the aspect.
:::

### Retaining audit records for longer

A retention period beyond a year is a common requirement in regulated environments, including
public-sector, defence, and other industries whose audit-record obligations exceed one year. Pass a longer
`logs.RetentionDays` value -- for example `TEN_YEARS` -- at the call site above.

Two considerations before doing so:

-   The value applies to **every** log group, including high-volume AWS Lambda execution logs, not only
    the nine audit groups. Amazon CloudWatch Logs storage is billed per GB-month across the whole
    retention window, so the cost scales with total ingestion volume.
-   If only the audit groups need the longer period, narrow the aspect by log-group name rather than
    raising it globally. The audit groups are created in `storageBuilder-nestedStack.ts` and exposed as
    `storageResources.cloudWatchAuditLogGroups`.

Archiving to Amazon S3 remains the lower-cost option for very long retention horizons.

## Querying Logs with Amazon CloudWatch Logs Insights

Amazon CloudWatch Logs Insights provides a powerful query language for analyzing audit logs.

### Cost Attribution

Action records carry the user, the API key, the database, the asset and the sizes the handler knew, so the actions log group answers who used what. Run these against the actions log group (and, for the byte counts of downloads and uploads, the file log groups). Use `parse` to read the payload from the record:

**Actions by user**

```sql
fields @message
| parse @message '"action": "*"' as action
| parse @message '"userId": "*"' as user
| stats count(*) as actions by user, action
| sort actions desc
```

**Actions by database and by asset**

```sql
fields @message
| parse @message '"databaseId": "*"' as databaseId
| parse @message '"assetId": "*"' as assetId
| filter ispresent(databaseId)
| stats count(*) as actions by databaseId, assetId
| sort actions desc
```

**Actions by API key**

```sql
fields @message
| parse @message '"apiKeyId": "*"' as apiKeyId
| filter ispresent(apiKeyId)
| parse @message '"action": "*"' as action
| stats count(*) as actions by apiKeyId, action
```

**Bytes written directly to each database's bucket per day** (bucket sync records)

```sql
fields @timestamp, @message
| filter @message like /"action": "fileSync"/
| parse @message '"databaseId": "*"' as databaseId
| parse @message '"bytes": *,' as bytes
| filter ispresent(bytes)
| stats sum(bytes) as totalBytes by databaseId, bin(1d)
```

Each byte is counted once: bucket sync records only objects written to the bucket directly (an object VAMS wrote carries its change source and is recorded by the upload, copy, move, revert or pipeline action that wrote it), and an ingest records the sizes it was told as `details.declaredBytes`, not as `bytes`, since an upload that never completes stores nothing. Uploads through VAMS record the files, not their stored size; for the stored size of a database use Amazon S3 Storage Lens or the bucket's metrics. Deletions made directly in the bucket write no action record. They appear in AWS CloudTrail data events only when the stack's trail is on (`app.addStackCloudTrailLogs`, which logs all Amazon S3 data events; see [AWS CloudTrail Integration](../architecture/security.md#aws-cloudtrail-integration)) or a trail of your own logs S3 data events for the bucket. CloudTrail, not the audit log, is the authoritative record of direct bucket access: bucket sync decides whether an object was written by VAMS from the change source stored on the object, which a principal with direct write access to the bucket can set.

**Workflow executions and their duration, by the user they ran for**

```sql
fields @message
| filter @message like /"action": "workflowExecutionEnd"/
| parse @message '"onBehalfOf": "*"' as initiator
| parse @message '"outcome": "*"' as outcome
| parse @message '"durationMs": *,' as durationMs
| stats count(*) as executions, sum(durationMs) as totalMs by initiator, outcome
```

**Failed requests by action and reason**

```sql
fields @message
| filter @message like /"outcome": "failed"/
| parse @message '"action": "*"' as action
| parse @message '"reason": "*"' as reason
| stats count(*) by action, reason
```

A value is written with a following comma unless it is the last key of its object, so a `parse` pattern ending in `,` misses a value written last; check a sample record when a query returns fewer rows than expected. A record cut to the [size caps](#redaction-and-size-caps) keeps its IDs and counts.

### Find All Failed Authorization Attempts

```text
fields @timestamp, @message
| filter @message like /\[AUTHORIZATION\]\[authorized: False\]/
| sort @timestamp desc
| limit 100
```

### Find Failed Authorization Attempts, Excluding Permission Probes

```text
fields @timestamp, @message
| filter @message like /\[AUTHORIZATION\]\[authorized: False\]/
| filter @message not like /^\[AUTHORIZATION\]\[authorized: False\] \[probe: /
| sort @timestamp desc
| limit 100
```

### Track File Uploads by User

```text
fields @timestamp, @message
| filter @message like /\[FILEUPLOAD\]/
| parse @message /\[user: (?<user>[^\]]+)\]/
| stats count() by user
```

### Group Authorizer Denials by Reason

```text
fields @timestamp, @message
| filter @message like /\[AUTHORIZATION\]\[authorized: False\]/
| parse @message /"failureReason": "(?<reason>[^"]+)"/
| stats count() by reason
| sort count() desc
```

### Find Errors by Type

```text
fields @timestamp, @message
| filter @message like /\[ERRORS\]/
| parse @message /\[type: (?<errorType>[^\]]+)\]/
| stats count() by errorType
| sort count() desc
```

### Track Auth Changes (Role Assignments)

```text
fields @timestamp, @message
| filter @message like /\[AUTHCHANGES\]/
| parse @message /\[type: (?<changeType>[^\]]+)\]/
| sort @timestamp desc
| limit 50
```

## Data Protection

### What Is Never Logged

The values of the redacted keys listed under [Automatic Data Masking](#automatic-data-masking) never reach an audit record. That covers:

-   JWT tokens and bearer tokens in the `Authorization` header, and the raw token in `idJwtToken` fields
-   API keys (`X-Api-Key` header, `apiKey`, `apiKeySecret` and `rawKey` fields) and cookies (`Cookie`, `Set-Cookie`)
-   Passwords, secrets, client secrets, access and refresh tokens and private keys in fields with those names
-   Proxy credentials (`Proxy-Authorization`) and AWS credentials (access keys, secret keys, session tokens, `X-Amz-Security-Token`)
-   Token signatures or detailed token validation errors (the authorizer records a generic failure category)
-   Pipeline template bodies -- stored or supplied inline as an execute-time override -- their form definitions and input instructions, and the tag values substituted into them

Redaction is by key name. A secret typed into a free-form field that is not on the list, such as a description, a metadata value or a file name, is written as submitted.

### What Is Logged

Each record holds the bracketed prefix, a payload, and an echo of the triggering request:

-   **Prefix** -- the event type, the user ID (from verified token claims), the user's roles and MFA status, and for the authorization log the allow or deny result.
-   **Payload** -- what the calling handler records for the event: resource identifiers (database, asset, file path, pipeline, workflow and execution IDs), the action and the object checked, counts and flags, and for the authorizer the source IP address and a generic failure reason. An [action record](#action-records) also carries what a write wrote or changed, in full and after the redaction below: names, descriptions, comment text, metadata values, tag names and subscriber IDs a user submits reach the actions log group in `changes` and `previous`. A read records what was asked, never what it returned.
-   **Request echo** (`--- [event: ...]`) -- the API Gateway event the handler received, after redaction: the method, path, path and query string parameters, the request headers other than the redacted ones (for example `User-Agent`, `Host`, `X-Forwarded-For`), the request context (source IP address, user agent, request ID and the claims the authorizer verified, which can include the user's email address and any other claim the identity provider puts in the token), and the request `body` with only the redacted keys removed. Data-level authorization records echo only the user ID.

Because the body is echoed, whatever the caller submitted reaches the audit log: asset and database names and descriptions, metadata keys and values, tag names, comments, file names and paths, and any personal data a user types into them.

Records are one line each, with every value in a bracketed field encoded as described in [Log Format](#log-format).

### Automatic Data Masking

Every audit entry carries an echo of the triggering API event, and the `mask_sensitive_data()` function filters that echo before it is written to Amazon CloudWatch. Matching is case-insensitive on the key name and applies at every nesting level, walking both objects and arrays. Two key families are replaced with `<redacted>`, and the distinction between them is why the second family exists at all: a credential key holds a value that is never safe to log, while a content key holds a document the caller authored, whose free-form text can contain a prompt, a file path, or a credential-shaped string that no key-name rule could find once it is inside the body.

**Credential keys** -- the value is never safe to log

-   `authorization` headers
-   `idJwtToken` fields
-   `Credentials`, `AccessKeyId`, `SecretAccessKey`, `SessionToken` objects
-   `apiKey`, `apiKeySecret`, `rawKey` fields (the API key identifier fields `apiKeyId`, `apiKeyName` and `apiKeyHash` stay readable, so a record still names the key a call used)
-   The credential-carrying request and response headers `Cookie`, `Set-Cookie`, `X-Api-Key`, `Proxy-Authorization` and `X-Amz-Security-Token`, and the origin verification header `x-vams-origin-verify` that Amazon CloudFront adds to the API requests it forwards. A header is redacted in both `headers` and `multiValueHeaders`, whatever its letter case.
-   The credential field names `password`, `secret`, `clientSecret`, `client_secret`, `accessToken`, `access_token`, `refreshToken`, `refresh_token`, `privateKey`, `private_key`. The whole name is matched, in any letter case, so a field that only contains the word, such as `passwordPolicy` or `secretArn`, stays readable.
-   The keys an add-on or a fork registers (`ADDON_LOG_SENSITIVE_KEYS`, `CUSTOM_LOG_SENSITIVE_KEYS`)

**Content keys** -- caller-authored payloads. Every field that can deliver a template body belongs here, whichever request carries it:

-   `configBody` -- a pipeline template body, which carries free-form content such as generative-AI prompts and model configuration
-   `customTemplateOverride` -- the same kind of body supplied inline on an execute request instead of stored as a template
-   `webFormJson` -- the form definition stored alongside a template body
-   `inputInstructions` -- the authored guidance stored alongside a template body
-   `templateTags` and `tagValues` -- the caller-supplied values substituted into a template body

The field name is preserved when the value is redacted, so the audit trail still records that a template body, form definition, or tag value was submitted with the request. A request `body` is filtered whether it arrives as a JSON object or as a JSON string: the string is parsed, filtered, and re-serialized, and a string payload that names a redacted key but does not parse as JSON is dropped in full.

The two families are declared as `SENSITIVE_KEYS` (`CORE_LOG_SENSITIVE_KEYS` in `backend/backend/common/constants.py`, merged with the add-on and fork lists) and `CONTENT_KEYS` in `backend/backend/customLogging/logger.py`. A new request field that carries a caller-authored body is protected only when its key is added to `CONTENT_KEYS`, and a new header or field that carries a credential only when its name is added to the credential keys.

## Integration with SIEM Systems

VAMS audit logs can be forwarded to Security Information and Event Management (SIEM) systems using standard AWS patterns:

1. **Amazon CloudWatch Logs Subscription Filters** -- Stream log events to Amazon Kinesis Data Firehose, AWS Lambda, or Amazon OpenSearch Service
2. **Amazon S3 Export** -- Export log data to Amazon S3 for ingestion by external SIEM tools
3. **Amazon EventBridge** -- Create rules to forward specific log patterns to third-party integrations

### Key Considerations

-   Logs are stored in the same AWS Region as the VAMS deployment
-   AWS KMS keys are Region-specific
-   No cross-Region log replication is configured by default
-   An audit entry cannot be edited once written. Log groups and streams can be deleted by a principal that holds those permissions; no VAMS application role does
-   Log stream names use date-based format (YYYY/MM/DD)

## Monitoring Audit Logging Health

An entry that cannot be written to its audit log group is not dropped. The AWS Lambda function that produced it writes the entry, exactly as it would have been written (masked event echo included, capped at the same 255 KB as an audit event), to its own log group (`/aws/lambda/<function name>`) as one JSON line:

```json
{
    "auditFallback": true,
    "logGroupKey": "auditActions",
    "reason": "putLogEventsFailed",
    "record": "[ACTIONS][type: assetCreate] [user: john.doe] ..."
}
```

`logGroupKey` names the audit log group the entry was meant for (`auditAuthentication`, `auditAuthorization`, `auditFileUpload`, `auditFileDownload`, `auditFileDownloadStreamed`, `auditAuthOther`, `auditAuthChanges`, `auditActions`, `auditErrors`). `reason` is one of:

| Reason                  | What happened                                                                                                                     |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `putLogEventsFailed`    | Amazon CloudWatch Logs refused or did not answer the write (throttling, a missing log group or stream, an IAM or AWS KMS denial). |
| `createLogStreamFailed` | The day's log stream could not be created.                                                                                        |
| `clientUnavailable`     | The Amazon CloudWatch Logs client could not be created when the function started.                                                 |
| `logGroupUnresolved`    | The audit log group name could not be read from AWS Systems Manager Parameter Store.                                              |
| `flushDeadlineExceeded` | The request's held records were not confirmed within the 1.5-second deadline. The write may still complete.                       |
| `formatFailed`          | The entry could not be formatted; a `[ERROR: Failed to format message]` entry was written in its place.                           |
| `unexpectedError`       | Any other failure. `record` is `null` when the entry was never formatted.                                                         |

The same write also emits an Amazon CloudWatch [embedded metric format](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format.html) line, which Amazon CloudWatch turns into the metric `AuditWriteFailures` in the namespace `VAMS/Audit`, with the dimension `AuditLogType` set to the `logGroupKey` value and the number of entries lost as its value. The metric is extracted from the function's own log, so it needs no permission beyond the function's log delivery. A healthy deployment emits no data points.

Alarm on the metric for each audit log type you depend on, for example the authorization log:

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name vams-audit-authorization-write-failures \
  --namespace VAMS/Audit --metric-name AuditWriteFailures \
  --dimensions Name=AuditLogType,Value=auditAuthorization \
  --statistic Sum --period 300 --evaluation-periods 1 \
  --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching \
  --alarm-actions <your SNS topic ARN>
```

To alarm on every audit log type at once, base the alarm on the Metrics Insights query `SELECT SUM(AuditWriteFailures) FROM SCHEMA("VAMS/Audit", AuditLogType)`. To recover the entries themselves, query the Lambda function log groups with Amazon CloudWatch Logs Insights:

```text
fields @timestamp, logGroupKey, reason, record
| filter @message like /"auditFallback": true/
| sort @timestamp desc
```

Entries recovered this way live in the function log group, under its retention and encryption settings rather than the audit log group's; give the function log groups the same read restrictions as the audit log groups.

The functions also log these error lines when an audit write fails:

```text
Failed to write audit log to CloudWatch log group
Failed to log [event_type] audit event
AUDIT_LOG_[TYPE] resource name not resolved
Failed loading resource name parameters from SSM
CloudWatch Logs client not initialized
```

:::tip[Set Up Alarms]
Alarm on `AuditWriteFailures`. While the silent failure pattern prevents API disruption, you should know when an entry reached only the function log.
:::

## Security Best Practices

1. **Enable AWS KMS encryption** for audit log groups in production deployments
2. **Restrict read access** to audit logs using IAM policies
3. **Review logs regularly** for suspicious patterns (repeated authorization failures, unusual file downloads)
4. **Set up Amazon CloudWatch alarms** for critical events
5. **Archive to Amazon S3** for retention beyond the configured period
6. **Monitor audit logging health** to detect failures in the logging system itself

## When to Use Each Function

The following table provides guidance on which logging function to call in different operational contexts:

| Function                       | When to Use                                                                                                                                                                                                                                                                           |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `log_authentication()`         | Authentication results a VAMS handler determines itself -- token validation outcomes and session creation. The identity provider records sign-in itself, so most deployments emit no entries here.                                                                                    |
| `log_authorization()`          | Permission checks using claims and roles directly. Called inside Casbin enforcement logic where the `claims_and_roles` dictionary is available (not the full API Gateway event). Only log on data-level authorization failures for performance.                                       |
| `log_authorization_api()`      | API-level permission checks using the full API Gateway event. Called in handler entry points where `enforceAPI()` is evaluated.                                                                                                                                                       |
| `log_authorization_gateway()`  | Authorization events from the API Gateway custom Lambda authorizer. Uses enhanced security: never logs JWT tokens, uses generic failure categories only, and extracts user IDs only after successful JWT verification.                                                                |
| `log_file_upload()`            | File uploads to Amazon S3, multipart uploads, and upload validation results (including denied uploads with reasons).                                                                                                                                                                  |
| `log_file_download()`          | Direct file downloads and presigned URL generation.                                                                                                                                                                                                                                   |
| `log_file_download_bulk()`     | Multi-file downloads, where one entry per file is written to the file download log group in a single Amazon CloudWatch call.                                                                                                                                                          |
| `log_file_download_streamed()` | Streaming downloads and large file transfers via chunked protocols.                                                                                                                                                                                                                   |
| `log_auth_other()`             | Authentication-adjacent reads and writes that do not change the authorization model, such as a user profile fetch or update.                                                                                                                                                          |
| `log_auth_changes()`           | Role and user-role changes, permission constraint and constraint-template changes, API key lifecycle events, Amazon Cognito user management, and any other change to the authorization model.                                                                                         |
| `log_actions()`                | Orchestration write operations: launching, aborting, re-running, and permanently deleting an execution, and creating, updating, archiving, or deleting a workflow, pipeline, pipeline template, or workflow trigger. See [Orchestration action events](#orchestration-action-events). |
| `log_errors()`                 | Application errors, input validation failures, system exceptions, and any unhandled error conditions.                                                                                                                                                                                 |
| `log_action()`                 | Any action in the audit action registry, in the record contract: after a write succeeds, when an authorized attempt fails, or when a data-level check denies it. See [Action Records](#action-records).                                                                               |
| `audit_request()`              | Around the work of every Lambda entrypoint, so a request's identity is resolved once and its action records are written together.                                                                                                                                                     |

:::note[Function signature differences]
`log_authorization()` accepts `claims_and_roles` as its first parameter (the claims dictionary from `request_to_claims()`), not the full API Gateway event. Use `log_authorization_api()` when you have the full event object, and `log_authorization_gateway()` in the API Gateway authorizer Lambda.
:::

### Orchestration action events

Every write on the pipeline, workflow, and execution surface records an `ACTIONS` entry. The
`secondary_type` names the operation, and the payload carries identifiers, counts, and flags:

| `secondary_type`                                                               | Recorded when                                              | Payload highlights                                                                      |
| ------------------------------------------------------------------------------ | ---------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `workflowExecute`                                                              | An execution is launched and its state machine has started | Workflow and execution ids, trigger type, input-file count, output target               |
| `workflowExecutionAbort`                                                       | A single execution is aborted                              | Execution and workflow ids                                                              |
| `workflowExecutionGroupAbort`                                                  | An execution group is aborted                              | Group id, how many this pass aborted, how many were withheld for access, more-remaining |
| `workflowExecutionRerun`                                                       | An execution is re-run                                     | The replayed execution id and the new execution id                                      |
| `workflowExecutionPermanentDelete`                                             | An execution's records are permanently deleted             | Execution and workflow ids                                                              |
| `workflowCreate` / `workflowUpdate` / `workflowArchive`                        | A workflow is created, updated, or archived                | Database and workflow ids, pipeline count                                               |
| `pipelineCreate` / `pipelineUpdate` / `pipelineArchive`                        | A pipeline is created, updated, or archived                | Database and pipeline ids, execution type, whether the execution config changed         |
| `pipelineTemplateCreate` / `pipelineTemplateUpdate` / `pipelineTemplateDelete` | A template is created, updated, or deleted                 | Database, pipeline, and template ids, configuration format, default flag                |
| `workflowTriggerSet` / `workflowTriggerDelete`                                 | A trigger is set or removed                                | Database and workflow ids, trigger type, enabled flag                                   |

Three properties hold across all of them:

-   **The entry follows the write.** It is emitted only after the operation succeeded, so a write that
    failed — or a request rejected as not found — never appears as a completed action.
-   **Configuration bodies and tag values are never recorded.** A rendered configuration body or a tag
    value can carry a model prompt or a credential-shaped string, so the entry records the template id,
    the format, and counts instead.
-   **A trigger-launched execution is attributed to `SYSTEM_USER`.** A user may upload a file without
    holding permission to run the workflow the upload fires, so the execution runs as the system
    identity by design; the `triggerType` field records that the run was automatic rather than manual.

### Compliance action events

Every compliance API call writes an action record ([Action Records](#action-records)) in addition to the entry the compliance services write to their own [compliance audit trail](../user-guide/compliance.md#audit-log), which the compliance audit routes read. A call answered with `403` is written to the authorization log group as `denied`; any other refused or failed call is written with `failed` and a generic reason (`validation`, `notFound`, `conflict`, `internal`). A request that works across databases (approving or starting a cascade, a sweep, the audit trail across databases, the schema listing's create scopes) records a denial for each database the caller may not act on, as the core listings do.

| Action                                                                                                                                                                                                                                                      | Kind  | Recorded when                                                             | Record highlights                                                                                                                                                                                             |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `complianceSchemaCreate` / `complianceSchemaUpdate` / `complianceSchemaDelete`                                                                                                                                                                              | write | A schema is registered, versioned or deleted                              | `schemaName`, the scope and description set, the version written (`internalVersion`), whether the rule document changed (never the document)                                                                  |
| `complianceBindingSet` / `complianceBindingDelete`                                                                                                                                                                                                          | write | A schema is bound to, or unbound from, a database or one asset            | Database and asset ids, the schema name and automatic-evaluation setting; for a database, the background apply it started (`details.applyId`)                                                                 |
| `complianceEvaluate` / `complianceDatabaseEvaluate` / `complianceSchemaSweep`                                                                                                                                                                               | write | An asset, a database's tracked assets, or a schema's assets are evaluated | Database, asset or schema ids; for one asset, the `evaluationId` of the evaluation; for a database or a schema, the evaluated-asset count of the page (never the evaluation ids) and whether it resumed a run |
| `complianceQuarantineRelease` / `complianceExceptionGrant` / `complianceExceptionRevoke`                                                                                                                                                                    | write | A quarantine is released, or an exception granted or revoked              | Database and asset ids, the resulting state and the reason given                                                                                                                                              |
| `complianceCascadeCreate` / `complianceCascadeApprove` / `complianceCascadeReject`                                                                                                                                                                          | write | A cascade is created, approved or rejected                                | The `cascadeId` (a create names the cascade it created, so it joins the approval or rejection), the trigger database and asset ids; approval requirement; the reason given                                    |
| `complianceSchemaList` / `complianceSchemaGet` / `complianceBindingGet` / `complianceStateGet` / `complianceStateList` / `complianceEvaluationList` / `complianceQuarantineList` / `complianceCascadeList` / `complianceCascadeGet` / `complianceAuditList` | read  | A compliance read succeeds                                                | The ids, filters and page size asked for (a continuation token only as `startingTokenSent`), and the number of items returned                                                                                 |

The reads are subject to the [reads switch](#reads-switch): with `app.auditLogging.recordReads` set to `false`, a successful compliance read writes no action record, and a denied or failed one still does. Each read writes one record per request, whatever the number of items it returns. Evaluations that the compliance trigger, the workflow callback and the cascade executor run in the background serve no API route; they are recorded in the compliance audit trail only.

## Error Handling Details

### Silent Failure Design

All audit logging functions implement a consistent silent failure pattern with the following behavior:

1. **Try-except wrapper** -- Each logging function is wrapped in a top-level `try-except` block that catches all exceptions.
2. **Local logging** -- When audit logging fails, the error is logged to the Lambda function's standard Amazon CloudWatch log stream using the `safeLogger` module.
3. **No re-raise** -- Exceptions are caught but never re-raised, ensuring the calling handler continues execution.
4. **The entry is kept** -- An entry that did not reach its audit log group is written to the function's own log as an `auditFallback` line and counted in the `AuditWriteFailures` metric (see [Monitoring Audit Logging Health](#monitoring-audit-logging-health)). Nothing is added to a successful write.

### Error Scenarios Handled

The following failure scenarios are handled silently without disrupting Lambda execution:

-   Amazon CloudWatch Logs client initialization failure (for example, missing IAM permissions at cold start)
-   Log group name resolution failure -- an unreachable AWS Systems Manager Parameter Store, a missing `cloudwatchLogGroups/*` parameter, or an unset `VAMS_RESOURCE_PARAM_PREFIX` (the function logs the failure and writes the entry to its own log instead)
-   Log group does not exist in Amazon CloudWatch
-   Log stream creation failure
-   Network timeouts when writing to Amazon CloudWatch
-   IAM permission errors on `logs:PutLogEvents`
-   Invalid or non-serializable data formats in the `custom_data` parameter (a value JSON cannot encode is written as its string form)

### Monitoring for Failures

While silent failure prevents API disruption, you should monitor for audit logging issues. Alarm on the `AuditWriteFailures` metric in the `VAMS/Audit` namespace and recover lost entries from the function logs, as [Monitoring Audit Logging Health](#monitoring-audit-logging-health) describes. The error lines below accompany each failure:

```text
Failed to write audit log to CloudWatch log group
Failed to log [event_type] audit event
AUDIT_LOG_[TYPE] resource name not resolved
Failed loading resource name parameters from SSM
CloudWatch Logs client not initialized
```

## API Gateway Authorizer Security

The API Gateway custom Lambda authorizer (`apiGatewayAuthorizerRest.py`) uses a dedicated logging function, `log_authorization_gateway()`, that implements additional security measures beyond the standard audit logging functions.

### Security Controls

1. **No token logging** -- JWT tokens are never logged, even in failure cases. The authorizer processes tokens in memory but strips them from all audit output.
2. **Generic failure categories** -- Failure reasons use safe, non-descriptive categories that do not expose token structure or validation details:
    - "IP address not authorized"
    - "Token missing or invalid format"
    - "Token verification failed"
    - "API key is disabled", "API key has expired", and the other API key denial categories
3. **User context after verification only** -- User IDs are only extracted and logged after successful JWT verification. For failed authorization attempts, the user field is set to `"unknown"`.
4. **Source IP and the masked request** -- The payload carries the requesting IP address and the generic failure reason. The record's request echo is the authorizer event after redaction: the method, path and query string, and the request headers other than the redacted ones (see [Automatic Data Masking](#automatic-data-masking)).

### Why a Separate Function

The API Gateway authorizer runs before normal request processing and handles raw JWT tokens. A separate `log_authorization_gateway()` function ensures that:

-   The full API Gateway event (which contains the `Authorization` header) is passed to the standard `mask_sensitive_data()` filter before any Amazon CloudWatch write.
-   Only the `context` field (populated after successful JWT verification) is used for user identity extraction.
-   MFA status is read from verified claims only, never from unverified token contents.

## Compliance Considerations

### Data Residency

-   Audit logs are stored in the same AWS Region as your VAMS deployment.
-   AWS KMS keys used for log encryption are Region-specific.
-   No cross-Region log replication is configured by default.

### Audit Trail Integrity

-   An audit entry cannot be edited once written. VAMS application roles may only create log streams and write entries (`logs:CreateLogStream`, `logs:PutLogEvents`); none may delete a log group or stream or change its retention. A principal outside VAMS that holds those permissions can, so restrict them, and record them with AWS CloudTrail.
-   Records are kept for the retention period. A request to erase personal data is met by that retention, not by deleting entries.
-   Log stream names use a date-based format (`YYYY/MM/DD`) for chronological organization.
-   Each log entry includes a millisecond-precision timestamp for chronological ordering.
-   The silent failure design ensures no API disruption due to logging errors; an entry that cannot be written to its audit log group is kept in the function log and counted in the `AuditWriteFailures` metric, which should be alarmed on.

### Privacy and Data Protection

-   User IDs are logged for audit purposes (legitimate interest for security monitoring).
-   Records can contain personal data beyond the user ID: the request echo carries the claims the authorizer verified (which can include an email address), the source IP address and user agent, and the submitted request body after redaction, so names, descriptions, metadata values, comments and file names a user enters are logged as entered (see [What Is Logged](#what-is-logged)). Treat the audit log groups, and the function log groups that hold [undelivered entries](#monitoring-audit-logging-health), as containing personal data when you set their access, retention and export.
-   Action records carry what a write changed, in full up to the size caps, so a comment, a metadata value or a user's email address that a write set is recorded as written.
-   Logs can be exported for data subject access requests.
-   The retention period is configurable; regulated environments that require multi-year audit records can extend it (see [Configuring Log Retention](#configuring-log-retention)).
-   Automatic data masking removes the values of the credential and content keys listed under [Automatic Data Masking](#automatic-data-masking) before writing; it does not inspect the text of other fields.

## Maintenance and Troubleshooting

### Log Group Management

| Aspect         | Details                                                                                                                    |
| -------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Retention**  | Applied by the `LogRetentionAspect` CDK aspect; 1 year by default.                                                         |
| **Encryption** | Managed by the VAMS AWS KMS key when `useKmsCmkEncryption.enabled` is `true`. Key rotation is automatic.                   |
| **Cleanup**    | Log groups are destroyed with stack deletion (`RemovalPolicy.DESTROY`).                                                    |
| **Naming**     | Uses a unique 10-character hash derived from the stack name and account ID to prevent naming conflicts across deployments. |

### Cost Optimization

-   Use Amazon CloudWatch Logs Insights for ad-hoc querying instead of exporting all logs.
-   Consider archiving to Amazon S3 for long-term storage beyond the configured period at lower cost.
-   Monitor ingestion rates and adjust retention at the aspect if the default period does not match your compliance requirements.
-   For high-volume deployments, set up Amazon CloudWatch Logs subscription filters to stream only critical events to downstream systems.

### Logs Not Appearing

If audit events are not appearing in the expected log groups:

1. Verify the Lambda function has `VAMS_RESOURCE_PARAM_PREFIX` set by checking the function configuration in the AWS Management Console.
2. Confirm the nine `cloudwatchLogGroups/*` parameters exist beneath that prefix in AWS Systems Manager Parameter Store and that the execution role can read them (`ssm:GetParametersByPath`).
3. Confirm IAM permissions for `logs:CreateLogStream` and `logs:PutLogEvents` are present on the Lambda execution role.
4. Check the Lambda function's standard Amazon CloudWatch log stream for audit logging error messages and `auditFallback` lines; the `reason` of a fallback line says why the entry was not written.
5. Ensure the audit log group exists in the correct AWS Region.

### Performance Impact

-   Outside a request audit context each entry is one synchronous write (tens of milliseconds). Inside one, a request's successful action records are written together at the end, one parallel write per log group, so the time is that of one write however many groups the request touched.
-   The silent failure pattern prevents any cascading impact on API response times.
-   Reporting an entry that could not be written adds two lines to the function log and nothing to a successful write.
-   Amazon CloudWatch Logs uses batched writes internally, optimizing throughput for high-volume scenarios.

## Next Steps

-   [Backend Development](backend.md) -- Handler patterns that integrate with audit logging
-   [CDK Infrastructure](cdk.md) -- How audit log groups are provisioned and secured
-   [Local Development Setup](setup.md) -- Testing audit logging locally
