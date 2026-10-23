"""Exceptions of the EDH Workstations add-on commands and ``APIClient`` methods.

Every class derives from ``EdhError``, a ``BusinessLogicError`` that the ``edh`` commands catch and
report. The class names are what ``--json-output`` and the MCP tools report as ``error_type``.
"""

from ..exceptions import BusinessLogicError


class EdhError(BusinessLogicError):
    """Base class for EDH Workstations add-on errors."""
    pass


class EdhWorkstationNotFoundError(EdhError):
    """Raised when a workstation session is not found, or is not owned by the caller."""
    pass


class EdhImageNotFoundError(EdhError):
    """Raised when an EDH software-stack image is not found."""
    pass


class InvalidEdhRequestError(EdhError):
    """Raised when an EDH request is rejected as invalid."""
    pass


class EdhOperationError(EdhError):
    """Raised when an EDH operation is refused for the resource's current state."""
    pass


class EdhUnavailableError(EdhError):
    """Raised when the EDH cluster did not answer the VAMS proxy."""
    pass


class EdhOutcomeUnknownError(EdhUnavailableError):
    """Raised when EDH did not confirm a mutating request, which may still be in progress."""
    # Carried into --json-output beside the error, as the API's own error body carries it.
    json_fields = {"outcomeUnknown": True}


class EdhStartInProgressError(EdhOperationError):
    """Raised when a workstation start is refused because an earlier start may still be under way,
    or the workstation's session key is being revoked or re-issued."""
    # Carried into --json-output beside the error, as the API's own error body carries it.
    json_fields = {"startInProgress": True}


class EdhReservationFullError(EdhOperationError):
    """Raised when a launch or start is refused because its capacity reservation has no free slot
    and does not fall back to on-demand (a launch), or the stopped desktop's reservation is full (a
    start)."""
    # Carried into --json-output beside the error, as the API's own error body carries it.
    json_fields = {"code": "RESERVATION_FULL"}


class EdhCapacityReservationNotFoundError(EdhError):
    """Raised when a capacity reservation is not registered in VAMS."""
    pass


class EdhContainerJobNotFoundError(EdhError):
    """Raised when an EDH container job is not found."""
    pass


class EdhAmiRecipeNotFoundError(EdhError):
    """Raised when an EDH AMI recipe is not found."""
    pass


class EdhAmiRecipeBuildNotFoundError(EdhError):
    """Raised when a build of an EDH AMI recipe is not found."""
    pass
