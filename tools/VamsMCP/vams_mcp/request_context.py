"""Per-request state of a remote server: who the request acts for, and the tool call's time budget.

Both live in context variables, so concurrent requests never see each other's values: the HTTP gate
sets the identity for one request (``http_app.py``), and the remote tool wrapper sets the deadline for
one tool call (``server.tool_result``) before running the tool body in a worker thread with a copy of
the context. Over stdio neither is ever set, and every helper here answers as if there were no bound.
"""

from __future__ import annotations

import contextlib
import contextvars
import time
from typing import Any, Iterator, Optional, Tuple

# The verified identity of the HTTP request being served (``remote_auth.VerifiedIdentity``).
_IDENTITY: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar("vams_mcp_identity", default=None)
# (time.monotonic() deadline, budget in seconds) of the tool call being served.
_DEADLINE: contextvars.ContextVar[Optional[Tuple[float, float]]] = contextvars.ContextVar(
    "vams_mcp_deadline", default=None
)

# A paged walk does not start another page once less than this share of the budget remains, so the
# page it would start (one API request, bounded by the client's read timeout) cannot carry the call
# past its budget.
PAGE_RESERVE_FRACTION = 0.25


def current_identity() -> Optional[Any]:
    """The verified identity of the request being served, or None outside a remote request."""
    return _IDENTITY.get()


@contextlib.contextmanager
def acting_for(identity: Any) -> Iterator[None]:
    """Make ``identity`` the current identity for the body of the ``with`` block."""
    token = _IDENTITY.set(identity)
    try:
        yield
    finally:
        _IDENTITY.reset(token)


@contextlib.contextmanager
def time_budget(seconds: float) -> Iterator[None]:
    """Give the body of the ``with`` block a deadline of ``seconds`` from now."""
    token = _DEADLINE.set((time.monotonic() + seconds, float(seconds)))
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def remaining_seconds() -> Optional[float]:
    """Seconds left in the current tool call's budget, or None when no budget applies (stdio)."""
    budget = _DEADLINE.get()
    if budget is None:
        return None
    return budget[0] - time.monotonic()


def budget_low() -> bool:
    """True when a call should start no further API request: at most ``PAGE_RESERVE_FRACTION`` of the
    budget remains. Always False when no budget applies (stdio)."""
    budget = _DEADLINE.get()
    if budget is None:
        return False
    return budget[0] - time.monotonic() <= budget[1] * PAGE_RESERVE_FRACTION
