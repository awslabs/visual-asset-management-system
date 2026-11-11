"""Tests for how the Isaac Sim connector's CLI wrapper recognises a credential VAMS refused.

The CLI exits 1 for an HTTP 401 or 403 exactly as for any other failure, so a refusal is told apart
by the error document it prints. A workstation's desktop session can hold a key VAMS revoked when the
desktop last stopped, and the extension says how to recover only when it recognises that case.
"""

import ast
import json
import re
from pathlib import Path

import pytest

from vams.connector.isaacsim import vams_cli_service as module
from vams.connector.isaacsim.vams_cli_service import VamsAccessDeniedError, VamsCliError

VAMSCLI_PACKAGE = Path(__file__).resolve().parents[3] / "VamsCLI" / "vamscli"
ARCGIS_CLI_SERVICE = (Path(__file__).resolve().parents[2] / "arcgispro-connector-for-vams" / "Services"
                      / "VamsCliService.cs")

# The texts the CLI prints for a request the deployment's AWS WAF web ACL blocked
# (`_WAF_BLOCKED_MESSAGE` and `_WAF_OR_STAGE_MESSAGE` in vamscli/utils/api_client.py, and
# `WAF_ADMIN_HINT` in vamscli/constants.py); the wording guard below reads the CLI's own copies.
_WAF_BLOCKED = (
    "The deployment's AWS WAF web ACL blocked this request before it reached VAMS (HTTP 403). A managed "
    "rule matched something in the request, most often a value in the request body. Signing in again "
    "does not change the result.")
_WAF_OR_STAGE = (
    "Amazon API Gateway refused this request with a bare 403 \"Forbidden\". The deployment's AWS WAF web "
    "ACL answers a blocked request this way, and so does a base URL that names no deployed stage "
    "(see 'vamscli profile info'). Signing in again does not change the result.")
_WAF_ADMIN_HINT = (
    "Ask the VAMS administrator to find the request in the web ACL's sampled requests, or in its "
    "aws-waf-logs-vams-* log group, which names the rule that matched.")

# The ArcGIS Pro connector re-prompts a sign-in when a CLI error contains one of these
# (`catch (InvalidOperationException ex) when (ex.Message.Contains(...))` in VamsCliService.cs).
_ARCGIS_SIGN_IN_TRIGGERS = ("401", "token has expired")


class _CompletedProcess:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


@pytest.fixture
def cli_error(monkeypatch):
    """Runs the real ``_execute_command`` over a CLI that prints ``document`` and returns the
    exception it raised."""
    monkeypatch.setattr(module.VamsCliService, "_verify_cli_installed", lambda self: None)
    service = module.VamsCliService()
    outputs = []
    monkeypatch.setattr(module.subprocess, "run", lambda cmd, **kwargs: outputs.pop(0))

    def _run(document, returncode=1):
        outputs.append(_CompletedProcess(returncode, json.dumps(document)))
        with pytest.raises(VamsCliError) as excinfo:
            service._execute_command(["database", "list", "--json-output"])
        return excinfo.value

    return _run


@pytest.mark.parametrize("document", [
    # A 403 a command wrapped in its own message; the live desktop showed this one.
    {"error": "API Error: Failed to list databases: Access forbidden. You do not have permission to "
              "perform this action. Contact your administrator if you believe this is an error."},
    {"error": "Permission Denied: Access forbidden. You do not have permission to perform this action."},
    {"error": "Authentication Error: Authentication failed: 401 Client Error: Unauthorized"},
    {"error": "Override Token Error: Override token authentication failed. The token may be invalid "
              "or expired."},
    {"error": "Token Expired: Authentication token has expired."},
    {"error": "Access forbidden. You do not have permission to perform this action.",
     "error_type": "PermissionDeniedError"},
    {"error": "Token has expired", "error_type": "TokenExpiredError"},
    # A role or entity refusal by a VAMS handler, as the CLI's global handler prints it.
    {"error": "Permission Denied: Access forbidden. Not Authorized", "error_type": "PermissionDeniedError"},
])
def test_a_refused_credential_is_recognised(cli_error, document):
    error = cli_error(document)
    assert isinstance(error, VamsAccessDeniedError)
    assert str(error) == document["error"]
    assert error.exit_code == 1


def test_a_refusal_in_a_zero_exit_response_is_recognised(cli_error):
    assert isinstance(cli_error({"error": "Permission Denied: Access forbidden."}, returncode=0),
                      VamsAccessDeniedError)


@pytest.mark.parametrize("document", [
    {"error": "Asset 'a1' not found", "error_type": "Resource Not Found"},
    {"error": "API Error: Failed to list databases: 500 Server Error: Internal Server Error"},
    {"error": "Operation cancelled by user", "error_type": "Aborted"},
    {"error": "Setup Required: Setup required for profile 'edh-i-0123456789abcdef0'."},
])
def test_any_other_error_is_not_a_refusal(cli_error, document):
    assert type(cli_error(document)) is VamsCliError


@pytest.mark.parametrize("document", [
    {"error": f"Request Blocked: {_WAF_BLOCKED}", "error_type": "RequestBlockedError", "blockedBy": "waf",
     "requestId": "r-1", "additional_info": [f"{_WAF_ADMIN_HINT} Request ID: r-1."]},
    {"error": f"Request Blocked: {_WAF_OR_STAGE}", "error_type": "RequestBlockedError",
     "blockedBy": "waf-or-stage", "additional_info": [_WAF_ADMIN_HINT]},
    # The heading form, from a CLI that prints no error type.
    {"error": f"Request Blocked: {_WAF_BLOCKED}"},
    {"error": f"Request Blocked: {_WAF_OR_STAGE}"},
], ids=["json-blocked", "json-blocked-or-no-stage", "heading-blocked", "heading-blocked-or-no-stage"])
def test_a_request_the_web_acl_blocked_is_not_a_refused_credential(cli_error, document):
    """Signing in again does not change a block, so the extension must not read it as a sign-in that
    is pending."""
    error = cli_error(document)
    assert type(error) is VamsCliError
    assert str(error) == document["error"]
    assert error.exit_code == 1


@pytest.mark.skipif(not VAMSCLI_PACKAGE.is_dir(), reason="the VAMS CLI sources are not beside the connector")
def test_the_recognised_text_is_what_the_cli_prints():
    """The connector does not import the CLI, so a reworded CLI message would otherwise go unnoticed
    until a refused desktop session lost its recovery hint."""
    api_client = (VAMSCLI_PACKAGE / "utils" / "api_client.py").read_text(encoding="utf-8")
    handler = (VAMSCLI_PACKAGE / "utils" / "global_exceptions.py").read_text(encoding="utf-8")
    exceptions = (VAMSCLI_PACKAGE / "utils" / "exceptions.py").read_text(encoding="utf-8")
    json_output = (VAMSCLI_PACKAGE / "utils" / "json_output.py").read_text(encoding="utf-8")
    for wording in module._ACCESS_DENIED_WORDING:
        assert wording in api_client
    for heading in module._ACCESS_DENIED_HEADINGS:
        assert f'_handle_global_error("{heading.rstrip(":")}"' in handler
    for error_type in module._ACCESS_DENIED_ERROR_TYPES:
        assert f"class {error_type}(" in exceptions
    assert '"error_type": error.__class__.__name__' in json_output


def _module_string(path, name):
    """A module-level string constant of a CLI source file, read without importing the CLI."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not a module-level constant of {path.name}")


@pytest.mark.skipif(not VAMSCLI_PACKAGE.is_dir(), reason="the VAMS CLI sources are not beside the connector")
def test_the_cli_texts_for_a_blocked_request_carry_no_refusal_wording():
    """The connectors do not import the CLI, so a block message reworded with a refusal's wording would
    make an adopted desktop report every blocked call as a pending sign-in, and ArcGIS Pro re-prompt a
    sign-in that cannot help."""
    api_client = VAMSCLI_PACKAGE / "utils" / "api_client.py"
    handler = (VAMSCLI_PACKAGE / "utils" / "global_exceptions.py").read_text(encoding="utf-8")
    assert '_handle_global_error("Request Blocked"' in handler
    texts = {
        "heading": "Request Blocked:",
        "_WAF_BLOCKED_MESSAGE": _module_string(api_client, "_WAF_BLOCKED_MESSAGE"),
        "_WAF_OR_STAGE_MESSAGE": _module_string(api_client, "_WAF_OR_STAGE_MESSAGE"),
        "WAF_ADMIN_HINT": _module_string(VAMSCLI_PACKAGE / "constants.py", "WAF_ADMIN_HINT"),
    }
    triggers = (*module._ACCESS_DENIED_HEADINGS, *module._ACCESS_DENIED_WORDING,
                *module._ACCESS_DENIED_ERROR_TYPES, *_ARCGIS_SIGN_IN_TRIGGERS)
    for name, text in texts.items():
        assert isinstance(text, str) and text, name
        for trigger in triggers:
            assert trigger.lower() not in text.lower(), (name, trigger)
    # Control: the CLI's own denial texts carry a trigger, and read as a refusal by their wording
    # alone, so the matching above is live.
    for name in ("_RESOURCE_POLICY_MESSAGE", "_AUTHORIZER_DENIED_MESSAGE"):
        denial = _module_string(api_client, name)
        assert any(trigger.lower() in denial.lower() for trigger in triggers), name
        assert isinstance(module._cli_error(denial, "Error"), VamsAccessDeniedError), name


@pytest.mark.skipif(not ARCGIS_CLI_SERVICE.is_file(), reason="the ArcGIS Pro connector is not beside this one")
def test_the_arcgis_sign_in_triggers_are_the_ones_the_guard_reads():
    """Every text VamsCliService.cs looks for in an exception message, in any catch filter or body and
    with any comparison argument, is one the wording guard reads."""
    source = ARCGIS_CLI_SERVICE.read_text(encoding="utf-8")
    triggers = set(re.findall(
        r'Message\??(?:\.To(?:Lower|Upper)(?:Invariant)?\(\)\??)?\.(?:Contains|StartsWith|EndsWith|IndexOf)'
        r'\(\s*"([^"]+)"', source))
    assert triggers == set(_ARCGIS_SIGN_IN_TRIGGERS)
