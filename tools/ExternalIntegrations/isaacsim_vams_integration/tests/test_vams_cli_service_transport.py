"""Tests for the transport layer of the Isaac Sim connector's CLI wrapper.

The shared ``cli_service`` fixture in ``conftest.py`` replaces ``_execute_command`` itself, so every
assertion in ``test_vams_cli_service.py`` covers argument construction and JSON key mapping while
skipping the layer underneath: the exit-code handling, the error-document parsing, the timeout and
missing-executable branches, and ``_parse_json``'s failure. That layer is where a CLI behaviour change
lands, and none of it is observable through the shared fixture. These tests patch ``subprocess.run``
instead, so the real ``_execute_command`` runs.

The behaviour most worth pinning here is the transfer exit code. ``file upload``, ``assets download``
and ``sync file push``/``pull`` write their full report to stdout and THEN exit 1 whenever
``overall_success`` is false, so a 900-of-1000-file download is reported as a failure whose payload
still names the 100 files that failed. A transport that discards stdout on a non-zero exit turns that
into an undiagnosable total failure — and under ``--json-output`` stderr is empty, so the message
would carry nothing at all.
"""

import json
import subprocess

import pytest

from vams.connector.isaacsim import vams_cli_service as module
from vams.connector.isaacsim.vams_cli_service import (
    VamsCliError,
    VamsNotInstalledError,
    VamsProfileNotSetupError,
    VamsTransferError,
)


class _CompletedProcess:
    def __init__(self, returncode=0, stdout="{}", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Recorder:
    """A ``subprocess.run`` stand-in that returns queued results and can raise instead."""

    def __init__(self):
        self.argvs = []
        self.results = []
        self.raises = None

    def run(self, cmd, **_kwargs):
        self.argvs.append(list(cmd))
        if self.raises is not None:
            raise self.raises
        return self.results.pop(0) if self.results else _CompletedProcess()

    @property
    def calls(self):
        return len(self.argvs)


@pytest.fixture
def transport(monkeypatch):
    """A service with the real ``_execute_command`` over a stubbed ``subprocess.run``."""
    monkeypatch.setattr(module.VamsCliService, "_verify_cli_installed", lambda self: None)
    recorder = _Recorder()
    monkeypatch.setattr(module.subprocess, "run", recorder.run)
    service = module.VamsCliService()
    monkeypatch.setattr(service, "ensure_authenticated", lambda: None)
    recorder.service = service
    return recorder


def _download_report(overall_success=False, total=1000, successful=900, failed=100, named=3):
    return json.dumps({
        "overall_success": overall_success,
        "total_files": total,
        "successful_files": successful,
        "failed_files": failed,
        "total_size": 123,
        "total_size_formatted": "123 B",
        "successful_downloads": [],
        "failed_downloads": [
            {"relative_key": f"/models/f{i}.glb", "local_path": f"/tmp/f{i}.glb",
             "error": "403 Forbidden"}
            for i in range(named)
        ],
    })


class TestSuccessPathIsUnchanged:
    """Controls. Without these, every "raises" assertion below could be satisfied by a transport
    that fails on everything."""

    def test_a_zero_exit_returns_stdout(self, transport):
        transport.results = [_CompletedProcess(stdout='{"Items": []}')]
        assert transport.service.list_databases() == []
        assert transport.calls == 1

    def test_a_successful_transfer_is_not_treated_as_a_failure(self, transport, tmp_path):
        transport.results = [_CompletedProcess(stdout=_download_report(
            overall_success=True, failed=0, named=0))]
        result = transport.service.download_asset(str(tmp_path / "out"), "db", "a1")
        assert result.overall_success is True
        assert result.successful_files == 900


class TestTransferFailuresKeepTheirReport:
    """A non-zero exit that still carries a report must not be flattened into "exit 1: ""."""

    def test_a_partial_download_names_the_failed_files(self, transport):
        transport.results = [_CompletedProcess(returncode=1, stdout=_download_report(), stderr="")]
        with pytest.raises(VamsTransferError) as excinfo:
            transport.service._execute_command(["assets", "download", "x", "--json-output"])
        message = str(excinfo.value)
        assert "900 of 1000" in message
        assert "/models/f0.glb" in message
        assert excinfo.value.payload["failed_files"] == 100
        assert excinfo.value.exit_code == 1

    def test_download_asset_reports_a_partial_download_rather_than_raising(self, transport, tmp_path):
        """`DownloadResult` already expresses a partial outcome, and "which files did I get" is a
        more useful answer than an exception for a recursive download."""
        transport.results = [_CompletedProcess(returncode=1, stdout=_download_report())]
        result = transport.service.download_asset(str(tmp_path / "out"), "db", "a1")
        assert result.overall_success is False
        assert (result.total_files, result.successful_files, result.failed_files) == (1000, 900, 100)
        assert [f["relative_key"] for f in result.failed_downloads] == [
            "/models/f0.glb", "/models/f1.glb", "/models/f2.glb"]

    def test_a_partial_upload_raises_and_names_the_failed_keys(self, transport, tmp_path):
        """`create_and_upload` and `export_and_upload_scene` both discard the upload payload, so a
        swallowed partial upload would be reported to the user as a completed export."""
        source = tmp_path / "scene.usd"
        source.write_text("usd")
        transport.results = [_CompletedProcess(returncode=1, stdout=json.dumps({
            "overall_success": False,
            "total_files": 3,
            "successful_files": 2,
            "failed_files": 1,
            "sequence_results": [{"sequence_number": 1, "successful_files": 2,
                                  "failed_files": ["/textures/big.png"]}],
        }))]
        with pytest.raises(VamsTransferError) as excinfo:
            transport.service.upload_file(str(source), "db", "a1")
        assert "/textures/big.png" in str(excinfo.value)
        assert "2 of 3" in str(excinfo.value)

    def test_a_total_failure_is_worded_as_such(self, transport):
        transport.results = [_CompletedProcess(returncode=1, stdout=_download_report(
            total=2, successful=0, failed=2, named=2))]
        with pytest.raises(VamsTransferError) as excinfo:
            transport.service._execute_command(["assets", "download", "x", "--json-output"])
        assert "Transfer failed: 2 of 2" in str(excinfo.value)

    def test_a_single_file_download_failure_becomes_false_at_the_connector(self, transport,
                                                                          tmp_path, monkeypatch):
        """`IsaacVAMSConnector.download_file` maps any VamsCliError to False; the raised message is
        what reaches the Kit log, so it has to say which file failed and why."""
        from vams.connector.isaacsim.connector import IsaacVAMSConnector

        connector = IsaacVAMSConnector()
        monkeypatch.setattr(connector._cli, "ensure_authenticated", lambda: None)
        transport.results = [_CompletedProcess(returncode=1, stdout=json.dumps({
            "overall_success": False, "total_files": 1, "successful_files": 0, "failed_files": 1,
            "failed_downloads": [{"relative_key": "/a.glb", "error": "404 Not Found"}],
        }))]
        assert connector.download_file("db", "a1", "/a.glb", str(tmp_path / "out")) is False


class TestErrorDocumentsAndFailureBranches:
    """Every non-success path through ``_execute_command`` and ``_parse_json``."""

    def test_a_non_zero_exit_with_an_error_document(self, transport):
        transport.results = [_CompletedProcess(returncode=1, stdout=json.dumps({
            "error": "Asset 'a1' not found", "error_type": "Resource Not Found"}))]
        with pytest.raises(VamsCliError) as excinfo:
            transport.service._execute_command(["assets", "get", "a1", "--json-output"])
        assert str(excinfo.value) == "Asset 'a1' not found"
        assert excinfo.value.error_type == "Resource Not Found"

    def test_a_non_zero_exit_with_only_stderr(self, transport):
        transport.results = [_CompletedProcess(returncode=2, stdout="", stderr="no such option\n")]
        with pytest.raises(VamsCliError) as excinfo:
            transport.service._execute_command(["database", "list", "--nope"])
        assert "exit 2" in str(excinfo.value)
        assert "no such option" in str(excinfo.value)

    def test_an_error_field_in_a_zero_exit_response(self, transport):
        transport.results = [_CompletedProcess(stdout=json.dumps({
            "error": "Forbidden", "error_type": "Authorization Error"}))]
        with pytest.raises(VamsCliError) as excinfo:
            transport.service._execute_command(["database", "list", "--json-output"])
        assert str(excinfo.value) == "Forbidden"

    def test_a_cancelled_run_reports_the_cancellation(self, transport):
        """Under ``--json-output`` a cancelled command now writes a document where it previously
        wrote nothing, so this reaches the user as the reason rather than as a JSON parse failure."""
        transport.results = [_CompletedProcess(returncode=1, stdout=json.dumps({
            "error": "Operation cancelled by user", "error_type": "Aborted"}))]
        with pytest.raises(VamsCliError) as excinfo:
            transport.service._execute_command(["execution", "abort", "e1", "--json-output"])
        assert str(excinfo.value) == "Operation cancelled by user"
        assert excinfo.value.error_type == "Aborted"

    def test_a_timeout_is_reported(self, transport):
        transport.raises = subprocess.TimeoutExpired(["vamscli"], 600)
        with pytest.raises(VamsCliError) as excinfo:
            transport.service._execute_command(["database", "list", "--json-output"])
        assert "timed out" in str(excinfo.value)

    def test_a_missing_executable_at_spawn_time(self, transport):
        """``_verify_cli_installed`` runs once at construction; the executable can still disappear,
        or be shadowed by a directory entry that is not executable."""
        transport.raises = FileNotFoundError(2, "No such file or directory")
        with pytest.raises(VamsNotInstalledError) as excinfo:
            transport.service._execute_command(["database", "list", "--json-output"])
        assert "pip install vamscli" in str(excinfo.value)

    def test_a_missing_executable_at_construction(self, monkeypatch):
        monkeypatch.setattr(module.shutil, "which", lambda _name: None)
        with pytest.raises(VamsNotInstalledError) as excinfo:
            module.VamsCliService(vamscli_path="definitely-not-on-path-vamscli")
        assert "pip install vamscli" in str(excinfo.value)

    def test_non_json_stdout_is_reported_with_the_output(self, transport):
        transport.results = [_CompletedProcess(stdout="Retrieving files...\n")]
        with pytest.raises(VamsCliError) as excinfo:
            transport.service.list_databases()
        assert "Failed to parse CLI JSON output" in str(excinfo.value)
        assert "Retrieving files" in str(excinfo.value)


class TestProfileLookupFailureIsNamed:
    """`profile info` on a MISSING profile exits 1 with ``{"error", "error_type"}`` and no
    ``profile_info`` key. Reaching for ``profile_info`` on a zero exit would have defaulted the auth
    type to "Cognito" and sent a password to a deployment configured for external OIDC."""

    def test_a_missing_profile_raises_and_names_the_profile(self, monkeypatch):
        monkeypatch.setattr(module.VamsCliService, "_verify_cli_installed", lambda self: None)
        recorder = _Recorder()
        monkeypatch.setattr(module.subprocess, "run", recorder.run)
        recorder.results = [_CompletedProcess(returncode=1, stdout=json.dumps({
            "error": "Profile 'prod' does not exist", "error_type": "ProfileNotFoundError"}))]
        service = module.VamsCliService(profile="prod")
        with pytest.raises(VamsProfileNotSetupError) as excinfo:
            service.get_auth_type()
        assert "'prod'" in str(excinfo.value)
        assert service._cached_auth_type is None

    def test_an_existing_profile_resolves_and_caches(self, transport):
        """Control: the happy path is byte-identical to before, and is only fetched once."""
        transport.results = [_CompletedProcess(stdout=json.dumps({
            "profile_name": "default", "exists": True,
            "profile_info": {"auth_type": "External",
                             "web_deployed_url": "https://vams.example.invalid"}}))]
        assert transport.service.get_auth_type() == "External"
        assert transport.service.get_auth_type() == "External"
        assert transport.calls == 1
        assert transport.service.web_deployed_url == "https://vams.example.invalid"


class TestAuthGateFailureModes:
    """`check_authentication` and `logout` deliberately absorb a failure; `ensure_authenticated`
    must not."""

    def test_check_authentication_absorbs_a_failure(self, transport):
        transport.results = [_CompletedProcess(returncode=1, stdout="", stderr="boom")]
        status = transport.service.check_authentication()
        assert status.authenticated is False
        assert status.is_expired is True

    def test_a_token_without_an_expiry_is_not_treated_as_expired(self, transport):
        transport.results = [_CompletedProcess(stdout=json.dumps({
            "authenticated": True, "user_id": "u", "authentication_type": "token_override"}))]
        assert transport.service.is_authenticated() is True

    def test_ensure_authenticated_raises_without_cached_credentials(self, transport):
        # A second service off the same stubs: the fixture's service has the auth gate stubbed out,
        # so it cannot exercise the gate itself.
        service = module.VamsCliService()
        transport.results = [_CompletedProcess(stdout=json.dumps({"authenticated": False}))]
        with pytest.raises(VamsCliError) as excinfo:
            service.ensure_authenticated()
        assert "login" in str(excinfo.value).lower()

    def test_logout_absorbs_a_failure_and_clears_the_cache(self, transport):
        transport.service._cached_username = "u"
        transport.service._cached_credential = "pw"
        transport.results = [_CompletedProcess(returncode=1, stdout="", stderr="boom")]
        transport.service.logout()
        assert transport.service._cached_username is None
        assert transport.service._cached_credential is None
