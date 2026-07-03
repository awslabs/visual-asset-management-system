# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Mock implementation of customLogging.auditLogging for testing.

The real auditLogging module writes structured audit events to CloudWatch log
groups. Tests do not assert on audit output and have no CloudWatch backend, so
every audit function here is a no-op. This mock exists so that handler modules
that `from customLogging.auditLogging import ...` at import time can be collected
and imported under pytest (the mock customLogging package previously only
provided logger.py, which broke collection of any handler that audits).

Keep the exported names in sync with backend/customLogging/auditLogging.py.
"""


def _noop(*args, **kwargs):
    """Generic no-op stand-in for all audit log functions."""
    return None


# Mirror the public audit functions exported by the real module.
log_authentication = _noop
log_authorization = _noop
log_authorization_api = _noop
log_authorization_gateway = _noop
log_file_upload = _noop
log_file_download = _noop
log_file_download_bulk = _noop
log_file_download_streamed = _noop
log_auth_other = _noop
log_auth_changes = _noop
log_actions = _noop
log_errors = _noop
