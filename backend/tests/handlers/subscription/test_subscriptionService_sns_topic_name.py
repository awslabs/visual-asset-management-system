# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Subscription recreate path must use the shared collision-safe topic name."""

import hashlib
import importlib
import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Keep real common/models packages available before root conftest stubs handlers.
from backend.backend.handlers.subscription import subscriptionService as _seed  # noqa: F401

PACKAGE = "backend.backend.handlers.subscription"
HANDLER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(_seed.__file__)), "subscriptionService.py")


def _digest(asset_id: str) -> str:
    return hashlib.sha256(asset_id.encode('utf-8')).hexdigest()[:10]


def _load_subscription_service():
    """Load a fresh subscriptionService with auth/authz stubbed, SNS mockable."""
    stub_names = ("handlers.auth", "handlers.authz")
    saved = {name: sys.modules.get(name) for name in stub_names}

    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock()
    sys.modules["handlers.auth"] = auth_stub

    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub

    module_name = f"{PACKAGE}._sns_topic_probe"
    # Restore real common.* before exec so snsTopicNames resolves.
    real_common = {
        name: mod for name, mod in list(sys.modules.items())
        if mod is not None and (name == "common" or name.startswith("common."))
    }
    saved_common = {name: sys.modules.get(name) for name in real_common}
    for name, mod in real_common.items():
        sys.modules[name] = mod

    os.environ.setdefault("SUBSCRIPTIONS_STORAGE_TABLE_NAME", "test-subscription-table")
    os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
    os.environ.setdefault("USER_STORAGE_TABLE_NAME", "test-user-table")
    os.environ.setdefault("AWS_REGION", "us-east-1")

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
                "boto3.resource", return_value=MagicMock()):
            spec = importlib.util.spec_from_file_location(module_name, HANDLER_PATH)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            return module
    finally:
        sys.modules.pop(module_name, None)
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        for name, previous in saved_common.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


@pytest.mark.unit
class TestSubscriptionCreateSnsTopicUsesSharedHelper:
    def test_dotted_asset_id_uses_collision_safe_name(self):
        m = _load_subscription_service()
        client = MagicMock()
        client.create_topic.return_value = {"TopicArn": "arn:sns:t"}
        m.sns_client = client
        asset_id = "x-r.1"

        arn = m.create_sns_topic(asset_id, "testdb1")

        assert arn == "arn:sns:t"
        client.create_topic.assert_called_once_with(
            Name=f"AssetTopictestdb1-x-r-1-{_digest(asset_id)}")

    def test_matches_create_asset_helper_for_colliding_ids(self):
        snsTopicNames = importlib.import_module("common.snsTopicNames")
        m = _load_subscription_service()
        client = MagicMock()
        client.create_topic.return_value = {"TopicArn": "arn:sns:t"}
        m.sns_client = client

        for asset_id in ("a.b", "a-b", "a b"):
            m.create_sns_topic(asset_id, "testdb1")

        names = [call.kwargs["Name"] for call in client.create_topic.call_args_list]
        expected = [
            snsTopicNames.asset_sns_topic_name("testdb1", asset_id)
            for asset_id in ("a.b", "a-b", "a b")
        ]
        assert names == expected
        assert len(set(names)) == 3
