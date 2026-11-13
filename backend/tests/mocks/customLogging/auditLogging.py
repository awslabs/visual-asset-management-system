# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Mock implementation of customLogging.auditLogging for testing.

The real auditLogging module writes structured audit events to CloudWatch log
groups. Tests do not assert on audit output and have no CloudWatch backend, so
every audit function here is a no-op (the record formatter returns a plain
record). This mock exists so that handler modules
that `from customLogging.auditLogging import ...` at import time can be collected
and imported under pytest (the mock customLogging package previously only
provided logger.py, which broke collection of any handler that audits).

Keep the exported names in sync with backend/customLogging/auditLogging.py.
"""

import contextlib
import json


def _noop(*args, **kwargs):
    """Generic no-op stand-in for all audit log functions."""
    return None


# Mirror the public audit functions exported by the real module.
log_authentication = _noop
log_authorization = _noop
log_authorization_denials = _noop
log_authorization_api = _noop
report_undelivered_audit_records = _noop


def format_authorization_denial_record(claims_and_roles, probe_endpoint, custom_data):
    """Stand-in for the real formatter: a record naming the probe endpoint and the denied check,
    so a test that inspects the batched records still sees what was denied."""
    return (f"[AUTHORIZATION][authorized: False] [probe: {probe_endpoint}] "
            f"{json.dumps(custom_data, default=str)}")


@contextlib.contextmanager
def deferred_authorization_audit():
    """No-op stand-in: there is nothing to collect when log_authorization writes nothing."""
    yield
log_authorization_gateway = _noop
log_file_upload = _noop
log_file_download = _noop
log_file_download_bulk = _noop
log_file_download_streamed = _noop
log_auth_other = _noop
log_auth_changes = _noop
log_actions = _noop
log_errors = _noop
log_action = _noop
current_audit_context = _noop
flush_audit_context = _noop

OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"
OUTCOME_DENIED = "denied"


def audit_records_reads():
    """The reads switch as the real module reads it: on unless AUDIT_RECORD_READS is "false"."""
    import os
    return os.environ.get("AUDIT_RECORD_READS", "").strip().lower() != "false"


@contextlib.contextmanager
def audit_request(event, lambda_context=None, *, on_behalf_of=None):
    """No-op stand-in for the request audit context: nothing is buffered when nothing is written."""
    yield None


def audited_handler(handler):
    """Stand-in for the entrypoint decorator: the handler unchanged."""
    return handler


AUDIT_INTERNAL_CALL_KEY = "vamsAuditInternalCall"


def internal_call_marker(event=None):
    """Stand-in for the real marker: empty when no request context is open."""
    return {}


def audit_failure_reason(status):
    """Stand-in for the real mapping of an error status to its generic reason."""
    return "internal" if status >= 500 else "clientError"
