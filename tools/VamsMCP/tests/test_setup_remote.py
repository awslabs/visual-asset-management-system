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


def test_external_idp_setup_records_the_deployments_user_id_claim_and_the_audience_claims(
    external, tmp_path, capsys
):
    original = external.amplify_config
    external.amplify_config = lambda: {**original(), "externalOAuthIdpUserIdClaim": "email"}
    code, output = _run(external, tmp_path, "--audience-claim", "client_id", "--audience-claim", "aud")
    assert code == 0
    settings = RemoteAuthSettings.load(str(output))
    assert (settings.audience_claims, settings.user_id_claim) == (("client_id", "aud"), "email")
    written = json.loads(output.read_text())
    assert (written["audienceClaims"], written["userIdClaim"]) == (["client_id", "aud"], "email")


def test_external_idp_setup_without_the_claim_settings_writes_neither_key(external, tmp_path, capsys):
    external.amplify_config = lambda: {**FakeDeployment.amplify_config(external), "externalOAuthIdpUserIdClaim": "undefined"}
    code, output = _run(external, tmp_path)
    assert code == 0
    written = json.loads(output.read_text())
    assert "audienceClaims" not in written and "userIdClaim" not in written


def test_the_user_id_claim_flag_overrides_the_deployments(external, tmp_path, capsys):
    original = external.amplify_config
    external.amplify_config = lambda: {**original(), "externalOAuthIdpUserIdClaim": "email"}
    code, output = _run(external, tmp_path, "--user-id-claim", "preferred_username")
    assert code == 0
    assert RemoteAuthSettings.load(str(output)).user_id_claim == "preferred_username"


@pytest.mark.parametrize("flags", [
    ("--audience-claim", "client_id"),
    ("--user-id-claim", "email"),
])
def test_the_claim_flags_are_refused_for_cognito(cognito, tmp_path, capsys, flags):
    code, _ = _run(cognito, tmp_path, *flags)
    assert code == 1
    assert "external IdP" in capsys.readouterr().err


def test_a_reserved_user_id_claim_is_refused(external, tmp_path, capsys):
    code, _ = _run(external, tmp_path, "--user-id-claim", "vams:tokens")
    assert code == 1
    assert "userIdClaim" in capsys.readouterr().err


def test_external_idp_setup_reads_the_published_audience_claims(external, tmp_path, capsys):
    original = external.amplify_config
    external.amplify_config = lambda: {**original(), "externalOAuthIdpAudienceClaims": "client_id,aud"}
    code, output = _run(external, tmp_path)
    assert code == 0
    assert RemoteAuthSettings.load(str(output)).audience_claims == ("client_id", "aud")
    assert "does not publish" not in capsys.readouterr().err


def test_the_audience_claim_flag_overrides_the_published_claims_with_a_note(external, tmp_path, capsys):
    original = external.amplify_config
    external.amplify_config = lambda: {**original(), "externalOAuthIdpAudienceClaims": "client_id"}
    code, output = _run(external, tmp_path, "--audience-claim", "aud")
    assert code == 0
    assert RemoteAuthSettings.load(str(output)).audience_claims == ("aud",)
    assert "overrides the deployment's published audience claims" in capsys.readouterr().err


def test_an_older_deployment_without_the_field_falls_back_to_the_default_with_a_note(external, tmp_path, capsys):
    original = external.amplify_config
    external.amplify_config = lambda: {k: v for k, v in original().items() if k != "externalOAuthIdpAudienceClaims"}
    code, output = _run(external, tmp_path)
    assert code == 0
    assert "audienceClaims" not in json.loads(output.read_text())
    assert "does not publish its audience claims" in capsys.readouterr().err


def test_an_unrecognized_published_audience_claim_value_is_refused(external, tmp_path, capsys):
    original = external.amplify_config
    external.amplify_config = lambda: {**original(), "externalOAuthIdpAudienceClaims": "azp"}
    code, output = _run(external, tmp_path)
    assert code == 1
    assert "externalOAuthIdpAudienceClaims" in capsys.readouterr().err
    assert not output.exists()


def test_the_user_id_claim_flag_that_differs_from_the_published_claim_gives_a_note(external, tmp_path, capsys):
    original = external.amplify_config
    external.amplify_config = lambda: {**original(), "externalOAuthIdpUserIdClaim": "sub"}
    code, output = _run(external, tmp_path, "--user-id-claim", "email")
    assert code == 0
    assert RemoteAuthSettings.load(str(output)).user_id_claim == "email"
    assert "differs from the deployment's published user id claim (sub)" in capsys.readouterr().err


def test_the_user_id_claim_flag_equal_to_the_published_claim_gives_no_note(external, tmp_path, capsys):
    original = external.amplify_config
    external.amplify_config = lambda: {**original(), "externalOAuthIdpUserIdClaim": "sub"}
    code, _ = _run(external, tmp_path, "--user-id-claim", "sub")
    assert code == 0
    assert "published user id claim" not in capsys.readouterr().err


def test_the_user_id_claim_flag_with_no_published_claim_gives_a_note(external, tmp_path, capsys):
    original = external.amplify_config
    external.amplify_config = lambda: {**original(), "externalOAuthIdpUserIdClaim": "undefined"}
    code, output = _run(external, tmp_path, "--user-id-claim", "sub")
    assert code == 0
    assert RemoteAuthSettings.load(str(output)).user_id_claim == "sub"
    err = capsys.readouterr().err
    assert "publishes no user id claim; using --user-id-claim sub" in err
    assert "cognito:username, username and sub" in err


def test_the_audience_claim_flag_with_no_published_claims_gives_a_note(external, tmp_path, capsys):
    original = external.amplify_config
    external.amplify_config = lambda: {**original(), "externalOAuthIdpAudienceClaims": "undefined"}
    code, _ = _run(external, tmp_path, "--audience-claim", "client_id")
    assert code == 0
    assert "publishes no audience claims; using --audience-claim client_id" in capsys.readouterr().err


def test_a_relative_published_discovery_path_is_joined_to_the_provider_url(external, tmp_path, capsys):
    original = external.amplify_config
    external.amplify_config = lambda: {**original(), "externalOAuthIdpDiscoveryEndpoint": "/.well-known/openid-configuration"}
    code, output = _run(external, tmp_path)
    assert code == 0, capsys.readouterr().err
    assert RemoteAuthSettings.load(str(output)).issuer == external.issuer
    # with --issuer too: the issuer only overrides the issuer recorded, discovery still succeeds
    code, _ = _run(external, tmp_path, "--force", "--issuer", external.issuer)
    assert code == 0


def test_an_absolute_published_discovery_url_is_used_as_given(external, tmp_path, capsys):
    original = external.amplify_config
    external.amplify_config = lambda: {
        **original(),
        "externalOAuthIdpDiscoveryEndpoint": f"{external.issuer}/.well-known/openid-configuration",
    }
    code, output = _run(external, tmp_path)
    assert code == 0, capsys.readouterr().err
    assert RemoteAuthSettings.load(str(output)).jwks_uri == external.jwks_uri


def test_the_issuer_flag_is_the_fallback_when_the_published_discovery_path_does_not_resolve(
    external, tmp_path, capsys
):
    original = external.amplify_config
    external.amplify_config = lambda: {**original(), "externalOAuthIdpDiscoveryEndpoint": "/no/such/document"}
    code, _ = _run(external, tmp_path)
    assert code == 1 and "pass --issuer" in capsys.readouterr().err
    code, output = _run(external, tmp_path, "--issuer", external.issuer)
    assert code == 0, capsys.readouterr().err
    assert RemoteAuthSettings.load(str(output)).jwks_uri == external.jwks_uri


# --- The deployment's settings are read over https, or http to loopback only -------------------------


def test_a_plain_http_vams_url_is_refused_before_any_request(tmp_path, capsys, monkeypatch):
    def no_request(*args, **kwargs):
        raise AssertionError("nothing may be read over plain http from another host")

    monkeypatch.setattr(setup_remote.requests, "get", no_request)
    code = setup_remote.main(["--vams-url", "http://vams.example.com", "--resource-url", RESOURCE,
                              "--client-id", REMOTE_CLIENT_ID, "--output", str(tmp_path / "a.json")])
    assert code == 1 and not (tmp_path / "a.json").exists()
    assert "https" in capsys.readouterr().err


@pytest.mark.parametrize("url", [
    "http://idp.example.com/.well-known/openid-configuration", "http://10.0.0.5/api/amplify-config",
])
def test_no_setting_is_read_over_plain_http_from_another_host(monkeypatch, url):
    monkeypatch.setattr(setup_remote.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError(url)))
    with pytest.raises(setup_remote.SetupError, match="https"):
        setup_remote._get_json(url)


def test_a_published_plain_http_discovery_url_is_refused(external, tmp_path, capsys, monkeypatch):
    """The deployment's published absolute discovery URL is read only over https (or loopback http)."""
    published = "http://idp.example.com/.well-known/openid-configuration"
    original = external.amplify_config

    def amplify_config():
        return {**original(), "externalOAuthIdpDiscoveryEndpoint": published}

    monkeypatch.setattr(external, "amplify_config", amplify_config)
    code, output = _run(external, tmp_path, "--audience", "api://vams")
    assert code == 1 and not output.exists()
    assert published in capsys.readouterr().err


@pytest.mark.parametrize("host", [
    "localhost", "LOCALHOST", "127.0.0.1", "127.8.9.10", "::1", "[::1]", " localhost ",
    "", "0.0.0.0", "10.0.0.1", "192.168.1.1", "localhost.example.com", "example.com", "::ffff:127.0.0.1",
])
def test_the_server_and_the_cli_agree_on_which_hosts_are_loopback(host):
    """`setup-remote` checks URLs with the CLI's rule and the server checks its own bind and resource
    URL with its own copy: the two must answer alike."""
    from vamscli.commands.setup import is_loopback_host as cli_rule
    from vams_mcp.config import is_loopback_host as server_rule

    assert cli_rule(host) == server_rule(host)
