"""Remote mode: the auth configuration, and the server's own verification of each request's token.

A remote server verifies every request's bearer token itself, before any tool or resource runs, and
then sends that same token to the VAMS API, whose authorizer and Casbin policy decide every call for
that user. Verifying here is what keeps a token the deployment never issued, or issued to another
client, from reaching a tool at all; the API's checks still apply to every call (defence in depth).

The rules follow the VAMS API authorizer (``backend/backend/common/auth/authorizerCore.py``):

* RS256 only, signed by a key of the issuer's JWKS (an unknown key id refetches the set once, at most
  every ``JWKS_REFETCH_MIN_SECONDS``); ``exp`` and ``iss`` required, ``exp`` and ``nbf`` checked.
* Amazon Cognito: ``token_use`` is ``id`` or ``access``; the app client (``aud`` of an ID token,
  ``client_id`` of an access token) is one of the configured audiences; an access token names its
  user in ``username`` (a client credentials token carries none).
* External OAuth IdP: one of the token's ``aud`` values is a configured audience, and the token names
  a user: an identity claim (``cognito:username``, ``username`` or ``sub``) that is not its own
  client identifier (``client_id``, ``azp``, ``cid``), and no app-only marker (``gty`` =
  ``client-credentials``, ``idtyp`` = ``app``, ``preferred_username`` starting ``service-account-``).

The configured audiences are this server's own clients only (the ones the operator registered for
remote MCP clients), not the web application's client, so a web-app token is not accepted here.

When the issuer's keys cannot be read, no request passes (``VerificationUnavailable``, answered 503).
Nothing here logs or returns a token, and rejection reasons go to the log as a category only.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import jwt as pyjwt
import requests

from .config import ConfigError, is_loopback_host

logger = logging.getLogger("vams_mcp.remote")

AUTH_CONFIG_VERSION = 1
PROVIDER_COGNITO = "cognito"
PROVIDER_EXTERNAL = "external"
PROVIDERS = (PROVIDER_COGNITO, PROVIDER_EXTERNAL)
# The path the server serves MCP on; the resource URL's path must be this one.
MCP_PATH = "/mcp"
# At most this many audiences, the same bound the API's additionalTokenAudiences setting has.
MAX_AUDIENCES = 10

ALLOWED_ALGORITHMS = ["RS256"]
JWKS_CACHE_SECONDS = 3600
JWKS_REFETCH_MIN_SECONDS = 60
# After a failed key-set read, requests are answered unavailable for this long without a new read.
JWKS_FAILURE_BACKOFF_SECONDS = 5
JWKS_FETCH_TIMEOUT = (5, 10)
JWKS_MAX_BYTES = 512 * 1024
# A bearer token larger than this is refused before it is parsed.
MAX_TOKEN_BYTES = 16 * 1024

_CLIENT_IDENTIFIER_CLAIMS = ("client_id", "azp", "cid")
_MACHINE_TOKEN_CLAIM_MARKERS = (("gty", "client-credentials"), ("idtyp", "app"))
_MACHINE_TOKEN_USERNAME_PREFIXES = (("preferred_username", "service-account-"),)


class VerificationUnavailable(Exception):
    """The token could not be evaluated: the issuer's keys could not be read. No request passes."""


def _check_url(name: str, value: Any, *, require_path: Optional[str] = None) -> str:
    """``value`` as an https URL (http only for a loopback host), or a ConfigError naming ``name``."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"remote auth configuration: '{name}' is missing")
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ConfigError(f"remote auth configuration: '{name}' must be an https URL, not '{value}'")
    if parts.scheme == "http" and not is_loopback_host(parts.hostname):
        raise ConfigError(
            f"remote auth configuration: '{name}' must use https (plain http is accepted for a loopback "
            f"host only), not '{value}'"
        )
    if parts.query or parts.fragment:
        raise ConfigError(f"remote auth configuration: '{name}' must not carry a query or fragment")
    if require_path is not None and parts.path.rstrip("/") != require_path:
        raise ConfigError(
            f"remote auth configuration: '{name}' must end in the path {require_path} "
            f"(for example https://mcp.example.com{require_path}), not '{value}'"
        )
    return value


@dataclass(frozen=True)
class RemoteAuthSettings:
    """What ``vams-mcp setup-remote`` writes and the remote server reads. Holds no secret."""

    api_url: str
    resource_url: str
    provider: str
    issuer: str
    jwks_uri: str
    audiences: Tuple[str, ...]
    scopes: Tuple[str, ...] = ()
    client: Dict[str, Any] = field(default_factory=dict)

    @property
    def resource_path(self) -> str:
        return urlsplit(self.resource_url).path.rstrip("/") or MCP_PATH

    @property
    def metadata_url(self) -> str:
        """The RFC 9728 Protected Resource Metadata URL of the resource (path inserted after the host)."""
        parts = urlsplit(self.resource_url)
        return f"{parts.scheme}://{parts.netloc}/.well-known/oauth-protected-resource{self.resource_path}"

    def protected_resource_metadata(self) -> Dict[str, Any]:
        """The RFC 9728 document: public discovery data only."""
        document: Dict[str, Any] = {
            "resource": self.resource_url,
            "authorization_servers": [self.issuer],
            "bearer_methods_supported": ["header"],
            "resource_name": "VAMS",
        }
        if self.scopes:
            document["scopes_supported"] = list(self.scopes)
        return document

    @classmethod
    def from_dict(cls, data: Any) -> "RemoteAuthSettings":
        if not isinstance(data, dict):
            raise ConfigError("remote auth configuration: the file must hold a JSON object")
        if data.get("version") != AUTH_CONFIG_VERSION:
            raise ConfigError(
                f"remote auth configuration: 'version' must be {AUTH_CONFIG_VERSION}; "
                "write the file again with `vams-mcp setup-remote`"
            )
        provider = data.get("provider")
        if provider not in PROVIDERS:
            raise ConfigError(f"remote auth configuration: 'provider' must be one of {', '.join(PROVIDERS)}")
        audiences = data.get("audiences")
        if (
            not isinstance(audiences, list)
            or not audiences
            or len(audiences) > MAX_AUDIENCES
            or not all(isinstance(a, str) and a.strip() and a == a.strip() for a in audiences)
        ):
            raise ConfigError(
                f"remote auth configuration: 'audiences' must list 1 to {MAX_AUDIENCES} client ids or "
                "audiences of the clients registered for remote MCP clients"
            )
        scopes = data.get("scopes") or []
        if not isinstance(scopes, list) or not all(isinstance(s, str) and s and " " not in s for s in scopes):
            raise ConfigError("remote auth configuration: 'scopes' must be a list of scope names")
        client = data.get("client") or {}
        if not isinstance(client, dict):
            raise ConfigError("remote auth configuration: 'client' must be an object")
        return cls(
            api_url=_check_url("apiUrl", data.get("apiUrl")).rstrip("/"),
            resource_url=_check_url("resourceUrl", data.get("resourceUrl"), require_path=MCP_PATH).rstrip("/"),
            provider=provider,
            issuer=_check_url("issuer", data.get("issuer")),
            jwks_uri=_check_url("jwksUri", data.get("jwksUri")),
            audiences=tuple(dict.fromkeys(audiences)),
            scopes=tuple(dict.fromkeys(scopes)),
            client=dict(client),
        )

    @classmethod
    def load(cls, path: str) -> "RemoteAuthSettings":
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            raise ConfigError(
                f"remote auth configuration file '{path}' does not exist; write it with `vams-mcp setup-remote`"
            )
        except (OSError, ValueError) as exc:
            raise ConfigError(f"remote auth configuration file '{path}' could not be read: {exc}")
        return cls.from_dict(data)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": AUTH_CONFIG_VERSION,
            "apiUrl": self.api_url,
            "resourceUrl": self.resource_url,
            "provider": self.provider,
            "issuer": self.issuer,
            "jwksUri": self.jwks_uri,
            "audiences": list(self.audiences),
            "scopes": list(self.scopes),
            "client": dict(self.client),
        }


class VerifiedIdentity:
    """The user a verified token acts for. Holds the token in memory for this request only.

    ``repr`` and ``str`` never show the token, so an identity that reaches a log or an error message
    carries no credential.
    """

    __slots__ = ("_token", "user_id", "client_id", "expires_at", "client")

    def __init__(self, token: str, user_id: str, client_id: str, expires_at: Optional[int]) -> None:
        self._token = token
        self.user_id = user_id
        self.client_id = client_id
        self.expires_at = expires_at
        # The per-request VamsClient built from the token on first use (client.RequestScopedClient).
        self.client: Any = None

    @property
    def token(self) -> str:
        return self._token

    def __repr__(self) -> str:
        return f"VerifiedIdentity(user_id={self.user_id!r}, client_id={self.client_id!r})"

    __str__ = __repr__


def _non_blank(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _is_machine_token(claims: Dict[str, Any]) -> bool:
    for claim, value in _MACHINE_TOKEN_CLAIM_MARKERS:
        if claims.get(claim) == value:
            return True
    for claim, prefix in _MACHINE_TOKEN_USERNAME_PREFIXES:
        claim_value = claims.get(claim)
        if isinstance(claim_value, str) and claim_value.startswith(prefix):
            return True
    return False


class _Rejected(Exception):
    """A token that verified as not acceptable. ``reason`` is a category for the log, never token text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TokenVerifier:
    """Verifies bearer tokens against one issuer's keys and the configured audiences.

    ``verify`` returns a ``VerifiedIdentity`` for an accepted token and None for a rejected one, and
    raises ``VerificationUnavailable`` when the issuer's keys cannot be read. Thread-safe: the gate
    calls it from worker threads.
    """

    def __init__(self, settings: RemoteAuthSettings, session: Optional[requests.Session] = None) -> None:
        self.settings = settings
        self._session = session or requests.Session()
        self._lock = threading.Lock()
        self._keys: Dict[str, Any] = {}
        self._fetched_at: Optional[float] = None
        self._last_attempt: Optional[float] = None
        self._last_failure: Optional[float] = None

    # --- Keys -------------------------------------------------------------

    def _fetch_keys(self) -> Dict[str, Any]:
        now = time.monotonic()
        if self._last_failure is not None and now - self._last_failure < JWKS_FAILURE_BACKOFF_SECONDS:
            # Keys that could not be read a moment ago are not fetched again per request: an outage
            # of the issuer must not turn every incoming request into a request to the issuer.
            raise VerificationUnavailable("the issuer's key set could not be read a moment ago")
        try:
            keys = self._read_keys()
        except VerificationUnavailable:
            self._last_failure = time.monotonic()
            raise
        self._last_failure = None
        return keys

    def _read_keys(self) -> Dict[str, Any]:
        try:
            with self._session.get(
                self.settings.jwks_uri, timeout=JWKS_FETCH_TIMEOUT, allow_redirects=False, stream=True
            ) as response:
                if response.status_code != 200:
                    raise VerificationUnavailable(f"the issuer's key set answered HTTP {response.status_code}")
                body = response.raw.read(JWKS_MAX_BYTES + 1, decode_content=True)
            if len(body) > JWKS_MAX_BYTES:
                raise VerificationUnavailable("the issuer's key set is larger than the accepted size")
            document = json.loads(body)
        except VerificationUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure to read the keys is an outage
            raise VerificationUnavailable(f"the issuer's key set could not be read ({type(exc).__name__})")
        keys: Dict[str, Any] = {}
        for jwk in document.get("keys", []) if isinstance(document, dict) else []:
            if not isinstance(jwk, dict) or jwk.get("kty") != "RSA" or not _non_blank(jwk.get("kid")):
                continue
            if jwk.get("use") not in (None, "sig") or jwk.get("alg") not in (None, "RS256"):
                continue
            try:
                keys[jwk["kid"]] = pyjwt.PyJWK(jwk, algorithm="RS256").key
            except Exception:  # noqa: BLE001 - a malformed key is skipped, not trusted
                continue
        if not keys:
            raise VerificationUnavailable("the issuer's key set holds no usable RS256 signing key")
        return keys

    def _key_for(self, kid: str) -> Any:
        now = time.monotonic()
        with self._lock:
            fresh = self._fetched_at is not None and now - self._fetched_at < JWKS_CACHE_SECONDS
            if fresh and kid in self._keys:
                return self._keys[kid]
            # A kid the cached set lacks is what a key rotation looks like: refetch, but not more
            # often than JWKS_REFETCH_MIN_SECONDS, so unknown kids cannot drive a fetch per request.
            if fresh and self._last_attempt is not None and now - self._last_attempt < JWKS_REFETCH_MIN_SECONDS:
                return None
            self._last_attempt = now
            self._keys = self._fetch_keys()
            self._fetched_at = time.monotonic()
            return self._keys.get(kid)

    def prefetch(self) -> None:
        """Read the issuer's keys now (at start), raising VerificationUnavailable when they cannot be."""
        with self._lock:
            self._last_attempt = time.monotonic()
            self._keys = self._fetch_keys()
            self._fetched_at = time.monotonic()

    # --- Claims -----------------------------------------------------------

    def _cognito_identity(self, claims: Dict[str, Any]) -> Tuple[str, str]:
        token_use = claims.get("token_use")
        if token_use not in ("id", "access"):
            raise _Rejected("token_use")
        audience = claims.get("aud") if token_use == "id" else claims.get("client_id")
        if not isinstance(audience, str) or audience not in self.settings.audiences:
            raise _Rejected("audience")
        user = claims.get("cognito:username") if token_use == "id" else claims.get("username")
        if not _non_blank(user):
            # A client credentials (machine-to-machine) access token names no user.
            raise _Rejected("no_user")
        return user, audience

    def _external_identity(self, claims: Dict[str, Any]) -> Tuple[str, str]:
        aud = claims.get("aud")
        candidates = [aud] if isinstance(aud, str) else (aud if isinstance(aud, list) else [])
        matched = next((a for a in candidates if isinstance(a, str) and a in self.settings.audiences), None)
        if matched is None:
            raise _Rejected("audience")
        identity = claims.get("cognito:username") or claims.get("username") or claims.get("sub")
        if not _non_blank(identity) or _is_machine_token(claims):
            raise _Rejected("no_user")
        client_ids = {claims.get(c) for c in _CLIENT_IDENTIFIER_CLAIMS if _non_blank(claims.get(c))}
        if identity in client_ids:
            raise _Rejected("no_user")
        return identity, matched

    def verify(self, token: str) -> Optional[VerifiedIdentity]:
        try:
            if not isinstance(token, str) or not token or len(token.encode("utf-8")) > MAX_TOKEN_BYTES:
                raise _Rejected("malformed")
            try:
                header = pyjwt.get_unverified_header(token)
            except pyjwt.PyJWTError:
                raise _Rejected("malformed")
            if header.get("alg") not in ALLOWED_ALGORITHMS:
                raise _Rejected("algorithm")
            kid = header.get("kid")
            if not _non_blank(kid):
                raise _Rejected("malformed")
            key = self._key_for(kid)
            if key is None:
                raise _Rejected("unknown_key")
            external = self.settings.provider == PROVIDER_EXTERNAL
            try:
                claims = pyjwt.decode(
                    token,
                    key,
                    algorithms=ALLOWED_ALGORITHMS,
                    issuer=self.settings.issuer,
                    # A Cognito access token carries its client in client_id, not aud, so the audience
                    # is checked against the token_use-specific claim in _cognito_identity instead.
                    audience=list(self.settings.audiences) if external else None,
                    options={
                        "require": ["exp", "iss"],
                        "verify_signature": True,
                        "verify_exp": True,
                        "verify_nbf": True,
                        "verify_iss": True,
                        "verify_aud": external,
                    },
                )
            except pyjwt.ExpiredSignatureError:
                raise _Rejected("expired")
            except pyjwt.InvalidAudienceError:
                raise _Rejected("audience")
            except pyjwt.InvalidIssuerError:
                raise _Rejected("issuer")
            except pyjwt.InvalidSignatureError:
                raise _Rejected("signature")
            except pyjwt.PyJWTError:
                raise _Rejected("invalid")
            user, client_id = (self._external_identity if external else self._cognito_identity)(claims)
            expires_at = claims.get("exp") if isinstance(claims.get("exp"), int) else None
            return VerifiedIdentity(token, user, client_id, expires_at)
        except _Rejected as rejected:
            logger.info("[vams-mcp] rejected a bearer token: %s", rejected.reason)
            return None
