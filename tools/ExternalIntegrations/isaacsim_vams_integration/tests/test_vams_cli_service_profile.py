"""Every vamscli invocation must name the connector's configured profile.

`vamscli`'s global `--profile` option declares no Click default, so an omitted flag resolves to the
profile recorded in ``active_profile.json`` by `vamscli profile switch`. A connector that emitted the
flag only when its configured profile differed from the literal "default" would, for a session
configured for the default profile, silently follow whatever deployment the user last switched to —
an Isaac Sim stage populated from, or written to, a different environment than the extension is set
to.

These tests patch ``subprocess.run`` rather than ``_execute_command``. The shared ``cli_service``
fixture in ``conftest.py`` replaces ``_execute_command`` itself, so every ``service.commands``
assertion in ``test_vams_cli_service.py`` records only the tokens AFTER the
``[vamscli, --profile, X]`` prefix is assembled, so no assertion there can see the prefix at all.
It is only observable at the process boundary.

Guards FIX-027 (S6C-CONNECTORS-001): a connector that omits ``--profile`` follows whatever
``vamscli profile switch`` last selected rather than its own configured profile.
"""

import json

import pytest

from vams.connector.isaacsim import vams_cli_service as module


class _CompletedProcess:
    """Minimal stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, stdout: str):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


class _Recorder:
    """Records the full argv of every ``subprocess.run`` call the connector makes."""

    def __init__(self):
        self.argvs = []
        self.kwargs = []
        self.responses = []

    def run(self, cmd, **kwargs):
        self.argvs.append(list(cmd))
        self.kwargs.append(kwargs)
        return _CompletedProcess(self.responses.pop(0) if self.responses else "{}")

    @property
    def last(self):
        return self.argvs[-1]


@pytest.fixture
def recorder(monkeypatch):
    """A ``subprocess.run`` recorder plus a factory for services wired to it.

    ``make(profile=...)`` returns a ``VamsCliService`` with the executable check and the auth gate
    bypassed but the REAL ``_execute_command``, so the argv it builds reaches the recorder intact.
    """
    monkeypatch.setattr(module.VamsCliService, "_verify_cli_installed", lambda self: None)
    rec = _Recorder()
    monkeypatch.setattr(module.subprocess, "run", rec.run)

    def make(profile=None):
        service = (module.VamsCliService() if profile is None
                   else module.VamsCliService(profile=profile))
        monkeypatch.setattr(service, "ensure_authenticated", lambda: None)
        return service

    rec.make = make
    return rec


def _drive_every_method(service, tmp_path):
    """Call every public VamsCliService method that shells out, once each.

    Returns the list of method names in the order they were invoked, so an argv list can be
    attributed to the call that produced it.
    """
    upload_source = tmp_path / "scene.usd"
    upload_source.write_text("usd")
    upload_dir = tmp_path / "stage"
    upload_dir.mkdir()
    download_dir = str(tmp_path / "out")

    calls = [
        ("get_auth_type", lambda: service.get_auth_type()),
        ("check_authentication", lambda: service.check_authentication()),
        ("logout", lambda: service.logout()),
        ("login_with_token", lambda: service.login_with_token("u", "tok")),
        ("list_databases", lambda: service.list_databases()),
        ("get_database", lambda: service.get_database("db")),
        ("list_assets", lambda: service.list_assets("db")),
        ("get_asset", lambda: service.get_asset("db", "a1")),
        ("create_asset", lambda: service.create_asset("db", "Asset One")),
        ("list_files", lambda: service.list_files("db", "a1")),
        ("get_file_info", lambda: service.get_file_info("db", "a1", "/a.glb")),
        ("download_file", lambda: service.download_file(download_dir, "db", "a1", "/a.glb")),
        ("download_asset", lambda: service.download_asset(download_dir, "db", "a1")),
        ("upload_file", lambda: service.upload_file(str(upload_source), "db", "a1")),
        ("upload_directory", lambda: service.upload_directory(str(upload_dir), "db", "a1")),
        ("list_workflows", lambda: service.list_workflows("db")),
        ("list_workflow_executions",
         lambda: service.list_workflow_executions("db", "a1", workflow_id="wf")),
        ("execute_workflow",
         lambda: service.execute_workflow("db", "a1", "wf", "GLOBAL")),
    ]

    names = []
    for name, call in calls:
        call()
        names.append(name)

    # login() is driven separately: it needs a success payload, and a missing web URL would make it
    # issue a second `auth status` call.
    service._cached_auth_type = "Cognito"
    return names


@pytest.fixture
def login_response():
    return json.dumps({"success": True, "web_deployed_url": "https://vams.example.invalid"})


class TestProfileFlagIsAlwaysPassed:
    """A connector operation must run against the connector's profile, not the switched-to one."""

    def test_the_default_profile_is_named_explicitly(self, recorder):
        service = recorder.make()
        service.check_authentication()
        assert recorder.last == [
            "vamscli", "--profile", "default", "auth", "status", "--json-output",
        ]

    def test_a_non_default_profile_is_named(self, recorder):
        """Control: the recorder really does observe the prefix, on a profile that is not the default.

        Without this, "the profile is named" could be satisfied by a recorder that never fired.
        """
        service = recorder.make(profile="prod")
        service.check_authentication()
        assert recorder.last == [
            "vamscli", "--profile", "prod", "auth", "status", "--json-output",
        ]

    @pytest.mark.parametrize("subcommand, drive", [
        ("auth", lambda service, tmp_path: service.check_authentication()),
        ("database", lambda service, tmp_path: service.list_databases()),
        ("assets", lambda service, tmp_path: service.download_asset(
            str(tmp_path / "out"), "db", "a1")),
        ("file", lambda service, tmp_path: service.list_files("db", "a1")),
        ("workflow", lambda service, tmp_path: service.list_workflow_executions("db", "a1")),
        ("profile", lambda service, tmp_path: service.get_auth_type()),
    ])
    def test_the_flag_precedes_the_subcommand(self, recorder, tmp_path, subcommand, drive):
        """`--profile` is a GROUP-level option: appended after the subcommand Click rejects it.

        An argv-membership assertion alone would pass on a fix that appended the flag, which then
        fails at runtime with Click's "no such option".
        """
        service = recorder.make()
        drive(service, tmp_path)
        argv = recorder.last
        assert "--profile" in argv, f"no --profile in {argv}"
        assert argv.index(subcommand) > 0, f"{subcommand} not in {argv}"
        assert argv.index("--profile") < argv.index(subcommand), (
            f"--profile must precede the subcommand, got {argv}"
        )

    def test_every_operation_names_the_profile_exactly_once(self, recorder, tmp_path):
        """Catches a prefix applied to only some call paths, and a double-prepend."""
        service = recorder.make()
        names = _drive_every_method(service, tmp_path)
        assert len(recorder.argvs) >= len(names)

        for name, argv in zip(names, recorder.argvs):
            assert argv[:3] == ["vamscli", "--profile", "default"], f"{name}: {argv}"
            assert argv.count("--profile") == 1, f"{name} passed --profile twice: {argv}"

    def test_every_operation_names_a_configured_profile_exactly_once(self, recorder, tmp_path):
        """The same sweep for a profile that is not the default, so neither branch can regress."""
        service = recorder.make(profile="prod")
        names = _drive_every_method(service, tmp_path)

        for name, argv in zip(names, recorder.argvs):
            assert argv[:3] == ["vamscli", "--profile", "prod"], f"{name}: {argv}"
            assert argv.count("--profile") == 1, f"{name} passed --profile twice: {argv}"

    def test_login_names_the_profile(self, recorder, login_response):
        """`auth login` writes credentials into a profile directory; the wrong one 401s later."""
        service = recorder.make()
        recorder.responses = [login_response]
        service.login("user@example.invalid", "pw")
        assert recorder.last[:3] == ["vamscli", "--profile", "default"]

    def test_login_names_a_configured_profile(self, recorder, login_response):
        """Control for the login path on a profile that is not the default."""
        service = recorder.make(profile="prod")
        recorder.responses = [login_response]
        service.login("user@example.invalid", "pw")
        assert recorder.last[:3] == ["vamscli", "--profile", "prod"]


class TestProfileInfoLookupTargetsTheConfiguredProfile:
    """`profile info` caches the auth type and the web URL; it must read the profile in use."""

    def test_profile_info_uses_the_configured_profile_name(self, recorder):
        """The positional profile name has to track the configured one too, not just ``--profile``.

        ArcGIS's ``GetAuthTypeAsync`` runs the same lookup. Adding ``--profile`` there while leaving
        the positional at the literal 'default' would resolve the auth type and web URL from a
        different profile than operations use. There is no C# test project, so this records the
        contract on the side that can be tested.
        """
        service = recorder.make(profile="prod")
        service.get_auth_type()
        argv = recorder.last
        assert argv[-4:] == ["profile", "info", "prod", "--json-output"]
        assert "default" not in argv
