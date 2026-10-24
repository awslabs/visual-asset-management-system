"""MCP tools for the EDH Workstations add-on routes (``/addon/edh/*``).

``register`` is one of the modules ``addon_tools.register_addon_tools`` calls, so ``server.py``
registers these tools after its own and before a fork's. The add-on's handlers return no ``message``
envelope, and its list routes return every row in one ``{"Items": [...]}`` page (EDH itself does not
paginate), so these tools hand the body back directly rather than through ``paginate()``. Every EDH
tool is registered whether or not the deployment enabled the add-on, and opens with
``client.require_feature``: on a deployment without the add-on the tool answers the CLI's "not
enabled" message before any request is made. ``get_edh_workstation_connect`` sits with the writes: it
changes nothing, but it issues a desktop credential. No tool reaches the Windows sign-in of a
workstation.
"""

import uuid
from typing import Any, Callable, Dict, List, Optional

from vamscli.addon_constants import (
    EDH_AMI_RECIPES_DISABLED_MESSAGE,
    EDH_CONTAINERS_DISABLED_MESSAGE,
    EDH_WORKSTATIONS_DISABLED_MESSAGE,
    FEATURE_EDH_AMI_RECIPES,
    FEATURE_EDH_CONTAINERS,
    FEATURE_EDH_WORKSTATIONS,
)


def register(
    mcp: Any,
    client: Any,
    config: Any,
    tool_result: Callable[[Callable[..., Any]], Callable[..., Any]],
) -> None:
    """Register the EDH Workstations tools on ``mcp``: the reads always, the writes under
    ``config.enable_writes`` and the destructive tools under ``config.enable_destructive``."""

    # --- Read tools (always registered) -----------------------------------

    @mcp.tool()
    @tool_result
    def list_edh_workstations() -> Dict[str, Any]:
        """List the AUTHENTICATED user's EDH GPU workstations as `Items`. Each row carries `bootstrapId`
        (the launch's id, always present), `sessionId` (null until the launch worker, or else the
        reconciler's next sweep, reads it back from EDH), `userId`, `edhUid` (the EDH identity the
        desktop runs as), `name`, `state`
        (CREATING|ACTIVE|STOPPED|DELETED|ORPHANED), `activity` (what the workstation is doing whatever
        `state` says, derived on every read: `starting` from a start request until EDH reports the
        desktop connectable, so also on a STOPPED row whose start is in progress, a CREATING row with
        a `sessionId` and an ACTIVE row that is not connectable yet; `stopping` while EDH still stops
        the desktop; null otherwise), `edhState`, `lastError` (why a launch ended
        ORPHANED: USER_NOT_PROVISIONED, QUOTA_EXCEEDED, IMAGE_UNAVAILABLE, CAPACITY_UNAVAILABLE,
        EDH_REFUSED, LAUNCH_ERROR or CREATE_TIMED_OUT; LEASE_EXPIRED on a desktop the lease sweep stopped;
        on a STOPPED row, why its last start left it stopped: CAPACITY_UNAVAILABLE (AWS had no
        capacity for the instance type in the desktop's Availability Zone), RESERVATION_FULL (its
        capacity reservation was full) or START_FAILED (any other refusal, or a start that never
        took effect), kept until the next start is accepted; null otherwise), `startFailure` (for
        those three, a map of `errorCode` (the AWS error code EDH's refusal named, e.g.
        `InsufficientInstanceCapacity`), `availabilityZone` (the Availability Zone the desktop and
        its disk are in) and `observedAt` (when VAMS recorded it), the first two absent when not
        read; null otherwise),
        `edhFailure` (the launch failure VAMS captured from EDH's own record of a
        desktop that never ran, as `{event, errorCode, operation, checkpoint, observedAt}`: `event`
        (`placement_failed` or `session-failed`), `errorCode` and `operation` the AWS error code and
        the AWS operation EDH's status named, e.g. `InsufficientInstanceCapacity` from
        `CreateCapacityReservation`, `checkpoint` the desktop setup step a `session-failed` stopped at,
        each absent when not captured, and `observedAt` when VAMS captured it; null when none was
        captured. A capacity code makes `lastError` CAPACITY_UNAVAILABLE and a vCPU or instance limit
        QUOTA_EXCEEDED; EDH's own wording is never returned),
        `launchAttempts`, `imageId`, `osFamily`, `instanceType`, `rootSizeGb`, `createdAt`, `updatedAt`,
        `connectionAvailable`, and `leaseExpiresAt`, `leaseStoppedAt` and `leaseHoursAtLaunch` (null
        when no lease applies). Each row also names the image it was launched from: `imageName` (the
        EDH image name), `recipeId` (the AMI recipe whose build produced the image, null for a
        baseline image), `recipeDisplayName`, `recipeVersion` (the build version it runs),
        `latestRecipeVersion` (the recipe's newest AVAILABLE build version, derived on each read) and
        `imageLatest` (true on that newest build, false only when a newer AVAILABLE build exists, null
        for a baseline image, an unknown recipe or version, or a recipe with no AVAILABLE build), each
        null when not known; a workstation keeps the image it was launched from, so launch a new
        workstation to use a newer build. Every session tool takes `session_id` as the row's
        `sessionId`, or its `bootstrapId` while the row has no session id. Follow a launch here,
        selecting the row by its `bootstrapId`, which stays on the row after EDH assigns the
        `sessionId`. Another user's
        workstations are never included, so an empty list says nothing about whether they exist.
        Reading it makes EDH issue no connection token; `connectionAvailable` is true while EDH reports
        the desktop running. Not paginated: the list is complete."""
        client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
        return client.api.list_edh_workstations()

    @mcp.tool()
    @tool_result
    def get_edh_workstation(session_id: str) -> Dict[str, Any]:
        """Get one of the authenticated user's EDH workstations by `session_id` — the row's `sessionId`,
        or its `bootstrapId` for a launch that has not received one (state CREATING or ORPHANED). A
        `bootstrapId` is accepted only while the row has no `sessionId`: once EDH assigns one, the
        `bootstrapId` is reported as not found, so follow a launch with list_edh_workstations. A
        workstation owned by another user is reported as not found, not as forbidden. The record carries
        the fields of a list_edh_workstations row, `activity` (`starting`, `stopping` or null),
        `lastError`, `startFailure` (why the last start left a STOPPED row stopped; null when it did
        not) and `edhFailure` (the AWS error code and
        operation, or the setup checkpoint, VAMS captured from EDH for a failed launch; null when none
        was captured) among them. It carries the image fields `imageName`, `recipeId`,
        `recipeDisplayName`, `recipeVersion`, `latestRecipeVersion` and `imageLatest` as a
        list_edh_workstations row does. Reading it makes
        EDH issue no connection token; `connectionAvailable` is true while EDH reports the desktop
        running."""
        client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
        return client.api.get_edh_workstation(session_id)

    @mcp.tool()
    @tool_result
    def list_edh_catalog_images() -> Dict[str, Any]:
        """List the EDH desktop images the authenticated user may launch, as `Items`, filtered per image
        by the user's permissions. Each item carries `imageId`, `name`, `baseOs`, `osFamily`,
        `architecture` (the image's CPU architecture as EDH reports it, such as `x86_64` or `arm64`, or
        null when EDH reports none), `rootSize`, `description` and `category`: `amiRecipe` for an image
        an AMI recipe build registered, which also carries `recipeId`, `recipeDisplayName` and
        `recipeVersion` (the build version), and `baseline` for every other image. A recipe image whose
        recipe lists them also carries `launchInstanceTypes`: the instance types the image's recipe
        offers its desktops, in the recipe's order; the field is absent for every other image. Recipe
        images are listed first, then the baseline images, each group sorted by name. Use an item's
        `imageId` as `image_id` and its `osFamily` as `os_family` for create_edh_workstation, and
        choose its `instance_type` by the image's `architecture` and `launchInstanceTypes` (see
        list_edh_catalog_instance_types). Not paginated."""
        client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
        return client.api.list_edh_catalog_images()

    @mcp.tool()
    @tool_result
    def list_edh_catalog_instance_types() -> Dict[str, Any]:
        """List the EC2 instance types this deployment allows for EDH workstations, as `Items` of
        `{instanceType, architectures}` sorted by name with numbers in numeric order (`g5.2xlarge`
        before `g5.12xlarge`): the EDH desktop profile's types, narrowed to the deployment's allow-list
        when one is configured. `architectures` names the profile's architecture groups that list the
        type, in EDH's order, and is `[]` when the profile does not group its types. A type runs an
        image when the image's `architecture` is null, the type's `architectures` is empty, or it
        contains the image's architecture, and an image that carries `launchInstanceTypes` is offered
        only the types it lists; pass only such a type to `create_edh_workstation`, as
        `instance_type`. Answers 502 `EDH virtual desktop profile is unavailable` when EDH lists no
        desktop profile with the deployment's configured id. Not paginated."""
        client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
        return client.api.list_edh_catalog_instance_types()

    @mcp.tool()
    @tool_result
    def get_edh_catalog_lease() -> Dict[str, Any]:
        """The workstation lease policy as configured by the administrator, as
        `{"lease": {enabled, leaseHours, forced, graceHours}}` (no audit fields). When leases are
        disabled, `enabled` is false and the other fields are the stored policy values that would apply if
        leases were turned on. Not paginated."""
        client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
        return client.api.get_edh_catalog_lease()

    @mcp.tool()
    @tool_result
    def list_edh_catalog_capacity_reservations(
        instance_type: Optional[str] = None,
        os_family: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List the EC2 capacity reservations the authenticated user may launch an EDH workstation
        into, as `Items`: registered by an administrator, enabled, granted to the user's role, and
        active in EC2. Each item carries `capacityReservationId` (the value create_edh_workstation
        takes as `capacity_reservation_id`), `label`, `fallbackToOnDemand` (whether a launch into the
        reservation runs on-demand when it has no free slot), `enabled`, `instanceType`,
        `availabilityZone`, `instancePlatform` (`Linux/UNIX`, `Windows`, ...), `state`,
        `totalInstanceCount`, `availableInstanceCount`, `instanceMatchCriteria`, `endDate` (ISO-8601
        or null) and `reservationType` (`default` or `capacity-block`). `instance_type` keeps the
        reservations of that type, and `os_family` (`linux` or `windows`) those whose platform suits
        an image of that family; a launch takes a reservation only when both match. An empty list
        means every launch is on-demand, which is also the answer when EC2 could not be read. Not
        paginated."""
        client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
        return client.api.list_edh_catalog_capacity_reservations(
            instance_type=instance_type, os_family=os_family)

    @mcp.tool()
    @tool_result
    def list_edh_admin_capacity_reservations() -> Dict[str, Any]:
        """ADMIN: list every registered EC2 capacity reservation with EC2's live view of it, as
        `Items` shaped as list_edh_catalog_capacity_reservations items. `state` is EC2's, or
        `notFound` when EC2 no longer knows the id, or `unknown` when the read failed (the EC2 fields
        are then null). Requires the EDH admin routes; a user without them gets a 403. Not
        paginated."""
        client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
        return client.api.list_edh_admin_capacity_reservations()

    @mcp.tool()
    @tool_result
    def list_edh_admin_images() -> Dict[str, Any]:
        """ADMIN: list every EDH software-stack image registered on the cluster (desktop and
        simulation), as `Items`, filtered per image by the user's permissions. Each item carries `kind`
        (`vdi` or `simulation`), `imageId`, `stackName`, `amiId`, `baseOs`, `osFamily`, `architecture`
        (the CPU architecture EDH reports for the image, such as `x86_64` or `arm64`, or null),
        `rootSize`, `profileId` and `description`. Requires the EDH admin routes; a user without them
        gets a 403. Not paginated."""
        client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
        return client.api.list_edh_admin_images()

    @mcp.tool()
    @tool_result
    def list_edh_admin_instances() -> Dict[str, Any]:
        """ADMIN: list EVERY user's EDH workstation sessions on the cluster, as `Items`. Each item joins
        the EDH session with its VAMS row and carries `activity` (`starting`, `stopping` or null,
        derived as on a list_edh_workstations row) and that row's `lastError`, `startFailure` (why
        the last start left the desktop stopped, shaped as on a list_edh_workstations row; null when
        it did not) and `edhFailure` (the AWS
        error code and operation, or the setup checkpoint, VAMS captured from EDH for a failed launch,
        shaped as on a list_edh_workstations row; null for a session VAMS did not launch or captured
        no failure for). Each item also carries the image fields `imageName`, `recipeId`,
        `recipeDisplayName`, `recipeVersion`, `latestRecipeVersion` and `imageLatest`, shaped as on a
        list_edh_workstations row. Rows name the
        owning user, which is why this tool is not in the README's `autoApprove` sample. Not
        paginated."""
        client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
        return client.api.list_edh_admin_instances()

    @mcp.tool()
    @tool_result
    def list_edh_stale_instances(idle_minutes: Optional[int] = None) -> Dict[str, Any]:
        """ADMIN: list the workstation sessions in state STOPPED or ORPHANED whose state last changed
        more than `idle_minutes` ago (server default 120; `staleReason` `stopped`), plus sessions VAMS
        knows but EDH no longer lists (`staleReason` `missingInEdh`), as `Items` (the body also echoes
        the `idleMinutes` applied; each item carries `stale` and `staleReason`, and `edhFailure` as
        list_edh_admin_instances does, null when none was captured) — the candidates for
        deactivate_edh_admin_instance. ACTIVE desktops are never listed here, however long unused; see
        list_edh_admin_instances for them. Rows name the owning user, so this tool is not in
        `autoApprove`. Not paginated."""
        client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
        return client.api.list_edh_stale_instances(idle_minutes=idle_minutes)

    @mcp.tool()
    @tool_result
    def get_edh_capacity() -> Dict[str, Any]:
        """ADMIN: the EDH cluster's current workstation capacity summary, as EDH reports it."""
        client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
        return client.api.get_edh_capacity()

    @mcp.tool()
    @tool_result
    def get_edh_health() -> Dict[str, Any]:
        """ADMIN: whether the VAMS proxy can reach the EDH API with its configured credentials. Call
        this first when other edh tools return errors saying the EDH cluster did not answer. An error
        whose `error_type` is `EdhOutcomeUnknownError` ("... not confirmed") is not a health problem:
        EDH may have carried the request out, so call the matching list tool (list_edh_workstations,
        list_edh_admin_instances, list_edh_admin_images or list_edh_container_jobs) before retrying."""
        client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
        return client.api.get_edh_health()

    @mcp.tool()
    @tool_result
    def get_edh_admin_settings() -> Dict[str, Any]:
        """ADMIN: the workstation lease policy as configured by the administrator, as
        `{"lease": {enabled, leaseHours, forced, graceHours, updatedAt, updatedBy}}`. Includes
        `updatedBy` (the user who last changed it), which is why this tool is not in the README's
        `autoApprove` sample. `leaseHours` ranges 1-720; `graceHours` ranges 0-168. Not paginated."""
        client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
        return client.api.get_edh_admin_settings()

    @mcp.tool()
    @tool_result
    def list_edh_container_catalog() -> Dict[str, Any]:
        """List the container compute registered on the EDH cluster as `Items` — each a curated Batch
        job definition (`jobDefinitionName`, `jobDefinitionArn`, `revision`, `status`, `image`,
        `platformCapabilities`, `vcpus`, `memory`, `gpu`), Batch job queue (`jobQueueName`,
        `jobQueueArn`, `state`, `status`) or EKS cluster (`name`), tagged with `kind` (`jobDefinition`,
        `jobQueue` or `eksCluster`) — plus `eksEnabled`, whether this deployment accepts the `eks`
        backend. Job definitions are listed by name, not ARN; that name is the
        `job_definition` submit_edh_container_job takes and the one the deployment's permissions
        evaluate. On a deployment that has not enabled the add-on's container jobs the tool answers
        that they are not enabled, before any request."""
        client.require_feature(FEATURE_EDH_CONTAINERS, EDH_CONTAINERS_DISABLED_MESSAGE)
        return client.api.list_edh_container_catalog()

    @mcp.tool()
    @tool_result
    def list_edh_container_jobs(
        backend: Optional[str] = None,
        cluster: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List the authenticated user's EDH container jobs on one `backend` (`batch`, the server
        default, or the experimental `eks`) as `Items`, each with `jobId` (the value
        kill_edh_container_job takes), `jobName`, `status`, `backend`, `edhJobDefinition` and
        `jobHandle` beside the fields EDH reports. The `eks` backend addresses one `cluster` (required)
        and `namespace` (server default `default`); both are ignored for `batch`. Another user's jobs
        are never included, and a job on the other backend is not listed — query both to see every
        job. Not paginated: the list is complete."""
        client.require_feature(FEATURE_EDH_CONTAINERS, EDH_CONTAINERS_DISABLED_MESSAGE)
        return client.api.list_edh_container_jobs(backend=backend, cluster=cluster, namespace=namespace)

    @mcp.tool()
    @tool_result
    def list_edh_ami_recipes() -> Dict[str, Any]:
        """List all EDH AMI recipes as `Items`, each with its stored definition, `source` (`config` for
        a recipe seeded from the configuration, `admin` for one created through the API), `createdBy`
        / `updatedBy`, and `latestBuild` (its newest build row, or null before the first build). Not
        paginated: the list is complete. On a deployment that has not enabled AMI recipe builds the
        tool answers that they are not enabled, before any request."""
        client.require_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
        return client.api.list_edh_ami_recipes()

    @mcp.tool()
    @tool_result
    def get_edh_ami_recipe(recipe_id: str) -> Dict[str, Any]:
        """Get one EDH AMI recipe by its `recipeId`: the stored definition (inline components, parent
        image, build settings), `source`, `keepVersions`, `contentHash`, `latestVersion`,
        `imageBuilder` (the Image Builder resource ARNs of the last build), `createdBy` / `updatedBy`,
        and `latestBuild` (the newest build row, or null). It carries no build history; that is
        list_edh_ami_recipe_builds. On a deployment that has not enabled AMI recipe builds the tool
        answers that they are not enabled, before any request."""
        client.require_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
        return client.api.get_edh_ami_recipe(recipe_id)

    @mcp.tool()
    @tool_result
    def list_edh_ami_recipe_builds(recipe_id: str) -> Dict[str, Any]:
        """List every build of one EDH AMI recipe as `Items`, newest version first. Each build carries
        `version`, `status` (QUEUED, BUILDING, REGISTERING, AVAILABLE, REGISTER_FAILED, FAILED or
        RETIRED), `requestedBy`, `resolvedParentAmi`, `amiId`, `snapshotIds`, `edhStackName` (the
        recipe's `stackName` plus ` v<version>`), `edhStackId`, `error` where the build has one (an
        AVAILABLE build can carry one too), `registerAttempts` (automatic registration attempts that
        failed), `retireWarning` and `pruneWarning` where this build could not be deactivated or its AMI
        not deregistered, `amiInUseBy` (the instance list from the last successful in-use check: the
        ids, at most 20, of the Amazon EC2 instances that keep the AMI of a build outside `keepVersions`
        or of a retired build) and `amiRemovalPending` (true on a build an administrator retired whose
        AMI has not been deregistered yet), and the `requestedAt`, `startedAt`, `completedAt`,
        `registeredAt`, `retiredAt` and `amiDeregisteredAt` timestamps. Not paginated: the list is
        complete. On a deployment that has not enabled AMI recipe builds the tool answers that they are
        not enabled, before any request."""
        client.require_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
        return client.api.list_edh_ami_recipe_builds(recipe_id)

    # --- Write tools (require VAMS_ENABLE_WRITES=true) ---------------------

    if config.enable_writes:

        @mcp.tool()
        @tool_result
        def get_edh_workstation_connect(session_id: str) -> Dict[str, Any]:
            """Get the DCV connection details for one of the authenticated user's ACTIVE EDH workstations:
            `{sessionId, connectionUrl, osFamily}`. A workstation that is not connectable (CREATING,
            STOPPED, or without an EDH session) is refused with a 409 that is returned as the error.
            Each call makes EDH issue at most one connection token per request EDH answers, for this
            desktop; VAMS repeats a read once after a transport failure or a gateway error, which can make
            a second.

            `connectionUrl` is a CREDENTIAL: it carries a DCV session auth token, so anyone holding it can
            open the desktop until the token expires. Because it is returned as tool output it lands in
            the host's conversation log and telemetry. Issuing that credential is why this tool requires
            VAMS_ENABLE_WRITES although it changes nothing. It is never in `autoApprove`; relay the URL
            to the user once rather than repeating it, and prefer pointing the user at the VAMS web UI's
            Connect button when they can use it."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            return client.api.get_edh_workstation_connection(session_id)

        @mcp.tool()
        @tool_result
        def create_edh_workstation(
            image_id: str,
            instance_type: str,
            name: str,
            root_size_gb: int,
            os_family: str,
            bootstrap_id: Optional[str] = None,
            lease_hours: Optional[int] = None,
            capacity_reservation_id: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Launch a new EDH GPU workstation for the authenticated user. Returns the accepted session
            record (HTTP 202): `state` CREATING, `sessionId` null, `lastError` null, plus
            `sessionBootstrap` (provisioned|skipped|unavailable|disabled, the outcome of placing a
            session key for the desktop). The record names the image the launch uses in the image
            fields `imageName`, `recipeId`, `recipeDisplayName`, `recipeVersion`,
            `latestRecipeVersion` and `imageLatest`, as a list_edh_workstations row does. The launch
            itself runs asynchronously — poll
            list_edh_workstations and select the row by the returned `bootstrapId` until it reports
            ACTIVE and a `sessionId`; get_edh_workstation accepts the `bootstrapId` only while the row
            has no `sessionId`, and reports it not found once EDH assigns one. A launch EDH refuses ends
            ORPHANED with the reason in `lastError`, never as an error from this call.

            `image_id` and `os_family` (`linux` or `windows`, the row's `osFamily`) come from the chosen
            row of list_edh_catalog_images, `instance_type` from list_edh_catalog_instance_types (a type
            that runs the image's `architecture`), and `root_size_gb` is 10-2048; all five are required.
            `bootstrap_id` is the launch's idempotency key: a repeat by the same user returns the stored
            record (HTTP 200) and launches nothing, and a key another user's launch holds is refused
            (409). A uuid4 is generated when omitted, and re-sending the one from a call that timed out
            is how to retry without launching twice.
            `lease_hours` (1 up to the policy's `leaseHours`, see get_edh_catalog_lease) is honoured
            only when the admin lease policy is enabled and not forced, and is refused (400) while
            leases are disabled. Refused (400) `instanceType does not match the image's architecture`
            when the image's profile lists the type only under architectures of another family than the
            image's (`x86_64` and `i386` are one family), refused (400) `instanceType is not offered for
            this image` when the image's recipe lists `launchInstanceTypes` that leave the type out, and
            refused (403) for a session authenticated with a VAMS API key.
            `capacity_reservation_id` launches into a reservation from
            list_edh_catalog_capacity_reservations whose `instanceType` and platform match the launch;
            omitted, the launch is on-demand. It is refused (400) when it is not registered, not
            active, or of another instance type or platform, (403) when the user's role does not grant
            it, and (503) when EC2 could not be read. A full reservation whose `fallbackToOnDemand` is
            true launches on-demand (the record then carries `capacityReservationFallback` true);
            one whose `fallbackToOnDemand` is false is refused (409, `error_type`
            `EdhReservationFullError`, `code` `RESERVATION_FULL`): launch without it, or later.

            This starts real GPU compute and incurs cost until the workstation is stopped or deleted.
            Confirm with the user before calling it, and keep it out of `autoApprove`.
            """
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            payload: Dict[str, Any] = {
                "bootstrapId": bootstrap_id or str(uuid.uuid4()),
                "imageId": image_id,
                "instanceType": instance_type,
                "name": name,
                "rootSizeGb": root_size_gb,
                "osFamily": os_family,
            }
            if lease_hours is not None:
                payload["leaseHours"] = lease_hours
            if capacity_reservation_id is not None:
                payload["capacityReservationId"] = capacity_reservation_id
            return client.api.create_edh_workstation(payload)

        @mcp.tool()
        @tool_result
        def start_edh_workstation(session_id: str) -> Dict[str, Any]:
            """Start one of the authenticated user's STOPPED EDH workstations. The start is accepted
            (HTTP 202) and runs in the background: the tool returns the record at once, still
            STOPPED with `activity` `starting`, plus `sessionBootstrap`
            (provisioned|skipped|unavailable|disabled, the outcome of placing a fresh session key).
            The record carries the image fields `imageName`, `recipeId`, `recipeDisplayName`,
            `recipeVersion`, `latestRecipeVersion` and `imageLatest` as a list_edh_workstations row
            does: a start keeps the image the workstation was launched from.
            Follow it with list_edh_workstations (or get_edh_workstation): the row reads `activity`
            `starting` until it is ACTIVE with `activity` null. A start EDH refuses leaves the row
            STOPPED with `activity` null, `lastError` CAPACITY_UNAVAILABLE (AWS had no capacity for
            the instance type in the desktop's Availability Zone; the desktop keeps its disk there,
            so start it again later), RESERVATION_FULL (its capacity reservation is full) or
            START_FAILED, and `startFailure` `{errorCode, availabilityZone, observedAt}`; the
            desktop is never deleted for a refused start. An error whose `error_type` is
            `EdhOutcomeUnknownError` means the start was not confirmed and may still be under way:
            call list_edh_workstations before retrying. While EDH still lists the desktop stopped, a
            start is refused (409, `error_type` `EdhStartInProgressError`) when another start of it is
            being processed, within 3 minutes of one EDH did not confirm, while the reconciler revokes
            the stopped desktop's key, or while a lease renewal re-issues its key (up to 5 minutes):
            list the workstations until it shows the start, and if the list still shows it stopped
            after 5 minutes, start it again. This resumes
            real GPU compute and cost. Confirm with the user before calling it, and keep it out of
            `autoApprove`."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            return client.api.start_edh_workstation(session_id)

        @mcp.tool()
        @tool_result
        def renew_edh_workstation(session_id: str) -> Dict[str, Any]:
            """Renew one of the authenticated user's leased workstations: reset `leaseExpiresAt` to the
            full lease window from now and clear `leaseStoppedAt`. Returned record carries
            `leaseExpiresAt`, `leaseHoursAtLaunch`, `leaseStoppedAt` (null after a renewal), and
            `activity` and `startFailure` as a list_edh_workstations row does. It carries the image
            fields `imageName`, `recipeId`, `recipeDisplayName`, `recipeVersion`,
            `latestRecipeVersion` and `imageLatest` as that row does too. A renew
            of an ACTIVE workstation with no start in progress also tries to re-issue the desktop's
            session key and reports the outcome in `sessionBootstrap`
            (provisioned|skipped|unavailable|disabled, as for a start); the field is absent when no
            re-issue was attempted, as for a session authenticated with a VAMS API key, which renews
            the lease only. Refused (409) when leases are disabled, when the workstation has no lease,
            or when it is DELETED. This extends the workstation's lease and thereby its cost. Confirm
            with the user before calling it, and keep it out of `autoApprove`."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            return client.api.renew_edh_workstation(session_id)

        @mcp.tool()
        @tool_result
        def stop_edh_workstation(session_id: str) -> Dict[str, Any]:
            """Stop one of the authenticated user's ACTIVE EDH workstations; returns the updated record,
            whose `activity` reads `stopping` while EDH still stops the desktop (and whose
            `startFailure` is null). The record carries the image fields `imageName`, `recipeId`,
            `recipeDisplayName`, `recipeVersion`, `latestRecipeVersion` and `imageLatest` as a
            list_edh_workstations row does. Stopping interrupts whatever is running on the desktop's
            compute, and is reversible with start_edh_workstation. An `EdhOutcomeUnknownError` error
            means EDH did not confirm the stop; call list_edh_workstations before retrying. Confirm
            with the user before calling it, and keep it out of `autoApprove`."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            return client.api.stop_edh_workstation(session_id)

        @mcp.tool()
        @tool_result
        def register_edh_vdi_image(
            ami_id: str,
            stack_name: str,
            base_os: str,
            root_size: int,
            virtual_desktop_profile_id: int,
            launch_tenancy: Optional[str] = None,
            description: Optional[str] = None,
        ) -> Dict[str, Any]:
            """ADMIN: register an AMI as an EDH desktop (VDI) software stack so users can launch it.
            Returns `{kind, imageId, stackName, amiId, osFamily}` (HTTP 201); `imageId` is the new
            stack's id, read back from the catalog by stack name. A read-back that does not find the
            stack, or a project assignment that fails, is returned as the error with the stack left
            registered, and an `EdhOutcomeUnknownError` error means EDH did not confirm the
            registration: call list_edh_admin_images before registering again. Requires the EDH admin
            routes."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            payload: Dict[str, Any] = {
                "amiId": ami_id,
                "stackName": stack_name,
                "baseOs": base_os,
                "rootSize": root_size,
                "virtualDesktopProfileId": virtual_desktop_profile_id,
            }
            if launch_tenancy is not None:
                payload["launchTenancy"] = launch_tenancy
            if description is not None:
                payload["description"] = description
            return client.api.register_edh_vdi_image(payload)

        @mcp.tool()
        @tool_result
        def create_edh_ami_recipe(recipe: Dict[str, Any]) -> Dict[str, Any]:
            """ADMIN: create an EDH AMI recipe (`source` admin) from `recipe`, a dict in the shape of the
            CLI's recipe file: `recipeId`, `displayName`, `kind` (vdi|simulation), `osFamily`, `baseOs`
            (vdi only), `architecture`, `parentImage`, `buildInstanceType` (one instance type, or a list
            Image Builder chooses among by availability), `rootSizeGb`, `stackName`, `profileId`,
            `components`, and optional `launchInstanceTypes` (1-64 distinct instance types a
            desktop of the recipe's image may launch on; absent, null or empty offers every type the
            deployment allows; outside the content hash, so changing it starts no build), `parameters`,
            `keepVersions`, `recipeRevision`, `description` and `enabled`. Components are inline only —
            each `{arn}` or `{name, data}`; the CLI's `{file}`
            form is not read here. A recipe declaring `assets`, a `recipeId` already in use, a
            `stackName` another recipe uses, or a component document naming an undeclared placeholder
            or longer than 16000 characters once its placeholders are resolved is refused (409). A
            `buildInstanceType` or an `ami-` or `ssm:` `parentImage` that EC2 reports as running another
            architecture family than `architecture` (`x86_64` and `i386` are one family) is refused (400)
            with `buildInstanceType does not match the recipe's architecture` or `parentImage does not
            match the recipe's architecture`; a lookup that fails refuses nothing, and the build start
            checks again. To copy an existing recipe with its bundled files, call
            duplicate_edh_ami_recipe. No build starts; call start_edh_ami_recipe_build. Returns the
            created recipe (HTTP 201). Requires the EDH admin routes."""
            client.require_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
            return client.api.create_edh_ami_recipe(recipe)

        @mcp.tool()
        @tool_result
        def update_edh_ami_recipe(recipe_id: str, recipe: Dict[str, Any]) -> Dict[str, Any]:
            """ADMIN: replace an `admin` EDH AMI recipe with `recipe`, the full definition, validated as
            for create_edh_ami_recipe; `recipe_id` overrides any `recipeId` in it. A `config`
            recipe, a `stackName` another recipe uses, `assets` other than the ones a duplicated recipe
            already carries, or a definition whose component documents create would refuse, is refused
            (409). A `buildInstanceType` or `parentImage` that EC2 reports as running another
            architecture family than `architecture` is refused (400) with `buildInstanceType does not
            match the recipe's architecture` or `parentImage does not match the recipe's architecture`,
            as on create. The recipe keeps its builds, its bundled files and `duplicatedFrom`, and no
            build starts. Returns the updated recipe. Requires the EDH admin routes."""
            client.require_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
            return client.api.update_edh_ami_recipe(recipe_id, recipe)

        @mcp.tool()
        @tool_result
        def duplicate_edh_ami_recipe(
            source_recipe_id: str,
            recipe_id: str,
            display_name: Optional[str] = None,
            stack_name: Optional[str] = None,
            keep_versions: Optional[int] = None,
        ) -> Dict[str, Any]:
            """ADMIN: duplicate an EDH AMI recipe, `config` (shipped) or `admin`, into a new `admin`
            recipe `recipe_id` that update_edh_ami_recipe can then change. Reads `source_recipe_id` and
            creates the copy with its definition, its `launchInstanceTypes` included, and `duplicateOf`;
            the service copies the source's
            bundled files (`assets` and `assetPrefix`), which the copy cannot change, and records
            `duplicatedFrom`. `display_name` defaults to the source's `displayName` plus ` (copy)`,
            `stack_name` to its `stackName` plus ` Copy`, and `keep_versions`, the number of AMIs
            pruning keeps for the copy (1-20; another value is refused (400)), to its `keepVersions`.
            A `recipe_id` already in use, or a stack name another recipe uses, is refused (409); a
            source that does not exist is reported as not found (404). A copy whose
            `buildInstanceType` or `parentImage` EC2 reports as running another architecture family
            than its `architecture` is refused (400) with `buildInstanceType does not match the
            recipe's architecture` or `parentImage does not match the recipe's architecture`. The
            caller must be allowed to read the source and create the copy. No build starts; call
            start_edh_ami_recipe_build. Returns the created recipe (HTTP 201). Requires the EDH admin
            routes."""
            client.require_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
            return client.api.duplicate_edh_ami_recipe(
                source_recipe_id, recipe_id, display_name=display_name, stack_name=stack_name,
                keep_versions=keep_versions)

        @mcp.tool()
        @tool_result
        def start_edh_ami_recipe_build(recipe_id: str) -> Dict[str, Any]:
            """ADMIN: start a new Image Builder build for an EDH AMI recipe. Launches an EC2 build
            instance that is billed until the image completes. Answers 202 with the build record and an
            `outcome`: `started` (status BUILDING), or `queued` (status QUEUED) when the recipe already
            has a BUILDING or REGISTERING build of other content. When the recipe's current content is
            already QUEUED, BUILDING or REGISTERING it answers 200 with that build and the outcome
            `in-progress`, and starts nothing, so a repeated call does not build the content twice.
            Registration in EDH, retirement of the recipe's other AVAILABLE builds' stacks and pruning
            of AMIs beyond `keepVersions` follow automatically; follow them with
            list_edh_ami_recipe_builds. Refused (409) for a disabled recipe; a build that starts at once
            is also refused, with nothing started, for a parent image that cannot be resolved, a parent
            AMI EC2 does not find (`the parent image could not be found`), a parent or
            `buildInstanceType` EC2 reports as running another architecture family than the recipe's
            `architecture` (`the parent image's architecture does not match the recipe's architecture`,
            `buildInstanceType does not match the recipe's architecture`), or a component placeholder
            the recipe does not declare. A queued build is checked when it is
            promoted, by the sweep or by this call when nothing is ahead of it (which then answers
            `started` with that build, or 409 with the reason); one that fails the check stays QUEUED
            while the sweep retries the promotion. Confirm with the user before calling it, and keep it
            out of `autoApprove`. Requires the EDH admin routes."""
            client.require_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
            return client.api.start_edh_ami_recipe_build(recipe_id)

        @mcp.tool()
        @tool_result
        def register_edh_ami_recipe_build(recipe_id: str, version: int) -> Dict[str, Any]:
            """ADMIN: retry the EDH registration of a build whose `status` is REGISTER_FAILED. A build
            registers automatically when its image completes, and the sweep retries a failed
            registration on its own until five automatic attempts have failed; this retry resets that
            count. The AMI is tagged again and registered as an EDH software stack; a build in any other
            status, or one whose AMI has been deregistered, answers a conflict, so read
            list_edh_ami_recipe_builds first. A successful retry makes the build AVAILABLE and retires
            the recipe's other AVAILABLE builds, a newer one included. Returns the updated build record
            with HTTP 200 whether the retry succeeded or failed again, so read its `status` (AVAILABLE,
            or REGISTER_FAILED with the reason in `error`). Requires the EDH admin routes."""
            client.require_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
            return client.api.register_edh_ami_recipe_build(recipe_id, version)

        @mcp.tool()
        @tool_result
        def register_edh_simulation_image(
            ami_id: str,
            stack_name: str,
            root_size: int,
            target_node_profile_id: int,
            target_node_user_data_id: Optional[int] = None,
            user_data_variables: Optional[str] = None,
            description: Optional[str] = None,
        ) -> Dict[str, Any]:
            """ADMIN: register an AMI as an EDH simulation (target node) software stack. Returns
            `{kind, imageId, stackName, amiId, osFamily}` (HTTP 201); `imageId` is the new stack's id,
            read back from the catalog by stack name. A read-back that does not find the stack, or a
            project assignment that fails, is returned as the error with the stack left registered, and
            an `EdhOutcomeUnknownError` error means EDH did not confirm the registration: call
            list_edh_admin_images before registering again. `target_node_user_data_id` names the
            EDH target-node user-data template (a positive integer) and is sent only when given; the
            server defaults to template 1. Requires the EDH admin routes."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            payload: Dict[str, Any] = {
                "amiId": ami_id,
                "stackName": stack_name,
                "rootSize": root_size,
                "targetNodeProfileId": target_node_profile_id,
            }
            if target_node_user_data_id is not None:
                payload["targetNodeUserDataId"] = target_node_user_data_id
            if user_data_variables is not None:
                payload["userDataVariables"] = user_data_variables
            if description is not None:
                payload["description"] = description
            return client.api.register_edh_simulation_image(payload)

        @mcp.tool()
        @tool_result
        def stop_edh_admin_instance(session_id: str) -> Dict[str, Any]:
            """ADMIN: stop ANY user's ACTIVE EDH workstation session by `session_id`, reclaiming its
            GPU; the owner can start it again. This acts on another user's running compute, so confirm
            with the user before calling it and keep it out of `autoApprove`. list_edh_admin_instances
            lists the candidates; a session EDH no longer lists is refused (409), and
            deactivate_edh_admin_instance reclaims its record. An `EdhOutcomeUnknownError` error means
            EDH did not confirm the stop; call list_edh_admin_instances before retrying."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            return client.api.stop_edh_admin_instance(session_id)

        @mcp.tool()
        @tool_result
        def update_edh_admin_settings(
            enabled: Optional[bool] = None,
            lease_hours: Optional[int] = None,
            forced: Optional[bool] = None,
            grace_hours: Optional[int] = None,
        ) -> Dict[str, Any]:
            """ADMIN: update the workstation lease policy. Only the arguments you pass are changed.
            Returns `{"lease": {enabled, leaseHours, forced, graceHours, updatedAt, updatedBy}}`.
            `lease_hours` ranges 1-720 (hours, server default 8); `grace_hours` ranges 0-168 (hours,
            server default 24). Rejected (400) on a range violation. This is a partial patch: the backend
            re-stamps `updatedAt`/`updatedBy` for every accepted change, so an empty call is refused here
            before the API to avoid meaningless audit entries."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            data: Dict[str, Any] = {}
            if enabled is not None:
                data["enabled"] = enabled
            if lease_hours is not None:
                data["leaseHours"] = lease_hours
            if forced is not None:
                data["forced"] = forced
            if grace_hours is not None:
                data["graceHours"] = grace_hours
            if not data:
                raise ValueError("Pass at least one of enabled, lease_hours, forced, grace_hours")
            return client.api.update_edh_admin_settings(data)

        @mcp.tool()
        @tool_result
        def register_edh_capacity_reservation(
            capacity_reservation_id: str,
            label: str,
            fallback_to_on_demand: Optional[bool] = None,
            enabled: Optional[bool] = None,
        ) -> Dict[str, Any]:
            """ADMIN: register an EC2 capacity reservation (`cr-` and 17 hex digits, in the deployment's
            account and Region) so users whose role grants it can launch EDH workstations into it.
            `label` (1-64 letters, digits, spaces, hyphens, periods or underscores) is what the launch
            wizard shows. `fallback_to_on_demand` (server default true) decides what a launch into the
            full reservation does: run on-demand, or be refused; `enabled` (server default true)
            whether it is offered at all. Returns the registered reservation as
            list_edh_admin_capacity_reservations shows it. Refused (400) when EC2 does not find the
            reservation, or 100 are already registered, and (409) when it is already registered. The
            registry is VAMS's own: this never changes the reservation in EC2."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            payload: Dict[str, Any] = {"capacityReservationId": capacity_reservation_id, "label": label}
            if fallback_to_on_demand is not None:
                payload["fallbackToOnDemand"] = fallback_to_on_demand
            if enabled is not None:
                payload["enabled"] = enabled
            return client.api.register_edh_capacity_reservation(payload)

        @mcp.tool()
        @tool_result
        def update_edh_capacity_reservation(
            capacity_reservation_id: str,
            label: Optional[str] = None,
            fallback_to_on_demand: Optional[bool] = None,
            enabled: Optional[bool] = None,
        ) -> Dict[str, Any]:
            """ADMIN: change a registered capacity reservation's `label`, `fallback_to_on_demand` or
            `enabled`; only the arguments you pass are changed, and an empty call is refused here before
            the API. Returns the reservation as list_edh_admin_capacity_reservations shows it; an
            unregistered id answers not found (`EdhCapacityReservationNotFoundError`). Never changes
            the reservation in EC2."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            data: Dict[str, Any] = {}
            if label is not None:
                data["label"] = label
            if fallback_to_on_demand is not None:
                data["fallbackToOnDemand"] = fallback_to_on_demand
            if enabled is not None:
                data["enabled"] = enabled
            if not data:
                raise ValueError("Pass at least one of label, fallback_to_on_demand, enabled")
            return client.api.update_edh_capacity_reservation(capacity_reservation_id, data)

        @mcp.tool()
        @tool_result
        def submit_edh_container_job(
            job_definition: str,
            name: str,
            backend: str,
            job_queue: str,
            command: Optional[List[str]] = None,
            environment: Optional[Dict[str, str]] = None,
            vcpus: Optional[int] = None,
            memory: Optional[int] = None,
            gpu: Optional[int] = None,
        ) -> Dict[str, Any]:
            """Submit a container job to EDH on `backend` `batch` (AWS Batch) or `eks` (Amazon EKS,
            experimental; accepted only where list_edh_container_catalog reports `eksEnabled`).
            `job_definition` is a job definition's name from list_edh_container_catalog (for `eks`, the
            container image URI) and `job_queue` the Batch job queue (for `eks`, the EKS cluster name);
            `command` is the container command as a token list, `environment` a name→value map,
            `memory` in MiB (at least 512). Returns the job as EDH reports it (HTTP 202); its `jobId`,
            with the same `backend`, is what kill_edh_container_job takes. An `EdhOutcomeUnknownError`
            error means EDH did not confirm the submission and the job may be running: call
            list_edh_container_jobs before submitting again.

            This starts real compute and incurs cost until the job finishes or is killed. Confirm with
            the user before calling it, and keep it out of `autoApprove`.
            """
            client.require_feature(FEATURE_EDH_CONTAINERS, EDH_CONTAINERS_DISABLED_MESSAGE)
            payload: Dict[str, Any] = {
                "jobDefinition": job_definition,
                "jobQueue": job_queue,
                "name": name,
                "backend": backend,
            }
            if command is not None:
                payload["command"] = command
            if environment is not None:
                payload["environment"] = environment
            if vcpus is not None:
                payload["vcpus"] = vcpus
            if memory is not None:
                payload["memory"] = memory
            if gpu is not None:
                payload["gpu"] = gpu
            return client.api.submit_edh_container_job(payload)

    # --- Destructive tools (require VAMS_ENABLE_DESTRUCTIVE=true AND writes enabled)

    if config.enable_destructive:

        @mcp.tool()
        @tool_result
        def delete_edh_workstation(session_id: str) -> Dict[str, Any]:
            """PERMANENTLY delete one of the authenticated user's EDH workstations: the desktop and its
            session are removed. Irreversible. Consider stop_edh_workstation first — it releases the GPU
            and can be undone with start_edh_workstation. `session_id` may be the `bootstrapId` of a
            launch that never received a session id: an ORPHANED launch is deleted at once, a CREATING
            one only after 15 minutes without progress (younger → 409, returned as the error). The
            returned record carries `activity` and `startFailure` as a list_edh_workstations row does,
            and the image fields `imageName`, `recipeId`, `recipeDisplayName`, `recipeVersion`,
            `latestRecipeVersion` and `imageLatest` as that row does.
            An `EdhOutcomeUnknownError` error means EDH did not confirm the delete; call
            list_edh_workstations before retrying."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            return client.api.delete_edh_workstation(session_id)

        @mcp.tool()
        @tool_result
        def delete_edh_vdi_image(image_id: str) -> Dict[str, Any]:
            """ADMIN: PERMANENTLY unregister an EDH desktop (VDI) software stack by `image_id`, so users
            can no longer launch it. Irreversible; register_edh_vdi_image creates a new registration.
            An `EdhOutcomeUnknownError` error means EDH did not confirm the delete; call
            list_edh_admin_images before retrying."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            return client.api.delete_edh_vdi_image(image_id)

        @mcp.tool()
        @tool_result
        def delete_edh_simulation_image(image_id: str) -> Dict[str, Any]:
            """ADMIN: PERMANENTLY unregister an EDH simulation (target node) software stack by
            `image_id`. Irreversible; register_edh_simulation_image creates a new registration. An
            `EdhOutcomeUnknownError` error means EDH did not confirm the delete; call
            list_edh_admin_images before retrying."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            return client.api.delete_edh_simulation_image(image_id)

        @mcp.tool()
        @tool_result
        def delete_edh_ami_recipe(recipe_id: str) -> Dict[str, Any]:
            """ADMIN: PERMANENTLY delete an `admin` EDH AMI recipe, removing the Image Builder resources
            its last build recorded, the recipe and every build row; answers `recipeId`, `deleted` and
            `warnings` (resources that could not be removed). The AMIs, snapshots, Image Builder images
            and EDH stacks its builds still hold stay, and without the build rows they can no longer be
            retired or pruned, so first run retire_edh_ami_recipe_build on the AVAILABLE builds, and the
            REGISTER_FAILED builds whose EDH stack exists, that should go. A `config` recipe, one with a
            QUEUED, BUILDING or REGISTERING build, or one another delete is already removing, is refused
            (409), and so is a recipe while a build's AMI removal is pending: a RETIRED build with
            `amiRemovalPending`, or a RETIRED, REGISTER_FAILED or FAILED build outside the recipe's
            `keepVersions` newest AMIs that still holds its AMI, as while an Amazon EC2 instance uses
            the AMI (list_edh_ami_recipe_builds). Delete the recipe once the sweep has deregistered
            those AMIs (`amiDeregisteredAt`). Irreversible. Requires the EDH admin routes."""
            client.require_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
            return client.api.delete_edh_ami_recipe(recipe_id)

        @mcp.tool()
        @tool_result
        def retire_edh_ami_recipe_build(recipe_id: str, version: int) -> Dict[str, Any]:
            """ADMIN: retire an AVAILABLE or REGISTER_FAILED AMI recipe build: deactivates its EDH stack,
            then deregisters its AMI and snapshots and deletes its Image Builder image once no Amazon EC2
            instance uses the AMI. While an instance uses it, the build stays RETIRED with
            `amiRemovalPending` and `amiInUseBy` (the instance ids), and VAMS removes the AMI after the
            last instance is terminated, so existing desktops can still be started. Earlier builds are
            retired and pruned automatically, so this is mainly for the current build, and retiring the
            AVAILABLE build leaves the recipe with no launchable image. Any other status, or a build
            whose stack cannot be found, is refused (409). Irreversible. Returns the build record with
            status RETIRED. Requires the EDH admin routes."""
            client.require_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
            return client.api.retire_edh_ami_recipe_build(recipe_id, version)

        @mcp.tool()
        @tool_result
        def deactivate_edh_admin_instance(session_id: str) -> Dict[str, Any]:
            """ADMIN: force-deactivate ANY user's EDH workstation session — the desktop is terminated.
            Irreversible, and it acts on another user's compute; stop_edh_admin_instance is the
            reversible form. Confirm with the user before calling it. `session_id` may be the
            `bootstrapId` of a launch that never received a session id: a session EDH no longer lists
            (or an ORPHANED launch) is marked DELETED without an EDH call, and a CREATING launch
            younger than 15 minutes is refused (409, returned as the error). An `EdhOutcomeUnknownError`
            error means EDH did not confirm the deactivation; call list_edh_admin_instances before
            retrying."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            return client.api.deactivate_edh_admin_instance(session_id)

        @mcp.tool()
        @tool_result
        def unregister_edh_capacity_reservation(capacity_reservation_id: str) -> Dict[str, Any]:
            """ADMIN: unregister a capacity reservation, so VAMS no longer offers it for launches. The
            EC2 reservation itself is not changed (it stays billed until it expires or is cancelled in
            EC2), and workstations launched into it keep running and keep their
            `capacityReservationId`. register_edh_capacity_reservation registers it again; an
            unregistered id answers not found (`EdhCapacityReservationNotFoundError`)."""
            client.require_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
            return client.api.unregister_edh_capacity_reservation(capacity_reservation_id)

        @mcp.tool()
        @tool_result
        def kill_edh_container_job(
            job_id: str,
            backend: Optional[str] = None,
            cluster: Optional[str] = None,
            namespace: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Kill one of the authenticated user's running EDH container jobs by `job_id` — the `jobId`
            list_edh_container_jobs showed (a Batch job id, or an EKS job name) — on `backend` (`batch`,
            the server default, or `eks`, which also needs the `cluster` the job was listed under and
            its `namespace`, server default `default`). Irreversible: the job's compute stops and it
            cannot be resumed; submit_edh_container_job starts a new one. Another user's job, or a job
            on the other backend, is reported as not found. Returns `jobId`, `backend` and EDH's
            `message` (plus `cluster` and `namespace` for `eks`). An `EdhOutcomeUnknownError` error
            means EDH did not confirm the kill; call list_edh_container_jobs before retrying."""
            client.require_feature(FEATURE_EDH_CONTAINERS, EDH_CONTAINERS_DISABLED_MESSAGE)
            return client.api.kill_edh_container_job(
                job_id, backend=backend, cluster=cluster, namespace=namespace)
