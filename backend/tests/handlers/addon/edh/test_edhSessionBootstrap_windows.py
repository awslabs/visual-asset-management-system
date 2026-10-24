# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The session bootstrap applies to Linux desktops only: a Windows image carries no consumer for a session
key, so every mint entry point answers ``disabled`` for a Windows row and mints nothing."""

from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap

USER = "user@example.com"
BOOTSTRAP_ID = "5d1c8a2e-0b1f-4a6c-9d3e-7f8a9b0c1d2e"
EVENT = {
    "requestContext": {"http": {"method": "POST", "path": "/addon/edh/workstations"}},
    "pathParameters": None,
    "queryStringParameters": None,
    "headers": {"authorization": "Bearer test-token"},
}
MINTED = {"apiKeyId": "key-2", "apiKey": "vams_" + "k" * 64, "expiresAt": "2026-09-08T20:00:00Z"}
SECRET_ARN = "secret-arn-" + BOOTSTRAP_ID


def _row(os_family):
    row = {"bootstrapId": BOOTSTRAP_ID, "userId": USER, "osFamily": os_family}
    if os_family is None:
        row.pop("osFamily")
    return row


@pytest.fixture
def enabled(monkeypatch):
    """Session bootstrap on, with the mint and the secret write replaced; returns (mint, create)."""
    monkeypatch.setattr(bootstrap, "session_bootstrap_enabled", True)
    monkeypatch.setattr(bootstrap, "resolve_vams_base_url", lambda: "https://vams.example.com")
    monkeypatch.setattr(bootstrap, "_budget_deadline", None)
    mint = MagicMock(return_value=dict(MINTED))
    create = MagicMock(return_value=SECRET_ARN)
    monkeypatch.setattr(bootstrap, "mint_session_key", mint)
    monkeypatch.setattr(bootstrap, "create_session_secret", create)
    return mint, create


@pytest.mark.unit
class TestBootstrapApplies:
    @pytest.mark.parametrize("flag,os_family,expected", [
        (True, "linux", True),
        (True, None, True),
        (True, "windows", False),
        (False, "linux", False),
        (False, "windows", False),
    ])
    def test_the_bootstrap_applies_to_desktops_that_are_not_windows(self, monkeypatch, flag, os_family, expected):
        monkeypatch.setattr(bootstrap, "session_bootstrap_enabled", flag)
        assert bootstrap.bootstrap_applies(_row(os_family)) is expected


@pytest.mark.unit
class TestWindowsRowsMintNothing:
    def test_a_windows_launch_is_disabled_and_mints_nothing(self, enabled):
        mint, create = enabled
        assert bootstrap.bootstrap_session(EVENT, _row("windows"), False) == (bootstrap.BOOTSTRAP_DISABLED, {})
        mint.assert_not_called()
        create.assert_not_called()

    def test_a_linux_launch_still_provisions(self, enabled):
        """Control: the same doubles provision a Linux row."""
        mint, create = enabled
        outcome, attributes = bootstrap.bootstrap_session(EVENT, _row("linux"), False)
        assert outcome == bootstrap.BOOTSTRAP_PROVISIONED
        assert (attributes["apiKeyId"], attributes["secretArn"]) == ("key-2", SECRET_ARN)
        mint.assert_called_once_with(EVENT, USER, BOOTSTRAP_ID, False)
        create.assert_called_once()

    def test_a_windows_reissue_is_disabled_and_mints_nothing(self, enabled):
        mint, _create = enabled
        assert bootstrap.mint_reissue_key(EVENT, _row("windows"), True) == (bootstrap.BOOTSTRAP_DISABLED, None)
        mint.assert_not_called()

    def test_a_linux_reissue_still_mints(self, enabled):
        """Control: the same doubles mint a re-issue key for a Linux row."""
        assert bootstrap.mint_reissue_key(EVENT, _row("linux"), True) == (bootstrap.BOOTSTRAP_PROVISIONED, MINTED)
