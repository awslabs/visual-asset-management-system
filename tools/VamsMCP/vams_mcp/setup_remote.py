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
from .remote_auth import MAX_AUDIENCES, PROVIDER_COGNITO, PROVIDER_EXTERNAL, RemoteAuthSettings

HTTP_TIMEOUT = (5, 15)
DEFAULT_OUTPUT = "vams-mcp-remote-auth.json"
DEFAULT_COGNITO_SCOPES = ("openid", "email", "profile")
# A Cognito app client id: word characters and '+', at most 128 (the service's own pattern).
_COGNITO_CLIENT_ID = re.compile(r"^[\w+]{1,128}$")
_UNSET = ("", "undefined", None)


class SetupError(Exception):
    """A step the operator has to correct; the message says how."""


def _get_json(url: str) -> Dict[str, Any]:
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
    from vamscli.commands.setup import normalize_base_url_for_stage, validate_base_url

    url = url.strip().rstrip("/")
    if not validate_base_url(url):
        raise SetupError(f"'{url}' is not a valid VAMS web application or API URL")
    return normalize_base_url_for_stage(url).rstrip("/")


def _amplify_config(vams_url: str) -> Dict[str, Any]:
    from vamscli.constants import API_AMPLIFY_CONFIG

    return _get_json(f"{vams_url}/{API_AMPLIFY_CONFIG.lstrip('/')}")


def _discovery(issuer: str, explicit: Optional[str] = None) -> Optional[Dict[str, Any]]:
    url = explicit or f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        return _get_json(url)
    except SetupError:
        return None


def _domain_url(domain: str) -> str:
    domain = domain.strip().rstrip("/")
    return domain if domain.startswith("https://") else f"https://{domain}"


def build_settings(args: argparse.Namespace, amplify: Dict[str, Any]) -> RemoteAuthSettings:
    api_url = _set(amplify.get("api"))
    if not api_url:
        raise SetupError("the deployment's amplify configuration names no API URL")
    api_url = _normalize_vams_url(api_url)
    audiences: List[str] = list(dict.fromkeys([args.client_id, *(args.audience or [])]))
    if len(audiences) > MAX_AUDIENCES:
        raise SetupError(f"at most {MAX_AUDIENCES} audiences can be configured")

    pool_id = _set(amplify.get("cognitoUserPoolId"))
    if pool_id:
        provider = PROVIDER_COGNITO
        for value in audiences:
            if not _COGNITO_CLIENT_ID.match(value):
                raise SetupError(f"'{value}' is not a Cognito app client id")
        if args.client_id == _set(amplify.get("cognitoAppClientId")):
            raise SetupError(
                "the client id is the web application's own app client; register a separate app client "
                "for remote MCP clients (see the remote MCP server documentation)"
            )
        endpoint = _set(amplify.get("cognitoUserPoolEndpoint"))
        if not endpoint:
            region = _set(amplify.get("region"))
            if not region:
                raise SetupError("the deployment's amplify configuration names no region")
            endpoint = f"https://cognito-idp.{region}.amazonaws.com"
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
        provider_url = _set(amplify.get("externalOAuthIdpURL"))
        document = _discovery(args.issuer or provider_url, _set(amplify.get("externalOAuthIdpDiscoveryEndpoint")))
        if document is None:
            raise SetupError(
                "the external IdP's OpenID Connect discovery document could not be read; pass --issuer "
                "with the issuer that signs the IdP's access tokens"
            )
        issuer = args.issuer or _set(document.get("issuer"))
        jwks_uri = _set(document.get("jwks_uri"))
        if not issuer or not jwks_uri:
            raise SetupError("the external IdP's discovery document names no issuer or jwks_uri")
        scopes = tuple((args.scopes or _set(amplify.get("externalOAuthIdpScope")) or "openid").split())
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
    parser.add_argument("--issuer", help="External IdP: the issuer of its access tokens, when discovery differs")
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
