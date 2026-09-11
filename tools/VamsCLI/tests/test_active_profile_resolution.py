"""A command with no --profile runs against the ACTIVE profile.

`vamscli profile switch <name>` writes active_profile.json, but every command that did not receive an
explicit --profile resolved to the literal default profile instead. The switch was therefore a no-op:
commands read the default profile's credentials and API URL and ran against whatever deployment that
pointed at, while reporting success. On a machine with profiles for two deployments this silently
mixed them — `auth login` wrote its token into the default profile and subsequent reads returned the
other environment's data, which is indistinguishable from "the data is missing".

Three fallbacks had to change together: the --profile Click option (which declared a default, so the
callback could never tell "omitted" from "explicitly default"), the context helper every command
calls, and APIClient's own default ProfileManager.
"""

import json
from unittest.mock import patch

import pytest

from vamscli.constants import DEFAULT_PROFILE_NAME
from vamscli.utils.profile import read_active_profile_name


@pytest.fixture
def active_profile_file(tmp_path):
    """Redirect the config dir at every read site so the marker file is this test's own."""
    def _write(payload):
        marker = tmp_path / "active_profile.json"
        if payload is not None:
            marker.write_text(json.dumps(payload) if isinstance(payload, dict) else payload)
        return tmp_path
    return _write


class TestReadActiveProfileName:
    def test_returns_the_switched_profile(self, active_profile_file):
        base = active_profile_file({"active_profile": "prod5"})
        with patch("vamscli.utils.profile.get_config_dir", return_value=base):
            assert read_active_profile_name() == "prod5"

    def test_missing_marker_falls_back_to_default(self, active_profile_file):
        base = active_profile_file(None)
        with patch("vamscli.utils.profile.get_config_dir", return_value=base):
            assert read_active_profile_name() == DEFAULT_PROFILE_NAME

    @pytest.mark.parametrize("payload", [
        "{not json",                       # truncated / mid-write
        {"active_profile": ""},            # blank name
        {"active_profile": None},          # null name
        {"something_else": "prod5"},       # key absent
    ])
    def test_unusable_marker_falls_back_to_default(self, active_profile_file, payload):
        base = active_profile_file(payload)
        with patch("vamscli.utils.profile.get_config_dir", return_value=base):
            assert read_active_profile_name() == DEFAULT_PROFILE_NAME

    def test_an_unreadable_marker_never_raises(self, active_profile_file):
        """This runs on EVERY invocation, so it must not be able to abort a command.

        A test that patches builtins.open for its own purposes previously made this read return a
        MagicMock and failed three unrelated GLB tests.
        """
        base = active_profile_file({"active_profile": "prod5"})
        with patch("vamscli.utils.profile.get_config_dir", return_value=base):
            with patch("builtins.open", side_effect=OSError("device busy")):
                assert read_active_profile_name() == DEFAULT_PROFILE_NAME
            with patch("builtins.open", create=True):     # returns a MagicMock, as a test double would
                assert read_active_profile_name() == DEFAULT_PROFILE_NAME


class TestProfileResolutionWiring:
    """The three fallback sites resolve the active profile rather than the literal default."""

    def test_the_profile_option_declares_no_default(self):
        """A Click default would hide "omitted" from the callback, re-breaking `profile switch`."""
        from vamscli.main import cli

        option = next(p for p in cli.params if "--profile" in getattr(p, "opts", []))
        assert option.default is None, (
            "--profile declares a Click default again; the callback can no longer tell an omitted "
            "flag from an explicit default, so `profile switch` is ignored"
        )

    def test_an_omitted_profile_resolves_to_the_active_one(self):
        from vamscli.main import handle_profile_option

        class Ctx:
            obj = None

        ctx = Ctx()
        with patch("vamscli.utils.profile.read_active_profile_name", return_value="prod5"):
            assert handle_profile_option(ctx, None, None) == "prod5"
        assert ctx.obj["profile_name"] == "prod5"

    def test_an_explicit_profile_still_wins(self):
        from vamscli.main import handle_profile_option

        class Ctx:
            obj = None

        ctx = Ctx()
        with patch("vamscli.utils.profile.read_active_profile_name", return_value="prod5"):
            assert handle_profile_option(ctx, None, "otherprofile") == "otherprofile"
        assert ctx.obj["profile_name"] == "otherprofile"

    def test_the_context_helper_uses_the_active_profile_when_none_requested(self):
        from vamscli.utils import decorators

        with patch.object(decorators, "read_active_profile_name", return_value="prod5"):
            manager = decorators.get_profile_manager_from_context(None)
        assert manager.profile_name == "prod5"

    def test_the_context_helper_prefers_an_explicit_request(self):
        from vamscli.utils import decorators

        class Ctx:
            obj = {"profile_name": "otherprofile"}

        with patch.object(decorators, "read_active_profile_name", return_value="prod5"):
            manager = decorators.get_profile_manager_from_context(Ctx())
        assert manager.profile_name == "otherprofile"

    def test_api_client_defaults_to_the_active_profile(self):
        """A bare APIClient would otherwise read the default profile's token and base URL."""
        from vamscli.utils import api_client as api_client_module

        with patch.object(api_client_module, "read_active_profile_name", return_value="prod5"):
            client = api_client_module.APIClient("https://example.invalid/api")
        assert client.profile_manager.profile_name == "prod5"


class TestTheProfileFlagContractTheConnectorsDependOn:
    """The external connectors pass `--profile` on every invocation; this pins what they rely on.

    Neither the Isaac Sim nor the ArcGIS Pro connector imports this package — they build an argument
    vector and spawn `vamscli`. Nothing catches a change to this contract at build or import time.
    """

    def test_an_explicit_profile_wins_and_an_omitted_one_follows_the_marker(self):
        """Both directions in one test, so an inverted precedence cannot pass either half.

        Split across two tests, a resolution that ignored the flag entirely would still satisfy the
        "omitted follows the marker" half, and a resolution that ignored the marker entirely would
        still satisfy the "explicit wins" half.
        """
        from vamscli.main import cli

        with patch("vamscli.utils.profile.read_active_profile_name", return_value="switchedto"):
            explicit = cli.make_context("vamscli", ["--profile", "namedprof", "database", "list"])
            omitted = cli.make_context("vamscli", ["database", "list"])

        assert explicit.obj["profile_name"] == "namedprof", (
            "an explicit --profile no longer wins; a connector configured for one deployment would "
            "run against whichever one `profile switch` last selected"
        )
        assert omitted.obj["profile_name"] == "switchedto", (
            "an omitted --profile no longer follows the active-profile marker, so `profile switch` "
            "is a no-op again"
        )

    def test_the_flag_is_rejected_after_the_subcommand(self, cli_runner, generic_command_mocks):
        """`--profile` is a group-level option, which is why the connectors must PREPEND it.

        A connector fix that appended the flag would satisfy an argv-membership assertion and then
        fail at runtime here. Recorded on the CLI side because that is where the rejection lives.
        """
        from vamscli.main import cli

        with generic_command_mocks("database") as mocks:
            mocks["api_client"].list_databases.return_value = {"Items": []}

            prepended = cli_runner.invoke(cli, ["--profile", "namedprof", "database", "list"])
            appended = cli_runner.invoke(cli, ["database", "list", "--profile", "namedprof"])

        assert prepended.exit_code == 0, prepended.output
        assert appended.exit_code != 0
        assert "No such option" in appended.output and "--profile" in appended.output
