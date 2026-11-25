"""token_helper.py: the profile's token for the sign-in callback, renewed with the refresh token when it expires."""
import json
import sys
import time
import types

import pytest

from vams.connector.isaacsim.storage_adapter import token_helper


class FakeProfileManager:
    saved = []
    auth = {}

    def __init__(self, name):
        self.name = name

    def load_auth_profile(self):
        return dict(FakeProfileManager.auth) if FakeProfileManager.auth else None

    def load_config(self):
        return {"amplify_config": {"region": "us-east-1", "cognitoUserPoolId": "p", "cognitoAppClientId": "c"}}

    def save_auth_profile(self, data):
        FakeProfileManager.saved.append(dict(data))


@pytest.fixture
def cli(monkeypatch):
    """Fake vamscli modules: a profile manager and a Cognito authenticator whose refresh is scripted."""
    FakeProfileManager.saved, FakeProfileManager.auth = [], {}
    refreshes = []
    outcome = {"result": {"access_token": "fresh", "id_token": "i", "expires_at": int(time.time()) + 3600}}

    class FakeAuthenticator:
        def refresh_token(self, refresh_token):
            refreshes.append(refresh_token)
            print("Refreshing tokens...")  # must not reach stdout
            if isinstance(outcome["result"], Exception):
                raise outcome["result"]
            return dict(outcome["result"])

    profile = types.ModuleType("vamscli.utils.profile")
    profile.ProfileManager = FakeProfileManager
    auth = types.ModuleType("vamscli.commands.auth")
    auth.get_authenticator = lambda config: FakeAuthenticator()
    for name, module in {"vamscli": types.ModuleType("vamscli"), "vamscli.utils": types.ModuleType("vamscli.utils"),
                         "vamscli.commands": types.ModuleType("vamscli.commands"),
                         "vamscli.utils.profile": profile, "vamscli.commands.auth": auth}.items():
        monkeypatch.setitem(sys.modules, name, module)
    return types.SimpleNamespace(refreshes=refreshes, outcome=outcome)


def answer(capsys):
    out = capsys.readouterr().out
    return json.loads(out) if out else None


def test_a_current_sign_in_is_handed_out_without_renewal(cli, capsys):
    FakeProfileManager.auth = {"access_token": "tok", "refresh_token": "r", "expires_at": int(time.time()) + 3000}
    assert token_helper.main(["--profile", "p"]) == 0
    assert answer(capsys)["token"] == "tok" and cli.refreshes == [] and FakeProfileManager.saved == []


@pytest.mark.parametrize("left", [-60, 120])
def test_an_expired_or_expiring_cognito_sign_in_is_renewed_and_saved(cli, capsys, left):
    FakeProfileManager.auth = {"access_token": "old", "refresh_token": "r", "expires_at": int(time.time()) + left}
    assert token_helper.main(["--profile", "p"]) == 0
    assert answer(capsys)["token"] == "fresh"
    assert cli.refreshes == ["r"]
    saved = FakeProfileManager.saved[-1]
    assert saved["access_token"] == "fresh" and saved["refresh_token"] == "r"


def test_a_failed_renewal_of_an_expired_sign_in_reports_no_sign_in_and_saves_nothing(cli, capsys):
    cli.outcome["result"] = RuntimeError("Token refresh failed")
    FakeProfileManager.auth = {"access_token": "old", "refresh_token": "r", "expires_at": int(time.time()) - 60}
    assert token_helper.main(["--profile", "p"]) == 3
    assert answer(capsys) is None and FakeProfileManager.saved == []


def test_a_failed_renewal_of_a_still_valid_sign_in_hands_out_the_current_token(cli, capsys):
    cli.outcome["result"] = RuntimeError("Token refresh failed")
    FakeProfileManager.auth = {"access_token": "old", "refresh_token": "r", "expires_at": int(time.time()) + 120}
    assert token_helper.main(["--profile", "p"]) == 0
    assert answer(capsys)["token"] == "old"


def test_an_override_token_is_never_renewed(cli, capsys):
    FakeProfileManager.auth = {"access_token": "key", "token_type": "override", "refresh_token": "r",
                               "expires_at": int(time.time()) - 60}
    assert token_helper.main(["--profile", "p"]) == 3
    assert cli.refreshes == [] and FakeProfileManager.saved == []


def test_no_sign_in_reports_no_sign_in(cli, capsys):
    assert token_helper.main(["--profile", "p"]) == 3
    assert answer(capsys) is None and cli.refreshes == []


def test_expires_within_reads_the_shapes_the_cli_writes():
    now = time.time()
    assert token_helper._expires_within(int(now) + 100, 300) is True
    assert token_helper._expires_within(int(now) + 1000, 300) is False
    assert token_helper._expires_within(int(now * 1000) + 100_000, 300) is True
    assert token_helper._expires_within("2999-01-01T00:00:00Z", 300) is False
    assert token_helper._expires_within("not a date", 300) is False
    assert token_helper._expires_within(None, 300) is False
