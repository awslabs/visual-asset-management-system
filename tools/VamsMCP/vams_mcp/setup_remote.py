"""``vams-mcp setup-remote``: write a remote server's auth configuration from the deployment's own.

The operator runs it once per remote server, after registering the OAuth client that remote MCP
clients sign users in with (a Cognito app client, or a client of the external IdP). It reads the
deployment's public auth settings (``GET /api/amplify-config``, no sign-in), resolves the issuer and
its key set, writes the JSON file the server reads (``VAMS_MCP_AUTH_CONFIG``), and prints what a
remote MCP client such as Amazon Quick needs: the endpoint, client id, authorization and token URLs,
and scopes.

It handles no user token and stores none, and the file it writes holds no secret: a client secret,
when the IdP issues one, is entered in the MCP client only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import requests

from .config import ConfigError
from .remote_auth import (
    MAX_AUDIENCES,
    PROVIDER_COGNITO,
    PROVIDER_EXTERNAL,
    SUPPORTED_AUDIENCE_CLAIMS,
    RemoteAuthSettings,
)

HTTP_TIMEOUT = (5, 15)
DEFAULT_OUTPUT = "vams-mcp-remote-auth.json"
DEFAULT_COGNITO_SCOPES = ("openid", "email", "profile")
# A Cognito app client id: word characters and '+', at most 128 (the service's own pattern).
_COGNITO_CLIENT_ID = re.compile(r"^[\w+]{1,128}$")
_UNSET = ("", "undefined", None)


class SetupError(Exception):
    """A step the operator has to correct; the message says how."""


def _require_secure_url(url: str) -> None:
    """Refuse a URL to read the deployment's settings from unless it is https (or http to loopback)."""
    from vamscli.commands.setup import BASE_URL_RULE, validate_base_url

    if not validate_base_url(url):
        raise SetupError(f"'{url}' cannot be read: it must be {BASE_URL_RULE}")


def _get_json(url: str) -> Dict[str, Any]:
    _require_secure_url(url)
    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        raise SetupError(f"could not reach {url}: {type(exc).__name__}")
    if response.status_code != 200:
        raise SetupError(f"{url} answered HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise SetupError(f"{url} did not return JSON")
    if not isinstance(data, dict):
        raise SetupError(f"{url} did not return a JSON object")
    return data


def _set(value: Any) -> Optional[str]:
    return None if value in _UNSET else str(value)


def _normalize_vams_url(url: str) -> str:
    from vamscli.commands.setup import BASE_URL_RULE, normalize_base_url_for_stage, validate_base_url

    url = url.strip().rstrip("/")
    if not validate_base_url(url):
        raise SetupError(f"'{url}' is not a valid VAMS web application or API URL: use {BASE_URL_RULE}")
    return normalize_base_url_for_stage(url).rstrip("/")


def _amplify_config(vams_url: str) -> Dict[str, Any]:
    from vamscli.constants import API_AMPLIFY_CONFIG

    return _get_json(f"{vams_url}/{API_AMPLIFY_CONFIG.lstrip('/')}")


def _discovery_url(base: str, published: Optional[str] = None) -> str:
    """The discovery document URL: the deployment's published value, else the standard path under ``base``.

    The deployment publishes externalOAuthIdpDiscoveryEndpoint as a path (for example
    ``/.well-known/openid-configuration``) that is joined to the provider URL, as the web application
    does; an absolute http(s) value is used as given.
    """
    if published:
        if published.startswith(("https://", "http://")):
            return published
        if published.startswith("/"):
            return f"{base.rstrip('/')}{published}"
    return f"{base.rstrip('/')}/.well-known/openid-configuration"


def _discovery(
    issuer: str, explicit: Optional[str] = None, fallback_issuer: Optional[str] = None,
    errors: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Read the discovery document from ``issuer`` (the published path applies), else from ``fallback_issuer``.

    Each read that fails adds its reason to ``errors`` when a list is given.
    """
    urls = [_discovery_url(issuer, explicit)]
    if fallback_issuer:
        urls.append(_discovery_url(fallback_issuer))
    for url in dict.fromkeys(urls):
        try:
            return _get_json(url)
        except SetupError as exc:
            if errors is not None:
                errors.append(str(exc))
            continue
    return None


def _domain_url(domain: str) -> str:
    domain = domain.strip().rstrip("/")
    return domain if domain.startswith("https://") else f"https://{domain}"


def _dns_suffix(region: str) -> str:
    """The AWS DNS suffix of a Region's partition (the EU Sovereign Cloud uses amazonaws.eu)."""
    return "amazonaws.eu" if region.startswith("eusc-") else "amazonaws.com"


def _refuse_web_client(audiences: List[str], web_client_id: Optional[str]) -> None:
    """Refuse the web application's own client as the client id or as an extra audience."""
    if web_client_id and web_client_id in audiences:
        raise SetupError(
            f"'{web_client_id}' is the web application's own client; the remote server must not accept its "
            "tokens. Register a separate app client for remote MCP clients (see the remote MCP server documentation)"
        )


def _published_audience_claims(value: Any) -> List[str]:
    """The audience claim names a deployment publishes: [] when absent, an error when not recognized."""
    text = _set(value)
    if not text:
        return []
    names = [name.strip() for name in text.split(",")]
    if not names or any(name not in SUPPORTED_AUDIENCE_CLAIMS for name in names) or len(set(names)) != len(names):
        raise SetupError(
            f"the deployment publishes an unrecognized externalOAuthIdpAudienceClaims value; expected a "
            f"comma-separated list of {', '.join(SUPPORTED_AUDIENCE_CLAIMS)}. Pass --audience-claim instead"
        )
    return names


def build_settings(args: argparse.Namespace, amplify: Dict[str, Any]) -> RemoteAuthSettings:
    api_url = _set(amplify.get("api"))
    if not api_url:
        raise SetupError("the deployment's amplify configuration names no API URL")
    api_url = _normalize_vams_url(api_url)
    audiences: List[str] = list(dict.fromkeys([args.client_id, *(args.audience or [])]))
    if len(audiences) > MAX_AUDIENCES:
        raise SetupError(f"at most {MAX_AUDIENCES} audiences can be configured")

    user_id_claim: Optional[str] = None
    audience_claims: List[str] = []
    pool_id = _set(amplify.get("cognitoUserPoolId"))
    if pool_id:
        provider = PROVIDER_COGNITO
        if args.audience_claim or args.user_id_claim:
            raise SetupError("--audience-claim and --user-id-claim apply to an external IdP deployment only")
        for value in audiences:
            if not _COGNITO_CLIENT_ID.match(value):
                raise SetupError(f"'{value}' is not a Cognito app client id")
        web_client_id = _set(amplify.get("cognitoAppClientId"))
        _refuse_web_client(audiences, web_client_id)
        # The deployment reports its own partition-aware user pool endpoint; the fallback serves
        # deployments older than the field.
        endpoint = _set(amplify.get("cognitoUserPoolEndpoint"))
        if not endpoint:
            region = _set(amplify.get("region"))
            if not region:
                raise SetupError("the deployment's amplify configuration names no region")
            endpoint = f"https://cognito-idp.{region}.{_dns_suffix(region)}"
        issuer = f"{endpoint.rstrip('/')}/{pool_id}"
        jwks_uri = f"{issuer}/.well-known/jwks.json"
        scopes = tuple(args.scopes.split()) if args.scopes else DEFAULT_COGNITO_SCOPES
        authorization_url = token_url = None
        domain = args.cognito_domain or _set((amplify.get("cognitoFederatedConfig") or {}).get("customCognitoAuthDomain"))
        if domain:
            base = _domain_url(domain)
            authorization_url, token_url = f"{base}/oauth2/authorize", f"{base}/oauth2/token"
        else:
            document = _discovery(issuer) or {}
            authorization_url = _set(document.get("authorization_endpoint"))
            token_url = _set(document.get("token_endpoint"))
    elif _set(amplify.get("externalOAuthIdpURL")):
        provider = PROVIDER_EXTERNAL
        web_client_id = _set(amplify.get("externalOAuthIdpClientId"))
        _refuse_web_client(audiences, web_client_id)
        provider_url = _set(amplify.get("externalOAuthIdpURL"))
        discovery_errors: List[str] = []
        document = _discovery(
            provider_url, _set(amplify.get("externalOAuthIdpDiscoveryEndpoint")), fallback_issuer=args.issuer,
            errors=discovery_errors,
        )
        if document is None:
            raise SetupError(
                f"the external IdP's OpenID Connect discovery document could not be read "
                f"({'; '.join(discovery_errors)}); pass --issuer with the issuer that signs the IdP's access tokens"
            )
        issuer = args.issuer or _set(document.get("issuer"))
        jwks_uri = _set(document.get("jwks_uri"))
        if not issuer or not jwks_uri:
            raise SetupError("the external IdP's discovery document names no issuer or jwks_uri")
        scopes = tuple((args.scopes or _set(amplify.get("externalOAuthIdpScope")) or "openid").split())
        # The deployment reports the claim its API reads the user id from (blank or "undefined" when
        # not set) and the claims its API reads the audience from (externalOAuthIdpAudienceClaims,
        # comma-separated names). --audience-claim stays as an explicit override. A deployment that
        # predates the field publishes nothing: the default (aud) applies and a note says so.
        published_user_id_claim = _set(amplify.get("externalOAuthIdpUserIdClaim"))
        user_id_claim = args.user_id_claim or published_user_id_claim
        if args.user_id_claim and not published_user_id_claim:
            print(
                f"vams-mcp setup-remote: note: the deployment publishes no user id claim; using "
                f"--user-id-claim {args.user_id_claim}. The API identifies users by the first of "
                "cognito:username, username and sub, so a different value makes the server refuse tokens",
                file=sys.stderr,
            )
        elif args.user_id_claim and args.user_id_claim != published_user_id_claim:
            print(
                f"vams-mcp setup-remote: note: --user-id-claim {args.user_id_claim} differs from the "
                f"deployment's published user id claim ({published_user_id_claim}); the API identifies users "
                "by the published claim, so the user label the server logs can differ from the API user",
                file=sys.stderr,
            )
        published_claims = _published_audience_claims(amplify.get("externalOAuthIdpAudienceClaims"))
        if args.audience_claim:
            audience_claims = list(dict.fromkeys(args.audience_claim))
            if not published_claims:
                print(
                    f"vams-mcp setup-remote: note: the deployment publishes no audience claims; using "
                    f"--audience-claim {','.join(audience_claims)}. The API refuses tokens that do not "
                    "match its lambdaAuthorizorJWTAudienceClaims",
                    file=sys.stderr,
                )
            elif audience_claims != published_claims:
                print(
                    f"vams-mcp setup-remote: note: --audience-claim {','.join(audience_claims)} overrides the "
                    f"deployment's published audience claims ({','.join(published_claims)}); the API will "
                    "refuse tokens that do not match the deployment",
                    file=sys.stderr,
                )
        elif published_claims:
            audience_claims = published_claims
        else:
            print(
                "vams-mcp setup-remote: note: the deployment does not publish its audience claims (an older "
                "VAMS release); assuming the default (aud). Pass --audience-claim if its "
                "lambdaAuthorizorJWTAudienceClaims differs",
                file=sys.stderr,
            )
        authorization_url = _set(document.get("authorization_endpoint")) or _set(
            amplify.get("externalOAuthIdpAuthorizationEndpoint")
        )
        token_url = _set(document.get("token_endpoint")) or _set(amplify.get("externalOAuthIdpTokenEndpoint"))
    else:
        raise SetupError("the deployment's amplify configuration names neither Cognito nor an external IdP")

    data = {
        "version": 1,
        "apiUrl": api_url,
        "resourceUrl": args.resource_url.strip().rstrip("/"),
        "provider": provider,
        "issuer": issuer,
        "jwksUri": jwks_uri,
        "audiences": audiences,
        "scopes": list(scopes),
        "client": {"clientId": args.client_id, "authorizationUrl": authorization_url, "tokenUrl": token_url},
    }
    if web_client_id:
        # Recorded so the server refuses to start if the file is later edited to accept the web
        # application's tokens.
        data["webAppClientId"] = web_client_id
    if audience_claims:
        data["audienceClaims"] = audience_claims
    if user_id_claim:
        data["userIdClaim"] = user_id_claim
    try:
        return RemoteAuthSettings.from_dict(data)
    except ConfigError as exc:
        raise SetupError(str(exc))


def _write(path: str, settings: RemoteAuthSettings, force: bool) -> None:
    if os.path.exists(path) and not force:
        raise SetupError(f"'{path}' exists; pass --force to replace it")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with os.fdopen(os.open(path, flags, 0o644), "w", encoding="utf-8") as handle:
        json.dump(settings.to_dict(), handle, indent=2)
        handle.write("\n")


def summary(settings: RemoteAuthSettings, path: str) -> Dict[str, Any]:
    client = settings.client
    setting = (
        "app.authProvider.useCognito.additionalTokenAudiences"
        if settings.provider == PROVIDER_COGNITO
        else "app.authProvider.useExternalOAuthIdp.additionalTokenAudiences"
    )
    return {
        "authConfigFile": os.path.abspath(path),
        "mcpClient": {
            "endpoint": settings.resource_url,
            "clientId": client.get("clientId"),
            "authorizationUrl": client.get("authorizationUrl"),
            "tokenUrl": client.get("tokenUrl"),
            "scopes": " ".join(settings.scopes),
        },
        "operatorSteps": [
            f"List {', '.join(settings.audiences)} in {setting} and deploy VAMS, so the VAMS API accepts "
            "these tokens.",
            "Register the MCP client's redirect (callback) URL on the client.",
            f"Start the server with VAMS_MCP_TRANSPORT=streamable-http and VAMS_MCP_AUTH_CONFIG={os.path.abspath(path)}, "
            "behind an HTTPS front end that serves the endpoint above.",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vams-mcp setup-remote",
        description="Write a remote VAMS MCP server's auth configuration and print what MCP clients need.",
    )
    parser.add_argument("--vams-url", required=True, help="The VAMS web application URL or API Gateway URL")
    parser.add_argument(
        "--resource-url", required=True, help="The public https URL MCP clients connect to, ending in /mcp"
    )
    parser.add_argument(
        "--client-id", required=True, help="The OAuth client registered for remote MCP clients (user sign-in)"
    )
    parser.add_argument(
        "--audience", action="append", help="Another accepted audience (repeatable; the client id is always one)"
    )
    parser.add_argument("--cognito-domain", help="The Cognito user pool domain, for the authorization URLs")
    parser.add_argument(
        "--issuer",
        help="External IdP: the issuer of its access tokens, when it differs from the discovery document or "
        "when the provider URL serves no discovery document (for example an Amazon Cognito hosted UI domain)",
    )
    parser.add_argument(
        "--audience-claim",
        action="append",
        choices=SUPPORTED_AUDIENCE_CLAIMS,
        help="External IdP: a token claim the audience is read from (repeatable, in order; default aud). "
        "Overrides the deployment's published externalOAuthIdpAudienceClaims (default: the published value, "
        "or aud when the deployment does not publish it); must equal the deployment's "
        "lambdaAuthorizorJWTAudienceClaims",
    )
    parser.add_argument(
        "--user-id-claim",
        help="External IdP: the token claim the user id is read from, when the deployment does not "
        "publish it (default: the deployment's externalOAuthIdpUserIdClaim)",
    )
    parser.add_argument("--scopes", help="Space-separated scopes the MCP client requests")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"The file to write (default {DEFAULT_OUTPUT})")
    parser.add_argument("--force", action="store_true", help="Replace an existing file")
    parser.add_argument("--json", action="store_true", help="Print the summary as JSON")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if urlsplit(args.resource_url.strip()).scheme not in ("https", "http"):
            raise SetupError("--resource-url must be the https URL MCP clients connect to")
        amplify = _amplify_config(_normalize_vams_url(args.vams_url))
        settings = build_settings(args, amplify)
        _write(args.output, settings, args.force)
    except SetupError as exc:
        print(f"vams-mcp setup-remote: {exc}", file=sys.stderr)
        return 1
    result = summary(settings, args.output)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    client = result["mcpClient"]
    print(f"Wrote {result['authConfigFile']}\n")
    print("Values for the remote MCP client:")
    for label, key in (("Endpoint", "endpoint"), ("Client ID", "clientId"), ("Authorization URL", "authorizationUrl"),
                       ("Token URL", "tokenUrl"), ("Scopes", "scopes")):
        print(f"  {label + ':':19} {client[key] or '(not known: see the operator steps in the documentation)'}")
    print("\nOperator steps:")
    for number, step in enumerate(result["operatorSteps"], 1):
        print(f"  {number}. {step}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
