---
sidebar_label: CLI Troubleshooting
title: EDH Workstations CLI Troubleshooting
---

# EDH Workstations CLI Troubleshooting

This page covers common problems encountered with the VamsCLI `edh` commands, along with their causes and resolutions.

---

## JSON Error Fields

With `--json-output`, an `edh` command error can carry these fields beside the ones the [JSON Error Format](../../cli/automation.md#json-error-format) lists:

| Field             | Carried by                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `outcomeUnknown`  | `EdhOutcomeUnknownError` (`true`): EDH may have carried out a request whose answer was lost; check the listing before sending it again (see [A Request Was Not Confirmed](#a-request-was-not-confirmed))                                                                                                                                                                                                                       |
| `startInProgress` | `EdhStartInProgressError` (`true`): another start of the workstation is in progress (see [Start Refused Because a Start Is in Progress](#start-refused-because-a-start-is-in-progress))                                                                                                                                                                                                                                        |
| `code`            | `EdhReservationFullError` (`RESERVATION_FULL`): the capacity reservation of a launch has no free slot (see [Launch Refused Because the Capacity Reservation Is Full](#launch-refused-because-the-capacity-reservation-is-full)); a start's full reservation is recorded on the workstation instead (see [Start Left the Workstation Stopped](#start-left-the-workstation-stopped))                                             |
| `waitOutcome`     | `EdhStartFailedError` (`failed`), `EdhStartStalledError` (`stalled`) and `EdhStartWaitTimeoutError` (`timedOut`), raised by `edh workstation start --wait` alone, beside the record's `state`, `activity`, `lastError` and `startFailure` (see [Start Left the Workstation Stopped](#start-left-the-workstation-stopped), [Start Stalled](#start-stalled) and [Waiting for a Start Timed Out](#waiting-for-a-start-timed-out)) |

---

## Add-on Availability

### Add-on Not Enabled

**Symptoms:**

-   Every `edh workstation`, `edh catalog` and `edh admin` command other than `edh admin ami-recipe` fails with `The EDH Workstations add-on is not enabled for this deployment` before any request is made

**Cause:**

These commands are gated on the `EDH_WORKSTATIONS` feature switch; `edh container` and `edh admin ami-recipe` have switches of their own (see [Container Commands Not Enabled](#container-commands-not-enabled) and [AMI Recipe Commands Not Enabled](#ami-recipe-commands-not-enabled)). The switch list is read from `/secure-config` at `vamscli auth login` and stored in the profile, so it is a snapshot of the deployment at login time.

**Resolution:**

1. Check the switches the profile holds:

    ```bash
    vamscli features list
    vamscli features check EDH_WORKSTATIONS
    ```

2. If the add-on was enabled after you logged in, log in again to refresh the snapshot:

    ```bash
    vamscli auth login -u you@example.com
    ```

3. If the switch is still absent, the deployment does not enable the add-on (`app.addons.useEdhWorkstations.enabled`); contact your VAMS administrator.

---

## Workstation Lookups

### Workstation Not Found

**Symptoms:**

-   `edh workstation get`, `start`, `stop`, `delete` or `connect` fails with `Workstation Not Found` for a session ID that appears in the web UI

**Cause:**

The user commands act only on workstations you own. A session that belongs to another user is reported as not found rather than forbidden. A launch that has not received a session id yet has no `sessionId` to look up — address it by its `bootstrapId`, which `edh workstation list` shows with a trailing `*`. A `bootstrapId` addresses the launch only until EDH assigns it a session id, so a `bootstrapId` that worked while the launch was `CREATING` and now answers not found means the launch has a `sessionId`; use that value from the list.

**Resolution:**

1. List your own workstations and use the identifier column:

    ```bash
    vamscli edh workstation list
    ```

2. An administrator can act on any session with `vamscli edh admin instances stop` or `deactivate`.

### Workstation Has No EDH Session

**Symptoms:**

-   `edh workstation start`, `stop` or `connect` fails with an `EDH Error` whose message ends in `Workstation has no EDH session` or `Workstation is not ready for connection` (HTTP 409)
-   `edh workstation delete` fails with `Workstation is still being created` (HTTP 409)

**Cause:**

The launch runs asynchronously. Until VAMS reads the created desktop back from EDH — through the launch worker, or else the reconciler's next sweep — the record is `CREATING` with no session id, so there is nothing to start, stop or connect to; `delete` refuses such a launch until its record has gone 15 minutes without a change. `connect` also refuses a `STOPPED` workstation and one whose desktop is not yet `running` in EDH (`connectionAvailable` false).

**Resolution:**

1. Watch the launch in the list, selecting it by its `bootstrapId`, until it reports `ACTIVE` and a session id, then address it by that `sessionId`:

    ```bash
    vamscli edh workstation list --json-output | jq '.Items[] | select(.bootstrapId == "<BOOTSTRAP_ID>") | {state, sessionId, lastError, edhFailure}'
    ```

2. A launch whose record has not changed for 15 minutes while `CREATING` can be deleted by its bootstrap id with `--confirm`; one that ends `ORPHANED` is explained under [Launch Ended ORPHANED](#launch-ended-orphaned).

### Start Refused for a Workstation That Is Not Stopped

**Symptoms:**

-   `edh workstation start` fails with HTTP 409 `Workstation is already running` or `Workstation is not stopped`

**Cause:**

Only a `STOPPED` workstation can be started. A running one is already available to `connect`; one that is `CREATING` — including one you have just started, whose `State` column reads `STARTING` (`activity` `starting`) until EDH reports it running — is still in transition. One that reads `STALLED` is `CREATING` too: stop it, then start it again (see [Start Stalled](#start-stalled)).

**Resolution:**

Watch the record with `vamscli edh workstation get SESSION_ID` until it reports `ACTIVE` with `Activity: none` and `connectionAvailable` true, then `connect`; stop it first if you meant to restart it. `vamscli edh workstation start SESSION_ID --wait` does the watching for you.

### Start Refused While EDH Is Still Stopping the Workstation

**Symptoms:**

-   `edh workstation start` fails with `Workstation start refused (409): The workstation is still stopping in EDH; start it again once it has stopped.`, or names another EDH state such as `interrupted`

**Cause:**

VAMS records a workstation `STOPPED` as soon as the stop is accepted, but EDH reports the desktop `stopping` while Amazon EC2 stops the instance and until EDH next checks the desktop's state, which can take several minutes; meanwhile the `State` column reads `STOPPING` (`activity` `stopping`). EDH starts a desktop only once it reports it `stopped`, so the start is refused before a session key is minted or EDH is sent a start.

**Resolution:**

Wait until `vamscli edh workstation get SESSION_ID` reports `edhState` `stopped` and `Activity: none`, then run `edh workstation start` again.

### Start Refused Because a Start Is in Progress

**Symptoms:**

-   `edh workstation start` fails with `Workstation start already in progress (409): A start of this workstation is already in progress. Run 'vamscli edh workstation list' to see its state.`
-   With `--json-output`, the error object carries `"error_type": "EdhStartInProgressError"` and `"startInProgress": true`

**Cause:**

Another start of the workstation is still in progress (its `State` column reads `STARTING`), for example one sent from another terminal or the web console, the reconciler is revoking the stopped desktop's session key, or a lease renewal is still re-issuing that key, and EDH still reports the desktop stopped. A start runs in the background, and can take several minutes when EDH first waits for a capacity reservation; a renewal, or a reconciler sweep that ends part-way through its revoke, holds off a start for up to 5 minutes. The refusal follows the proxy's read of the desktop's state, mints no session key and sends EDH no start.

**Resolution:**

Watch the workstation with `vamscli edh workstation list` until its `State` column leaves `STARTING`: it reads `ACTIVE` once the start succeeded, or `STOPPED` with a `Last Error` once it failed (see [Start Left the Workstation Stopped](#start-left-the-workstation-stopped)). If it reads `STOPPED` with no `Last Error` 5 minutes after a renewal or the reconciler's revoke, run `edh workstation start` again.

### Start Refused Because the Lease Expired

**Symptoms:**

-   `edh workstation start` fails with HTTP 409 `The lease on this workstation has expired; renew it before starting`
-   `edh workstation get SESSION_ID` reports `STOPPED` with `lastError` `LEASE_EXPIRED`, a `leaseExpiresAt` in the past and a `leaseStoppedAt`

**Cause:**

The deployment time-boxes workstations, and this one's lease ran out. The reconciler's sweep stopped the desktop and recorded the expiry code; the desktop is kept for the policy's grace hours, counted from whichever came later, the expiry or the stop, and deleted after that.

**Resolution:**

Renew the lease, then start the workstation:

```bash
vamscli edh workstation renew SESSION_ID
vamscli edh workstation start SESSION_ID
```

A renewal grants the policy's current lease length, which `vamscli edh catalog lease` reports, and is only possible while leases are enabled. A renewal that answers `409` `Leases are disabled for this deployment` means an administrator has turned leases off since the expiry was stamped; a start is not refused while they are off, so the workstation starts as it is, and the stored expiry applies again if an administrator turns leases back on. An administrator changes the lease length and the grace hours with `vamscli edh admin settings set`.

### Start Left the Workstation Stopped

**Symptoms:**

-   After `edh workstation start`, `edh workstation list` read `STARTING`, then the workstation is `STOPPED` again, with `Last Error` `CAPACITY_UNAVAILABLE`, `RESERVATION_FULL` or `START_FAILED`
-   `edh workstation get SESSION_ID` shows a `Start Failure:` line, for example:

    ```text
      Last Error:      CAPACITY_UNAVAILABLE
      Start Failure:   AWS had no capacity for g5.16xlarge in this workstation's Availability Zone (us-west-2b) when it was started. A stopped workstation keeps its disk and starts only in that zone, so it stays stopped; start it again later.
                       AWS: InsufficientInstanceCapacity in us-west-2b
    ```

-   `edh workstation start --wait` exits 1 with `Workstation Start Failed: Workstation start failed (<lastError>): <reason>`, and with `--json-output` an error object carrying `"error_type": "EdhStartFailedError"`, `"waitOutcome": "failed"`, `lastError` and `startFailure`

**Cause:**

A start is accepted (HTTP 202) and carried out in the background, so a start EDH refuses is recorded on the workstation rather than answered by the command. The workstation and its root disk are kept: VAMS never deletes, terminates or orphans a desktop for a refused start, and takes back the session key it minted for the start. `startFailure` holds the AWS error code EDH's refusal named (`errorCode`) and the Availability Zone the desktop's instance, and so its disk, is in (`availabilityZone`); either is absent when it could not be read.

| `lastError`            | Reason shown                                                                                                                                                                                                                  | What happened                                                                                                                                                                                                                                                                                                                              |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `CAPACITY_UNAVAILABLE` | `AWS had no capacity for <instance type> in this workstation's Availability Zone (<zone>) when it was started. A stopped workstation keeps its disk and starts only in that zone, so it stays stopped; start it again later.` | AWS had no capacity for the instance type in the desktop's Availability Zone (typically `InsufficientInstanceCapacity`). EDH first tries to reserve capacity in that zone and then, in its default configuration, starts the instance on-demand; both found none. A stopped desktop starts only in its own zone, on its own instance type. |
| `RESERVATION_FULL`     | `The capacity reservation has no free capacity; the workstation stays stopped. Start it again once a slot is free, or ask an administrator.`                                                                                  | The workstation was launched into a capacity reservation (`capacityReservationId` in `edh workstation get --json-output`). A stopped desktop gives its slot back and starts again in the same reservation; every slot is in use now. VAMS does not move a stopped desktop to another reservation or to on-demand.                          |
| `START_FAILED`         | `The workstation did not start; it is still stopped with its disk. Start it again, or ask an administrator.`                                                                                                                  | EDH refused the start for another reason, or the start never took effect: the reconciler's sweep records it once EDH still reports the desktop stopped 10 minutes after the start was requested.                                                                                                                                           |

The `Last Error` and `Start Failure` lines stay until a start of the workstation is accepted.

**Resolution:**

1. For `CAPACITY_UNAVAILABLE`, run `vamscli edh workstation start SESSION_ID` again later, when AWS has capacity in that zone again; `--wait` tells you whether it ran. If you need a desktop at once, launch a new workstation (`edh workstation create`), which EDH may place in another zone, and delete the stopped one only once you no longer need its disk.
2. For `RESERVATION_FULL`, start it again once a slot is free; an administrator sees the free slots with `vamscli edh admin capacity-reservation list` (`FREE/TOTAL`). Free a slot by stopping or deleting another workstation in the same reservation, or ask an administrator to. To run a desktop on-demand, launch a new workstation without `--capacity-reservation-id`; the stopped one keeps its reservation.
3. For `START_FAILED`, start it again; if it fails again, give the `Start Failure` lines to an administrator, who can check the cluster with `vamscli edh admin health`.

### Waiting for a Start Timed Out

**Symptoms:**

-   `edh workstation start --wait` exits 1 with `Workstation still STARTING after 10 minutes; the start may still complete. Follow it with 'vamscli edh workstation get <id>'.`
-   With `--json-output`, the error object carries `"error_type": "EdhStartWaitTimeoutError"`, `"waitOutcome": "timedOut"` and the record's `state` and `activity`

**Cause:**

`--wait` reads the workstation every 10 seconds for up to 10 minutes. The start was still in progress when the wait ran out: EDH can wait for a capacity reservation in the desktop's zone before it starts the instance, and a large instance type takes several minutes to boot. The start is not cancelled.

**Resolution:**

Follow the workstation with `vamscli edh workstation get SESSION_ID` until it reads `ACTIVE` with `Activity: none`, or `STOPPED` with a `Start Failure` line (see [Start Left the Workstation Stopped](#start-left-the-workstation-stopped)). Do not start it again while it reads `STARTING`: the start is refused with `Workstation start already in progress (409)`. If it is still not running 30 minutes after the start, it reads `STALLED` (see [Start Stalled](#start-stalled)).

### Start Stalled

**Symptoms:**

-   `edh workstation list` shows `STALLED` in the `State` column and `START_STALLED` under `Last Error`
-   `edh workstation get SESSION_ID` shows `State: CREATING`, `Activity: none`, `EDH State: pending` (or `placing`) and:

    ```text
      Last Error:      START_STALLED
      Start Failure:   EDH has not finished starting this workstation after 30 minutes. Stop it, then start it again; if it stalls again, ask your administrator.
    ```

-   `edh workstation start --wait` exits 1 with `Workstation Start Stalled: Workstation start stalled (START_STALLED): …`, and with `--json-output` an error object carrying `"error_type": "EdhStartStalledError"` and `"waitOutcome": "stalled"`

**Cause:**

EDH accepted the start of the stopped workstation but has not reported the desktop running: 30 minutes after the start was accepted, it still lists the desktop `pending`. A start normally reports running within minutes. The reconciler's sweep records the stall; it does not stop, delete or orphan the workstation, and keeps its session key, because EDH may still finish the start, in which case the workstation turns `ACTIVE` on its own and the stall is cleared. EDH reported no AWS error, so there is no `AWS:` line.

**Resolution:**

1. Stop the workstation with `vamscli edh workstation stop SESSION_ID`, and wait until `edh workstation list` shows it `STOPPED`.
2. Start it again with `vamscli edh workstation start SESSION_ID --wait`.
3. If it stalls again, give the `Start Failure` line and the session id to an administrator, who sees the same session on the web's **Sessions** tab and can check the cluster with `vamscli edh admin health`.

### The Image Is an Older Build or Shows Only Its Id

**Symptoms:**

-   `edh workstation list` shows `(v<n> available)` after the image name in the `Image` column, and `edh workstation get` prints `Recipe version:` with `(latest <n>)`
-   The `Image` column, and the `Image:` line of `get`, show only the image id

**Cause:**

A workstation keeps the image it was launched from. `(v<n> available)` means an administrator has built the workstation's AMI recipe again since its launch, and build `<n>` is available (`imageLatest` `false` in `--json-output`); stopping and starting the workstation does not change its image. An image id alone means VAMS cannot name the image: the workstation was launched before VAMS recorded the image name, and either its image was not built from an AMI recipe, the deployment has AMI builds turned off, the recipe has since been deleted, or VAMS could not read the recipes when it answered (the next read tries again). The web falls back to the name the image catalog lists, while the catalog still lists the image.

**Resolution:**

To use the newer build, launch a new workstation from it (`vamscli edh catalog images` lists it with its version in the `RECIPE` column), then delete the old workstation once you have moved your work. For an image shown by its id, `vamscli edh catalog images` names it while the catalog still lists it.

### The Connection URL Is Not Shown

**Symptoms:**

-   `edh workstation connect` prints a `DCV Host` line but no URL

**Cause:**

The connection URL usually carries an Amazon DCV connection token, so it is printed only when explicitly requested. In EDH's high-scale layout the broker issues a fresh token for each `connect` request EDH answers, at most one per request and only for that desktop (VAMS repeats a read once after a transport failure or a gateway error, which can issue a second), and never for `list` or `get`. The token is valid for EDH's configured lifetime (12 hours by default), and anyone holding the URL can open the desktop until the token expires.

**Resolution:**

Request the full response and read `connectionUrl`; treat the value like a password, because it is written to stdout and lands in your terminal scrollback and in anything that captures or pipes the output:

```bash
vamscli edh workstation connect SESSION_ID --json-output | jq -r '.connectionUrl'
```

### A Windows Workstation Opens at the Lock Screen

**Symptoms:**

-   The tab that `connectionUrl` opens shows the Windows lock screen, `Press Ctrl+Alt+Delete to unlock`, instead of a desktop

**Cause:**

A Windows workstation runs an Amazon DCV console session, and Windows does not sign in on its own: the desktop waits at its lock screen for its Windows user name and password. Amazon DCV also locks Windows when the last connection to the desktop closes.

**Resolution:**

Read the sign-in, choose **Send Ctrl + Alt + Del** from the menu under the computer name in the Amazon DCV toolbar, choose the user name in the lower-left corner if Windows offers another account, and type the password:

```bash
vamscli edh workstation connect SESSION_ID --show-windows-password
```

The password is a credential written to stdout; treat the output like the connection URL. The same password unlocks the desktop after a reconnect. After a stop and start, the desktop opens at the lock screen again and the same password signs in; Windows then shows the Shutdown Event Tracker, which **Cancel** closes. The web app shows the same sign-in under **Show Windows password** (see [Signing in to a Windows workstation](user-guide.md#signing-in-to-a-windows-workstation)).

### Windows Sign-in Refused

**Symptoms:**

-   `edh workstation connect --show-windows-password` fails with HTTP 409 and `Windows password is not available yet`
-   `edh workstation connect --show-windows-password` fails with HTTP 400 and `Workstation is not a Windows workstation`

**Cause:**

For the 409, the workstation is running, but EDH lists no Windows password for it yet. For the 400, the workstation's `osFamily` is `linux`: only a Windows workstation has a Windows sign-in. Either refusal prints only the error, not the connection details.

**Resolution:**

For the 409, wait a few minutes and run the command again; if it persists, an administrator can check the desktop in EDH's own console. For a Linux workstation, run the command without `--show-windows-password`.

### The Windows Sign-in Names a Directory Account

**Symptoms:**

-   `edh workstation connect --show-windows-password` prints `sign in with your directory password` in place of a password, and `windowsSignIn` carries `"password": null` and `"directoryAccount": true`

**Cause:**

The desktop signs in with a directory account, a user name of the form `DOMAIN\user`, as on an EDH cluster joined to Active Directory. Its password is the account's own directory password, which VAMS does not hold.

**Resolution:**

At the lock screen, type the user name shown and your own directory password.

---

## Launch Refusals

### Launch Refused for an API-Key Session

**Symptoms:**

-   `edh workstation create` or `edh workstation start` fails with `Permission Denied: Access forbidden. Workstation launch is not available to API key sessions` (or `... Workstation start ...`) although `edh workstation list` works, and you logged in with `--token-override-stdin` using a `vams_…` API key

**Cause:**

Launching or starting a workstation is refused (HTTP 403) for a session authenticated with a VAMS API key. A key seeded into a workstation by the session bootstrap cannot be used to launch further workstations.

**Resolution:**

Log in with your user credentials (`vamscli auth login -u you@example.com`) and launch again, or launch from the VAMS web UI.

### Launch Refused for the Image or OS Family

**Symptoms:**

-   `edh workstation create` fails with HTTP 400 and `imageId is not a registered VDI image`, `The image's OS family cannot be determined` or `osFamily does not match the image`

**Cause:**

Before recording a launch, the API reads EDH's desktop image catalog and checks `--image-id` against it: the image is not registered, EDH reports neither an OS family nor a base OS for it, or `--os-family` is not the family the catalog lists for that image. Nothing was recorded and no desktop was requested.

**Resolution:**

Pick the image and its `osFamily` from `edh catalog images` and launch again; an image whose family cannot be determined is not offered there.

### Launch Refused for the Instance Type or Lease

**Symptoms:**

-   `edh workstation create` fails with HTTP 400 and `instanceType is not permitted in this deployment`
-   `edh workstation create` fails with HTTP 400 and `instanceType does not match the image's architecture`
-   `edh workstation create` fails with HTTP 400 and `instanceType is not offered for this image`
-   `edh workstation create --lease-hours` fails with HTTP 400 and `Leases are disabled for this deployment` or `leaseHours must be 1-<hours>`

**Cause:**

The deployment restricts desktops to an allow-list of Amazon EC2 instance types (`app.addons.useEdhWorkstations.edhAllowedInstanceTypes`), and `--instance-type` is not on it. For the architecture, the image's own EDH profile lists `--instance-type` only under architectures of another family than the image's (`x86_64` and `i386` are one family, `arm64` another), so the type cannot run the image. For an image an AMI recipe built, the recipe lists launch instance types (`launchInstanceTypes` on the image in `edh catalog images --json-output`) and `--instance-type` is not among them. For a lease, `--lease-hours` was given while the lease policy is off, or exceeds the policy's `leaseHours`. Nothing was recorded and no desktop was requested.

**Resolution:**

Pick an instance type from `edh catalog instance-types --image-id <ID>`, which lists only the types the deployment permits that run the image's architecture (its `ARCH` column, or `architectures` in `--json-output`, against the image's `ARCH` in `edh catalog images`) and that the image's recipe offers. An administrator changes a recipe's list with `edh admin ami-recipe update`, or in a seeded recipe's `recipe.json`; that starts no build. Check the lease policy with `vamscli edh catalog lease`: omit `--lease-hours` while leases are off, or pass a value from 1 up to the policy's `leaseHours`.

### Launch Refused for the Root Disk Size

**Symptoms:**

-   `edh workstation create` fails with HTTP 400 and `Root disk size must be at least <n> GB for this image`

**Cause:**

EDH registered the image with a root volume of `<n>` GiB, and `--root-size-gb` is smaller: a desktop cannot launch with a root disk smaller than its image's. Nothing was recorded and no desktop was requested.

**Resolution:**

Read the image's `rootSize` from `vamscli edh catalog images` and launch again with `--root-size-gb` of at least that size.

### Launch Refused Because the Capacity Reservation Is Full

**Symptoms:**

-   `edh workstation create --capacity-reservation-id` fails with `Capacity Reservation Full: Workstation launch refused (409): The capacity reservation has no free capacity`
-   With `--json-output`, the error carries `error_type` `EdhReservationFullError` and `"code": "RESERVATION_FULL"`

**Cause:**

Every slot of the reservation is in use, and its registration does not fall back to on-demand (`FALLBACK` `refuse` in `edh catalog capacity-reservations`), so VAMS refused the launch rather than run it on-demand. Nothing was recorded and no desktop was requested. A reservation that falls back launches the workstation on-demand instead, and the record then carries `capacityReservationFallback` `true`.

**Resolution:**

1. Launch on-demand by leaving out `--capacity-reservation-id`, or into another reservation with a free slot from `vamscli edh catalog capacity-reservations --instance-type <TYPE> --os-family <FAMILY>`.
2. Or launch again once a slot is free; stopping or deleting a workstation in the reservation frees one. An administrator can let launches fall back with `vamscli edh admin capacity-reservation update <CR_ID> --fallback`.

### Capacity Reservation Not Registered

**Symptoms:**

-   `edh workstation create --capacity-reservation-id` fails with `Workstation launch rejected (400): capacityReservationId is not registered`
-   It fails with `Access forbidden.` (HTTP 403)

**Cause:**

An administrator has not registered the reservation, has disabled it, or has unregistered it. A 403 means the reservation is registered but your role does not grant it: launching into a reservation needs an `edhCapacityReservation` constraint allowing `POST` on it, and seeing it in the catalog one allowing `GET`. The **EDH User** template grants none. Nothing was recorded.

**Resolution:**

List the reservations you may use with `vamscli edh catalog capacity-reservations`, and launch into one of those, or on-demand without the option. Ask an administrator to register the reservation or to grant it to your role.

### Capacity Reservation Not Active

**Symptoms:**

-   `edh workstation create --capacity-reservation-id` fails with `Workstation launch rejected (400): The capacity reservation is not active`

**Cause:**

EC2 reports the reservation in a state other than `active`: it has expired, been cancelled, is still pending, or EC2 no longer knows it (`notFound` in `edh admin capacity-reservation list`). Nothing was recorded.

**Resolution:**

Launch on-demand or into another reservation from `vamscli edh catalog capacity-reservations`, which lists only active ones. An administrator unregisters a reservation that has ended.

### Capacity Reservation Does Not Match the Launch

**Symptoms:**

-   `edh workstation create --capacity-reservation-id` fails with `Workstation launch rejected (400): instanceType does not match the capacity reservation`
-   It fails with `Workstation launch rejected (400): The capacity reservation's platform does not match the image`

**Cause:**

A reservation holds instances of one instance type and one platform. `--instance-type` differs from the reservation's, or the reservation's platform does not suit the image: a Linux image (`--os-family linux`) launches only into a `Linux/UNIX` reservation, a Windows image only into a `Windows` one. Nothing was recorded.

**Resolution:**

List the reservations that suit the launch, and use the instance type a reservation holds:

```bash
vamscli edh catalog capacity-reservations --instance-type g5.16xlarge --os-family linux
```

### Capacity Reservation Could Not Be Read

**Symptoms:**

-   `edh workstation create --capacity-reservation-id` fails with `Workstation launch failed (503): The capacity reservation could not be read; try again`, `error_type` `EdhUnavailableError`

**Cause:**

VAMS reads the reservation from Amazon EC2 before every launch into it, and that read failed. VAMS does not fall back to on-demand on a failed read, since it cannot tell whether the reservation is full. Nothing was recorded and EDH was not called.

**Resolution:**

Run the command again. If it keeps failing, launch on-demand without `--capacity-reservation-id`, and ask an administrator to check `vamscli edh admin capacity-reservation list`, where the reservation's `STATE` reads `unknown` while EC2 cannot be read.

### Launch Ended ORPHANED

**Symptoms:**

-   `edh workstation create` returned `Workstation launch accepted (HTTP 202)`, but `edh workstation list` now reports the launch `ORPHANED` with a `lastError` code
-   `edh workstation get BOOTSTRAP_ID` may also show an `EDH failure:` line, such as `InsufficientInstanceCapacity (CreateCapacityReservation)`, `VcpuLimitExceeded (RunInstances)` or `desktop setup failed at <checkpoint>`

**Cause:**

The launch itself runs after the command returns. When EDH refuses it, VAMS records a generic code rather than EDH's own message, and the same field carries the code the lease sweep writes when it stops a workstation. When EDH's own record of the desktop names the AWS error behind a failed placement, or the setup step a desktop failed at, VAMS also stores that as `edhFailure` (shown on the `EDH failure:` line; see [edh workstation list](cli-commands.md#edh-workstation-list)), and an AWS capacity or limit code turns a generic `LAUNCH_ERROR` into `CAPACITY_UNAVAILABLE` or `QUOTA_EXCEEDED`:

| `lastError`            | Meaning                                                                                                                                                                                                              |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `USER_NOT_PROVISIONED` | The deployment does not auto-provision EDH identities and yours does not exist yet                                                                                                                                   |
| `QUOTA_EXCEEDED`       | EDH's per-user or profile quota (session count or budget) is used up, or AWS refused the instance for an account limit (`edhFailure` names it, such as `VcpuLimitExceeded`)                                          |
| `CAPACITY_UNAVAILABLE` | EDH found no available AWS capacity for the chosen instance type (`edhFailure` names the AWS code, such as `InsufficientInstanceCapacity`)                                                                           |
| `IMAGE_UNAVAILABLE`    | The chosen image is no longer registered in EDH                                                                                                                                                                      |
| `EDH_REFUSED`          | EDH rejected the request (an instance type the image's profile does not allow, …)                                                                                                                                    |
| `LAUNCH_ERROR`         | EDH reports the desktop in an `error` state, or the last launch attempt failed without a definite refusal                                                                                                            |
| `CREATE_TIMED_OUT`     | The launch made no progress for 30 minutes after its record's last write (5 minutes once all three launch attempts had started) and was closed by the reconciler's sweep                                             |
| `RESERVATION_FULL`     | The capacity reservation the launch was sent into had no free slot when EDH placed it, and its registration does not fall back to on-demand (`edhFailure` may name `ReservationCapacityExceeded` or a capacity code) |
| `LEASE_EXPIRED`        | Not a launch refusal: the sweep stopped a workstation whose lease ran out, leaving it `STOPPED`                                                                                                                      |

**Resolution:**

1. Delete the orphaned record so it stops counting against you, using the identifier `edh workstation list` shows for it: the bootstrap id, or the session id when EDH had assigned one before the desktop entered its `error` state (`LAUNCH_ERROR`):

    ```bash
    vamscli edh workstation delete BOOTSTRAP_ID --confirm
    ```

2. For `USER_NOT_PROVISIONED`, ask the EDH cluster operator to provision your EDH identity — the `edhUid` shown by `vamscli edh workstation get BOOTSTRAP_ID --json-output`. For `QUOTA_EXCEEDED`, stop or delete a workstation you no longer need (`vamscli edh workstation list`) or ask an administrator to check `vamscli edh admin capacity`. For `EDH_REFUSED`, retry with an instance type from `edh catalog instance-types` and an image from `edh catalog images`. For `CAPACITY_UNAVAILABLE`, launch again with another instance type from `edh catalog instance-types`, or later; EDH reserves a desktop's capacity in one subnet, so the same type soon after usually fails the same way, and an administrator can add EDH subnets in more Availability Zones. EDH refuses a launch for capacity only where the cluster's `AsyncPlacement` is `false` and `EnableCapacityReservation` is `true`; with EDH's asynchronous placement, a shortage arrives as an `edhFailure` with a capacity `errorCode` and `lastError` `CAPACITY_UNAVAILABLE`, or as `LAUNCH_ERROR` when VAMS could not read the reason from EDH. For an `edhFailure` `VcpuLimitExceeded` (or another limit code under `QUOTA_EXCEEDED`), the AWS account's vCPU quota for the instance family is used up: stop or delete a workstation of the same family, or ask an administrator to raise the Amazon EC2 quota in the cluster's account and Region (for G instances, **Running On-Demand G and VT instances**) in Service Quotas.

3. `LAUNCH_ERROR` and `CREATE_TIMED_OUT` are cluster-side; an administrator can confirm the proxy's view of the cluster with `vamscli edh admin health`. An `EDH failure:` line `desktop setup failed at <checkpoint>` names the step at which EDH's setup of the desktop stopped; give it to the cluster operator. For `RESERVATION_FULL`, launch again on-demand (without `--capacity-reservation-id`), into another reservation, or once a slot is free; see [Launch Refused Because the Capacity Reservation Is Full](#launch-refused-because-the-capacity-reservation-is-full). `LEASE_EXPIRED` is not a launch outcome at all — see [Start Refused Because the Lease Expired](#start-refused-because-the-lease-expired).

### Windows Launch Ended ORPHANED With QUOTA_EXCEEDED

**Symptoms:**

-   A Windows `edh workstation create` ends `ORPHANED` with `lastError` `QUOTA_EXCEEDED` although fewer of your Windows workstations are running than the limit you expect

**Cause:**

EDH limits the desktops each user holds per operating system family, four by default, and a stopped desktop counts against the limit as well as a running one.

**Resolution:**

Delete the Windows workstations you no longer need, the stopped ones included (`vamscli edh workstation list`), delete the orphaned record, and launch again. An administrator can raise the limit in EDH.

### Confirmation Required

**Symptoms:**

-   `edh workstation delete`, `edh admin images delete-vdi`, `edh admin images delete-simulation`, `edh admin ami-recipe delete`, `edh admin ami-recipe retire`, `edh admin instances deactivate` or `edh container job kill` exits non-zero with `Confirmation required`, and in `--json-output` mode emits `{"error": "Confirmation required", ...}`

**Cause:**

These operations are irreversible and refuse to run without the `--confirm` flag.

**Resolution:**

Re-run with `--confirm`. There is no interactive prompt; the flag is the only confirmation.

### Capacity Reservation Commands Refused

**Symptoms:**

-   `edh admin capacity-reservation add` fails with `Capacity reservation registration rejected (400): Capacity reservation not found in this account and Region`, with HTTP 400 because 100 reservations are already registered, or with `Capacity reservation registration refused (409)` for a reservation already registered
-   `edh admin capacity-reservation update` or `remove` fails with `Capacity Reservation Not Found: Capacity reservation '<CR_ID>' is not registered`
-   Any of them fails with exit 2 and `Capacity reservation id must be cr- and 17 hex digits`, or a label outside its rule

**Cause:**

The registry holds VAMS's own entries for EC2 capacity reservations, at most 100. Registering reads the reservation from EC2 in the deployment's account and Region, so a reservation in another account or Region, or a mistyped id, is not found. `update` and `remove` address a registered reservation only. An id that is not `cr-` and 17 hexadecimal digits is refused before any request.

**Resolution:**

1. List the registry with `vamscli edh admin capacity-reservation list`, and the reservations in EC2 with `aws ec2 describe-capacity-reservations --region <region>` in the deployment's account.
2. Unregister one you no longer use with `vamscli edh admin capacity-reservation remove <CR_ID> --confirm` before registering another beyond the hundredth; the EC2 reservation itself is not changed.

---

## Session Keys

### API Key Create or Update Refused on a Workstation

**Symptoms:**

-   From a desktop whose `vamscli` was signed in by session bootstrap, `vamscli auth api-key create` (or an update) fails with HTTP 403 `API keys cannot be created or updated with a temporary session key`, while listing and deleting keys work

**Cause:**

The desktop's credential is a temporary session key: an API key whose name starts with the reserved prefix `temporary-session-`, which session bootstrap mints as `temporary-session-edh-<bootstrapId>`, with a `-r<YYYYmmddHHMMSS>` suffix on a key a renewal re-issues. API-key create and update refuse a caller holding a temporary session key, whatever the user's roles grant, so a workstation credential cannot mint a permanent key or extend its own.

**Resolution:**

Create or update API keys from the web interface or from a CLI session signed in with `vamscli auth login`.

### Session Key Expired on a Workstation

**Symptoms:**

-   On the desktop, `vamscli` commands fail with `Override token has expired. …`, or with `Override token authentication failed. The token may be invalid or expired. …`
-   The Isaac Sim connector's status line reads `access refused - …`
-   `vamscli --profile "edh-$(/usr/local/bin/vams-instance-id)" auth status` reports an expiration that has passed

**Cause:**

The desktop's session key lasts `sessionKeyTtlMinutes` (480 by default) from the launch, the last start or the last lease renewal that re-issued it, and the CLI does not refresh it. The reconciler revokes a key once it has expired, whatever the workstation's state.

**Resolution:**

1. Where the workstation carries a lease, renew it from the web interface or from a CLI session that did not authenticate with an API key; the renewal re-issues the key of an `ACTIVE` workstation:

    ```bash
    vamscli edh workstation renew SESSION_ID
    ```

    A Linux desktop built from a shipped recipe (`al2023-desktop` or `isaacsim`) signs in with the new key within about a minute, and `~/.vams/hosts/<instance-id>/session-bootstrap.log` on the desktop records `signed in; the key expires at <expiresAt>`. On a desktop whose image signs in only when the desktop session starts, such as one built from an earlier Isaac Sim recipe, run `/usr/local/bin/vams-session-bootstrap.sh` on the desktop after the renewal.

2. Where it carries none, stop the workstation and start it again. The start mints a new key, which the desktop signs in with when its desktop session starts:

    ```bash
    vamscli edh workstation stop SESSION_ID
    vamscli edh workstation start SESSION_ID
    ```

    Start it once `vamscli edh workstation get SESSION_ID` reports `edhState` `stopped` (see [Start Refused While EDH Is Still Stopping the Workstation](#start-refused-while-edh-is-still-stopping-the-workstation)).

3. Or sign in by hand in a terminal on the desktop, as yourself:

    ```bash
    vamscli --profile "edh-$(/usr/local/bin/vams-instance-id)" auth login -u <user-id>
    vamscli --profile edh auth login -u <user-id>
    ```

    The first command signs in the desktop's own profile, which the Isaac Sim connector uses, and the second the `edh` alias terminals use. Where the deployment does not sign users in with a password, sign in with a token instead (see [Setup and Authentication](../../cli/commands/setup-and-auth.md)). A sign-in made by hand is your own session, which a stop of the workstation does not sign out; the desktop's next start, or its next re-issued key, replaces it.

### A Renewal Placed No New Session Key

**Symptoms:**

-   `vamscli edh workstation renew SESSION_ID --json-output` answers `"sessionBootstrap": "unavailable"` or `"skipped"`, or carries no `sessionBootstrap`

**Cause:**

-   `unavailable`: no new key could be minted or written into the session secret, or another request held the workstation's credential, such as another renewal still re-issuing its key or the reconciler revoking it
-   `skipped`: your account holds no roles, so no key can be minted
-   No `sessionBootstrap`: the renewal authenticated with an API key, for example the desktop's own session key, the workstation is not `ACTIVE`, or a start of it is in progress; such a renewal renews the lease only

The lease is renewed in every case. A key a running desktop still holds keeps working until its own expiry. A stopped workstation's keys were revoked when it stopped, and its next start mints a new one.

**Resolution:**

For a running workstation, renew again after a few minutes, from the web interface or from a CLI session that did not authenticate with an API key; for `skipped`, ask an administrator to assign you a role first. For a stopped workstation, start it.

### A Desktop Does Not Sign In Because of Your Own AWS Credentials

**Symptoms:**

-   On a Linux desktop built from a shipped recipe, `~/.vams/hosts/<instance-id>/session-bootstrap.log` records `edh:JobName 'None' is not a VAMS launch; nothing to sign in`, or an `UnauthorizedOperation` or `AccessDeniedException` error every minute
-   `aws sts get-caller-identity` on the desktop names a principal other than the desktop's instance role

**Cause:**

The desktop signs in with AWS CLI calls that read its EC2 tags and its session secret. The AWS CLI takes credentials from the `AWS_*` environment variables and from the `~/.aws/credentials` and `~/.aws/config` files before the instance role, so a `[default]` profile there, for example one that `aws configure` wrote, replaces the instance role for those calls. Credentials that cannot read the desktop's tags fail with `UnauthorizedOperation`, and credentials of another AWS account find no `edh:JobName` tag for the desktop. Every desktop of a user mounts the same home directory, so such a profile affects every desktop of the user that signs in from its session secret.

**Resolution:**

Remove the `[default]` entry, or move those credentials to a named profile and pass `--profile` to the commands that need them. The next check of the sign-in, within about a minute, signs the desktop in, and the log records `signed in; the key expires at <expiresAt>`.

`AWS_*` credential variables that the desktop session started with stay in effect for the sign-in until the next desktop login. Remove them from wherever the session sets them, such as `~/.profile`, so the next login starts without them. To sign in sooner, run `/usr/local/bin/vams-session-bootstrap.sh` from a terminal in which they are not set, for example after `unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE`; it prints `signed in; the key expires at <expiresAt>`.

### Profiles of Deleted Workstations

**Symptoms:**

-   On a Linux desktop built from a shipped recipe, `vamscli profile list` no longer lists the `edh-i-…` profile of a workstation you deleted, or still lists one
-   The active profile is `edh` after you signed in to a desktop

**Cause:**

Every desktop of a user mounts the same home directory, and each desktop's sign-in leaves a CLI profile `edh-<instance-id>` and a directory `~/.vams/hosts/<instance-id>/` there. At each desktop login, once its sign-in has completed or given up, a Linux desktop built from a shipped recipe runs `/usr/local/bin/vams-profile-cleanup.sh`, which removes the profile and host directory of every other desktop that Amazon EC2 no longer lists, that is, a desktop that was deleted. A desktop that still exists, running or stopped, keeps both. The cleanup never touches this desktop's own profile, `edh`, `default` or any other profile not named `edh-i-` followed by 8–17 hex digits, and removes nothing when it cannot tell: an Amazon EC2 call that fails, or an answer that does not list this desktop itself. It then makes the profile that was active before the sign-in active again, unless that was a desktop's own profile or one that no longer exists, in which case it makes `edh` active. While the login's sign-in runs, the active profile is the desktop's own. A desktop's own profile that a later check of the session creates, because the login's sign-in wrote none — for example once a lease renewal delivers a first key — also becomes the active profile, and the next login's cleanup then makes `edh` active rather than the profile that was active before.

**Resolution:**

Read `~/.vams/hosts/<instance-id>/profile-cleanup.log` on the desktop, which records each profile removed and why nothing was removed. `EC2 could not be asked which desktops still exist; nothing removed` means the call failed, for example because the desktop's instance role cannot call `ec2:DescribeTags`, which the sign-in needs as well; the next login tries again. To make another profile active, run `vamscli profile switch <name>`.

---

## Container Jobs

### Container Commands Not Enabled

**Symptoms:**

-   `edh container …` fails with `EDH container jobs are not enabled for this deployment` while `edh workstation …` works

**Cause:**

The container commands are gated on the separate `EDH_CONTAINERS` switch (`app.addons.useEdhWorkstations.containers.enabled`), which a deployment can leave off while running workstations.

**Resolution:**

Check `vamscli features check EDH_CONTAINERS`; log in again if the switch was enabled after your login, or ask the administrator to enable container jobs.

### EKS Backend Not Enabled

**Symptoms:**

-   `--backend eks` is rejected with `The eks backend is not enabled in this deployment` while `--backend batch` works

**Cause:**

The experimental Amazon EKS backend is a separate opt-in (`app.addons.useEdhWorkstations.containers.eksEnabled`); `edh container catalog` reports its state as `eksEnabled` and lists the clusters only when it is on.

**Resolution:**

Submit to the AWS Batch backend, or ask the administrator to enable the EKS backend.

### Job Not Found

**Symptoms:**

-   `edh container job kill` reports `Job Not Found` for a job that exists

**Cause:**

Jobs are listed and killed under your own EDH identity on one backend: another user's job, or a job on the other backend (a Batch job id sent with `--backend eks`, or an EKS job name without `--backend eks --cluster`), is reported as not found.

**Resolution:**

Use `vamscli edh container job list` (with `--backend eks --cluster NAME` for EKS jobs) and pass the row's `jobId` verbatim, with the same `--backend`, `--cluster` and `--namespace` options.

---

## AMI Recipes

### AMI Recipe Commands Not Enabled

**Symptoms:**

-   `edh admin ami-recipe …` fails with `EDH AMI recipes are not enabled for this deployment` while the other `edh admin` commands work

**Cause:**

The AMI recipe commands are gated on the separate `EDH_AMI_RECIPES` switch (`app.addons.useEdhWorkstations.amiBuilds.enabled`), which the deployment publishes only while the add-on itself is enabled.

**Resolution:**

Check `vamscli features check EDH_AMI_RECIPES`; log in again if the switch was enabled after your login, or ask the administrator to enable AMI builds.

### Recipe or Build Not Found

**Symptoms:**

-   An `edh admin ami-recipe` command fails with `Recipe Not Found: AMI recipe '<RECIPE_ID>' not found` (`error_type` `EdhAmiRecipeNotFoundError` with `--json-output`)
-   `edh admin ami-recipe register` or `retire` fails with `Build Not Found: AMI recipe '<RECIPE_ID>' build v<VERSION> not found` (`error_type` `EdhAmiRecipeBuildNotFoundError`)
-   The command fails with HTTP 400 `recipeId is invalid` or `version is invalid`

**Cause:**

No recipe has that `recipeId`, or the recipe has no build with that version. A recipe id is 2-40 lowercase letters, digits, and hyphens, starting with a letter or digit, and a version is the build's integer `version`; any other value is refused before the lookup.

**Resolution:**

Take the recipe id from `vamscli edh admin ami-recipe list` and the version from `vamscli edh admin ami-recipe builds RECIPE_ID`.

### Recipe File Refused

**Symptoms:**

-   `edh admin ami-recipe create` or `update` fails with a `File Read Error` that names `Component file is outside the recipe directory` or `Component file not found`, or reports a JSON parse error
-   The command fails with `AMI recipe creation rejected (400)` or `AMI recipe update rejected (400)` and the field the API refused
-   The command fails with `AMI recipe creation rejected (400)` or `AMI recipe update rejected (400)` and `Windows recipes support x86_64 only`
-   `edh admin ami-recipe create`, `update` or `duplicate` fails with `AMI recipe creation rejected (400)` or `AMI recipe update rejected (400)` and `buildInstanceType does not match the recipe's architecture` or `parentImage does not match the recipe's architecture`
-   The command fails with HTTP 409 `assets are only supported on shipped recipes`, `the assets of a duplicated recipe cannot be changed`, `recipeId is already in use` or `stackName is already used by recipe '<id>'`
-   The command fails with HTTP 409 `unresolved placeholder(s) in component document: ...`, `deployment value(s) not in the expected form for: ...` or `component <n> is <length> characters once its placeholders are resolved; Image Builder accepts at most 16000`

**Cause:**

The CLI reads the recipe file and inlines every `{"file": ...}` component before it sends a request, and a component path must resolve inside the recipe file's directory. The API then validates the whole definition against the [recipe file format](cli-commands.md#ami-recipe-file). A recipe created or updated through the API cannot declare `assets`, except a duplicated recipe, which keeps its source's and accepts only those; each `recipeId` names one recipe, and a create or update refuses a `stackName` another recipe uses, compared without surrounding spaces and without regard to case. Its component documents are resolved when it is saved, as a build resolves them; see [Build Refused](#build-refused) for the placeholder texts. A create, update or duplicate also reads from Amazon EC2 the architectures each `buildInstanceType` supports and, for an `ami-` or `ssm:` `parentImage`, the parent AMI's architecture, and refuses a recipe either of which runs another architecture family than `architecture` (`x86_64` and `i386` are one family); a lookup that fails refuses nothing, and the build start checks again.

**Resolution:**

Correct the file and run the command again: keep component files in or below the recipe file's directory and refer to them by relative path, remove `assets` (a duplicated recipe keeps its bundled files without them), choose another `recipeId` or change the existing recipe with `vamscli edh admin ami-recipe update`, and choose a `stackName` no other recipe uses. A recipe whose `osFamily` is `windows` takes `architecture` `x86_64`, because the Amazon DCV server for Windows runs on x86_64 only. Name build instance types and a parent image of the recipe's `architecture` (Graviton instance types for `arm64`), or change `architecture`.

### Build Refused

**Symptoms:**

-   `edh admin ami-recipe build` fails with `AMI recipe build start refused (409)` and `the recipe is disabled`, `SSM parameter <name> could not be read (<code>)`, `EDH lists no <kind> stack named '<name>'`, `the parent image could not be found`, `the parent image's architecture does not match the recipe's architecture`, `buildInstanceType does not match the recipe's architecture`, `unresolved placeholder(s) in component document: ...`, `deployment value(s) not in the expected form for: ...`, `component <n> is <length> characters once its placeholders are resolved; Image Builder accepts at most 16000`, or `recipe declares assets but EDH_AMI_BUILD_ARTEFACTS_BUCKET or assetPrefix is not set`
-   `edh admin ami-recipe build` fails with `AMI recipe build start refused (409)` and `the recipe is being deleted`, `the recipe no longer exists`, or `the recipe's builds changed while the request was placed; try again`
-   A build ends `FAILED` with `launch did not complete`
-   A build stays `QUEUED` after the recipe's other builds have settled, with `not started: <reason>` in its `error`

**Cause:**

A build that starts at once is checked before anything starts, and nothing is recorded when it is refused. The recipe's `enabled` is `false` (a `config` recipe the deployment no longer includes is disabled), its `parentImage` cannot be resolved to an AMI (the public parameter could not be read, or EDH lists no stack of that name and kind), Amazon EC2 does not find the AMI it resolves to, Amazon EC2 reports that AMI or a build instance type as running another architecture family than the recipe's `architecture`, a component document refers to a `${PARAM_...}`, `${ASSET_...}` or `${DEPLOY_...}` value that is not declared or is longer than 16,000 characters once its placeholders are resolved, the deployment's `sessionBootstrap.secretNamePrefix` that `${DEPLOY_sessionSecretPrefix}` writes is not of its configured form, or a `config` recipe that declares `assets` has no artefacts location to read them from. A build of other content requested while another is `BUILDING` or `REGISTERING` is recorded `QUEUED`, refused only for a disabled recipe, a recipe being deleted or one gone, and checked when it is promoted; a check that fails then leaves it `QUEUED` with `not started: <reason>` in its `error`, and the sweep logs a warning and tries the promotion again every 30 minutes. A request that claimed a version and found it taken reads the builds again, and answers `the recipe's builds changed while the request was placed; try again` after five such attempts. `launch did not complete` is the sweep's verdict on a build whose request claimed its version but never recorded the image pipeline start, as when the request stopped part way; the sweep fails such a build 30 minutes after its claim.

**Resolution:**

For an `admin` recipe, correct the recipe file (`enabled`, `parentImage`, `buildInstanceType`, `parameters`, or the component document) and run `vamscli edh admin ami-recipe update`, then build again. A `config` recipe is corrected in its recipe directory and deployed again; a refused deployment value is corrected in `app.addons.useEdhWorkstations.sessionBootstrap.secretNamePrefix` and deployed again. A queued build is promoted with the recipe's current definition, so once the recipe is corrected the next sweep starts it, or the next `ami-recipe build`, which starts the queued build instead of a new one. After `the recipe's builds changed while the request was placed; try again`, run the build again. After `launch did not complete`, build again; an image the interrupted launch did start is not tracked by the build, and is found on the recipe's image pipeline in the EC2 Image Builder console.

### Build Stuck in REGISTER_FAILED

**Symptoms:**

-   `edh admin ami-recipe builds` shows a build `REGISTER_FAILED` whose `error` ends in `registration retries exhausted; use Retry registration`
-   A build shows `REGISTER_FAILED` with `registration did not complete`

**Cause:**

The image completed and its AMI is kept, but registering the AMI in EDH, or attaching the stack to the launch project, failed; the start of `error` names the step (`EDH registration failed (...)`, `registered in EDH but not assigned to project` — which also covers a stack whose id could not be read back from the catalog — and `completion failed (...)`). The sweep retries a failed registration every 30 minutes until five automatic attempts have failed, and records a build left `REGISTERING` for 30 minutes as `registration did not complete`.

**Resolution:**

1. Confirm that VAMS can reach EDH with `vamscli edh admin health`, and correct the cause the `error` names.
2. Retry the registration, which resets the attempt count:

    ```bash
    vamscli edh admin ami-recipe register RECIPE_ID VERSION
    ```

A successful retry makes the build `AVAILABLE` and retires the recipe's other `AVAILABLE` builds, a newer one included, and a retry the sweep completes on its own has the same effect. Retry an older build only if it should replace the current one; to keep a newer build, retire the older `REGISTER_FAILED` build with `vamscli edh admin ami-recipe retire RECIPE_ID VERSION --confirm`. That retire is refused while EDH lists no stack for the build (see [Retire Refused](#retire-refused)), and the sweep then keeps retrying the build until its five automatic attempts have failed. A retry refused with `the build's AMI has been deregistered` concerns a build that was already pruned; build the recipe again instead.

### Duplicate Refused

**Symptoms:**

-   `edh admin ami-recipe duplicate` fails with `Recipe Not Found: AMI recipe '<SOURCE_ID>' not found`
-   The command fails with `AMI recipe creation refused (409)` and `recipeId is already in use`, `stackName is already used by recipe '<id>'` or `the recipe to duplicate does not exist`
-   The command fails with `AMI recipe creation rejected (400)` and the display name or stack name field
-   The command fails with `Permission Denied: Access forbidden. Not Authorized` (HTTP 403)
-   The command fails with `Request Blocked:` and names the deployment's AWS WAF web ACL

**Cause:**

The command reads the source recipe and creates the copy from it. No recipe has the `SOURCE_ID`, or it was deleted between the read and the create; the `--recipe-id` names a recipe that exists; or another recipe registers its builds under the copy's stack name, compared without surrounding spaces and without regard to case, which a second copy of one recipe meets under the default `<stackName> Copy`. A display name or stack name built from a long source name can exceed the 256 or 200 characters its field allows. A 403 means the role may not call `GET /addon/edh/admin/ami-recipes/{recipeId}` or `POST /addon/edh/admin/ami-recipes`, or may not `GET` the source recipe or `POST` the new one, each checked against the recipe id and OS family. `Request Blocked` means the deployment's AWS WAF web ACL refused the create before it reached VAMS: the copied recipe body matched a managed rule the AMI recipe exemption does not relax.

**Resolution:**

Take the source id from `vamscli edh admin ami-recipe list`, choose a `--recipe-id` no recipe uses, and pass a `--stack-name` of the copy's own, or a shorter `--display-name`. For a 403, ask the administrator for `GET` on the source recipe and `POST` on the new recipe id in the role's `edhImage` constraints, and for `GET` on `/addon/edh/admin/ami-recipes/{recipeId}` and `POST` on `/addon/edh/admin/ami-recipes` in its `api` constraints. For `Request Blocked`, see [Request Blocked (AWS WAF)](../../cli/troubleshooting/general.md#request-blocked-aws-waf). See [`ami-recipe duplicate`](cli-commands.md#edh-admin-ami-recipe-duplicate).

### Recipe Cannot Be Edited or Deleted

**Symptoms:**

-   `edh admin ami-recipe update` fails with HTTP 409 `a config recipe is edited in the repository, not here`
-   `edh admin ami-recipe delete` fails with HTTP 409 `a config recipe is removed from the configuration, not here`, `a build is in progress` or `the recipe is being deleted`

**Cause:**

A recipe whose `source` is `config` is seeded from the configuration (the shipped recipe directories or `predefinedImages.amiRecipes[].recipeDir`) and is replaced from its recipe directory at each deployment, so the API does not change or delete it. A recipe with a `QUEUED`, `BUILDING` or `REGISTERING` build cannot be deleted until that build settles. `the recipe is being deleted` means another delete of the recipe is running, or one that did not finish marked the recipe less than 30 minutes ago.

**Resolution:**

Change a `config` recipe in its recipe directory (under `backendAddons/edh/amiRecipes/` for the shipped recipes, or the `recipeDir` its `predefinedImages.amiRecipes` entry names) and deploy again, or duplicate it into an `admin` recipe with [`ami-recipe duplicate`](cli-commands.md#edh-admin-ami-recipe-duplicate) and update and build the copy. To remove a `config` recipe, take it out of the deployment configuration (remove its `app.addons.useEdhWorkstations.predefinedImages.amiRecipes` entry, or set `app.addons.useEdhWorkstations.amiBuilds.includeShippedRecipes` to `false` for the recipes under `backendAddons/edh/amiRecipes/`) and deploy again: the deployment disables its row rather than deleting it, and builds of it are refused with `the recipe is disabled`. For a build in progress, wait until `vamscli edh admin ami-recipe builds RECIPE_ID` shows it settled, then delete again, and for a delete already running, wait for it to finish; retire the builds you want removed first with [`ami-recipe retire`](cli-commands.md#edh-admin-ami-recipe-retire), because the delete leaves the AMIs, snapshots and EDH stacks the recipe's builds still hold in place; the delete is refused while a build you retired still has its AMI removal pending (see [Delete Refused While an AMI Removal Is Pending](#delete-refused-while-an-ami-removal-is-pending)).

### Delete Refused While an AMI Removal Is Pending

**Symptoms:**

-   `edh admin ami-recipe delete` fails with `AMI recipe deletion refused (409): a build's AMI removal is pending` (`error_type` `EdhOperationError` with `--json-output`)

**Cause:**

A recipe's build rows are the sweep's only record of the AMIs pruning still has to remove, so the delete is refused while one of its builds owes an AMI removal: a `RETIRED` build with `amiRemovalPending`, or a `RETIRED`, `REGISTER_FAILED` or `FAILED` build outside the recipe's `keepVersions` newest AMIs that still holds its AMI. A build keeps its AMI while an Amazon EC2 instance uses the AMI, or after an in-use check or a deregistration that failed (see [Build Keeps Its AMI](#build-keeps-its-ami)). The refused delete leaves the recipe and its builds as they were.

**Resolution:**

List the builds with `vamscli edh admin ami-recipe builds RECIPE_ID` and find the ones the refusal concerns: those with `amiRemovalPending`, and the `RETIRED`, `REGISTER_FAILED` and `FAILED` builds outside the newest `keepVersions` that show an `amiId` without `amiDeregisteredAt`. `amiInUseBy` names the instances that keep each AMI. Delete those desktops once they are no longer needed. The first sweep after the last of them is terminated deregisters the AMI, and the sweep runs every 30 minutes. When each of those builds shows `amiDeregisteredAt`, delete the recipe again.

### Retire Refused

**Symptoms:**

-   `edh admin ami-recipe retire` fails with HTTP 409 `only an AVAILABLE or REGISTER_FAILED build can be retired`, `stack id unknown; cannot deactivate` or `the build status changed`

**Cause:**

Only an `AVAILABLE` or `REGISTER_FAILED` build has an EDH stack to deactivate. `stack id unknown; cannot deactivate` means VAMS holds no stack id for the build and EDH lists no stack with its name, as for a `REGISTER_FAILED` build whose registration never created the stack. `the build status changed` means another retire, a registration retry or pruning claimed the build between the command's read and its write, and this command changed nothing.

**Resolution:**

After `the build status changed`, list the builds with `vamscli edh admin ami-recipe builds RECIPE_ID` and act on the status they now show. Leave a `REGISTER_FAILED` build to pruning: each time a build of the recipe becomes `AVAILABLE`, and at every 30-minute sweep, a `RETIRED`, `REGISTER_FAILED`, or `FAILED` build outside the recipe's `keepVersions` newest AMIs has its AMI and snapshots deregistered unless an Amazon EC2 instance uses the AMI (see [Build Keeps Its AMI](#build-keeps-its-ami)). Until its five automatic attempts have failed, the sweep may still register the build, which then retires the recipe's other `AVAILABLE` builds (see [Build Stuck in REGISTER_FAILED](#build-stuck-in-register_failed)).

### Build Keeps Its AMI

**Symptoms:**

-   `edh admin ami-recipe retire` returns the build `RETIRED` with `amiRemovalPending`, without `amiDeregisteredAt`, and with `amiInUseBy` or a `pruneWarning` of `in-use check failed (...)`
-   `edh admin ami-recipe builds` shows a build outside the recipe's `keepVersions`, or one with `amiRemovalPending`, that keeps its `amiId`, with `amiInUseBy` or a `pruneWarning` of `in-use check failed (...)` or `pruning failed (...)`

**Cause:**

VAMS does not deregister an AMI that an Amazon EC2 instance in the account and Region uses while `pending`, `running`, `stopping` or `stopped`, so that a stopped desktop launched from it can still be started. `amiInUseBy` lists those instances, whether or not VAMS launched them. `in-use check failed (...)` means VAMS could not list the instances and kept the AMI; `UnauthorizedOperation` there means the AMI recipe functions' role does not hold `ec2:DescribeInstances`. `pruning failed (...)` means the deregistration itself failed.

**Resolution:**

Nothing is needed while the desktops are wanted. The AMI stays while any instance uses it; `amiInUseBy` names up to 20 of them as of the last successful check. The first sweep after the last of them is terminated deregisters the AMI, its snapshots and its EC2 Image Builder image; the sweep runs every 30 minutes. Delete those desktops once they are no longer needed. For a failed check, correct its cause, for example by deploying the stack so the AMI recipe functions hold `ec2:DescribeInstances`; the next sweep checks again, and it also retries a deregistration that failed.

---

## Cluster Connectivity

### A Request Was Not Confirmed

**Symptoms:**

-   A command fails with an `EDH Error` such as `Workstation start not confirmed (502): EDH did not confirm the request in time; it may still be in progress. Refresh before trying again. Run 'vamscli edh workstation list' to see its state.`, or `not confirmed (504)`
-   With `--json-output`, the error object carries `"error_type": "EdhOutcomeUnknownError"` and `"outcomeUnknown": true`

**Cause:**

EDH did not answer the request within the proxy's budget after the request may have reached it, or, for `container job submit`, accepted the job with an answer that could not be read, so EDH may have carried it out. For `workstation start`, `stop` and `delete`, the proxy reads the desktop's state again first and reports the request not confirmed only when that read does not show it carried out; EDH records a start only once any capacity reservation it makes has settled, so a start can outlast the budget and still take effect. The administrative and container commands report it without a second listing. A start that is not confirmed keeps its fresh session key.

**Resolution:**

Run the listing the message names until it shows the outcome, and send the request again only if it was not carried out. A workstation start sent again within 3 minutes, while EDH still reports the desktop stopped, is refused; see [Start Refused Because a Start Is in Progress](#start-refused-because-a-start-is-in-progress).

### The EDH Cluster Did Not Answer

**Symptoms:**

-   Commands fail with an `EDH Error` whose message says `the EDH cluster did not answer` (HTTP 502, 503 or 504); a request EDH may have carried out reads `not confirmed` instead, as above
-   A launch stays `CREATING` far longer than usual

**Cause:**

The VAMS proxy could not complete a call to the EDH REST API within its budget (the API answers within 29 seconds, so long EDH operations are reported as unknown rather than waited on). A launch never waits on EDH in the API call itself: it is retried in the background twice within twenty minutes, and a third transient failure records `ORPHANED` with `LAUNCH_ERROR`, so a slow cluster shows as a long `CREATING` rather than as an error from `create`. A launch the worker never settled stays `CREATING` until the reconciler's sweep closes it as `ORPHANED` with `CREATE_TIMED_OUT` 30 minutes after the record's last write, or 5 minutes after it once all three launch attempts have started; it can be deleted by its bootstrap id once 15 minutes have passed since its last change (`vamscli edh workstation delete BOOTSTRAP_ID --confirm`).

**Resolution:**

1. Do not re-run a launch that is still `CREATING`; watch it with `vamscli edh workstation list`, selecting the row by its `bootstrapId` (the list shows its `sessionId` once EDH assigns one). A launch that ended `ORPHANED` is not retried by re-sending its `--bootstrap-id`: the API answers with the stored record unchanged and launches nothing. Delete the record and launch again without `--bootstrap-id` (a new key is generated); see [Launch Ended ORPHANED](#launch-ended-orphaned). Re-send a `create` with its original `--bootstrap-id` only when the first response was lost, to read the record it created instead of launching a second workstation:

    ```bash
    vamscli edh workstation delete BOOTSTRAP_ID --confirm
    vamscli edh workstation create --image-id 12 --instance-type g5.2xlarge --name "Design desk" --root-size-gb 200 --os-family linux
    ```

2. An administrator can confirm the proxy's view of the cluster:

    ```bash
    vamscli edh admin health
    ```

    A failed probe names the reason: `credentialRejected` after the EDH root key changed (see [Root key rotation](overview.md#root-key-rotation)), `unreachable` or `refused`.

3. If the health check fails persistently, the EDH cluster, its load balancer, or the network path from the VAMS add-on subnets is down; contact the cluster operator.

### Instance Type Listing Reports the Profile Unavailable

**Symptoms:**

-   `edh catalog instance-types` fails with `Instance type listing failed (502): the EDH cluster did not answer. EDH virtual desktop profile is unavailable`, while other `edh` commands work

**Cause:**

EDH answered, but none of the desktop profiles it returned has the id the deployment is configured with (`app.addons.useEdhWorkstations.vdi.virtualDesktopProfileId`), so VAMS has no instance types to offer. The message says the cluster did not answer because the CLI reports every HTTP 502 that way.

**Resolution:**

An administrator can compare the profiles EDH reports in `vamscli edh admin capacity --json-output` (`profiles`) with the configured profile id, and correct the configuration or the EDH profile.
