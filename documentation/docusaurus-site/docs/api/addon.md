# Add-on API Reference

This page documents every VAMS REST API surface contributed by an optional [add-on](../developer/addons-overview.md). Each add-on's endpoints live under `/addon/{addonName}/...` and are only deployed when the corresponding add-on is enabled in `infra/config/config.json`.

Add-on endpoints enforce the same VAMS two-tier Casbin authorization as the core API: API-tier (can the role reach the route) and object-tier (can the role access the specific data entity). Both tiers must allow for a call to succeed.

---

## Physna add-on

The Physna add-on surfaces endpoints that gate Physna-hosted functionality behind VAMS authorization. All routes below are only deployed when `app.addons.usePhysnaSync.enabled` is `true`.

See the [Physna Integration](../developer/physna-integration.md) developer guide for broader context on the add-on, its sync behavior, and its configuration.

### GET `/addon/physna/viewer`

Returns a JSON envelope describing whether the Physna viewer can currently be rendered for a given asset file, and — when it can — the values the frontend needs to embed Physna's hosted viewer in an `<iframe>` directly.

:::warning[The viewer token is not scoped to an asset]
The `viewerToken` field in the `ready` response carries no asset scope. `POST /viewer/token` accepts no asset parameter, so Physna issues the token at tenant scope, and every synced database and asset lives under the single tenant named in `tenantId`. Physna sets the token's lifetime; VAMS reads no expiry and mints a fresh token on each `ready` response.

The token is returned to the browser so the iframe can load Physna's hosted viewer directly, which avoids the Lambda response-size limits and URL-rewriting complexity of proxying the full viewer payload. VAMS's object-tier authorization runs on every call to this endpoint — a user who cannot see the asset cannot obtain a viewer token for it, but the token they receive is not restricted to it. See [Physna Viewer](../developer/physna-integration.md#physna-viewer) for the limitation and its side effects.
:::

#### Query parameters

| Parameter      | Type   | Required | Description                                                     |
| -------------- | ------ | -------- | --------------------------------------------------------------- |
| `databaseId`   | string | Yes      | VAMS database ID the asset belongs to.                          |
| `assetId`      | string | Yes      | VAMS asset ID.                                                  |
| `relativePath` | string | Yes      | Relative path of the file within the asset, beginning with `/`. |

#### Responses

All responses have `Content-Type: application/json`. The body always includes `status` and `message`; additional fields are populated for specific statuses.

| `status`               | HTTP  | Additional fields                                           | Meaning                                                                   |
| ---------------------- | ----- | ----------------------------------------------------------- | ------------------------------------------------------------------------- |
| `ready`                | `200` | `physnaAssetId`, `tenantId`, `viewerToken`, `physnaApiBase` | Asset is ready to render. Use the fields to build the Physna iframe URL.  |
| `indexing`             | `200` | `physnaState`                                               | Physna is still indexing the asset. Poll again shortly.                   |
| `not_synced`           | `200` | —                                                           | File has not appeared in Physna yet. Poll again shortly.                  |
| `failed`               | `200` | `physnaState`                                               | Physna reported a permanent failure state; the viewer cannot render.      |
| `unsupported`          | `400` | —                                                           | File extension is not in the Physna-supported list.                       |
| `not_found`            | `404` | —                                                           | Asset does not exist in VAMS.                                             |
| `forbidden`            | `403` | —                                                           | Caller lacks API- or object-level access.                                 |
| `invalid_request`      | `400` | —                                                           | Query parameters failed validation.                                       |
| `request_failed`       | `400` | —                                                           | The request could not be completed against Physna.                        |
| `method_not_allowed`   | `405` | —                                                           | Non-`GET` request.                                                        |
| `upstream_unavailable` | `502` | —                                                           | Physna API could not be reached, or the viewer token could not be minted. |
| `internal_error`       | `500` | —                                                           | Unexpected server error.                                                  |

#### Example request

```
GET /addon/physna/viewer?databaseId=engineering&assetId=pump-housing-v2&relativePath=%2Fpump.stp
```

#### Example `ready` response

```json
{
    "status": "ready",
    "message": "Physna viewer is ready.",
    "tenantId": "bc336bab-e3e4-4c50-bd00-9d6cbbd9f194",
    "physnaAssetId": "8d65af7c-5412-404b-a494-fbd2cdb62442",
    "viewerToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "physnaApiBase": "https://app-api.physna.com/v3"
}
```

The frontend uses these fields to build the iframe URL:

```
${physnaApiBase}/tenants/${tenantId}/viewer/asset
  ?assetId=${physnaAssetId}
  &token=${viewerToken}
  &theme=dark
  &parentOrigin=${window.location.origin}
```

#### Example `indexing` response

```json
{
    "status": "indexing",
    "message": "Physna is still indexing this file. Please check back shortly.",
    "physnaState": "indexing"
}
```

#### Authorization

-   **API tier:** The caller's role must allow `GET` on `/addon/physna/viewer` through a permission constraint with `objectType: "api"`; a `web` constraint does not grant it. The seeded `admin` and `basicReadOnly` roles and the Database Admin, Database User, Database Read-Only, and Global Read-Only permission templates grant the route.
-   **Object tier:** The caller must have `GET` permission on the specific `databaseId` / `assetId` combination (`objectType: "asset"`).

Both tiers must allow for the endpoint to succeed. Failures return `status: "forbidden"` with HTTP `403`.

---

## EDH Workstations add-on

The EDH Workstations add-on proxies an EDH cluster's desktop, catalog, administration and container operations behind VAMS authorization. All routes below are only deployed when `app.addons.useEdhWorkstations.enabled` is `true`; the `/addon/edh/containers/*` routes additionally require `app.addons.useEdhWorkstations.containers.enabled`, and the `/addon/edh/admin/ami-recipes*` routes additionally require `app.addons.useEdhWorkstations.amiBuilds.enabled`. The session bootstrap and the lease policy change how routes behave but gate none. Every request body is `application/json`; there is no multipart or binary upload on this surface. The calls VAMS makes to the EDH cluster on a request's behalf are form-encoded and are not part of this API. Of the routes below, only `GET /addon/edh/workstations/{sessionId}/connect` and `GET /addon/edh/workstations/{sessionId}/windows-sign-in` make EDH issue an Amazon DCV connection token, for that one desktop: at most one per request EDH answers, and VAMS repeats a read once after a transport failure or a gateway error, which can make a second.

See the [EDH Workstations Add-on](../developer/edh-workstations/overview.md) developer guide for the identity mapping, the ownership rule, the session table and the session bootstrap; the `vamscli edh` command group in [EDH Workstations CLI Commands](../developer/edh-workstations/cli-commands.md) calls these routes.

#### Common error responses

Every route can return the following. EDH's own error text is logged and never echoed; the `message` is VAMS's own wording. A refusal raised inside a handler as a general error — the container routes' `400` texts below, and `Request body must be a JSON object` on the admin registration, lease settings, AMI recipe create and update, and container submission routes — reads `VAMS General Error: <text>`; the texts named on this page are the `<text>`.

The EDH-mapped answers — `EDH rejected the request.` and `EDH found no available AWS capacity for this instance type. Try again later.` (`400`), `429`, `502` and `The requested EDH resource was not found.` (`404`) — come from the workstation, catalog, administration and container routes. The AMI recipe routes (`/addon/edh/admin/ami-recipes*`) do not map EDH errors: an EDH failure while retiring a build or while resolving an `edhStack:` parent image answers `500`, and an EDH failure during a build's registration is recorded on the build row (`status: REGISTER_FAILED` with an `error`) rather than returned.

| HTTP  | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400` | Request validation failed (`instanceType is not permitted in this deployment`, `idleMinutes must be an integer`, the launch texts under `POST /addon/edh/workstations`, the container and AMI recipe texts below, a malformed path identifier), or EDH rejected an operation performed synchronously (`EDH rejected the request.` — an AMI EDH does not know on registration, a stop EDH refuses; `EDH found no available AWS capacity for this instance type. Try again later.` — a start EDH refuses because it found no available AWS capacity for the instance type).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `403` | The caller lacks API-tier or object-tier access, is the `SYSTEM_USER` system identity (which every EDH route refuses), or authenticated with an API key on a route that requires an ID token (`Workstation launch is not available to API key sessions`, `Workstation start is not available to API key sessions`). With AWS WAF enabled, a request the deployment's web ACL blocks is answered `403` before it reaches VAMS, with the body `{"message": "Request blocked by the deployment's AWS WAF web ACL", "blockedBy": "waf", "requestId": "<id>"}` (see [Requests Blocked by AWS WAF](overview.md#requests-blocked-by-aws-waf)).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `404` | The session, image, job, recipe or build does not exist — or, on a `/workstations/{sessionId}` route, is not the caller's (`Workstation not found`; `Session not found` on the admin routes; `Image not found`; `Job not found`; `Recipe not found`; `Build not found`; `The requested EDH resource was not found.` when EDH reports the resource absent during an operation).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `409` | `bootstrapId is already in use` (another user's launch); `Workstation has no EDH session` (stop, start or connect of a launch without a session id); `Workstation is still being created` (delete of a session-less `CREATING` launch, or deactivate of a `CREATING` launch EDH does not list, whose record changed less than 15 minutes ago); `Workstation is not ready for connection`; `A deleted workstation cannot be started` and `The lease on this workstation has expired; renew it before starting` (start of a deleted workstation, or of one whose lease ran out); `The workstation is still <edhState> in EDH; start it again once it has stopped.` (start of a stopped workstation EDH still reports in another state, such as `stopping`); `A start of this workstation is already in progress` (start of a workstation EDH still reports stopped while another start of it is being processed, less than 3 minutes after a start EDH did not confirm, while the reconciler revokes the stopped desktop's key, or while a lease renewal re-issues the desktop's key, for up to 5 minutes; the body also carries `"startInProgress": true`, which no other answer carries); `Leases are disabled for this deployment`, `This workstation has no lease to renew` and `A deleted workstation cannot be renewed` (renew); `Session is not running in EDH` (administrative stop of a session EDH no longer lists); the AMI recipe conflicts listed under each recipe route. |
| `429` | `The EDH quota for this operation has been reached.` — EDH refused a job submission (`POST /addon/edh/containers/jobs`) for a quota, budget or target-node limit. A launch never answers `429`: EDH's refusal is recorded on the row as `lastError`. Any other route relays this status only if EDH's own error text names a quota.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `502` | `EDH did not confirm the request in time; it may still be in progress. Refresh before trying again.` — a request VAMS sends to EDH once (workstation stop, start and delete; administrative image registration and deletion, and instance stop and deactivate; container job submission and kill) lost its answer after it may have reached EDH, so EDH may have carried it out; the body also carries `"outcomeUnknown": true`, as does the container job submit's unreadable answer below, and the route's listing shows the outcome before the request is sent again. `EDH is unavailable. Try again later.` — EDH is unreachable or answered `5xx`; `EDH accepted the request but returned an unreadable answer.` — EDH answered a success status with a body that is not JSON (the container job submit alone adds `Check the job list before retrying.` and `"outcomeUnknown": true`, since the job may have been submitted); `EDH rejected the deployment credential.` — the root key was refused. The health route adds a `reason`.                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `500` | Unexpected server error, or an EDH failure on an AMI recipe route.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

#### Workstation record

The routes under `/addon/edh/workstations` return this object, directly for a single session and as the `Items` of a list.

| Field                 | Type    | Description                                                                                                                                                                                                                                                 |
| --------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bootstrapId`         | string  | Client-generated UUID that keyed the launch; the idempotency key, and the identifier of a record that has no session id.                                                                                                                                    |
| `sessionId`           | string  | EDH session id; `null` while the record is `CREATING` and on an `ORPHANED` launch that never received one.                                                                                                                                                  |
| `userId`              | string  | VAMS user who owns the session.                                                                                                                                                                                                                             |
| `edhUid`              | string  | The EDH uid the session was created under.                                                                                                                                                                                                                  |
| `state`               | string  | `CREATING`, `ACTIVE`, `STOPPED`, `DELETED` or `ORPHANED` — VAMS's record of the session, derived from EDH's session state on every read.                                                                                                                    |
| `edhState`            | string  | EDH's own state string for the session (`pending`, `placing`, `running`, `stopping`, `stopped`, `interrupting`, `interrupted`, `error`); `null` until EDH has reported one.                                                                                 |
| `lastError`           | string  | Why a launch ended `ORPHANED` — `USER_NOT_PROVISIONED`, `QUOTA_EXCEEDED`, `IMAGE_UNAVAILABLE`, `CAPACITY_UNAVAILABLE`, `EDH_REFUSED`, `LAUNCH_ERROR` or `CREATE_TIMED_OUT` — or `LEASE_EXPIRED` on a workstation the lease sweep stopped; `null` otherwise. |
| `launchAttempts`      | integer | How many times the launch worker started this launch (at most 3).                                                                                                                                                                                           |
| `name`                | string  | Display name given at launch.                                                                                                                                                                                                                               |
| `imageId`             | string  | EDH software-stack id the session runs.                                                                                                                                                                                                                     |
| `osFamily`            | string  | `linux` or `windows`.                                                                                                                                                                                                                                       |
| `instanceType`        | string  | Amazon EC2 instance type of the desktop.                                                                                                                                                                                                                    |
| `rootSizeGb`          | integer | Root volume size in GiB requested at launch.                                                                                                                                                                                                                |
| `createdAt`           | string  | ISO 8601 timestamp of the launch.                                                                                                                                                                                                                           |
| `updatedAt`           | string  | ISO 8601 timestamp of the last state change.                                                                                                                                                                                                                |
| `connectionAvailable` | boolean | `true` while EDH reports the desktop `running`; **Connect** then fetches its connection URL.                                                                                                                                                                |
| `leaseExpiresAt`      | string  | ISO 8601 timestamp at which the lease runs out; `null` on a workstation no lease covers.                                                                                                                                                                    |
| `leaseStoppedAt`      | string  | ISO 8601 timestamp at which the lease sweep stopped the workstation, from which the grace window runs; `null` until then, and cleared by a renewal or a start.                                                                                              |
| `leaseHoursAtLaunch`  | integer | The lease the workstation was launched with, in hours; `null` on a workstation no lease covers. A renewal recomputes the expiry from the current policy and leaves it alone.                                                                                |

Every field is present on every record; `sessionId`, `edhState`, `lastError`, `leaseExpiresAt`, `leaseStoppedAt` and `leaseHoursAtLaunch` are `null` where noted. The record never carries the per-session API key, its secret ARN or name, or the key's expiry. The meanings of the states and the failure codes are in [EDH Workstations Add-on — Architecture](../developer/edh-workstations/overview.md#session-states), and the lease rules are in [EDH Workstations Add-on — Leases](../developer/edh-workstations/overview.md#leases).

#### Identifiers

The `sessionId` path parameter of every `/addon/edh/workstations/{sessionId}` route, and of the administrative stop and deactivate routes, is the EDH session id or — for a launch that has not received one — the record's `bootstrapId`. A malformed value answers `400` before any lookup: on a `/addon/edh/workstations/{sessionId}` route, one that does not match `^[-_a-zA-Z0-9]{3,63}$` (`sessionId is invalid. Must follow the regexp ^[-_a-zA-Z0-9]{3,63}$`); on the administrative routes, one that is not 1–128 letters, digits, `.`, `_` or `-` (`sessionId is invalid`).

### GET `/addon/edh/workstations`

Lists the caller's workstations that are not deleted, merged with the state EDH reports for the caller's sessions (read without issuing a connection token); a launch that has no session id yet is returned as recorded until the launch worker or the reconciler's next sweep reads its id back. The state EDH implies is persisted, so `state`, `edhState` and `connectionAvailable` are live. A record deleted or removed after the call read it is returned as read, and nothing is written onto it.

#### Responses

| HTTP  | Body                                                              |
| ----- | ----------------------------------------------------------------- |
| `200` | `{ "Items": [<workstation record>, ...] }`, `Items` may be empty. |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/workstations` (`objectType: "api"`).
-   **Object tier:** each record is filtered through `GET` on `edhWorkstation` (`edhImageId`, `edhOsFamily`, `edhInstanceType`); records the caller may not see are omitted. Only the caller's own sessions are listed.

### POST `/addon/edh/workstations`

Launches a desktop. The launch is asynchronous: the record is written in state `CREATING` and the EDH calls run in a background function after the response; poll `GET /addon/edh/workstations` or the record until it reports `ACTIVE` and a `sessionId`, or `ORPHANED` with a `lastError`. Refuses callers that authenticated with an API key. The request makes one EDH read — the desktop software-stack catalog — to check that `imageId` is a registered desktop image and to derive its OS family, which the body's `osFamily` must match, and its architecture, which `instanceType` must run; it mutates nothing in EDH, and an EDH failure on that read answers `502` (EDH unreachable or its credential rejected) before anything is written.

#### Request body

| Field          | Type    | Required | Description                                                                                                                                                                                                                                                                                           |
| -------------- | ------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bootstrapId`  | string  | Yes      | Client-generated UUID (36 characters, 8-4-4-4-12 hexadecimal; the CLI and the web console generate version 4); a repeat with the same value returns the existing record.                                                                                                                              |
| `imageId`      | string  | Yes      | EDH software-stack id from `GET /addon/edh/catalog/images`; 1–256 characters.                                                                                                                                                                                                                         |
| `instanceType` | string  | Yes      | Instance type (`family.size`, hyphens allowed in either part); must be in `edhAllowedInstanceTypes` when that list is non-empty. A type the image's EDH profile lists only under architectures of another family than the image's (`x86_64` and `i386` are one family) is refused.                    |
| `name`         | string  | Yes      | Display name; 1–256 characters, VAMS object-name rules apply.                                                                                                                                                                                                                                         |
| `rootSizeGb`   | integer | Yes      | Root volume size, 10–2048, and at least the image's registered root size (`rootSize` in `GET /addon/edh/catalog/images`).                                                                                                                                                                             |
| `osFamily`     | string  | Yes      | `linux` or `windows`; must equal the image's `osFamily` as the catalog reports it.                                                                                                                                                                                                                    |
| `leaseHours`   | integer | No       | How long the workstation may run before its lease expires, 1 up to the lease policy's `leaseHours`. Omit it for no expiry, unless the policy is `forced`: every launch is then stamped with the policy's `leaseHours` and this value is ignored. Read the policy from `GET /addon/edh/catalog/lease`. |

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `202` | The new workstation record (`state: CREATING`, `sessionId: null`) plus `sessionBootstrap`: `provisioned`, `skipped`, `unavailable` or `disabled` — the outcome of placing a session key; always `disabled` for a Windows workstation, which takes no session key.                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `200` | The stored record, when the caller repeats a `POST` with a `bootstrapId` of their own; nothing is launched again.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `400` | Validation failed; `Request body is not valid JSON`; `instanceType is not permitted in this deployment`; `imageId is not a registered VDI image`; `The image's OS family cannot be determined`; `osFamily does not match the image`; `instanceType does not match the image's architecture` — the image's profile lists the instance type only under architectures of another family than the image's (`x86_64` and `i386` are one family); `Root disk size must be at least <n> GB for this image` — `rootSizeGb` is below the image's registered root size; `Leases are disabled for this deployment` — a `leaseHours` sent while the policy is off; `leaseHours must be 1-<cap>` — above the policy's `leaseHours`. |
| `403` | The caller lacks access, or authenticated with an API key (`Workstation launch is not available to API key sessions`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `409` | `bootstrapId is already in use` — the id belongs to another user's launch.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `500` | The launch could not be handed to the background function: any session key minted for it is revoked and the record is removed; nothing was launched in EDH, so the same `bootstrapId` can be sent again.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |

#### Example request

```json
{
    "bootstrapId": "5d0f6e1a-9c1b-4d0e-9d6a-2f1f7b0f1e4c",
    "imageId": "42",
    "instanceType": "g5.xlarge",
    "name": "cad-review",
    "rootSizeGb": 100,
    "osFamily": "linux"
}
```

#### Authorization

-   **API tier:** `POST` on `/addon/edh/workstations`; the caller must hold an ID token (API-key principals receive `403`).
-   **Object tier:** `POST` on `edhWorkstation` evaluated against the request body's `imageId` and `instanceType` and the OS family EDH's catalog reports for the image.

### GET `/addon/edh/workstations/{sessionId}`

Returns one workstation record; for a record with a session id, merged with the state EDH reports for its session (read without issuing a connection token), unless the record was deleted or removed after the call read it, which is returned as read.

#### Path parameters

| Parameter   | Type   | Description                                                   |
| ----------- | ------ | ------------------------------------------------------------- |
| `sessionId` | string | EDH session id, or the `bootstrapId` of a launch without one. |

#### Responses

| HTTP  | Body                                                                                                                 |
| ----- | -------------------------------------------------------------------------------------------------------------------- |
| `200` | The workstation record.                                                                                              |
| `400` | `{ "message": "sessionId is invalid. Must follow the regexp ^[-_a-zA-Z0-9]{3,63}$" }` — the path value is malformed. |
| `404` | `{ "message": "Workstation not found" }` — no such record, or not the caller's.                                      |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/workstations/{sessionId}`.
-   **Object tier:** `GET` on `edhWorkstation` from the stored record; a session that is not the caller's answers `404`.

### DELETE `/addon/edh/workstations/{sessionId}`

Deletes the desktop in EDH (a desktop EDH already removed is tolerated), marks the record `DELETED`, and — with session bootstrap enabled — revokes every session key of the desktop and removes its secret. A delete EDH does not answer is followed by a read of the desktop's state: a desktop EDH no longer reports is recorded `DELETED` as above, and any other outcome answers `502`. A launch that never received a session id is deleted without an EDH call: an `ORPHANED` one at once, a `CREATING` one only after 15 minutes without a change.

#### Path parameters

| Parameter   | Type   | Description                                                   |
| ----------- | ------ | ------------------------------------------------------------- |
| `sessionId` | string | EDH session id, or the `bootstrapId` of a launch without one. |

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                               |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | The updated record (`state: DELETED`).                                                                                                                                                                                                             |
| `400` | `{ "message": "sessionId is invalid. Must follow the regexp ^[-_a-zA-Z0-9]{3,63}$" }` — the path value is malformed.                                                                                                                               |
| `404` | `{ "message": "Workstation not found" }`                                                                                                                                                                                                           |
| `409` | `{ "message": "Workstation is still being created" }` — a session-less `CREATING` launch whose record changed less than 15 minutes ago.                                                                                                            |
| `502` | `{ "message": "EDH did not confirm the request in time; it may still be in progress. Refresh before trying again.", "outcomeUnknown": true }` — EDH did not answer the delete, and the read after it still shows the desktop or could not be made. |

#### Authorization

-   **API tier:** `DELETE` on `/addon/edh/workstations/{sessionId}`.
-   **Object tier:** `DELETE` on `edhWorkstation` from the stored record; owner only.

### POST `/addon/edh/workstations/{sessionId}/stop`

Stops the desktop and marks the record `STOPPED`; with session bootstrap enabled, revokes every session key of the desktop and overwrites the secret's value, keeping the secret. A stop EDH does not answer is followed by a read of the desktop's state: a desktop EDH reports `stopping` or `stopped` is recorded as above, and any other outcome answers `502` and leaves the keys in place.

#### Path parameters

| Parameter   | Type   | Description                                                   |
| ----------- | ------ | ------------------------------------------------------------- |
| `sessionId` | string | EDH session id, or the `bootstrapId` of a launch without one. |

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                     |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `200` | The updated record (`state: STOPPED`, `connectionAvailable: false`).                                                                                                                                                                                                     |
| `400` | `{ "message": "sessionId is invalid. Must follow the regexp ^[-_a-zA-Z0-9]{3,63}$" }` — the path value is malformed.                                                                                                                                                     |
| `404` | `{ "message": "Workstation not found" }`                                                                                                                                                                                                                                 |
| `409` | `{ "message": "Workstation has no EDH session" }` — the launch has not received a session id.                                                                                                                                                                            |
| `502` | `{ "message": "EDH did not confirm the request in time; it may still be in progress. Refresh before trying again.", "outcomeUnknown": true }` — EDH did not answer the stop, and the read after it shows the desktop neither stopping nor stopped, or could not be made. |

#### Authorization

-   **API tier:** `POST` on `/addon/edh/workstations/{sessionId}/stop`.
-   **Object tier:** `POST` on `edhWorkstation` from the stored record; owner only.

### POST `/addon/edh/workstations/{sessionId}/start`

Starts a stopped workstation: the record returns to `CREATING` with `edhState` `pending` and no connection until EDH reports the desktop `running`, when the next list or get reports it `ACTIVE`; with session bootstrap enabled, mints a new session key into the same secret first (a Windows workstation takes none and answers `disabled`) and reports the outcome in `sessionBootstrap`. A `STOPPED` workstation EDH still reports in a state other than `stopped` — `stopping` while the instance stops and until EDH next checks it, or `interrupted` — is refused, since EDH starts only a desktop it reports stopped; one EDH reports without a state is left to EDH. A start EDH does not answer is followed by a read of the desktop's state: a desktop EDH reports `pending`, `placing`, `running` or `error` counts as started and answers as above, and any other outcome answers `502` with the new key kept, since EDH records a start only once the instance is starting. Refuses callers that authenticated with an API key (`Workstation start is not available to API key sessions`).

#### Path parameters

| Parameter   | Type   | Description                                                   |
| ----------- | ------ | ------------------------------------------------------------- |
| `sessionId` | string | EDH session id, or the `bootstrapId` of a launch without one. |

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | The updated record (`state: CREATING`, `edhState: pending`) plus `sessionBootstrap`: `provisioned`, `skipped`, `unavailable` or `disabled` — the outcome of placing a session key, as for a launch. Also the answer to a start EDH did not answer that the read of the desktop's state after it shows started.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `400` | `{ "message": "sessionId is invalid. Must follow the regexp ^[-_a-zA-Z0-9]{3,63}$" }` — the path value is malformed. `{ "message": "EDH found no available AWS capacity for this instance type. Try again later." }` — EDH refused the start because it found no available AWS capacity for the workstation's instance type; the workstation stays `STOPPED` and any session key minted for the start is revoked.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `403` | The caller authenticated with an API key.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `404` | `{ "message": "Workstation not found" }`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `409` | `{ "message": "Workstation has no EDH session" }` — the launch has not received a session id; `{ "message": "A deleted workstation cannot be started" }` — the record is `DELETED`, or was deleted or removed after the start read it; `{ "message": "The lease on this workstation has expired; renew it before starting" }` — leases are enabled and `leaseExpiresAt` has passed; `{ "message": "Workstation is already running" }` — the record is `ACTIVE`; `{ "message": "Workstation is not stopped" }` — any other state but `STOPPED`. `{ "message": "The workstation is still <edhState> in EDH; start it again once it has stopped." }` — the record is `STOPPED` but EDH reports the desktop in a state other than `stopped`, such as `stopping` or `interrupted`; answered after EDH's state read, before any key is minted, the start is claimed or EDH is sent a start. `{ "message": "A start of this workstation is already in progress", "startInProgress": true }` — EDH still reports the desktop stopped while another start of it, from another tab or client, is being processed, less than 3 minutes after a start it did not confirm, while the reconciler revokes the stopped desktop's key, or while a lease renewal re-issues the desktop's key, for up to 5 minutes; a start that did not happen holds nothing off. The deleted and expired-lease refusals are answered before EDH is called, except for a record deleted or removed after the start read it, which is answered after EDH's state read as the in-progress refusal is; none of them mints a session key. |
| `502` | `{ "message": "EDH did not confirm the request in time; it may still be in progress. Refresh before trying again.", "outcomeUnknown": true }` — EDH did not answer the start, and the read after it shows the desktop stopped, does not report it, or could not be made. The desktop may still be starting and keeps the new session key; a start sent again within 3 minutes answers `409`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |

#### Authorization

-   **API tier:** `POST` on `/addon/edh/workstations/{sessionId}/start`; ID token required.
-   **Object tier:** `POST` on `edhWorkstation` from the stored record; owner only.

### GET `/addon/edh/workstations/{sessionId}/connect`

Returns the Amazon DCV connection URL of an `ACTIVE` workstation — the connection string EDH's per-user listing renders for that one session — which the browser navigates to in the tab it opened on the click; the URL points at the EDH cluster's Amazon DCV entry point, which the browser must be able to reach. EDH issues at most one connection token per request it answers, for that desktop; VAMS repeats the read once after a transport failure or a gateway error, which can make a second. The URL is a credential: it appears in this response only and is never logged, audited or stored.

#### Path parameters

| Parameter   | Type   | Description                                                   |
| ----------- | ------ | ------------------------------------------------------------- |
| `sessionId` | string | EDH session id, or the `bootstrapId` of a launch without one. |

#### Responses

| HTTP  | Body                                                                                                                                                                                                                     |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `200` | `{ "sessionId": "...", "connectionUrl": "https://...", "osFamily": "linux" }`                                                                                                                                            |
| `400` | `{ "message": "sessionId is invalid. Must follow the regexp ^[-_a-zA-Z0-9]{3,63}$" }` — the path value is malformed.                                                                                                     |
| `404` | `{ "message": "Workstation not found" }`                                                                                                                                                                                 |
| `409` | `{ "message": "Workstation has no EDH session" }` — no session id yet; `{ "message": "Workstation is not ready for connection" }` — the record is not `ACTIVE`, or EDH does not report the desktop `running` with a URL. |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/workstations/{sessionId}/connect`.
-   **Object tier:** `GET` on `edhWorkstation` from the stored record; owner only.

### GET `/addon/edh/workstations/{sessionId}/windows-sign-in`

Returns the Windows sign-in of an `ACTIVE` Windows workstation: the user name and the password EDH generated for the desktop, with which its owner signs in at the Windows lock screen after choosing Send Ctrl + Alt + Del in the Amazon DCV toolbar. The user name is the `username` value of the desktop's connection string, else the EDH session owner, else the caller's EDH uid. The route reads that one desktop from EDH's per-user listing, as connect does, so EDH issues at most one connection token per request it answers, for that desktop, and VAMS repeats the read once after a transport failure or a gateway error, which can make a second; the route takes only the user name from the connection string and never returns the string itself. A directory account, a user name of the form `DOMAIN\user`, signs in with its own directory password, so the response carries `"password": null` and `"directoryAccount": true`. The password is a credential: it appears in this response only, which is sent with `Cache-Control: no-cache, no-store`, and is never logged, audited or stored. No MCP tool calls this route.

#### Path parameters

| Parameter   | Type   | Description                                                   |
| ----------- | ------ | ------------------------------------------------------------- |
| `sessionId` | string | EDH session id, or the `bootstrapId` of a launch without one. |

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                       |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | `{ "sessionId": "...", "username": "...", "password": "...", "directoryAccount": false }`                                                                                                                                                                                                                                  |
| `400` | `{ "message": "sessionId is invalid. Must follow the regexp ^[-_a-zA-Z0-9]{3,63}$" }` — the path value is malformed; `{ "message": "Workstation is not a Windows workstation" }` — the record's `osFamily` is not `windows`.                                                                                               |
| `404` | `{ "message": "Workstation not found" }`                                                                                                                                                                                                                                                                                   |
| `409` | `{ "message": "Workstation has no EDH session" }` — no session id yet; `{ "message": "Workstation is not ready for connection" }` — the record is not `ACTIVE`, or EDH does not report the desktop `running` with a URL; `{ "message": "Windows password is not available yet" }` — EDH lists no password for the desktop. |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/workstations/{sessionId}/windows-sign-in`.
-   **Object tier:** `GET` on `edhWorkstation` from the stored record; owner only.

### POST `/addon/edh/workstations/{sessionId}/renew`

Resets the lease to the policy's current `leaseHours` counted from the renewal, clears `leaseStoppedAt`, and clears `lastError` when it is `LEASE_EXPIRED`. The new expiry follows the policy in force at the renewal rather than the record's `leaseHoursAtLaunch`, so an administrator who shortens or lengthens the lease changes what a renewal grants. A renewal of an `ACTIVE` workstation with no start in progress also re-issues the desktop's VAMS sign-in: with session bootstrap enabled, it mints a new session key into the workstation's session secret (a Windows workstation takes none and answers `disabled`) and keeps the key it replaces valid until that key's own expiry. A caller that authenticated with an API key renews the lease only, as does a renewal of a workstation in any other state. The renewal asks nothing of EDH. A workstation the sweep has already stopped is renewed and then started with `POST /addon/edh/workstations/{sessionId}/start`.

#### Path parameters

| Parameter   | Type   | Description                                                   |
| ----------- | ------ | ------------------------------------------------------------- |
| `sessionId` | string | EDH session id, or the `bootstrapId` of a launch without one. |

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | The updated record, carrying the new `leaseExpiresAt` and `leaseStoppedAt: null`, plus `sessionBootstrap` when the renewal re-issued, or tried to re-issue, the desktop's VAMS sign-in: `provisioned` — a new session key was placed in the workstation's session secret; `skipped` — the caller holds no roles, so no key was minted; `unavailable` — no new key could be minted, the new key could not be confirmed in the session secret, or another request held the workstation's credential or changed it meanwhile; `disabled` — session bootstrap is off for the deployment, or the workstation is Windows. With `skipped` or `unavailable`, any key held by a desktop that is still running stays valid until its own expiry, and the lease is renewed all the same. The field is absent when no re-issue was attempted: for a caller that authenticated with an API key, a workstation that is not `ACTIVE`, or one whose start is in progress. |
| `400` | `{ "message": "sessionId is invalid. Must follow the regexp ^[-_a-zA-Z0-9]{3,63}$" }` — the path value is malformed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `404` | `{ "message": "Workstation not found" }`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `409` | `{ "message": "Leases are disabled for this deployment" }`; `{ "message": "This workstation has no lease to renew" }` — the record carries no `leaseExpiresAt`; `{ "message": "A deleted workstation cannot be renewed" }` — the record is `DELETED`, or was deleted or removed after the renew read it; the lease is not written.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |

#### Authorization

-   **API tier:** `POST` on `/addon/edh/workstations/{sessionId}/renew`.
-   **Object tier:** `POST` on `edhWorkstation` from the stored record; owner only.

### GET `/addon/edh/catalog/images`

Lists the desktop images users may launch. A stack for which EDH reports neither an OS family nor a base OS is not listed.

Each image carries `category`. An image is `amiRecipe` when an `AVAILABLE` desktop build of an AMI recipe registered it, that is, when the build's `edhStackId` is the image's `imageId`; such an image also carries `recipeId`, `recipeDisplayName` and `recipeVersion`, the build's version as an integer. Every other image is `baseline`, including an image registered through `POST /addon/edh/admin/images/vdi` or at deployment, and the stack of a `RETIRED` build. The recipe images come first and the baseline images follow, each group sorted by `name` without regard to case and with each run of digits compared as a number, so `Ubuntu 9` comes before `ubuntu 22 GPU`. The recipe table is read only while `amiBuilds.enabled` is `true`, so with AMI builds off every image is `baseline`; a read that fails is logged as a warning and lists every image as `baseline`, and the request still answers `200`.

Each image also carries `architecture`, the CPU architecture EDH registered for the software stack (for example `x86_64` or `arm64`), or `null` when EDH records none.

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                                                                     |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | `{ "Items": [{ "imageId", "name", "baseOs", "osFamily", "architecture", "rootSize", "description", "category", "recipeId", "recipeDisplayName", "recipeVersion" }, ...] }` — `osFamily` is the value to send on launch; `architecture` is a string or `null`; `category` is `amiRecipe` or `baseline`, and `recipeId`, `recipeDisplayName` and `recipeVersion` are present on an `amiRecipe` image only. |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/catalog/images`.
-   **Object tier:** each item is filtered through `GET` on `edhImage` (`edhImageId`, `edhOsFamily`).

### GET `/addon/edh/catalog/instance-types`

Lists the instance types users may pick: the EDH profile's types intersected with `edhAllowedInstanceTypes` when that list is non-empty, sorted by name without regard to case and with each run of digits compared as a number, so `g5.2xlarge` comes before `g5.12xlarge` and `m7i.large` before `m7i.xlarge`.

Each type carries `architectures`, the architecture groups of the EDH profile that list it, in EDH's order, or `[]` when the profile's allowed types are not grouped by architecture. An instance type runs an image when the image's `architecture` is `null`, the type's `architectures` is empty, or `architectures` contains the image's `architecture`; the launch wizard and `vamscli edh catalog instance-types --image-id` offer only the types that run the chosen image by this rule.

#### Responses

| HTTP  | Body                                                                                                                                  |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | `{ "Items": [{ "instanceType": "g5.xlarge", "architectures": ["x86_64"] }, ...] }`                                                    |
| `502` | `{ "message": "EDH virtual desktop profile is unavailable" }` — EDH does not list the profile named by `vdi.virtualDesktopProfileId`. |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/catalog/instance-types`.
-   **Object tier:** none (API-level only).

### GET `/addon/edh/catalog/lease`

Reports the lease policy as a launch reads it, so a client can decide whether to offer an expiry and how long a lease it may ask for. The policy's audit fields are administrative and are not part of this response.

#### Responses

| HTTP  | Body                                                                                   |
| ----- | -------------------------------------------------------------------------------------- |
| `200` | `{ "lease": { "enabled": true, "leaseHours": 8, "forced": false, "graceHours": 24 } }` |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/catalog/lease`.
-   **Object tier:** none (API-level only).

### GET `/addon/edh/admin/images`

Lists every registered desktop and simulation image.

Each image carries `architecture`, the CPU architecture EDH reports for the image, such as `x86_64` or `arm64`, or `null` when EDH reports none; a desktop image carries the value `GET /addon/edh/catalog/images` carries for it.

#### Responses

| HTTP  | Body                                                                                                                                                                                                                     |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `200` | `{ "Items": [{ "kind", "imageId", "stackName", "amiId", "baseOs", "osFamily", "architecture", "rootSize", "profileId", "description" }, ...] }` — `kind` is `vdi` or `simulation`; `architecture` is a string or `null`. |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/admin/images`.
-   **Object tier:** each item is filtered through `GET` on `edhImage`.

### POST `/addon/edh/admin/images/vdi`

Registers a desktop image in EDH, reads the new software stack's id back from the catalog by stack name, and attaches the stack to the project named by `vdi.defaultProject`, which is how EDH resolves a user's launchable stacks. An assignment EDH refuses answers with the mapped EDH status code and the message `registered in EDH but not assigned to project '<project>' (<ErrorClass>: <message>)`; the stack stays registered outside the project. An assignment EDH does not answer carries `"outcomeUnknown": true` beside that message, since the stack may be in the project, and a registration EDH does not answer is the unknown-outcome `502` of the common table, since the stack may exist. A read-back that fails answers with the mapped status of that failure and `registered in EDH but not assigned to project '<project>' (the catalog could not be read back)`, and one that does not find the stack answers `502` with `registered in EDH but not assigned to project '<project>' (its EDH id could not be read back)`; neither attempts the assignment. EDH refuses a second active stack of the same name (`400` `EDH rejected the request.`), so a stack left outside the project is attached to it in EDH, or deleted and registered again.

#### Request body

| Field                     | Type    | Required | Description                                                                                                                                                                                              |
| ------------------------- | ------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `amiId`                   | string  | Yes      | AMI id (`ami-` followed by 8–17 hex digits).                                                                                                                                                             |
| `stackName`               | string  | Yes      | Unique stack name; the registration's natural key. 1–256 characters, VAMS object-name rules apply.                                                                                                       |
| `baseOs`                  | string  | Yes      | EDH base-OS key of the AMI: `^[a-z][a-z0-9]{1,63}$`, one of the keys EDH's `DCV_BASE_OS` setting lists (for example `ubuntu2204`, `amazonlinux2023`, `windows2022`); EDH refuses a key it does not list. |
| `rootSize`                | integer | Yes      | Root volume size in GiB, 1–2048.                                                                                                                                                                         |
| `virtualDesktopProfileId` | integer | Yes      | EDH virtual-desktop profile id; at least 0.                                                                                                                                                              |
| `launchTenancy`           | string  | No       | EDH launch tenancy; at most 30 characters.                                                                                                                                                               |
| `description`             | string  | No       | Description shown in the catalog; at most 256 characters.                                                                                                                                                |

#### Responses

| HTTP  | Body                                                                                                                                                                                                                     |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `201` | `{ "kind": "vdi", "imageId", "stackName", "amiId", "osFamily" }` — `imageId` is the new stack's id, read back from the catalog by stack name; the stack is attached to the project.                                      |
| `502` | `registered in EDH but not assigned to project '<project>' (its EDH id could not be read back)` — the catalog does not list the new stack. The other unassigned answers carry the status of the EDH failure behind them. |

#### Authorization

-   **API tier:** `POST` on `/addon/edh/admin/images/vdi`.
-   **Object tier:** `POST` on `edhImage` evaluated against the body (`edhImageId` is the `stackName`, `edhOsFamily` is derived from `baseOs`).

### POST `/addon/edh/admin/images/simulation`

Registers a simulation (target-node) image in EDH and attaches the new software stack to the project named by `vdi.defaultProject`, as a desktop image registration does.

#### Request body

| Field                  | Type    | Required | Description                                                                                         |
| ---------------------- | ------- | -------- | --------------------------------------------------------------------------------------------------- |
| `amiId`                | string  | Yes      | AMI id (`ami-` followed by 8–17 hex digits).                                                        |
| `stackName`            | string  | Yes      | Unique stack name; the registration's natural key. 1–256 characters, VAMS object-name rules apply.  |
| `rootSize`             | integer | Yes      | Root volume size in GiB, 1–2048.                                                                    |
| `targetNodeProfileId`  | integer | Yes      | EDH target-node profile id; at least 0.                                                             |
| `targetNodeUserDataId` | integer | No       | EDH user-data template the stack boots with, at least 1; `1` (EDH's default template) when omitted. |
| `userDataVariables`    | string  | No       | User-data variables EDH applies at launch; at most 16,384 characters.                               |
| `description`          | string  | No       | Description shown in the catalog; at most 256 characters.                                           |

#### Responses

| HTTP  | Body                                                                                                                                                          |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `201` | `{ "kind": "simulation", "imageId", "stackName", "amiId", "osFamily" }` — `imageId` and the project assignment as for a desktop image; `osFamily` is `linux`. |
| `502` | As for a desktop image.                                                                                                                                       |

#### Authorization

-   **API tier:** `POST` on `/addon/edh/admin/images/simulation`.
-   **Object tier:** `POST` on `edhImage` evaluated against the body.

### DELETE `/addon/edh/admin/images/vdi/{imageId}`

Deletes a desktop image from EDH.

#### Path parameters

| Parameter | Type   | Description            |
| --------- | ------ | ---------------------- |
| `imageId` | string | EDH software-stack id. |

#### Responses

| HTTP  | Body                                            |
| ----- | ----------------------------------------------- |
| `200` | `{ "kind": "vdi", "imageId", "deleted": true }` |
| `400` | `{ "message": "imageId is invalid" }`           |
| `404` | `{ "message": "Image not found" }`              |

#### Authorization

-   **API tier:** `DELETE` on `/addon/edh/admin/images/vdi/{imageId}`.
-   **Object tier:** `DELETE` on `edhImage` from the EDH record.

### DELETE `/addon/edh/admin/images/simulation/{imageId}`

Deletes a simulation image from EDH.

#### Path parameters

| Parameter | Type   | Description            |
| --------- | ------ | ---------------------- |
| `imageId` | string | EDH software-stack id. |

#### Responses

| HTTP  | Body                                                   |
| ----- | ------------------------------------------------------ |
| `200` | `{ "kind": "simulation", "imageId", "deleted": true }` |
| `400` | `{ "message": "imageId is invalid" }`                  |
| `404` | `{ "message": "Image not found" }`                     |

#### Authorization

-   **API tier:** `DELETE` on `/addon/edh/admin/images/simulation/{imageId}`.
-   **Object tier:** `DELETE` on `edhImage` from the EDH record.

### GET `/addon/edh/admin/instances`

Lists every session on the cluster, joined with the VAMS session record where one exists.

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                            |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | `{ "Items": [{ "sessionId", "edhState", "owner", "name", "createdAt", "updatedAt", "userId", "bootstrapId", "state", "lastError", "imageId", "osFamily", "instanceType" }, ...] }` — `userId`, `bootstrapId`, `updatedAt` and `lastError` are `null` for a session VAMS did not launch; `imageId`, `osFamily` and `instanceType` then come from the EDH record. |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/admin/instances`.
-   **Object tier:** each item is filtered through `GET` on `edhWorkstation`.

### GET `/addon/edh/admin/instances/stale`

Lists the sessions whose state is `STOPPED` or `ORPHANED` — EDH reports them stopped, interrupted or in `error` — and whose record last changed at least `idleMinutes` ago (such a session VAMS never launched is listed at once), plus the sessions VAMS recorded that EDH no longer lists.

#### Query parameters

| Parameter     | Type    | Required | Description                                                  |
| ------------- | ------- | -------- | ------------------------------------------------------------ |
| `idleMinutes` | integer | No       | Idle threshold in minutes; `120` when omitted, at least `1`. |

#### Responses

| HTTP  | Body                                                                                                                                         |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | `{ "Items": [<instance item> + { "stale": true, "staleReason": "stopped" \| "missingInEdh" }, ...], "idleMinutes": <threshold in minutes> }` |
| `400` | `{ "message": "idleMinutes must be an integer" }` or `{ "message": "idleMinutes must be at least 1" }`                                       |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/admin/instances/stale`.
-   **Object tier:** each item is filtered through `GET` on `edhWorkstation`.

### POST `/addon/edh/admin/instances/{sessionId}/stop`

Stops any session, regardless of owner; the stop is sent to EDH as the session's owner and the record's `connectionAvailable` is cleared. Every session key of the desktop, if any, is revoked inline once EDH has acted and the secret's value is overwritten; a revoke that fails is left to the reconciler's next sweep.

#### Path parameters

| Parameter   | Type   | Description                                                   |
| ----------- | ------ | ------------------------------------------------------------- |
| `sessionId` | string | EDH session id, or the `bootstrapId` of a launch without one. |

#### Responses

| HTTP  | Body                                                                                                       |
| ----- | ---------------------------------------------------------------------------------------------------------- |
| `200` | The instance item with `state: STOPPED`; the VAMS record's `connectionAvailable` is cleared.               |
| `400` | `{ "message": "sessionId is invalid" }`                                                                    |
| `404` | `{ "message": "Session not found" }` — neither EDH nor VAMS knows the id.                                  |
| `409` | `{ "message": "Session is not running in EDH" }` — EDH no longer lists the session; deactivate it instead. |

#### Authorization

-   **API tier:** `POST` on `/addon/edh/admin/instances/{sessionId}/stop`.
-   **Object tier:** `POST` on `edhWorkstation` from the VAMS record or, for a session VAMS did not launch, from the EDH record.

### POST `/addon/edh/admin/instances/{sessionId}/deactivate`

Deletes any session in EDH, regardless of owner, acting as the service actor, and marks its VAMS record `DELETED`. A session EDH no longer lists is recorded `DELETED` without an EDH call. Every session key of the desktop, if any, is revoked inline and its secret removed; a revoke that fails is left to the reconciler's next sweep.

#### Path parameters

| Parameter   | Type   | Description                                                   |
| ----------- | ------ | ------------------------------------------------------------- |
| `sessionId` | string | EDH session id, or the `bootstrapId` of a launch without one. |

#### Responses

| HTTP  | Body                                                                                                                                          |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | The instance item with `state: DELETED`.                                                                                                      |
| `400` | `{ "message": "sessionId is invalid" }`                                                                                                       |
| `404` | `{ "message": "Session not found" }`                                                                                                          |
| `409` | `{ "message": "Workstation is still being created" }` — a `CREATING` launch EDH does not list, whose record changed less than 15 minutes ago. |

#### Authorization

-   **API tier:** `POST` on `/addon/edh/admin/instances/{sessionId}/deactivate`.
-   **Object tier:** `POST` on `edhWorkstation` from the VAMS record or the EDH record.

### GET `/addon/edh/admin/capacity`

Returns the EDH desktop profiles and an aggregate of every session on the cluster.

#### Responses

| HTTP  | Body                                                                                                                                                    |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | `{ "profiles": [<EDH profile records as EDH reports them>], "totalSessions": <int>, "byState": { "<edhState>": <int>, ... }, "distinctOwners": <int> }` |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/admin/capacity`.
-   **Object tier:** none (API-level only).

### GET `/addon/edh/admin/health`

Makes one cheap EDH read under the root key.

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `200` | `{ "status": "ok", "edhClusterId": "<edhClusterId>", "latencyMs": <int> }`                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `502` | `{ "status": "error", "reason": "credentialRejected" \| "unreachable" \| "refused", "edhClusterId": "<edhClusterId>", "message": "..." }` — `credentialRejected`: `EDH rejected the deployment credential.` (EDH refused the root key, or its secret could not be read — re-publish the key or restore the secret); `unreachable`: `EDH is unavailable. Try again later.` (no network path, an unexpected answer, or a CA bundle that could not be loaded); `refused`: `EDH refused the health probe.` |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/admin/health`.
-   **Object tier:** none (API-level only).

### GET `/addon/edh/admin/settings`

Returns the stored lease policy together with who last changed it and when. Until an administrator saves the policy, the response is the one seeded from `app.addons.useEdhWorkstations.vdi.lease` at deployment, with `updatedBy` naming the system user that seeded it.

#### Responses

| HTTP  | Body                                                                                                                                                                                                    |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | `{ "lease": { "enabled": true, "leaseHours": 8, "forced": false, "graceHours": 24, "updatedAt": "...", "updatedBy": "..." } }` — both audit fields are `null` only where no settings row exists at all. |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/admin/settings`.
-   **Object tier:** none (API-level only).

### PUT `/addon/edh/admin/settings`

Merges the supplied fields onto the stored lease policy and records the caller as the last to change it. The request body carries any of `enabled`, `leaseHours` (1–720), `forced` and `graceHours` (0–168); an omitted field keeps its stored value, and the merged result is what the ranges are checked against. A policy change does not move an expiry already stamped — it stands until that workstation is renewed — and turning `enabled` off leaves the stamped expiries in place while the sweep stops acting on them. `graceHours` is read at each sweep, so shortening it brings forward the removal of a desktop the sweep has already stopped, and lengthening it extends that desktop's window.

#### Responses

| HTTP  | Body                                                                                                                                              |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | The merged policy under `lease`, with `updatedAt` and `updatedBy` set to this change.                                                             |
| `400` | Validation failed; `{ "message": "leaseHours must be 1-720" }` or `{ "message": "graceHours must be 0-168" }` — the merged value is out of range. |

#### Authorization

-   **API tier:** `PUT` on `/addon/edh/admin/settings`.
-   **Object tier:** none (API-level only).

### GET `/addon/edh/admin/ami-recipes`

Lists every AMI recipe, sorted by `recipeId`: those seeded from the deployment configuration (`source: config`) and those an administrator created (`source: admin`). Each item is the recipe view, which carries the recipe's latest build. The recipe life cycle, the content hash and the build states are described in [EDH Workstations Add-on — AMI recipes](../developer/edh-workstations/overview.md#ami-recipes).

#### Responses

| HTTP  | Body                                |
| ----- | ----------------------------------- |
| `200` | `{ "Items": [<recipe view>, ...] }` |

#### Recipe view

The recipe routes return this object. A field not described as conditional is present on every recipe.

| Field                    | Type            | Description                                                                                                                                                                                                                                                              |
| ------------------------ | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `recipeId`               | string          | Recipe id.                                                                                                                                                                                                                                                               |
| `displayName`            | string          | Display name.                                                                                                                                                                                                                                                            |
| `source`                 | string          | `config` for a recipe seeded from the configuration, `admin` for one created through this API.                                                                                                                                                                           |
| `kind`                   | string          | `vdi` or `simulation`.                                                                                                                                                                                                                                                   |
| `osFamily`               | string          | `linux` or `windows`.                                                                                                                                                                                                                                                    |
| `baseOs`                 | string          | EDH base-OS key; present on `vdi` recipes only.                                                                                                                                                                                                                          |
| `architecture`           | string          | `x86_64` or `arm64`.                                                                                                                                                                                                                                                     |
| `parentImage`            | string          | The parent image reference as supplied.                                                                                                                                                                                                                                  |
| `buildInstanceType`      | string or array | The Amazon EC2 instance type the build runs on, or the list of types EC2 Image Builder chooses among by availability, in the shape the recipe gives it.                                                                                                                  |
| `rootSizeGb`             | integer         | Root volume size in GiB.                                                                                                                                                                                                                                                 |
| `stackName`              | string          | Base name of the EDH software stack each build registers.                                                                                                                                                                                                                |
| `profileId`              | integer         | EDH profile id the stacks are registered against.                                                                                                                                                                                                                        |
| `components`             | array           | The component entries, each `{ "arn" }` or `{ "name", "data" }`.                                                                                                                                                                                                         |
| `parameters`             | object          | The parameter map; present when the recipe declares one.                                                                                                                                                                                                                 |
| `assets`, `assetPrefix`  | array, string   | The repository trees a seeded recipe installs from and the prefix they were uploaded under; present on a seeded recipe that declares assets and on an `admin` recipe duplicated from any recipe that holds them, seeded or itself a duplicate, which keeps the source's. |
| `duplicatedFrom`         | string          | The id of the recipe an `admin` recipe was duplicated from; present on a duplicate.                                                                                                                                                                                      |
| `keepVersions`           | integer         | Number of builds whose AMI is kept: the recipe's value, or `amiBuilds.keepVersionsDefault` when the recipe sets none.                                                                                                                                                    |
| `recipeRevision`         | integer         | Revision number; `1` unless set.                                                                                                                                                                                                                                         |
| `description`            | string          | Present when set.                                                                                                                                                                                                                                                        |
| `enabled`                | boolean         | Whether builds are allowed.                                                                                                                                                                                                                                              |
| `buildOnDeploy`          | boolean         | Whether a deployment starts a build of the recipe, skipped while its newest build carries the same content hash and is `QUEUED`, `BUILDING`, `REGISTERING` or `AVAILABLE`; always `false` on an `admin` recipe.                                                          |
| `contentHash`            | string          | SHA-256 over the fields that shape the AMI, including the deployment values its documents name.                                                                                                                                                                          |
| `latestVersion`          | integer         | The version of the build most recently queued or started; present once a build has been started or queued.                                                                                                                                                               |
| `imageBuilder`           | object          | The EC2 Image Builder resources of the most recent build start — `componentArns`, `recipeArn`, `recipeVersion`, `infrastructureConfigurationArn`, `distributionConfigurationArn`, `pipelineArn` and `buildKey`; present once a build has started.                        |
| `deletingAt`             | string          | The instant a running delete marked the recipe; present while the delete runs. A mark older than 30 minutes, from a delete that did not finish, holds nothing off and is taken over by the next delete.                                                                  |
| `createdBy`, `updatedBy` | string          | VAMS user who created and last changed the recipe; `deploy` on a seeded recipe.                                                                                                                                                                                          |
| `createdAt`, `updatedAt` | string          | ISO 8601 timestamps.                                                                                                                                                                                                                                                     |
| `latestBuild`            | object          | The newest [build row](#build-row), or `null` before the first build.                                                                                                                                                                                                    |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/admin/ami-recipes`.
-   **Object tier:** `GET` on `edhImage` (`edhImageId` = recipe id, `edhOsFamily`), applied per item.

### POST `/addon/edh/admin/ami-recipes`

Creates an AMI recipe with `source: admin`. Creating a recipe starts no build; start one with `POST /addon/edh/admin/ami-recipes/{recipeId}/builds`.

With `duplicateOf`, the request creates a copy of that recipe, shipped or administrator-defined. The copy takes the source's bundled files, `assets` and `assetPrefix`, and records the source's id as `duplicatedFrom`; the body leaves `assets` out or repeats the source's exactly, in any order. The bundled files are the trees the deployment uploaded for the seeded recipe they come from, which are never pruned, so the copy builds with the source's files as they were when it was duplicated. Every other field comes from the body: the web's **Duplicate** action, `vamscli edh admin ami-recipe duplicate` and the MCP tool `duplicate_edh_ami_recipe` send the source's definition, its `parameters` included, with the new `recipeId`, the display name `<displayName> (copy)` and the stack name `<stackName> Copy` unless others are given, and without `enabled`, so the copy starts enabled. The copy carries none of the source's builds, nor its `latestVersion` or `imageBuilder`.

Creating or updating an `admin` recipe refuses a `stackName` another recipe uses, compared with surrounding spaces removed and without regard to case, with `409`, because a build adopts the EDH stack already listed under `<stackName> v<version>`.

Creating, duplicating or updating an `admin` recipe also reads from Amazon EC2 the architectures each `buildInstanceType` supports and, for an `ami-` or `ssm:` `parentImage`, the parent AMI's architecture. A recipe either of which runs another architecture family than `architecture` is refused with `400`; `x86_64` and `i386` are one family. A lookup that fails, a type or parent EC2 reports no known architecture for, a type EC2 does not offer (the other types are still checked), and an `edhStack:` parent refuse nothing, and the build start checks again.

#### Request body

| Field               | Type            | Required  | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------------------- | --------------- | --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `recipeId`          | string          | Yes       | Recipe id: 2–40 lowercase alphanumerics and hyphens, starting with a letter or digit.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `displayName`       | string          | Yes       | Display name shown in listings: 1–256 letters, digits, spaces, hyphens, periods, underscores and parentheses, such as the `<displayName> (copy)` a duplicate starts from.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `kind`              | string          | Yes       | `vdi` (a desktop image) or `simulation` (a target-node image).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `osFamily`          | string          | Yes       | `linux` or `windows`. For `vdi` it must match `baseOs`: `windows` for a `windows*` key, `linux` for any other.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `baseOs`            | string          | For `vdi` | EDH base-OS key: `amazonlinux2`, `amazonlinux2023`, `rhel7`, `rhel8`, `rhel9`, `rocky8`, `rocky9`, `ubuntu2204`, `ubuntu2404`, `windows2019`, `windows2022` or `windows2025`. Required for `vdi` and refused for `simulation`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `architecture`      | string          | Yes       | `x86_64` or `arm64`; `x86_64` only when `osFamily` is `windows`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `parentImage`       | string          | Yes       | An AMI id (`ami-` followed by 8–17 hex digits), a public AMI parameter path (`ssm:/aws/service/...`) read when the build starts, or `edhStack:` and the name of a software stack EDH lists with an AMI; at most 1,300 characters. An `ssm:` path outside `ssm:/aws/service/` is refused.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `buildInstanceType` | string or array | Yes       | Amazon EC2 instance type the EC2 Image Builder build runs on (`family.size`, 3–64 characters), or a list of 1–8 distinct such types that EC2 Image Builder chooses among by availability.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `rootSizeGb`        | integer         | Yes       | Root volume size in GiB, 10–2048.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `stackName`         | string          | Yes       | Base name of the EDH software stack each build registers, as `<stackName> v<version>`; 1–200 characters, VAMS object-name rules apply, and no other recipe may use it, compared with surrounding spaces removed and without regard to case.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `profileId`         | integer         | Yes       | EDH profile id, at least 0: the virtual-desktop profile for `vdi` (typically `vdi.virtualDesktopProfileId`), or the target-node profile for `simulation`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `components`        | array           | Yes       | 1–20 EC2 Image Builder components, run in order. Each entry is either `{ "arn": "<componentArn>" }` — an existing component owned by `aws` or a 12-digit account, where `x` version wildcards are allowed and `${AWS::Partition}` and `${AWS::Region}` are substituted — or `{ "name": "<name>", "data": "<YAML>" }` for an inline document, whose `name` is 1–64 letters, digits, `_` or `-` starting with a letter or digit and whose `data` is at most 16,000 characters, as sent and once its placeholders are resolved, and declares `schemaVersion`. An inline document reaches a `parameters` entry as `${PARAM_<key>}` and the deployment's `sessionBootstrap.secretNamePrefix` as `${DEPLOY_sessionSecretPrefix}`; the documents are resolved when the recipe is saved. |
| `parameters`        | object          | No        | At most 16 string entries, substituted into inline component documents as `${PARAM_<key>}`; keys match `^[A-Za-z][A-Za-z0-9_]{0,63}$` and values are at most 1,024 characters.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `assets`            | array           | No        | Refused with `409` without `duplicateOf`: assets are supported only on recipes seeded from the repository and on recipes duplicated from a recipe that holds them, which take them from the source. A duplicate's body may repeat the source's `assets` exactly.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `keepVersions`      | integer         | No        | Number of builds whose AMI is kept, 1–20; `amiBuilds.keepVersionsDefault` (3 unless configured) when omitted.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `recipeRevision`    | integer         | No        | Revision number, at least 1; `1` when omitted. Part of the content hash: a changed revision gives the next build new versions of its inline EC2 Image Builder components and its image recipe, and on a seeded recipe with `buildOnDeploy` it starts a build on redeploy even when nothing else changed. `POST .../builds` starts a build whatever the hash.                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `description`       | string          | No        | Description, at most 256 characters; carried onto the registered stack.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `enabled`           | boolean         | No        | Whether builds are allowed; defaults to `true`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `duplicateOf`       | string          | No        | The `recipeId` of the recipe to duplicate, in the `recipeId` format. The new recipe takes that recipe's `assets` and `assetPrefix` and records its id as `duplicatedFrom`; `PUT` ignores the field.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `201` | The recipe view.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `400` | Validation failed — among the texts, `baseOs must be one of: <keys>`, `osFamily does not match baseOs`, `baseOs applies to vdi recipes only`, `Windows recipes support x86_64 only`, `parentImage as an SSM parameter path must start with ssm:/aws/service/`, `a component is either {arn} or {name, data}` and `an inline component document must declare schemaVersion`; `buildInstanceType does not match the recipe's architecture` — Amazon EC2 lists a build instance type under another architecture family only; `parentImage does not match the recipe's architecture` — the `ami-` or `ssm:` parent AMI is of another architecture family; or `VAMS General Error: Request body must be a JSON object`. |
| `409` | `recipeId is already in use`; `the recipe to duplicate does not exist` — no recipe has the `duplicateOf` id; `stackName is already used by recipe '<id>'` — recipe `<id>` uses the `stackName`; `assets are only supported on shipped recipes` — the body carries `assets` without `duplicateOf`; `the assets of a duplicated recipe cannot be changed` — the body's `assets` are not the source's; a component document that cannot be materialized — `unresolved placeholder(s) in component document: <placeholders>`, `deployment value(s) not in the expected form for: <placeholders>` or `component <n> is <length> characters once its placeholders are resolved; Image Builder accepts at most 16000`.    |

#### Authorization

-   **API tier:** `POST` on `/addon/edh/admin/ami-recipes`.
-   **Object tier:** `POST` on `edhImage` (`edhImageId` = recipe id from body, `edhOsFamily` from body); with `duplicateOf`, also `GET` on `edhImage` from the source recipe (its recipe id and `osFamily`), checked once the source is read.

### GET `/addon/edh/admin/ami-recipes/{recipeId}`

Reads one AMI recipe.

#### Responses

| HTTP  | Body                                                                            |
| ----- | ------------------------------------------------------------------------------- |
| `200` | The recipe view.                                                                |
| `400` | `recipeId is invalid` — the path id does not match `^[a-z0-9][a-z0-9-]{1,39}$`. |
| `404` | `Recipe not found`.                                                             |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/admin/ami-recipes/{recipeId}`.
-   **Object tier:** `GET` on `edhImage` from the recipe.

### PUT `/addon/edh/admin/ami-recipes/{recipeId}`

Replaces an `admin` recipe with the request body, which has the same shape as `POST`. The `recipeId` in the path replaces any `recipeId` in the body, and an omitted optional field returns to its default. The recipe keeps its `createdBy`, `createdAt`, `latestVersion`, `imageBuilder` and `buildOnDeploy`, and its `contentHash` is recomputed; an update starts no build. A duplicated recipe also keeps its `assets`, `assetPrefix` and `duplicatedFrom`: the body leaves `assets` out or repeats them exactly, and `duplicateOf` is ignored. The recipe may keep its own `stackName`; one another recipe uses answers `409`.

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | The updated recipe view.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `400` | `recipeId is invalid`; validation failed, as for `POST`; `buildInstanceType does not match the recipe's architecture` or `parentImage does not match the recipe's architecture`, as for `POST`; `VAMS General Error: Request body must be a JSON object`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `404` | `Recipe not found`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `409` | `a config recipe is edited in the repository, not here` — the recipe has `source: config`; `assets are only supported on shipped recipes` — the body carries `assets` and the recipe holds none; `the assets of a duplicated recipe cannot be changed` — the body's `assets` are not the ones the recipe holds; `stackName is already used by recipe '<id>'` — recipe `<id>` uses the `stackName`; a component document that cannot be materialized — `unresolved placeholder(s) in component document: <placeholders>`, `deployment value(s) not in the expected form for: <placeholders>` or `component <n> is <length> characters once its placeholders are resolved; Image Builder accepts at most 16000`; `the recipe no longer exists` — the recipe was deleted after the request read it; `the recipe changed while it was saved; try again` — other writes to the recipe kept landing between the request's read and its write. |

#### Authorization

-   **API tier:** `PUT` on `/addon/edh/admin/ami-recipes/{recipeId}`.
-   **Object tier:** `PUT` on `edhImage` from the recipe.

### DELETE `/addon/edh/admin/ami-recipes/{recipeId}`

Deletes an `admin` recipe. The delete first marks the recipe with `deletingAt` and is refused while any of its builds is `QUEUED`, `BUILDING` or `REGISTERING`; a build requested while the delete runs is refused with `the recipe is being deleted`, and a queued build started meanwhile goes back to `QUEUED` with that reason. VAMS deletes, in order, the EC2 Image Builder image pipeline, image recipe, components owned by the account, infrastructure configuration and distribution configuration recorded in the recipe's `imageBuilder`, skipping a resource that is already gone and never deleting an AWS-managed component. It then deletes the recipe row and every build row. The AMIs, snapshots, EC2 Image Builder images and EDH software stacks its builds still hold, and the earlier versions of its image recipe and inline components, stay in place and VAMS keeps no record of them; retire the `AVAILABLE` builds, and the `REGISTER_FAILED` builds whose EDH stack exists, first to remove their AMIs, images and stacks (retire refuses a build whose stack id is unknown). A retire keeps an AMI an Amazon EC2 instance still uses, and the sweep removes it only while the build row exists, so the delete is refused while the sweep still has an AMI to remove.

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | `{ "recipeId", "deleted": true, "warnings": [...] }` — one `warnings` entry, `<method> <arn>: <ErrorType>`, per EC2 Image Builder resource that could not be deleted, such as a component another image still references.                                                                                                                                                                                                                                       |
| `400` | `recipeId is invalid`.                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `404` | `Recipe not found`.                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `409` | `a config recipe is removed from the configuration, not here` — the recipe has `source: config`; `a build is in progress` — a build is `QUEUED`, `BUILDING` or `REGISTERING`; `a build's AMI removal is pending` — a build holds an AMI not yet deregistered and is beyond `keepVersions` with status `RETIRED`, `REGISTER_FAILED` or `FAILED`, or `RETIRED` with `amiRemovalPending`; `the recipe is being deleted` — another delete of the recipe is running. |

#### Authorization

-   **API tier:** `DELETE` on `/addon/edh/admin/ami-recipes/{recipeId}`.
-   **Object tier:** `DELETE` on `edhImage` from the recipe.

### GET `/addon/edh/admin/ami-recipes/{recipeId}/builds`

Lists the builds of one AMI recipe, newest first.

#### Responses

| HTTP  | Body                              |
| ----- | --------------------------------- |
| `200` | `{ "Items": [<build row>, ...] }` |
| `400` | `recipeId is invalid`.            |
| `404` | `Recipe not found`.               |

#### Build row

The build routes return this object. A field appears once the build reaches the stage that sets it; the meaning of each `status` is in [EDH Workstations Add-on — Builds](../developer/edh-workstations/overview.md#builds).

| Field                                                                | Type          | Description                                                                                                                                                                                                                                                                                                                                   |
| -------------------------------------------------------------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `recipeId`                                                           | string        | Recipe id.                                                                                                                                                                                                                                                                                                                                    |
| `version`                                                            | integer       | Build number, counted per recipe from `1`.                                                                                                                                                                                                                                                                                                    |
| `status`                                                             | string        | `QUEUED`, `BUILDING`, `REGISTERING`, `AVAILABLE`, `REGISTER_FAILED`, `FAILED` or `RETIRED`.                                                                                                                                                                                                                                                   |
| `contentHash`                                                        | string        | The recipe's content hash when the build was requested.                                                                                                                                                                                                                                                                                       |
| `requestedBy`                                                        | string        | VAMS user who requested the build, or `deploy` for a build a deployment started.                                                                                                                                                                                                                                                              |
| `requestedAt`                                                        | string        | ISO 8601 timestamp of the request.                                                                                                                                                                                                                                                                                                            |
| `claimedAt`                                                          | string        | Set when a request claims the build's version, before its image pipeline starts; the row written when the pipeline starts drops it. It stays on a build that went back to `QUEUED` or failed with `launch did not complete`.                                                                                                                  |
| `startedAt`, `imageBuildVersionArn`, `resolvedParentAmi`, `buildKey` | string        | Set when the image pipeline starts: the start time, the EC2 Image Builder image build version ARN, the AMI the parent image resolved to, and the key that versions the image recipe.                                                                                                                                                          |
| `completedAt`                                                        | string        | ISO 8601 timestamp at which the image finished or failed.                                                                                                                                                                                                                                                                                     |
| `amiId`, `snapshotIds`                                               | string, array | The built AMI and its snapshots; set when the image reports an AMI.                                                                                                                                                                                                                                                                           |
| `edhStackName`, `edhStackId`, `edhKind`                              | string        | The EDH software stack (`<stackName> v<version>`), its id — absent until it is read back from the catalog — and the recipe kind.                                                                                                                                                                                                              |
| `registeredAt`                                                       | string        | ISO 8601 timestamp of the registration.                                                                                                                                                                                                                                                                                                       |
| `registerAttempts`                                                   | integer       | Failed automatic registration attempts. After five the sweep stops retrying and appends `registration retries exhausted; use Retry registration` to `error`; `POST .../register` resets the count to `0`.                                                                                                                                     |
| `error`                                                              | string        | The failure reason (`launch did not complete` on a build whose claimed launch never recorded a pipeline start), `not started: <reason>` on a `QUEUED` build whose start failed, or a warning on an `AVAILABLE` build, `registered; retirement or pruning of earlier versions failed (<ErrorType>)`; empty on a build that registered cleanly. |
| `retireWarning`                                                      | string        | Why the build could not be deactivated when another build of the recipe became `AVAILABLE`.                                                                                                                                                                                                                                                   |
| `pruneWarning`                                                       | string        | Why its AMI could not be pruned: `pruning failed (<ErrorType>)`, or `in-use check failed (<code>)` when the instances that use it could not be listed, where `<code>` is the AWS error code, or the exception class name for an error that is not an AWS error.                                                                               |
| `retiredAt`                                                          | string        | ISO 8601 timestamp at which the build was retired.                                                                                                                                                                                                                                                                                            |
| `amiDeregisteredAt`                                                  | string        | ISO 8601 timestamp at which its AMI was deregistered.                                                                                                                                                                                                                                                                                         |
| `amiInUseBy`                                                         | array         | Ids of the Amazon EC2 instances, sorted and at most 20, that use the AMI of a build VAMS would otherwise deregister, so the AMI is kept; removed once the AMI is deregistered or its deregistration fails.                                                                                                                                    |
| `amiRemovalPending`                                                  | boolean       | `true` on a build an administrator retired while its AMI awaits removal; removed once the AMI is deregistered.                                                                                                                                                                                                                                |
| `updatedAt`                                                          | string        | ISO 8601 timestamp of the row's last update.                                                                                                                                                                                                                                                                                                  |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/admin/ami-recipes/{recipeId}/builds`.
-   **Object tier:** `GET` on `edhImage` from the recipe.

### POST `/addon/edh/admin/ami-recipes/{recipeId}/builds`

Starts a build of the recipe. A `QUEUED` build with nothing active ahead of it is started first, and the route answers with it (`outcome: started`). A build of the recipe's current content hash that is already `QUEUED`, `BUILDING` or `REGISTERING` is not repeated: the route answers `200` with that build (`outcome: in-progress`) and starts nothing. Any other request takes the next version, which it claims with a conditional write before anything starts; a request that finds its version taken reads the builds again and decides anew. When another build of the recipe is `BUILDING` or `REGISTERING`, the new version is recorded `QUEUED` (`outcome: queued`) and starts when the active build ends or at the next sweep; its parent image and component documents are resolved, and its architectures checked, only then, and a failure there leaves it `QUEUED`, with `not started: <reason>` in its `error`, for the next sweep to try again. Otherwise VAMS resolves the parent image, reads from Amazon EC2 the architecture of the AMI it resolves to and of each build instance type, creates or reuses the EC2 Image Builder resources and starts the image pipeline (`outcome: started`, `status: BUILDING`); a launch that fails before the pipeline starts gives its version back.

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | The build already queued or in progress for the recipe's current content, plus `outcome: in-progress`; nothing is started or queued.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `202` | The build row plus `outcome`: `started` — a new version, or a queued build started ahead of the request — or `queued`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `400` | `recipeId is invalid`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `404` | `Recipe not found`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `409` | `the recipe is disabled`; `the recipe is being deleted`; `the recipe no longer exists` — the recipe was deleted after the request read it; `the recipe's builds changed while the request was placed; try again` — other requests kept taking the version this one claimed; on a build that starts at once, a parent image that cannot be resolved — `SSM parameter <name> could not be read (<code>)` or `EDH lists no <kind> stack named '<name>'`; `the parent image could not be found` — Amazon EC2 does not find the AMI the parent resolves to; `the parent image's architecture does not match the recipe's architecture` or `buildInstanceType does not match the recipe's architecture` — Amazon EC2 reports that AMI, or a build instance type, as running another architecture family than the recipe's `architecture`; or a component document that cannot be materialized — `recipe declares assets but EDH_AMI_BUILD_ARTEFACTS_BUCKET or assetPrefix is not set`, `unresolved placeholder(s) in component document: <placeholders>`, `deployment value(s) not in the expected form for: <placeholders>` or `component <n> is <length> characters once its placeholders are resolved; Image Builder accepts at most 16000`. A refusal creates no EC2 Image Builder resource. A refused new version is not recorded; a queued build the request started goes back to `QUEUED` with `not started: <reason>` in its `error`. |
| `500` | EDH could not be read while resolving an `edhStack:` parent image, or an EC2 Image Builder call failed.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

#### Authorization

-   **API tier:** `POST` on `/addon/edh/admin/ami-recipes/{recipeId}/builds`.
-   **Object tier:** `POST` on `edhImage` from the recipe.

### POST `/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}/register`

Retries EDH registration of a `REGISTER_FAILED` build. The retry resets `registerAttempts` to `0`, re-tags the AMI, registers the software stack `<stackName> v<version>` — or adopts a stack EDH already lists under that name — and attaches it to the project named by `vdi.defaultProject`. A build that becomes `AVAILABLE` then retires every other `AVAILABLE` build of the recipe (a newer one included, when an older build is retried) and prunes, as a completed build does, the AMIs of the builds beyond `keepVersions` and of the retired builds whose removal is pending, unless an instance uses them.

#### Responses

| HTTP  | Body                                                                                                                                                                                   |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | The build row: `status: AVAILABLE` on success, or `status: REGISTER_FAILED` with an `error` when the retry failed.                                                                     |
| `400` | `recipeId is invalid`; `version is invalid` — the path version is not 1–6 digits.                                                                                                      |
| `404` | `Recipe not found`; `Build not found`.                                                                                                                                                 |
| `409` | `only a REGISTER_FAILED build can be re-registered`; `the build's AMI has been deregistered`; `the build status changed` — another retry, a retire or pruning claimed the build first. |

#### Authorization

-   **API tier:** `POST` on `/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}/register`.
-   **Object tier:** `POST` on `edhImage` from the recipe.

### DELETE `/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}`

Retires an `AVAILABLE` or `REGISTER_FAILED` build. VAMS deletes the build's EDH software stack — looking its id up by `edhStackName` when the row carries none — and sets `status: RETIRED` with `retiredAt` and `amiRemovalPending: true`. It then lists the Amazon EC2 instances in the deployment account and Region whose image is the build's AMI and whose state is `pending`, `running`, `stopping` or `stopped`. When there are none, VAMS deregisters the AMI, deletes its snapshots and deletes the EC2 Image Builder image, records `amiDeregisteredAt` and removes `amiRemovalPending`. When an instance uses the AMI, or the instances cannot be listed, the AMI and its snapshots are kept so that existing desktops can still be started: the row carries `amiInUseBy` or `pruneWarning` and no `amiDeregisteredAt`, and the build registrar's 30-minute sweep deregisters the AMI once no instance uses it. A build whose stack id is unknown is refused rather than leaving the stack behind. The build's status is claimed before the stack is deleted, so a build another request has taken since it was read is refused.

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | The `RETIRED` build row, with `amiDeregisteredAt`, or with `amiRemovalPending` and `amiInUseBy` or `pruneWarning` when the AMI is kept.                                                                                                                                                                                                                                                                                                      |
| `400` | `recipeId is invalid`; `version is invalid`.                                                                                                                                                                                                                                                                                                                                                                                                 |
| `404` | `Recipe not found`; `Build not found`.                                                                                                                                                                                                                                                                                                                                                                                                       |
| `409` | `only an AVAILABLE or REGISTER_FAILED build can be retired`; `stack id unknown; cannot deactivate` — neither the row nor the EDH catalog gives the stack id; `the build status changed` — another retire, a registration retry or pruning claimed the build first; `the recipe is being deleted` — a delete of the recipe removed the build row while the retire ran, which leaves the stack deactivated and may leave the AMI deregistered. |
| `500` | EDH refused or failed the stack deletion, which leaves the build's status unchanged; or the AMI could not be deregistered after the stack was deleted, which leaves the build `RETIRED` with `amiRemovalPending` and without `amiDeregisteredAt`, for the next sweep to deregister.                                                                                                                                                          |

#### Authorization

-   **API tier:** `DELETE` on `/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}`.
-   **Object tier:** `DELETE` on `edhImage` from the recipe.

### GET `/addon/edh/containers/catalog`

Lists the AWS Batch job definitions the caller may submit, every job queue the cluster offers and, when `containers.eksEnabled` is `true`, its Amazon EKS clusters. Every item is a curated projection tagged with its `kind`; nothing else of EDH's record is copied.

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `200` | `{ "Items": [{ "kind": "jobDefinition", "jobDefinitionName", "jobDefinitionArn", "revision", "status", "image", "platformCapabilities", "vcpus", "memory", "gpu" }, { "kind": "jobQueue", "jobQueueName", "jobQueueArn", "state", "status" }, { "kind": "eksCluster", "name" }, ...], "eksEnabled": true \| false }` — every active revision of a definition is listed; `memory` (MiB) and `gpu` are integers or `null`; `vcpus` is a number (fractional for AWS Fargate definitions) or `null`. |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/containers/catalog`.
-   **Object tier:** each job-definition item is filtered through `GET` on `edhContainer` (`edhJobDefinition`); queues and clusters are not filtered.

### GET `/addon/edh/containers/jobs`

Lists the caller's container jobs on one backend.

#### Query parameters

| Parameter   | Type   | Required  | Description                                                                                                                |
| ----------- | ------ | --------- | -------------------------------------------------------------------------------------------------------------------------- |
| `backend`   | string | No        | `batch` (the default when omitted) or `eks` (experimental); `eks` is accepted only when `containers.eksEnabled` is `true`. |
| `cluster`   | string | For `eks` | The Amazon EKS cluster whose jobs are listed.                                                                              |
| `namespace` | string | No        | Kubernetes namespace for `eks`; `default` when omitted.                                                                    |

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | `{ "Items": [{ "backend": "batch" \| "eks", "jobId", "jobName", "status", "edhJobDefinition", "jobHandle", ... }, ...] }` — a curated item per job, not EDH's record: `batch` items add `statusReason`, `jobQueueName`, `createdAt`, `startedAt` and `stoppedAt` (AWS Batch epoch milliseconds or `null`); `eks` items add `cluster`, `namespace` and `creationTimestamp`. `jobId` is the value the `DELETE` route takes (the AWS Batch job id, or the Amazon EKS job name); `edhJobDefinition` is the job definition name (`batch`) or the image URI (`eks`). |
| `400` | `backend must be one of: batch, eks`; `The eks backend is not enabled in this deployment`; `cluster is required for the eks backend`; `namespace is invalid`.                                                                                                                                                                                                                                                                                                                                                                                                  |

#### Authorization

-   **API tier:** `GET` on `/addon/edh/containers/jobs`.
-   **Object tier:** each item is filtered through `GET` on `edhContainer` (`edhJobDefinition`).

### POST `/addon/edh/containers/jobs`

Submits a container job to EDH on the backend the body names.

#### Request body

| Field           | Type     | Required | Description                                                                                                                                                                            |
| --------------- | -------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `jobDefinition` | string   | Yes      | Job definition name (`batch`) or image URI (`eks`) from the catalog; 1–256 characters.                                                                                                 |
| `jobQueue`      | string   | Yes      | The AWS Batch job queue, or the Amazon EKS cluster for `eks`.                                                                                                                          |
| `name`          | string   | Yes      | Job name: 1–128 letters, digits, hyphens or underscores, starting with a letter or digit, for `batch`; a lower-case DNS label of at most 63 characters for `eks`.                      |
| `command`       | string[] | No       | Command override: at most 64 entries of at most 1,024 characters each.                                                                                                                 |
| `environment`   | object   | No       | Environment variables (`string` → `string`): at most 64 entries, keys of at most 256 characters and values of at most 1,024. For `batch`, a name starting with `AWS_BATCH` is refused. |
| `vcpus`         | integer  | No       | vCPU override; at least 1.                                                                                                                                                             |
| `memory`        | integer  | No       | Memory override in MiB; at least 512.                                                                                                                                                  |
| `gpu`           | integer  | No       | GPU count override; at least 0. `0` sends no override, so the job definition's own GPU count applies on `batch` and EDH's default on `eks`.                                            |
| `backend`       | string   | Yes      | `batch` or `eks` (experimental); `eks` is accepted only when `containers.eksEnabled` is `true`.                                                                                        |

#### Responses

| HTTP  | Body                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `202` | `{ "jobId", "jobHandle", "backend", "jobDefinition", "jobQueue", "name", "message" }` (plus `cluster` and `namespace` for `eks`) — `jobId` is the AWS Batch job id EDH reports, or the Amazon EKS job name.                                                                                                                                                                                                                                                                        |
| `400` | Validation failed; `jobQueue is required: a Batch job queue, or the EKS cluster for the eks backend`; `memory must be at least 512 MiB`; `name must be 1-128 letters, digits, hyphens or underscores, starting with a letter or digit, for the batch backend`; `environment names cannot start with AWS_BATCH for the batch backend`; `name must be a lower-case DNS label of at most 63 characters for the eks backend`; the backend texts above; or EDH rejected the submission. |
| `429` | EDH refused the job for a quota limit.                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `502` | `{ "message": "EDH did not confirm the request in time; it may still be in progress. Refresh before trying again.", "outcomeUnknown": true }` — EDH did not answer the submission; `{ "message": "EDH accepted the request but returned an unreadable answer. Check the job list before retrying.", "outcomeUnknown": true }` — EDH accepted the job without naming it; in either case the job may have been submitted, so list the jobs before submitting again.                  |

#### Authorization

-   **API tier:** `POST` on `/addon/edh/containers/jobs`.
-   **Object tier:** `POST` on `edhContainer` evaluated against the body's `jobDefinition`, before any EDH call.

### DELETE `/addon/edh/containers/jobs/{jobId}`

Terminates one of the caller's container jobs on the backend the query names. The caller's own job listing decides whether the id is theirs; a job that belongs to someone else reads the same as one that does not exist.

#### Path parameters

| Parameter | Type   | Description                                                                    |
| --------- | ------ | ------------------------------------------------------------------------------ |
| `jobId`   | string | The `jobId` of a listed job: the AWS Batch job id, or the Amazon EKS job name. |

#### Query parameters

| Parameter   | Type   | Required  | Description                                                                                                                |
| ----------- | ------ | --------- | -------------------------------------------------------------------------------------------------------------------------- |
| `backend`   | string | No        | `batch` (the default when omitted) or `eks` (experimental); `eks` is accepted only when `containers.eksEnabled` is `true`. |
| `cluster`   | string | For `eks` | The Amazon EKS cluster the job runs on.                                                                                    |
| `namespace` | string | No        | Kubernetes namespace for `eks`; `default` when omitted.                                                                    |

#### Responses

| HTTP  | Body                                                                                                                                                                              |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `200` | `{ "jobId", "backend", "message" }` (plus `cluster` and `namespace` for `eks`).                                                                                                   |
| `400` | `backend must be one of: batch, eks`; `The eks backend is not enabled in this deployment`; `cluster is required for the eks backend`; `namespace is invalid`; `jobId is invalid`. |
| `404` | `{ "message": "Job not found" }` — the id is not in the caller's own listing.                                                                                                     |

#### Authorization

-   **API tier:** `DELETE` on `/addon/edh/containers/jobs/{jobId}`.
-   **Object tier:** `DELETE` on `edhContainer` from the listed job record's definition.
