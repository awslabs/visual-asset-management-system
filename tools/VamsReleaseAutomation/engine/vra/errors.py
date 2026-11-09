# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exit codes and exception types. Agents branch on the exit codes."""

from __future__ import annotations

OK = 0
OTHER_ERROR = 1
USAGE_ERROR = 2
BLOCKED_ON_IT = 10
NEEDS_HUMAN = 20
TESTS_FAILED = 30
ROLLBACK_FAILED = 40

# Run status values
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
S_BLOCKED_ON_IT = "BLOCKED_ON_IT"
S_NEEDS_HUMAN = "NEEDS_HUMAN"
S_TESTS_FAILED = "TESTS_FAILED"
ROLLED_BACK = "ROLLED_BACK"
S_ROLLBACK_FAILED = "ROLLBACK_FAILED"
FAILED = "FAILED"

RUN_STATUSES = (RUNNING, SUCCEEDED, S_BLOCKED_ON_IT, S_NEEDS_HUMAN, S_TESTS_FAILED, ROLLED_BACK,
                S_ROLLBACK_FAILED, FAILED)

STATUS_TO_EXIT = {
    SUCCEEDED: OK,
    RUNNING: OK,
    ROLLED_BACK: TESTS_FAILED,
    S_BLOCKED_ON_IT: BLOCKED_ON_IT,
    S_NEEDS_HUMAN: NEEDS_HUMAN,
    S_TESTS_FAILED: TESTS_FAILED,
    S_ROLLBACK_FAILED: ROLLBACK_FAILED,
    FAILED: OTHER_ERROR,
}


class VraError(Exception):
    """Base error. ``exit_code`` and ``run_status`` drive the CLI exit and run state."""

    exit_code = OTHER_ERROR
    run_status = FAILED
    step_status = "failed"

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class UsageError(VraError):
    exit_code = USAGE_ERROR


class ConfigError(VraError):
    """Invalid ledger / configuration. Treated as a usage problem."""

    exit_code = USAGE_ERROR


class BlockedOnIT(VraError):
    exit_code = BLOCKED_ON_IT
    run_status = S_BLOCKED_ON_IT
    step_status = "blocked"


class NeedsHuman(VraError):
    exit_code = NEEDS_HUMAN
    run_status = S_NEEDS_HUMAN
    step_status = "blocked"


class TestsFailed(VraError):
    exit_code = TESTS_FAILED
    run_status = S_TESTS_FAILED
    step_status = "failed"


class RolledBack(VraError):
    """Tests/deploy failed and the env was rolled back successfully."""

    exit_code = TESTS_FAILED
    run_status = ROLLED_BACK
    step_status = "failed"


class RollbackFailed(VraError):
    exit_code = ROLLBACK_FAILED
    run_status = S_ROLLBACK_FAILED
    step_status = "failed"


class PermissionDenied(VraError):
    """Raised by a step when an AWS call / CloudFormation event was denied.

    Carries classified denials; the run engine turns it into an IT request
    and BLOCKED_ON_IT.
    """

    exit_code = BLOCKED_ON_IT
    run_status = S_BLOCKED_ON_IT
    step_status = "blocked"

    def __init__(self, message: str, denials: list, principal: str | None = None,
                 env: str | None = None, role: str = "deploy"):
        super().__init__(message)
        self.denials = denials
        self.principal = principal
        self.env = env
        self.role = role


class CommandFailed(VraError):
    def __init__(self, message: str, returncode: int, output: str = ""):
        super().__init__(message, {"returncode": returncode})
        self.returncode = returncode
        self.output = output
