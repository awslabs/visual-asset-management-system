---
sidebar_label: CLI Commands
title: EDH Workstations CLI Commands
---

# EDH Workstations CLI Commands

Launch, connect to, stop, start and delete GPU workstations (EDH virtual desktops) from VAMS, browse the image catalog, submit container jobs to the cluster's AWS Batch (or experimental Amazon EKS) backend, and — as an administrator — register images, define and build AMI recipes with EC2 Image Builder, and reclaim cluster capacity. The `edh` command group is present in every CLI build; its commands refuse to run, before any API call, when the deployment has not enabled the part of the add-on they belong to.

:::note[Prerequisites]
The `workstation`, `catalog` and `admin` commands are gated on the `EDH_WORKSTATIONS` feature switch, the `admin ami-recipe` commands on `EDH_AMI_RECIPES`, and the `container` commands on `EDH_CONTAINERS`; the deployment publishes the last two only while the add-on itself is enabled. The switches are read from the deployment at `vamscli auth login` and stored in the profile, so a feature enabled after you logged in becomes visible only after another `vamscli auth login`. Check with `vamscli features check EDH_WORKSTATIONS` (or the switch the command needs).
:::

`SESSION_ID` is the workstation's `sessionId` as `edh workstation list` shows it. A launch that has not received a session id yet (state `CREATING`, or `ORPHANED` after EDH refused it) is addressed by its `bootstrapId` instead; the list marks such rows with a trailing `*`, and every workstation command accepts either value. The `bootstrapId` addresses a launch only until EDH assigns it a session id: from then on it is reported as not found, and the `sessionId` the list shows takes its place. The user commands act only on your own workstations — one owned by another user is reported as not found. The `admin` commands act on every user's sessions and require the EDH admin routes; the `admin instances` commands take either value too, but `admin instances stop` needs a session EDH is running (a launch that has no session id yet is refused with HTTP 409), while `admin instances deactivate` reclaims such a launch by its `bootstrapId`: an `ORPHANED` one at once, a `CREATING` one EDH does not list yet once 15 minutes have passed since its last change (earlier, HTTP 409).

`workstation start`, `stop` and `delete`, `admin images register-vdi`, `register-simulation`, `delete-vdi` and `delete-simulation`, `admin instances stop` and `deactivate`, and `container job submit` and `kill` send a request EDH can carry out without confirming it. When EDH does not confirm it (HTTP 502 with `outcomeUnknown`, for an answer that did not come in time or a job submission EDH accepted without a readable answer, or HTTP 504), the command exits non-zero with `<operation> not confirmed (<status>): <message> Run '<list command>' to see its state.`, naming `edh workstation list`, `edh admin images list`, `edh admin instances list` or `edh container job list`, and `--json-output` prints an error object with `error_type` `EdhOutcomeUnknownError` and `"outcomeUnknown": true`. Run that listing before sending the request again; see [A Request Was Not Confirmed](cli-troubleshooting.md#a-request-was-not-confirmed).

---

## Feature switches

`vamscli features list` shows the add-on's feature switches beside the core switches that [Available feature switches](../../cli/commands/setup-and-auth.md#available-feature-switches) lists, and [Feature flags](overview.md#feature-flags) describes what each one enables:

| Feature            | Description                                                                      |
| ------------------ | -------------------------------------------------------------------------------- |
| `EDH_WORKSTATIONS` | EDH Workstations add-on (GPU Amazon DCV desktops, image catalog, administration) |
| `EDH_CONTAINERS`   | EDH Workstations add-on container jobs (AWS Batch or Amazon EKS through EDH)     |
| `EDH_AMI_RECIPES`  | EDH Workstations add-on AMI recipe builds (EC2 Image Builder)                    |

---

## Quick examples

```bash
vamscli edh catalog images
vamscli edh workstation create --image-id 12 --instance-type g5.2xlarge --name "Design desk" --root-size-gb 200 --os-family linux
vamscli edh workstation list
vamscli edh workstation connect SESSION_ID --json-output
vamscli edh admin ami-recipe build al2023-desktop
vamscli edh container job submit --job-definition render-def --name render-1 --job-queue gpu-queue --backend batch
```

---

## Workstations

### edh workstation list

List your workstations with their state, instance type, OS family and creation time.

```bash
vamscli edh workstation list [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh workstation list
vamscli edh workstation list --json-output | jq '.Items[] | select(.state == "ACTIVE") | .sessionId'
```

Each record carries `bootstrapId`, `sessionId` (null until the launch worker, or else the reconciler's next sweep, reads it back from EDH), `userId` (the VAMS user who launched it), `edhUid` (the EDH identity the desktop runs as), `state` (`CREATING`, `ACTIVE`, `STOPPED`, `DELETED`, `ORPHANED`), `edhState` (EDH's own session state, once reported), `lastError` (why a launch ended `ORPHANED`: `USER_NOT_PROVISIONED`, `QUOTA_EXCEEDED`, `IMAGE_UNAVAILABLE`, `CAPACITY_UNAVAILABLE`, `EDH_REFUSED`, `LAUNCH_ERROR` or `CREATE_TIMED_OUT`; `LEASE_EXPIRED` on a desktop the lease sweep stopped; null otherwise), `launchAttempts`, `name`, `imageId`, `osFamily`, `instanceType`, `rootSizeGb`, `createdAt`, `updatedAt`, `connectionAvailable` (`true` while EDH reports the desktop `running`; `connect` then fetches its connection URL), `leaseExpiresAt` (null when no lease applies), `leaseStoppedAt` (set once the sweep has stopped the desktop at expiry, otherwise null) and `leaseHoursAtLaunch` (the policy hours stamped at launch, null when no lease applied). The list is returned in one page. In CLI mode the table shows the identifier, name, state, last error, instance type, OS family and creation time.

### edh workstation create

Launch a workstation. The launch is accepted asynchronously (HTTP 202) and runs in the background: the returned record is `CREATING` with no `sessionId`; before answering, the API has only read EDH's image catalog to check `--image-id` and `--os-family`, and no desktop has been requested when the command returns. Follow the launch in `edh workstation list`, selecting the row by its `bootstrapId`, until it reports `ACTIVE` and a `sessionId`; `edh workstation get BOOTSTRAP_ID` answers only while the launch has no session id and reports the workstation not found once EDH has assigned one. A launch EDH refuses ends `ORPHANED` with the reason in `lastError` (see [Launch Ended ORPHANED](cli-troubleshooting.md#launch-ended-orphaned)). The command does not wait for the launch to finish. `--os-family` is the chosen image's family as `edh catalog images` lists it (`osFamily`); a value that differs from the catalog's, or an `--image-id` the catalog does not list, is refused with HTTP 400.

```bash
vamscli edh workstation create --image-id <ID> --instance-type <TYPE> --name <NAME> --root-size-gb <GIB> --os-family <linux|windows> [OPTIONS]
```

| Option            | Type    | Required | Description                                                                                                                                                                                                                                                                                |
| ----------------- | ------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--image-id`      | TEXT    | Yes      | Catalog image ID (`edh catalog images`)                                                                                                                                                                                                                                                    |
| `--instance-type` | TEXT    | Yes      | Amazon EC2 instance type; it must run the image's architecture, and a recipe image that lists launch instance types takes one of them (`edh catalog instance-types --image-id`)                                                                                                            |
| `--name`          | TEXT    | Yes      | Workstation name                                                                                                                                                                                                                                                                           |
| `--root-size-gb`  | INTEGER | Yes      | Root volume size in GiB, 10 to 2048, and at least the image's `rootSize` from `edh catalog images`                                                                                                                                                                                         |
| `--os-family`     | CHOICE  | Yes      | `linux` or `windows`: the chosen image's `osFamily` from `edh catalog images`                                                                                                                                                                                                              |
| `--lease-hours`   | INTEGER | No       | Lease in hours, 1 up to the policy's `leaseHours` (`edh catalog lease`). Honoured only while the lease policy is enabled and not forced — omitted, the workstation has no lease; overridden when the policy is forced, and refused with HTTP 400 when leases are off or above `leaseHours` |
| `--bootstrap-id`  | UUID    | No       | Idempotency key (UUID); generated when omitted. Re-send it only when the create response was lost: a repeat returns the existing record and launches nothing                                                                                                                               |
| `--json-output`   | Flag    | No       | Output raw JSON response                                                                                                                                                                                                                                                                   |

```bash
vamscli edh workstation create --image-id 12 --instance-type g5.2xlarge --name "Design desk" --root-size-gb 200 --os-family linux
vamscli edh workstation create --image-id 12 --instance-type g5.2xlarge --name "Design desk" --root-size-gb 200 --os-family windows --json-output
vamscli edh workstation list --json-output | jq '.Items[] | select(.bootstrapId == "<BOOTSTRAP_ID>") | {state, sessionId, lastError}'
```

The response carries the record plus `sessionBootstrap` — `provisioned`, `skipped`, `unavailable` or `disabled`, the outcome of placing a session key for the desktop (always `disabled` for a Windows workstation, which takes no session key; see [POST /addon/edh/workstations](../../api/addon.md#post-addonedhworkstations)). A repeat with the same `--bootstrap-id` by the user who launched it returns the stored record with HTTP 200 and no `sessionBootstrap`, and launches nothing; a `--bootstrap-id` that another user's launch holds is refused with HTTP 409 `bootstrapId is already in use`. An `--instance-type` outside the deployment's allow-list is refused with HTTP 400 `instanceType is not permitted in this deployment`, and one the image's EDH profile lists only under architectures of another family than the image's (`x86_64` and `i386` are one family) with HTTP 400 `instanceType does not match the image's architecture`, and one outside the `launchInstanceTypes` of an image an AMI recipe built with HTTP 400 `instanceType is not offered for this image`. A `--root-size-gb` below the image's `rootSize` is refused with HTTP 400 `Root disk size must be at least <n> GB for this image`.

:::note
Launching is refused (HTTP 403) for a session authenticated with a VAMS API key: a key seeded into a workstation cannot launch further workstations. `edh workstation start` is refused the same way. Log in with your user credentials to launch.
:::

### edh workstation get

Show one of your workstations.

```bash
vamscli edh workstation get SESSION_ID [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh workstation get 3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30
vamscli edh workstation get 3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30 --json-output
```

### edh workstation start

Start one of your stopped workstations. The returned record is `CREATING` (EDH reports the desktop `pending`) until EDH reports it running, when `get` or `list` reports `ACTIVE`; the response also carries `sessionBootstrap`, the outcome of placing a fresh session key, as for `create` (see [the start route](../../api/addon.md#post-addonedhworkstationssessionidstart)). A workstation that is not stopped — already running, or still being created — is refused with HTTP 409 (`Workstation is already running`, `Workstation is not stopped`), as are a launch that has no EDH session yet (`Workstation has no EDH session`), a deleted workstation (`A deleted workstation cannot be started`), one whose lease has expired (`The lease on this workstation has expired; renew it before starting`), one EDH still reports in a state other than `stopped`, such as `stopping` for several minutes after a stop (`The workstation is still <state> in EDH; start it again once it has stopped.`, printed as `Workstation start refused (409): …` and under `--json-output` with `error_type` `EdhOperationError`), and one EDH still reports stopped while another start of it is being processed, less than 3 minutes after a start EDH did not confirm, while the reconciler revokes its session key, or while a lease renewal is still re-issuing that key (`A start of this workstation is already in progress`, printed as `Workstation start already in progress (409)` followed by the listing to run, and under `--json-output` with `error_type` `EdhStartInProgressError` and `"startInProgress": true`); a session authenticated with a VAMS API key is refused with HTTP 403. A start EDH refuses because it found no available AWS capacity for the instance type is refused with HTTP 400 (`EDH found no available AWS capacity for this instance type. Try again later.`, printed as `Workstation start rejected (400): …` and under `--json-output` with `error_type` `InvalidEdhRequestError`); the workstation stays stopped.

```bash
vamscli edh workstation start SESSION_ID [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh workstation start 3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30
```

### edh workstation stop

Stop one of your running workstations. Start it again with `edh workstation start`. A launch that has no EDH session yet is refused with HTTP 409.

```bash
vamscli edh workstation stop SESSION_ID [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh workstation stop 3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30
```

### edh workstation delete

Delete one of your workstations. This is irreversible; `--confirm` is required in both output modes, and without it the command emits a `Confirmation required` error and exits non-zero without deleting anything. A launch that never received a session id is deleted by its `bootstrapId`: an `ORPHANED` launch at once, a `CREATING` one only after it has made no progress for 15 minutes — one that changed more recently is refused with HTTP 409 while the launch may still complete.

```bash
vamscli edh workstation delete SESSION_ID --confirm [OPTIONS]
```

| Option          | Type | Required | Description                             |
| --------------- | ---- | -------- | --------------------------------------- |
| `--confirm`     | Flag | Yes      | Confirm workstation deletion (required) |
| `--json-output` | Flag | No       | Output raw JSON response                |

```bash
vamscli edh workstation delete 3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30 --confirm
vamscli edh workstation delete 3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30 --confirm --json-output
```

### edh workstation connect

Fetch the Amazon DCV connection details for one of your `ACTIVE` workstations. The response carries `sessionId`, `connectionUrl` and `osFamily`. A workstation that is not connectable yet (still `CREATING`, `STOPPED`, or without an EDH session) is refused with HTTP 409.

```bash
vamscli edh workstation connect SESSION_ID [OPTIONS]
```

| Option                    | Type | Required | Description                                                                          |
| ------------------------- | ---- | -------- | ------------------------------------------------------------------------------------ |
| `--show-windows-password` | Flag | No       | Also show the Windows user name and password of a Windows workstation (a credential) |
| `--json-output`           | Flag | No       | Output raw JSON response, including the full connection URL                          |

```bash
vamscli edh workstation connect 3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30
vamscli edh workstation connect 3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30 --json-output | jq -r '.connectionUrl'
vamscli edh workstation connect 3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30 --show-windows-password
```

`connectionUrl` is the Amazon DCV connection URL EDH returns. It points at the EDH cluster's DCV entry point, not at VAMS, so the browser that opens it needs a network path to that entry point (see [Connecting to a workstation](user-guide.md#connecting-to-a-workstation)).

:::warning[The connection URL is a credential]
`connectionUrl` usually carries an Amazon DCV connection token. In EDH's high-scale layout the broker issues a fresh one each time EDH answers a `connect` request, valid for EDH's configured lifetime (12 hours by default), and anyone holding the URL can open the desktop until it expires. Only `connect` and the Windows sign-in make EDH issue a token, at most one per request it answers and only for that desktop, so `--show-windows-password` makes two and `list` and `get` make none; VAMS repeats a read once after a transport failure or a gateway error, which can make a second. In CLI mode the command prints only the DCV host; the full URL is returned only with `--json-output`, which writes it to stdout — it lands in your terminal scrollback and in anything that captures or pipes the output (log files, CI output, agent transcripts). Treat that output like a password. The CLI's own log file never records it.
:::

On a Windows workstation the desktop opens at the Windows lock screen, and CLI mode adds a line that names `--show-windows-password`. With that flag the command also fetches the Windows sign-in and prints the Windows user name and the password EDH generated for the desktop, under a line that marks them as a credential; for a directory account (a user name of the form `DOMAIN\user`) it prints `sign in with your directory password` in place of a password. The password reaches stdout like the connection URL, so the warning above applies to it as well, and the CLI's own log file never records it either. With `--json-output` the sign-in is returned under `windowsSignIn`:

```json
{
    "sessionId": "3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30",
    "connectionUrl": "https://...",
    "osFamily": "windows",
    "windowsSignIn": {
        "username": "<user name>",
        "password": "<password>",
        "directoryAccount": false
    }
}
```

A workstation that is not Windows is refused with HTTP 400 `Workstation is not a Windows workstation`, and a Windows workstation EDH lists no password for yet with HTTP 409 `Windows password is not available yet`; either refusal prints only the error and exits non-zero. For the sign-in at the lock screen, see [Signing in to a Windows workstation](user-guide.md#signing-in-to-a-windows-workstation).

### edh workstation renew

Renew a workstation's lease, resetting it to the administrator's full lease window counted from now. A workstation the lease sweep has already stopped is renewed and then started with `edh workstation start`. The command is refused with HTTP 409 when leases are disabled (`Leases are disabled for this deployment`), when the workstation carries no lease (`This workstation has no lease to renew`), or when it is deleted (`A deleted workstation cannot be renewed`).

```bash
vamscli edh workstation renew SESSION_ID [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh workstation renew 3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30
vamscli edh workstation renew 3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30 --json-output
```

The new expiry follows the policy in force at the renewal rather than the hours the workstation was launched with, so `leaseExpiresAt` on the returned record is `leaseHours` from now as `edh catalog lease` reports it.

For an `ACTIVE` workstation with no start in progress, the renewal also re-issues the desktop's VAMS session key (a Windows workstation takes none and answers `disabled`), and the response carries `sessionBootstrap`, which `--json-output` prints: `provisioned`, `skipped`, `unavailable` or `disabled`, as for `start` (see [the renew route](../../api/addon.md#post-addonedhworkstationssessionidrenew)). The new key lasts `sessionKeyTtlMinutes` from the renewal, and the key it replaces stays valid until its own expiry; with `skipped` or `unavailable` a key the desktop still holds keeps working until its own expiry. A session authenticated with a VAMS API key, such as the workstation's own `vamscli`, renews the lease only, and its response carries no `sessionBootstrap`; to re-key the desktop, renew from the web interface or from a CLI session that did not authenticate with an API key. A Linux desktop built from a shipped recipe (`al2023-desktop` or `isaacsim`) signs in with the new key within about a minute. For other desktops, and for a workstation without a lease, see [Session Key Expired on a Workstation](cli-troubleshooting.md#session-key-expired-on-a-workstation).

---

## Catalog

### edh catalog images

List the desktop images you may launch. The images are filtered by your permissions; an image for which EDH reports neither an OS family nor a base OS is not listed. The images an AMI recipe built come first and the other images follow, each group sorted by name without regard to case and with numbers in numeric order.

```bash
vamscli edh catalog images [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh catalog images
vamscli edh catalog images --json-output
```

CLI mode prints one row per image, in the order the API returns them, under the columns `CATEGORY`, `IMAGE`, `NAME`, `OS`, `ARCH`, `BASE OS`, `ROOT GIB`, `LAUNCH TYPES` and `RECIPE`, or `No images found.` when there are none. `ARCH` is the image's CPU architecture, or `-` when EDH records none. `LAUNCH TYPES` reads `<n> listed` for an image whose recipe lists launch instance types, which `edh catalog instance-types --image-id` names, and `any` for every other image. `CATEGORY` reads `Custom AMI Recipe` for an image an `AVAILABLE` build of an AMI recipe registered and `Baseline Image` for any other, and `RECIPE` names such an image's recipe and build version as `<recipeId> v<version>`, or `-`. Each item of the JSON response carries `imageId`, `name`, `baseOs`, `osFamily` (`linux` or `windows` — the value `edh workstation create --os-family` takes), `architecture` (such as `x86_64` or `arm64`, or `null`), `rootSize`, `description` and `category` (`amiRecipe` or `baseline`), an `amiRecipe` image also `recipeId`, `recipeDisplayName` and `recipeVersion`, and one whose recipe lists them `launchInstanceTypes`, in the recipe's order. While the deployment's AMI builds are off, every image is `baseline`.

### edh catalog instance-types

List the Amazon EC2 instance types the deployment allows for workstations: the types of the EDH virtual desktop profile the deployment names (`vdi.virtualDesktopProfileId`), narrowed to its instance-type allow-list when one is configured, and sorted by name without regard to case and with numbers in numeric order, so `g5.2xlarge` comes before `g5.12xlarge`. A profile EDH does not list is reported with HTTP 502 `EDH virtual desktop profile is unavailable`.

```bash
vamscli edh catalog instance-types [OPTIONS]
```

| Option          | Type | Required | Description                                                                                           |
| --------------- | ---- | -------- | ----------------------------------------------------------------------------------------------------- |
| `--image-id`    | TEXT | No       | Only the instance types that run this catalog image's architecture (see `vamscli edh catalog images`) |
| `--json-output` | Flag | No       | Output raw JSON response                                                                              |

```bash
vamscli edh catalog instance-types
vamscli edh catalog instance-types --image-id <ID>
vamscli edh catalog instance-types --json-output | jq -r '.Items[].instanceType'
```

Each item of the JSON response carries `instanceType` and `architectures`, the architecture groups of the deployment's desktop profile that list the type, in EDH's order, or `[]` when the profile does not group its types by architecture:

```json
{ "Items": [{ "instanceType": "g5.xlarge", "architectures": ["x86_64"] }] }
```

CLI mode prints one row per type, in that order, under the columns `INSTANCE TYPE` and `ARCH`, which joins the type's architectures with commas or reads `-` when it has none, or `No instance types found.` when there are none.

With `--image-id`, the command reads the image catalog and lists only the types that run that image: a type runs an image when the image's `architecture` is `null`, the type's `architectures` is empty, or `architectures` contains the image's `architecture`; and an image that carries `launchInstanceTypes` is offered only the types it lists, as in the web's launch wizard. When the deployment allows instance types but none runs the image, CLI mode prints `No instance type in this deployment runs this image's architecture (<architecture>). Choose another image or ask an administrator.`, or, for an image whose recipe lists launch instance types, `No instance type this deployment allows is offered for this image. Choose another image or ask an administrator.`, and `--json-output` prints `{"Items": []}`; the command exits 0 in both cases. An `--image-id` your image catalog does not list fails (exit 1) with `Image Not Found: Image '<ID>' is not in your image catalog. Use 'vamscli edh catalog images' to see the images you may launch.`

### edh catalog lease

Show the lease policy parameters any workstation user may read before launching.

```bash
vamscli edh catalog lease [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh catalog lease
vamscli edh catalog lease --json-output | jq -r '.lease.leaseHours'
```

The response carries `lease` with `enabled` (whether workstations are time-boxed at all), `leaseHours` (the longest lease `edh workstation create --lease-hours` may ask for, and what a renewal grants), `forced` (whether every launch is given that lease regardless of what it asks for) and `graceHours` (how long a workstation stopped for an expired lease is kept before it is removed). The policy's audit fields are administrative; read them with `edh admin settings get`.

---

## Administration

The `admin` commands require the `/addon/edh/admin` API routes, which the seeded `admin` role holds and the `edh-admin` permission template grants. The CLI needs no other grant. The `admin instances` commands act on EDH sessions — the view the web calls **Sessions**; the CLI and the API keep the `instances` resource name.

### edh admin images list

List every registered image, desktop and simulation alike, as EDH reports them.

```bash
vamscli edh admin images list [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh admin images list
vamscli edh admin images list --json-output
```

Each item carries `kind` (`vdi` or `simulation`), `imageId`, `stackName`, `amiId`, `baseOs`, `osFamily`, `architecture` (the CPU architecture EDH reports for the image, such as `x86_64` or `arm64`, or `null`), `rootSize`, `profileId` and `description`.

### edh admin images register-vdi

Register an AMI as a desktop (VDI) image users can launch.

```bash
vamscli edh admin images register-vdi --ami-id <AMI> --stack-name <NAME> --base-os <OS> --root-size <GIB> --virtual-desktop-profile-id <ID> [OPTIONS]
```

| Option                         | Type    | Required | Description                                                                          |
| ------------------------------ | ------- | -------- | ------------------------------------------------------------------------------------ |
| `--ami-id`                     | TEXT    | Yes      | AMI ID of the desktop image                                                          |
| `--stack-name`                 | TEXT    | Yes      | Software-stack name shown in the catalog                                             |
| `--base-os`                    | TEXT    | Yes      | Lowercase alphanumeric EDH base-OS key of the AMI (`ubuntu2204`, `windows2022`, ...) |
| `--root-size`                  | INTEGER | Yes      | Root volume size in GiB, 1 to 2048                                                   |
| `--virtual-desktop-profile-id` | INTEGER | Yes      | EDH virtual desktop profile the stack launches under                                 |
| `--launch-tenancy`             | TEXT    | No       | Amazon EC2 launch tenancy                                                            |
| `--description`                | TEXT    | No       | Description shown in the catalog                                                     |
| `--json-output`                | Flag    | No       | Output raw JSON response                                                             |

```bash
vamscli edh admin images register-vdi --ami-id ami-0123456789abcdef0 --stack-name ubuntu-22-dcv --base-os ubuntu2204 --root-size 100 --virtual-desktop-profile-id 3
vamscli edh admin images register-vdi --ami-id ami-0123456789abcdef0 --stack-name win-dcv --base-os windows2022 --root-size 150 --virtual-desktop-profile-id 3 --launch-tenancy dedicated --description "Windows desktop" --json-output
```

### edh admin images register-simulation

Register an AMI as a simulation (target node) image.

```bash
vamscli edh admin images register-simulation --ami-id <AMI> --stack-name <NAME> --root-size <GIB> --target-node-profile-id <ID> [OPTIONS]
```

| Option                       | Type    | Required | Description                                              |
| ---------------------------- | ------- | -------- | -------------------------------------------------------- |
| `--ami-id`                   | TEXT    | Yes      | AMI ID of the simulation image                           |
| `--stack-name`               | TEXT    | Yes      | Software-stack name                                      |
| `--root-size`                | INTEGER | Yes      | Root volume size in GiB, 1 to 2048                       |
| `--target-node-profile-id`   | INTEGER | Yes      | EDH target node profile the stack launches under         |
| `--target-node-user-data-id` | INTEGER | No       | EDH target-node user-data template (default: template 1) |
| `--user-data-variables`      | TEXT    | No       | User-data variables passed to the node                   |
| `--description`              | TEXT    | No       | Description                                              |
| `--json-output`              | Flag    | No       | Output raw JSON response                                 |

```bash
vamscli edh admin images register-simulation --ami-id ami-0fedcba9876543210 --stack-name isaac-sim --root-size 200 --target-node-profile-id 7
vamscli edh admin images register-simulation --ami-id ami-0fedcba9876543210 --stack-name isaac-sim --root-size 200 --target-node-profile-id 7 --user-data-variables "MODE=headless" --description "Isaac Sim node"
vamscli edh admin images register-simulation --ami-id ami-0fedcba9876543210 --stack-name isaac-sim --root-size 200 --target-node-profile-id 7 --target-node-user-data-id 3
```

`--target-node-user-data-id` is a positive integer naming the EDH user-data template the node boots with; without it the request omits the template, so the deployment's default template applies.

### edh admin images delete-vdi

Unregister a desktop (VDI) image. Irreversible; `--confirm` is required.

```bash
vamscli edh admin images delete-vdi IMAGE_ID --confirm [OPTIONS]
```

| Option          | Type | Required | Description                       |
| --------------- | ---- | -------- | --------------------------------- |
| `--confirm`     | Flag | Yes      | Confirm image deletion (required) |
| `--json-output` | Flag | No       | Output raw JSON response          |

```bash
vamscli edh admin images delete-vdi 12 --confirm
```

### edh admin images delete-simulation

Unregister a simulation image. Irreversible; `--confirm` is required.

```bash
vamscli edh admin images delete-simulation IMAGE_ID --confirm [OPTIONS]
```

| Option          | Type | Required | Description                       |
| --------------- | ---- | -------- | --------------------------------- |
| `--confirm`     | Flag | Yes      | Confirm image deletion (required) |
| `--json-output` | Flag | No       | Output raw JSON response          |

```bash
vamscli edh admin images delete-simulation 7 --confirm --json-output
```

### AMI recipes and builds

The `edh admin ami-recipe` commands define AMI recipes, build them with EC2 Image Builder, and follow their registration in EDH as software stacks. They require the `EDH_AMI_RECIPES` feature switch. Each recipe carries a `source`: `config` for a recipe seeded from the configuration (the shipped recipe directories or `predefinedImages.amiRecipes[].recipeDir`), which is changed in its recipe directory and deployed again, and `admin` for a recipe created with `ami-recipe create` or `ami-recipe duplicate`, the only kind `update` and `delete` accept. A `config` recipe is changed through VAMS by duplicating it into an `admin` recipe with [`ami-recipe duplicate`](#edh-admin-ami-recipe-duplicate).

Each build takes the recipe's next `version`. When the EC2 Image Builder image completes, VAMS tags the AMI, registers it in EDH as the stack `<stackName> v<version>`, attaches the stack to the launch project, and marks the build `AVAILABLE`. It then retires the recipe's other `AVAILABLE` builds by deactivating their EDH stacks, and prunes AMIs: of the builds that still hold an AMI, the newest `keepVersions` keep it, and each older build that is `RETIRED`, `REGISTER_FAILED`, or `FAILED` has its AMI and snapshots deregistered and its EC2 Image Builder image deleted, unless an Amazon EC2 instance uses the AMI. The sweep prunes every 30 minutes as well. An AMI that any instance in the account and Region uses while `pending`, `running`, `stopping` or `stopped`, a stopped desktop included, is kept with its snapshots: the build records the instance ids in `amiInUseBy`, and the first sweep after the last of those instances is terminated removes the AMI. A recipe that omits `keepVersions` stores the deployment's `app.addons.useEdhWorkstations.amiBuilds.keepVersionsDefault`.

| `status`          | Meaning                                                                                                                                                                                                                                                                                                                                        |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `QUEUED`          | Waiting for the recipe's running build to end; it starts when that build settles, at the next sweep, or at the next `ami-recipe build`, which starts it instead of a new version. A start that fails records `not started: <reason>` in `error` and leaves it `QUEUED`                                                                         |
| `BUILDING`        | EC2 Image Builder is building the image; the build instance is billed while it runs                                                                                                                                                                                                                                                            |
| `REGISTERING`     | The AMI exists and VAMS is tagging it and registering the EDH stack                                                                                                                                                                                                                                                                            |
| `AVAILABLE`       | Registered in EDH; the recipe's current launchable image                                                                                                                                                                                                                                                                                       |
| `REGISTER_FAILED` | The AMI is kept but the EDH registration or project attachment did not complete; `error` gives the reason. The sweep retries it every 30 minutes until five automatic attempts have failed                                                                                                                                                     |
| `FAILED`          | EC2 Image Builder reports the image `FAILED`, `CANCELLED`, `DELETED` or `DEPRECATED`, the image has no AMI in the deployment's Region, the image no longer exists, or the build was still running `buildTimeoutMinutes` after it started, when the sweep also cancels its image creation; `error` gives the reason                             |
| `RETIRED`         | Superseded when another build of the recipe became `AVAILABLE`, retired with `ami-recipe retire`, or pruned; its EDH stack is deactivated where one was known. `amiDeregisteredAt` records when the AMI was removed; until then, on a build that pruning or a retire would otherwise deregister, `amiInUseBy` names the instances that keep it |

The sweep records a build that has stayed `REGISTERING` for 30 minutes as `REGISTER_FAILED` with `registration did not complete`, and fails a `BUILDING` build whose launch never recorded its image pipeline start 30 minutes after the build was claimed, with `launch did not complete`. A `REGISTER_FAILED` build that has used up its automatic retries has `registration retries exhausted; use Retry registration` appended to its `error`, and `ami-recipe register` retries it. A retry the sweep completes has the same effect as `ami-recipe register`: the build becomes `AVAILABLE` and the recipe's other `AVAILABLE` builds are retired, a newer one included, so retire an older `REGISTER_FAILED` build that should not replace the current one with [`ami-recipe retire`](#edh-admin-ami-recipe-retire).

A build whose new stack VAMS could not read back from the EDH catalog is `REGISTER_FAILED` with `registered in EDH but not assigned to project '<project>' (the catalog could not be read back)`, or `(its EDH id could not be read back)` when the catalog did not list the name; it carries no `edhStackId`, and a retry adopts the stack by its name. An `AVAILABLE` build can also carry an `error`, `registered; retirement or pruning of earlier versions failed (...)`, when the follow-on step failed. `retireWarning` and `pruneWarning` on an older build record why that build was not deactivated or pruned; a `pruneWarning` of `in-use check failed (...)` means VAMS could not list the instances that use the AMI and kept it.

A recipe that does not exist fails (exit 1) with `Recipe Not Found: AMI recipe '<RECIPE_ID>' not found` and `Use 'vamscli edh admin ami-recipe list' to see every recipe.`, and a version the recipe has no build of fails `ami-recipe register` and `ami-recipe retire` with `Build Not Found: AMI recipe '<RECIPE_ID>' build v<VERSION> not found` and `Use 'vamscli edh admin ami-recipe builds <RECIPE_ID>' to see its builds.` (HTTP 404); with `--json-output`, `error_type` is `EdhAmiRecipeNotFoundError` or `EdhAmiRecipeBuildNotFoundError`. A refused request answers HTTP 409 as `EDH Error: AMI recipe <operation> refused (409): <message>`, and a recipe file the API does not accept answers HTTP 400 as `AMI recipe <operation> rejected (400): <message>`. The [EDH Workstations developer page](overview.md#ami-recipes) describes the build pipeline and the shipped recipes.

### AMI recipe file

`ami-recipe create` and `ami-recipe update` read a JSON recipe file.

| Field                 | Required | Description                                                                                                                                                                                                                                                                                                                                                                       |
| --------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `recipeId`            | Yes      | 2-40 characters: lowercase letters, digits, and hyphens, starting with a letter or digit. `update` takes the id from the command line                                                                                                                                                                                                                                             |
| `displayName`         | Yes      | Display name, up to 256 characters: letters, digits, spaces, hyphens, periods, underscores, and parentheses                                                                                                                                                                                                                                                                       |
| `kind`                | Yes      | `vdi` (a desktop image) or `simulation` (a simulation node image)                                                                                                                                                                                                                                                                                                                 |
| `osFamily`            | Yes      | `linux` or `windows`                                                                                                                                                                                                                                                                                                                                                              |
| `baseOs`              | vdi only | Required on a `vdi` recipe and refused on a `simulation` recipe. One of `amazonlinux2`, `amazonlinux2023`, `rhel7`, `rhel8`, `rhel9`, `rocky8`, `rocky9`, `ubuntu2204`, `ubuntu2404`, `windows2019`, `windows2022`, `windows2025`; `osFamily` must match it (`windows` for a `windows` key, `linux` otherwise)                                                                    |
| `architecture`        | Yes      | `x86_64` or `arm64`; `x86_64` only for a `windows` recipe                                                                                                                                                                                                                                                                                                                         |
| `parentImage`         | Yes      | The image the build starts from: an AMI id (`ami-...`), a public AMI parameter (`ssm:/aws/service/...`), or the AMI of an EDH stack (`edhStack:<stack name>`)                                                                                                                                                                                                                     |
| `buildInstanceType`   | Yes      | Amazon EC2 instance type the build runs on, or a list of 1-8 distinct types that EC2 Image Builder chooses among by availability                                                                                                                                                                                                                                                  |
| `launchInstanceTypes` | No       | A list of 1-64 distinct instance types a desktop of the recipe's image may launch on, in the form of a `buildInstanceType` entry; the launch catalog offers the image only the deployment's types on the list, and a launch of another type is refused. Absent, `null` or empty offers every type the deployment allows. Outside the content hash, so changing it starts no build |
| `rootSizeGb`          | Yes      | Root volume size of the image and its EDH stack, 10-2048 GiB                                                                                                                                                                                                                                                                                                                      |
| `stackName`           | Yes      | EDH stack name, up to 200 characters: letters, digits, spaces, hyphens, periods, and underscores; each build registers `<stackName> v<version>`, so no other recipe may use the name, compared without surrounding spaces and without regard to case                                                                                                                              |
| `profileId`           | Yes      | EDH virtual desktop profile (`vdi`) or target node profile (`simulation`) the stack uses, 0 or greater                                                                                                                                                                                                                                                                            |
| `components`          | Yes      | 1-20 build steps, run in order (see below)                                                                                                                                                                                                                                                                                                                                        |
| `parameters`          | No       | Up to 16 string values a component document reads as `${PARAM_<key>}`; keys start with a letter and use letters, digits, and underscores (up to 64 characters), values up to 1024 characters. A document also reads the deployment's `sessionBootstrap.secretNamePrefix` as `${DEPLOY_sessionSecretPrefix}`                                                                       |
| `keepVersions`        | No       | Number of AMIs pruning keeps, 1-20                                                                                                                                                                                                                                                                                                                                                |
| `recipeRevision`      | No       | Integer, 1 or greater (default 1)                                                                                                                                                                                                                                                                                                                                                 |
| `description`         | No       | Description, up to 256 characters                                                                                                                                                                                                                                                                                                                                                 |
| `enabled`             | No       | Default `true`; `ami-recipe build` refuses a recipe that is not enabled                                                                                                                                                                                                                                                                                                           |

Each entry in `components` takes one of three forms:

-   `{"arn": "..."}` — an EC2 Image Builder component ARN. `${AWS::Partition}` and `${AWS::Region}` in the ARN are replaced at build time.
-   `{"name": "...", "data": "..."}` — an inline component document. The name is 1-64 letters, digits, underscores, or hyphens, starting with a letter or digit; the document is at most 16,000 characters, as sent and once its placeholders are resolved, and declares `schemaVersion`.
-   `{"file": "path/to/component.yml"}` — read by the CLI and sent as an inline component, named after the file without its `.yml` or `.yaml` extension. The path is resolved relative to the recipe file's directory; a path outside that directory, or a file that does not exist, is refused as a `File Read Error` before any request is sent.

A recipe created through the API cannot declare `assets` (repository directories a build downloads), which only recipes seeded from the configuration and recipes duplicated from a recipe that holds them carry (see [`ami-recipe duplicate`](#edh-admin-ami-recipe-duplicate)); a component that refers to a `${PARAM_...}`, `${ASSET_...}` or `${DEPLOY_...}` value that is not declared, or that is longer than 16,000 characters once its placeholders are resolved, is refused with HTTP 409 when the recipe is created or updated and when a build starts. The following example is modelled on the shipped `al2023-desktop` recipe, less the VAMS CLI, session sign-in and profile cleanup components, which depend on its `vamscli` asset; its component document is at `components/010-baseline.yml` below the recipe file's directory:

```json
{
    "recipeId": "team-desktop",
    "displayName": "Team AL2023 desktop",
    "kind": "vdi",
    "osFamily": "linux",
    "baseOs": "amazonlinux2023",
    "architecture": "x86_64",
    "parentImage": "ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64",
    "buildInstanceType": "m7i.large",
    "rootSizeGb": 60,
    "stackName": "Team AL2023 Desktop",
    "profileId": 1,
    "components": [
        {
            "arn": "arn:${AWS::Partition}:imagebuilder:${AWS::Region}:aws:component/update-linux/x.x.x"
        },
        { "file": "components/010-baseline.yml" }
    ],
    "keepVersions": 3,
    "description": "Amazon Linux 2023 desktop for the team"
}
```

### edh admin ami-recipe list

List every AMI recipe with its latest build.

```bash
vamscli edh admin ami-recipe list [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh admin ami-recipe list
vamscli edh admin ami-recipe list --json-output
```

The table shows each recipe's id, `kind`, `osFamily`, `architecture`, `source`, latest build version and status, `LAUNCH TYPES` — `<n> listed` for a recipe that lists launch instance types, which `ami-recipe get` names, or `any` — and `stackName`. The JSON response returns the recipes under `Items`, each with its stored fields and `latestBuild`, the recipe's newest build row, or `null` before its first build.

### edh admin ami-recipe get

Get one AMI recipe with its latest build.

```bash
vamscli edh admin ami-recipe get RECIPE_ID [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh admin ami-recipe get al2023-desktop
vamscli edh admin ami-recipe get al2023-desktop --json-output
```

Returns the recipe definition (the [recipe file](#ami-recipe-file) fields, with inline components), `source`, `assets` and `assetPrefix` on a recipe that installs bundled repository files, `duplicatedFrom` on a duplicated recipe, `keepVersions`, `contentHash` (the hash of the fields that shape the AMI: `parentImage`, `architecture`, `baseOs`, `rootSizeGb`, `recipeRevision`, the components, any parameters or assets, and the deployment values the components name), `latestVersion`, `buildOnDeploy` (whether a deployment starts a build of a `config` recipe, always `false` on an `admin` recipe; it skips the build while the recipe's latest build is `QUEUED`, `BUILDING`, `REGISTERING` or `AVAILABLE` with that same hash), `createdBy`, `updatedBy`, `createdAt`, `updatedAt`, `imageBuilder` (the EC2 Image Builder component, recipe, configuration, and pipeline ARNs of the last build), and `latestBuild`. The build history is listed by `ami-recipe builds`.

### edh admin ami-recipe create

Create an AMI recipe from a [recipe file](#ami-recipe-file). The recipe is created with `source` `admin` and is not built until `ami-recipe build` runs.

```bash
vamscli edh admin ami-recipe create --file PATH [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--file`        | PATH | Yes      | Path to recipe JSON file |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh admin ami-recipe create --file recipe.json
vamscli edh admin ami-recipe create --file recipe.json --json-output
```

Returns the new recipe with `latestBuild` `null` (HTTP 201). A field the API does not accept is refused with HTTP 400, as is a `buildInstanceType`, or an `ami-` or `ssm:` `parentImage`, that Amazon EC2 reports as running another architecture family than `architecture` (`buildInstanceType does not match the recipe's architecture`, `parentImage does not match the recipe's architecture`; `x86_64` and `i386` are one family), while a lookup that fails refuses nothing; a `recipeId` that is already in use (`recipeId is already in use`), a `stackName` another recipe uses (`stackName is already used by recipe '<id>'`), or a recipe that declares `assets` (`assets are only supported on shipped recipes`) is refused with HTTP 409.

### edh admin ami-recipe update

Replace an `admin` AMI recipe with the contents of a [recipe file](#ami-recipe-file). The whole definition is replaced and validated as on create; the `RECIPE_ID` argument overrides any `recipeId` in the file. The recipe keeps its builds and version numbering, and no build starts.

```bash
vamscli edh admin ami-recipe update RECIPE_ID --file PATH [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--file`        | PATH | Yes      | Path to recipe JSON file |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh admin ami-recipe update team-desktop --file recipe.json
vamscli edh admin ami-recipe update team-desktop --file recipe.json --json-output
```

A duplicated recipe keeps its bundled files and `duplicatedFrom`, so its file leaves `assets` out or repeats them exactly. A file that declares other `assets` is refused with HTTP 409 `the assets of a duplicated recipe cannot be changed`, or `assets are only supported on shipped recipes` for a recipe that holds none, a `stackName` another recipe uses with `stackName is already used by recipe '<id>'`, a recipe whose `source` is `config` with HTTP 409 `a config recipe is edited in the repository, not here`, a recipe deleted after the command read it with `the recipe no longer exists`, and an update that other writes to the recipe kept overtaking with `the recipe changed while it was saved; try again`. A definition whose build instance types or parent image run another architecture family than `architecture` is refused with HTTP 400, as on create.

### edh admin ami-recipe duplicate

Duplicate an AMI recipe, `config` or `admin`, into a new `admin` recipe, which `ami-recipe update` then changes and `ami-recipe build` builds; this is how a `config` recipe is changed without editing its recipe directory. The command reads `SOURCE_ID` and creates the copy from its definition — `kind`, `osFamily`, `baseOs`, `architecture`, `parentImage`, `buildInstanceType`, `launchInstanceTypes`, `rootSizeGb`, `profileId`, `components`, `parameters`, `keepVersions`, `recipeRevision` and `description` — under the new recipe id, with the `--keep-versions` value in place of the source's `keepVersions` when it is given. The service gives the copy the source's bundled files (`assets` and `assetPrefix`), which cannot be changed, and records the source's id as `duplicatedFrom`. The copy is enabled whether or not the source is, and no build starts.

```bash
vamscli edh admin ami-recipe duplicate SOURCE_ID --recipe-id TEXT [OPTIONS]
```

| Option            | Type    | Required | Description                                                                                               |
| ----------------- | ------- | -------- | --------------------------------------------------------------------------------------------------------- |
| `--recipe-id`     | TEXT    | Yes      | Recipe ID of the copy                                                                                     |
| `--display-name`  | TEXT    | No       | Display name of the copy (default: the source's display name plus ` (copy)`)                              |
| `--stack-name`    | TEXT    | No       | EDH stack name of the copy, which no other recipe may use (default: the source's stack name plus ` Copy`) |
| `--keep-versions` | INTEGER | No       | Number of AMIs pruning keeps for the copy, 1-20 (default: the source's `keepVersions`)                    |
| `--json-output`   | Flag    | No       | Output raw JSON response                                                                                  |

```bash
vamscli edh admin ami-recipe duplicate al2023-desktop --recipe-id team-desktop
vamscli edh admin ami-recipe duplicate isaacsim --recipe-id isaacsim-lab --stack-name "Isaac Sim Lab"
vamscli edh admin ami-recipe duplicate al2023-desktop --recipe-id team-desktop --keep-versions 5
vamscli edh admin ami-recipe duplicate al2023-desktop --recipe-id team-desktop --json-output
```

Returns the new recipe with `source` `admin`, `duplicatedFrom` and `latestBuild` `null` (HTTP 201). A `--keep-versions` value outside 1-20 is refused at the command line (exit 2) before any request is made. A source that does not exist fails with `Recipe Not Found: AMI recipe '<SOURCE_ID>' not found`, and nothing is created. The create is refused with HTTP 409, printed as `AMI recipe creation refused (409): <message>`, when the new recipe id is in use (`recipeId is already in use`), when another recipe uses the stack name (`stackName is already used by recipe '<id>'`, which a second copy of one recipe meets under the default stack name), or when the source is deleted between the read and the create (`the recipe to duplicate does not exist`). A default name longer than its field allows is refused with HTTP 400; pass `--display-name` or `--stack-name`. A source whose build instance types or parent image run another architecture family than its `architecture` is refused with HTTP 400, as on create. A role without the `GET` and `POST` AMI recipe routes, or without `GET` on the source recipe or `POST` on the new one, is refused with HTTP 403.

### edh admin ami-recipe delete

Delete an `admin` AMI recipe and every one of its build rows. Irreversible; `--confirm` is required.

```bash
vamscli edh admin ami-recipe delete RECIPE_ID --confirm [OPTIONS]
```

| Option          | Type | Required | Description                        |
| --------------- | ---- | -------- | ---------------------------------- |
| `--confirm`     | Flag | Yes      | Confirm recipe deletion (required) |
| `--json-output` | Flag | No       | Output raw JSON response           |

```bash
vamscli edh admin ami-recipe delete team-desktop --confirm
vamscli edh admin ami-recipe delete team-desktop --confirm --json-output
```

The delete removes the EC2 Image Builder pipeline, recipe, and configurations recorded by the recipe's last build, and the components it recorded in the deployment's own account (the inline components and any `{"arn"}` component of that account), then the recipe and its build rows, and returns `{"recipeId": ..., "deleted": true, "warnings": [...]}`. AWS-managed components are kept, and a resource that could not be removed, such as a component another image still references, is named in `warnings`. The AMIs, snapshots, EC2 Image Builder images, and EDH stacks the recipe's builds still hold, and the earlier versions of its image recipe and inline components, are left in place, and without the build rows VAMS cannot retire or prune them; a build whose AMI `ami-recipe retire` or pruning deregistered holds no AMI, snapshot, or image, but a retire keeps an AMI an Amazon EC2 instance still uses. Before deleting the recipe, retire the `AVAILABLE` builds, and the `REGISTER_FAILED` builds whose EDH stack exists, to remove their AMIs, images, and stacks; [`ami-recipe retire`](#edh-admin-ami-recipe-retire) refuses a build whose stack id cannot be resolved. A refused delete is HTTP 409, printed as `AMI recipe deletion refused (409): <message>`: a recipe whose `source` is `config` with `a config recipe is removed from the configuration, not here`, a recipe with a `QUEUED`, `BUILDING` or `REGISTERING` build with `a build is in progress`, a recipe another delete is already removing with `the recipe is being deleted`, and a recipe with a build whose AMI removal is pending with `a build's AMI removal is pending`. A build's AMI removal is pending while a `RETIRED` build carries `amiRemovalPending`, or a `RETIRED`, `REGISTER_FAILED` or `FAILED` build outside the recipe's `keepVersions` newest AMIs still holds its AMI, as while an Amazon EC2 instance uses the AMI; the build row is the sweep's only record of that AMI. Delete the recipe once the sweep has deregistered those AMIs and each such build shows `amiDeregisteredAt`.

### edh admin ami-recipe build

Start an EC2 Image Builder build of an AMI recipe.

```bash
vamscli edh admin ami-recipe build RECIPE_ID [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh admin ami-recipe build al2023-desktop
vamscli edh admin ami-recipe build al2023-desktop --json-output
```

Returns the build row with an `outcome`. A new build answers HTTP 202 and the CLI prints `Build v<version> <outcome>`: the outcome is `started` with status `BUILDING`, or `queued` with status `QUEUED` when the recipe already has a `BUILDING` or `REGISTERING` build of other content. A `QUEUED` build with nothing ahead of it is started instead of a new version and reported `started`. When the recipe's current content is already `QUEUED`, `BUILDING` or `REGISTERING`, nothing is started: the command answers HTTP 200 with that build and the outcome `in-progress`, prints `Build already in progress: <recipeId> v<version>`, and exits 0. Registration, retirement of the recipe's other `AVAILABLE` builds, and pruning follow automatically, as described in [AMI recipes and builds](#ami-recipes-and-builds).

A recipe that is not enabled is refused with HTTP 409 `the recipe is disabled`, a recipe being deleted with `the recipe is being deleted`, one deleted after the command read it with `the recipe no longer exists`, and a request whose version other requests kept taking with `the recipe's builds changed while the request was placed; try again`. A build that starts at once is also refused with HTTP 409, and nothing is started, when its `parentImage` cannot be resolved (an unreadable parameter, or no EDH stack of that name), Amazon EC2 does not find the AMI it resolves to (`the parent image could not be found`), Amazon EC2 reports that AMI or a build instance type as running another architecture family than the recipe's `architecture` (`the parent image's architecture does not match the recipe's architecture`, `buildInstanceType does not match the recipe's architecture`), or a component document cannot be resolved (`unresolved placeholder(s) in component document: ...`, `deployment value(s) not in the expected form for: ...`, or `component <n> is <length> characters once its placeholders are resolved; Image Builder accepts at most 16000`). A queued build is checked only when it is promoted, by the end of the build ahead of it, by the sweep, or by an `ami-recipe build` that finds nothing active: a check that fails then leaves it `QUEUED` with `not started: <reason>` in its `error`, and the sweep logs a warning and tries the promotion again every 30 minutes with the recipe's current definition.

### edh admin ami-recipe builds

List an AMI recipe's builds, newest first.

```bash
vamscli edh admin ami-recipe builds RECIPE_ID [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh admin ami-recipe builds al2023-desktop
vamscli edh admin ami-recipe builds al2023-desktop --json-output
```

The table shows each build's version, `status`, `amiId`, `edhStackName`, `requestedAt`, and `error`. The JSON response returns the builds under `Items`, each with `recipeId`, `version`, `status`, `contentHash`, `requestedBy`, `requestedAt`, and, as the build progresses, `startedAt`, `resolvedParentAmi`, `imageBuildVersionArn`, `buildKey` (the hash of the recipe content and the resolved parent AMI that versions the EC2 Image Builder recipe), `amiId`, `snapshotIds`, `completedAt`, `edhStackName`, `edhStackId`, `edhKind`, `registeredAt`, `registerAttempts`, `error`, `retireWarning`, `pruneWarning`, `retiredAt`, `amiDeregisteredAt`, `amiInUseBy` (the ids, at most 20, of the Amazon EC2 instances that the last successful in-use check found using the AMI of a build outside `keepVersions` or of a retired build), `amiRemovalPending` (`true` on a build an administrator retired whose AMI has not been deregistered yet), and `updatedAt`.

### edh admin ami-recipe register

Retry the EDH registration of a `REGISTER_FAILED` build. A build registers automatically when its image completes, and the sweep retries a failed registration until five automatic attempts have failed; this command retries it on request and resets `registerAttempts`.

```bash
vamscli edh admin ami-recipe register RECIPE_ID VERSION [OPTIONS]
```

| Option          | Type    | Required | Description              |
| --------------- | ------- | -------- | ------------------------ |
| VERSION         | INTEGER | Yes      | Build version number     |
| `--json-output` | Flag    | No       | Output raw JSON response |

```bash
vamscli edh admin ami-recipe register al2023-desktop 4
vamscli edh admin ami-recipe register al2023-desktop 4 --json-output
```

The retry tags the AMI again and registers the stack, adopting an EDH stack that already carries the build's name. It returns the build row: `AVAILABLE` when registration completes, which also retires the recipe's other `AVAILABLE` builds (a newer one included) and prunes AMIs, or `REGISTER_FAILED` with the reason in `error`. Both answer HTTP 200 and the CLI prints `Build registered in EDH` for either, so read `status` in the returned row (for example `--json-output | jq -r '.status'`). It is refused with HTTP 409 `only a REGISTER_FAILED build can be re-registered`, `the build's AMI has been deregistered`, or `the build status changed` when another retry, a retire or pruning claimed the build first.

### edh admin ami-recipe retire

Retire an `AVAILABLE` or `REGISTER_FAILED` build: deactivates its EDH stack, then deregisters the AMI and its snapshots and deletes the EC2 Image Builder image once no Amazon EC2 instance uses the AMI. While an instance uses it, the build stays `RETIRED` with `amiRemovalPending` and `amiInUseBy` (the instance ids), and VAMS removes the AMI after the last instance is terminated, so existing desktops can still be started. Irreversible; `--confirm` is required.

```bash
vamscli edh admin ami-recipe retire RECIPE_ID VERSION --confirm [OPTIONS]
```

| Option          | Type    | Required | Description                         |
| --------------- | ------- | -------- | ----------------------------------- |
| VERSION         | INTEGER | Yes      | Build version number                |
| `--confirm`     | Flag    | Yes      | Confirm build retirement (required) |
| `--json-output` | Flag    | No       | Output raw JSON response            |

```bash
vamscli edh admin ami-recipe retire al2023-desktop 1 --confirm
vamscli edh admin ami-recipe retire al2023-desktop 1 --confirm --json-output
```

Returns the build row with status `RETIRED` and `retiredAt`, and `amiDeregisteredAt` once the AMI is deregistered. A build whose AMI is kept carries `amiRemovalPending: true` and `amiInUseBy`, or a `pruneWarning` of `in-use check failed (...)` when VAMS could not list the instances, and the sweep removes the AMI within 30 minutes of the last instance's termination. Earlier builds are retired and pruned automatically, so the command is mainly for the recipe's current build; retiring the `AVAILABLE` build leaves the recipe with no launchable image until the next build registers. A build in another status is refused with HTTP 409 `only an AVAILABLE or REGISTER_FAILED build can be retired`, a build whose EDH stack cannot be found with `stack id unknown; cannot deactivate`, a build another retire, a registration retry or pruning claimed first with `the build status changed`, and a build whose recipe is deleted while the retire runs with `the recipe is being deleted`, which leaves the stack deactivated and may leave the AMI deregistered. A deactivation EDH refuses answers HTTP 500 and leaves the build as it was; an AMI that cannot be deregistered after the stack was deleted answers HTTP 500 and leaves the build `RETIRED` with `amiRemovalPending` and without `amiDeregisteredAt`, for the next sweep to remove. Pruning removes the AMI of a `REGISTER_FAILED` build whose stack cannot be found once it falls outside `keepVersions` and no instance uses it. An `AVAILABLE` build whose stack EDH does not list under the build's name is neither retired nor pruned by VAMS, so its AMI and snapshots stay until they are removed outside VAMS.

### edh admin instances list

List every user's workstation sessions on the cluster, as EDH reports them.

```bash
vamscli edh admin instances list [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh admin instances list
```

Each item carries `sessionId`, `edhState`, `owner`, `name`, `createdAt`, `updatedAt`, `userId`, `bootstrapId`, `state`, `lastError`, `imageId`, `osFamily` and `instanceType`; `userId`, `bootstrapId`, `updatedAt` and `lastError` are `null` for a session VAMS did not launch.

### edh admin instances stale

List the sessions whose state is `STOPPED` or `ORPHANED` and whose record last changed at least the threshold ago (`staleReason: "stopped"`), plus sessions VAMS knows but EDH no longer lists (`staleReason: "missingInEdh"`) — the candidates for `stop` and `deactivate`. Every item carries `stale: true` and a `staleReason` of `stopped` or `missingInEdh`; the response also echoes the `idleMinutes` threshold that was applied.

```bash
vamscli edh admin instances stale [OPTIONS]
```

| Option           | Type    | Required | Description                                 |
| ---------------- | ------- | -------- | ------------------------------------------- |
| `--idle-minutes` | INTEGER | No       | Idle threshold in minutes; 120 when omitted |
| `--json-output`  | Flag    | No       | Output raw JSON response                    |

```bash
vamscli edh admin instances stale
vamscli edh admin instances stale --idle-minutes 45
vamscli edh admin instances stale --json-output
```

### edh admin instances stop

Stop any user's workstation session. The owner can start it again with `edh workstation start`. With session bootstrap enabled, the session's VAMS API key is revoked at once. A session EDH no longer lists is refused with HTTP 409; `deactivate` reclaims its record.

```bash
vamscli edh admin instances stop SESSION_ID [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh admin instances stop 3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30
```

### edh admin instances deactivate

Force-deactivate any user's workstation session. Irreversible; `--confirm` is required. EDH deletes the desktop and its AWS CloudFormation stack; the session's VAMS API key, if any, is revoked at once. A session EDH no longer lists is marked `DELETED` without an EDH call. A launch that never received a session id is addressed by its `bootstrapId`; a `CREATING` launch EDH does not list whose record changed less than 15 minutes ago is refused with HTTP 409.

```bash
vamscli edh admin instances deactivate SESSION_ID --confirm [OPTIONS]
```

| Option          | Type | Required | Description                     |
| --------------- | ---- | -------- | ------------------------------- |
| `--confirm`     | Flag | Yes      | Confirm deactivation (required) |
| `--json-output` | Flag | No       | Output raw JSON response        |

```bash
vamscli edh admin instances deactivate 3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30 --confirm
```

### edh admin capacity

Show the cluster's workstation capacity summary as EDH reports it.

```bash
vamscli edh admin capacity [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh admin capacity
```

The response carries `profiles` (the EDH desktop profiles as EDH reports them), `totalSessions`, `byState` (a count per EDH state) and `distinctOwners`.

### edh admin health

Show whether VAMS can reach the EDH API with its configured credentials. Run this first when other `edh` commands report that the cluster did not answer; a command that reports its request `not confirmed` calls for the listing it names instead. A healthy probe returns `status: ok`, `edhClusterId` and `latencyMs`. A failed probe is reported as an `EDH Error` that names the reason: `credentialRejected` (EDH rejected the deployment credential — for example after a key rotation — or its secret could not be read), `unreachable` (no network path, a certificate that does not verify, or a CA bundle that could not be loaded) or `refused`.

```bash
vamscli edh admin health [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh admin health --json-output
```

### edh admin settings get

Show the current lease policy, including who last changed it and when. Users who only need to know what a launch may ask for read `edh catalog lease` instead, which needs no administrative route.

```bash
vamscli edh admin settings get [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh admin settings get
vamscli edh admin settings get --json-output
```

The response carries `lease` with `enabled`, `leaseHours`, `forced` and `graceHours`, plus `updatedAt` and `updatedBy`. A policy no administrator has saved names the system user in `updatedBy` and the deployment time in `updatedAt`.

### edh admin settings set

Update the lease policy. Only the flags you pass are changed; the rest keep their stored values, and the merged result is what the ranges are checked against. A policy change does not move an expiry already stamped: it stands until that workstation is renewed. `graceHours` is read at each sweep, so shortening it brings forward the removal of a desktop the sweep has already stopped, and lengthening it extends that desktop's window.

```bash
vamscli edh admin settings set [OPTIONS]
```

| Option                     | Type    | Required | Description                                                    |
| -------------------------- | ------- | -------- | -------------------------------------------------------------- |
| `--enabled` / `--disabled` | Flag    | No       | Turn leases on or off                                          |
| `--lease-hours`            | INTEGER | No       | Lease length, 1 to 720 hours                                   |
| `--forced` / `--no-forced` | Flag    | No       | Force a lease on every launch, or leave the choice to the user |
| `--grace-hours`            | INTEGER | No       | Stop-to-terminate grace, 0 to 168 hours                        |
| `--json-output`            | Flag    | No       | Output raw JSON response                                       |

```bash
vamscli edh admin settings set --enabled --lease-hours 24
vamscli edh admin settings set --grace-hours 6
vamscli edh admin settings set --disabled
vamscli edh admin settings set --forced --lease-hours 12 --json-output
```

A call that passes none of the four policy flags is a usage error and writes nothing, so an empty patch cannot silently rewrite `updatedAt`. A merged `leaseHours` or `graceHours` outside its range is refused with HTTP 400.

---

## Containers

The `container` commands are always present but refuse to run unless the deployment also enables the add-on's container jobs (`EDH_CONTAINERS`). Jobs run on AWS Batch — or, where the deployment enables it, on Amazon EKS (experimental) — through EDH; VAMS holds no Batch or EKS permissions of its own. A job is addressed by the `jobId` that `edh container job list` shows: a Batch job id, or an EKS job name inside one cluster and namespace.

### edh container catalog

Show the job definitions your `edhContainer` constraints allow, the job queues registered for the cluster and, when the deployment enables the eks backend, its EKS clusters, each in a curated shape rather than EDH's record. Each item carries a `kind`: a `jobDefinition` item carries `jobDefinitionName`, `jobDefinitionArn`, `revision`, `status`, `image`, `platformCapabilities`, `vcpus`, `memory` and `gpu`; a `jobQueue` item `jobQueueName`, `jobQueueArn`, `state` and `status`; an `eksCluster` item its `name`. The body's `eksEnabled` flag says whether `--backend eks` is accepted. `jobDefinitionName` is what `job submit --job-definition` takes and what the `edhContainer` permission constraint evaluates.

```bash
vamscli edh container catalog [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli edh container catalog --json-output
```

### edh container job submit

Submit a container job. EDH accepts the job and the command returns at once (HTTP 202) with the `jobId` that `job list` shows and `job kill` takes, plus `jobHandle`, `backend`, `jobDefinition`, `jobQueue` and `name`.

```bash
vamscli edh container job submit --job-definition <NAME> --name <NAME> --job-queue <NAME> --backend <batch|eks> [OPTIONS]
```

| Option             | Type    | Required | Description                                                                                                                                                      |
| ------------------ | ------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--job-definition` | TEXT    | Yes      | Job definition name from `edh container catalog` (the container image URI for `eks`)                                                                             |
| `--name`           | TEXT    | Yes      | Job name: 1–128 letters, digits, hyphens or underscores, starting with a letter or digit, for `batch`; a lower-case DNS label of at most 63 characters for `eks` |
| `--job-queue`      | TEXT    | Yes      | Batch job queue from `edh container catalog` (the EKS cluster name for `eks`)                                                                                    |
| `--backend`        | CHOICE  | Yes      | `batch`, or `eks` (experimental; accepted only when the catalog reports `eksEnabled: true`)                                                                      |
| `--command`        | TEXT    | No       | Container command token; repeat once per token                                                                                                                   |
| `--env`            | TEXT    | No       | Environment variable as `KEY=VALUE`; repeatable. With `--backend batch`, a name starting with `AWS_BATCH` is refused                                             |
| `--vcpus`          | INTEGER | No       | vCPUs requested; at least 1                                                                                                                                      |
| `--memory`         | INTEGER | No       | Memory requested in MiB; at least 512                                                                                                                            |
| `--gpu`            | INTEGER | No       | GPUs requested; 0 or more                                                                                                                                        |
| `--json-output`    | Flag    | No       | Output raw JSON response                                                                                                                                         |

```bash
vamscli edh container job submit --job-definition render-def --name render-1 --job-queue gpu-queue --backend batch
vamscli edh container job submit --job-definition render-def --name render-1 --job-queue gpu-queue --backend batch --command python --command main.py --env MODE=headless --vcpus 4 --memory 16384 --gpu 1 --json-output
```

`--vcpus` below 1, `--memory` below 512 and a negative `--gpu` are refused at the command line (exit 2) before any request is made. `--env` splits on the first `=`, so a value may itself contain `=`. With `--backend eks`, `--job-definition` is the container image URI, `--job-queue` the EKS cluster name, and the job runs in the cluster's `default` namespace. Submitting a job starts compute on the cluster and incurs cost until it finishes or is killed.

### edh container job list

List your container jobs on one backend, as EDH reports them. Each item carries `jobId` (the value `job kill` takes), `jobName`, `status`, `backend`, `edhJobDefinition` and `jobHandle` beside EDH's own fields; EKS items add `cluster` and `namespace`. Another user's jobs are never included, and a job on the other backend is not listed — query each backend to see every job.

```bash
vamscli edh container job list [OPTIONS]
```

| Option          | Type   | Required | Description                                                                  |
| --------------- | ------ | -------- | ---------------------------------------------------------------------------- |
| `--backend`     | CHOICE | No       | `batch` when omitted, or `eks` (experimental)                                |
| `--cluster`     | TEXT   | No       | EKS cluster name; required with `--backend eks`, refused otherwise           |
| `--namespace`   | TEXT   | No       | Kubernetes namespace within the cluster (`eks` only; `default` when omitted) |
| `--json-output` | Flag   | No       | Output raw JSON response                                                     |

```bash
vamscli edh container job list
vamscli edh container job list --backend eks --cluster sim-cluster --json-output
```

### edh container job kill

Kill one of your jobs. Irreversible; `--confirm` is required. `JOB_ID` is the job's `jobId` from `edh container job list` — a Batch job id, or an EKS job name inside the cluster and namespace named by `--cluster` and `--namespace`. The job is looked up in your own listing on the named backend, so another user's job, or a Batch job id sent with `--backend eks`, is reported as not found. The confirmation echoes `jobId` and `backend` (plus `cluster` and `namespace` for `eks`).

```bash
vamscli edh container job kill JOB_ID --confirm [OPTIONS]
```

| Option          | Type   | Required | Description                                                                  |
| --------------- | ------ | -------- | ---------------------------------------------------------------------------- |
| `--backend`     | CHOICE | No       | `batch` when omitted, or `eks` (experimental)                                |
| `--cluster`     | TEXT   | No       | EKS cluster name; required with `--backend eks`, refused otherwise           |
| `--namespace`   | TEXT   | No       | Kubernetes namespace within the cluster (`eks` only; `default` when omitted) |
| `--confirm`     | Flag   | Yes      | Confirm killing the job (required)                                           |
| `--json-output` | Flag   | No       | Output raw JSON response                                                     |

```bash
vamscli edh container job kill 8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f --confirm
vamscli edh container job kill render-1 --backend eks --cluster sim-cluster --confirm --json-output
```

---

## Related pages

-   [Setup and Authentication](../../cli/commands/setup-and-auth.md) — feature switches and `vamscli features check`
-   [Permissions](../../cli/commands/permissions.md) — role constraints and template import
-   [EDH Workstations CLI Troubleshooting](cli-troubleshooting.md)
