"""``APIClient`` methods for the EDH Workstations add-on routes (``/addon/edh/*``).

``EdhAPIClientMixin`` is one of the mixins ``utils/addon_api_client.AddonAPIClientMixin`` aggregates,
so each method here is an ordinary ``APIClient`` method that the ``edh`` commands
(``commands/addon/edh.py``) and the MCP server's EDH tools call. Endpoint paths come from
``vamscli.addon_constants`` and the exceptions from ``utils/addon/edh_exceptions.py``. The core
``_api_error_message`` is imported where it is called: ``utils/api_client.py`` imports this module
before it has finished loading.
"""

import re
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

from ...addon_constants import (
    API_EDH_WORKSTATIONS, API_EDH_WORKSTATION, API_EDH_WORKSTATION_STOP, API_EDH_WORKSTATION_START,
    API_EDH_WORKSTATION_CONNECT, API_EDH_WORKSTATION_RENEW, API_EDH_CATALOG_IMAGES, API_EDH_CATALOG_INSTANCE_TYPES,
    API_EDH_WORKSTATION_WINDOWS_SIGN_IN,
    API_EDH_CATALOG_LEASE, API_EDH_ADMIN_IMAGES, API_EDH_ADMIN_IMAGES_VDI, API_EDH_ADMIN_IMAGES_SIMULATION,
    API_EDH_ADMIN_IMAGE_VDI, API_EDH_ADMIN_IMAGE_SIMULATION, API_EDH_ADMIN_INSTANCES,
    API_EDH_ADMIN_INSTANCES_STALE, API_EDH_ADMIN_INSTANCE_STOP, API_EDH_ADMIN_INSTANCE_DEACTIVATE,
    API_EDH_ADMIN_CAPACITY, API_EDH_ADMIN_HEALTH, API_EDH_ADMIN_SETTINGS,
    API_EDH_CATALOG_CAPACITY_RESERVATIONS, API_EDH_ADMIN_CAPACITY_RESERVATIONS, API_EDH_ADMIN_CAPACITY_RESERVATION,
    API_EDH_ADMIN_AMI_RECIPES, API_EDH_ADMIN_AMI_RECIPE, API_EDH_ADMIN_AMI_RECIPE_BUILDS,
    API_EDH_ADMIN_AMI_RECIPE_BUILD, API_EDH_ADMIN_AMI_RECIPE_BUILD_REGISTER,
    API_EDH_CONTAINERS_CATALOG, API_EDH_CONTAINERS_JOBS, API_EDH_CONTAINERS_JOB,
)
from ..exceptions import APIError, AuthenticationError, GlobalInfrastructureError, PermissionDeniedError
from .edh_exceptions import (
    EdhWorkstationNotFoundError, EdhImageNotFoundError, InvalidEdhRequestError, EdhOperationError,
    EdhUnavailableError, EdhOutcomeUnknownError, EdhStartInProgressError,
    EdhContainerJobNotFoundError, EdhAmiRecipeNotFoundError, EdhAmiRecipeBuildNotFoundError,
    EdhReservationFullError, EdhCapacityReservationNotFoundError,
)


# A gateway 504 carries only the gateway's own timeout text, so an EDH mutation it cut off is described
# in the words the add-on's handlers answer a flagged 502 with.
_EDH_OUTCOME_UNKNOWN_MESSAGE = (
    "EDH did not confirm the request in time; it may still be in progress. Refresh before trying again.")

# The listings that show what the EDH mutations change, named when EDH did not confirm one or a start
# is already under way.
_EDH_WORKSTATION_LISTING = "vamscli edh workstation list"
_EDH_ADMIN_INSTANCE_LISTING = "vamscli edh admin instances list"
_EDH_ADMIN_IMAGE_LISTING = "vamscli edh admin images list"
_EDH_CONTAINER_JOB_LISTING = "vamscli edh container job list"

# The AMI recipe service's 404 texts: the path names no recipe, or no build of the recipe.
_EDH_AMI_RECIPE_NOT_FOUND_MESSAGE = "Recipe not found"
_EDH_AMI_RECIPE_BUILD_NOT_FOUND_MESSAGE = "Build not found"

# The service's own id rule for a workstation path value (backend/backend/common/validators.py id_pattern).
_EDH_SESSION_ID = re.compile(r"[-_a-zA-Z0-9]{3,63}")

# An EC2 capacity reservation id, as the request models take it.
EDH_CAPACITY_RESERVATION_ID = re.compile(r"cr-[0-9a-f]{17}")
EDH_CAPACITY_RESERVATION_ID_MESSAGE = "Capacity reservation id must be cr- and 17 hex digits"

# The code a launch or start is refused with when its capacity reservation has no free slot.
EDH_RESERVATION_FULL_CODE = "RESERVATION_FULL"

# The 503 a launch answers when VAMS could not read the capacity reservation from EC2: EDH was not
# called, so the generic "the EDH cluster did not answer" would misname the cause.
_EDH_RESERVATION_READ_FAILED_MESSAGE = "The capacity reservation could not be read; try again"

# The request fields a duplicated AMI recipe copies from its source. The copy has its own recipeId,
# displayName and stackName, the service copies the source's bundled files (assets), and a copy starts
# enabled.
_EDH_AMI_RECIPE_COPIED_FIELDS = (
    "kind", "osFamily", "baseOs", "architecture", "parentImage", "buildInstanceType", "launchInstanceTypes",
    "rootSizeGb", "profileId", "components", "parameters", "keepVersions", "recipeRevision", "description",
)


def _edh_body_flag(response, field: str) -> bool:
    """True when an /addon/edh error body sets `field` to true: `outcomeUnknown` on a mutation EDH may
    have carried out without confirming it, `startInProgress` on a start refused while an earlier one
    may be under way. Never raises, for the reason `_api_error_message` gives."""
    try:
        data = response.json() if getattr(response, "content", None) else None
    except Exception:
        return False
    return isinstance(data, dict) and data.get(field) is True


def _edh_body_code(response) -> Optional[str]:
    """The `code` an /addon/edh error body carries (`RESERVATION_FULL` on a launch or start refused
    for a full capacity reservation), or None. Never raises, for the reason `_api_error_message`
    gives."""
    try:
        data = response.json() if getattr(response, "content", None) else None
    except Exception:
        return None
    code = data.get("code") if isinstance(data, dict) else None
    return code if isinstance(code, str) else None


def _edh_capacity_reservation_id(capacity_reservation_id: str) -> str:
    """The reservation id a path is formatted with, refused before any request unless it is an EC2
    capacity reservation id: anything else would address another route."""
    if (not isinstance(capacity_reservation_id, str)
            or not EDH_CAPACITY_RESERVATION_ID.fullmatch(capacity_reservation_id)):
        raise InvalidEdhRequestError(EDH_CAPACITY_RESERVATION_ID_MESSAGE)
    return capacity_reservation_id


def _edh_ami_build_not_found(response, recipe_id: str, version: int) -> Optional[Exception]:
    """The not-found error a 404 from an AMI recipe build route stands for, read from its body's
    message: the recipe's when the service found no recipe, the build's when the recipe has no build
    of that version, and None for any other body. Never raises: `_api_error_message` reads the
    body."""
    from ..api_client import _api_error_message

    message = _api_error_message(response, "")
    if message == _EDH_AMI_RECIPE_NOT_FOUND_MESSAGE:
        return EdhAmiRecipeNotFoundError(f"AMI recipe '{recipe_id}' not found")
    if message == _EDH_AMI_RECIPE_BUILD_NOT_FOUND_MESSAGE:
        return EdhAmiRecipeBuildNotFoundError(f"AMI recipe '{recipe_id}' build v{version} not found")
    return None


def _as_sentence(text: str) -> str:
    """`text` ending in a full stop, so the sentence naming a listing can follow it."""
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _edh_session_id(session_id: str) -> str:
    """The workstation id a path is formatted with, refused before any request unless it is one path
    segment the service accepts: anything else would address another route."""
    if not isinstance(session_id, str) or not _EDH_SESSION_ID.fullmatch(session_id):
        raise InvalidEdhRequestError("Workstation id must be 3-63 letters, digits, hyphens or underscores")
    return session_id


def _edh_path_value(value) -> str:
    """An image, recipe, build or job id as one encoded path segment: a `/`, `?` or `#` in it is sent
    as part of the id, so the request addresses the resource the id names and no other."""
    return quote(str(value), safe="")


class EdhAPIClientMixin:
    """API client methods for the EDH Workstations add-on routes.

    Every call passes raise_http_errors=True so _edh_error sees the original HTTPError; with the
    default, _make_request converts a 404 into a generic APIError before the method's own handler
    runs. The add-on's handlers return no `message` envelope, and each list route returns every row
    in one {"Items": [...]} page.
    """

    def _edh_error(self, e: requests.exceptions.HTTPError, operation: str,
                   not_found: Optional[Exception] = None,
                   list_command: Optional[str] = None) -> Exception:
        """
        The typed exception for an HTTP error from an /addon/edh route.

        Args:
            e: The HTTPError raised for the response
            operation: Operation name used in the messages (e.g. "Workstation lookup")
            not_found: Exception returned for a 404 when the route addresses one resource
            list_command: For a mutating route, the command that lists what it changes. A 502 the
                handler flagged outcomeUnknown, or a 504, then raises EdhOutcomeUnknownError naming
                it: EDH may have carried the mutation out without confirming it. A 409 flagged
                startInProgress raises EdhStartInProgressError naming it.

        Returns:
            The exception the calling method raises
        """
        from ..api_client import _api_error_message

        status_code = e.response.status_code
        error_message = _api_error_message(e.response, str(e))
        if status_code == 404 and not_found is not None:
            return not_found
        if status_code == 400:
            return InvalidEdhRequestError(f"{operation} rejected (400): {error_message}")
        if status_code == 401:
            return AuthenticationError(f"Authentication failed: {e}")
        if status_code == 403:
            return PermissionDeniedError(f"Access forbidden. {error_message}")
        if status_code == 409:
            if list_command is not None and _edh_body_flag(e.response, "startInProgress"):
                return EdhStartInProgressError(
                    f"{operation} already in progress (409): {_as_sentence(error_message)} "
                    f"Run '{list_command}' to see its state.")
            if _edh_body_code(e.response) == EDH_RESERVATION_FULL_CODE:
                return EdhReservationFullError(f"{operation} refused (409): {error_message}")
            return EdhOperationError(f"{operation} refused (409): {error_message}")
        if list_command is not None and (
                status_code == 504
                or (status_code == 502 and _edh_body_flag(e.response, "outcomeUnknown"))):
            message = _EDH_OUTCOME_UNKNOWN_MESSAGE if status_code == 504 else error_message
            return EdhOutcomeUnknownError(
                f"{operation} not confirmed ({status_code}): {_as_sentence(message)} "
                f"Run '{list_command}' to see its state.")
        if status_code == 503 and error_message == _EDH_RESERVATION_READ_FAILED_MESSAGE:
            return EdhUnavailableError(f"{operation} failed (503): {error_message}")
        if status_code in [502, 503, 504]:
            return EdhUnavailableError(
                f"{operation} failed ({status_code}): the EDH cluster did not answer. {error_message}")
        return APIError(f"{operation} failed ({status_code}): {error_message}")

    def list_edh_workstations(self) -> Dict[str, Any]:
        """
        List the caller's EDH workstations using the /addon/edh/workstations GET endpoint.

        Returns:
            API response data: {"Items": [<workstation record>, ...]}

        Raises:
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.get(API_EDH_WORKSTATIONS, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Workstation listing")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to list workstations: {e}")

    def create_edh_workstation(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Launch an EDH workstation using the /addon/edh/workstations POST endpoint.

        Args:
            data: Launch request: bootstrapId (idempotency key), imageId, instanceType, name,
                rootSizeGb and osFamily (linux or windows) are required; leaseHours and
                capacityReservationId (a registered reservation to launch into) are optional

        Returns:
            API response data (HTTP 202): the workstation record as accepted -- state CREATING,
            sessionId null until the launch worker, or else the reconciler's next sweep, reads the
            desktop back from EDH, lastError null, launchAttempts 0.
            The launch runs asynchronously; poll get_edh_workstation with the bootstrapId.

        Raises:
            InvalidEdhRequestError: When the request is rejected, among others with "instanceType does
                not match the image's architecture" when the image's profile lists the type only under
                architectures of another family than the image's (x86_64 and i386 are one family), and
                for a capacity reservation that is not registered, not active, or of another
                instance type or platform
            EdhReservationFullError: When the capacity reservation has no free slot and does not
                fall back to on-demand (409 RESERVATION_FULL)
            EdhOperationError: When the launch is refused for the record's current state
            EdhUnavailableError: When the EDH cluster did not answer, or the capacity reservation
                could not be read (503)
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.post(API_EDH_WORKSTATIONS, data=data, include_auth=True,
                                 raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Workstation launch")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to launch workstation: {e}")

    def get_edh_workstation(self, session_id: str) -> Dict[str, Any]:
        """
        Get one of the caller's workstations using the /addon/edh/workstations/{sessionId} GET
        endpoint. A workstation owned by another user is reported as not found.

        Args:
            session_id: EDH session ID of the workstation, or the bootstrapId of a launch that has
                not received one

        Returns:
            API response data: the workstation record

        Raises:
            EdhWorkstationNotFoundError: When the workstation is not found or not the caller's
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_WORKSTATION.format(sessionId=_edh_session_id(session_id))

        try:
            response = self.get(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "Workstation lookup",
                EdhWorkstationNotFoundError(f"Workstation '{session_id}' not found"))

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to get workstation: {e}")

    def start_edh_workstation(self, session_id: str) -> Dict[str, Any]:
        """
        Start a stopped workstation using the /addon/edh/workstations/{sessionId}/start POST endpoint.

        Args:
            session_id: EDH session ID of the workstation, or the bootstrapId of a launch that has
                not received one

        Returns:
            API response data: the updated workstation record plus sessionBootstrap (provisioned,
                skipped, unavailable or disabled), the outcome of placing a fresh session key

        Raises:
            EdhWorkstationNotFoundError: When the workstation is not found or not the caller's
            EdhOperationError: When the workstation cannot be started from its current state
            EdhStartInProgressError: When an earlier start of the workstation may still be under way
            EdhOutcomeUnknownError: When EDH did not confirm the start; it may be in progress
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_WORKSTATION_START.format(sessionId=_edh_session_id(session_id))

        try:
            response = self.post(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "Workstation start",
                EdhWorkstationNotFoundError(f"Workstation '{session_id}' not found"),
                list_command=_EDH_WORKSTATION_LISTING)

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to start workstation: {e}")

    def stop_edh_workstation(self, session_id: str) -> Dict[str, Any]:
        """
        Stop a running workstation using the /addon/edh/workstations/{sessionId}/stop POST endpoint.

        Args:
            session_id: EDH session ID of the workstation, or the bootstrapId of a launch that has
                not received one

        Returns:
            API response data: the updated workstation record

        Raises:
            EdhWorkstationNotFoundError: When the workstation is not found or not the caller's
            EdhOperationError: When the workstation cannot be stopped from its current state
            EdhOutcomeUnknownError: When EDH did not confirm the stop; it may be in progress
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_WORKSTATION_STOP.format(sessionId=_edh_session_id(session_id))

        try:
            response = self.post(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "Workstation stop",
                EdhWorkstationNotFoundError(f"Workstation '{session_id}' not found"),
                list_command=_EDH_WORKSTATION_LISTING)

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to stop workstation: {e}")

    def delete_edh_workstation(self, session_id: str) -> Dict[str, Any]:
        """
        Delete a workstation using the /addon/edh/workstations/{sessionId} DELETE endpoint.

        Args:
            session_id: EDH session ID of the workstation, or the bootstrapId of a launch that has
                not received one

        Returns:
            API response data: the updated workstation record

        Raises:
            EdhWorkstationNotFoundError: When the workstation is not found or not the caller's
            EdhOperationError: When the workstation cannot be deleted from its current state
            EdhOperationError: When the launch is still being created (a session-less row younger
                than the service's grace window) -- HTTP 409
            EdhOutcomeUnknownError: When EDH did not confirm the deletion; it may be in progress
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_WORKSTATION.format(sessionId=_edh_session_id(session_id))

        try:
            response = self.delete(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "Workstation deletion",
                EdhWorkstationNotFoundError(f"Workstation '{session_id}' not found"),
                list_command=_EDH_WORKSTATION_LISTING)

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to delete workstation: {e}")

    def get_edh_workstation_connection(self, session_id: str) -> Dict[str, Any]:
        """
        Get the DCV connection details using the /addon/edh/workstations/{sessionId}/connect GET
        endpoint. The connectionUrl carries a session credential.

        Args:
            session_id: EDH session ID of the workstation, or the bootstrapId of a launch that has
                not received one

        Returns:
            API response data: {"sessionId": ..., "connectionUrl": ..., "osFamily": ...}

        Raises:
            EdhWorkstationNotFoundError: When the workstation is not found or not the caller's
            EdhOperationError: When the workstation is not ACTIVE with a connection available
                (HTTP 409, "Workstation is not ready for connection")
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_WORKSTATION_CONNECT.format(sessionId=_edh_session_id(session_id))

        try:
            response = self.get(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "Workstation connection",
                EdhWorkstationNotFoundError(f"Workstation '{session_id}' not found"))

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to get workstation connection: {e}")

    def get_edh_workstation_windows_sign_in(self, session_id: str) -> Dict[str, Any]:
        """
        Get the Windows sign-in of a running Windows workstation using the
        /addon/edh/workstations/{sessionId}/windows-sign-in GET endpoint. The password is a credential
        for the desktop.

        Args:
            session_id: EDH session ID of the workstation

        Returns:
            API response data: {"sessionId": ..., "username": ..., "password": ..., "directoryAccount": ...};
            password is None for a directory account

        Raises:
            EdhWorkstationNotFoundError: When the workstation is not found or not the caller's
            InvalidEdhRequestError: When session_id is not one path segment the service accepts (no
                request is sent), or the workstation is not a Windows workstation (HTTP 400)
            EdhOperationError: When the workstation is not ready for connection, or EDH holds no
                password for it yet (HTTP 409, "Windows password is not available yet")
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_WORKSTATION_WINDOWS_SIGN_IN.format(sessionId=_edh_session_id(session_id))

        try:
            response = self.get(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "Workstation Windows sign-in",
                EdhWorkstationNotFoundError(f"Workstation '{session_id}' not found"))

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to get workstation Windows sign-in: {e}")

    def renew_edh_workstation(self, session_id: str) -> Dict[str, Any]:
        """
        Renew a workstation's lease via POST /addon/edh/workstations/{sessionId}/renew, resetting it to
        the administrator's full lease window.

        Args:
            session_id: The workstation session ID or bootstrap ID

        Returns:
            The updated workstation record, plus sessionBootstrap (provisioned, skipped, unavailable or
                disabled) when the renew re-issued, or tried to re-issue, the desktop's session key

        Raises:
            EdhOperationError: When leases are disabled, when the workstation has no lease, or when it is deleted (HTTP 409)
            EdhWorkstationNotFoundError: When the workstation is not found (HTTP 404)
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_WORKSTATION_RENEW.format(sessionId=_edh_session_id(session_id))
        try:
            response = self.post(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "Workstation renew",
                EdhWorkstationNotFoundError(f"Workstation '{session_id}' not found"))

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to renew workstation: {e}")

    def list_edh_catalog_images(self) -> Dict[str, Any]:
        """
        List the desktop images the caller may launch using the /addon/edh/catalog/images GET
        endpoint.

        Returns:
            API response data: {"Items": [<image>, ...]}; each image carries architecture, its CPU
            architecture as EDH reports it (x86_64, arm64, ...), or None when EDH reports none

        Raises:
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.get(API_EDH_CATALOG_IMAGES, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Image catalog listing")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to list catalog images: {e}")

    def list_edh_catalog_instance_types(self) -> Dict[str, Any]:
        """
        List the allowed workstation instance types using the /addon/edh/catalog/instance-types GET
        endpoint.

        Returns:
            API response data: {"Items": [{"instanceType": ..., "architectures": [...]}, ...]};
            architectures names the architecture groups of the deployment's desktop profile that list
            the type, in EDH's order, and is empty when the profile does not group its types

        Raises:
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.get(API_EDH_CATALOG_INSTANCE_TYPES, include_auth=True,
                                raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Instance type listing")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to list instance types: {e}")

    def get_edh_catalog_lease(self) -> Dict[str, Any]:
        """
        Get the launch-time lease policy using the /addon/edh/catalog/lease GET endpoint. Returns the
        lease parameters (enabled, leaseHours, forced, graceHours) any workstation user may read before
        choosing --lease-hours.

        Returns:
            API response data: {"lease": {enabled, leaseHours, forced, graceHours}}

        Raises:
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.get(API_EDH_CATALOG_LEASE, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Lease policy read")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to get lease policy: {e}")

    def list_edh_catalog_capacity_reservations(self, instance_type: Optional[str] = None,
                                               os_family: Optional[str] = None) -> Dict[str, Any]:
        """
        List the capacity reservations the caller may launch into using the
        /addon/edh/catalog/capacity-reservations GET endpoint: registered, enabled, permitted to the
        caller, and active in EC2.

        Args:
            instance_type: Only the reservations of this instance type; sent as instanceType
            os_family: Only the reservations whose platform suits this image family (linux:
                Linux/UNIX, windows: Windows); sent as osFamily

        Returns:
            API response data: {"Items": [<capacity reservation view>, ...]}; a failed EC2 read
            answers an empty list, so a launch stays on-demand

        Raises:
            InvalidEdhRequestError: When a filter is rejected
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        params: Dict[str, Any] = {}
        if instance_type is not None:
            params['instanceType'] = instance_type
        if os_family is not None:
            params['osFamily'] = os_family

        try:
            response = self.get(API_EDH_CATALOG_CAPACITY_RESERVATIONS, include_auth=True,
                                raise_http_errors=True, params=params)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Capacity reservation listing")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to list capacity reservations: {e}")

    def list_edh_admin_images(self) -> Dict[str, Any]:
        """
        List every registered EDH software-stack image using the /addon/edh/admin/images GET endpoint.

        Returns:
            API response data: {"Items": [<image>, ...]}

        Raises:
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.get(API_EDH_ADMIN_IMAGES, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Image listing")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to list images: {e}")

    def register_edh_vdi_image(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Register a desktop (VDI) image using the /addon/edh/admin/images/vdi POST endpoint.

        Args:
            data: Registration request: amiId, stackName, baseOs, rootSize, virtualDesktopProfileId,
                and optionally launchTenancy, description

        Returns:
            API response data (HTTP 201): {"kind": "vdi", "imageId": str, "stackName": str,
                "amiId": str, "osFamily": str}; imageId is the new stack's id, read back from the
                catalog by stack name. A read-back that does not find the stack answers 502, raised as
                EdhUnavailableError, with the stack registered outside the project

        Raises:
            InvalidEdhRequestError: When the request is rejected
            EdhOperationError: When the registration is refused
            EdhOutcomeUnknownError: When EDH did not confirm the registration; it may be in progress
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.post(API_EDH_ADMIN_IMAGES_VDI, data=data, include_auth=True,
                                 raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "VDI image registration",
                                  list_command=_EDH_ADMIN_IMAGE_LISTING)

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to register VDI image: {e}")

    def register_edh_simulation_image(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Register a simulation (target node) image using the /addon/edh/admin/images/simulation POST
        endpoint.

        Args:
            data: Registration request: amiId, stackName, rootSize, targetNodeProfileId, and
                optionally targetNodeUserDataId (default 1), userDataVariables, description

        Returns:
            API response data (HTTP 201): {"kind": "simulation", "imageId": str,
                "stackName": str, "amiId": str, "osFamily": "linux"}; imageId is the new stack's id,
                read back from the catalog by stack name. A read-back that does not find the stack
                answers 502, raised as EdhUnavailableError, with the stack registered outside the
                project

        Raises:
            InvalidEdhRequestError: When the request is rejected
            EdhOperationError: When the registration is refused
            EdhOutcomeUnknownError: When EDH did not confirm the registration; it may be in progress
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.post(API_EDH_ADMIN_IMAGES_SIMULATION, data=data, include_auth=True,
                                 raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Simulation image registration",
                                  list_command=_EDH_ADMIN_IMAGE_LISTING)

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to register simulation image: {e}")

    def delete_edh_vdi_image(self, image_id: str) -> Dict[str, Any]:
        """
        Unregister a desktop (VDI) image using the /addon/edh/admin/images/vdi/{imageId} DELETE
        endpoint.

        Args:
            image_id: EDH software-stack ID of the image

        Returns:
            API response data: {"kind": "vdi", "imageId": str, "deleted": True}

        Raises:
            EdhImageNotFoundError: When the image is not found
            EdhOutcomeUnknownError: When EDH did not confirm the deletion; it may be in progress
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_ADMIN_IMAGE_VDI.format(imageId=_edh_path_value(image_id))

        try:
            response = self.delete(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "VDI image deletion", EdhImageNotFoundError(f"Image '{image_id}' not found"),
                list_command=_EDH_ADMIN_IMAGE_LISTING)

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to delete VDI image: {e}")

    def delete_edh_simulation_image(self, image_id: str) -> Dict[str, Any]:
        """
        Unregister a simulation image using the /addon/edh/admin/images/simulation/{imageId} DELETE
        endpoint.

        Args:
            image_id: EDH software-stack ID of the image

        Returns:
            API response data: {"kind": "simulation", "imageId": str, "deleted": True}

        Raises:
            EdhImageNotFoundError: When the image is not found
            EdhOutcomeUnknownError: When EDH did not confirm the deletion; it may be in progress
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_ADMIN_IMAGE_SIMULATION.format(imageId=_edh_path_value(image_id))

        try:
            response = self.delete(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "Simulation image deletion",
                EdhImageNotFoundError(f"Image '{image_id}' not found"),
                list_command=_EDH_ADMIN_IMAGE_LISTING)

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to delete simulation image: {e}")

    def list_edh_admin_instances(self) -> Dict[str, Any]:
        """
        List every user's workstation sessions using the /addon/edh/admin/instances GET endpoint.

        Returns:
            API response data: {"Items": [<session>, ...]}

        Raises:
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.get(API_EDH_ADMIN_INSTANCES, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Instance listing")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to list instances: {e}")

    def list_edh_stale_instances(self, idle_minutes: Optional[int] = None) -> Dict[str, Any]:
        """
        List the sessions EDH reports as stale using the /addon/edh/admin/instances/stale GET endpoint.

        Args:
            idle_minutes: Idle threshold in minutes; sent as the idleMinutes query parameter when
                given, otherwise the deployment default (120) applies

        Returns:
            API response data: {"Items": [<session>, ...], "idleMinutes": <threshold applied>}

        Raises:
            InvalidEdhRequestError: When idleMinutes is rejected
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        params: Dict[str, Any] = {}
        if idle_minutes is not None:
            params['idleMinutes'] = idle_minutes

        try:
            response = self.get(API_EDH_ADMIN_INSTANCES_STALE, include_auth=True,
                                raise_http_errors=True, params=params)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Stale instance listing")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to list stale instances: {e}")

    def stop_edh_admin_instance(self, session_id: str) -> Dict[str, Any]:
        """
        Stop any user's workstation session using the /addon/edh/admin/instances/{sessionId}/stop
        POST endpoint.

        Args:
            session_id: EDH session ID of the workstation

        Returns:
            API response data: the updated session as the handler reports it

        Raises:
            EdhWorkstationNotFoundError: When the session is not found
            EdhOperationError: When the session cannot be stopped from its current state
            EdhOperationError: When EDH no longer lists the session (HTTP 409); deactivate reclaims
                the record instead
            EdhOutcomeUnknownError: When EDH did not confirm the stop; it may be in progress
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_ADMIN_INSTANCE_STOP.format(sessionId=_edh_session_id(session_id))

        try:
            response = self.post(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "Instance stop", EdhWorkstationNotFoundError(f"Session '{session_id}' not found"),
                list_command=_EDH_ADMIN_INSTANCE_LISTING)

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to stop instance: {e}")

    def deactivate_edh_admin_instance(self, session_id: str) -> Dict[str, Any]:
        """
        Force-deactivate any user's workstation session using the
        /addon/edh/admin/instances/{sessionId}/deactivate POST endpoint.

        Args:
            session_id: EDH session ID of the workstation, or the bootstrapId of a launch that has
                not received one

        Returns:
            API response data: the updated session as the handler reports it

        Raises:
            EdhWorkstationNotFoundError: When the session is not found
            EdhOperationError: When the session cannot be deactivated from its current state
            EdhOperationError: When a launch without a session is younger than 15 minutes (HTTP 409);
                it may still be on the launch worker
            EdhOutcomeUnknownError: When EDH did not confirm the deactivation; it may be in progress
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_ADMIN_INSTANCE_DEACTIVATE.format(sessionId=_edh_session_id(session_id))

        try:
            response = self.post(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "Instance deactivation",
                EdhWorkstationNotFoundError(f"Session '{session_id}' not found"),
                list_command=_EDH_ADMIN_INSTANCE_LISTING)

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to deactivate instance: {e}")

    def get_edh_capacity(self) -> Dict[str, Any]:
        """
        Get the cluster capacity summary using the /addon/edh/admin/capacity GET endpoint.

        Returns:
            API response data: the capacity summary as EDH reports it

        Raises:
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.get(API_EDH_ADMIN_CAPACITY, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Capacity lookup")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to get capacity: {e}")

    def _edh_health_error(self, e: requests.exceptions.HTTPError) -> Exception:
        """
        The exception for a failed EDH health probe.

        The route's 502 body names a reason (credentialRejected, unreachable, refused) beside its
        text, so the message says what to look at rather than only that the cluster did not answer.

        Args:
            e: The HTTPError raised for the response

        Returns:
            The exception get_edh_health raises
        """
        from ..api_client import _api_error_message

        if e.response.status_code == 502:
            try:
                body = e.response.json()
            except Exception:
                body = None
            reason = body.get("reason") if isinstance(body, dict) else None
            if isinstance(reason, str) and reason.strip():
                return EdhUnavailableError(
                    f"Health check failed (502): {_api_error_message(e.response, str(e))} "
                    f"[reason: {reason.strip()}]")
        return self._edh_error(e, "Health check")

    def get_edh_health(self) -> Dict[str, Any]:
        """
        Get the EDH connectivity health using the /addon/edh/admin/health GET endpoint.

        Returns:
            API response data: {"status": "ok", "edhClusterId": str, "latencyMs": int}

        Raises:
            EdhUnavailableError: When the probe failed (HTTP 502); the message carries the reason the
                handler reports (credentialRejected, unreachable, refused) and its text
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.get(API_EDH_ADMIN_HEALTH, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_health_error(e)

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to get health: {e}")

    def get_edh_admin_settings(self) -> Dict[str, Any]:
        """
        Get the workstation lease policy using the /addon/edh/admin/settings GET endpoint.

        Returns:
            API response data: {"lease": {enabled, leaseHours, forced, graceHours, updatedAt, updatedBy}}

        Raises:
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.get(API_EDH_ADMIN_SETTINGS, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Admin settings read")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to read admin settings: {e}")

    def update_edh_admin_settings(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update the workstation lease policy using the /addon/edh/admin/settings PUT endpoint. Accepts
        enabled, leaseHours, forced, and graceHours; only the fields you pass are changed.

        Args:
            data: Patch dict with any of {enabled, leaseHours, forced, graceHours}

        Returns:
            API response data: {"lease": {enabled, leaseHours, forced, graceHours, updatedAt, updatedBy}}

        Raises:
            EdhError: When the request is refused (validation failure, bounds violation)
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.put(API_EDH_ADMIN_SETTINGS, data=data, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Admin settings update")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to update admin settings: {e}")

    def list_edh_admin_capacity_reservations(self) -> Dict[str, Any]:
        """
        List every registered capacity reservation with EC2's live view of it using the
        /addon/edh/admin/capacity-reservations GET endpoint.

        Returns:
            API response data: {"Items": [<capacity reservation view>, ...]}; state is EC2's, or
            notFound when EC2 no longer knows the id, or unknown when the read failed

        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.get(API_EDH_ADMIN_CAPACITY_RESERVATIONS, include_auth=True,
                                raise_http_errors=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Capacity reservation listing")
        except GlobalInfrastructureError:
            raise
        except Exception as e:
            raise APIError(f"Failed to list capacity reservations: {e}")

    def register_edh_capacity_reservation(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Register an EC2 capacity reservation for launches using the
        /addon/edh/admin/capacity-reservations POST endpoint. Changes VAMS only, never EC2.

        Args:
            data: {capacityReservationId, label, fallbackToOnDemand?, enabled?}

        Returns:
            API response data: the registered reservation's view

        Raises:
            InvalidEdhRequestError: When EC2 does not find the reservation in this account and
                Region, or the registry is full (400)
            EdhOperationError: When the reservation is already registered (409)
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.post(API_EDH_ADMIN_CAPACITY_RESERVATIONS, data=data, include_auth=True,
                                 raise_http_errors=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Capacity reservation registration")
        except GlobalInfrastructureError:
            raise
        except Exception as e:
            raise APIError(f"Failed to register capacity reservation: {e}")

    def update_edh_capacity_reservation(self, capacity_reservation_id: str,
                                        data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Change a registered reservation's label, fallbackToOnDemand or enabled using the
        /addon/edh/admin/capacity-reservations/{capacityReservationId} PUT endpoint.

        Args:
            capacity_reservation_id: The EC2 capacity reservation id (cr- and 17 hex digits)
            data: Patch dict with any of {label, fallbackToOnDemand, enabled}

        Returns:
            API response data: the reservation's view

        Raises:
            InvalidEdhRequestError: When the id is not a reservation id, or the patch is rejected
            EdhCapacityReservationNotFoundError: When the reservation is not registered (404)
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_ADMIN_CAPACITY_RESERVATION.format(
            capacityReservationId=_edh_capacity_reservation_id(capacity_reservation_id))
        try:
            response = self.put(endpoint, data=data, include_auth=True, raise_http_errors=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "Capacity reservation update",
                EdhCapacityReservationNotFoundError(
                    f"Capacity reservation '{capacity_reservation_id}' is not registered"))
        except GlobalInfrastructureError:
            raise
        except Exception as e:
            raise APIError(f"Failed to update capacity reservation: {e}")

    def unregister_edh_capacity_reservation(self, capacity_reservation_id: str) -> Dict[str, Any]:
        """
        Unregister a capacity reservation using the
        /addon/edh/admin/capacity-reservations/{capacityReservationId} DELETE endpoint. VAMS stops
        offering it; the EC2 reservation is not changed, and workstations launched into it keep their
        capacityReservationId.

        Args:
            capacity_reservation_id: The EC2 capacity reservation id (cr- and 17 hex digits)

        Returns:
            API response data: the deletion confirmation

        Raises:
            InvalidEdhRequestError: When the id is not a reservation id
            EdhCapacityReservationNotFoundError: When the reservation is not registered (404)
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_ADMIN_CAPACITY_RESERVATION.format(
            capacityReservationId=_edh_capacity_reservation_id(capacity_reservation_id))
        try:
            response = self.delete(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "Capacity reservation unregistration",
                EdhCapacityReservationNotFoundError(
                    f"Capacity reservation '{capacity_reservation_id}' is not registered"))
        except GlobalInfrastructureError:
            raise
        except Exception as e:
            raise APIError(f"Failed to unregister capacity reservation: {e}")

    def list_edh_ami_recipes(self) -> Dict[str, Any]:
        """
        List all AMI recipes with their latest builds using the /addon/edh/admin/ami-recipes GET
        endpoint.

        Returns:
            API response data: {"Items": [<recipe>, ...]}

        Raises:
            EdhError: When the request is refused
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.get(API_EDH_ADMIN_AMI_RECIPES, include_auth=True, raise_http_errors=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "AMI recipe listing")
        except GlobalInfrastructureError:
            raise
        except Exception as e:
            raise APIError(f"Failed to list AMI recipes: {e}")

    def get_edh_ami_recipe(self, recipe_id: str) -> Dict[str, Any]:
        """
        Get a specific AMI recipe using the /addon/edh/admin/ami-recipes/{recipeId} GET endpoint.

        Args:
            recipe_id: Recipe identifier

        Returns:
            API response data: the recipe definition with inline components, plus latestBuild (the
            newest build row, or None before the first build)

        Raises:
            EdhAmiRecipeNotFoundError: When the recipe is not found (404)
            EdhError: When the request is rejected (400), refused (409), or the EDH cluster did not
                answer (502/503/504)
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_ADMIN_AMI_RECIPE.format(recipeId=_edh_path_value(recipe_id))
        try:
            response = self.get(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "AMI recipe retrieval",
                                  EdhAmiRecipeNotFoundError(f"AMI recipe '{recipe_id}' not found"))
        except GlobalInfrastructureError:
            raise
        except Exception as e:
            raise APIError(f"Failed to get AMI recipe: {e}")

    def create_edh_ami_recipe(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new AMI recipe using the /addon/edh/admin/ami-recipes POST endpoint.

        Args:
            data: Recipe definition with components inlined

        Returns:
            API response data: created recipe object

        Raises:
            EdhError: When the request is refused or recipe is invalid
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.post(API_EDH_ADMIN_AMI_RECIPES, data=data, include_auth=True,
                                raise_http_errors=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "AMI recipe creation")
        except GlobalInfrastructureError:
            raise
        except Exception as e:
            raise APIError(f"Failed to create AMI recipe: {e}")

    def duplicate_edh_ami_recipe(self, source_id: str, new_id: str, display_name: Optional[str] = None,
                                 stack_name: Optional[str] = None,
                                 keep_versions: Optional[int] = None) -> Dict[str, Any]:
        """
        Duplicate an AMI recipe into a new admin recipe: read the source with the
        /addon/edh/admin/ami-recipes/{recipeId} GET endpoint, then create the copy with the
        /addon/edh/admin/ami-recipes POST endpoint, sending the source's definition with
        duplicateOf. The service copies the source's bundled files (assets) and records
        duplicatedFrom, so the body carries neither.

        Args:
            source_id: Recipe identifier to duplicate, a config or an admin recipe
            new_id: Recipe identifier of the copy
            display_name: Display name of the copy; "<source displayName> (copy)" when omitted
            stack_name: EDH stack name of the copy, which no other recipe may use;
                "<source stackName> Copy" when omitted
            keep_versions: Number of AMIs pruning keeps for the copy; the source's keepVersions
                when omitted

        Returns:
            API response data: the created recipe object

        Raises:
            EdhAmiRecipeNotFoundError: When the source recipe is not found (404)
            EdhError: When either request is rejected (400) or refused (409), for example for a
                recipe id or stack name already in use, or a keep_versions out of range (400)
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        source = self.get_edh_ami_recipe(source_id)
        body = {field: source[field] for field in _EDH_AMI_RECIPE_COPIED_FIELDS if source.get(field) is not None}
        body["recipeId"] = new_id
        body["displayName"] = f"{source.get('displayName', '')} (copy)" if display_name is None else display_name
        body["stackName"] = f"{source.get('stackName', '')} Copy" if stack_name is None else stack_name
        if keep_versions is not None:
            body["keepVersions"] = keep_versions
        body["duplicateOf"] = source_id
        return self.create_edh_ami_recipe(body)

    def update_edh_ami_recipe(self, recipe_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing AMI recipe using the /addon/edh/admin/ami-recipes/{recipeId} PUT endpoint.

        Args:
            recipe_id: Recipe identifier
            data: Updated recipe definition

        Returns:
            API response data: updated recipe object

        Raises:
            EdhAmiRecipeNotFoundError: When the recipe is not found (404)
            EdhError: When the request is refused
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_ADMIN_AMI_RECIPE.format(recipeId=_edh_path_value(recipe_id))
        try:
            response = self.put(endpoint, data=data, include_auth=True, raise_http_errors=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "AMI recipe update",
                                  EdhAmiRecipeNotFoundError(f"AMI recipe '{recipe_id}' not found"))
        except GlobalInfrastructureError:
            raise
        except Exception as e:
            raise APIError(f"Failed to update AMI recipe: {e}")

    def delete_edh_ami_recipe(self, recipe_id: str) -> Dict[str, Any]:
        """
        Delete an AMI recipe using the /addon/edh/admin/ami-recipes/{recipeId} DELETE endpoint.

        Args:
            recipe_id: Recipe identifier

        Returns:
            API response data: deletion confirmation

        Raises:
            EdhAmiRecipeNotFoundError: When the recipe is not found (404)
            EdhError: When the request is refused (409), for example while a build's AMI removal is
                pending
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_ADMIN_AMI_RECIPE.format(recipeId=_edh_path_value(recipe_id))
        try:
            response = self.delete(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "AMI recipe deletion",
                                  EdhAmiRecipeNotFoundError(f"AMI recipe '{recipe_id}' not found"))
        except GlobalInfrastructureError:
            raise
        except Exception as e:
            raise APIError(f"Failed to delete AMI recipe: {e}")

    def list_edh_ami_recipe_builds(self, recipe_id: str) -> Dict[str, Any]:
        """
        List all builds for an AMI recipe using the /addon/edh/admin/ami-recipes/{recipeId}/builds
        GET endpoint.

        Args:
            recipe_id: Recipe identifier

        Returns:
            API response data: {"Items": [<build>, ...]}

        Raises:
            EdhAmiRecipeNotFoundError: When the recipe is not found (404)
            EdhError: When the request is refused
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_ADMIN_AMI_RECIPE_BUILDS.format(recipeId=_edh_path_value(recipe_id))
        try:
            response = self.get(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "AMI recipe build listing",
                                  EdhAmiRecipeNotFoundError(f"AMI recipe '{recipe_id}' not found"))
        except GlobalInfrastructureError:
            raise
        except Exception as e:
            raise APIError(f"Failed to list AMI recipe builds: {e}")

    def start_edh_ami_recipe_build(self, recipe_id: str) -> Dict[str, Any]:
        """
        Start a new build for an AMI recipe using the /addon/edh/admin/ami-recipes/{recipeId}/builds
        POST endpoint.

        Args:
            recipe_id: Recipe identifier

        Returns:
            API response data: build record with version, status, outcome

        Raises:
            EdhAmiRecipeNotFoundError: When the recipe is not found (404)
            EdhError: When the build start is refused
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_ADMIN_AMI_RECIPE_BUILDS.format(recipeId=_edh_path_value(recipe_id))
        try:
            response = self.post(endpoint, data={}, include_auth=True, raise_http_errors=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "AMI recipe build start",
                                  EdhAmiRecipeNotFoundError(f"AMI recipe '{recipe_id}' not found"))
        except GlobalInfrastructureError:
            raise
        except Exception as e:
            raise APIError(f"Failed to start AMI recipe build: {e}")

    def register_edh_ami_recipe_build(self, recipe_id: str, version: int) -> Dict[str, Any]:
        """
        Register a completed AMI build in EDH using the
        /addon/edh/admin/ami-recipes/{recipeId}/builds/{version}/register POST endpoint.

        Args:
            recipe_id: Recipe identifier
            version: Build version number

        Returns:
            API response data: registration result

        Raises:
            EdhAmiRecipeNotFoundError: When the recipe is not found (404)
            EdhAmiRecipeBuildNotFoundError: When the recipe has no build of that version (404)
            EdhError: When the registration is refused
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_ADMIN_AMI_RECIPE_BUILD_REGISTER.format(
            recipeId=_edh_path_value(recipe_id), version=_edh_path_value(version))
        try:
            response = self.post(endpoint, data={}, include_auth=True, raise_http_errors=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "AMI recipe build registration",
                                  _edh_ami_build_not_found(e.response, recipe_id, version))
        except GlobalInfrastructureError:
            raise
        except Exception as e:
            raise APIError(f"Failed to register AMI recipe build: {e}")

    def retire_edh_ami_recipe_build(self, recipe_id: str, version: int) -> Dict[str, Any]:
        """
        Retire an AMI build version using the
        /addon/edh/admin/ami-recipes/{recipeId}/builds/{version} DELETE endpoint.

        Args:
            recipe_id: Recipe identifier
            version: Build version number

        Returns:
            API response data: retirement confirmation

        Raises:
            EdhAmiRecipeNotFoundError: When the recipe is not found (404)
            EdhAmiRecipeBuildNotFoundError: When the recipe has no build of that version (404)
            EdhError: When the retirement is refused
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_ADMIN_AMI_RECIPE_BUILD.format(
            recipeId=_edh_path_value(recipe_id), version=_edh_path_value(version))
        try:
            response = self.delete(endpoint, include_auth=True, raise_http_errors=True)
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "AMI recipe build retirement",
                                  _edh_ami_build_not_found(e.response, recipe_id, version))
        except GlobalInfrastructureError:
            raise
        except Exception as e:
            raise APIError(f"Failed to retire AMI recipe build: {e}")

    def _edh_container_scope(self, backend: Optional[str], cluster: Optional[str],
                             namespace: Optional[str]) -> Dict[str, Any]:
        """
        The query parameters that scope a container job listing or kill, each sent only when given:
        the handler defaults an absent backend to batch, and the eks backend addresses jobs inside
        one cluster (required) and namespace (default 'default').
        """
        params: Dict[str, Any] = {}
        if backend is not None:
            params['backend'] = backend
        if cluster is not None:
            params['cluster'] = cluster
        if namespace is not None:
            params['namespace'] = namespace
        return params

    def list_edh_container_catalog(self) -> Dict[str, Any]:
        """
        List the registered container job definitions, job queues and EKS clusters using the
        /addon/edh/containers/catalog GET endpoint.

        Returns:
            API response data: {"Items": [{"kind": "jobDefinition" | "jobQueue" | "eksCluster", ...},
            ...], "eksEnabled": bool}

        Raises:
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.get(API_EDH_CONTAINERS_CATALOG, include_auth=True, raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Container catalog listing")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to list container catalog: {e}")

    def list_edh_container_jobs(self, backend: Optional[str] = None, cluster: Optional[str] = None,
                                namespace: Optional[str] = None) -> Dict[str, Any]:
        """
        List the caller's container jobs on one backend using the /addon/edh/containers/jobs GET
        endpoint.

        Args:
            backend: 'batch' or 'eks', sent as the backend query parameter when given; the handler
                defaults an absent value to 'batch'
            cluster: EKS cluster name, sent as the cluster query parameter when given (required by
                the handler for the eks backend)
            namespace: Kubernetes namespace, sent as the namespace query parameter when given; the
                handler defaults an absent value to 'default'

        Returns:
            API response data: {"Items": [<job>, ...]}; each job carries jobId, jobName, status,
            backend, edhJobDefinition and jobHandle beside the fields EDH reports

        Raises:
            InvalidEdhRequestError: When the backend, cluster or namespace is rejected
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        params = self._edh_container_scope(backend, cluster, namespace)

        try:
            response = self.get(API_EDH_CONTAINERS_JOBS, include_auth=True, raise_http_errors=True,
                                params=params)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Container job listing")

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to list container jobs: {e}")

    def submit_edh_container_job(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit a container job using the /addon/edh/containers/jobs POST endpoint.

        Args:
            data: Job request: jobDefinition, jobQueue (the Batch job queue, or the EKS cluster for
                the eks backend), name, backend ('batch' | 'eks'), and optionally command,
                environment, vcpus, memory (MiB, at least 512) and gpu

        Returns:
            API response data (HTTP 202): jobId, jobHandle, backend, jobDefinition, jobQueue, name
            and message, plus cluster and namespace for the eks backend

        Raises:
            InvalidEdhRequestError: When the request is rejected
            EdhOperationError: When the submission is refused
            EdhOutcomeUnknownError: When EDH did not confirm the submission; it may be in progress
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.post(API_EDH_CONTAINERS_JOBS, data=data, include_auth=True,
                                 raise_http_errors=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(e, "Container job submission",
                                  list_command=_EDH_CONTAINER_JOB_LISTING)

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to submit container job: {e}")

    def kill_edh_container_job(self, job_id: str, backend: Optional[str] = None,
                               cluster: Optional[str] = None,
                               namespace: Optional[str] = None) -> Dict[str, Any]:
        """
        Kill a container job using the /addon/edh/containers/jobs/{jobId} DELETE endpoint.

        Args:
            job_id: The job's jobId as the listing shows it -- a Batch job id, or an EKS job name
            backend: 'batch' or 'eks', sent as the backend query parameter when given; the handler
                defaults an absent value to 'batch'
            cluster: EKS cluster name, sent as the cluster query parameter when given (required by
                the handler for the eks backend)
            namespace: Kubernetes namespace, sent as the namespace query parameter when given; the
                handler defaults an absent value to 'default'

        Returns:
            API response data: jobId, backend and message, plus cluster and namespace for the eks
            backend

        Raises:
            EdhContainerJobNotFoundError: When the job is not in the caller's listing on that backend
            InvalidEdhRequestError: When the job id, backend, cluster or namespace is rejected
            EdhOperationError: When the job cannot be killed from its current state
            EdhOutcomeUnknownError: When EDH did not confirm the kill; it may be in progress
            EdhUnavailableError: When the EDH cluster did not answer
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        endpoint = API_EDH_CONTAINERS_JOB.format(jobId=_edh_path_value(job_id))
        params = self._edh_container_scope(backend, cluster, namespace)

        try:
            response = self.delete(endpoint, include_auth=True, raise_http_errors=True, params=params)
            return response.json()

        except requests.exceptions.HTTPError as e:
            raise self._edh_error(
                e, "Container job kill", EdhContainerJobNotFoundError(f"Job '{job_id}' not found"),
                list_command=_EDH_CONTAINER_JOB_LISTING)

        except GlobalInfrastructureError:
            raise

        except Exception as e:
            raise APIError(f"Failed to kill container job: {e}")
