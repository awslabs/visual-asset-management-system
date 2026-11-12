"""Test support for remote mode: signing keys, tokens, a fake identity provider and VAMS API, and the
remote server itself, all on the loopback interface.

``FakeDeployment`` is one HTTP server that plays the issuer (its JWKS and OpenID Connect discovery
document), the deployment's ``/api/amplify-config``, and the VAMS API routes the tests call. The VAMS
API routes answer from the token's claims (read WITHOUT verification, as a stand-in for the real
authorizer), so a test can tell which user's token reached the API. Every Authorization header the
API receives is recorded.

``RemoteServer`` reloads ``vams_mcp.server`` in remote mode, builds the real remote application
(``http_app.build_app``) and serves it with uvicorn on 127.0.0.1 in a background thread.
"""

from __future__ import annotations

import base64
import contextlib
import importlib
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

import jwt as pyjwt
import requests
from cryptography.hazmat.primitives.asymmetric import rsa

POOL_ID = "us-east-1_TestPool"
REMOTE_CLIENT_ID = "remoteclient0000000000000a"
WEB_CLIENT_ID = "webclient000000000000000aa"
OTHER_CLIENT_ID = "otherclient00000000000000a"


def _b64(number: int) -> str:
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class SigningKey:
    def __init__(self, kid: str) -> None:
        self.kid = kid
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.private.public_key().public_numbers()
        self.jwk = {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": _b64(numbers.n), "e": _b64(numbers.e)}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeDeployment:
    """The issuer, the deployment's public config and a few VAMS API routes, on one loopback port."""

    def __init__(self, provider: str = "cognito") -> None:
        self.provider = provider
        self.key = SigningKey("key-1")
        self.keys: List[SigningKey] = [self.key]
        self.jwks_status = 200
        self.jwks_fetches = 0
        self.api_authorizations: List[str] = []
        self.api_delay = 0.0
        self.refused_users: set = set()
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # --- URLs ------------------------------------------------------------

    @property
    def issuer(self) -> str:
        return f"{self.base}/{POOL_ID}" if self.provider == "cognito" else f"{self.base}/idp"

    @property
    def jwks_uri(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

    @property
    def api_url(self) -> str:
        return f"{self.base}/api"

    def amplify_config(self) -> Dict[str, Any]:
        if self.provider == "cognito":
            return {
                "api": self.api_url + "/", "region": "us-east-1", "cognitoUserPoolId": POOL_ID,
                "cognitoAppClientId": WEB_CLIENT_ID, "cognitoUserPoolEndpoint": self.base,
                "externalOAuthIdpURL": "undefined",
            }
        return {
            "api": self.api_url + "/", "region": "us-east-1", "cognitoUserPoolId": "undefined",
            "cognitoAppClientId": "undefined", "externalOAuthIdpURL": self.issuer,
            "externalOAuthIdpScope": "openid profile", "externalOAuthIdpDiscoveryEndpoint": "undefined",
        }

    def settings_dict(self, resource_url: str, audiences: Optional[List[str]] = None) -> Dict[str, Any]:
        return {
            "version": 1, "apiUrl": self.api_url, "resourceUrl": resource_url, "provider": self.provider,
            "issuer": self.issuer, "jwksUri": self.jwks_uri, "audiences": audiences or [REMOTE_CLIENT_ID],
            "scopes": ["openid", "email", "profile"], "client": {"clientId": REMOTE_CLIENT_ID},
        }

    # --- Tokens ----------------------------------------------------------

    def token(self, user: Optional[str] = "alice", *, client_id: str = REMOTE_CLIENT_ID, token_use: str = "access",
              expires_in: int = 600, issuer: Optional[str] = None, key: Optional[SigningKey] = None,
              extra: Optional[Dict[str, Any]] = None, drop: tuple = ()) -> str:
        now = int(time.time())
        claims: Dict[str, Any] = {"iss": issuer or self.issuer, "exp": now + expires_in, "iat": now, "sub": f"sub-{user}"}
        if self.provider == "cognito":
            claims["token_use"] = token_use
            if token_use == "access":
                claims["client_id"] = client_id
                claims["scope"] = "openid email profile"
                if user is not None:
                    claims["username"] = user
            else:
                claims["aud"] = client_id
                if user is not None:
                    claims["cognito:username"] = user
        else:
            claims["aud"] = client_id
            if user is None:
                claims.pop("sub")
        claims.update(extra or {})
        for name in drop:
            claims.pop(name, None)
        signer = key or self.key
        return pyjwt.encode(claims, signer.private, algorithm="RS256", headers={"kid": signer.kid})

    # --- Server ----------------------------------------------------------

    def _handler(self):
        deployment = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _json(self, status: int, body: Any) -> None:
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):  # noqa: N802
                path = urlsplit(self.path).path
                if path.endswith("/.well-known/jwks.json"):
                    deployment.jwks_fetches += 1
                    if deployment.jwks_status != 200:
                        return self._json(deployment.jwks_status, {"error": "down"})
                    return self._json(200, {"keys": [k.jwk for k in deployment.keys]})
                if path.endswith("/.well-known/openid-configuration"):
                    return self._json(200, {
                        "issuer": deployment.issuer, "jwks_uri": deployment.jwks_uri,
                        "authorization_endpoint": f"{deployment.base}/oauth2/authorize",
                        "token_endpoint": f"{deployment.base}/oauth2/token",
                    })
                if path == "/api/amplify-config":
                    return self._json(200, deployment.amplify_config())
                if path.startswith("/api/"):
                    return self._api(path[len("/api"):])
                return self._json(404, {"message": "not found"})

            def _api(self, route: str) -> None:
                header = self.headers.get("Authorization", "")
                deployment.api_authorizations.append(header)
                token = header[len("Bearer "):] if header.startswith("Bearer ") else ""
                try:
                    claims = pyjwt.decode(token, options={"verify_signature": False})
                except Exception:  # noqa: BLE001
                    return self._json(401, {"message": "Unauthorized"})
                user = claims.get("username") or claims.get("cognito:username") or claims.get("sub")
                if user in deployment.refused_users:
                    return self._json(401, {"message": "Unauthorized"})
                if deployment.api_delay:
                    time.sleep(deployment.api_delay)
                if route == "/secure-config":
                    return self._json(200, {"featuresEnabled": "AUTHPROVIDER_COGNITO"})
                if route == "/database":
                    query = parse_qs(urlsplit(self.path).query)
                    page = int((query.get("startingToken") or ["0"])[0])
                    body = {"Items": [{"databaseId": f"db-of-{user}", "page": page}]}
                    if route == "/database" and query.get("pageSize") == ["1"]:
                        body["NextToken"] = str(page + 1)
                    return self._json(200, body)
                return self._json(404, {"message": "not found"})

        return Handler

    def __enter__(self) -> "FakeDeployment":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()


class RemoteServer:
    """The real remote application (vams_mcp.server in remote mode) served by uvicorn on loopback."""

    def __init__(self, app: Any, port: int) -> None:
        import uvicorn

        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False, lifespan="on")
        self._server = uvicorn.Server(config)
        # uvicorn installs signal handlers only on the main thread; this one is a background thread.
        if hasattr(self._server, "install_signal_handlers"):
            self._server.install_signal_handlers = lambda: None
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> "RemoteServer":
        self._thread.start()
        deadline = time.time() + 20
        while not getattr(self._server, "started", False):
            if time.time() > deadline:
                raise RuntimeError("the remote server did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=20)

    # --- MCP over HTTP ---------------------------------------------------

    def post(self, body: Dict[str, Any], token: Optional[str] = None, headers: Optional[Dict[str, str]] = None,
             path: str = "/mcp"):
        sent = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if token is not None:
            sent["Authorization"] = f"Bearer {token}"
        sent.update(headers or {})
        # Redirects are not followed: a client may not resend its POST body and token after one, so
        # the server must answer /mcp itself.
        return requests.post(f"{self.url}{path}", json=body, headers=sent, timeout=30, allow_redirects=False)

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None, token: Optional[str] = None):
        response = self.post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}},
            token,
        )
        return response

    @staticmethod
    def tool_result(response) -> Dict[str, Any]:
        """The JSON a tool returned, from its text content."""
        assert response.status_code == 200, (response.status_code, response.text[:500])
        message = response.json()
        content = message["result"]["content"]
        return json.loads(content[0]["text"])


@contextlib.contextmanager
def remote_server_module(settings: Dict[str, Any], tmp_path, env: Optional[Dict[str, str]] = None):
    """``vams_mcp.server`` reloaded in remote mode over ``settings``; reloaded back to stdio on exit."""
    from vams_mcp import server as server_module
    import logging

    import vamscli.utils.logging as cli_logging

    # Remote mode silences the CLI's request log process-wide; put it back for the stdio tests.
    cli_logger = logging.getLogger("vamscli")
    saved = (cli_logging._logger, list(cli_logger.handlers), cli_logger.level, cli_logger.propagate)

    path = tmp_path / "remote-auth.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    values = {"VAMS_MCP_TRANSPORT": "streamable-http", "VAMS_MCP_AUTH_CONFIG": str(path), **(env or {})}
    for name, value in values.items():
        os.environ[name] = value
    try:
        importlib.reload(server_module)
        yield server_module
    finally:
        for name in values:
            os.environ.pop(name, None)
        importlib.reload(server_module)
        cli_logging._logger = saved[0]
        cli_logger.handlers[:] = saved[1]
        cli_logger.setLevel(saved[2])
        cli_logger.propagate = saved[3]
