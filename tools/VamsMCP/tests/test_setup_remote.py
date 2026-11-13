"""``vams-mcp setup-remote``: the auth configuration it writes from the deployment's public settings.

The fake deployment of ``tests/remote_support.py`` serves ``/api/amplify-config`` and the issuer's
OpenID Connect discovery document on loopback. The command must write a file the server loads, print
the values a remote MCP client needs, refuse the web application's own client, and handle no token.
"""

import json
import os
import stat

import pytest

from vams_mcp import cli, setup_remote
from vams_mcp.remote_auth import RemoteAuthSettings

from .remote_support import POOL_ID, REMOTE_CLIENT_ID, WEB_CLIENT_ID, FakeDeployment

RESOURCE = "https://mcp.example.com/mcp"


@pytest.fixture
def cognito():
    with FakeDeployment("cognito") as fake:
        yield fake


@pytest.fixture
def external():
    with FakeDeployment("external") as fake:
        yield fake


def _run(fake, tmp_path, *extra, client_id=REMOTE_CLIENT_ID):
    output = tmp_path / "auth.json"
    code = setup_remote.main([
        "--vams-url", fake.base, "--resource-url", RESOURCE, "--client-id", client_id,
        "--output", str(output), "--json", *extra,
    ])
    return code, output


def test_cognito_setup_writes_a_configuration_the_server_loads(cognito, tmp_path, capsys):
    code, output = _run(cognito, tmp_path)
    assert code == 0
    settings = RemoteAuthSettings.load(str(output))
    assert settings.provider == "cognito"
    assert settings.issuer == f"{cognito.base}/{POOL_ID}"
    assert settings.jwks_uri == f"{cognito.base}/{POOL_ID}/.well-known/jwks.json"
    assert settings.api_url == cognito.api_url
    assert settings.audiences == (REMOTE_CLIENT_ID,)
    assert settings.scopes == ("openid", "email", "profile")
    assert stat.S_IMODE(os.stat(output).st_mode) == 0o644

    summary = json.loads(capsys.readouterr().out)
    assert summary["mcpClient"] == {
        "endpoint": RESOURCE,
        "clientId": REMOTE_CLIENT_ID,
        "authorizationUrl": f"{cognito.base}/oauth2/authorize",
        "tokenUrl": f"{cognito.base}/oauth2/token",
        "scopes": "openid email profile",
    }
    assert any("additionalTokenAudiences" in step for step in summary["operatorSteps"])


def test_the_file_holds_no_token_or_secret(cognito, tmp_path):
    _, output = _run(cognito, tmp_path)
    data = json.loads(output.read_text())
    flat = json.dumps(data).lower()
    assert "secret" not in flat and "password" not in flat
    assert set(data) == {"version", "apiUrl", "resourceUrl", "provider", "issuer", "jwksUri", "audiences",
                         "scopes", "client", "webAppClientId"}
    assert data["webAppClientId"] == WEB_CLIENT_ID
    assert set(data["client"]) == {"clientId", "authorizationUrl", "tokenUrl"}


def test_a_cognito_domain_gives_the_managed_login_urls(cognito, tmp_path, capsys):
    code, _ = _run(cognito, tmp_path, "--cognito-domain", "vams-mcp.auth.us-east-1.amazoncognito.com")
    assert code == 0
    client = json.loads(capsys.readouterr().out)["mcpClient"]
    assert client["authorizationUrl"] == "https://vams-mcp.auth.us-east-1.amazoncognito.com/oauth2/authorize"
    assert client["tokenUrl"] == "https://vams-mcp.auth.us-east-1.amazoncognito.com/oauth2/token"


def test_the_web_applications_own_client_is_refused(cognito, tmp_path, capsys):
    code, output = _run(cognito, tmp_path, client_id=WEB_CLIENT_ID)
    assert code == 1 and not output.exists()
    assert "separate app client" in capsys.readouterr().err


def test_a_value_that_is_not_a_cognito_client_id_is_refused(cognito, tmp_path, capsys):
    code, _ = _run(cognito, tmp_path, client_id="not a client/id")
    assert code == 1 and "Cognito app client id" in capsys.readouterr().err


def test_extra_audiences_are_kept_and_bounded(cognito, tmp_path, capsys):
    code, output = _run(cognito, tmp_path, "--audience", "secondclient00000000000000")
    assert code == 0
    assert RemoteAuthSettings.load(str(output)).audiences == (REMOTE_CLIENT_ID, "secondclient00000000000000")
    many = [arg for i in range(10) for arg in ("--audience", f"client{i:020d}")]
    code, _ = _run(cognito, tmp_path, "--force", *many)
    assert code == 1 and "at most 10" in capsys.readouterr().err


def test_an_existing_file_is_replaced_only_with_force(cognito, tmp_path, capsys):
    assert _run(cognito, tmp_path)[0] == 0
    assert _run(cognito, tmp_path)[0] == 1 and "--force" in capsys.readouterr().err
    assert _run(cognito, tmp_path, "--force")[0] == 0


def test_external_idp_setup_reads_the_discovery_document(external, tmp_path, capsys):
    code, output = _run(external, tmp_path, "--audience", "api://vams")
    assert code == 0
    settings = RemoteAuthSettings.load(str(output))
    assert settings.provider == "external"
    assert (settings.issuer, settings.jwks_uri) == (external.issuer, external.jwks_uri)
    assert settings.audiences == (REMOTE_CLIENT_ID, "api://vams")
    assert settings.scopes == ("openid", "profile")
    assert json.loads(capsys.readouterr().out)["mcpClient"]["tokenUrl"] == f"{external.base}/oauth2/token"


def test_an_unreachable_deployment_is_reported(tmp_path, capsys):
    code = setup_remote.main(["--vams-url", "http://127.0.0.1:9", "--resource-url", RESOURCE,
                              "--client-id", REMOTE_CLIENT_ID, "--output", str(tmp_path / "a.json")])
    assert code == 1 and "could not reach" in capsys.readouterr().err


def test_the_vams_mcp_command_dispatches_setup_remote(cognito, tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["setup-remote", "--vams-url", cognito.base, "--resource-url", RESOURCE,
                  "--client-id", REMOTE_CLIENT_ID, "--output", str(tmp_path / "a.json")])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "Values for the remote MCP client" in out and REMOTE_CLIENT_ID in out


def test_the_web_applications_own_client_is_refused_as_an_audience(cognito, tmp_path, capsys):
    code, output = _run(cognito, tmp_path, "--audience", WEB_CLIENT_ID)
    assert code == 1 and not output.exists()
    assert "web application's own client" in capsys.readouterr().err


def test_the_external_idps_web_client_is_refused_as_an_audience(external, tmp_path, capsys):
    external_web = "external-web-client"
    original = external.amplify_config

    def with_web_client():
        return {**original(), "externalOAuthIdpClientId": external_web}

    external.amplify_config = with_web_client
    code, output = _run(external, tmp_path, "--audience", external_web)
    assert code == 1 and not output.exists()
    assert "web application's own client" in capsys.readouterr().err


def test_the_fallback_user_pool_host_follows_the_partition():
    assert setup_remote._dns_suffix("eusc-de-east-1") == "amazonaws.eu"
    assert setup_remote._dns_suffix("us-gov-west-1") == "amazonaws.com"
    assert setup_remote._dns_suffix("us-east-1") == "amazonaws.com"
