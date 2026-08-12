# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os

# boto3 region — needed before physnaCommon imports because its module-level
# dynamodb/s3 resources require a region. Root conftest sets AWS_REGION but
# boto3 looks for AWS_DEFAULT_REGION in this version; set both.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

# Env vars required by physnaCommon / physnaFileSync / physnaAssetSync at import time
os.environ.setdefault("PHYSNA_TENANT_ID", "00000000-0000-0000-0000-000000000001")
os.environ.setdefault("PHYSNA_API_BASE", "https://app-api.physna.com/v3/")
os.environ.setdefault(
    "PHYSNA_TOKEN_URL",
    "https://physna-app.auth.us-east-2.amazoncognito.com/oauth2/token",
)
os.environ.setdefault("PHYSNA_AUTH_TYPE", "cognito")
os.environ.setdefault(
    "PHYSNA_CREDS_SECRET_ARN",
    "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams-physna-creds-ABCDEF",
)
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-assets")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-databases")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attributes")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets")
os.environ.setdefault("SYNC_TRACKING_OUTBOUND_STORAGE_TABLE_NAME", "test-sync-tracking")

# The Physna Viewer handler imports models.common, which in turn pulls in
# customLogging.auditLogging. The root test conftest installs customLogging
# as a non-package mock module, so the `from customLogging.auditLogging ...`
# import inside models.common fails at collection time unless we stub the
# submodule here. Matching pattern used elsewhere in the test suite.
import sys
import types

# Provide handlers.auth.request_to_claims and handlers.authz.CasbinEnforcer
# used by the Physna Viewer handler. The root conftest installs empty
# MockModule stubs for these paths on every test run (see autouse
# setup_mock_imports), so we ALSO register them here at collection time to
# satisfy module-load, and then re-register via a session-scoped fixture
# below so they survive the autouse fixture's clobbering.
_auth_mock = types.ModuleType("handlers.auth")


def _mock_request_to_claims(event):
    return {"tokens": ["mock-user"], "roles": []}


_auth_mock.request_to_claims = _mock_request_to_claims
sys.modules["handlers.auth"] = _auth_mock


class _MockCasbinEnforcer:
    def __init__(self, *_args, **_kwargs):
        pass

    def enforceAPI(self, _event):
        return True

    def enforce(self, *_args, **_kwargs):
        return True


_authz_mock = types.ModuleType("handlers.authz")
_authz_mock.CasbinEnforcer = _MockCasbinEnforcer
sys.modules["handlers.authz"] = _authz_mock


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reinstall_physna_viewer_auth_mocks():
    """The root conftest autouse fixture rebuilds handlers.auth / handlers.authz
    as empty MockModule instances before every test. Reinstall our functional
    mocks afterwards so the Physna Viewer lambda's lazy imports find real
    callables."""
    sys.modules["handlers.auth"] = _auth_mock
    sys.modules["handlers.authz"] = _authz_mock
    yield


# The mock common.validators shipped with the root test harness doesn't
# expose the regex-pattern constants the Physna Viewer model imports. Inject
# conservative matches so PhysnaViewerRequestModel can be imported in tests.
import re as _re  # noqa: E402


@pytest.fixture(autouse=True)
def _reinstall_common_validators_patterns():
    """Each test run, after the root autouse fixture resets common.validators,
    ensure the patterns the Physna Viewer model needs are present."""
    vmod = sys.modules.get("common.validators")
    if vmod is not None:
        if not hasattr(vmod, "id_pattern"):
            vmod.id_pattern = r"^[-_a-zA-Z0-9]{3,63}$"
        if not hasattr(vmod, "filename_pattern"):
            vmod.filename_pattern = r".{1,256}"
        if not hasattr(vmod, "relative_file_path_pattern"):
            vmod.relative_file_path_pattern = r"^/.*$"
        if not hasattr(vmod, "validate"):
            def _ok(_params):
                return (True, "")
            vmod.validate = _ok
    yield


if "customLogging.auditLogging" not in sys.modules:
    _audit_mock = types.ModuleType("customLogging.auditLogging")

    def _noop(*_args, **_kwargs):
        return None

    # Stub every audit helper as a no-op. Add more here if the backend
    # handlers under test import additional audit functions.
    for _name in (
        "log_authentication",
        "log_authorization",
        "log_authorization_api",
        "log_file_upload",
        "log_file_download",
        "log_file_download_streamed",
        "log_errors",
        "log_auth_other",
        "log_auth_changes",
        "log_actions",
    ):
        setattr(_audit_mock, _name, _noop)
    sys.modules["customLogging.auditLogging"] = _audit_mock
