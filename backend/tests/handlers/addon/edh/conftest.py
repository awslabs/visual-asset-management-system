# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import sys

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.addon.edh.edhRealConstants import install_real_constants

# The EDH modules bind common.constants at import, during collection, so the real module is pinned
# here first -- as the auth conftest does.
install_real_constants()

# Environment the EDH modules read at import time. Region variables and the mock customLogging
# package come from the root conftests, which load before this one.
os.environ.setdefault("EDH_API_ENDPOINT", "https://internal-edh-alb.example.internal")
os.environ.setdefault(
    "EDH_ADMIN_KEY_SECRET_ARN",
    "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams-edh-root-key-AbCdEf",
)
os.environ.setdefault("EDH_CA_BUNDLE_SECRET_ARN", "")
os.environ.setdefault("EDH_API_TLS_SERVER_NAME", "")
os.environ.setdefault("EDH_CLUSTER_ID", "edh-test")
os.environ.setdefault("EDH_AUTO_PROVISION_USERS", "true")
os.environ.setdefault("EDH_ALLOWED_INSTANCE_TYPES", '["g5.xlarge", "g5.2xlarge"]')
os.environ.setdefault("EDH_VDI_DEFAULT_PROJECT", "default")
os.environ.setdefault("EDH_VDI_PROFILE_ID", "1")
# The launch worker Lambda the workstation service invokes asynchronously for a create.
os.environ.setdefault("EDH_LAUNCH_WORKER_FUNCTION_NAME", "vams-edh-launch-worker")
# Break-glass override for AddonResourceKeys.EDH_SESSION_STORAGE_TABLE, so the session table resolves
# without an SSM call at import.
os.environ.setdefault("EDH_SESSION_STORAGE_TABLE_NAME", "test-edh-session-storage")
os.environ.setdefault("EDH_AMI_RECIPE_STORAGE_TABLE_NAME", "test-edh-ami-recipes")
os.environ.setdefault("EDH_AMI_BUILDS_ENABLED", "true")
os.environ.setdefault("EDH_AMI_BUILD_PREFIX", "vams-test-edh")
os.environ.setdefault("EDH_AMI_BUILD_SUBNET_ID", "subnet-0123456789abcdef0")
os.environ.setdefault("EDH_AMI_BUILD_SECURITY_GROUP_ID", "sg-0123456789abcdef0")
os.environ.setdefault("EDH_AMI_BUILD_INSTANCE_PROFILE_NAME", "vams-test-edh-ami-build")
os.environ.setdefault("EDH_AMI_BUILD_ACCOUNT_ID", "123456789012")
os.environ.setdefault("EDH_AMI_BUILD_PARTITION", "aws")
os.environ.setdefault("EDH_AMI_BUILD_TIMEOUT_MINUTES", "180")
os.environ.setdefault("EDH_AMI_KEEP_VERSIONS_DEFAULT", "3")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
# Session-bootstrap environment read by edhSessionBootstrap at import. The flag is off so the
# workstation-service tests, which do not replace the bootstrap module, never reach its Lambda or
# Secrets Manager clients; the bootstrap tests patch `session_bootstrap_enabled` on the module.
os.environ.setdefault("EDH_SESSION_BOOTSTRAP_ENABLED", "false")
os.environ.setdefault("EDH_SESSION_SECRET_PREFIX", "vams/edh-session/")
os.environ.setdefault("EDH_SESSION_KEY_TTL_MINUTES", "480")
os.environ.setdefault(
    "EDH_WORKSTATION_INSTANCE_ROLE_ARNS",
    '["arn:aws:iam::123456789012:role/EdhDesktopInstanceRole"]',
)
os.environ.setdefault(
    "EDH_SESSION_SECRET_KMS_KEY_ARN",
    "arn:aws:kms:us-east-1:123456789012:key/11111111-2222-3333-4444-555555555555",
)
os.environ.setdefault("API_KEY_SERVICE_LAMBDA_FUNCTION_NAME", "test-apiKeyService")
os.environ.setdefault("VAMS_BASE_URL", "https://vams.example.com")
# The container service reads the EKS switch at import; off, so the eks backend is refused unless a
# test turns it on through the module attribute.
os.environ.setdefault("EDH_CONTAINERS_EKS_ENABLED", "false")

# The root conftest's autouse fixture installs placeholder modules under backend.backend.handlers
# when nothing has imported the real package yet, and a placeholder has no __path__. Importing the
# package here, once the environment above is in place, keeps the real package registered for every
# EDH test whatever module is collected first.
import backend.backend.handlers  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _fresh_edh_module_state():
    """The module-level singletons and caches the EDH handlers lean on start empty and are emptied
    again after each test: the client's Secrets Manager handle, HTTP pool, root key and CA bundle, and
    the SSM resource-name cache. Imported lazily so the environment above is in place first, and by the
    package paths every EDH test and support module patches."""
    import backend.backend.handlers.addon.edh.common.edhClient as ec

    def reset():
        ec._reset_client_state_for_tests()
        resource_names = sys.modules.get("common.resourceNames")
        if isinstance(getattr(resource_names, "_cache", None), dict):
            resource_names._cache.clear()
            resource_names._missing_keys.clear()
            resource_names._cache_fetched_at = 0.0
            resource_names._ssm_client = None

    reset()
    yield
    reset()


@pytest.fixture
def edh_session_table(monkeypatch):
    """Create a moto DynamoDB session table keyed on bootstrapId."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table_name = os.environ["EDH_SESSION_STORAGE_TABLE_NAME"]
        resource.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "bootstrapId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "bootstrapId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        # Point the lease policy module at the mocked table.
        import backend.backend.handlers.addon.edh.common.edhLeasePolicy as lp
        monkeypatch.setattr(lp, "_session_table", resource.Table(table_name))
        yield resource.Table(table_name)
