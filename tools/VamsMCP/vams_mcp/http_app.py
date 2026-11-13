"""Remote mode: the HTTP application around the MCP SDK's streamable HTTP transport.

``RemoteGate`` is an ASGI application in front of the SDK's stateless streamable HTTP app:

* ``GET /.well-known/oauth-protected-resource/mcp`` (and the same document at
  ``/.well-known/oauth-protected-resource``) returns the RFC 9728 Protected Resource Metadata. It is
  the only route answered without a token, and it holds public discovery data only.
* Every other request needs ``Authorization: Bearer <token>``. The gate verifies the token itself
  (``remote_auth.TokenVerifier``) before the SDK sees the request. No token, or a token that does not
  verify: ``401`` with ``WWW-Authenticate: Bearer resource_metadata="..."``. Keys that cannot be read:
  ``503`` and nothing passes.
* A verified request runs with its identity in ``request_context``, so each tool call builds its
  VAMS API client from that request's own token, and the VAMS API's authorizer and Casbin policy
  decide every call for that user.

The gate is ours rather than the SDK's auth middleware because the SDK's resource-server wiring
differs across the supported ``mcp`` range (1.10.x serves the metadata at a path its own
``WWW-Authenticate`` challenge does not point to, later releases fix it, and 2.x moved the options
onto ``streamable_http_app``), while the server must answer the same way on every release. The SDK
still carries the MCP protocol itself, including its DNS rebinding protection, configured here.

Nothing here logs a token, a request body or a query string. Each request writes one line to the
``vams_mcp.remote`` logger (user, client, status, duration), and each tool call one more
(``server.tool_result``).
"""

from __future__ import annotations

import inspect
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import anyio

from . import request_context
from .config import Config, is_loopback_host
from .remote_auth import MCP_PATH, RemoteAuthSettings, TokenVerifier, VerificationUnavailable

logger = logging.getLogger("vams_mcp.remote")

Scope = Dict[str, Any]
Receive = Callable[[], Awaitable[Dict[str, Any]]]
Send = Callable[[Dict[str, Any]], Awaitable[None]]

# The largest request body accepted. An MCP tool call is a small JSON-RPC message.
MAX_BODY_BYTES = 1024 * 1024
WELL_KNOWN = "/.well-known/oauth-protected-resource"
# Worker threads for token verification, apart from the ones tool calls run on (server.py).
VERIFY_THREADS = 16


def _json_response(status: int, body: Dict[str, Any], headers: Optional[List[Tuple[bytes, bytes]]] = None):
    payload = json.dumps(body).encode("utf-8")
    start = {
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode("ascii")),
            (b"cache-control", b"no-store"),
            *(headers or []),
        ],
    }
    return start, {"type": "http.response.body", "body": payload}


def _bearer_token(scope: Scope) -> Optional[str]:
    """The token of an ``Authorization: Bearer <token>`` header, or None."""
    values = [value for name, value in scope.get("headers", []) if name.lower() == b"authorization"]
    if len(values) != 1:
        return None
    try:
        text = values[0].decode("latin-1")
    except Exception:  # noqa: BLE001
        return None
    scheme, _, token = text.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or " " in token:
        return None
    return token


class RemoteGate:
    """Verifies every request's token before the MCP SDK sees it (see the module docstring)."""

    def __init__(
        self,
        inner: Callable[..., Awaitable[None]],
        verifier: TokenVerifier,
        settings: RemoteAuthSettings,
        served_path: str = MCP_PATH,
    ):
        self.inner = inner
        self.verifier = verifier
        self.settings = settings
        self.served_path = served_path
        self.mcp_paths = {MCP_PATH, MCP_PATH + "/"}
        self.metadata_paths = {WELL_KNOWN + settings.resource_path, WELL_KNOWN}
        self.metadata_body = settings.protected_resource_metadata()
        self.challenge = f'Bearer resource_metadata="{settings.metadata_url}"'

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            # The SDK's app runs its session manager in its lifespan.
            await self.inner(scope, receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        if scope.get("path") in self.metadata_paths:
            await self._metadata(scope, send)
            return
        await self._authenticated(scope, receive, send)

    async def _send(self, send: Send, status: int, body: Dict[str, Any], headers=None) -> None:
        start, payload = _json_response(status, body, headers)
        await send(start)
        await send(payload)

    async def _metadata(self, scope: Scope, send: Send) -> None:
        cors = [
            (b"access-control-allow-origin", b"*"),
            (b"access-control-allow-methods", b"GET, OPTIONS"),
        ]
        method = scope.get("method")
        if method == "OPTIONS":
            await send({"type": "http.response.start", "status": 204, "headers": cors})
            await send({"type": "http.response.body", "body": b""})
        elif method == "GET":
            await self._send(send, 200, self.metadata_body, cors)
        else:
            await self._send(send, 405, {"error": "method_not_allowed"}, [(b"allow", b"GET, OPTIONS")])

    async def _authenticated(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = time.monotonic()
        token = _bearer_token(scope)
        if token is None:
            await self._send(
                send, 401, {"error": "unauthorized", "error_description": "A bearer token is required."},
                [(b"www-authenticate", self.challenge.encode("latin-1"))],
            )
            return
        try:
            # On its own thread limiter, so verification never waits behind tool calls.
            identity = await anyio.to_thread.run_sync(self.verifier.verify, token, limiter=_verify_limiter())
        except VerificationUnavailable as exc:
            logger.warning("[vams-mcp] cannot verify tokens: %s", exc)
            await self._send(
                send, 503,
                {"error": "temporarily_unavailable", "error_description": "Sign-in cannot be verified right now."},
                [(b"retry-after", b"30")],
            )
            return
        if identity is None:
            challenge = (
                f'Bearer error="invalid_token", error_description="The access token is not valid", '
                f'resource_metadata="{self.settings.metadata_url}"'
            )
            await self._send(
                send, 401, {"error": "invalid_token", "error_description": "The access token is not valid."},
                [(b"www-authenticate", challenge.encode("latin-1"))],
            )
            return

        declared = next((v for n, v in scope.get("headers", []) if n.lower() == b"content-length"), None)
        if declared is not None:
            try:
                too_large = int(declared) > MAX_BODY_BYTES
            except ValueError:
                too_large = True
            if too_large:
                await self._send(send, 413, {"error": "request_too_large"})
                return

        # The whole body is read here, before the SDK sees the request, so a body past the limit is
        # answered 413 whether it declared its length or arrived in chunks.
        body = await _read_body(receive)
        if body is None:
            await self._send(send, 413, {"error": "request_too_large"})
            logger.info(
                "[vams-mcp] request user=%s client=%s method=%s status=413 ms=%d",
                identity.user_id, identity.client_id, scope.get("method"), (time.monotonic() - started) * 1000,
            )
            return
        delivered = False

        async def replay_receive() -> Dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        status = {"code": 500}

        async def tracking_send(message: Dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                status["code"] = message.get("status", 500)
            await send(message)

        try:
            if scope.get("path") in self.mcp_paths and scope.get("path") != self.served_path:
                scope = {**scope, "path": self.served_path, "raw_path": self.served_path.encode("ascii")}
            with request_context.acting_for(identity):
                await self.inner(scope, replay_receive, tracking_send)
        finally:
            logger.info(
                "[vams-mcp] request user=%s client=%s method=%s status=%s ms=%d",
                identity.user_id, identity.client_id, scope.get("method"), status["code"],
                (time.monotonic() - started) * 1000,
            )


_VERIFY_LIMITER: Any = None


def _verify_limiter() -> Any:
    """The token-verification thread limiter of the running event loop."""
    from anyio.lowlevel import RunVar

    global _VERIFY_LIMITER
    if _VERIFY_LIMITER is None:
        _VERIFY_LIMITER = RunVar("vams_mcp_verify_limiter")
    try:
        return _VERIFY_LIMITER.get()
    except LookupError:
        limiter = anyio.CapacityLimiter(VERIFY_THREADS)
        _VERIFY_LIMITER.set(limiter)
        return limiter


async def _read_body(receive: Receive) -> Optional[bytes]:
    """The request body, or None once it passes MAX_BODY_BYTES (reading stops there)."""
    chunks: List[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break  # the client disconnected
        chunk = message.get("body", b"") or b""
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def transport_security_settings(settings: RemoteAuthSettings, config: Config):
    """The SDK's DNS rebinding protection, allowing the resource's own host and this machine's names."""
    from mcp.server.transport_security import TransportSecuritySettings

    resource = urlsplit(settings.resource_url)
    hosts = {resource.netloc, f"{resource.hostname}:*", "127.0.0.1:*", "localhost:*", "[::1]:*"}
    origins = {f"{resource.scheme}://{resource.netloc}", "http://127.0.0.1:*", "http://localhost:*"}
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(hosts),
        allowed_origins=sorted(origins),
    )


def sdk_streamable_app(mcp: Any, settings: RemoteAuthSettings, config: Config) -> Tuple[Any, str]:
    """The SDK's stateless streamable HTTP app with JSON responses, on any supported ``mcp`` release,
    and the exact path it serves MCP on."""
    security = transport_security_settings(settings, config)
    builder = mcp.streamable_http_app
    if "stateless_http" in inspect.signature(builder).parameters:  # mcp 2.x takes the options here
        app = builder(stateless_http=True, json_response=True, transport_security=security, host=config.host)
    else:
        # mcp 1.x reads them from its settings.
        mcp.settings.stateless_http = True
        mcp.settings.json_response = True
        mcp.settings.transport_security = security
        app = builder()
    return app, _served_path(app)


def _served_path(app: Any) -> str:
    """``/mcp/`` when the SDK mounts its handler (mcp 1.10.x), ``/mcp`` when it routes it.

    Starlette answers the other spelling with a 307 redirect, which a client may not follow with its
    POST body and token, so the gate hands the SDK the spelling it serves.
    """
    from starlette.routing import Mount

    for route in getattr(app, "routes", []):
        if getattr(route, "path", None) == MCP_PATH:
            return MCP_PATH + "/" if isinstance(route, Mount) else MCP_PATH
    return MCP_PATH


def build_app(mcp: Any, settings: RemoteAuthSettings, config: Config, verifier: Optional[TokenVerifier] = None):
    """The whole remote application: the gate in front of the SDK's app."""
    inner, served_path = sdk_streamable_app(mcp, settings, config)
    return RemoteGate(inner, verifier or TokenVerifier(settings), settings, served_path)


def check_exposure(settings: RemoteAuthSettings, config: Config) -> None:
    """Refuse a listener beyond this machine whose public URL is not https.

    A bind beyond loopback is an explicit operator setting (VAMS_MCP_HOST) for a container or private
    network behind an HTTPS front end; the resource URL clients use must then be https.
    """
    from .config import ConfigError

    if not is_loopback_host(config.host) and urlsplit(settings.resource_url).scheme != "https":
        raise ConfigError(
            "VAMS_MCP_HOST binds beyond this machine, so the resource URL must be the https URL of the "
            f"front end that terminates TLS, not '{settings.resource_url}'"
        )


def run(mcp: Any, settings: RemoteAuthSettings, config: Config) -> None:
    """Serve the remote application with uvicorn until stopped."""
    import uvicorn

    check_exposure(settings, config)
    verifier = TokenVerifier(settings)
    try:
        verifier.prefetch()
    except VerificationUnavailable as exc:
        # Served fail closed: every request is answered 503 until the keys can be read.
        logger.warning("[vams-mcp] the issuer's keys cannot be read yet (%s); requests are refused until they can", exc)
    app = build_app(mcp, settings, config, verifier)
    logger.warning(
        "[vams-mcp] serving remote MCP on http://%s:%d%s for %s (tool sets: %s)",
        config.host, config.port, settings.resource_path, settings.resource_url, ",".join(config.toolsets),
    )
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_config=None,
        access_log=False,
        server_header=False,
        lifespan="on",
    )
