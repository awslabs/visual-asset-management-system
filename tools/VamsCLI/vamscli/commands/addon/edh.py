# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""EDH Workstations add-on commands for VamsCLI."""

import json
import sys
import time
import uuid
import click
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlsplit

from ...addon_constants import (
    FEATURE_EDH_WORKSTATIONS, FEATURE_EDH_CONTAINERS, FEATURE_EDH_AMI_RECIPES,
    EDH_WORKSTATIONS_DISABLED_MESSAGE, EDH_CONTAINERS_DISABLED_MESSAGE, EDH_AMI_RECIPES_DISABLED_MESSAGE,
)
from ...utils.decorators import requires_setup_and_auth, requires_feature, get_profile_manager_from_context
from ...utils.api_client import APIClient
from ...utils.json_output import output_status, output_result, output_error, output_warning, output_info
from ...utils.addon.edh_exceptions import (
    EdhError, EdhWorkstationNotFoundError, EdhImageNotFoundError, EdhContainerJobNotFoundError,
    EdhAmiRecipeNotFoundError, EdhAmiRecipeBuildNotFoundError, EdhReservationFullError,
    EdhReservationNotPermittedError, EdhCapacityReservationNotFoundError, EdhStartFailedError, EdhStartStalledError,
    EdhStartWaitTimeoutError,
    EdhUnavailableError,
)
from ...utils.addon.edh_api_client import EDH_CAPACITY_RESERVATION_ID, EDH_CAPACITY_RESERVATION_ID_MESSAGE

# ROOT_SIZE_GB_MAX bounds every root volume, in GiB; ROOT_SIZE_GB_MIN is the workstation model's floor
# (image stacks accept from 1).
ROOT_SIZE_GB_MIN = 10
ROOT_SIZE_GB_MAX = 2048

# The bounds of an AMI recipe's keepVersions, the number of AMIs pruning keeps.
KEEP_VERSIONS_MIN = 1
KEEP_VERSIONS_MAX = 20

# The catalog categories under the names the web launch wizard groups them by.
CATALOG_CATEGORY_LABELS = {'amiRecipe': 'Custom AMI Recipe', 'baseline': 'Baseline Image'}

# The build outcome the service answers when the recipe's content is already queued or building.
AMI_BUILD_IN_PROGRESS = 'in-progress'

# The container job backends, and the least memory a job may ask for, in MiB.
CONTAINER_BACKENDS = ('batch', 'eks')
MIN_JOB_MEMORY_MIB = 512

# A capacity reservation label: the display-name charset, 1-64 characters, as the request model takes it.
CAPACITY_RESERVATION_LABEL_MAX = 64

# What the Capacity line says for a launch that asked for a full reservation and ran on-demand, and the
# fallback reason of a stopped workstation VAMS moved to on-demand because its reservation ended; the
# web Capacity detail says the same (web/src/addons/edh/capacityDisplayCases.json).
ON_DEMAND_FALLBACK_TEXT = 'On-demand (reservation full)'
FALLBACK_REASON_ENDED = 'ended'


def on_demand_ended_text(reservation: Any) -> str:
    """The Capacity line of a workstation moved to on-demand because its reservation ended: the
    reservation by label, else by id, else unnamed."""
    if isinstance(reservation, str) and reservation:
        return f'On-demand (reservation {reservation} ended)'
    return 'On-demand (reservation ended)'

# The remedies printed with a launch refused because its capacity reservation is full.
RESERVATION_FULL_LAUNCH_HINT = (
    "Launch without --capacity-reservation-id for an on-demand workstation, choose another reservation "
    "('vamscli edh catalog capacity-reservations'), or try again once a slot is free.")
CAPACITY_RESERVATION_LISTING_HINT = "Use 'vamscli edh admin capacity-reservation list' to see the registry."
# The remedies printed with a launch refused because the role may see the reservation but not launch
# into it (403 RESERVATION_NOT_PERMITTED).
RESERVATION_NOT_PERMITTED_LAUNCH_HINT = (
    "Launch without --capacity-reservation-id for an on-demand workstation, choose a reservation whose "
    "LAUNCH column reads yes in 'vamscli edh catalog capacity-reservations', or ask an administrator to "
    "grant your role launching into this one.")

# The catalog's LAUNCH column: whether the role may launch into the reservation, not only see it.
LAUNCH_ALLOWED_TEXT = 'yes'
LAUNCH_VIEW_ONLY_TEXT = 'view only'

# `start --wait` reads the workstation every START_WAIT_POLL_SECONDS until it is ACTIVE, the start has
# left it stopped, or START_WAIT_TIMEOUT_SECONDS have passed. The clock and the sleep are module names
# so the tests can replace them.
START_WAIT_POLL_SECONDS = 10
START_WAIT_TIMEOUT_SECONDS = 600
_monotonic = time.monotonic
_sleep = time.sleep

# The words the State column prints for a record's `activity`, whatever its state; the web's status
# badge reads Starting and Stopping for the same records (web/src/addons/edh/workstationStatusCases.json).
ACTIVITY_LABELS = {'starting': 'STARTING', 'stopping': 'STOPPING'}

# The lastError codes a start the service could not carry out leaves on a STOPPED workstation, and the
# sentences the web shows beneath its badge for the two that do not name the instance type.
START_FAILURE_CODES = ('CAPACITY_UNAVAILABLE', 'RESERVATION_FULL', 'RESERVATION_ENDED', 'START_FAILED')
RESERVATION_FULL_START_MESSAGE = (
    "The capacity reservation has no free capacity; the workstation stays stopped. Start it again once "
    "a slot is free, or ask an administrator.")
# A start VAMS refused because the workstation's reservation has ended and VAMS could not move the
# stopped instance to on-demand capacity; a start that could move it runs on-demand instead.
RESERVATION_ENDED_START_MESSAGE = (
    "The capacity reservation this workstation was launched into has ended, and VAMS could not move the "
    "workstation to on-demand capacity. Ask an administrator.")
START_FAILED_MESSAGE = (
    "The workstation did not start; it is still stopped with its disk. Start it again, or ask an "
    "administrator.")
# A start EDH accepted and has not finished after 30 minutes: the session sweep records lastError
# START_STALLED on the row, which stays CREATING with no activity. The State column prints STALLED
# (the web's badge reads Start stalled) and `get` prints the web's sentence (D16).
START_STALLED_CODE = 'START_STALLED'
STALLED_LABEL = 'STALLED'
START_STALLED_MESSAGE = (
    "EDH has not finished starting this workstation after 30 minutes. Stop it, then start it again; if "
    "it stalls again, ask your administrator.")
# A running workstation whose remote display session has ended (D20): the record carries
# connectionUnavailableReason DISPLAY_SESSION_ENDED, `get` prints the sentence the web shows under its inert
# Connect, and `connect` is refused (409) with it.
DISPLAY_SESSION_ENDED_CODE = 'DISPLAY_SESSION_ENDED'
DISPLAY_SESSION_ENDED_MESSAGE = (
    "The workstation is running but its remote display session has ended. Stop it, then start it again, "
    "to reconnect.")

# The `get` record's label column: a continuation line of a value starts under the value.
RECORD_VALUE_INDENT = ' ' * 19


def is_start_stalled(record: Dict[str, Any]) -> bool:
    """Whether the record is a start EDH accepted and has not finished: CREATING, with lastError
    START_STALLED and no activity (one that reports an activity is moving again)."""
    return (record.get('state') == 'CREATING' and record.get('lastError') == START_STALLED_CODE
            and not record.get('activity'))


def state_label(record: Dict[str, Any]) -> str:
    """What the State column prints for a workstation: STARTING or STOPPING while the record's
    `activity` says so, STALLED for a start the sweep found stalled, otherwise its state."""
    activity = record.get('activity')
    if activity in ACTIVITY_LABELS:
        return ACTIVITY_LABELS[activity]
    if is_start_stalled(record):
        return STALLED_LABEL
    return str(record.get('state') or 'N/A')


def capacity_start_failure_message(instance_type: Any, availability_zone: Any) -> str:
    """The sentence for a start AWS had no on-demand capacity for. A stopped desktop keeps its root
    disk in its instance's Availability Zone, so it can start only there."""
    instance = instance_type if isinstance(instance_type, str) and instance_type else "its instance type"
    zone = f" ({availability_zone})" if isinstance(availability_zone, str) and availability_zone else ""
    return (f"AWS had no capacity for {instance} in this workstation's Availability Zone{zone} when it "
            "was started. A stopped workstation keeps its disk and starts only in that zone, so it "
            "stays stopped; start it again later.")


def describe_start_failure(record: Dict[str, Any]) -> Optional[Tuple[str, Optional[str]]]:
    """Why the last start left a STOPPED workstation stopped, as the sentence and the AWS detail line
    (`AWS: <errorCode> in <Availability Zone>`, or the code alone; None when the failure names no
    code) the web shows beneath its badge. None for a workstation whose start did not fail, and while a
    new start of it runs. A start the sweep found stalled (is_start_stalled) gets the START_STALLED
    sentence and no detail line: EDH reported no AWS error."""
    if is_start_stalled(record):
        return START_STALLED_MESSAGE, None
    if record.get('state') != 'STOPPED' or record.get('activity'):
        return None
    code = record.get('lastError')
    if code not in START_FAILURE_CODES:
        return None
    failure = record.get('startFailure') if isinstance(record.get('startFailure'), dict) else {}
    zone = failure.get('availabilityZone')
    if code == 'CAPACITY_UNAVAILABLE':
        message = capacity_start_failure_message(record.get('instanceType'), zone)
    elif code == 'RESERVATION_FULL':
        message = RESERVATION_FULL_START_MESSAGE
    elif code == 'RESERVATION_ENDED':
        message = RESERVATION_ENDED_START_MESSAGE
    else:
        message = START_FAILED_MESSAGE
    error_code = failure.get('errorCode')
    detail = None
    if isinstance(error_code, str) and error_code:
        detail = f"AWS: {error_code}" + (f" in {zone}" if isinstance(zone, str) and zone else "")
    return message, detail


def start_wait_outcome(record: Any) -> Optional[str]:
    """`active` once the workstation runs (ACTIVE with no activity), `stalled` once the sweep found
    the start stalled (is_start_stalled), `failed` once the start is over without it (STOPPED, DELETED
    or ORPHANED with no activity), None while it is still starting."""
    if not isinstance(record, dict):
        return None
    if record.get('activity'):
        return None
    state = record.get('state')
    if state == 'ACTIVE':
        # A service that predates `activity` reports a desktop that is not connectable yet as ACTIVE.
        if 'activity' not in record and record.get('connectionAvailable') is False:
            return None
        return 'active'
    if is_start_stalled(record):
        return 'stalled'
    if state == 'CREATING':
        return None
    return 'failed'


def wait_for_start(api_client: APIClient, session_id: str, record: Dict[str, Any],
                   json_output: bool) -> Tuple[str, Dict[str, Any]]:
    """Read the workstation every START_WAIT_POLL_SECONDS until start_wait_outcome settles or
    START_WAIT_TIMEOUT_SECONDS pass; returns the outcome (`active`, `failed` or `timedOut`) and the last
    record read. A read the cluster did not answer is tried again at the next poll."""
    deadline = _monotonic() + START_WAIT_TIMEOUT_SECONDS
    shown = state_label(record)
    output_status(f"  {shown}", json_output)
    while True:
        outcome = start_wait_outcome(record)
        if outcome:
            return outcome, record
        remaining = deadline - _monotonic()
        if remaining <= 0:
            return 'timedOut', record
        _sleep(min(START_WAIT_POLL_SECONDS, remaining))
        try:
            record = api_client.get_edh_workstation(session_id)
        except EdhUnavailableError:
            continue
        if state_label(record) != shown:
            shown = state_label(record)
            output_status(f"  {shown}", json_output)


def _capacity_reservation_id(ctx, param, value):
    """Click callback: an EC2 capacity reservation id (cr- and 17 hex digits), or None when absent."""
    if value is None or EDH_CAPACITY_RESERVATION_ID.fullmatch(value):
        return value
    raise click.BadParameter(EDH_CAPACITY_RESERVATION_ID_MESSAGE)


def _capacity_reservation_label(ctx, param, value):
    """Click callback: a label of 1-64 display-name characters, or None when absent."""
    if value is None:
        return value
    label = value.strip()
    if not label or len(label) > CAPACITY_RESERVATION_LABEL_MAX or not all(
            ch.isascii() and (ch.isalnum() or ch in '-._ ') for ch in label):
        raise click.BadParameter(
            f"Label must be 1-{CAPACITY_RESERVATION_LABEL_MAX} letters, digits, spaces, hyphens, periods "
            f"or underscores")
    return label


def describe_capacity(record: Dict[str, Any]) -> Optional[str]:
    """The Capacity a workstation launched with: its reservation by label and id (the id alone once
    unregistered), that it ran on-demand because the reservation was full, that VAMS moved it to
    on-demand because its reservation ended (`capacityReservationFallbackReason` `ended`), or None for
    an on-demand launch. A fallback without a reason predates the field and was a full reservation's.
    The web Capacity detail formats the same fields (capacityDisplayCases.json)."""
    if record.get('capacityReservationFallback'):
        if record.get('capacityReservationFallbackReason') == FALLBACK_REASON_ENDED:
            return on_demand_ended_text(record.get('capacityReservationLabel')
                                        or record.get('capacityReservationRequested'))
        return ON_DEMAND_FALLBACK_TEXT
    reservation_id = record.get('capacityReservationId')
    if not reservation_id:
        return None
    label = record.get('capacityReservationLabel')
    return f"{label} ({reservation_id})" if label else str(reservation_id)


@click.group()
def edh():
    """EDH Workstations add-on: GPU desktops, image catalog, cluster administration, AMI recipes, and container jobs."""
    pass


def _api_client(ctx: click.Context) -> APIClient:
    """The APIClient for the profile this invocation resolved."""
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    return APIClient(config['api_gateway_url'], profile_manager)


def _confirmation_required(action: str, resource_key: str, resource_id: str, json_output: bool) -> None:
    """Refuse a destructive command that was run without --confirm, in either output mode.

    JSON mode exits directly after the one error object: main() renders a ClickException as a second
    JSON object, so raising would leave the caller two documents to parse.
    """
    if json_output:
        output_result({
            "error": "Confirmation required",
            "message": f"{action} requires the --confirm flag",
            resource_key: resource_id,
        }, json_output=True)
        sys.exit(1)
    output_warning(f"⚠️  {action} requires explicit confirmation!", json_output)
    output_info("Use --confirm to proceed.", json_output)
    raise click.ClickException(f"Confirmation required: {action.lower()}")


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def format_items(result: Dict[str, Any], noun: str) -> str:
    """Render an {"Items": [...]} body whose item shape is EDH's own, one JSON block per item."""
    items = result.get('Items', []) if isinstance(result, dict) else []
    if not items:
        return f"No {noun} found."
    blocks = []
    for item in items:
        if isinstance(item, dict):
            blocks.append(json.dumps(item, indent=2))
        else:
            blocks.append(f"  - {item}")
    return '\n'.join(blocks)


def describe_edh_failure(failure: Any) -> Optional[str]:
    """The text shown for the launch failure VAMS captured from EDH (a record's `edhFailure`): the AWS
    error code, with the operation that returned it when captured, else the checkpoint a desktop setup
    failed at; None when there is no failure or it names neither. The web formats the same fields."""
    if not isinstance(failure, dict):
        return None
    code, operation, checkpoint = (failure.get(key) for key in ('errorCode', 'operation', 'checkpoint'))
    if isinstance(code, str) and code:
        return f"{code} ({operation})" if isinstance(operation, str) and operation else code
    if isinstance(checkpoint, str) and checkpoint:
        return f"desktop setup failed at {checkpoint}"
    return None


def _edh_failure_line(item: Dict[str, Any]) -> Optional[str]:
    """The `EDH failure:` line of a record that carries a captured failure, in the record's label column."""
    detail = describe_edh_failure(item.get('edhFailure'))
    return f"  EDH failure:     {detail}" if detail else None


def format_admin_sessions(result: Dict[str, Any], noun: str) -> str:
    """The admin session listings: one JSON block per session, as format_items renders them, each
    followed by its `EDH failure:` line when the session carries a captured launch failure."""
    items = result.get('Items', []) if isinstance(result, dict) else []
    if not items:
        return f"No {noun} found."
    blocks = []
    for item in items:
        if not isinstance(item, dict):
            blocks.append(f"  - {item}")
            continue
        blocks.append(json.dumps(item, indent=2))
        line = _edh_failure_line(item)
        if line:
            blocks.append(line)
    return '\n'.join(blocks)


def _identifier(item: Dict[str, Any]) -> str:
    """The value the workstation commands accept for a row: its session id, or -- for a launch that has
    not received one -- its bootstrap id, marked so the reader can tell the two apart."""
    if item.get('sessionId'):
        return str(item['sessionId'])
    return f"{item.get('bootstrapId', 'N/A')}*"


# The states of a workstation that still exists and is not a failed launch: only these carry the
# newer-image suffix, as only these carry the web's newer-image badge.
NEWER_IMAGE_STATES = ('ACTIVE', 'STOPPED', 'CREATING')


def _version(value: Any) -> Optional[int]:
    """A build version the record carries, or None when it carries none (a bool is not a version)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def workstation_image_name(record: Dict[str, Any]) -> str:
    """The name shown for the image a workstation runs: the recipe's display name and the build version
    (`Isaac Sim 6.2 v13`) when the record carries both, else the record's image name, else the image
    id. The web shows the same name, falling back to its live catalog before the id
    (web/src/addons/edh/workstationImageCases.json)."""
    display_name = record.get('recipeDisplayName')
    version = _version(record.get('recipeVersion'))
    if isinstance(display_name, str) and display_name and version is not None:
        return f"{display_name} v{version}"
    image_name = record.get('imageName')
    if isinstance(image_name, str) and image_name:
        return image_name
    return str(record.get('imageId') or 'N/A')


def newer_image_suffix(record: Dict[str, Any]) -> str:
    """` (v<latest> available)` for an ACTIVE, STOPPED or CREATING workstation whose recipe has a newer
    AVAILABLE build than the one it runs (`imageLatest` false); empty otherwise. The web shows its
    `Newer image: v<latest>` badge for the same records."""
    if record.get('imageLatest') is not False or record.get('state') not in NEWER_IMAGE_STATES:
        return ''
    latest = _version(record.get('latestRecipeVersion'))
    if latest is None or _version(record.get('recipeVersion')) is None:
        return ''
    return f" (v{latest} available)"


def describe_recipe(record: Dict[str, Any]) -> Optional[str]:
    """The `Recipe:` value of a workstation whose image an AMI recipe built: the recipe's display name
    and id, the id alone without a display name; None for a baseline image or an unknown recipe."""
    recipe_id = record.get('recipeId')
    display_name = record.get('recipeDisplayName')
    if not (isinstance(recipe_id, str) and recipe_id):
        return display_name if isinstance(display_name, str) and display_name else None
    if isinstance(display_name, str) and display_name:
        return f"{display_name} ({recipe_id})"
    return recipe_id


def describe_recipe_version(record: Dict[str, Any]) -> Optional[str]:
    """The `Recipe version:` value: the build version, followed by `(latest)` when it is the recipe's
    newest AVAILABLE build or `(latest <m>)` when a newer one exists; the version alone when that is
    not known. None when the record carries no build version."""
    version = _version(record.get('recipeVersion'))
    if version is None:
        return None
    latest = _version(record.get('latestRecipeVersion'))
    if record.get('imageLatest') is True:
        return f"{version} (latest)"
    if record.get('imageLatest') is False and latest is not None:
        return f"{version} (latest {latest})"
    return str(version)


def format_workstation_list(result: Dict[str, Any]) -> str:
    items = result.get('Items', []) if isinstance(result, dict) else []
    if not items:
        return "No workstations found."
    images = [workstation_image_name(item) + newer_image_suffix(item) for item in items]
    # The Image column is as wide as its longest value, so a long recipe name keeps the table aligned.
    image_width = max(24, *(len(image) for image in images))
    header = (f"  {'Identifier':38s}  {'Name':24s}  {'State':10s}  {'Last Error':22s}  "
              f"{'Image':{image_width}s}  {'Instance':14s}  {'OS':8s}  Created")
    lines = [header, '-' * len(header)]
    session_less = False
    for item, image in zip(items, images):
        session_less = session_less or not item.get('sessionId')
        lines.append(
            f"  {_identifier(item):38s}  {str(item.get('name', 'N/A')):24s}  "
            f"{state_label(item):10s}  {str(item.get('lastError') or '-'):22s}  "
            f"{image:{image_width}s}  "
            f"{str(item.get('instanceType', 'N/A')):14s}  {str(item.get('osFamily', 'N/A')):8s}  "
            f"{item.get('createdAt', 'N/A')}"
        )
    if session_less:
        lines.append("")
        lines.append("  * bootstrap id: the launch has no EDH session id yet. Every workstation command "
                     "accepts it in place of SESSION_ID.")
    return '\n'.join(lines)


def format_workstation(result: Dict[str, Any]) -> str:
    lines = []
    lines.append(f"  Session ID:      {result.get('sessionId') or '(not assigned yet)'}")
    lines.append(f"  Bootstrap ID:    {result.get('bootstrapId', 'N/A')}")
    lines.append(f"  Name:            {result.get('name', 'N/A')}")
    lines.append(f"  State:           {result.get('state', 'N/A')}")
    lines.append(f"  Activity:        {result.get('activity') or 'none'}")
    lines.append(f"  EDH State:       {result.get('edhState') or 'N/A'}")
    lines.append(f"  Last Error:      {result.get('lastError') or 'none'}")
    start_failure = describe_start_failure(result)
    if start_failure:
        message, detail = start_failure
        lines.append(f"  Start Failure:   {message}")
        if detail:
            lines.append(f"{RECORD_VALUE_INDENT}{detail}")
    edh_failure = _edh_failure_line(result)
    if edh_failure:
        lines.append(edh_failure)
    lines.append(f"  Launch Attempts: {result.get('launchAttempts', 0)}")
    lines.append(f"  Image:           {workstation_image_name(result)}")
    lines.append(f"  Image ID:        {result.get('imageId', 'N/A')}")
    recipe = describe_recipe(result)
    if recipe:
        lines.append(f"  Recipe:          {recipe}")
    recipe_version = describe_recipe_version(result)
    if recipe_version:
        lines.append(f"  Recipe version:  {recipe_version}")
    lines.append(f"  OS Family:       {result.get('osFamily', 'N/A')}")
    lines.append(f"  Instance Type:   {result.get('instanceType', 'N/A')}")
    capacity_text = describe_capacity(result)
    if capacity_text:
        lines.append(f"  Capacity:        {capacity_text}")
    lines.append(f"  Created At:      {result.get('createdAt', 'N/A')}")
    lines.append(f"  Updated At:      {result.get('updatedAt', 'N/A')}")
    lines.append(f"  Connectable:     {result.get('connectionAvailable', 'N/A')}")
    if result.get('connectionUnavailableReason') == DISPLAY_SESSION_ENDED_CODE:
        lines.append(f"  Connection:      {DISPLAY_SESSION_ENDED_MESSAGE}")
    lines.append(f"  Lease Expires:   {result.get('leaseExpiresAt') or 'none'}")
    lines.append(f"  Lease Stopped:   {result.get('leaseStoppedAt') or 'none'}")
    lines.append(f"  Lease Hours:     {result.get('leaseHoursAtLaunch') or 'N/A'}")
    if result.get('rootSizeGb') is not None:
        lines.append(f"  Root Size (GB):  {result.get('rootSizeGb')}")
    if result.get('sessionBootstrap'):
        lines.append(f"  Session Sign-in: {result.get('sessionBootstrap')}")
    return '\n'.join(lines)


def format_launch_accepted(result: Dict[str, Any]) -> str:
    """The accepted launch record, then what happens next: the launch itself runs asynchronously."""
    bootstrap_id = result.get('bootstrapId', 'N/A')
    return '\n'.join([
        format_workstation(result),
        "",
        "  The launch runs asynchronously. The session id is assigned once EDH has created the",
        "  desktop; until then the bootstrap id identifies this workstation:",
        f"    vamscli edh workstation get {bootstrap_id}",
        "  A launch EDH refuses ends in state ORPHANED with the reason under Last Error, and the AWS",
        "  error EDH reported, when VAMS captured one, under EDH failure.",
    ])


def format_start_accepted(result: Dict[str, Any]) -> str:
    """The accepted start record, then what happens next: the start itself runs in the background."""
    session_id = result.get('sessionId') or result.get('bootstrapId', 'N/A')
    return '\n'.join([
        format_workstation(result),
        "",
        f"  Start requested; the workstation is starting. Follow it with "
        f"`vamscli edh workstation get {session_id}`.",
    ])


def format_connect_output(result: Dict[str, Any]) -> str:
    """The connection details without the URL: connectionUrl carries a DCV session credential. A
    Windows sign-in is printed only when --show-windows-password asked for it."""
    host = urlsplit(str(result.get('connectionUrl', ''))).hostname or 'N/A'
    lines = []
    lines.append(f"  Session ID:  {result.get('sessionId', 'N/A')}")
    lines.append(f"  OS Family:   {result.get('osFamily', 'N/A')}")
    lines.append(f"  DCV Host:    {host}")
    lines.append("")
    lines.append("  The connection URL carries a session credential and is not printed in CLI mode.")
    lines.append("  Re-run with --json-output to receive the full connectionUrl.")
    sign_in = result.get('windowsSignIn')
    if sign_in:
        lines.append("")
        lines.append("  Windows sign-in (a credential: it is printed because --show-windows-password was given):")
        lines.append(f"  User Name:   {sign_in.get('username') or 'N/A'}")
        lines.append(f"  Password:    {sign_in.get('password') or 'sign in with your directory password'}")
    elif result.get('osFamily') == 'windows':
        lines.append("")
        lines.append("  Windows opens at its lock screen; add --show-windows-password for the sign-in.")
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# workstation
# ---------------------------------------------------------------------------


@edh.group()
def workstation():
    """Launch, inspect, connect to, stop, start and delete your EDH workstations."""
    pass


@workstation.command(name='list')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def workstation_list(ctx: click.Context, json_output: bool):
    """List your EDH workstations. The State column reads STARTING or STOPPING while a workstation
    is between states (its `activity`), STALLED for a start EDH accepted and has not finished after
    30 minutes (Last Error START_STALLED; stop it, then start it again), otherwise its state. The
    Image column names the image the
    workstation was launched from (the recipe's display name and build version, else the image name,
    else the image id) and, on an ACTIVE, STOPPED or CREATING workstation whose recipe has a newer
    available build, appends `(v<n> available)`; the workstation keeps its image, so launch a new one
    to use that build.

    Examples:
        vamscli edh workstation list
        vamscli edh workstation list --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving workstations...", json_output)

    try:
        result = api_client.list_edh_workstations()
        items = result.get('Items', []) if isinstance(result, dict) else []
        output_result(
            result,
            json_output,
            success_message=f"Found {len(items)} workstation(s)",
            cli_formatter=format_workstation_list,
        )
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@workstation.command(name='create')
@click.option('--image-id', required=True, help='Catalog image ID (see `edh catalog images`)')
@click.option('--instance-type', required=True,
              help="EC2 instance type; it must run the image's architecture, and a recipe image "
                   "that lists launch instance types takes one of them "
                   "(see 'vamscli edh catalog instance-types --image-id')")
@click.option('--name', required=True, help='Workstation name')
@click.option('--root-size-gb', required=True, type=click.IntRange(ROOT_SIZE_GB_MIN, ROOT_SIZE_GB_MAX),
              help=f'Root volume size in GiB ({ROOT_SIZE_GB_MIN}-{ROOT_SIZE_GB_MAX})')
@click.option('--os-family', required=True, type=click.Choice(['linux', 'windows']),
              help='Operating system family of the chosen image (its `osFamily` in `edh catalog images`)')
@click.option('--lease-hours', default=None, type=click.IntRange(1, 720),
              help='Optional lease in hours, 1 up to the lease policy\'s leaseHours (see `edh catalog '
                   'lease`). Honoured only when the admin lease policy is enabled and not forced; '
                   'overridden when forced; rejected when disabled or above leaseHours.')
@click.option('--bootstrap-id', default=None, type=click.UUID,
              help='Idempotency key (UUID); generated when omitted. Re-send it only when the create '
                   'response was lost: a repeat returns the existing record and launches nothing')
@click.option('--capacity-reservation-id', default=None, callback=_capacity_reservation_id,
              help='Optional registered capacity reservation to launch into (see `edh catalog '
                   'capacity-reservations`); its instance type and platform must match the launch. '
                   'Omitted: an on-demand launch')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def create(ctx: click.Context, image_id: str, instance_type: str, name: str, root_size_gb: int,
           os_family: str, lease_hours: Optional[int], bootstrap_id: Optional[uuid.UUID],
           capacity_reservation_id: Optional[str], json_output: bool):
    """Launch a new EDH workstation.

    The launch is accepted asynchronously (HTTP 202) and runs in the background: the returned record
    is CREATING with no session id yet. Follow the launch in `edh workstation list`, selecting the
    row by its bootstrap id, until it reports ACTIVE and a session id; `edh workstation get
    BOOTSTRAP_ID` answers only until the session id exists. A launch EDH refuses ends ORPHANED with
    the reason in lastError. --os-family is the chosen image's family as `edh catalog images` lists it.
    With --capacity-reservation-id the launch goes into that reservation; when it is full, a
    reservation that falls back launches on-demand and one that does not is refused (409,
    RESERVATION_FULL); one your role may see but not launch into (LAUNCH `view only` in `edh catalog
    capacity-reservations`) is refused with 403 RESERVATION_NOT_PERMITTED. Launching is refused for a
    session authenticated with a VAMS API key.

    Examples:
        vamscli edh workstation create --image-id 12 --instance-type g5.2xlarge --name "Design desk" --root-size-gb 200 --os-family linux
        vamscli edh workstation create --image-id 12 --instance-type g5.2xlarge --name "Design desk" --root-size-gb 200 --os-family windows --json-output
        vamscli edh workstation create --image-id 12 --instance-type g5.16xlarge --name "Render desk" --root-size-gb 200 --os-family linux --capacity-reservation-id cr-0123456789abcdef0
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    data: Dict[str, Any] = {
        'bootstrapId': str(bootstrap_id) if bootstrap_id else str(uuid.uuid4()),
        'imageId': image_id,
        'instanceType': instance_type,
        'name': name,
        'rootSizeGb': root_size_gb,
        'osFamily': os_family,
    }

    if lease_hours is not None:
        data['leaseHours'] = lease_hours
    if capacity_reservation_id is not None:
        data['capacityReservationId'] = capacity_reservation_id

    output_status(f"Launching workstation '{name}'...", json_output)

    try:
        result = api_client.create_edh_workstation(data)
        output_result(
            result,
            json_output,
            success_message="Workstation launch accepted (HTTP 202)",
            cli_formatter=format_launch_accepted,
        )
    except EdhReservationFullError as e:
        output_error(e, json_output, error_type="Capacity Reservation Full",
                     helpful_message=RESERVATION_FULL_LAUNCH_HINT)
        raise click.ClickException(str(e))
    except EdhReservationNotPermittedError as e:
        output_error(e, json_output, error_type="Capacity Reservation Not Permitted",
                     helpful_message=RESERVATION_NOT_PERMITTED_LAUNCH_HINT)
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@workstation.command(name='get')
@click.argument('session_id')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def workstation_get(ctx: click.Context, session_id: str, json_output: bool):
    """Show one of your workstations by SESSION_ID.

    SESSION_ID is the workstation's session id, or the bootstrap id of a launch that has not
    received one. A workstation that belongs to another user is reported as not found. A launch that
    ended ORPHANED shows its reason under Last Error and, when VAMS captured the failure from EDH
    (the record's edhFailure), the AWS error code and operation, or the desktop setup checkpoint,
    under EDH failure. Activity is `starting` or `stopping` while the workstation is between states
    (none otherwise), and a start that left it STOPPED shows why under Start Failure, with the AWS
    error and Availability Zone the start recorded (the record's startFailure); a start EDH accepted
    and has not finished after 30 minutes (Last Error START_STALLED, `list` State STALLED) shows the
    sentence there too, with no AWS line. Image names the image
    the workstation was launched from, as `list` does, beside Image ID; for an image an AMI recipe
    built, Recipe names the recipe and Recipe version the build it runs, followed by `(latest)` or
    `(latest <n>)` from the record's imageLatest and latestRecipeVersion.

    Examples:
        vamscli edh workstation get SESSION_ID
        vamscli edh workstation get SESSION_ID --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status(f"Retrieving workstation '{session_id}'...", json_output)

    try:
        result = api_client.get_edh_workstation(session_id)
        output_result(result, json_output, success_message="Workstation retrieved",
                      cli_formatter=format_workstation)
    except EdhWorkstationNotFoundError as e:
        output_error(e, json_output, error_type="Workstation Not Found",
                     helpful_message="Use 'vamscli edh workstation list' to see your workstations.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@workstation.command(name='start')
@click.argument('session_id')
@click.option('--wait', is_flag=True,
              help=f'Follow the start: read the workstation every {START_WAIT_POLL_SECONDS} s until it is '
                   f'ACTIVE (exit 0), the start left it stopped (exit 1, with the reason), the start '
                   f'stalled (START_STALLED; exit 1, with the sentence), or '
                   f'{START_WAIT_TIMEOUT_SECONDS // 60} minutes pass (exit 1)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def start(ctx: click.Context, session_id: str, wait: bool, json_output: bool):
    """Start one of your stopped workstations. SESSION_ID may be the bootstrap id of a launch that has
    no session id yet; such a launch cannot be started and is refused (HTTP 409).

    The start is accepted (HTTP 202) and runs in the background: the returned record carries activity
    `starting`, and `edh workstation get` or `list` shows STARTING until the workstation is ACTIVE. A
    start EDH refuses leaves it STOPPED with the reason under Last Error and Start Failure (for lack
    of capacity, CAPACITY_UNAVAILABLE: the workstation keeps its disk in its Availability Zone; start
    it again later). A workstation whose capacity reservation has ended starts on-demand; when VAMS
    cannot move it to on-demand capacity the start is refused (RESERVATION_ENDED). The workstation is
    never deleted for a refused start. A start EDH accepts and
    has not finished after 30 minutes stays CREATING and reads STALLED (Last Error START_STALLED):
    stop it, then start it again. --wait follows the start and exits 0 only once the workstation is
    ACTIVE.

    Examples:
        vamscli edh workstation start SESSION_ID
        vamscli edh workstation start SESSION_ID --wait
        vamscli edh workstation start SESSION_ID --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status(f"Starting workstation '{session_id}'...", json_output)

    try:
        result = api_client.start_edh_workstation(session_id)
        if not wait:
            output_result(result, json_output, success_message="Workstation start requested",
                          cli_formatter=format_start_accepted)
            return
        target = result.get('sessionId') or session_id
        output_status(
            f"Workstation start requested; waiting for it to start (every {START_WAIT_POLL_SECONDS} s, "
            f"up to {START_WAIT_TIMEOUT_SECONDS // 60} minutes)...", json_output)
        outcome, record = wait_for_start(api_client, target, result, json_output)
        if outcome == 'active':
            output_result(record, json_output, success_message="Workstation started (ACTIVE)",
                          cli_formatter=format_workstation)
            return
        if not json_output:
            click.echo(format_workstation(record))
        if outcome == 'timedOut':
            raise EdhStartWaitTimeoutError(
                f"Workstation still {state_label(record)} after {START_WAIT_TIMEOUT_SECONDS // 60} "
                f"minutes; the start may still complete. Follow it with "
                f"'vamscli edh workstation get {target}'.", record)
        if outcome == 'stalled':
            raise EdhStartStalledError(
                f"Workstation start stalled ({START_STALLED_CODE}): {START_STALLED_MESSAGE}", record)
        failure = describe_start_failure(record)
        if failure:
            raise EdhStartFailedError(
                f"Workstation start failed ({record.get('lastError')}): {failure[0]}", record)
        raise EdhStartFailedError(
            f"Workstation start did not complete: the workstation is {state_label(record)}.", record)
    except EdhStartWaitTimeoutError as e:
        output_error(e, json_output, error_type="Workstation Start Not Finished")
        raise click.ClickException(str(e))
    except EdhStartStalledError as e:
        output_error(e, json_output, error_type="Workstation Start Stalled",
                     helpful_message=f"Stop it with 'vamscli edh workstation stop {session_id}', then "
                                     f"start it again.")
        raise click.ClickException(str(e))
    except EdhStartFailedError as e:
        output_error(e, json_output, error_type="Workstation Start Failed",
                     helpful_message="See 'Start Failure' above; the workstation keeps its disk.")
        raise click.ClickException(str(e))
    except EdhWorkstationNotFoundError as e:
        output_error(e, json_output, error_type="Workstation Not Found",
                     helpful_message="Use 'vamscli edh workstation list' to see your workstations.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@workstation.command(name='stop')
@click.argument('session_id')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def stop(ctx: click.Context, session_id: str, json_output: bool):
    """Stop one of your running workstations. Start it again with `edh workstation start`. A launch
    that has no EDH session yet is refused (HTTP 409). A start EDH accepted and has not finished
    after 30 minutes (`list` State STALLED, Last Error START_STALLED) is stopped the same way; start
    it again once it reads STOPPED.

    Examples:
        vamscli edh workstation stop SESSION_ID
        vamscli edh workstation stop SESSION_ID --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status(f"Stopping workstation '{session_id}'...", json_output)

    try:
        result = api_client.stop_edh_workstation(session_id)
        output_result(result, json_output, success_message="Workstation stop requested",
                      cli_formatter=format_workstation)
    except EdhWorkstationNotFoundError as e:
        output_error(e, json_output, error_type="Workstation Not Found",
                     helpful_message="Use 'vamscli edh workstation list' to see your workstations.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@workstation.command(name='delete')
@click.argument('session_id')
@click.option('--confirm', is_flag=True, help='Confirm workstation deletion (required)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def delete(ctx: click.Context, session_id: str, confirm: bool, json_output: bool):
    """Delete one of your workstations. This is irreversible; --confirm is required.

    A launch that never received a session id is deleted by its bootstrap id: an ORPHANED launch at
    once, a CREATING one only after it has made no progress for 15 minutes (one that changed more
    recently is refused with HTTP 409 while the launch may still complete).

    Examples:
        vamscli edh workstation delete SESSION_ID --confirm
        vamscli edh workstation delete SESSION_ID --confirm --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    if not confirm:
        _confirmation_required("Workstation deletion", "sessionId", session_id, json_output)

    output_status(f"Deleting workstation '{session_id}'...", json_output)

    try:
        result = api_client.delete_edh_workstation(session_id)
        output_result(result, json_output, success_message="Workstation deleted",
                      cli_formatter=format_workstation)
    except EdhWorkstationNotFoundError as e:
        output_error(e, json_output, error_type="Workstation Not Found",
                     helpful_message="Use 'vamscli edh workstation list' to see your workstations.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@workstation.command(name='connect')
@click.argument('session_id')
@click.option('--show-windows-password', is_flag=True,
              help='Also show the Windows user name and password of a Windows workstation (a credential)')
@click.option('--json-output', is_flag=True,
              help='Output raw JSON response, including the full connection URL')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def connect(ctx: click.Context, session_id: str, show_windows_password: bool, json_output: bool):
    """Fetch the DCV connection details for one of your ACTIVE workstations.

    The response is sessionId, connectionUrl and osFamily. A workstation that is not connectable yet
    (still CREATING, STOPPED, or without an EDH session) is refused with HTTP 409. So is a running
    workstation whose remote display session has ended (code DISPLAY_SESSION_ENDED; `get` shows it as
    Connection): stop it, then start it again, to reconnect.

    The connection URL carries a session credential, so in CLI mode only the DCV host is printed.
    Use --json-output to receive the full connectionUrl; it is written to stdout, so it lands in your
    terminal scrollback and in anything that captures or pipes the output (log files, CI output,
    agent transcripts). The CLI's own log file never records it.

    A Windows workstation opens at its lock screen. --show-windows-password also prints the Windows
    user name and the password EDH generated for the desktop, a credential written to stdout like the
    URL; for a directory account it says to sign in with your directory password. With --json-output
    they are returned under windowsSignIn. A workstation that is not Windows is refused with HTTP 400.

    Examples:
        vamscli edh workstation connect SESSION_ID
        vamscli edh workstation connect SESSION_ID --json-output
        vamscli edh workstation connect SESSION_ID --show-windows-password
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status(f"Retrieving connection details for '{session_id}'...", json_output)

    try:
        result = api_client.get_edh_workstation_connection(session_id)
        if show_windows_password:
            # Fetched before anything is printed, so a refused sign-in prints only its error.
            sign_in = api_client.get_edh_workstation_windows_sign_in(session_id)
            result = {**result, 'windowsSignIn': {
                'username': sign_in.get('username'),
                'password': sign_in.get('password'),
                'directoryAccount': bool(sign_in.get('directoryAccount')),
            }}
        output_result(result, json_output, success_message="Connection details retrieved",
                      cli_formatter=format_connect_output)
    except EdhWorkstationNotFoundError as e:
        output_error(e, json_output, error_type="Workstation Not Found",
                     helpful_message="Use 'vamscli edh workstation list' to see your workstations.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@workstation.command(name='renew')
@click.argument('session_id')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def renew(ctx: click.Context, session_id: str, json_output: bool):
    """Renew a workstation's lease, resetting it to the administrator's full lease window.

    Rejected (409) when leases are disabled, when the workstation has no lease, or when it is deleted.

    Examples:
        vamscli edh workstation renew SESSION_ID
        vamscli edh workstation renew SESSION_ID --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status(f"Renewing lease for '{session_id}'...", json_output)

    try:
        result = api_client.renew_edh_workstation(session_id)
        output_result(result, json_output, success_message="Lease renewed",
                      cli_formatter=format_workstation)
    except EdhWorkstationNotFoundError as e:
        output_error(e, json_output, error_type="Workstation Not Found",
                     helpful_message="Use 'vamscli edh workstation list' to see your workstations.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


def format_catalog_images(result: Dict[str, Any]) -> str:
    """One row per image in the API's order, recipe images first; ARCH is the image's CPU
    architecture ('-' when EDH reports none), LAUNCH TYPES counts the instance types the image's recipe
    offers ('any' when it lists none; `catalog instance-types --image-id` names them), and RECIPE names
    the recipe and build version a Custom AMI Recipe image was built from."""
    items = result.get('Items', []) if isinstance(result, dict) else []
    if not items:
        return "No images found."
    rows = [['CATEGORY', 'IMAGE', 'NAME', 'OS', 'ARCH', 'BASE OS', 'ROOT GIB', 'LAUNCH TYPES', 'RECIPE']]
    for item in items:
        category = item.get('category')
        recipe = f"{item['recipeId']} v{item.get('recipeVersion', '?')}" if item.get('recipeId') else '-'
        rows.append([CATALOG_CATEGORY_LABELS.get(category, category or '-'), str(item.get('imageId', '')),
                     str(item.get('name') or ''), str(item.get('osFamily') or ''),
                     str(item.get('architecture') or '-'), str(item.get('baseOs') or ''),
                     str(item.get('rootSize') or '-'), launch_types_summary(item.get('launchInstanceTypes')),
                     recipe])
    # Each column but the last is at least as wide as its longest value, so a long name stays aligned.
    widths = [max(minimum, *(len(row[i]) for row in rows))
              for i, minimum in enumerate((18, 6, 34, 8, 7, 16, 9, 13))]
    return '\n'.join(' '.join(cell.ljust(width) for cell, width in zip(row, widths)) + ' ' + row[-1]
                     for row in rows)


def launch_types_summary(launch_types: Any) -> str:
    """'<n> listed' for a recipe's list of launch instance types, 'any' for none."""
    return f"{len(launch_types)} listed" if isinstance(launch_types, list) and launch_types else 'any'


def format_instance_types(result: Dict[str, Any]) -> str:
    """One row per instance type, with the architecture groups of the deployment's desktop profile
    that list it."""
    items = result.get('Items', []) if isinstance(result, dict) else []
    if not items:
        return "No instance types found."
    rows = [['INSTANCE TYPE', 'ARCH']]
    for item in items:
        if isinstance(item, dict):
            architectures = ','.join(item.get('architectures') or []) or '-'
            rows.append([str(item.get('instanceType', 'N/A')), architectures])
        else:
            rows.append([str(item), '-'])
    width = max(16, *(len(row[0]) for row in rows))
    return '\n'.join(row[0].ljust(width) + ' ' + row[1] for row in rows)


def instance_type_runs_image(item: Any, image: Dict[str, Any]) -> bool:
    """Whether a catalog instance type runs a catalog image, by the web launch wizard's rule: the
    image's architecture is unknown, the type names no architecture group, or one of its groups is
    the image's architecture."""
    architecture = image.get('architecture') if isinstance(image, dict) else None
    architectures = (item.get('architectures') if isinstance(item, dict) else None) or []
    return not architecture or not architectures or architecture in architectures


def instance_type_offered_for_image(item: Any, image: Dict[str, Any]) -> bool:
    """Whether a catalog image's recipe offers a catalog instance type, by the web launch wizard's rule:
    the image carries no launchInstanceTypes (a baseline image, or a recipe that lists none), or the list
    names the type. The launch holds the type to the same list."""
    offered = image.get('launchInstanceTypes') if isinstance(image, dict) else None
    instance_type = item.get('instanceType') if isinstance(item, dict) else item
    return not offered or instance_type in offered


@edh.group()
def catalog():
    """Browse the desktop images and instance types available for launching workstations."""
    pass


@catalog.command(name='images')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def catalog_images(ctx: click.Context, json_output: bool):
    """List the desktop images you may launch (permission-filtered): images an AMI recipe built
    (Custom AMI Recipe, with the recipe and build version) first, then the other images (Baseline
    Image), each group sorted by name. ARCH is the image's CPU architecture.

    Examples:
        vamscli edh catalog images
        vamscli edh catalog images --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving image catalog...", json_output)

    try:
        result = api_client.list_edh_catalog_images()
        items = result.get('Items', []) if isinstance(result, dict) else []
        output_result(
            result,
            json_output,
            success_message=f"Found {len(items)} image(s)",
            cli_formatter=format_catalog_images,
        )
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@catalog.command(name='instance-types')
@click.option('--image-id', help="Only the instance types that run this catalog image's architecture "
                                 "(see 'vamscli edh catalog images').")
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def catalog_instance_types(ctx: click.Context, image_id: Optional[str], json_output: bool):
    """List the EC2 instance types this deployment allows for workstations, sorted by name with
    numbers in numeric order (g5.2xlarge before g5.12xlarge). ARCH names the architecture groups of
    the deployment's desktop profile that list the type. With --image-id, only the types that run
    that catalog image are listed: a type runs an image when the image's architecture is unknown, the
    type names no architecture, or the type's architectures include the image's; and an image whose
    recipe lists launch instance types offers only those.

    Examples:
        vamscli edh catalog instance-types
        vamscli edh catalog instance-types --image-id IMAGE_ID
        vamscli edh catalog instance-types --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving instance types...", json_output)

    try:
        image = None
        if image_id is not None:
            images = api_client.list_edh_catalog_images()
            catalog_items = images.get('Items', []) if isinstance(images, dict) else []
            image = next((item for item in catalog_items
                          if isinstance(item, dict) and item.get('imageId') == image_id), None)
            if image is None:
                raise EdhImageNotFoundError(
                    f"Image '{image_id}' is not in your image catalog. Use 'vamscli edh catalog images' "
                    f"to see the images you may launch.")
        result = api_client.list_edh_catalog_instance_types()
        items = result.get('Items', []) if isinstance(result, dict) else []
        empty_message = None
        if image is not None:
            matching = [item for item in items
                        if instance_type_runs_image(item, image) and instance_type_offered_for_image(item, image)]
            if items and not matching:
                if image.get('launchInstanceTypes'):
                    empty_message = ("No instance type this deployment allows is offered for this image. "
                                     "Choose another image or ask an administrator.")
                else:
                    # Only a known image architecture excludes a type, so the message can name it.
                    empty_message = ("No instance type in this deployment runs this image's architecture "
                                     f"({image.get('architecture')}). Choose another image or ask an "
                                     "administrator.")
            items = matching
            result = {**(result if isinstance(result, dict) else {}), 'Items': items}
        output_result(
            result,
            json_output,
            success_message=f"Found {len(items)} instance type(s)",
            cli_formatter=(lambda _result: empty_message) if empty_message else format_instance_types,
        )
    except EdhImageNotFoundError as e:
        output_error(e, json_output, error_type="Image Not Found")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@catalog.command(name='lease')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def catalog_lease(ctx: click.Context, json_output: bool):
    """Show the lease policy parameters any workstation user may read before launching.

    Examples:
        vamscli edh catalog lease
        vamscli edh catalog lease --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving lease policy...", json_output)

    try:
        result = api_client.get_edh_catalog_lease()
        output_result(result, json_output, success_message="Lease policy retrieved",
                      cli_formatter=format_json)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


def _dash(value: Any) -> str:
    return '-' if value is None or value == '' else str(value)


def edh_subnet_note(item: Dict[str, Any]) -> Optional[str]:
    """The note on a reservation EDH cannot launch into for want of a private subnet in its
    Availability Zone (`edhSubnetAvailable` false): `No EDH subnet in <az>`; None when EDH has one or
    when VAMS could not read EDH's subnets (null). The web Settings table's State cell shows the same."""
    if item.get('edhSubnetAvailable') is not False:
        return None
    zone = item.get('availabilityZone')
    return f"No EDH subnet in {zone if isinstance(zone, str) and zone else 'its Availability Zone'}"


def _launch_text(item: Dict[str, Any]) -> str:
    """The catalog's LAUNCH cell: `yes`, `view only` for a reservation the role may see but not launch
    into, `-` from a service that predates `launchAllowed`."""
    allowed = item.get('launchAllowed')
    if allowed is None:
        return '-'
    return LAUNCH_ALLOWED_TEXT if allowed else LAUNCH_VIEW_ONLY_TEXT


def format_capacity_reservations(result: Dict[str, Any], launch_column: bool = False) -> str:
    """One row per capacity reservation in the web Capacity reservations table's columns: FREE/TOTAL
    is EC2's available and total instance counts, FALLBACK what a launch into a full reservation does
    (`on-demand` or `refuse`), and STATE EC2's state, `notFound` when EC2 no longer knows the id, or
    `unknown` when the read failed, followed by `(No EDH subnet in <az>)` when EDH has no private
    subnet in the reservation's Availability Zone. With `launch_column` (the user catalog) a last
    LAUNCH column reads `yes`, or `view only` for a reservation the role may see but not launch into."""
    items = result.get('Items', []) if isinstance(result, dict) else []
    if not items:
        return "No capacity reservations found."
    rows = [['LABEL', 'RESERVATION', 'INSTANCE TYPE', 'AZ', 'PLATFORM', 'STATE', 'FREE/TOTAL', 'END DATE',
             'FALLBACK', 'ENABLED'] + (['LAUNCH'] if launch_column else [])]
    for item in items:
        if not isinstance(item, dict):
            continue
        available, total = item.get('availableInstanceCount'), item.get('totalInstanceCount')
        note = edh_subnet_note(item)
        state = _dash(item.get('state'))
        rows.append([
            str(item.get('label') or ''), str(item.get('capacityReservationId') or ''),
            _dash(item.get('instanceType')), _dash(item.get('availabilityZone')),
            _dash(item.get('instancePlatform')), f"{state} ({note})" if note else state,
            '-' if available is None or total is None else f"{available}/{total}",
            _dash(item.get('endDate')),
            'on-demand' if item.get('fallbackToOnDemand') else 'refuse',
            'yes' if item.get('enabled') else 'no',
        ] + ([_launch_text(item)] if launch_column else []))
    # Each column but the last is at least as wide as its longest value, so a long label stays aligned.
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    return '\n'.join(' '.join(cell.ljust(width) for cell, width in zip(row, widths)) + ' ' + row[-1]
                     for row in rows)


def format_catalog_capacity_reservations(result: Dict[str, Any]) -> str:
    """The user catalog's listing: format_capacity_reservations with the LAUNCH column."""
    return format_capacity_reservations(result, launch_column=True)


@catalog.command(name='capacity-reservations')
@click.option('--instance-type', default=None, help='Only the reservations of this instance type')
@click.option('--os-family', default=None, type=click.Choice(['linux', 'windows']),
              help='Only the reservations whose platform suits an image of this family (linux: '
                   'Linux/UNIX, windows: Windows)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def catalog_capacity_reservations(ctx: click.Context, instance_type: Optional[str], os_family: Optional[str],
                                  json_output: bool):
    """List the capacity reservations you may launch into: registered by an administrator, enabled,
    granted to your role, active in EC2, and in an Availability Zone where EDH has a private subnet.
    Pass one as `edh workstation create --capacity-reservation-id`; its instance type and platform
    must match the launch. The LAUNCH column reads `yes`, or `view only` for a reservation your role
    may see but not launch into (a launch into it is refused with 403 RESERVATION_NOT_PERMITTED). An
    empty list means every launch is on-demand.

    Examples:
        vamscli edh catalog capacity-reservations
        vamscli edh catalog capacity-reservations --instance-type g5.16xlarge --os-family linux
        vamscli edh catalog capacity-reservations --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving capacity reservations...", json_output)

    try:
        result = api_client.list_edh_catalog_capacity_reservations(
            instance_type=instance_type, os_family=os_family)
        items = result.get('Items', []) if isinstance(result, dict) else []
        output_result(result, json_output, success_message=f"Found {len(items)} capacity reservation(s)",
                      cli_formatter=format_catalog_capacity_reservations)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


# ---------------------------------------------------------------------------
# admin
# ---------------------------------------------------------------------------


def format_json(result: Any) -> str:
    """Render a body whose shape is EDH's own verbatim."""
    return json.dumps(result, indent=2)


@edh.group()
def admin():
    """Register images, build AMI recipes, inspect every user's sessions, set the lease policy, register
    capacity reservations, and reclaim cluster capacity."""
    pass


@admin.group()
def images():
    """Register and unregister EDH software-stack images."""
    pass


@images.command(name='list')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def images_list(ctx: click.Context, json_output: bool):
    """List every registered image (desktop and simulation).

    Examples:
        vamscli edh admin images list
        vamscli edh admin images list --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving registered images...", json_output)

    try:
        result = api_client.list_edh_admin_images()
        items = result.get('Items', []) if isinstance(result, dict) else []
        output_result(
            result,
            json_output,
            success_message=f"Found {len(items)} image(s)",
            cli_formatter=lambda r: format_items(r, "images"),
        )
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@images.command(name='register-vdi')
@click.option('--ami-id', required=True, help='AMI ID of the desktop image')
@click.option('--stack-name', required=True, help='Software-stack name shown in the catalog')
@click.option('--base-os', required=True, help='EDH base OS identifier of the AMI')
@click.option('--root-size', required=True, type=click.IntRange(1, ROOT_SIZE_GB_MAX),
              help=f'Root volume size in GiB (1-{ROOT_SIZE_GB_MAX})')
@click.option('--virtual-desktop-profile-id', required=True, type=int,
              help='EDH virtual desktop profile the stack launches under')
@click.option('--launch-tenancy', default=None, help='EC2 launch tenancy')
@click.option('--description', default=None, help='Description shown in the catalog')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def register_vdi(ctx: click.Context, ami_id: str, stack_name: str, base_os: str, root_size: int,
                 virtual_desktop_profile_id: int, launch_tenancy: Optional[str],
                 description: Optional[str], json_output: bool):
    """Register an AMI as a desktop (VDI) image users can launch.

    Examples:
        vamscli edh admin images register-vdi --ami-id ami-0123 --stack-name ubuntu-22-dcv --base-os ubuntu2204 --root-size 100 --virtual-desktop-profile-id 3
        vamscli edh admin images register-vdi --ami-id ami-0123 --stack-name win-dcv --base-os windows2022 --root-size 150 --virtual-desktop-profile-id 3 --launch-tenancy dedicated --description "Windows desktop" --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    data: Dict[str, Any] = {
        'amiId': ami_id,
        'stackName': stack_name,
        'baseOs': base_os,
        'rootSize': root_size,
        'virtualDesktopProfileId': virtual_desktop_profile_id,
    }
    if launch_tenancy is not None:
        data['launchTenancy'] = launch_tenancy
    if description is not None:
        data['description'] = description

    output_status(f"Registering VDI image '{stack_name}'...", json_output)

    try:
        result = api_client.register_edh_vdi_image(data)
        output_result(result, json_output, success_message="VDI image registered",
                      cli_formatter=format_json)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@images.command(name='register-simulation')
@click.option('--ami-id', required=True, help='AMI ID of the simulation image')
@click.option('--stack-name', required=True, help='Software-stack name')
@click.option('--root-size', required=True, type=click.IntRange(1, ROOT_SIZE_GB_MAX),
              help=f'Root volume size in GiB (1-{ROOT_SIZE_GB_MAX})')
@click.option('--target-node-profile-id', required=True, type=int,
              help='EDH target node profile the stack launches under')
@click.option('--target-node-user-data-id', default=None, type=click.IntRange(min=1),
              help='EDH target-node user-data template (default: template 1)')
@click.option('--user-data-variables', default=None, help='User-data variables passed to the node')
@click.option('--description', default=None, help='Description')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def register_simulation(ctx: click.Context, ami_id: str, stack_name: str, root_size: int,
                        target_node_profile_id: int, target_node_user_data_id: Optional[int],
                        user_data_variables: Optional[str], description: Optional[str],
                        json_output: bool):
    """Register an AMI as a simulation (target node) image.

    Examples:
        vamscli edh admin images register-simulation --ami-id ami-0456 --stack-name isaac-sim --root-size 200 --target-node-profile-id 7
        vamscli edh admin images register-simulation --ami-id ami-0456 --stack-name isaac-sim --root-size 200 --target-node-profile-id 7 --user-data-variables "MODE=headless" --description "Isaac Sim node" --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    data: Dict[str, Any] = {
        'amiId': ami_id,
        'stackName': stack_name,
        'rootSize': root_size,
        'targetNodeProfileId': target_node_profile_id,
    }
    if target_node_user_data_id is not None:
        data['targetNodeUserDataId'] = target_node_user_data_id
    if user_data_variables is not None:
        data['userDataVariables'] = user_data_variables
    if description is not None:
        data['description'] = description

    output_status(f"Registering simulation image '{stack_name}'...", json_output)

    try:
        result = api_client.register_edh_simulation_image(data)
        output_result(result, json_output, success_message="Simulation image registered",
                      cli_formatter=format_json)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@images.command(name='delete-vdi')
@click.argument('image_id')
@click.option('--confirm', is_flag=True, help='Confirm image deletion (required)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def delete_vdi(ctx: click.Context, image_id: str, confirm: bool, json_output: bool):
    """Unregister a desktop (VDI) image by IMAGE_ID. Irreversible; --confirm is required.

    Examples:
        vamscli edh admin images delete-vdi IMAGE_ID --confirm
        vamscli edh admin images delete-vdi IMAGE_ID --confirm --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    if not confirm:
        _confirmation_required("Image deletion", "imageId", image_id, json_output)

    output_status(f"Deleting VDI image '{image_id}'...", json_output)

    try:
        result = api_client.delete_edh_vdi_image(image_id)
        output_result(result, json_output, success_message="Image deleted", cli_formatter=format_json)
    except EdhImageNotFoundError as e:
        output_error(e, json_output, error_type="Image Not Found",
                     helpful_message="Use 'vamscli edh admin images list' to see registered images.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@images.command(name='delete-simulation')
@click.argument('image_id')
@click.option('--confirm', is_flag=True, help='Confirm image deletion (required)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def delete_simulation(ctx: click.Context, image_id: str, confirm: bool, json_output: bool):
    """Unregister a simulation image by IMAGE_ID. Irreversible; --confirm is required.

    Examples:
        vamscli edh admin images delete-simulation IMAGE_ID --confirm
        vamscli edh admin images delete-simulation IMAGE_ID --confirm --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    if not confirm:
        _confirmation_required("Image deletion", "imageId", image_id, json_output)

    output_status(f"Deleting simulation image '{image_id}'...", json_output)

    try:
        result = api_client.delete_edh_simulation_image(image_id)
        output_result(result, json_output, success_message="Image deleted", cli_formatter=format_json)
    except EdhImageNotFoundError as e:
        output_error(e, json_output, error_type="Image Not Found",
                     helpful_message="Use 'vamscli edh admin images list' to see registered images.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@admin.group()
def instances():
    """Inspect every user's workstation sessions and reclaim capacity."""
    pass


@instances.command(name='list')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def instances_list(ctx: click.Context, json_output: bool):
    """List every user's workstation sessions on the cluster.

    Each session is printed as its JSON item. A session whose launch failed and for which VAMS
    captured the failure from EDH (the item's edhFailure) is followed by an `EDH failure:` line naming
    the AWS error code and operation, or the desktop setup checkpoint, as `workstation get` shows it.

    Examples:
        vamscli edh admin instances list
        vamscli edh admin instances list --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving workstation sessions...", json_output)

    try:
        result = api_client.list_edh_admin_instances()
        items = result.get('Items', []) if isinstance(result, dict) else []
        output_result(
            result,
            json_output,
            success_message=f"Found {len(items)} session(s)",
            cli_formatter=lambda r: format_admin_sessions(r, "sessions"),
        )
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@instances.command(name='stale')
@click.option('--idle-minutes', type=click.IntRange(min=1), default=None,
              help='Idle threshold in minutes (deployment default: 120)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def instances_stale(ctx: click.Context, idle_minutes: Optional[int], json_output: bool):
    """List the sessions idle longer than the threshold, plus sessions VAMS knows but EDH no longer
    lists (staleReason "missingInEdh") -- the candidates for stop / deactivate. The response also
    carries the idleMinutes threshold that was applied. A failed launch whose failure VAMS captured
    from EDH (the item's edhFailure) is followed by an `EDH failure:` line, as in `instances list`.

    Examples:
        vamscli edh admin instances stale
        vamscli edh admin instances stale --idle-minutes 45
        vamscli edh admin instances stale --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving stale sessions...", json_output)

    try:
        result = api_client.list_edh_stale_instances(idle_minutes=idle_minutes)
        items = result.get('Items', []) if isinstance(result, dict) else []
        output_result(
            result,
            json_output,
            success_message=f"Found {len(items)} stale session(s)",
            cli_formatter=lambda r: format_admin_sessions(r, "stale sessions"),
        )
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@instances.command(name='stop')
@click.argument('session_id')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def instances_stop(ctx: click.Context, session_id: str, json_output: bool):
    """Stop any user's workstation session by SESSION_ID (the owner can start it again). A session EDH
    no longer lists is refused (HTTP 409); use `deactivate` to reclaim its record.

    Examples:
        vamscli edh admin instances stop SESSION_ID
        vamscli edh admin instances stop SESSION_ID --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status(f"Stopping session '{session_id}'...", json_output)

    try:
        result = api_client.stop_edh_admin_instance(session_id)
        output_result(result, json_output, success_message="Instance stop requested",
                      cli_formatter=format_json)
    except EdhWorkstationNotFoundError as e:
        output_error(e, json_output, error_type="Session Not Found",
                     helpful_message="Use 'vamscli edh admin instances list' to see every session.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@instances.command(name='deactivate')
@click.argument('session_id')
@click.option('--confirm', is_flag=True, help='Confirm deactivation (required)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def instances_deactivate(ctx: click.Context, session_id: str, confirm: bool, json_output: bool):
    """Force-deactivate any user's workstation session by SESSION_ID. Irreversible; --confirm is
    required. A session EDH no longer lists is marked DELETED without an EDH call. A launch that never
    received a session id is addressed by its bootstrap id.

    Examples:
        vamscli edh admin instances deactivate SESSION_ID --confirm
        vamscli edh admin instances deactivate SESSION_ID --confirm --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    if not confirm:
        _confirmation_required("Session deactivation", "sessionId", session_id, json_output)

    output_status(f"Deactivating session '{session_id}'...", json_output)

    try:
        result = api_client.deactivate_edh_admin_instance(session_id)
        output_result(result, json_output, success_message="Instance deactivated",
                      cli_formatter=format_json)
    except EdhWorkstationNotFoundError as e:
        output_error(e, json_output, error_type="Session Not Found",
                     helpful_message="Use 'vamscli edh admin instances list' to see every session.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@admin.command(name='capacity')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def capacity(ctx: click.Context, json_output: bool):
    """Show the cluster's workstation capacity summary as EDH reports it.

    Examples:
        vamscli edh admin capacity
        vamscli edh admin capacity --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving capacity...", json_output)

    try:
        result = api_client.get_edh_capacity()
        output_result(result, json_output, success_message="Capacity retrieved", cli_formatter=format_json)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@admin.command(name='health')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def health(ctx: click.Context, json_output: bool):
    """Show whether VAMS can reach the EDH API with its configured credentials.

    Examples:
        vamscli edh admin health
        vamscli edh admin health --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Checking EDH health...", json_output)

    try:
        result = api_client.get_edh_health()
        output_result(result, json_output, success_message="Health retrieved", cli_formatter=format_json)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@admin.group()
def settings():
    """Administer EDH workstation runtime settings (lease policy)."""
    pass


@settings.command(name='get')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def settings_get(ctx: click.Context, json_output: bool):
    """Show the current lease policy.

    Examples:
        vamscli edh admin settings get
        vamscli edh admin settings get --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving lease policy...", json_output)

    try:
        result = api_client.get_edh_admin_settings()
        output_result(result, json_output, success_message="Lease policy", cli_formatter=format_json)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@settings.command(name='set')
@click.option('--enabled/--disabled', 'enabled', default=None, help='Turn leases on or off')
@click.option('--lease-hours', default=None, type=click.IntRange(1, 720), help='Lease length (1-720 hours)')
@click.option('--forced/--no-forced', 'forced', default=None, help='Force a lease on every launch')
@click.option('--grace-hours', default=None, type=click.IntRange(0, 168), help='Stop-to-terminate grace (0-168 hours)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def settings_set(ctx: click.Context, enabled, lease_hours, forced, grace_hours, json_output):
    """Update the lease policy. Only the flags you pass are changed.

    Examples:
        vamscli edh admin settings set --enabled --lease-hours 24
        vamscli edh admin settings set --grace-hours 6
        vamscli edh admin settings set --disabled
        vamscli edh admin settings set --forced --lease-hours 12 --json-output
    """
    # Setup/auth already validated by decorator
    data = {}
    if enabled is not None:
        data['enabled'] = enabled
    if lease_hours is not None:
        data['leaseHours'] = lease_hours
    if forced is not None:
        data['forced'] = forced
    if grace_hours is not None:
        data['graceHours'] = grace_hours

    if not data:
        raise click.UsageError("Pass at least one of --enabled/--disabled, --lease-hours, --forced/--no-forced, --grace-hours")

    api_client = _api_client(ctx)

    output_status("Updating lease policy...", json_output)

    try:
        result = api_client.update_edh_admin_settings(data)
        output_result(result, json_output, success_message="Lease policy updated", cli_formatter=format_json)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


# ---------------------------------------------------------------------------
# capacity-reservation
# ---------------------------------------------------------------------------


@admin.group(name='capacity-reservation')
def capacity_reservation():
    """Register the EC2 capacity reservations workstations may launch into. The registry is VAMS's
    own: no command here changes a reservation in EC2."""
    pass


@capacity_reservation.command(name='list')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def capacity_reservation_list(ctx: click.Context, json_output: bool):
    """List every registered capacity reservation with EC2's live view of it.

    Examples:
        vamscli edh admin capacity-reservation list
        vamscli edh admin capacity-reservation list --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving capacity reservations...", json_output)

    try:
        result = api_client.list_edh_admin_capacity_reservations()
        items = result.get('Items', []) if isinstance(result, dict) else []
        output_result(result, json_output, success_message=f"Found {len(items)} capacity reservation(s)",
                      cli_formatter=format_capacity_reservations)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@capacity_reservation.command(name='add')
@click.option('--capacity-reservation-id', required=True, callback=_capacity_reservation_id,
              help='EC2 capacity reservation id (cr- and 17 hex digits) in this account and Region')
@click.option('--label', required=True, callback=_capacity_reservation_label,
              help='Name shown in the launch wizard (1-64 letters, digits, spaces, hyphens, periods, underscores)')
@click.option('--fallback/--no-fallback', 'fallback', default=None,
              help='When full: launch on-demand (--fallback, the server default) or refuse (--no-fallback)')
@click.option('--enabled/--disabled', 'enabled', default=None,
              help='Offer the reservation for launches (--enabled, the server default) or not')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def capacity_reservation_add(ctx: click.Context, capacity_reservation_id: str, label: str, fallback, enabled,
                             json_output: bool):
    """Register a capacity reservation for launches. EC2 must find it in this account and Region.
    Users see it once their role grants it (object type edhCapacityReservation).

    Examples:
        vamscli edh admin capacity-reservation add --capacity-reservation-id cr-0123456789abcdef0 --label "Render pool"
        vamscli edh admin capacity-reservation add --capacity-reservation-id cr-0123456789abcdef0 --label "Render pool" --no-fallback
    """
    data: Dict[str, Any] = {'capacityReservationId': capacity_reservation_id, 'label': label}
    if fallback is not None:
        data['fallbackToOnDemand'] = fallback
    if enabled is not None:
        data['enabled'] = enabled

    api_client = _api_client(ctx)

    output_status("Registering capacity reservation...", json_output)

    try:
        result = api_client.register_edh_capacity_reservation(data)
        output_result(result, json_output, success_message="Capacity reservation registered",
                      cli_formatter=format_json)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@capacity_reservation.command(name='update')
@click.argument('capacity_reservation_id', callback=_capacity_reservation_id)
@click.option('--label', default=None, callback=_capacity_reservation_label, help='New label')
@click.option('--fallback/--no-fallback', 'fallback', default=None,
              help='When full: launch on-demand (--fallback) or refuse (--no-fallback)')
@click.option('--enabled/--disabled', 'enabled', default=None, help='Offer the reservation for launches or not')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def capacity_reservation_update(ctx: click.Context, capacity_reservation_id: str, label: Optional[str],
                                fallback, enabled, json_output: bool):
    """Change a registered reservation's label or switches. Only the flags you pass are changed.

    Examples:
        vamscli edh admin capacity-reservation update cr-0123456789abcdef0 --label "Render farm"
        vamscli edh admin capacity-reservation update cr-0123456789abcdef0 --no-fallback --disabled
    """
    data: Dict[str, Any] = {}
    if label is not None:
        data['label'] = label
    if fallback is not None:
        data['fallbackToOnDemand'] = fallback
    if enabled is not None:
        data['enabled'] = enabled
    if not data:
        raise click.UsageError("Pass at least one of --label, --fallback/--no-fallback, --enabled/--disabled")

    api_client = _api_client(ctx)

    output_status(f"Updating capacity reservation '{capacity_reservation_id}'...", json_output)

    try:
        result = api_client.update_edh_capacity_reservation(capacity_reservation_id, data)
        output_result(result, json_output, success_message="Capacity reservation updated",
                      cli_formatter=format_json)
    except EdhCapacityReservationNotFoundError as e:
        output_error(e, json_output, error_type="Capacity Reservation Not Found",
                     helpful_message=CAPACITY_RESERVATION_LISTING_HINT)
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@capacity_reservation.command(name='remove')
@click.argument('capacity_reservation_id', callback=_capacity_reservation_id)
@click.option('--confirm', is_flag=True, help='Confirm unregistering (required)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_WORKSTATIONS, EDH_WORKSTATIONS_DISABLED_MESSAGE)
def capacity_reservation_remove(ctx: click.Context, capacity_reservation_id: str, confirm: bool,
                                json_output: bool):
    """Unregister a capacity reservation. VAMS stops offering it for launches; the EC2 reservation
    itself is not changed, and workstations launched into it keep running.

    Examples:
        vamscli edh admin capacity-reservation remove cr-0123456789abcdef0 --confirm
    """
    if not confirm:
        _confirmation_required("Unregistering a capacity reservation", "capacityReservationId",
                               capacity_reservation_id, json_output)

    api_client = _api_client(ctx)

    output_status(f"Unregistering capacity reservation '{capacity_reservation_id}'...", json_output)

    try:
        result = api_client.unregister_edh_capacity_reservation(capacity_reservation_id)
        output_result(result, json_output, success_message="Capacity reservation unregistered",
                      cli_formatter=lambda _result: "  The EC2 reservation itself is not changed.")
    except EdhCapacityReservationNotFoundError as e:
        output_error(e, json_output, error_type="Capacity Reservation Not Found",
                     helpful_message=CAPACITY_RESERVATION_LISTING_HINT)
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


# ---------------------------------------------------------------------------
# ami-recipe
# ---------------------------------------------------------------------------


def load_ami_recipe_file(path: str) -> Dict[str, Any]:
    """The recipe JSON at ``path`` with every ``{"file": ...}`` component inlined relative to the
    file's directory, as the deploy-time seeding does for a recipe directory it seeds. A component
    whose path resolves outside that directory is refused, as an absolute path or a ``..`` segment
    is at deploy time."""
    import os
    import re
    with open(path, "r", encoding="utf-8") as handle:
        body = json.load(handle)
    base = os.path.dirname(os.path.abspath(path))
    components = []
    for component in body.get("components", []):
        if isinstance(component, dict) and "file" in component:
            file_path = os.path.abspath(os.path.join(base, str(component["file"])))
            try:
                inside = os.path.commonpath([base, file_path]) == base
            except ValueError:
                # Another drive on Windows shares no common path with the recipe directory.
                inside = False
            if not inside:
                raise click.ClickException(
                    f"Component file is outside the recipe directory {base}: {file_path}")
            try:
                with open(file_path, "r", encoding="utf-8") as handle:
                    data = handle.read()
            except FileNotFoundError:
                raise click.ClickException(f"Component file not found: {file_path}")
            components.append({"name": re.sub(r"\.ya?ml$", "", os.path.basename(component["file"])), "data": data})
        else:
            components.append(component)
    body["components"] = components
    return body


def format_ami_recipe_list(result: Dict[str, Any]) -> str:
    """One row per recipe; LAUNCH TYPES counts the instance types its desktops launch on ('any' when it
    lists none; `ami-recipe get` names them)."""
    items = result.get("Items", []) if isinstance(result, dict) else []
    if not items:
        return "No AMI recipes."
    lines = [f"{'RECIPE':<28} {'KIND':<11} {'OS':<8} {'ARCH':<7} {'SOURCE':<7} {'LATEST':<26} "
             f"{'LAUNCH TYPES':<13} STACK"]
    for r in items:
        latest = r.get("latestBuild") or {}
        latest_text = f"v{latest['version']} {latest.get('status', '')}" if latest else "-"
        launch_text = launch_types_summary(r.get("launchInstanceTypes"))
        lines.append(f"{r.get('recipeId', ''):<28} {r.get('kind', ''):<11} {r.get('osFamily', ''):<8} "
                     f"{r.get('architecture', ''):<7} {r.get('source', ''):<7} {latest_text:<26} "
                     f"{launch_text:<13} {r.get('stackName', '')}")
    return "\n".join(lines)


def format_ami_build_list(result: Dict[str, Any]) -> str:
    items = result.get("Items", []) if isinstance(result, dict) else []
    if not items:
        return "No builds."
    lines = [f"{'VERSION':<8} {'STATUS':<16} {'AMI':<22} {'EDH STACK':<34} {'REQUESTED':<26} ERROR"]
    for b in items:
        lines.append(f"v{b.get('version', ''):<7} {b.get('status', ''):<16} {b.get('amiId') or '-':<22} "
                     f"{b.get('edhStackName') or '-':<34} {b.get('requestedAt') or '-':<26} {b.get('error') or ''}")
    return "\n".join(lines)


@admin.group(name='ami-recipe')
def ami_recipe():
    """Define AMI recipes, build them with EC2 Image Builder and follow their EDH registration."""
    pass


@ami_recipe.command(name='list')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
def ami_recipe_list(ctx: click.Context, json_output: bool):
    """List every AMI recipe with its latest build.

    Examples:
        vamscli edh admin ami-recipe list
        vamscli edh admin ami-recipe list --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving AMI recipes...", json_output)
    try:
        result = api_client.list_edh_ami_recipes()
        items = result.get('Items', []) if isinstance(result, dict) else []
        output_result(result, json_output, success_message=f"Found {len(items)} recipe(s)",
                      cli_formatter=format_ami_recipe_list)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@ami_recipe.command(name='get')
@click.argument('recipe_id')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
def ami_recipe_get(ctx: click.Context, recipe_id: str, json_output: bool):
    """Get a specific AMI recipe: its full definition and its latest build (latestBuild). List every
    build with `edh admin ami-recipe builds`.

    Examples:
        vamscli edh admin ami-recipe get al2023-desktop
        vamscli edh admin ami-recipe get al2023-desktop --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status(f"Retrieving AMI recipe '{recipe_id}'...", json_output)
    try:
        result = api_client.get_edh_ami_recipe(recipe_id)
        output_result(result, json_output, success_message="Recipe retrieved", cli_formatter=format_json)
    except EdhAmiRecipeNotFoundError as e:
        output_error(e, json_output, error_type="Recipe Not Found",
                     helpful_message="Use 'vamscli edh admin ami-recipe list' to see every recipe.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@ami_recipe.command(name='create')
@click.option('--file', required=True, type=click.Path(exists=True), help='Path to recipe JSON file')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
def ami_recipe_create(ctx: click.Context, file: str, json_output: bool):
    """Create a new AMI recipe from a JSON file. File components are inlined relative to the file directory.

    Examples:
        vamscli edh admin ami-recipe create --file recipe.json
        vamscli edh admin ami-recipe create --file recipe.json --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    try:
        data = load_ami_recipe_file(file)
    except Exception as e:
        output_error(e, json_output, error_type="File Read Error")
        raise click.ClickException(str(e))

    output_status("Creating AMI recipe...", json_output)
    try:
        result = api_client.create_edh_ami_recipe(data)
        output_result(result, json_output, success_message="AMI recipe created", cli_formatter=format_json)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@ami_recipe.command(name='update')
@click.argument('recipe_id')
@click.option('--file', required=True, type=click.Path(exists=True), help='Path to recipe JSON file')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
def ami_recipe_update(ctx: click.Context, recipe_id: str, file: str, json_output: bool):
    """Update an existing admin AMI recipe from a JSON file. File components are inlined relative to the file directory.

    Examples:
        vamscli edh admin ami-recipe update team-desktop --file recipe.json
        vamscli edh admin ami-recipe update team-desktop --file recipe.json --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    try:
        data = load_ami_recipe_file(file)
    except Exception as e:
        output_error(e, json_output, error_type="File Read Error")
        raise click.ClickException(str(e))

    output_status(f"Updating AMI recipe '{recipe_id}'...", json_output)
    try:
        result = api_client.update_edh_ami_recipe(recipe_id, data)
        output_result(result, json_output, success_message="AMI recipe updated", cli_formatter=format_json)
    except EdhAmiRecipeNotFoundError as e:
        output_error(e, json_output, error_type="Recipe Not Found",
                     helpful_message="Use 'vamscli edh admin ami-recipe list' to see every recipe.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@ami_recipe.command(name='duplicate')
@click.argument('source_id')
@click.option('--recipe-id', required=True, help='Recipe ID of the copy')
@click.option('--display-name', default=None,
              help="Display name of the copy (default: the source's display name plus ' (copy)')")
@click.option('--stack-name', default=None,
              help="EDH stack name of the copy, which no other recipe may use (default: the source's "
                   "stack name plus ' Copy')")
@click.option('--keep-versions', default=None, type=click.IntRange(KEEP_VERSIONS_MIN, KEEP_VERSIONS_MAX),
              help=f"Number of AMIs pruning keeps for the copy ({KEEP_VERSIONS_MIN}-{KEEP_VERSIONS_MAX}; "
                   "default: the source's keepVersions)")
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
def ami_recipe_duplicate(ctx: click.Context, source_id: str, recipe_id: str, display_name: Optional[str],
                         stack_name: Optional[str], keep_versions: Optional[int], json_output: bool):
    """Duplicate an AMI recipe, shipped or admin, into a new admin recipe to update and build. The copy
    takes the source's definition and bundled files (assets) and records duplicatedFrom; its bundled
    files cannot be changed. No build starts.

    Examples:
        vamscli edh admin ami-recipe duplicate al2023-desktop --recipe-id team-desktop
        vamscli edh admin ami-recipe duplicate isaacsim --recipe-id isaacsim-lab --stack-name "Isaac Sim Lab"
        vamscli edh admin ami-recipe duplicate al2023-desktop --recipe-id team-desktop --keep-versions 5
        vamscli edh admin ami-recipe duplicate al2023-desktop --recipe-id team-desktop --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status(f"Duplicating AMI recipe '{source_id}' as '{recipe_id}'...", json_output)
    try:
        result = api_client.duplicate_edh_ami_recipe(source_id, recipe_id, display_name=display_name,
                                                     stack_name=stack_name, keep_versions=keep_versions)
        output_result(result, json_output, success_message="AMI recipe duplicated", cli_formatter=format_json)
    except EdhAmiRecipeNotFoundError as e:
        output_error(e, json_output, error_type="Recipe Not Found",
                     helpful_message="Use 'vamscli edh admin ami-recipe list' to see every recipe.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@ami_recipe.command(name='delete')
@click.argument('recipe_id')
@click.option('--confirm', is_flag=True, help='Confirm recipe deletion (required)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
def ami_recipe_delete(ctx: click.Context, recipe_id: str, confirm: bool, json_output: bool):
    """Delete an admin AMI recipe: the Image Builder pipeline, recipe, configurations and own-account
    components its last build recorded, and every build row; a config recipe is refused. The AMIs,
    snapshots, Image Builder images and EDH stacks its builds still hold stay, and without the build
    rows VAMS cannot retire or prune them, so first retire the AVAILABLE builds, and the
    REGISTER_FAILED builds whose EDH stack exists, that should go. The delete is refused while a
    build's AMI removal is pending: a RETIRED build with amiRemovalPending, or a RETIRED,
    REGISTER_FAILED or FAILED build outside the recipe's keepVersions newest AMIs that still holds its
    AMI, as while an Amazon EC2 instance uses the AMI. Delete the recipe once the sweep has
    deregistered those AMIs (amiDeregisteredAt). Requires --confirm.

    Examples:
        vamscli edh admin ami-recipe delete team-desktop --confirm
        vamscli edh admin ami-recipe delete team-desktop --confirm --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    if not confirm:
        _confirmation_required("Deleting an AMI recipe", "recipeId", recipe_id, json_output)

    output_status(f"Deleting AMI recipe '{recipe_id}'...", json_output)
    try:
        result = api_client.delete_edh_ami_recipe(recipe_id)
        output_result(result, json_output, success_message="AMI recipe deleted", cli_formatter=format_json)
    except EdhAmiRecipeNotFoundError as e:
        output_error(e, json_output, error_type="Recipe Not Found",
                     helpful_message="Use 'vamscli edh admin ami-recipe list' to see every recipe.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@ami_recipe.command(name='build')
@click.argument('recipe_id')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
def ami_recipe_build(ctx: click.Context, recipe_id: str, json_output: bool):
    """Start a new EC2 Image Builder build for an AMI recipe.

    The build is queued behind a build of other content already in progress. A
    recipe whose current content is already queued or building reports that build
    instead of starting another.

    Examples:
        vamscli edh admin ami-recipe build al2023-desktop
        vamscli edh admin ami-recipe build al2023-desktop --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status(f"Starting build for AMI recipe '{recipe_id}'...", json_output)
    try:
        result = api_client.start_edh_ami_recipe_build(recipe_id)
        version = result.get('version', 'N/A')
        outcome = result.get('outcome', '')
        if outcome == AMI_BUILD_IN_PROGRESS:
            message = f"Build already in progress: {recipe_id} v{version}"
        else:
            message = f"Build v{version} {outcome}"
        output_result(result, json_output, success_message=message, cli_formatter=format_json)
    except EdhAmiRecipeNotFoundError as e:
        output_error(e, json_output, error_type="Recipe Not Found",
                     helpful_message="Use 'vamscli edh admin ami-recipe list' to see every recipe.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@ami_recipe.command(name='builds')
@click.argument('recipe_id')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
def ami_recipe_builds(ctx: click.Context, recipe_id: str, json_output: bool):
    """List all builds for an AMI recipe.

    Examples:
        vamscli edh admin ami-recipe builds al2023-desktop
        vamscli edh admin ami-recipe builds al2023-desktop --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status(f"Retrieving builds for AMI recipe '{recipe_id}'...", json_output)
    try:
        result = api_client.list_edh_ami_recipe_builds(recipe_id)
        items = result.get('Items', []) if isinstance(result, dict) else []
        output_result(result, json_output, success_message=f"Found {len(items)} build(s)",
                      cli_formatter=format_ami_build_list)
    except EdhAmiRecipeNotFoundError as e:
        output_error(e, json_output, error_type="Recipe Not Found",
                     helpful_message="Use 'vamscli edh admin ami-recipe list' to see every recipe.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@ami_recipe.command(name='register')
@click.argument('recipe_id')
@click.argument('version', type=int)
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
def ami_recipe_register(ctx: click.Context, recipe_id: str, version: int, json_output: bool):
    """Retry the EDH registration of a REGISTER_FAILED build and reset its automatic attempt count.

    A build registers automatically when its image completes. Read `status` in the returned build:
    AVAILABLE when the retry registered it (the recipe's other AVAILABLE builds are then retired), or
    REGISTER_FAILED with the reason in `error`.

    Examples:
        vamscli edh admin ami-recipe register al2023-desktop 1
        vamscli edh admin ami-recipe register al2023-desktop 1 --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status(f"Registering AMI recipe '{recipe_id}' build v{version} in EDH...", json_output)
    try:
        result = api_client.register_edh_ami_recipe_build(recipe_id, version)
        output_result(result, json_output, success_message="Build registered in EDH", cli_formatter=format_json)
    except EdhAmiRecipeNotFoundError as e:
        output_error(e, json_output, error_type="Recipe Not Found",
                     helpful_message="Use 'vamscli edh admin ami-recipe list' to see every recipe.")
        raise click.ClickException(str(e))
    except EdhAmiRecipeBuildNotFoundError as e:
        output_error(e, json_output, error_type="Build Not Found",
                     helpful_message=f"Use 'vamscli edh admin ami-recipe builds {recipe_id}' to see its builds.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@ami_recipe.command(name='retire')
@click.argument('recipe_id')
@click.argument('version', type=int)
@click.option('--confirm', is_flag=True, help='Confirm build retirement (required)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_AMI_RECIPES, EDH_AMI_RECIPES_DISABLED_MESSAGE)
def ami_recipe_retire(ctx: click.Context, recipe_id: str, version: int, confirm: bool, json_output: bool):
    """Retire an AMI build version: deactivates the EDH software stack, then deregisters the AMI and
    its snapshots and deletes the Image Builder image version once no Amazon EC2 instance uses the
    AMI. While an instance uses it, the build stays RETIRED with amiRemovalPending and amiInUseBy
    (the instance ids), and VAMS removes the AMI after the last instance is terminated. Requires
    --confirm.

    Examples:
        vamscli edh admin ami-recipe retire al2023-desktop 1 --confirm
        vamscli edh admin ami-recipe retire al2023-desktop 1 --confirm --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    if not confirm:
        _confirmation_required("Retiring an AMI build", "version", str(version), json_output)

    output_status(f"Retiring AMI recipe '{recipe_id}' build v{version}...", json_output)
    try:
        result = api_client.retire_edh_ami_recipe_build(recipe_id, version)
        output_result(result, json_output, success_message="Build retired", cli_formatter=format_json)
    except EdhAmiRecipeNotFoundError as e:
        output_error(e, json_output, error_type="Recipe Not Found",
                     helpful_message="Use 'vamscli edh admin ami-recipe list' to see every recipe.")
        raise click.ClickException(str(e))
    except EdhAmiRecipeBuildNotFoundError as e:
        output_error(e, json_output, error_type="Build Not Found",
                     helpful_message=f"Use 'vamscli edh admin ami-recipe builds {recipe_id}' to see its builds.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


# ---------------------------------------------------------------------------
# container
# ---------------------------------------------------------------------------


def _parse_environment(pairs) -> Dict[str, str]:
    """KEY=VALUE pairs into a dict; the split is on the first '=' so a value may contain one."""
    environment: Dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition('=')
        if not separator or not key:
            raise click.BadParameter(f"'{pair}' is not KEY=VALUE", param_hint='--env')
        environment[key] = value
    return environment


def _container_scope(backend: Optional[str], cluster: Optional[str],
                     namespace: Optional[str]) -> Dict[str, Optional[str]]:
    """The backend, cluster and namespace a job listing or kill is scoped to, as APIClient keyword
    arguments. The eks backend addresses jobs inside one cluster (required) and namespace (the server
    default is 'default'); Batch takes neither."""
    if backend == 'eks':
        if cluster is None:
            raise click.MissingParameter(message='It is required for the eks backend.',
                                         param_hint="'--cluster'", param_type='option')
    else:
        for option, value in (("'--cluster'", cluster), ("'--namespace'", namespace)):
            if value is not None:
                raise click.BadParameter('applies only to the eks backend', param_hint=option)
    return {'backend': backend, 'cluster': cluster, 'namespace': namespace}


@edh.group()
def container():
    """Submit and manage EDH container jobs (AWS Batch, or EKS, through EDH)."""
    pass


@container.command(name='catalog')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_CONTAINERS, EDH_CONTAINERS_DISABLED_MESSAGE)
def container_catalog(ctx: click.Context, json_output: bool):
    """Show the registered job definitions, job queues and EKS clusters as EDH reports them.

    Each item carries its kind (jobDefinition, jobQueue or eksCluster); the body's eksEnabled flag
    says whether the deployment accepts the eks backend.

    Examples:
        vamscli edh container catalog
        vamscli edh container catalog --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving container catalog...", json_output)

    try:
        result = api_client.list_edh_container_catalog()
        output_result(result, json_output, success_message="Container catalog retrieved",
                      cli_formatter=format_json)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@container.group()
def job():
    """Submit, list and kill container jobs."""
    pass


@job.command(name='submit')
@click.option('--job-definition', required=True,
              help='Job definition name from `edh container catalog` (the container image URI for eks)')
@click.option('--name', required=True, help='Job name')
@click.option('--backend', required=True, type=click.Choice(CONTAINER_BACKENDS),
              help='Compute backend the job runs on: batch, or eks (experimental)')
@click.option('--job-queue', required=True,
              help='Batch job queue from `edh container catalog` (the EKS cluster name for eks)')
@click.option('--command', 'command_parts', multiple=True,
              help='Container command token; repeat once per token')
@click.option('--env', 'env_pairs', multiple=True,
              help='Environment variable as KEY=VALUE; repeat once per variable')
@click.option('--vcpus', type=click.IntRange(min=1), default=None, help='vCPUs requested')
@click.option('--memory', type=click.IntRange(min=MIN_JOB_MEMORY_MIB), default=None,
              help=f'Memory requested in MiB (at least {MIN_JOB_MEMORY_MIB})')
@click.option('--gpu', type=click.IntRange(min=0), default=None, help='GPUs requested')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_CONTAINERS, EDH_CONTAINERS_DISABLED_MESSAGE)
def job_submit(ctx: click.Context, job_definition: str, name: str, backend: str, job_queue: str,
               command_parts, env_pairs, vcpus: Optional[int], memory: Optional[int],
               gpu: Optional[int], json_output: bool):
    """Submit a container job to EDH.

    The job runs on the Batch job queue named by --job-queue. With --backend eks (experimental),
    --job-definition is the container image URI and --job-queue the EKS cluster name, and the job runs
    in the cluster's default namespace. EDH accepts the job and answers at once (HTTP 202) with the
    jobId that `job list` shows and `job kill` takes.

    Examples:
        vamscli edh container job submit --job-definition render-def --name render-1 --job-queue gpu-queue --backend batch
        vamscli edh container job submit --job-definition render-def --name render-1 --job-queue gpu-queue --backend batch --command python --command main.py --env MODE=headless --vcpus 4 --memory 16384 --gpu 1 --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    data: Dict[str, Any] = {
        'jobDefinition': job_definition,
        'name': name,
        'jobQueue': job_queue,
        'backend': backend,
    }
    if command_parts:
        data['command'] = list(command_parts)
    if env_pairs:
        data['environment'] = _parse_environment(env_pairs)
    if vcpus is not None:
        data['vcpus'] = vcpus
    if memory is not None:
        data['memory'] = memory
    if gpu is not None:
        data['gpu'] = gpu

    output_status(f"Submitting container job '{name}'...", json_output)

    try:
        result = api_client.submit_edh_container_job(data)
        output_result(result, json_output, success_message="Container job submitted",
                      cli_formatter=format_json)
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@job.command(name='list')
@click.option('--backend', type=click.Choice(CONTAINER_BACKENDS), default=None,
              help='Compute backend whose jobs to list: batch (the deployment default), or eks (experimental)')
@click.option('--cluster', default=None, help='EKS cluster name (required with --backend eks)')
@click.option('--namespace', default=None,
              help='Kubernetes namespace within the cluster (eks only; deployment default: default)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_CONTAINERS, EDH_CONTAINERS_DISABLED_MESSAGE)
def job_list(ctx: click.Context, backend: Optional[str], cluster: Optional[str], namespace: Optional[str],
             json_output: bool):
    """List your container jobs on one backend.

    Each job carries its jobId (the value `job kill` takes), jobName, status, backend and
    edhJobDefinition beside the fields EDH reports; another user's jobs are never included.

    Examples:
        vamscli edh container job list
        vamscli edh container job list --backend eks --cluster sim-cluster
        vamscli edh container job list --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)
    scope = _container_scope(backend, cluster, namespace)

    output_status("Retrieving container jobs...", json_output)

    try:
        result = api_client.list_edh_container_jobs(**scope)
        items = result.get('Items', []) if isinstance(result, dict) else []
        output_result(
            result,
            json_output,
            success_message=f"Found {len(items)} job(s)",
            cli_formatter=lambda r: format_items(r, "jobs"),
        )
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))


@job.command(name='kill')
@click.argument('job_id')
@click.option('--backend', type=click.Choice(CONTAINER_BACKENDS), default=None,
              help='Compute backend the job runs on: batch (the deployment default), or eks (experimental)')
@click.option('--cluster', default=None, help='EKS cluster name (required with --backend eks)')
@click.option('--namespace', default=None,
              help='Kubernetes namespace within the cluster (eks only; deployment default: default)')
@click.option('--confirm', is_flag=True, help='Confirm killing the job (required)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_EDH_CONTAINERS, EDH_CONTAINERS_DISABLED_MESSAGE)
def job_kill(ctx: click.Context, job_id: str, backend: Optional[str], cluster: Optional[str],
             namespace: Optional[str], confirm: bool, json_output: bool):
    """Kill one of your container jobs by JOB_ID. Irreversible; --confirm is required.

    JOB_ID is the job's jobId as `edh container job list` shows it: a Batch job id, or an EKS job
    name inside the cluster and namespace named by --cluster and --namespace.

    Examples:
        vamscli edh container job kill JOB_ID --confirm
        vamscli edh container job kill render-1 --backend eks --cluster sim-cluster --confirm --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)
    scope = _container_scope(backend, cluster, namespace)

    if not confirm:
        _confirmation_required("Job kill", "jobId", job_id, json_output)

    output_status(f"Killing container job '{job_id}'...", json_output)

    try:
        result = api_client.kill_edh_container_job(job_id, **scope)
        output_result(result, json_output, success_message="Container job kill requested",
                      cli_formatter=format_json)
    except EdhContainerJobNotFoundError as e:
        output_error(e, json_output, error_type="Job Not Found",
                     helpful_message="Use 'vamscli edh container job list' to see your jobs and their jobId values.")
        raise click.ClickException(str(e))
    except EdhError as e:
        output_error(e, json_output, error_type="EDH Error")
        raise click.ClickException(str(e))
