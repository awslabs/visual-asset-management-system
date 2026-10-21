# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared EDH REST client for the EDH Workstations add-on Lambdas.

Every call carries the cluster root key in ``X-EDH-TOKEN`` and an acting user in ``X-EDH-USER``:
the caller's opaque EDH uid on a user-scoped route; on a cluster-scoped one, the uid its caller
names, or the service actor ``vamsservice`` when it names none. EDH stamps ownership from that
header, and EDH's feature flags evaluate it on the routes they gate: ``VIRTUAL_DESKTOPS`` on every
desktop route except the session-state read, and each other area's flag on its own routes; the
directory user probe and the project listing and update carry none. No call goes out without one.
The root key is read from Secrets Manager, cached for an hour and re-read once when EDH answers
401. EDH answers every route with a ``{"success": bool, "message": ...}`` envelope -- 200 on
success and, for almost every business refusal, 500 -- so a refusal is recognised by the envelope
and classified by the wording of its message, never by the status alone. Write routes read form
fields: a body goes out ``application/x-www-form-urlencoded`` (multipart only when a thumbnail file
part is attached), never as JSON.

Two timing profiles exist because two kinds of caller exist. API-facing handlers run behind an
API Gateway integration timeout whose floor is 29 s, so the ``api`` profile bounds ALL EDH calls of
one request inside a shared ``time.monotonic()`` deadline of ``EDH_REQUEST_BUDGET_SECONDS`` and
retries only GET; the deploy-time registration custom resource and scheduled workers use the
``batch`` profile with the longer connect/read budget. Each attempt's connect and read timeouts are
clamped so their sum never exceeds what remains of the deadline, which is what bounds a request:
the ``api`` pair alone, twice, plus its backoff would run past the budget. No profile retries a
mutation: a POST/PUT/DELETE that timed out may have been executed, and a second desktop is worse than
a 502 the caller reconciles by polling. A request sent once that fails after it may have reached EDH
is ``EdhOutcomeUnknownError``, a caller's cue to read EDH's state before it concludes anything; one
that failed in the connect phase never left and stays ``EdhUnavailableError``.

The pool always verifies the server certificate; a private-CA bundle, when configured, is written to
/tmp once and handed to urllib3 as ``ca_certs``. Log lines carry the method, route, status and
attempt; never the headers, the secret payload or a successful response body.
"""

import json
import os
import re
import secrets
import ssl
import string
import time
import urllib.parse
from typing import Any, Dict, NamedTuple, Optional, Tuple

import boto3
import urllib3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from urllib3.util.ssl_ import create_urllib3_context

from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, general_error
from .edhSessionState import (
    EDH_FIELD_INSTANCE_ID, EDH_SCHEDULE_DAYS, EDH_SCHEDULE_FIELD, EDH_SESSION_ID_FIELD, EDH_SESSION_STATE_FIELD,
)

logger = safeLogger(service_name="EdhClient")

_retry_config = BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"})

# Module-level config, read once at cold start; a missing value aborts the import.
try:
    EDH_API_ENDPOINT = os.environ["EDH_API_ENDPOINT"]
    EDH_ADMIN_KEY_SECRET_ARN = os.environ["EDH_ADMIN_KEY_SECRET_ARN"]
    EDH_AUTO_PROVISION_USERS = os.environ["EDH_AUTO_PROVISION_USERS"]
    # Empty when the EDH ALB certificate chains to a publicly trusted root.
    EDH_CA_BUNDLE_SECRET_ARN = os.environ.get("EDH_CA_BUNDLE_SECRET_ARN", "")
    # The name the EDH certificate is verified against when it is not the endpoint host; empty
    # verifies the host of EDH_API_ENDPOINT.
    EDH_API_TLS_SERVER_NAME = os.environ.get("EDH_API_TLS_SERVER_NAME", "").strip()
    # EDH routes are absolute /api/... paths; an endpoint that carried a path would be joined twice.
    if urllib.parse.urlsplit(EDH_API_ENDPOINT).path.strip("/"):
        raise ValueError("EDH_API_ENDPOINT must be the scheme and host of the EDH ALB, without a path")
except Exception:
    logger.exception("Failed loading EDH environment configuration")
    raise

# Header and secret contract with EDH.
EDH_USER_HEADER = "X-EDH-USER"
EDH_TOKEN_HEADER = "X-EDH-TOKEN"
EDH_ROOT_KEY_SECRET_FIELD = "rootKey"
# EDH answers every route as {"success": bool, "message": <payload or text>}.
EDH_RESPONSE_SUCCESS_KEY = "success"
EDH_RESPONSE_PAYLOAD_KEY = "message"
# Write routes read form fields; a file part (a software-stack thumbnail) makes the body multipart.
EDH_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"

# Request-latency budget for API-facing callers. API Gateway applies one integration timeout to
# every route and getConfig() enforces 29 s as its FLOOR, so the arithmetic holds for every
# deployment. The reserve covers cold start (Casbin + boto3 + the SSM resource-name fetch, ~4 s), the
# Tier-1 enforcer build, response serialisation and the audit write; what remains is shared by every
# EDH round trip of one request through the client's monotonic deadline.
API_GATEWAY_TIMEOUT_FLOOR_SECONDS = 29
REQUEST_OVERHEAD_RESERVE_SECONDS = 4.7
EDH_REQUEST_BUDGET_SECONDS = 20

# How long a root key read from Secrets Manager is reused before it is fetched again.
_SECRET_CACHE_TTL_SECONDS = 3600
_CA_BUNDLE_PATH = "/tmp/edh-ca-bundle.pem"
# Upstream error bodies are logged for diagnosis, truncated to this many bytes.
_ERROR_BODY_LOG_LIMIT = 512

# Lower-cased substrings of an EDH refusal message. EDH signals almost every refusal as a 500 carrying
# a success:false envelope, so the class of a refusal is read from its wording.
EDH_NOT_FOUND_MARKERS = ("does not exist", "not found", "unable to find", "does not seems to exist")
EDH_QUOTA_ERROR_MARKERS = (
    "max number of linux or windows session already reached",
    "target nodes. delete one",
    "budget allocation for this project has been exceeded",
    "quota",
)
# Read before the not-found markers, since EDH's create refusal opens with "unable to find". A bare
# "capacity" would also take EDH's dry-run refusal of an AMI that does not exist.
EDH_CAPACITY_ERROR_MARKERS = (
    "unable to find available capacity",
    "available aws capacity",
    "no reserved capacity available",
    "insufficientinstancecapacity",
    "reservationcapacityexceeded",
)

# The one method a failed attempt may repeat: a GET that failed was not executed, or reads the same
# state again. Everything else is sent exactly once.
_RETRIED_METHODS = frozenset({"GET"})

# An attempt is started only while at least this much budget remains: urllib3 refuses a zero timeout
# and a TLS handshake alone needs about this long, so a shorter attempt could only time out.
_MIN_ATTEMPT_SECONDS = 1.0

# EDH routes: absolute /api/... paths joined to the endpoint origin (_build_url).
EDH_ROUTE_DESKTOPS_LIST = "/api/dcv/virtual_desktops/list"
EDH_ROUTE_DESKTOPS_LIST_ALL = "/api/dcv/virtual_desktops/list_all"
EDH_ROUTE_DESKTOPS_SESSION_STATE = "/api/dcv/virtual_desktops/session_state"
EDH_ROUTE_DESKTOPS_CREATE = "/api/dcv/virtual_desktops/create"
EDH_ROUTE_DESKTOPS_STOP = "/api/dcv/virtual_desktops/stop"
EDH_ROUTE_DESKTOPS_START = "/api/dcv/virtual_desktops/start"
EDH_ROUTE_DESKTOPS_DELETE = "/api/dcv/virtual_desktops/delete"
EDH_ROUTE_DESKTOPS_SCHEDULE = "/api/dcv/virtual_desktops/schedule"
EDH_SOFTWARE_STACK_ROUTES = {
    "dcv": "/api/dcv/virtual_desktops/software_stacks",
    "target_nodes": "/api/target_nodes/software_stacks",
}
EDH_PROFILE_ROUTES = {
    "dcv": "/api/dcv/virtual_desktops/profiles",
    "target_nodes": "/api/target_nodes/profiles",
}
EDH_ROUTE_LDAP_USER = "/api/ldap/user"
# The whole-directory listing; the user probe is GET EDH_ROUTE_LDAP_USER?user=<uid>, so no wrapper reads it.
EDH_ROUTE_LDAP_USERS = "/api/ldap/users"
EDH_ROUTE_BATCH_JOB_DEFINITIONS = "/api/containers/batch/job_definition"
EDH_ROUTE_BATCH_JOB_QUEUES = "/api/containers/batch/job_queue"
EDH_ROUTE_EKS_CLUSTERS = "/api/containers/eks/list_clusters"
EDH_CONTAINER_JOB_ROUTES = {
    "batch": "/api/containers/batch/job",
    "eks": "/api/containers/eks/job",
}
EDH_CONTAINER_JOBS_ROUTES = {
    "batch": "/api/containers/batch/jobs",
    "eks": "/api/containers/eks/jobs",
}
EDH_CONTAINER_BACKENDS = tuple(EDH_CONTAINER_JOB_ROUTES)

# The X-EDH-USER value of a call given no caller uid. The cluster-scoped calls made without a caller
# carry it -- the reconciler's list_all, the catalog reads, the deploy-time and AMI-recipe catalog
# writes, the profile listings, the directory probe and create, the container catalog listings; the
# admin views name the admin's uid on list_all and on their catalog writes, and the launch worker's
# read-back names the row's edhUid on list_all. EDH's feature flags evaluate it on the routes they gate:
# VIRTUAL_DESKTOPS on every desktop route except the session-state read, and each other area's flag on
# its own routes; the directory user probe and the project listing and update carry none. A cluster
# whose allow-lists are non-empty therefore admits this uid; EDH records it as the acting user in its
# audit table and created_by/deactivated_by. It fits EDH's username rule so an allow-list can name it.
EDH_SERVICE_ACTOR_UID = "vamsservice"

# Form fields each write route parses; EDH's parser drops anything else without a word.
EDH_DESKTOP_CREATE_FIELDS = frozenset({
    "instance_type", "software_stack_id", "disk_size", "session_name", "project", "subnet_id",
    "hibernate", "tenancy", "nested_virtualization", "session_type", "capacity_reservation_id", "spot",
    "bootstrap_cache_bypass", "on_behalf_of",
})
EDH_DESKTOP_SCHEDULE_FIELDS = frozenset({EDH_SESSION_ID_FIELD, EDH_SCHEDULE_FIELD})
# The last minute of the day a schedule window may name; EDH's bounds are 0 and this, inclusive.
EDH_SCHEDULE_LAST_MINUTE = 1440
# Desktop row fields the client reads back from the list routes.
EDH_SESSION_NAME_FIELD = "session_name"
EDH_SESSION_OWNER_FIELD = "session_owner"
EDH_SESSION_CREATED_ON_FIELD = "created_on"
# EDH rewrites session_name to [a-zA-Z0-9]{1,32}; a uuid4 bootstrap id fits as its 32 hex digits.
_SESSION_NAME_PATTERN = re.compile(r"[a-zA-Z0-9]{1,32}")
# The session-state read: how many session ids one request asks for (comma-joined under session_uuid),
# the state key of each entry, and the key of an entry EDH answers with a successor session's values.
EDH_SESSION_STATE_BATCH_SIZE = 40
EDH_DESKTOP_STATE_FIELD = "state"
EDH_DESKTOP_SUCCESSOR_FIELD = "current_session_uuid"
# A desktop session id as EDH issues it (uuid4); only an id in this form is sent in a query.
_SESSION_UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

EDH_SOFTWARE_STACK_REGISTER_FIELDS = {
    "dcv": frozenset({
        "ami_id", "stack_name", "base_os", "root_size", "launch_tenancy", "launch_host",
        "virtual_desktop_profile_id", "description", "share_scope", "volume_acceleration",
    }),
    "target_nodes": frozenset({
        "ami_id", "stack_name", "root_size", "launch_tenancy", "launch_host", "user_data_variables",
        "target_node_profile_id", "connection_string", "target_node_user_data_id", "description",
    }),
}
EDH_SOFTWARE_STACK_THUMBNAIL_PART = "thumbnail"
# The catalog row's id as the list routes return it, and the form field the DELETE routes read.
EDH_SOFTWARE_STACK_ID_FIELD = "id"
EDH_SOFTWARE_STACK_DELETE_FIELD = "software_stack_id"
# An empty DCV catalog is a 500 refusal, not an empty mapping.
EDH_EMPTY_CATALOG_MARKERS = ("no ami found",)

# Projects. EDH resolves a user's launchable software stacks through the projects a stack is attached
# to, and its project update is a full replace: a field the update leaves out is cleared, so every
# field read is echoed back. The listing reports the target-node membership as
# `target_node_software_stack_ids`; the update reads it as `target_nodes_software_stack_ids`.
EDH_ROUTE_PROJECTS = "/api/projects"
EDH_PROJECT_ID_FIELD = "id"
EDH_PROJECT_NAME_FIELD = "project_name"
EDH_PROJECT_ACTIVE_FIELD = "is_active"
EDH_PROJECT_DESCRIPTION_FIELD = "description"
EDH_PROJECT_APPLICATION_PROFILE_FIELD = "application_profile_ids"
# The membership field names (listed by the GET, read by the PUT) per software-stack kind.
EDH_PROJECT_STACK_ID_FIELDS = {
    "dcv": ("software_stack_ids", "software_stack_ids"),
    "target_nodes": ("target_node_software_stack_ids", "target_nodes_software_stack_ids"),
}
# Access lists the update requires: EDH answers a missing one with CLIENT_MISSING_PARAMETER.
EDH_PROJECT_MEMBERSHIP_FIELDS = ("allowed_users", "denied_users", "allowed_groups", "denied_groups")
EDH_PROJECT_UPDATE_FIELDS = frozenset({
    "project_id", EDH_PROJECT_DESCRIPTION_FIELD, "aws_budget", "hardware_profile_id",
    EDH_PROJECT_APPLICATION_PROFILE_FIELD, *EDH_PROJECT_MEMBERSHIP_FIELDS,
    *(written for _listed, written in EDH_PROJECT_STACK_ID_FIELDS.values()),
})
# Project fields written back only when the project carries one: EDH reads a blank budget as no budget.
EDH_PROJECT_OPTIONAL_FIELDS = ("aws_budget", "hardware_profile_id")

# Directory user create: user, password, sudoers (int), email, shell, uid, gid; there is no display name.
EDH_LDAP_USER_FIELD = "user"
EDH_LDAP_PASSWORD_FIELD = "password"
EDH_LDAP_EMAIL_FIELD = "email"
EDH_LDAP_USER_STATIC_FIELDS = {"sudoers": 0, "shell": "/bin/bash"}
EDH_LDAP_EMAIL_DOMAIN = "vams.invalid"
EDH_LDAP_CREATE_FIELDS = frozenset({
    EDH_LDAP_USER_FIELD, EDH_LDAP_PASSWORD_FIELD, EDH_LDAP_EMAIL_FIELD, "sudoers", "shell", "uid", "gid",
})
# Lower-cased substrings of an EDH refusal that mean the directory user already exists.
EDH_USER_EXISTS_MARKERS = ("already exist",)
# The throw-away directory password: never stored, logged or reused (a workstation user signs in through
# the DCV connection token). Every class is present so any directory policy accepts it.
EDH_PASSWORD_LENGTH = 24
_PASSWORD_CLASSES = (string.ascii_lowercase, string.ascii_uppercase, string.digits, "!#$%&*+-=?_~")

# Container jobs.
EDH_BATCH_SUBMIT_FIELDS = frozenset({
    "job_name", "job_definition", "job_queue", "job_dependencies", "array_size", "overrideCommands",
    "envVars", "scheduling_priority", "job_attempts", "execution_timeout", "vcpus", "memory",
    "consumableResources",
})
EDH_EKS_SUBMIT_FIELDS = frozenset({
    "image_uri", "eks_cluster", "job_name", "cpu", "memory", "gpu", "instance_type", "overrideCommands",
    "overrideArgs", "envVars", "namespace",
})
EDH_JOB_ID_FIELD = "job_id"
EDH_BATCH_KILL_FIELDS = frozenset({EDH_JOB_ID_FIELD, "reason"})
EDH_EKS_KILL_FIELDS = frozenset({"eks_cluster", "job_name", "namespace"})
EDH_EKS_DEFAULT_NAMESPACE = "default"
# EKS job rows come keyed by the job's metadata.uid; the key is injected under this name.
EDH_EKS_JOB_UID_FIELD = "uid"
# The batch jobs list looks back 24 h unless told otherwise; a year keeps every job a caller can act on.
EDH_BATCH_JOBS_TIME_RANGE_HOURS = 8760
# A job handle names the backend and the key EDH kills by: batch:<jobId>, eks:<cluster>/<namespace>/<name>.
EDH_JOB_HANDLE_FIELD = "job_handle"
_BATCH_JOB_ID_PATTERN = re.compile(r"Job ID:\s*(\S+)")
_UNKNOWN_JOB_ID = "unknown"

_NOT_JSON = object()

_FALLBACK_RESPONSE = (502, "EDH is unavailable. Try again later.")
# Follows the contract message when the request was a job submit: a job may be running under the answer
# the client could not read, so a plain retry would submit it twice.
_JOB_SUBMITTED_CONTRACT_SUFFIX = " Check the job list before retrying."
# Body field set on the answer to a request whose outcome EDH did not confirm, so a caller can report it as
# unconfirmed rather than as failed.
OUTCOME_UNKNOWN_FIELD = "outcomeUnknown"
# Body field set on the refusal of a workstation start while an earlier start of it may still be under way.
START_IN_PROGRESS_FIELD = "startInProgress"


class _Profile(NamedTuple):
    connect_timeout: float
    read_timeout: float
    attempts: int
    backoff_cap: float
    budget: float
    # The read timeout of a request sent exactly once, clamped like every other to what remains.
    single_attempt_read_timeout: float


# api: connect 3 s, read 8 s, two attempts, backoff capped at 1 s. Twice the pair plus the backoff
# would run to 23 s, so the per-attempt clamp against the deadline (EdhClient._attempt_timeout) is
# what keeps a request inside the 20 s budget. A request sent once has no second attempt to save for,
# so it reads for whatever the budget leaves after its connect phase: EDH's start can outlast 8 s (it
# waits for a capacity reservation to settle before it starts the instance), and an answer given up on
# is an outcome the caller must then go and look for. batch: for callers that CloudFormation or a
# schedule waits on.
_PROFILES = {
    "api": _Profile(connect_timeout=3.0, read_timeout=8.0, attempts=2, backoff_cap=1.0,
                    budget=float(EDH_REQUEST_BUDGET_SECONDS),
                    single_attempt_read_timeout=float(EDH_REQUEST_BUDGET_SECONDS)),
    "batch": _Profile(connect_timeout=10.0, read_timeout=60.0, attempts=3, backoff_cap=10.0,
                      budget=240.0, single_attempt_read_timeout=60.0),
}


def _backoff_seconds(profile: _Profile, attempt: int) -> float:
    """The pause after attempt ``attempt`` (0-based): exponential, capped by the profile."""
    return float(min(2 ** attempt, profile.backoff_cap))


def _backoff_total(profile: _Profile) -> float:
    """Every pause a request with the profile's attempt count can spend."""
    return sum(_backoff_seconds(profile, attempt) for attempt in range(profile.attempts - 1))


class EdhError(Exception):
    """Base class for EDH client errors; ``status`` is the EDH HTTP status when there was one."""

    def __init__(self, message: str, status: Optional[int] = None, body_text: str = ""):
        super().__init__(message)
        self.status = status
        self.body_text = body_text


# Failures of an attempt that never sent a byte of the request: urllib3's connect phase
# (HTTPConnection._new_conn raises ConnectTimeoutError, or its subclasses NewConnectionError for a
# refused connection and NameResolutionError for a name that does not resolve) and the pool's own setup
# (the CA bundle). Everything else -- a read timeout, which is also what a stalled TLS handshake raises,
# or a ProtocolError or SSLError from a connection reset or closed -- can follow the send.
_UNSENT_ERRORS = (urllib3.exceptions.ConnectTimeoutError, EdhError)


class EdhAuthError(EdhError):
    """EDH rejected the root key (401), redirected to its login page (3xx), or the root key secret is
    unusable or could not be read."""


class EdhApiError(EdhError):
    """EDH refused the request: a refusal envelope that is neither auth, not-found, quota nor capacity,
    or a 4xx EDH did not wrap."""


class EdhQuotaError(EdhError):
    """EDH refused the request because a quota, session count or budget is exhausted."""


class EdhCapacityError(EdhError):
    """EDH found no available AWS capacity for the instance type, by the wording of EDH's refusal."""


class EdhNotFoundError(EdhError):
    """The EDH resource does not exist for this caller, by the wording of EDH's refusal."""


class EdhUnavailableError(EdhError):
    """EDH could not be reached, answered a 5xx without a refusal envelope, the budget ran out, or the
    CA bundle its connection needs could not be loaded."""


class EdhOutcomeUnknownError(EdhUnavailableError):
    """A request sent once lost its answer after it may have reached EDH -- a read timeout, a connection
    reset or closed while the request was on its way or its answer was being read -- so EDH may have
    carried it out. The caller learns the outcome by reading EDH's state."""


class EdhContractError(EdhError):
    """EDH answered success but its body could not be read -- a 2xx that is not JSON, or an accepted
    job whose id is missing from the prose -- so the request may have taken effect. ``job_submitted``
    marks the container job submit, whose unreadable answer leaves a running job to look for."""

    def __init__(self, message: str, status: Optional[int] = None, body_text: str = "", *,
                 job_submitted: bool = False):
        super().__init__(message, status, body_text)
        self.job_submitted = job_submitted


# Client-facing message per typed error, the first match winning, so a subclass precedes its base; the
# upstream text stays in the log. A request whose outcome is unknown may still be in progress in EDH, so
# its message does not invite a plain retry.
_ERROR_RESPONSES = (
    (EdhQuotaError, 429, "The EDH quota for this operation has been reached."),
    (EdhCapacityError, 400, "EDH found no available AWS capacity for this instance type. Try again later."),
    (EdhNotFoundError, 404, "The requested EDH resource was not found."),
    (EdhAuthError, 502, "EDH rejected the deployment credential."),
    (EdhOutcomeUnknownError, 502,
     "EDH did not confirm the request in time; it may still be in progress. Refresh before trying again."),
    (EdhUnavailableError, 502, "EDH is unavailable. Try again later."),
    (EdhContractError, 502, "EDH accepted the request but returned an unreadable answer."),
    (EdhApiError, 400, "EDH rejected the request."),
)


# Lazy module-level singletons, kept across warm invocations.
_secretsmanager = None
_http = None
_cached_root_key: Optional[str] = None
_cached_root_key_fetched_at: float = 0.0
_ca_bundle_path: Optional[str] = None
_ca_bundle_resolved = False


def _get_secretsmanager_client():
    global _secretsmanager
    if _secretsmanager is None:
        _secretsmanager = boto3.client("secretsmanager", config=_retry_config)
    return _secretsmanager


def _load_edh_root_key() -> str:
    """The EDH root key, read from Secrets Manager and cached for ``_SECRET_CACHE_TTL_SECONDS``."""
    global _cached_root_key, _cached_root_key_fetched_at
    if (
        _cached_root_key is not None
        and (time.time() - _cached_root_key_fetched_at) < _SECRET_CACHE_TTL_SECONDS
    ):
        return _cached_root_key
    try:
        response = _get_secretsmanager_client().get_secret_value(SecretId=EDH_ADMIN_KEY_SECRET_ARN)
    except (ClientError, BotoCoreError) as exc:
        logger.warning("EDH root key secret read failed", extra={"error": exc.__class__.__name__})
        raise EdhAuthError("The EDH root key secret could not be read") from exc
    try:
        payload = json.loads(response["SecretString"])
    except (KeyError, TypeError, ValueError):
        raise EdhAuthError("The EDH root key secret is not a JSON object")
    root_key = payload.get(EDH_ROOT_KEY_SECRET_FIELD) if isinstance(payload, dict) else None
    if not isinstance(root_key, str) or root_key == "":
        raise EdhAuthError(f"The EDH root key secret carries no {EDH_ROOT_KEY_SECRET_FIELD}")
    _cached_root_key = root_key
    _cached_root_key_fetched_at = time.time()
    return root_key


def _invalidate_secret_cache() -> None:
    """Drop the cached root key so the next load re-reads Secrets Manager."""
    global _cached_root_key
    _cached_root_key = None


def _load_ca_bundle_path() -> Optional[str]:
    """Path of the private CA bundle written to /tmp, or None when EDH presents a public chain. A
    bundle that cannot be read or written raises ``EdhUnavailableError`` and stays unresolved, so the
    next call reads it again."""
    global _ca_bundle_path, _ca_bundle_resolved
    if _ca_bundle_resolved:
        return _ca_bundle_path
    if EDH_CA_BUNDLE_SECRET_ARN:
        try:
            response = _get_secretsmanager_client().get_secret_value(SecretId=EDH_CA_BUNDLE_SECRET_ARN)
            with open(_CA_BUNDLE_PATH, "w", encoding="utf-8") as handle:
                handle.write(response["SecretString"])
        except (ClientError, BotoCoreError, OSError) as exc:
            logger.warning("EDH CA bundle could not be loaded", extra={"error": exc.__class__.__name__})
            raise EdhUnavailableError("The EDH CA bundle could not be loaded") from exc
        _ca_bundle_path = _CA_BUNDLE_PATH
    _ca_bundle_resolved = True
    return _ca_bundle_path


def _tls_context_for_server_name(ca_bundle: Optional[str]) -> ssl.SSLContext:
    """The TLS context used when ``EDH_API_TLS_SERVER_NAME`` is set: the chain is verified against the
    bundle, and the name is matched against the certificate's subject alternative names or, for a
    certificate that carries none (the one the EDH installer creates), its common name."""
    context = create_urllib3_context()
    if ca_bundle:
        context.load_verify_locations(cafile=ca_bundle)
    context.hostname_checks_common_name = True
    return context


def _get_http_pool() -> urllib3.PoolManager:
    global _http
    if _http is None:
        pool_kwargs: Dict[str, Any] = {"cert_reqs": "CERT_REQUIRED"}
        ca_bundle = _load_ca_bundle_path()
        if ca_bundle:
            pool_kwargs["ca_certs"] = ca_bundle
        if EDH_API_TLS_SERVER_NAME:
            # The certificate is verified as this name in place of the endpoint host.
            pool_kwargs["ssl_context"] = _tls_context_for_server_name(ca_bundle)
            pool_kwargs["assert_hostname"] = EDH_API_TLS_SERVER_NAME
        _http = urllib3.PoolManager(**pool_kwargs)
    return _http


def _http_request(method: str, url: str, **kwargs):
    """Thin wrapper around the module-level urllib3 pool; the single seam tests monkeypatch.

    urllib3's own retry object and its redirect following are disabled: the client owns attempts and
    the deadline, a library-level retry would multiply both, and a redirect is an answer to classify
    rather than a hop to follow.
    """
    return _get_http_pool().request(method, url, retries=False, redirect=False, **kwargs)


def _reset_client_state_for_tests() -> None:
    """Test hook: clear module-level caches and singletons."""
    global _secretsmanager, _http, _cached_root_key, _cached_root_key_fetched_at
    global _ca_bundle_path, _ca_bundle_resolved
    _secretsmanager = None
    _http = None
    _cached_root_key = None
    _cached_root_key_fetched_at = 0.0
    _ca_bundle_path = None
    _ca_bundle_resolved = False


def _build_url(path: str, params: Optional[dict]) -> str:
    """The URL for an EDH route: the endpoint origin plus the route's absolute ``/api/...`` path."""
    route = path if path.startswith("/") else f"/{path}"
    url = EDH_API_ENDPOINT.rstrip("/") + route
    if params:
        pairs = [(key, _form_value(value)) for key, value in params.items() if value is not None]
        url = f"{url}?{urllib.parse.urlencode(pairs)}"
    return url


def _form_value(value) -> str:
    """One form or query value as EDH's parsers read it: booleans as ``true``/``false``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _encode_form(form: dict) -> bytes:
    """The urlencoded body of one form; a None value leaves its field out."""
    pairs = [(key, _form_value(value)) for key, value in form.items() if value is not None]
    return urllib.parse.urlencode(pairs).encode("utf-8")


def _encode_multipart(form: dict, files: dict):
    """``(body, content_type)`` for a form carrying file parts, each ``(filename, bytes, mime)``."""
    fields: Dict[str, Any] = {key: _form_value(value) for key, value in form.items() if value is not None}
    fields.update(files)
    return urllib3.encode_multipart_formdata(fields)


def _check_form_fields(form: dict, allowed: frozenset, route: str) -> None:
    """Refuse a field EDH's parser would silently drop."""
    unknown = sorted(set(form) - allowed)
    if unknown:
        raise ValueError(f"Fields not accepted by {route}: {', '.join(unknown)}")


def _check_schedule(schedule) -> None:
    """Refuse a schedule EDH's scheduler could not read back: anything but a dict whose seven days each
    carry integer ``start`` and ``stop`` minutes inside the day. EDH's own validator is looser (a Python
    literal, string minutes), and its scheduler skips or fails on what that lets through."""
    if not isinstance(schedule, dict):
        raise ValueError("An EDH schedule is a dict of day windows")
    for day in EDH_SCHEDULE_DAYS:
        window = schedule.get(day)
        if not isinstance(window, dict):
            raise ValueError(f"The EDH schedule carries no window for {day}")
        for bound in ("start", "stop"):
            minute = window.get(bound)
            if type(minute) is not int or not 0 <= minute <= EDH_SCHEDULE_LAST_MINUTE:
                raise ValueError(f"The EDH schedule's {day} {bound} is not a minute of the day")


def _decode_json(raw: bytes):
    """The parsed JSON body, None for an empty body, ``_NOT_JSON`` for anything else."""
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _NOT_JSON


def _is_refusal_envelope(parsed) -> bool:
    return isinstance(parsed, dict) and parsed.get(EDH_RESPONSE_SUCCESS_KEY) is False


def _refusal_text(parsed: dict) -> str:
    message = parsed.get(EDH_RESPONSE_PAYLOAD_KEY, "")
    return message if isinstance(message, str) else json.dumps(message)


def _mentions(text: str, markers) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _payload(parsed):
    """The EDH payload inside a parsed response: the ``message`` of the envelope."""
    if isinstance(parsed, dict) and EDH_RESPONSE_PAYLOAD_KEY in parsed:
        return parsed[EDH_RESPONSE_PAYLOAD_KEY]
    return parsed


def _as_list(payload, key_field: Optional[str] = None) -> list:
    """A list payload as-is; an id-keyed mapping as its values, each dict value given its key under
    ``key_field`` when it lacks it; a string (EDH's empty-result sentinel) or None as an empty list."""
    if payload is None or isinstance(payload, str):
        return []
    if isinstance(payload, list):
        return list(payload)
    if isinstance(payload, dict):
        items = []
        for key, value in payload.items():
            if key_field and isinstance(value, dict) and key_field not in value:
                value = {key_field: key, **value}
            items.append(value)
        return items
    return [payload]


def _as_dict(payload) -> dict:
    return payload if isinstance(payload, dict) else {EDH_RESPONSE_PAYLOAD_KEY: payload}


def _route_for(routes: Dict[str, str], kind: str) -> str:
    if kind not in routes:
        raise ValueError(f"Unknown EDH route kind: {kind}")
    return routes[kind]


def _generate_password(length: int = EDH_PASSWORD_LENGTH) -> str:
    """A throw-away password with at least one character of every class, in random order."""
    chars = [secrets.choice(group) for group in _PASSWORD_CLASSES]
    alphabet = "".join(_PASSWORD_CLASSES)
    chars.extend(secrets.choice(alphabet) for _ in range(length - len(chars)))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _ldap_user_form(edh_uid: str) -> dict:
    """The create form for a directory user; the password is fresh on every call."""
    return {
        EDH_LDAP_USER_FIELD: edh_uid,
        EDH_LDAP_PASSWORD_FIELD: _generate_password(),
        EDH_LDAP_EMAIL_FIELD: f"{edh_uid}@{EDH_LDAP_EMAIL_DOMAIN}",
        **EDH_LDAP_USER_STATIC_FIELDS,
    }


def edh_session_name(bootstrap_id) -> str:
    """The EDH session name of a workstation: its uuid4 bootstrap id without hyphens (32 hex digits),
    which EDH's ``[a-zA-Z0-9]{1,32}`` sanitiser passes unchanged."""
    name = str(bootstrap_id).replace("-", "")
    if not _SESSION_NAME_PATTERN.fullmatch(name):
        raise ValueError("bootstrapId does not reduce to an EDH session name")
    return name


def _newest(rows: list) -> Optional[dict]:
    """The row with the latest ``created_on`` (ISO-8601 strings order lexically); None for no rows."""
    if not rows:
        return None
    return max(rows, key=lambda row: str(row.get(EDH_SESSION_CREATED_ON_FIELD) or ""))


def _parse_batch_job_id(message) -> str:
    """The job id EDH embeds in its submit prose. EDH composes the prose after the job is submitted, so a
    missing or ``unknown`` id is a contract failure of an accepted job, never a refusal."""
    text = message if isinstance(message, str) else json.dumps(message)
    match = _BATCH_JOB_ID_PATTERN.search(text)
    if not match or match.group(1) == _UNKNOWN_JOB_ID:
        # EDH's prose is the only record of the job it accepted.
        logger.warning("EDH accepted the job without returning its id",
                       extra={"edhRoute": EDH_CONTAINER_JOB_ROUTES["batch"], "edhBody": text[:_ERROR_BODY_LOG_LIMIT]})
        raise EdhContractError("EDH accepted the job without returning its id", status=200, body_text=text,
                               job_submitted=True)
    return match.group(1)


def _job_handle(backend: str, *parts) -> str:
    return f"{backend}:{'/'.join(str(part) for part in parts)}"


def _job_handle_parts(backend: str, handle) -> tuple:
    """The EDH kill key inside a job handle: ``(job_id,)`` for batch, ``(cluster, namespace, name)``
    for eks. A bare key without the ``<backend>:`` prefix is accepted."""
    text = str(handle)
    prefix = f"{backend}:"
    if text.startswith(prefix):
        text = text[len(prefix):]
    if backend == "batch":
        if not text or "/" in text or ":" in text:
            raise ValueError("A batch job handle is batch:<jobId>")
        return (text,)
    parts = text.split("/")
    if len(parts) != 3 or not all(parts):
        raise ValueError("An eks job handle is eks:<cluster>/<namespace>/<jobName>")
    return tuple(parts)


def _with_job_handle(backend: str, row):
    """One job row from a list route, given its handle when the row carries the key parts."""
    if not isinstance(row, dict):
        return row
    if backend == "batch":
        job_id = row.get("jobId")
        return {**row, EDH_JOB_HANDLE_FIELD: _job_handle("batch", job_id)} if job_id else row
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    cluster, namespace, name = row.get("eks_cluster"), metadata.get("namespace"), metadata.get("name")
    if cluster and namespace and name:
        return {**row, EDH_JOB_HANDLE_FIELD: _job_handle("eks", cluster, namespace, name)}
    return row


def _classify_error(method: str, path: str, status: int, parsed, body_text: str) -> EdhError:
    """The typed error for an answer that is not a success: by status for the credential, by wording
    for an EDH refusal envelope, by status again for anything EDH did not answer itself."""
    if status == 401:
        return EdhAuthError(f"EDH rejected the root key for {method} {path}", status=status, body_text=body_text)
    if 300 <= status < 400:
        return EdhAuthError(f"EDH redirected {method} {path} to its login page", status=status, body_text=body_text)
    if _is_refusal_envelope(parsed):
        if _mentions(body_text, EDH_CAPACITY_ERROR_MARKERS):
            return EdhCapacityError(f"EDH found no capacity for {method} {path}", status=status,
                                    body_text=body_text)
        if _mentions(body_text, EDH_NOT_FOUND_MARKERS):
            return EdhNotFoundError(f"EDH has no such resource for {method} {path}", status=status,
                                    body_text=body_text)
        if _mentions(body_text, EDH_QUOTA_ERROR_MARKERS):
            return EdhQuotaError(f"EDH quota refused {method} {path}", status=status, body_text=body_text)
        return EdhApiError(f"EDH refused {method} {path} with {status}", status=status, body_text=body_text)
    if status == 404:
        return EdhNotFoundError(f"EDH has no such route for {method} {path}", status=status, body_text=body_text)
    if status >= 500:
        return EdhUnavailableError(f"EDH returned {status} for {method} {path}", status=status, body_text=body_text)
    if status == 429:
        return EdhUnavailableError(f"EDH infrastructure returned {status} for {method} {path}", status=status,
                                   body_text=body_text)
    return EdhApiError(f"EDH rejected {method} {path} with {status}", status=status, body_text=body_text)


def _error_for(method: str, path: str, response, parsed) -> EdhError:
    """Log a failed answer (status and truncated text, never headers) and build its typed error."""
    raw = response.data or b""
    if _is_refusal_envelope(parsed):
        body_text = _refusal_text(parsed)
    else:
        body_text = raw[:_ERROR_BODY_LOG_LIMIT].decode("utf-8", errors="replace")
    logger.warning("EDH error response",
                   extra={"edhMethod": method, "edhRoute": path, "edhStatus": response.status,
                          "edhBody": body_text[:_ERROR_BODY_LOG_LIMIT]})
    return _classify_error(method, path, response.status, parsed, body_text)


def _is_job_submit(method: str, path: str) -> bool:
    """Whether the request was a container job submit: the one route whose unreadable success leaves a
    running job behind."""
    return method == "POST" and path in EDH_CONTAINER_JOB_ROUTES.values()


def _parse_response(method: str, path: str, response) -> dict:
    """A successful body as its parsed envelope; every other answer as its typed error."""
    raw = response.data or b""
    parsed = _decode_json(raw)
    if 200 <= response.status < 300 and not _is_refusal_envelope(parsed):
        if not raw:
            return {}
        if parsed is _NOT_JSON:
            body_text = raw[:_ERROR_BODY_LOG_LIMIT].decode("utf-8", errors="replace")
            logger.warning("EDH returned a non-JSON body",
                           extra={"edhMethod": method, "edhRoute": path, "edhStatus": response.status,
                                  "edhBody": body_text})
            raise EdhContractError(f"EDH returned a non-JSON body for {method} {path}", status=response.status,
                                   body_text=body_text, job_submitted=_is_job_submit(method, path))
        return parsed if isinstance(parsed, dict) else {EDH_RESPONSE_PAYLOAD_KEY: parsed}
    raise _error_for(method, path, response, parsed)


class EdhClient:
    """EDH REST client bound to one timing profile and one deadline.

    ``deadline`` is a ``time.monotonic()`` instant; when omitted it is ``now + profile.budget``.
    Handlers create one client per request so every EDH call of that request shares the budget.
    """

    def __init__(self, profile: str = "api", deadline: Optional[float] = None) -> None:
        if profile not in _PROFILES:
            raise ValueError(f"Unknown EDH client profile: {profile}")
        self.profile_name = profile
        self._profile = _PROFILES[profile]
        self._deadline = deadline if deadline is not None else time.monotonic() + self._profile.budget

    def remaining_seconds(self) -> float:
        return self._deadline - time.monotonic()

    def _attempt_timeout(self, remaining: float, sent_once: bool) -> urllib3.util.Timeout:
        """Connect and read timeouts for one attempt, clamped so their sum never exceeds ``remaining``.

        With the whole budget left this is the profile's own pair, the read being the profile's
        single-attempt read for a request that is ``sent_once``; when less remains the connect phase
        takes at most half of it and the read phase what is left after connect.
        """
        connect = min(self._profile.connect_timeout, remaining / 2)
        read_timeout = self._profile.single_attempt_read_timeout if sent_once else self._profile.read_timeout
        read = min(read_timeout, remaining - connect)
        return urllib3.util.Timeout(connect=connect, read=read)

    def _headers(self, edh_uid: Optional[str]) -> Dict[str, str]:
        """The request headers: the root key and the acting user, the service actor when none is given."""
        return {EDH_TOKEN_HEADER: _load_edh_root_key(), "Accept": "application/json",
                "Accept-Language": "en", EDH_USER_HEADER: edh_uid or EDH_SERVICE_ACTOR_UID}

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = min(_backoff_seconds(self._profile, attempt), max(0.0, self.remaining_seconds()))
        # Pause between attempts, capped by the profile and by the deadline. Must be the last comment
        # line before the call -- semgrep attaches `nosemgrep` to the next line only.
        # nosemgrep: arbitrary-sleep
        time.sleep(delay)

    def _send(self, method: str, path: str, *, edh_uid: Optional[str], form: Optional[dict],
              files: Optional[dict], params: Optional[dict]):
        """One EDH exchange under the deadline and the retry policy; the raw urllib3 response."""
        url = _build_url(path, params)
        body: Optional[bytes] = None
        content_type: Optional[str] = None
        if files:
            body, content_type = _encode_multipart(form or {}, files)
        elif form is not None:
            body, content_type = _encode_form(form), EDH_FORM_CONTENT_TYPE
        sent_once = method not in _RETRIED_METHODS
        attempts = 1 if sent_once else self._profile.attempts
        attempt = 0
        refreshed_key = False
        while True:
            remaining = self.remaining_seconds()
            if remaining < _MIN_ATTEMPT_SECONDS:
                logger.warning("EDH request budget exhausted",
                               extra={"edhMethod": method, "edhRoute": path, "attempt": attempt + 1})
                raise EdhUnavailableError(f"EDH request budget exhausted before {method} {path}")
            headers = self._headers(edh_uid)
            if content_type:
                headers["Content-Type"] = content_type
            kwargs: Dict[str, Any] = {"headers": headers, "timeout": self._attempt_timeout(remaining, sent_once)}
            if body is not None:
                kwargs["body"] = body

            try:
                response = _http_request(method, url, **kwargs)
            except Exception as exc:
                logger.warning("EDH request failed",
                               extra={"edhMethod": method, "edhRoute": path, "attempt": attempt + 1,
                                      "error": exc.__class__.__name__})
                if attempt + 1 >= attempts:
                    if sent_once and not isinstance(exc, _UNSENT_ERRORS):
                        raise EdhOutcomeUnknownError(
                            f"EDH did not answer {method} {path}; it may have been carried out") from exc
                    raise EdhUnavailableError(f"EDH unreachable for {method} {path}") from exc
                self._sleep_before_retry(attempt)
                attempt += 1
                continue

            if response.status == 401 and not refreshed_key:
                # A 401 can mean the root key was rotated. EDH rejected the request before acting on
                # it, so re-sending it with a re-read key is safe for every method.
                logger.warning("EDH returned 401; re-reading the root key",
                               extra={"edhMethod": method, "edhRoute": path, "attempt": attempt + 1})
                _invalidate_secret_cache()
                refreshed_key = True
                continue

            if (500 <= response.status < 600 and attempt + 1 < attempts
                    and not _is_refusal_envelope(_decode_json(response.data or b""))):
                # A 5xx without EDH's envelope came from the tier in front of the handler or from a
                # crash; a refusal envelope is EDH's final answer and is not repeated.
                logger.warning("EDH returned a server error; retrying",
                               extra={"edhMethod": method, "edhRoute": path,
                                      "edhStatus": response.status, "attempt": attempt + 1})
                self._sleep_before_retry(attempt)
                attempt += 1
                continue

            return response

    def request(self, method: str, path: str, *, edh_uid: Optional[str] = None,
                form: Optional[dict] = None, files: Optional[dict] = None,
                params: Optional[dict] = None) -> dict:
        """Send one EDH request and return its parsed JSON envelope.

        ``edh_uid`` is the acting user ``X-EDH-USER`` names, the service actor when None. ``form`` is
        sent ``application/x-www-form-urlencoded`` -- what every EDH write route reads, DELETE
        included; ``files`` (``{part: (filename, bytes, mime)}``) switches the body to
        multipart/form-data with ``form`` as its text parts; ``params`` is the query string. Raises
        the typed ``EdhError`` subclasses.
        """
        response = self._send(method, path, edh_uid=edh_uid, form=form, files=files, params=params)
        return _parse_response(method, path, response)

    # -- DCV desktops --------------------------------------------------------------------------

    def list_all_desktops(self, edh_uid: str = EDH_SERVICE_ACTOR_UID) -> list:
        """Every active desktop on the cluster; the header names the acting user, not a filter."""
        parsed = self.request("GET", EDH_ROUTE_DESKTOPS_LIST_ALL, edh_uid=edh_uid)
        if parsed.get("success") is not True or not isinstance(parsed.get("message"), dict):
            raise EdhContractError(
                f"EDH list_all returned success={parsed.get('success')} with message "
                f"type {type(parsed.get('message')).__name__}; expected success=True + dict",
                status=200)
        return _as_list(_payload(parsed), key_field=EDH_SESSION_ID_FIELD)

    def get_desktop_states(self, edh_uid, session_ids) -> list:
        """The state EDH reports for each of ``session_ids``: per id EDH answers, in the order asked, a
        record carrying ``session_uuid``, plus ``session_state`` and ``instance_id`` when EDH gives them.
        EDH issues no connection token for this read. The route has no owner filter, so a caller passes
        only the session ids its own rows hold. An id that is not a UUID is never sent, so no comma
        reaches the query; an entry EDH answers with a successor session's values, an id not asked for
        and every other key are dropped."""
        if not edh_uid:
            raise ValueError("A desktop state read names the caller's EDH uid")
        ids = [str(session_id) for session_id in session_ids]
        asked = list(dict.fromkeys(text for text in ids if _SESSION_UUID_PATTERN.fullmatch(text)))
        malformed = sum(1 for text in ids if not _SESSION_UUID_PATTERN.fullmatch(text))
        if malformed:
            logger.warning(f"{malformed} session id(s) not in UUID form were not sent to EDH")
        records = []
        for start in range(0, len(asked), EDH_SESSION_STATE_BATCH_SIZE):
            batch = asked[start:start + EDH_SESSION_STATE_BATCH_SIZE]
            parsed = self.request("GET", EDH_ROUTE_DESKTOPS_SESSION_STATE, edh_uid=edh_uid,
                                  params={EDH_SESSION_ID_FIELD: ",".join(batch)})
            if parsed.get("success") is not True or not isinstance(parsed.get("message"), dict):
                raise EdhContractError(
                    f"EDH session_state returned success={parsed.get('success')} with message "
                    f"type {type(parsed.get('message')).__name__}; expected success=True + dict",
                    status=200)
            entries = parsed["message"]
            for session_id in batch:
                entry = entries.get(session_id)
                if not isinstance(entry, dict) or EDH_DESKTOP_SUCCESSOR_FIELD in entry:
                    continue
                record = {EDH_SESSION_ID_FIELD: session_id}
                state = entry.get(EDH_DESKTOP_STATE_FIELD)
                if isinstance(state, str) and state:
                    record[EDH_SESSION_STATE_FIELD] = state
                instance_id = entry.get(EDH_FIELD_INSTANCE_ID)
                if instance_id:
                    record[EDH_FIELD_INSTANCE_ID] = instance_id
                records.append(record)
        return records

    def get_desktop_connection(self, edh_uid, session_id) -> Optional[dict]:
        """EDH's per-user record of the caller's desktop ``session_id``, carrying its ``connection_string``,
        or None when EDH lists no such desktop of the caller. EDH issues a connection token on this read,
        at most one per request it answers, for that desktop: the list is narrowed to the one session. A
        record is returned only when its ``session_uuid`` is the id asked for and its ``session_owner``
        is the caller."""
        if not edh_uid:
            raise ValueError("A desktop connection read names the caller's EDH uid")
        if session_id is None or not _SESSION_UUID_PATTERN.fullmatch(str(session_id)):
            raise ValueError("A desktop connection read names one session id in UUID form")
        session_id = str(session_id)
        parsed = self.request("GET", EDH_ROUTE_DESKTOPS_LIST, edh_uid=edh_uid,
                              params={"is_active": True, EDH_SESSION_ID_FIELD: session_id})
        for row in _as_list(_payload(parsed), key_field=EDH_SESSION_ID_FIELD):
            if (isinstance(row, dict)
                    and str(row.get(EDH_SESSION_ID_FIELD)) == session_id
                    and row.get(EDH_SESSION_OWNER_FIELD) is not None
                    and row.get(EDH_SESSION_OWNER_FIELD) == edh_uid):
                return row
        return None

    def find_desktop_by_name(self, edh_uid, session_name: str) -> Optional[dict]:
        """The caller's newest active desktop named ``session_name``, read from the cluster listing as the
        caller, or None while EDH lists none. EDH issues no connection token for this read. The listing
        spans every owner, so only a record whose ``session_owner`` is the caller matches; a record
        without an owner never does."""
        if not edh_uid:
            raise ValueError("A desktop read-back names the caller's EDH uid")
        matches = [row for row in self.list_all_desktops(edh_uid=edh_uid)
                   if isinstance(row, dict)
                   and row.get(EDH_SESSION_NAME_FIELD) == session_name
                   and row.get(EDH_SESSION_OWNER_FIELD) is not None
                   and row.get(EDH_SESSION_OWNER_FIELD) == edh_uid]
        return _newest(matches)

    def create_desktop(self, edh_uid, form: dict) -> dict:
        """Start a desktop. EDH answers with prose only; the uuid is read back by session name."""
        _check_form_fields(form, EDH_DESKTOP_CREATE_FIELDS, EDH_ROUTE_DESKTOPS_CREATE)
        parsed = self.request("POST", EDH_ROUTE_DESKTOPS_CREATE, edh_uid=edh_uid, form=form)
        return _as_dict(_payload(parsed))

    def stop_desktop(self, edh_uid, session_id) -> dict:
        parsed = self.request("PUT", EDH_ROUTE_DESKTOPS_STOP, edh_uid=edh_uid,
                              form={EDH_SESSION_ID_FIELD: session_id})
        return _as_dict(_payload(parsed))

    def start_desktop(self, edh_uid, session_id) -> dict:
        parsed = self.request("PUT", EDH_ROUTE_DESKTOPS_START, edh_uid=edh_uid,
                              form={EDH_SESSION_ID_FIELD: session_id})
        return _as_dict(_payload(parsed))

    def delete_desktop(self, edh_uid, session_id) -> dict:
        parsed = self.request("DELETE", EDH_ROUTE_DESKTOPS_DELETE, edh_uid=edh_uid,
                              form={EDH_SESSION_ID_FIELD: session_id})
        return _as_dict(_payload(parsed))

    def update_desktop_schedule(self, edh_uid, session_id, schedule: dict) -> dict:
        """Set the minutes of each day EDH's scheduler starts and stops the desktop at. EDH looks the
        desktop up under the acting user as its owner, and stores the value it is sent for its scheduler
        to ``json.loads``, so the schedule goes out as JSON with integer minutes."""
        _check_schedule(schedule)
        form = {EDH_SESSION_ID_FIELD: session_id, EDH_SCHEDULE_FIELD: json.dumps(schedule)}
        _check_form_fields(form, EDH_DESKTOP_SCHEDULE_FIELDS, EDH_ROUTE_DESKTOPS_SCHEDULE)
        parsed = self.request("PUT", EDH_ROUTE_DESKTOPS_SCHEDULE, edh_uid=edh_uid, form=form)
        return _as_dict(_payload(parsed))

    # -- Software stacks and profiles ------------------------------------------------------------

    def list_software_stacks(self, kind: str, *, edh_uid: str = EDH_SERVICE_ACTOR_UID) -> list:
        """The catalog rows of one kind, each carrying ``id``. EDH words an empty DCV catalog as the
        refusal "No AMI found"; that is an empty list here."""
        route = _route_for(EDH_SOFTWARE_STACK_ROUTES, kind)
        try:
            parsed = self.request("GET", route, edh_uid=edh_uid)
        except EdhApiError as exc:
            if _mentions(exc.body_text, EDH_EMPTY_CATALOG_MARKERS):
                return []
            raise
        return _as_list(_payload(parsed), key_field=EDH_SOFTWARE_STACK_ID_FIELD)

    def register_software_stack(self, kind: str, fields: dict, *, edh_uid: str = EDH_SERVICE_ACTOR_UID,
                                thumbnail=None) -> dict:
        """Register an image; ``thumbnail`` is an optional ``(filename, bytes, mime)`` file part. EDH
        answers with prose and no id: the new row is found by ``stack_name`` in the list."""
        route = _route_for(EDH_SOFTWARE_STACK_ROUTES, kind)
        _check_form_fields(fields, EDH_SOFTWARE_STACK_REGISTER_FIELDS[kind], route)
        files = {EDH_SOFTWARE_STACK_THUMBNAIL_PART: thumbnail} if thumbnail else None
        parsed = self.request("POST", route, edh_uid=edh_uid, form=fields, files=files)
        return _as_dict(_payload(parsed))

    def delete_software_stack(self, kind: str, image_id, *, edh_uid: str = EDH_SERVICE_ACTOR_UID) -> dict:
        route = _route_for(EDH_SOFTWARE_STACK_ROUTES, kind)
        parsed = self.request("DELETE", route, edh_uid=edh_uid,
                              form={EDH_SOFTWARE_STACK_DELETE_FIELD: image_id})
        return _as_dict(_payload(parsed))

    def list_profiles(self, kind: str, *, edh_uid: str = EDH_SERVICE_ACTOR_UID) -> list:
        """The profiles of one kind, each carrying ``id``; EDH's "none found" sentinel is an empty list."""
        parsed = self.request("GET", _route_for(EDH_PROFILE_ROUTES, kind), edh_uid=edh_uid)
        return _as_list(_payload(parsed), key_field=EDH_SOFTWARE_STACK_ID_FIELD)

    # -- Projects ----------------------------------------------------------------------------------

    def list_projects(self, *, edh_uid: str = EDH_SERVICE_ACTOR_UID) -> list:
        """The cluster's active projects, each carrying ``id``. EDH lists only active ones and words an
        empty table as the message "No Project found"; that is an empty list here."""
        parsed = self.request("GET", EDH_ROUTE_PROJECTS, edh_uid=edh_uid)
        return _as_list(_payload(parsed), key_field=EDH_PROJECT_ID_FIELD)

    def _project_named(self, project_name: str, edh_uid: str) -> dict:
        for project in self.list_projects(edh_uid=edh_uid):
            if (isinstance(project, dict)
                    and str(project.get(EDH_PROJECT_NAME_FIELD)) == project_name
                    and project.get(EDH_PROJECT_ACTIVE_FIELD, True)):
                return project
        raise EdhNotFoundError(f"EDH has no active project named '{project_name}'")

    def assign_software_stack_to_project(self, kind: str, stack_id, project_name: str, *,
                                         edh_uid: str = EDH_SERVICE_ACTOR_UID) -> bool:
        """Attach a software stack to the named project; returns whether an update was sent.

        A stack is launchable only through a project that carries it. The update replaces the project
        wholesale, so every field the listing reported goes back with the id added to the kind's
        membership; a stack the project already carries sends nothing.
        """
        if kind not in EDH_PROJECT_STACK_ID_FIELDS:
            raise ValueError(f"Unknown EDH route kind: {kind}")
        project = self._project_named(project_name, edh_uid)
        wanted = str(stack_id)
        membership = {}
        for member_kind, (listed, written) in EDH_PROJECT_STACK_ID_FIELDS.items():
            ids = [str(value) for value in (project.get(listed) or [])]
            if member_kind == kind:
                if wanted in ids:
                    return False
                ids = [*ids, wanted]
            membership[written] = ",".join(ids)
        form = {
            "project_id": str(project.get(EDH_PROJECT_ID_FIELD)),
            EDH_PROJECT_DESCRIPTION_FIELD: project.get(EDH_PROJECT_DESCRIPTION_FIELD) or "",
            EDH_PROJECT_APPLICATION_PROFILE_FIELD: ",".join(
                str(value) for value in (project.get(EDH_PROJECT_APPLICATION_PROFILE_FIELD) or [])),
            **{field: project.get(field) or "" for field in EDH_PROJECT_MEMBERSHIP_FIELDS},
            **membership,
        }
        form.update({field: project[field] for field in EDH_PROJECT_OPTIONAL_FIELDS
                     if project.get(field) is not None})
        _check_form_fields(form, EDH_PROJECT_UPDATE_FIELDS, EDH_ROUTE_PROJECTS)
        self.request("PUT", EDH_ROUTE_PROJECTS, edh_uid=edh_uid, form=form)
        return True

    # -- Directory users ---------------------------------------------------------------------------

    def user_exists(self, edh_uid: str) -> bool:
        """Whether the directory has ``edh_uid``: the lookup answers 200 with the entries found, [] for none.
        The service actor is the acting user; the probed uid may not exist yet."""
        parsed = self.request("GET", EDH_ROUTE_LDAP_USER, edh_uid=EDH_SERVICE_ACTOR_UID,
                              params={EDH_LDAP_USER_FIELD: edh_uid})
        return bool(_as_list(_payload(parsed)))

    def ensure_user(self, edh_uid: str, display_name: Optional[str] = None) -> None:
        """Make sure EDH knows ``edh_uid``: probe the directory, then create only when allowed.

        User creation runs a directory poll and key generation inside EDH and outlasts the api
        budget, so this is a batch-profile call. A user the directory already lists costs the probe
        alone. With provisioning enabled an unlisted user is created, and an already-exists refusal of
        that create (a concurrent launch won the race) is success. With provisioning disabled an
        unlisted user raises ``EdhNotFoundError``. ``display_name`` has no EDH sink -- the directory
        API stores none -- and is accepted for the caller's records only.
        """
        if self.profile_name != "batch":
            raise ValueError("ensure_user runs under the batch profile: user creation outlasts the api budget")
        if self.user_exists(edh_uid):
            return
        if EDH_AUTO_PROVISION_USERS != "true":
            raise EdhNotFoundError("The EDH user is not provisioned")
        try:
            # The service actor creates the user: the new uid is not a directory principal yet.
            self.request("POST", EDH_ROUTE_LDAP_USER, edh_uid=EDH_SERVICE_ACTOR_UID, form=_ldap_user_form(edh_uid))
        except EdhApiError as exc:
            if _mentions(exc.body_text, EDH_USER_EXISTS_MARKERS):
                return
            raise

    # -- Container jobs ----------------------------------------------------------------------------

    def list_batch_job_definitions(self, status: Optional[str] = None, *,
                                   edh_uid: str = EDH_SERVICE_ACTOR_UID) -> list:
        """The job definitions EDH lists for this cluster (ACTIVE unless ``status`` says otherwise)."""
        parsed = self.request("GET", EDH_ROUTE_BATCH_JOB_DEFINITIONS, edh_uid=edh_uid, params={"status": status})
        return _as_list(_payload(parsed))

    def list_batch_queues(self, *, edh_uid: str = EDH_SERVICE_ACTOR_UID) -> list:
        return _as_list(_payload(self.request("GET", EDH_ROUTE_BATCH_JOB_QUEUES, edh_uid=edh_uid)))

    def list_eks_clusters(self, *, edh_uid: str = EDH_SERVICE_ACTOR_UID) -> list:
        """The names of the EKS clusters EDH lists for this cluster."""
        return _as_list(_payload(self.request("GET", EDH_ROUTE_EKS_CLUSTERS, edh_uid=edh_uid)))

    def submit_job(self, edh_uid, backend: str, form: dict) -> dict:
        """Submit a job: EDH's prose plus the handle the job is listed and killed by."""
        route = _route_for(EDH_CONTAINER_JOB_ROUTES, backend)
        allowed = EDH_BATCH_SUBMIT_FIELDS if backend == "batch" else EDH_EKS_SUBMIT_FIELDS
        _check_form_fields(form, allowed, route)
        message = _payload(self.request("POST", route, edh_uid=edh_uid, form=form))
        if backend == "batch":
            job_id = _parse_batch_job_id(message)
            return {EDH_JOB_ID_FIELD: job_id, EDH_JOB_HANDLE_FIELD: _job_handle("batch", job_id),
                    EDH_RESPONSE_PAYLOAD_KEY: message}
        # EDH names an EKS job by (cluster, namespace, lower-cased name) and returns none of them.
        cluster = str(form.get("eks_cluster") or "")
        namespace = str(form.get("namespace") or EDH_EKS_DEFAULT_NAMESPACE)
        name = str(form.get("job_name") or "").lower()
        return {"eks_cluster": cluster, "namespace": namespace, "job_name": name,
                EDH_JOB_HANDLE_FIELD: _job_handle("eks", cluster, namespace, name),
                EDH_RESPONSE_PAYLOAD_KEY: message}

    def list_jobs(self, edh_uid, backend: str, *, cluster: Optional[str] = None,
                  namespace: Optional[str] = None,
                  time_range_hours: int = EDH_BATCH_JOBS_TIME_RANGE_HOURS) -> list:
        """The caller's jobs on one backend, each given its handle. Batch always names the look-back
        window (EDH's default hides jobs older than a day); eks needs the cluster EDH requires."""
        route = _route_for(EDH_CONTAINER_JOBS_ROUTES, backend)
        if backend == "batch":
            parsed = self.request("GET", route, edh_uid=edh_uid, params={"time_range": time_range_hours})
            rows = _as_list(_payload(parsed))
        else:
            if not cluster:
                raise ValueError("Listing eks jobs needs the cluster name")
            parsed = self.request("GET", route, edh_uid=edh_uid,
                                  params={"cluster": cluster, "namespace": namespace or EDH_EKS_DEFAULT_NAMESPACE})
            rows = _as_list(_payload(parsed), key_field=EDH_EKS_JOB_UID_FIELD)
        return [_with_job_handle(backend, row) for row in rows]

    def kill_job(self, edh_uid, backend: str, job_id) -> dict:
        """Terminate a job named by its handle (``batch:<jobId>`` / ``eks:<cluster>/<ns>/<name>``)."""
        route = _route_for(EDH_CONTAINER_JOB_ROUTES, backend)
        parts = _job_handle_parts(backend, job_id)
        if backend == "batch":
            form = {EDH_JOB_ID_FIELD: parts[0]}
        else:
            form = dict(zip(("eks_cluster", "namespace", "job_name"), parts))
        parsed = self.request("DELETE", route, edh_uid=edh_uid, form=form)
        return _as_dict(_payload(parsed))


def edh_error_response_parts(err: EdhError) -> Tuple[int, str]:
    """The status code and curated message for a typed EDH error; never the upstream body."""
    for error_class, status_code, message in _ERROR_RESPONSES:
        if isinstance(err, error_class):
            if isinstance(err, EdhContractError) and err.job_submitted:
                message += _JOB_SUBMITTED_CONTRACT_SUFFIX
            return status_code, message
    return _FALLBACK_RESPONSE


def edh_error_reason(err: EdhError) -> str:
    """The curated one-line reason for a typed EDH error, prefixed with its type name."""
    return f"{type(err).__name__}: {edh_error_response_parts(err)[1]}"


def outcome_unknown_fields(err: EdhError) -> Dict[str, bool]:
    """``{"outcomeUnknown": True}`` for an error whose outcome EDH did not confirm -- an answer that never
    came, or a job submit EDH accepted without naming the job; nothing otherwise."""
    unknown = isinstance(err, EdhOutcomeUnknownError) or (isinstance(err, EdhContractError) and err.job_submitted)
    return {OUTCOME_UNKNOWN_FIELD: True} if unknown else {}


def map_edh_error_to_response(err: EdhError, event) -> APIGatewayProxyResponseV2:
    """The API response for a typed EDH error: a curated message, never the upstream body, plus
    ``outcomeUnknown`` when EDH did not confirm the outcome."""
    status_code, message = edh_error_response_parts(err)
    return general_error(status_code=status_code, body={"message": message, **outcome_unknown_fields(err)},
                         event=event)
