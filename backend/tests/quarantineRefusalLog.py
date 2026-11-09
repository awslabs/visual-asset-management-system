# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""How every file route logs a quarantine refusal while the download block is on (D174).

The refusal is an expected 400, so it is logged once at INFO with the database, the asset and the
route, and without a traceback (``quarantineGuard.quarantine_refusal``). ``general_error`` adds its
WARNING "General error" line, as for every 4xx answer. Nothing is logged at ERROR. Before, each
guarded handler's ``except VAMSGeneralErrorResponse`` wrote the refusal with ``logger.exception``:
an ERROR line with a traceback for every refused download.

``captured_logs(handler_module)`` puts recording loggers in place of the handler's, the guard's and
the one ``general_error`` writes with; ``assert_refusal_logged_once`` checks what they recorded.
"""

import sys
from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

import common.compliance.quarantineGuard as guard

# The log methods that write at ERROR or above.
ERROR_LEVELS = ("error", "exception", "critical")


@contextmanager
def captured_logs(handler_module):
    """`{"handler", "guard", "general_error"}` -> a MagicMock logger in place of each one."""
    loggers = {"handler": MagicMock(name="handler.logger"), "guard": MagicMock(name="guard.logger"),
               "general_error": MagicMock(name="general_error.logger")}
    guard_general_error = getattr(guard, "general_error", handler_module.general_error)
    general_modules = {sys.modules[handler_module.general_error.__module__],
                       sys.modules[guard_general_error.__module__]}
    with ExitStack() as stack:
        stack.enter_context(patch.object(handler_module, "logger", loggers["handler"]))
        stack.enter_context(patch.object(guard, "logger", loggers["guard"]))
        for module in general_modules:
            stack.enter_context(patch.object(module, "logger", loggers["general_error"]))
        yield loggers


def _records(loggers):
    """(logger name, level, message, kwargs) of every call a recording logger took."""
    records = []
    for name, logger in loggers.items():
        for method, args, kwargs in logger.method_calls:
            message = str(args[0]) if args else ""
            records.append((name, method, message, kwargs))
    return records


def assert_refusal_logged_once(loggers, database_id, asset_id, method, path):
    """One INFO line names the refusal (database, asset, `<METHOD> <path>`), no line is at ERROR
    or above, none carries a traceback, and `general_error` wrote its one WARNING line."""
    records = _records(loggers)
    refusals = [r for r in records if "quarantin" in r[2].lower()]
    assert len(refusals) == 1, records
    name, level, message, kwargs = refusals[0]
    assert (name, level) == ("guard", "info"), records
    assert f"asset {asset_id}" in message and f"database {database_id}" in message, message
    assert f"on {method} {path}" in message, message
    assert not kwargs.get("exc_info"), kwargs
    assert [r for r in records if r[1] in ERROR_LEVELS] == [], records
    assert [r for r in records if r[3].get("exc_info")] == [], records
    general = [r for r in records if r[0] == "general_error"]
    assert [(r[1], r[2]) for r in general] == [("warning", "General error")], records


def assert_error_logged_with_traceback(loggers):
    """[control] Another `VAMSGeneralErrorResponse` keeps the handler's `logger.exception` line."""
    records = _records(loggers)
    assert [r[1] for r in records if r[0] == "handler" and r[2].startswith("VAMS error")] == [
        "exception"], records
