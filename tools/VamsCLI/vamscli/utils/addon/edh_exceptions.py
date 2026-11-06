"""Exceptions of the EDH Workstations add-on commands and ``APIClient`` methods.

Every class derives from ``EdhError``, a ``BusinessLogicError`` that the ``edh`` commands catch and
report. The class names are what ``--json-output`` and the MCP tools report as ``error_type``.
"""

from ..exceptions import BusinessLogicError, PermissionDeniedError


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
    """Raised when a launch is refused because its capacity reservation has no free slot and does
    not fall back to on-demand. A start answers 202 and runs in the background, so a start refused
    for a full reservation is recorded on the workstation (`lastError` RESERVATION_FULL) instead."""
    # Carried into --json-output beside the error, as the API's own error body carries it.
    json_fields = {"code": "RESERVATION_FULL"}


class EdhDisplaySessionEndedError(EdhOperationError):
    """Raised when a connect or a desktop sign-in is refused (409 `code` DISPLAY_SESSION_ENDED) because
    the workstation is running but its remote display session has ended: EDH lists it with a link that
    signs no one in. The workstation records `connectionUnavailableReason` DISPLAY_SESSION_ENDED; stop
    it, then start it again, to reconnect."""
    # Carried into --json-output beside the error, as the API's own error body carries it.
    json_fields = {"code": "DISPLAY_SESSION_ENDED"}


class EdhReservationNotPermittedError(PermissionDeniedError, EdhError):
    """Raised when a launch is refused (403) because the caller's role may see the capacity
    reservation but not launch into it (`code` RESERVATION_NOT_PERMITTED). Both a permission denial,
    so the client's guards let it through as one, and an EdhError, so the `edh` commands report it
    with its remedy. A role that may not see the reservation either gets the generic 403, a
    PermissionDeniedError, which names nothing."""
    # PermissionDeniedError.for_code picks this class for a handler's 403 coded so.
    forbidden_code = "RESERVATION_NOT_PERMITTED"
    # Carried into --json-output beside the error, as the API's own error body carries it.
    json_fields = {"code": "RESERVATION_NOT_PERMITTED"}


# The record fields a `start --wait` error carries into --json-output beside its outcome.
_START_WAIT_RECORD_FIELDS = ("state", "activity", "lastError", "startFailure")


def _start_wait_fields(outcome: str, record) -> dict:
    fields = {"waitOutcome": outcome}
    if isinstance(record, dict):
        fields.update({key: record.get(key) for key in _START_WAIT_RECORD_FIELDS})
    return fields


class EdhStartFailedError(EdhOperationError):
    """Raised by `edh workstation start --wait` when the start ended without the workstation
    running: EDH refused it, leaving it STOPPED with the reason under lastError and startFailure, or
    it ended in another settled state."""
    # Carried into --json-output beside the error; an instance adds the record's state, activity,
    # lastError and startFailure.
    json_fields = {"waitOutcome": "failed"}

    def __init__(self, message: str, record=None):
        super().__init__(message)
        self.json_fields = _start_wait_fields("failed", record)


class EdhStartStalledError(EdhOperationError):
    """Raised by `edh workstation start --wait` when the session sweep recorded the start as stalled
    (`lastError` START_STALLED): EDH accepted it and has not finished it after 30 minutes. The
    workstation stays CREATING and is not stopped for it; stop it, then start it again. Not an
    EdhStartFailedError, so a caller that starts a failed start again does not start this one."""
    # Carried into --json-output beside the error; an instance adds the record's state, activity,
    # lastError and startFailure.
    json_fields = {"waitOutcome": "stalled"}

    def __init__(self, message: str, record=None):
        super().__init__(message)
        self.json_fields = _start_wait_fields("stalled", record)


class EdhStartWaitTimeoutError(EdhError):
    """Raised by `edh workstation start --wait` when the workstation was still starting when the
    wait ran out; the start may still complete."""
    # Carried into --json-output beside the error; an instance adds the record's state, activity,
    # lastError and startFailure.
    json_fields = {"waitOutcome": "timedOut"}

    def __init__(self, message: str, record=None):
        super().__init__(message)
        self.json_fields = _start_wait_fields("timedOut", record)


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
