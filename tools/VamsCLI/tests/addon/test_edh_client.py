# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""APIClient methods for the EDH Workstations add-on routes.

Every method posts to a constant that `tests/test_constants_contract.py` cross-checks against the
backend route registry, passes `raise_http_errors=True` so the typed EDH exception mapping is live
(with the default, `_make_request` converts a 404 into a generic `APIError` before the method's own
handler runs), and returns the parsed body unaltered — the EDH handlers use no `message` envelope.
"""

import ast
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from vamscli import addon_constants, constants
from vamscli.utils.addon import edh_api_client, edh_exceptions
from vamscli.utils.addon.edh_exceptions import (
    EdhAmiRecipeBuildNotFoundError,
    EdhAmiRecipeNotFoundError,
    EdhContainerJobNotFoundError,
    EdhImageNotFoundError,
    EdhOperationError,
    EdhOutcomeUnknownError,
    EdhStartInProgressError,
    EdhUnavailableError,
    EdhWorkstationNotFoundError,
    InvalidEdhRequestError,
)
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import (
    APIError,
    AuthenticationError,
    BusinessLogicError,
    GlobalInfrastructureError,
    InvalidRequestPathError,
    PermissionDeniedError,
)
from vamscli.utils.json_output import output_error


EXPECTED_ROUTES = {
    "API_EDH_WORKSTATIONS": "/addon/edh/workstations",
    "API_EDH_WORKSTATION": "/addon/edh/workstations/{sessionId}",
    "API_EDH_WORKSTATION_STOP": "/addon/edh/workstations/{sessionId}/stop",
    "API_EDH_WORKSTATION_START": "/addon/edh/workstations/{sessionId}/start",
    "API_EDH_WORKSTATION_CONNECT": "/addon/edh/workstations/{sessionId}/connect",
    "API_EDH_WORKSTATION_WINDOWS_SIGN_IN": "/addon/edh/workstations/{sessionId}/windows-sign-in",
    "API_EDH_WORKSTATION_RENEW": "/addon/edh/workstations/{sessionId}/renew",
    "API_EDH_CATALOG_IMAGES": "/addon/edh/catalog/images",
    "API_EDH_CATALOG_INSTANCE_TYPES": "/addon/edh/catalog/instance-types",
    "API_EDH_CATALOG_LEASE": "/addon/edh/catalog/lease",
    "API_EDH_ADMIN_IMAGES": "/addon/edh/admin/images",
    "API_EDH_ADMIN_IMAGES_VDI": "/addon/edh/admin/images/vdi",
    "API_EDH_ADMIN_IMAGES_SIMULATION": "/addon/edh/admin/images/simulation",
    "API_EDH_ADMIN_IMAGE_VDI": "/addon/edh/admin/images/vdi/{imageId}",
    "API_EDH_ADMIN_IMAGE_SIMULATION": "/addon/edh/admin/images/simulation/{imageId}",
    "API_EDH_ADMIN_INSTANCES": "/addon/edh/admin/instances",
    "API_EDH_ADMIN_INSTANCES_STALE": "/addon/edh/admin/instances/stale",
    "API_EDH_ADMIN_INSTANCE_STOP": "/addon/edh/admin/instances/{sessionId}/stop",
    "API_EDH_ADMIN_INSTANCE_DEACTIVATE": "/addon/edh/admin/instances/{sessionId}/deactivate",
    "API_EDH_ADMIN_CAPACITY": "/addon/edh/admin/capacity",
    "API_EDH_ADMIN_HEALTH": "/addon/edh/admin/health",
    "API_EDH_ADMIN_SETTINGS": "/addon/edh/admin/settings",
    "API_EDH_CONTAINERS_CATALOG": "/addon/edh/containers/catalog",
    "API_EDH_CONTAINERS_JOBS": "/addon/edh/containers/jobs",
    "API_EDH_CONTAINERS_JOB": "/addon/edh/containers/jobs/{jobId}",
}

EDH_AMI_RECIPE_SERVICE = (Path(__file__).resolve().parents[4]
                          / "backend/backend/handlers/addon/edh/edhAmiRecipeService.py")
EDH_MODELS = Path(__file__).resolve().parents[4] / "backend/backend/models/addon/edh.py"
EDH_HANDLERS = Path(__file__).resolve().parents[4] / "backend/backend/handlers/addon/edh"

# The AMI recipe service's 404 bodies: the path names no recipe, or no build of the recipe.
RECIPE_NOT_FOUND = {"message": "Recipe not found"}
BUILD_NOT_FOUND = {"message": "Build not found"}
# The AMI recipe service's 409 text for a delete while a build's AMI removal is pending.
AMI_REMOVAL_PENDING = "a build's AMI removal is pending"

# Each method addressing one AMI recipe: its arguments and the verb it sends.
RECIPE_METHODS = [
    ("get_edh_ami_recipe", ("team-desktop",), "get"),
    ("update_edh_ami_recipe", ("team-desktop", {"displayName": "Team desktop"}), "put"),
    ("delete_edh_ami_recipe", ("team-desktop",), "delete"),
    ("list_edh_ami_recipe_builds", ("team-desktop",), "get"),
    ("start_edh_ami_recipe_build", ("team-desktop",), "post"),
]

# Each method addressing one build of an AMI recipe, and the verb it sends.
BUILD_METHODS = [
    ("register_edh_ami_recipe_build", "post"),
    ("retire_edh_ami_recipe_build", "delete"),
]

RECORD = {"bootstrapId": "b-1", "sessionId": "s-1", "state": "ACTIVE", "name": "Design desk"}

WORKSTATION_ID_METHODS = (
    "get_edh_workstation",
    "start_edh_workstation",
    "stop_edh_workstation",
    "delete_edh_workstation",
    "get_edh_workstation_connection",
    "renew_edh_workstation",
    "get_edh_workstation_windows_sign_in",
)

ADMIN_INSTANCE_ID_METHODS = ("stop_edh_admin_instance", "deactivate_edh_admin_instance")

# The jobs list and kill routes read `backend`, `cluster` and `namespace` from the query string: an
# absent backend means batch, and eks addresses a job by its name inside one cluster and namespace.
EKS_SCOPE = {"backend": "eks", "cluster": "sim-cluster", "namespace": "render"}

# The add-on's answer when EDH did not confirm a mutating request that may already have reached it.
UNCONFIRMED = ("EDH did not confirm the request in time; it may still be in progress. "
               "Refresh before trying again.")
UNAVAILABLE = "EDH is unavailable. Try again later."
# The add-on's answer to a job submit EDH accepted without a readable reply.
JOB_SUBMITTED = ("EDH accepted the request but returned an unreadable answer. "
                 "Check the job list before retrying.")
# The add-on's answer to a start sent while an earlier start of the workstation may be under way.
START_IN_PROGRESS = "A start of this workstation is already in progress"

# Each mutating route: the method, its arguments, the verb it sends, the operation its messages name,
# and the listing that shows the resource it changes.
MUTATIONS = [
    ("start_edh_workstation", ("s-1",), "post", "Workstation start", "vamscli edh workstation list"),
    ("stop_edh_workstation", ("s-1",), "post", "Workstation stop", "vamscli edh workstation list"),
    ("delete_edh_workstation", ("s-1",), "delete", "Workstation deletion",
     "vamscli edh workstation list"),
    ("stop_edh_admin_instance", ("s-1",), "post", "Instance stop",
     "vamscli edh admin instances list"),
    ("deactivate_edh_admin_instance", ("s-1",), "post", "Instance deactivation",
     "vamscli edh admin instances list"),
    ("register_edh_vdi_image", ({"amiId": "ami-1"},), "post", "VDI image registration",
     "vamscli edh admin images list"),
    ("register_edh_simulation_image", ({"amiId": "ami-1"},), "post",
     "Simulation image registration", "vamscli edh admin images list"),
    ("delete_edh_vdi_image", ("7",), "delete", "VDI image deletion", "vamscli edh admin images list"),
    ("delete_edh_simulation_image", ("3",), "delete", "Simulation image deletion",
     "vamscli edh admin images list"),
    ("submit_edh_container_job", ({"name": "render-1"},), "post", "Container job submission",
     "vamscli edh container job list"),
    ("kill_edh_container_job", ("j-1",), "delete", "Container job kill",
     "vamscli edh container job list"),
]

# A shipped recipe as GET returns it: its request fields, then what the service stores and adds.
SOURCE_RECIPE = {
    "recipeId": "al2023-desktop",
    "displayName": "Amazon Linux 2023 Desktop",
    "kind": "vdi",
    "osFamily": "linux",
    "baseOs": "amazonlinux2023",
    "architecture": "x86_64",
    "parentImage": "ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
    "buildInstanceType": ["m7i.large", "m6i.large"],
    "rootSizeGb": 100,
    "stackName": "Amazon Linux 2023 Desktop",
    "profileId": 0,
    "components": [{"name": "010-baseline", "data": "schemaVersion: 1.0"}, {"arn": "arn:aws:imagebuilder:x"}],
    "parameters": {"VAMSCLI_VERSION": "2.6.2"},
    "keepVersions": 3,
    "recipeRevision": 2,
    "description": "Amazon DCV desktop",
    "enabled": False,
    "assets": [{"id": "vamscli", "repoPath": "tools/VamsCLI"}],
    "assetPrefix": "addon/edh/amiRecipes/abc123/al2023-desktop/assets",
    "source": "config",
    "contentHash": "f00d",
    "latestVersion": 5,
    "imageBuilder": {"pipelineArn": "arn:aws:imagebuilder:pipeline"},
    "createdBy": "SYSTEM_USER",
    "updatedBy": "SYSTEM_USER",
    "buildOnDeploy": True,
    "latestBuild": {"version": 5, "status": "AVAILABLE"},
}

# The create body for a copy named by default: the source's definition under the new id.
DUPLICATE_BODY = {
    "recipeId": "team-desktop",
    "displayName": "Amazon Linux 2023 Desktop (copy)",
    "kind": "vdi",
    "osFamily": "linux",
    "baseOs": "amazonlinux2023",
    "architecture": "x86_64",
    "parentImage": "ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
    "buildInstanceType": ["m7i.large", "m6i.large"],
    "rootSizeGb": 100,
    "stackName": "Amazon Linux 2023 Desktop Copy",
    "profileId": 0,
    "components": [{"name": "010-baseline", "data": "schemaVersion: 1.0"}, {"arn": "arn:aws:imagebuilder:x"}],
    "parameters": {"VAMSCLI_VERSION": "2.6.2"},
    "keepVersions": 3,
    "recipeRevision": 2,
    "description": "Amazon DCV desktop",
    "duplicateOf": "al2023-desktop",
}

CREATED_COPY = {"recipeId": "team-desktop", "source": "admin", "duplicatedFrom": "al2023-desktop"}


@pytest.mark.parametrize("name,path", sorted(EXPECTED_ROUTES.items()))
def test_edh_constants_name_the_addon_routes(name, path):
    """The CLI's route strings are the master plan's; the contract test then proves the backend
    registers each one."""
    assert getattr(addon_constants, name) == path
    # constants.py re-exports it, which is how tests/test_constants_contract.py reaches it.
    assert getattr(constants, name) == path


# --- Shared helpers for the method tests --------------------------------------


def _client() -> APIClient:
    # A real ProfileManager reads the developer's config directory; a stub keeps every assertion on
    # the request that was built and the exception that was raised.
    return APIClient("https://api.example.com", profile_manager=Mock())


class _Response:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _http_error(status: int, body: dict) -> requests.exceptions.HTTPError:
    response = Mock()
    response.status_code = status
    encoded = json.dumps(body)
    response.content = encoded.encode("utf-8")
    response.text = encoded
    response.json.return_value = body
    return requests.exceptions.HTTPError(f"{status} Client Error", response=response)


def _capture(monkeypatch, client: APIClient, verb: str, body=None, error: Exception = None) -> dict:
    """Replace one HTTP verb on the client, recording the call; return the recorder dict."""
    seen = {}

    def _fake(endpoint, *args, **kwargs):
        seen["endpoint"] = endpoint
        seen["args"] = args
        seen["kwargs"] = kwargs
        if error is not None:
            raise error
        return _Response(body)

    monkeypatch.setattr(client, verb, _fake)
    return seen


# --- Exception family -----------------------------------------------------------


def test_the_edh_exceptions_are_business_logic_errors_under_one_base():
    """Every EDH exception is a BusinessLogicError that commands catch; none is a
    GlobalInfrastructureError, so main()'s global handler never sees one."""

    for name in (
        "EdhWorkstationNotFoundError",
        "EdhImageNotFoundError",
        "InvalidEdhRequestError",
        "EdhOperationError",
        "EdhUnavailableError",
        "EdhOutcomeUnknownError",
        "EdhStartInProgressError",
        "EdhContainerJobNotFoundError",
        "EdhAmiRecipeNotFoundError",
        "EdhAmiRecipeBuildNotFoundError",
    ):
        klass = getattr(edh_exceptions, name)
        assert issubclass(klass, edh_exceptions.EdhError), name
        assert issubclass(klass, BusinessLogicError), name
        assert not issubclass(klass, GlobalInfrastructureError), name


class TestJsonFields:
    """`output_error` merges the flag an EDH exception class carries beside the error, as the API's own
    error body carries it."""

    @pytest.mark.parametrize(
        "error_class,flag",
        [(EdhOutcomeUnknownError, "outcomeUnknown"), (EdhStartInProgressError, "startInProgress")],
    )
    def test_the_class_flag_is_merged_beside_the_error(self, capsys, error_class, flag):
        with pytest.raises(SystemExit):
            output_error(error_class("Workstation start already in progress (409)."), True)
        assert json.loads(capsys.readouterr().out) == {
            "error": "Workstation start already in progress (409).",
            "error_type": error_class.__name__,
            flag: True,
        }


# --- Workstation and catalog methods ----------------------------------------------

class TestWorkstationMethods:
    def test_list_gets_the_collection_route_with_typed_errors_enabled(self, monkeypatch):
        client = _client()
        seen = _capture(monkeypatch, client, "get", body={"Items": [RECORD]})
        assert client.list_edh_workstations() == {"Items": [RECORD]}
        assert seen["endpoint"] == addon_constants.API_EDH_WORKSTATIONS
        assert seen["kwargs"]["raise_http_errors"] is True
        assert seen["kwargs"]["include_auth"] is True

    def test_create_posts_the_body_unchanged_and_returns_the_accepted_record(self, monkeypatch):
        client = _client()
        body = {"bootstrapId": "b-1", "imageId": "12", "instanceType": "g5.2xlarge",
                "name": "Design desk", "rootSizeGb": 200, "osFamily": "linux"}
        seen = _capture(monkeypatch, client, "post", body={**body, "state": "CREATING"})
        result = client.create_edh_workstation(body)
        assert result["state"] == "CREATING"
        assert seen["endpoint"] == addon_constants.API_EDH_WORKSTATIONS
        assert seen["kwargs"]["data"] == body
        assert seen["kwargs"]["raise_http_errors"] is True

    def test_create_docstring_states_the_family_rule_of_the_architecture_refusal(self):
        # The backend refuses only a type listed solely under another architecture family, so an
        # i386-grouped type for an x86_64 image is accepted.
        doc = " ".join(APIClient.create_edh_workstation.__doc__.split())
        assert "instanceType does not match the image's architecture" in doc
        assert ("only under architectures of another family than the image's "
                "(x86_64 and i386 are one family)") in doc

    def test_get_formats_the_session_id_into_the_route(self, monkeypatch):
        client = _client()
        seen = _capture(monkeypatch, client, "get", body=RECORD)
        assert client.get_edh_workstation("s-1") == RECORD
        assert seen["endpoint"] == "/addon/edh/workstations/s-1"
        assert seen["kwargs"]["raise_http_errors"] is True

    @pytest.mark.parametrize(
        "method,constant",
        [("start_edh_workstation", "API_EDH_WORKSTATION_START"),
         ("stop_edh_workstation", "API_EDH_WORKSTATION_STOP"),
         ("renew_edh_workstation", "API_EDH_WORKSTATION_RENEW")],
    )
    def test_workstation_actions_post_to_their_action_routes_without_a_body(self, monkeypatch, method, constant):
        client = _client()
        seen = _capture(monkeypatch, client, "post", body=RECORD)
        assert getattr(client, method)("s-1") == RECORD
        assert seen["endpoint"] == getattr(addon_constants, constant).format(sessionId="s-1")
        assert "data" not in seen["kwargs"]
        assert seen["kwargs"]["raise_http_errors"] is True

    def test_delete_uses_the_delete_verb_on_the_session_route(self, monkeypatch):
        client = _client()
        seen = _capture(monkeypatch, client, "delete", body={**RECORD, "state": "DELETED"})
        assert client.delete_edh_workstation("s-1")["state"] == "DELETED"
        assert seen["endpoint"] == "/addon/edh/workstations/s-1"
        assert seen["kwargs"]["raise_http_errors"] is True

    def test_connection_returns_the_url_untouched(self, monkeypatch):
        client = _client()
        body = {"sessionId": "s-1", "connectionUrl": "https://dcv.example.internal/?authToken=abc",
                "osFamily": "linux"}
        seen = _capture(monkeypatch, client, "get", body=body)
        assert client.get_edh_workstation_connection("s-1") == body
        assert seen["endpoint"] == "/addon/edh/workstations/s-1/connect"

    def test_windows_sign_in_returns_the_body_untouched(self, monkeypatch):
        client = _client()
        body = {"sessionId": "s-1", "username": "vabcdefghij23456", "password": "winpw-" + "x" * 12,
                "directoryAccount": False}
        seen = _capture(monkeypatch, client, "get", body=body)
        assert client.get_edh_workstation_windows_sign_in("s-1") == body
        assert seen["endpoint"] == "/addon/edh/workstations/s-1/windows-sign-in"
        assert seen["kwargs"]["raise_http_errors"] is True
        assert seen["kwargs"]["include_auth"] is True

    @pytest.mark.parametrize(
        "method,constant",
        [("list_edh_catalog_images", "API_EDH_CATALOG_IMAGES"),
         ("list_edh_catalog_instance_types", "API_EDH_CATALOG_INSTANCE_TYPES"),
         ("get_edh_catalog_lease", "API_EDH_CATALOG_LEASE")],
    )
    def test_catalog_reads_get_their_routes(self, monkeypatch, method, constant):
        client = _client()
        seen = _capture(monkeypatch, client, "get", body={"Items": []})
        assert getattr(client, method)() == {"Items": []}
        assert seen["endpoint"] == getattr(addon_constants, constant)
        assert seen["kwargs"]["raise_http_errors"] is True


class TestEdhErrorMapping:
    def test_a_404_on_a_session_route_is_the_workstation_not_found_error(self, monkeypatch):
        client = _client()
        _capture(monkeypatch, client, "get", error=_http_error(404, {"message": "Not found"}))
        with pytest.raises(EdhWorkstationNotFoundError) as excinfo:
            client.get_edh_workstation("s-404")
        assert "s-404" in str(excinfo.value)

    def test_a_404_on_renew_is_the_workstation_not_found_error(self, monkeypatch):
        client = _client()
        _capture(monkeypatch, client, "post", error=_http_error(404, {"message": "Not found"}))
        with pytest.raises(EdhWorkstationNotFoundError) as excinfo:
            client.renew_edh_workstation("s-404")
        assert "s-404" in str(excinfo.value)

    @pytest.mark.parametrize(
        "status,expected",
        [(404, EdhWorkstationNotFoundError), (400, InvalidEdhRequestError), (409, EdhOperationError),
         (502, EdhUnavailableError)],
    )
    def test_windows_sign_in_errors_map_onto_the_edh_family(self, monkeypatch, status, expected):
        client = _client()
        _capture(monkeypatch, client, "get", error=_http_error(status, {"message": "server text"}))
        with pytest.raises(expected):
            client.get_edh_workstation_windows_sign_in("s-1")

    def test_a_404_on_a_collection_route_is_a_plain_api_error(self, monkeypatch):
        # No single resource is addressed, so "not found" means the route itself is absent.
        client = _client()
        _capture(monkeypatch, client, "get", error=_http_error(404, {"message": "Not Found"}))
        with pytest.raises(APIError):
            client.list_edh_workstations()

    @pytest.mark.parametrize(
        "status,expected",
        [(400, InvalidEdhRequestError), (409, EdhOperationError), (502, EdhUnavailableError),
         (503, EdhUnavailableError), (504, EdhUnavailableError), (500, APIError)],
    )
    def test_statuses_map_onto_the_edh_family_and_keep_the_server_text(self, monkeypatch, status, expected):
        client = _client()
        _capture(monkeypatch, client, "get", error=_http_error(status, {"message": "server text"}))
        with pytest.raises(expected) as excinfo:
            client.list_edh_workstations()
        assert "server text" in str(excinfo.value)

    def test_auth_statuses_become_authentication_errors(self, monkeypatch):
        client = _client()
        _capture(monkeypatch, client, "get", error=_http_error(401, {"Message": "denied"}))
        with pytest.raises(AuthenticationError):
            client.get_edh_workstation("s-1")

    def test_a_403_is_a_permission_denial_with_the_handler_text(self, monkeypatch):
        """A refusal is not a sign-in problem; the handler's own words follow `Access forbidden.`."""
        client = _client()
        _capture(monkeypatch, client, "get", error=_http_error(403, {"message": "denied"}))
        with pytest.raises(PermissionDeniedError) as excinfo:
            client.get_edh_workstation("s-1")
        assert str(excinfo.value) == "Access forbidden. denied"

    def test_global_infrastructure_errors_propagate_unwrapped(self, monkeypatch):
        """`_make_request` raises the global tier itself (403 permission or web ACL block, 429
        exhaustion); wrapping one in APIError would hide it from the global handler."""
        from vamscli.utils.exceptions import RequestBlockedError

        for error in (PermissionDeniedError("Access forbidden"), RequestBlockedError("Request blocked")):
            client = _client()
            _capture(monkeypatch, client, "get", error=error)
            with pytest.raises(type(error)):
                client.get_edh_workstation("s-1")

    def test_unexpected_failures_become_api_errors(self, monkeypatch):
        client = _client()
        _capture(monkeypatch, client, "get", error=ValueError("no json"))
        with pytest.raises(APIError):
            client.list_edh_workstations()


class TestRequestPathHasNoDotOrEmptySegment:
    """`_make_request` refuses a path with an empty, '.' or '..' segment before any request
    (`tests/test_api_request_path.py`), so no path value of an EDH method, or of a core method, reaches
    another EDH route (the Windows sign-in among them)."""

    @pytest.mark.parametrize(
        "method,args,path",
        [
            ("get_edh_ami_recipe", ("windows2022-desktop",), "/addon/edh/admin/ami-recipes/windows2022-desktop"),
            ("list_edh_ami_recipe_builds", ("windows2022-desktop",),
             "/addon/edh/admin/ami-recipes/windows2022-desktop/builds"),
        ],
    )
    def test_a_well_formed_value_is_sent_to_its_own_route(self, monkeypatch, method, args, path):
        """Control: the same recorder sees the method's request for a well-formed value."""
        client = _client()
        sent = _record_requests(monkeypatch, client)
        getattr(client, method)(*args)
        assert sent == [("GET", "https://api.example.com" + path)]

    @pytest.mark.parametrize(
        "method,args",
        [
            ("get_edh_ami_recipe", ("../../workstations/s-1/windows-sign-in",)),
            ("list_edh_ami_recipe_builds", ("../../workstations/s-1/windows-sign-in?x=",)),
            ("get_database", ("../addon/edh/workstations/s-1/windows-sign-in",)),
            ("get_asset", ("../addon/edh/workstations/s-1/windows-sign-in?x=", "a1")),
            ("get_edh_ami_recipe", ("%2e%2e/%2e%2e/workstations/s-1/windows-sign-in",)),
            ("get_database", ("%2E%2E/addon/edh/workstations/s-1/windows-sign-in",)),
        ],
    )
    def test_a_value_carrying_a_dot_segment_is_refused_unsent(self, monkeypatch, method, args):
        client = _client()
        sent = _record_requests(monkeypatch, client)
        with pytest.raises(APIError) as excinfo:
            getattr(client, method)(*args)
        assert sent == []
        # A method may wrap the refusal in its own APIError; the refusal stays the cause.
        error = excinfo.value
        while not isinstance(error, InvalidRequestPathError) and error.__context__ is not None:
            error = error.__context__
        assert isinstance(error, InvalidRequestPathError)


# --- Admin methods ---------------------------------------------------------------

def _record_verbs(monkeypatch, client):
    """Replace every HTTP verb on the client with a recorder that answers an empty body."""
    sent = []
    for verb in ("get", "post", "put", "delete"):
        monkeypatch.setattr(
            client, verb,
            lambda endpoint, *args, _verb=verb, **kwargs: sent.append((_verb, endpoint)) or _Response({}))
    return sent


class TestWorkstationIdIsOnePathSegment:
    """A workstation id is formatted into the request path, so an id that is not one segment the
    service accepts would address another route (the Windows sign-in among them). It is refused
    before any request."""

    @pytest.mark.parametrize("method", WORKSTATION_ID_METHODS)
    def test_a_well_formed_id_is_sent_to_its_own_route(self, monkeypatch, method):
        """Control: the same recorder sees a request for a session id the service would accept."""
        client = _client()
        sent = _record_verbs(monkeypatch, client)
        getattr(client, method)("3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30")
        ((_verb, endpoint),) = sent
        assert endpoint.startswith("/addon/edh/workstations/3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30")

    @pytest.mark.parametrize("method", WORKSTATION_ID_METHODS)
    @pytest.mark.parametrize(
        "session_id",
        ["s-1/windows-sign-in", "s-1?x=", "s-1#x", "../s-1", "s-1%2Fx", "", "s1", "s" * 64, None],
    )
    def test_an_id_that_is_not_one_segment_is_refused_unsent(self, monkeypatch, method, session_id):
        client = _client()
        sent = _record_verbs(monkeypatch, client)
        with pytest.raises(InvalidEdhRequestError):
            getattr(client, method)(session_id)
        assert sent == []


class TestAdminInstanceIdIsOnePathSegment:
    """An administrator's instance action formats the session id into its path the same way, so an id
    that is not one segment of the workstation id rule (`_edh_session_id`; EDH session ids and bootstrap
    ids are UUIDs) -- one reaching back to a workstation route, the Windows sign-in among them -- is
    refused before any request."""

    @pytest.mark.parametrize("method", ADMIN_INSTANCE_ID_METHODS)
    def test_a_well_formed_id_is_sent_to_its_own_route(self, monkeypatch, method):
        """Control: the same recorder sees a request for a session id the service would accept."""
        client = _client()
        sent = _record_verbs(monkeypatch, client)
        getattr(client, method)("3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30")
        ((_verb, endpoint),) = sent
        assert endpoint.startswith("/addon/edh/admin/instances/3f9a1c2e-0b7d-4e6f-9a21-5c8d7e6f4b30/")

    @pytest.mark.parametrize("method", ADMIN_INSTANCE_ID_METHODS)
    @pytest.mark.parametrize(
        "session_id",
        ["../../workstations/s-1/windows-sign-in", "s-1/x", "s-1?x=", "s-1#x", "s-1%2Fx", "", "s1",
         "s" * 64, None],
    )
    def test_an_id_that_is_not_one_segment_is_refused_unsent(self, monkeypatch, method, session_id):
        client = _client()
        sent = _record_verbs(monkeypatch, client)
        with pytest.raises(InvalidEdhRequestError):
            getattr(client, method)(session_id)
        assert sent == []


class _SessionAnswer:
    """An empty 200 from the session."""

    status_code = 200
    content = b"{}"
    text = "{}"
    headers = {}

    def json(self):
        return {}

    def raise_for_status(self):
        return None


def _record_requests(monkeypatch, client):
    """Record every request the client's session is asked to send, answering an empty 200. The path
    guard sits in `_make_request`, so this recorder replaces the session below it, not the verbs."""
    sent = []
    monkeypatch.setattr(client, "_validate_token_before_request", lambda include_auth: None)
    monkeypatch.setattr(client, "_get_headers", lambda include_auth: {})
    monkeypatch.setattr(
        client.session, "request", lambda method, url, **kwargs: sent.append((method, url)) or _SessionAnswer())
    return sent


class TestAdminMethods:
    @pytest.mark.parametrize(
        "method,constant",
        [("list_edh_admin_images", "API_EDH_ADMIN_IMAGES"),
         ("list_edh_admin_instances", "API_EDH_ADMIN_INSTANCES"),
         ("list_edh_stale_instances", "API_EDH_ADMIN_INSTANCES_STALE"),
         ("get_edh_capacity", "API_EDH_ADMIN_CAPACITY"),
         ("get_edh_health", "API_EDH_ADMIN_HEALTH"),
         ("get_edh_admin_settings", "API_EDH_ADMIN_SETTINGS")],
    )
    def test_admin_reads_get_their_routes_and_return_the_body(self, monkeypatch, method, constant):
        client = _client()
        seen = _capture(monkeypatch, client, "get", body={"any": "shape"})
        assert getattr(client, method)() == {"any": "shape"}
        assert seen["endpoint"] == getattr(addon_constants, constant)
        assert seen["kwargs"]["raise_http_errors"] is True

    def test_update_admin_settings_puts_the_body_unchanged(self, monkeypatch):
        client = _client()
        patch = {"enabled": True, "leaseHours": 12, "forced": False, "graceHours": 6}
        seen = _capture(monkeypatch, client, "put", body={"lease": {"enabled": True}})
        assert client.update_edh_admin_settings(patch) == {"lease": {"enabled": True}}
        assert seen["endpoint"] == addon_constants.API_EDH_ADMIN_SETTINGS
        assert seen["kwargs"]["data"] == patch
        assert seen["kwargs"]["raise_http_errors"] is True

    def test_stale_forwards_idle_minutes_as_a_query_parameter_only_when_given(self, monkeypatch):
        """The stale route reads `idleMinutes` (server default 120); a dropped optional would pin
        every caller to that default with nothing failing."""
        client = _client()
        seen = _capture(monkeypatch, client, "get", body={"Items": []})
        client.list_edh_stale_instances()
        assert seen["kwargs"]["params"] == {}
        client.list_edh_stale_instances(idle_minutes=45)
        assert seen["endpoint"] == addon_constants.API_EDH_ADMIN_INSTANCES_STALE
        assert seen["kwargs"]["params"] == {"idleMinutes": 45}

    @pytest.mark.parametrize(
        "method,constant",
        [("register_edh_vdi_image", "API_EDH_ADMIN_IMAGES_VDI"),
         ("register_edh_simulation_image", "API_EDH_ADMIN_IMAGES_SIMULATION")],
    )
    def test_registrations_post_the_body_unchanged(self, monkeypatch, method, constant):
        client = _client()
        body = {"amiId": "ami-0123", "stackName": "ubuntu-dcv", "rootSize": 100}
        seen = _capture(monkeypatch, client, "post", body={"registered": True})
        assert getattr(client, method)(body) == {"registered": True}
        assert seen["endpoint"] == getattr(addon_constants, constant)
        assert seen["kwargs"]["data"] == body
        assert seen["kwargs"]["raise_http_errors"] is True

    @pytest.mark.parametrize(
        "method,expected_endpoint",
        [("delete_edh_vdi_image", "/addon/edh/admin/images/vdi/img-7"),
         ("delete_edh_simulation_image", "/addon/edh/admin/images/simulation/img-7")],
    )
    def test_image_deletes_format_the_image_id_into_their_kind_route(self, monkeypatch, method, expected_endpoint):
        client = _client()
        seen = _capture(monkeypatch, client, "delete", body={"deleted": "img-7"})
        assert getattr(client, method)("img-7") == {"deleted": "img-7"}
        assert seen["endpoint"] == expected_endpoint
        assert seen["kwargs"]["raise_http_errors"] is True

    @pytest.mark.parametrize("method", ["delete_edh_vdi_image", "delete_edh_simulation_image"])
    def test_image_deletes_map_404_to_image_not_found(self, monkeypatch, method):
        client = _client()
        _capture(monkeypatch, client, "delete", error=_http_error(404, {"message": "Not found"}))
        with pytest.raises(EdhImageNotFoundError) as excinfo:
            getattr(client, method)("img-404")
        assert "img-404" in str(excinfo.value)

    @pytest.mark.parametrize(
        "method,expected_endpoint",
        [("stop_edh_admin_instance", "/addon/edh/admin/instances/s-9/stop"),
         ("deactivate_edh_admin_instance", "/addon/edh/admin/instances/s-9/deactivate")],
    )
    def test_instance_actions_post_without_a_body(self, monkeypatch, method, expected_endpoint):
        client = _client()
        seen = _capture(monkeypatch, client, "post", body={"sessionId": "s-9"})
        assert getattr(client, method)("s-9") == {"sessionId": "s-9"}
        assert seen["endpoint"] == expected_endpoint
        assert "data" not in seen["kwargs"]
        assert seen["kwargs"]["raise_http_errors"] is True

    @pytest.mark.parametrize("method", ["stop_edh_admin_instance", "deactivate_edh_admin_instance"])
    def test_instance_actions_map_404_to_workstation_not_found(self, monkeypatch, method):
        client = _client()
        _capture(monkeypatch, client, "post", error=_http_error(404, {"message": "Not found"}))
        with pytest.raises(EdhWorkstationNotFoundError):
            getattr(client, method)("s-404")

    def test_a_failed_health_probe_names_the_reason_the_handler_reports(self, monkeypatch):
        """The health route's 502 body carries a `reason` (credentialRejected, unreachable, refused)
        beside its text; a message that only said the cluster did not answer would hide a rotated
        deployment credential behind a connectivity problem."""
        client = _client()
        body = {"status": "error", "reason": "credentialRejected", "edhClusterId": "edh-1",
                "message": "EDH rejected the deployment credential."}
        _capture(monkeypatch, client, "get", error=_http_error(502, body))
        with pytest.raises(EdhUnavailableError) as excinfo:
            client.get_edh_health()
        assert "credentialRejected" in str(excinfo.value)
        assert "EDH rejected the deployment credential." in str(excinfo.value)

    def test_a_502_without_a_reason_still_maps_to_the_unavailable_error(self, monkeypatch):
        # Amazon API Gateway's own 502 (the Lambda did not answer) carries no `reason`.
        client = _client()
        _capture(monkeypatch, client, "get",
                 error=_http_error(502, {"message": "Internal server error"}))
        with pytest.raises(EdhUnavailableError) as excinfo:
            client.get_edh_health()
        assert "Internal server error" in str(excinfo.value)


# --- Container methods -----------------------------------------------------------

class TestContainerMethods:
    @pytest.mark.parametrize(
        "method,constant",
        [("list_edh_container_catalog", "API_EDH_CONTAINERS_CATALOG"),
         ("list_edh_container_jobs", "API_EDH_CONTAINERS_JOBS")],
    )
    def test_container_reads_get_their_routes(self, monkeypatch, method, constant):
        client = _client()
        seen = _capture(monkeypatch, client, "get", body={"Items": []})
        assert getattr(client, method)() == {"Items": []}
        assert seen["endpoint"] == getattr(addon_constants, constant)
        assert seen["kwargs"]["raise_http_errors"] is True

    def test_submit_posts_the_body_unchanged_and_returns_the_accepted_job(self, monkeypatch):
        client = _client()
        body = {"jobDefinition": "render-def", "jobQueue": "render-queue", "name": "render-1",
                "backend": "batch", "command": ["python", "main.py"], "environment": {"A": "1"},
                "vcpus": 4, "memory": 2048}
        accepted = {"jobId": "j-1", "jobHandle": "batch:j-1", "backend": "batch",
                    "jobDefinition": "render-def", "jobQueue": "render-queue", "name": "render-1",
                    "message": "Job submitted"}
        seen = _capture(monkeypatch, client, "post", body=accepted)
        assert client.submit_edh_container_job(body)["jobHandle"] == "batch:j-1"
        assert seen["endpoint"] == addon_constants.API_EDH_CONTAINERS_JOBS
        assert seen["kwargs"]["data"] == body
        assert seen["kwargs"]["raise_http_errors"] is True

    def test_jobs_list_forwards_the_scope_as_query_parameters_only_when_given(self, monkeypatch):
        """The handler defaults an absent `backend` to batch and rejects an unknown one with 400,
        so the client sends each scope parameter only when the caller chose it."""
        client = _client()
        seen = _capture(monkeypatch, client, "get", body={"Items": []})
        client.list_edh_container_jobs()
        assert seen["kwargs"]["params"] == {}
        client.list_edh_container_jobs(backend="batch")
        assert seen["kwargs"]["params"] == {"backend": "batch"}
        client.list_edh_container_jobs(**EKS_SCOPE)
        assert seen["endpoint"] == addon_constants.API_EDH_CONTAINERS_JOBS
        assert seen["kwargs"]["params"] == EKS_SCOPE

    def test_kill_deletes_the_job_route_and_forwards_the_scope_only_when_given(self, monkeypatch):
        # The path carries the listing's jobId as it is -- a Batch job id or an EKS job name.
        client = _client()
        seen = _capture(monkeypatch, client, "delete",
                        body={"jobId": "j-1", "backend": "batch", "message": "Job cancelled"})
        assert client.kill_edh_container_job("j-1")["backend"] == "batch"
        assert seen["endpoint"] == "/addon/edh/containers/jobs/j-1"
        assert seen["kwargs"]["raise_http_errors"] is True
        assert seen["kwargs"]["params"] == {}
        client.kill_edh_container_job("render-1", **EKS_SCOPE)
        assert seen["endpoint"] == "/addon/edh/containers/jobs/render-1"
        assert seen["kwargs"]["params"] == EKS_SCOPE

    def test_kill_maps_404_to_job_not_found(self, monkeypatch):
        client = _client()
        _capture(monkeypatch, client, "delete", error=_http_error(404, {"message": "Job not found"}))
        with pytest.raises(EdhContainerJobNotFoundError) as excinfo:
            client.kill_edh_container_job("j-404")
        assert "j-404" in str(excinfo.value)


# --- Mutations EDH did not confirm ------------------------------------------------------------------

class TestUnconfirmedMutations:
    @pytest.mark.parametrize("method,args,verb,operation,listing", MUTATIONS)
    def test_a_flagged_502_is_not_confirmed_and_points_at_the_listing(
            self, monkeypatch, method, args, verb, operation, listing):
        client = _client()
        _capture(monkeypatch, client, verb,
                 error=_http_error(502, {"message": UNCONFIRMED, "outcomeUnknown": True}))
        with pytest.raises(EdhOutcomeUnknownError) as excinfo:
            getattr(client, method)(*args)
        assert str(excinfo.value) == (
            f"{operation} not confirmed (502): {UNCONFIRMED} Run '{listing}' to see its state.")
        # A subclass, so every handler of an unanswered EDH request still catches it.
        assert isinstance(excinfo.value, EdhUnavailableError)

    @pytest.mark.parametrize("method,args,verb,operation,listing", MUTATIONS)
    def test_the_gateways_504_is_not_confirmed_in_the_handlers_words(
            self, monkeypatch, method, args, verb, operation, listing):
        # API Gateway's own 504 body names only its timeout.
        client = _client()
        _capture(monkeypatch, client, verb,
                 error=_http_error(504, {"message": "Endpoint request timed out"}))
        with pytest.raises(EdhOutcomeUnknownError) as excinfo:
            getattr(client, method)(*args)
        assert str(excinfo.value) == (
            f"{operation} not confirmed (504): {UNCONFIRMED} Run '{listing}' to see its state.")

    def test_a_plain_502_on_a_mutation_is_still_an_unanswered_request(self, monkeypatch):
        client = _client()
        _capture(monkeypatch, client, "post", error=_http_error(502, {"message": UNAVAILABLE}))
        with pytest.raises(EdhUnavailableError) as excinfo:
            client.start_edh_workstation("s-1")
        assert not isinstance(excinfo.value, EdhOutcomeUnknownError)
        assert str(excinfo.value) == (
            f"Workstation start failed (502): the EDH cluster did not answer. {UNAVAILABLE}")

    def test_the_flag_counts_only_on_a_mutation(self, monkeypatch):
        # A read changes nothing, so an unanswered one is an unanswered request.
        client = _client()
        _capture(monkeypatch, client, "get",
                 error=_http_error(502, {"message": UNCONFIRMED, "outcomeUnknown": True}))
        with pytest.raises(EdhUnavailableError) as excinfo:
            client.list_edh_workstations()
        assert not isinstance(excinfo.value, EdhOutcomeUnknownError)

    def test_a_502_body_that_is_not_json_is_an_unanswered_request(self, monkeypatch):
        client = _client()
        error = _http_error(502, {})
        error.response.json.side_effect = ValueError("not json")
        error.response.content = b"Bad Gateway"
        _capture(monkeypatch, client, "post", error=error)
        with pytest.raises(EdhUnavailableError) as excinfo:
            client.stop_edh_workstation("s-1")
        assert not isinstance(excinfo.value, EdhOutcomeUnknownError)

    def test_a_submit_edh_accepted_without_a_readable_answer_is_not_confirmed(self, monkeypatch):
        client = _client()
        _capture(monkeypatch, client, "post",
                 error=_http_error(502, {"message": JOB_SUBMITTED, "outcomeUnknown": True}))
        with pytest.raises(EdhOutcomeUnknownError) as excinfo:
            client.submit_edh_container_job({"name": "render-1"})
        assert str(excinfo.value) == (
            f"Container job submission not confirmed (502): {JOB_SUBMITTED} "
            "Run 'vamscli edh container job list' to see its state.")


class TestStartInProgress:
    def test_a_flagged_409_is_already_in_progress_and_points_at_the_listing(self, monkeypatch):
        client = _client()
        _capture(monkeypatch, client, "post",
                 error=_http_error(409, {"message": START_IN_PROGRESS, "startInProgress": True}))
        with pytest.raises(EdhStartInProgressError) as excinfo:
            client.start_edh_workstation("s-1")
        assert str(excinfo.value) == (
            f"Workstation start already in progress (409): {START_IN_PROGRESS}. "
            "Run 'vamscli edh workstation list' to see its state.")
        # A subclass, so every handler of a refused request still catches it.
        assert isinstance(excinfo.value, EdhOperationError)

    def test_a_409_without_the_flag_is_refused_whatever_its_words(self, monkeypatch):
        client = _client()
        _capture(monkeypatch, client, "post", error=_http_error(409, {"message": START_IN_PROGRESS}))
        with pytest.raises(EdhOperationError) as excinfo:
            client.start_edh_workstation("s-1")
        assert not isinstance(excinfo.value, EdhStartInProgressError)
        assert str(excinfo.value) == f"Workstation start refused (409): {START_IN_PROGRESS}"


# --- AMI recipe and build lookups ---------------------------------------------------

def _service_404_messages():
    """The `message` of every `general_error(status_code=404, body={...})` the recipe service writes."""
    messages = set()
    for node in ast.walk(ast.parse(EDH_AMI_RECIPE_SERVICE.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "general_error"):
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        status, body = keywords.get("status_code"), keywords.get("body")
        if isinstance(status, ast.Constant) and status.value == 404 and isinstance(body, ast.Dict):
            for key, value in zip(body.keys, body.values):
                if isinstance(key, ast.Constant) and key.value == "message" and isinstance(value, ast.Constant):
                    messages.add(value.value)
    return messages


class TestAmiRecipeNotFound:
    @pytest.mark.parametrize("method,args,verb", RECIPE_METHODS)
    def test_a_404_on_a_recipe_route_is_the_recipe_not_found_error(self, monkeypatch, method, args, verb):
        client = _client()
        _capture(monkeypatch, client, verb, error=_http_error(404, RECIPE_NOT_FOUND))
        with pytest.raises(EdhAmiRecipeNotFoundError) as excinfo:
            getattr(client, method)(*args)
        assert str(excinfo.value) == "AMI recipe 'team-desktop' not found"

    @pytest.mark.parametrize("method,verb", BUILD_METHODS)
    def test_a_build_the_recipe_does_not_have_is_the_build_not_found_error(self, monkeypatch, method, verb):
        client = _client()
        _capture(monkeypatch, client, verb, error=_http_error(404, BUILD_NOT_FOUND))
        with pytest.raises(EdhAmiRecipeBuildNotFoundError) as excinfo:
            getattr(client, method)("team-desktop", 7)
        assert str(excinfo.value) == "AMI recipe 'team-desktop' build v7 not found"

    @pytest.mark.parametrize("method,verb", BUILD_METHODS)
    def test_a_missing_recipe_on_a_build_route_is_the_recipe_not_found_error(self, monkeypatch, method, verb):
        client = _client()
        _capture(monkeypatch, client, verb, error=_http_error(404, RECIPE_NOT_FOUND))
        with pytest.raises(EdhAmiRecipeNotFoundError) as excinfo:
            getattr(client, method)("team-desktop", 7)
        assert str(excinfo.value) == "AMI recipe 'team-desktop' not found"

    @pytest.mark.parametrize(
        "body",
        [
            {"message": "Not Found"},
            {"message": "recipe not found"},
            {"message": "Build not found for this recipe"},
            {"message": "Recipe not found: team-desktop"},
            {},
        ],
    )
    @pytest.mark.parametrize("method,verb", BUILD_METHODS)
    def test_another_404_on_a_build_route_stays_an_api_error(self, monkeypatch, method, verb, body):
        # A 404 body the service does not write, such as API Gateway's own, names neither one.
        client = _client()
        _capture(monkeypatch, client, verb, error=_http_error(404, body))
        with pytest.raises(APIError) as excinfo:
            getattr(client, method)("team-desktop", 7)
        assert "failed (404)" in str(excinfo.value)

    @pytest.mark.parametrize(
        "method,args,verb",
        [("list_edh_ami_recipes", (), "get"), ("create_edh_ami_recipe", ({"recipeId": "team-desktop"},), "post")],
    )
    def test_a_404_on_the_collection_route_stays_an_api_error(self, monkeypatch, method, args, verb):
        # No recipe is addressed, so "not found" means the route itself is absent.
        client = _client()
        _capture(monkeypatch, client, verb, error=_http_error(404, {"message": "Not Found"}))
        with pytest.raises(APIError) as excinfo:
            getattr(client, method)(*args)
        assert "failed (404): Not Found" in str(excinfo.value)

    def test_the_bodies_the_client_reads_are_the_ones_the_recipe_service_answers_404_with(self):
        """Register and retire tell the two errors apart by the 404 body's exact text, so a reworded
        service message would turn both back into an APIError without any other test failing."""
        messages = _service_404_messages()
        for name in ("_EDH_AMI_RECIPE_NOT_FOUND_MESSAGE", "_EDH_AMI_RECIPE_BUILD_NOT_FOUND_MESSAGE"):
            assert getattr(edh_api_client, name) in messages, (name, messages)


# --- AMI recipe duplication -----------------------------------------------------------

def _duplicating_client(monkeypatch, source=None, get_error=None, post_error=None):
    """A client whose GET answers the source recipe and whose POST answers the created copy; returns
    the client and the recorders of both requests."""
    client = _client()
    read = _capture(monkeypatch, client, "get", body=dict(SOURCE_RECIPE if source is None else source),
                    error=get_error)
    created = _capture(monkeypatch, client, "post", body=dict(CREATED_COPY), error=post_error)
    return client, read, created


def _request_model_fields(class_name):
    """The annotated fields of a backend Pydantic model, read from its source."""
    tree = ast.parse(EDH_MODELS.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {statement.target.id for statement in node.body
                    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)}
    raise AssertionError(f"no class {class_name} in {EDH_MODELS}")


class TestAmiRecipeDuplicate:
    def test_a_duplicate_reads_the_source_and_posts_its_definition_with_duplicate_of(self, monkeypatch):
        client, read, created = _duplicating_client(monkeypatch)
        assert client.duplicate_edh_ami_recipe("al2023-desktop", "team-desktop") == CREATED_COPY
        assert read["endpoint"] == "/addon/edh/admin/ami-recipes/al2023-desktop"
        assert read["kwargs"]["raise_http_errors"] is True
        assert created["endpoint"] == addon_constants.API_EDH_ADMIN_AMI_RECIPES
        assert created["kwargs"]["raise_http_errors"] is True
        assert created["kwargs"]["data"] == DUPLICATE_BODY

    def test_the_bundled_files_and_the_stored_fields_are_not_sent(self, monkeypatch):
        """The service copies the source's assets and prefix itself, and the copy starts enabled with
        no builds; a body carrying them would be refused or misread."""
        client, _, created = _duplicating_client(monkeypatch)
        client.duplicate_edh_ami_recipe("al2023-desktop", "team-desktop")
        body = created["kwargs"]["data"]
        for key in ("assets", "assetPrefix", "source", "contentHash", "latestVersion", "imageBuilder",
                    "createdBy", "updatedBy", "buildOnDeploy", "latestBuild", "enabled", "duplicatedFrom"):
            assert key not in body, key

    def test_the_names_given_replace_the_defaults(self, monkeypatch):
        client, _, created = _duplicating_client(monkeypatch)
        client.duplicate_edh_ami_recipe("al2023-desktop", "team-desktop", display_name="Team desktop",
                                        stack_name="Team Desktop")
        body = created["kwargs"]["data"]
        assert (body["recipeId"], body["displayName"], body["stackName"], body["duplicateOf"]) == (
            "team-desktop", "Team desktop", "Team Desktop", "al2023-desktop")

    def test_a_keep_versions_given_replaces_the_source_s(self, monkeypatch):
        client, _, created = _duplicating_client(monkeypatch)
        client.duplicate_edh_ami_recipe("al2023-desktop", "team-desktop", keep_versions=7)
        assert created["kwargs"]["data"] == {**DUPLICATE_BODY, "keepVersions": 7}

    def test_a_keep_versions_given_is_sent_for_a_source_without_one(self, monkeypatch):
        source = {key: value for key, value in SOURCE_RECIPE.items() if key != "keepVersions"}
        client, _, created = _duplicating_client(monkeypatch, source=source)
        client.duplicate_edh_ami_recipe("al2023-desktop", "team-desktop", keep_versions=1)
        assert created["kwargs"]["data"]["keepVersions"] == 1

    def test_an_omitted_keep_versions_is_the_source_s(self, monkeypatch):
        client, _, created = _duplicating_client(monkeypatch, source={**SOURCE_RECIPE, "keepVersions": 9})
        client.duplicate_edh_ami_recipe("al2023-desktop", "team-desktop", keep_versions=None)
        assert created["kwargs"]["data"]["keepVersions"] == 9

    def test_a_field_the_source_does_not_carry_is_not_sent(self, monkeypatch):
        source = {key: value for key, value in SOURCE_RECIPE.items()
                  if key not in ("baseOs", "parameters", "description")}
        source.update(kind="simulation", buildInstanceType="g5.xlarge")
        client, _, created = _duplicating_client(monkeypatch, source=source)
        client.duplicate_edh_ami_recipe("al2023-desktop", "sim-node")
        body = created["kwargs"]["data"]
        assert "baseOs" not in body and "parameters" not in body and "description" not in body
        assert body["kind"] == "simulation" and body["buildInstanceType"] == "g5.xlarge"

    def test_a_missing_source_creates_nothing(self, monkeypatch):
        client, _, created = _duplicating_client(
            monkeypatch, get_error=_http_error(404, {"message": "Recipe not found"}))
        with pytest.raises(EdhAmiRecipeNotFoundError) as excinfo:
            client.duplicate_edh_ami_recipe("gone", "team-desktop")
        assert str(excinfo.value) == "AMI recipe 'gone' not found"
        assert created == {}

    def test_a_refused_create_keeps_the_service_text(self, monkeypatch):
        refusal = "stackName is already used by recipe 'al2023-desktop'"
        client, _, _ = _duplicating_client(monkeypatch, post_error=_http_error(409, {"message": refusal}))
        with pytest.raises(EdhOperationError) as excinfo:
            client.duplicate_edh_ami_recipe("al2023-desktop", "team-desktop", stack_name="Amazon Linux 2023 Desktop")
        assert str(excinfo.value) == f"AMI recipe creation refused (409): {refusal}"

    def test_a_duplicate_carries_every_request_field_it_does_not_set_or_leave_to_the_service(self):
        """A field added to the request model and not to the copied list would be dropped from every
        duplicate without an error; the copy's own fields, the bundled files the service copies and
        `enabled` (a copy starts enabled) are the only ones not copied."""
        fields = _request_model_fields("AmiRecipeRequestModel")
        # Control: the model read is the one that takes duplicateOf.
        assert "duplicateOf" in fields
        own = {"recipeId", "displayName", "stackName", "duplicateOf"}
        not_copied = {"assets", "enabled"}
        copied = set(edh_api_client._EDH_AMI_RECIPE_COPIED_FIELDS)
        assert copied | own | not_copied == fields
        assert not copied & (own | not_copied)


# --- AMI recipe deletion ------------------------------------------------------------------


def _handler_string_constants():
    """Every string literal in the EDH handler sources."""
    strings = set()
    for source in EDH_HANDLERS.rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.add(node.value)
    return strings


class TestAmiRecipeDelete:
    def test_a_delete_refused_while_an_ami_removal_is_pending_keeps_the_service_text(self, monkeypatch):
        client = _client()
        seen = _capture(monkeypatch, client, "delete", error=_http_error(409, {"message": AMI_REMOVAL_PENDING}))
        with pytest.raises(EdhOperationError) as excinfo:
            client.delete_edh_ami_recipe("team-desktop")
        assert str(excinfo.value) == f"AMI recipe deletion refused (409): {AMI_REMOVAL_PENDING}"
        assert seen["endpoint"] == "/addon/edh/admin/ami-recipes/team-desktop"
        assert seen["kwargs"]["raise_http_errors"] is True

    def test_the_refusal_quoted_is_one_the_recipe_handlers_write(self):
        """The CLI help, the troubleshooting page and these tests quote the delete refusal's text, so a
        reworded handler message would leave them describing an error no request returns."""
        strings = _handler_string_constants()
        # Control: the reader finds a message the recipe service is known to write.
        assert RECIPE_NOT_FOUND["message"] in strings
        assert AMI_REMOVAL_PENDING in strings
