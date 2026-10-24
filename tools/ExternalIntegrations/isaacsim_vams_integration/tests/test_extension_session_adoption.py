"""Tests for how the extension takes over a session the VAMS CLI profile already holds.

An EDH workstation signs the CLI in at desktop login and keeps retrying for up to 20 minutes, while
Isaac Sim starts after two, so the extension can start before its profile is signed in. Browsing must
then pick the session up once the sign-in lands, not only at startup; and shutting down must leave a
session the extension did not create signed in, because the desktop has no other way to sign it
back in. A session it took over can also hold the key VAMS revoked when the desktop last stopped, or
a key that expired, which the CLI reports as a permission error, so a refusal of that session says
how to recover.
"""

import json
import types

import pytest

from vams.connector.isaacsim import extension as extension_module
from vams.connector.isaacsim import vams_cli_service as service_module
from vams.connector.isaacsim.extension import VamsConnectorExtension
from vams.connector.isaacsim.vams_cli_service import (
    Asset,
    AssetFile,
    AuthStatus,
    Database,
    VamsAccessDeniedError,
    VamsAuthenticationError,
    VamsCliError,
)

_FORBIDDEN = ("API Error: Failed to list databases: Access forbidden. You do not have permission to "
              "perform this action. Contact your administrator if you believe this is an error.")
_HINT = ("access refused - if this desktop was just started, its VAMS sign-in may not have completed; "
         "select Refresh Databases later. If the sign-in has expired, renew the workstation's lease in "
         "VAMS, or sign in under Authentication")


def _raise(error):
    def _call(*_args, **_kwargs):
        raise error
    return _call


class _Profile:
    """A connector over a CLI profile that the desktop's sign-in may or may not have written yet."""

    def __init__(self):
        self.signed_in = False
        self._authenticated = False
        self.logouts = 0

    def get_auth_status(self):
        if not self.signed_in:
            return AuthStatus()
        return AuthStatus(authenticated=True, user_id="user@example.com", is_expired=False)

    def list_databases(self):
        if not self.signed_in:
            raise VamsAuthenticationError("Not authenticated. Please login first.")
        return [Database(database_id="db1")]

    def login_with_token(self, user_id, token):
        self.signed_in = True
        self._authenticated = True
        return True

    def logout(self):
        self.logouts += 1
        self.signed_in = False
        self._authenticated = False


class _Model:
    def __init__(self):
        self.value = ""

    def get_value_as_string(self):
        return self.value

    def set_value(self, value):
        self.value = value


@pytest.fixture
def extension(monkeypatch):
    monkeypatch.setattr(extension_module, "_defer", lambda fn: fn())
    ext = VamsConnectorExtension()
    ext._init_state()
    ext._window = None
    ext._connector = _Profile()
    ext._username_model = _Model()
    ext._credential_model = _Model()
    ext._auth_frame = types.SimpleNamespace(collapsed=False)
    ext._status_label = types.SimpleNamespace(text="")
    return ext


def test_a_session_signed_in_after_startup_is_taken_over_by_refresh(extension):
    extension._check_existing_session()
    assert extension._connector._authenticated is False
    assert extension._auth_frame.collapsed is False

    extension._connector.signed_in = True
    extension._load_databases()

    assert extension._connector._authenticated is True
    assert extension._auth_frame.collapsed is True
    assert extension._username_model.value == "user@example.com"
    assert extension._databases == [Database(database_id="db1")]
    assert extension._status_label.text == "  1 database(s)"


def test_refresh_before_the_sign_in_lands_reports_not_authenticated(extension):
    extension._load_databases()
    assert extension._connector._authenticated is False
    assert "Not authenticated" in extension._status_label.text


def test_shutdown_leaves_a_taken_over_session_signed_in(extension):
    extension._connector.signed_in = True
    extension._check_existing_session()
    connector = extension._connector
    extension.on_shutdown()
    assert connector.logouts == 0
    assert connector.signed_in is True


def test_shutdown_signs_out_a_session_the_extension_created(extension):
    """Control: a credential typed into the extension is still cleared when it shuts down, including
    after it had taken over the desktop's session."""
    extension._connector.signed_in = True
    extension._check_existing_session()
    extension._username_model.set_value("user@example.com")
    extension._credential_model.set_value("vams_typed")
    extension._on_login_token()
    connector = extension._connector
    extension.on_shutdown()
    assert connector.logouts == 1


def test_the_cli_service_reads_the_profile_on_every_call(monkeypatch):
    """The refresh relies on this: the service holds no signed-out answer from an earlier call."""
    monkeypatch.setattr(service_module.VamsCliService, "_verify_cli_installed", lambda self: None)
    service = service_module.VamsCliService(profile="edh-i-0123456789abcdef0")
    profile = {"signed_in": False}

    def _execute(args, stdin_payload=None):
        if args[:2] == ["auth", "status"]:
            return json.dumps({"authenticated": profile["signed_in"], "user_id": "user@example.com"})
        if args[:2] == ["database", "list"]:
            return json.dumps({"Items": [{"databaseId": "db1"}]})
        return "{}"

    monkeypatch.setattr(service, "_execute_command", _execute)
    with pytest.raises(VamsAuthenticationError):
        service.list_databases()
    profile["signed_in"] = True
    assert [db.database_id for db in service.list_databases()] == ["db1"]


def test_a_refused_desktop_session_says_how_to_recover(extension):
    """The status line is a single line that clips a long message, so the recovery clause comes
    first and the CLI's own error follows it."""
    extension._connector.signed_in = True
    extension._connector.list_databases = _raise(VamsAccessDeniedError(_FORBIDDEN))
    extension._check_existing_session()
    assert extension._status_label.text == f"  Error: {_HINT} ({_FORBIDDEN})"


def test_a_refused_desktop_session_says_how_to_recover_from_a_file_action(extension):
    extension._connector.signed_in = True
    extension._check_existing_session()
    extension._selected_db = Database(database_id="db1")
    extension._selected_asset = Asset(asset_id="a1", asset_name="house")
    extension._connector.fetch_local_copy = _raise(
        VamsAccessDeniedError("Permission Denied: Access forbidden."))
    extension._on_load_file(AssetFile(relative_path="/house.usda"))
    assert extension._status_label.text == (
        f"  Load error: {_HINT} (Permission Denied: Access forbidden.)")


def test_a_refused_desktop_session_says_how_to_recover_from_download_only(extension):
    extension._connector.signed_in = True
    extension._check_existing_session()
    extension._selected_db = Database(database_id="db1")
    extension._selected_asset = Asset(asset_id="a1", asset_name="house")
    extension._connector.download_file = _raise(
        VamsAccessDeniedError("Permission Denied: Access forbidden."))
    extension._on_download_file(AssetFile(relative_path="/house/textures/color.jpg"))
    assert extension._status_label.text == (
        f"  Download failed: {_HINT} (Permission Denied: Access forbidden.)")


def test_the_recovery_hint_covers_an_expired_sign_in():
    """The desktop's session key expires; a renewed lease re-issues it, and a sign-in under
    Authentication replaces it."""
    hint = extension_module._SIGN_IN_PENDING
    assert "If the sign-in has expired" in hint
    assert "renew the workstation's lease in VAMS" in hint
    assert "sign in under Authentication" in hint


def test_any_other_download_only_failure_says_what_failed(extension):
    """Control: a failed transfer is reported as the CLI worded it, with no sign-in hint."""
    extension._connector.signed_in = True
    extension._check_existing_session()
    extension._selected_db = Database(database_id="db1")
    extension._selected_asset = Asset(asset_id="a1", asset_name="house")
    failure = "Transfer failed: 1 of 1 file(s) failed (/house/textures/color.jpg)"
    extension._connector.download_file = _raise(VamsCliError(failure))
    extension._on_download_file(AssetFile(relative_path="/house/textures/color.jpg"))
    assert extension._status_label.text == f"  Download failed: {failure}"


def test_a_refusal_of_a_session_signed_in_through_the_extension_carries_no_hint(extension):
    """Control: that credential was typed in, so its refusal reads as the CLI reported it."""
    extension._username_model.set_value("user@example.com")
    extension._credential_model.set_value("vams_typed")
    extension._on_login_token()
    extension._connector.list_databases = _raise(VamsAccessDeniedError(_FORBIDDEN))
    extension._load_databases()
    assert extension._status_label.text == f"  Error: {_FORBIDDEN}"


def test_any_other_error_on_a_desktop_session_carries_no_hint(extension):
    """Control: only a refused credential points at the sign-in."""
    extension._connector.signed_in = True
    extension._check_existing_session()
    failure = "API Error: Failed to list databases: 500 Server Error"
    extension._connector.list_databases = _raise(VamsCliError(failure))
    extension._load_databases()
    assert extension._status_label.text == f"  Error: {failure}"
