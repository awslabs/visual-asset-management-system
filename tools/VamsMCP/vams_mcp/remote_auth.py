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
* External OAuth IdP: the token's audience, read through the configured audience claims (``aud`` by
  default, or ``client_id`` or both; the first claim of the list the token carries decides, and a later
  claim cannot rescue a mismatch), is a configured audience; and the token names a user: the configured
  user id claim when one is set (that claim only), else the first of ``cognito:username``, ``username``
  and ``sub``. That identity must not be the token's own client identifier (``client_id``, ``azp``,
  ``cid``), and the token must carry no app-only marker (``gty`` = ``client-credentials``, ``idtyp`` =
  ``app``, ``preferred_username`` starting ``service-account-``). The two claim settings are the
  deployment's ``lambdaAuthorizorJWTAudienceClaims`` and ``lambdaAuthorizorJWTUserIdClaim``;
  ``tests/test_remote_auth_parity.py`` runs one set of token fixtures through this verifier and the
  authorizer and requires the same outcome.

The configured audiences are this server's own clients only (the ones the operator registered for
remote MCP clients), not the web application's client, so a web-app token is not accepted here.

When the issuer's keys cannot be read, no request passes (``VerificationUnavailable``, answered 503).
Nothing here logs or returns a token, and rejection reasons go to the log as a category only.
"""

from __future__ import annotations

import json
import logging
import re
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

# The claims an external token's audience may be read from, and the default (the authorizer's own).
SUPPORTED_AUDIENCE_CLAIMS = ("aud", "client_id")
DEFAULT_AUDIENCE_CLAIMS = ("aud",)
# A user id claim name, as the authorizer and the deployment's configuration accept it; the reserved
# 'vams:' prefix (any letter case) is refused.
USER_ID_CLAIM_PATTERN = re.compile(r"^(?!vams:)[A-Za-z0-9_:.\-]+$", re.IGNORECASE)

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


def check_audience_claims(value: Any) -> Tuple[str, ...]:
    """``value`` as a list of distinct audience claim names the authorizer supports, else a ConfigError."""
    if (
        not isinstance(value, (list, tuple))
        or not value
        or any(name not in SUPPORTED_AUDIENCE_CLAIMS for name in value)
        or len(set(value)) != len(value)
    ):
        raise ConfigError(
            "remote auth configuration: 'audienceClaims' must list distinct claim names from "
            f"{', '.join(SUPPORTED_AUDIENCE_CLAIMS)}"
        )
    return tuple(value)


def check_user_id_claim(value: Any) -> str:
    """``value`` as a user id claim name (not under the reserved 'vams:' prefix), else a ConfigError."""
    if not isinstance(value, str) or not USER_ID_CLAIM_PATTERN.fullmatch(value):
        raise ConfigError(
            "remote auth configuration: 'userIdClaim' must be a claim name of letters, digits and _ : . - "
            "that does not start with 'vams:'"
        )
    return value


def _parse_claim_settings(data: Dict[str, Any], provider: str) -> Tuple[Tuple[str, ...], Optional[str]]:
    """The audience claims and user id claim of a settings file (the defaults when absent).

    They apply to an external IdP only; a Cognito file naming them is refused rather than ignored.
    """
    has_audience_claims = data.get("audienceClaims") is not None
    has_user_id_claim = data.get("userIdClaim") not in (None, "")
    if provider != PROVIDER_EXTERNAL:
        if has_audience_claims or has_user_id_claim:
            raise ConfigError(
                "remote auth configuration: 'audienceClaims' and 'userIdClaim' apply to an external "
                "IdP only"
            )
        return DEFAULT_AUDIENCE_CLAIMS, None
    audience_claims = (
        check_audience_claims(data["audienceClaims"]) if has_audience_claims else DEFAULT_AUDIENCE_CLAIMS
    )
    user_id_claim = check_user_id_claim(data["userIdClaim"]) if has_user_id_claim else None
    return audience_claims, user_id_claim


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
    # The web application's own client, recorded by `vams-mcp setup-remote`; never an audience.
    web_app_client_id: Optional[str] = None
    # External IdP only: the token claims the audience is read from, and the claim the user id is read
    # from (None: the first of cognito:username, username, sub). Absent in the file = the defaults.
    audience_claims: Tuple[str, ...] = DEFAULT_AUDIENCE_CLAIMS
    user_id_claim: Optional[str] = None

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
        web_app_client_id = data.get("webAppClientId") or None
        if web_app_client_id is not None and not isinstance(web_app_client_id, str):
            raise ConfigError("remote auth configuration: 'webAppClientId' must be a string")
        if web_app_client_id and web_app_client_id in audiences:
            raise ConfigError(
                "remote auth configuration: 'audiences' lists the web application's own client "
                f"('{web_app_client_id}'); the remote server accepts only the clients registered for remote "
                "MCP clients. Remove it, or write the file again with `vams-mcp setup-remote`"
            )
        audience_claims, user_id_claim = _parse_claim_settings(data, provider)
        return cls(
            api_url=_check_url("apiUrl", data.get("apiUrl")).rstrip("/"),
            resource_url=_check_url("resourceUrl", data.get("resourceUrl"), require_path=MCP_PATH).rstrip("/"),
            provider=provider,
            issuer=_check_url("issuer", data.get("issuer")),
            jwks_uri=_check_url("jwksUri", data.get("jwksUri")),
            audiences=tuple(dict.fromkeys(audiences)),
            scopes=tuple(dict.fromkeys(scopes)),
            client=dict(client),
            web_app_client_id=web_app_client_id,
            audience_claims=audience_claims,
            user_id_claim=user_id_claim,
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
            **({"webAppClientId": self.web_app_client_id} if self.web_app_client_id else {}),
            **(
                {"audienceClaims": list(self.audience_claims)}
                if self.audience_claims != DEFAULT_AUDIENCE_CLAIMS
                else {}
            ),
            **({"userIdClaim": self.user_id_claim} if self.user_id_claim else {}),
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
        self._fetch_lock = threading.Lock()
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
        # The cache lock is held only to read or swap the cached set, never across the key-set
        # download: a refetch (up to the fetch timeout) must not hold up the verification of tokens
        # whose key is already cached. Downloads are single-flight under _fetch_lock.
        now = time.monotonic()
        with self._lock:
            fresh = self._fetched_at is not None and now - self._fetched_at < JWKS_CACHE_SECONDS
            if fresh and kid in self._keys:
                return self._keys[kid]
        with self._fetch_lock:
            now = time.monotonic()
            with self._lock:
                fresh = self._fetched_at is not None and now - self._fetched_at < JWKS_CACHE_SECONDS
                if fresh and kid in self._keys:
                    return self._keys[kid]  # another thread fetched it meanwhile
                # A kid the cached set lacks is what a key rotation looks like: refetch, but not more
                # often than JWKS_REFETCH_MIN_SECONDS, so unknown kids cannot drive a fetch per request.
                if fresh and self._last_attempt is not None and now - self._last_attempt < JWKS_REFETCH_MIN_SECONDS:
                    return None
                self._last_attempt = now
            keys = self._fetch_keys()
            with self._lock:
                self._keys = keys
                self._fetched_at = time.monotonic()
                return keys.get(kid)

    def prefetch(self) -> None:
        """Read the issuer's keys now (at start), raising VerificationUnavailable when they cannot be."""
        with self._fetch_lock:
            self._last_attempt = time.monotonic()
            keys = self._fetch_keys()
            with self._lock:
                self._keys = keys
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

    def _matched_audience(self, claims: Dict[str, Any]) -> Optional[str]:
        """The configured audience the token names, read through the configured audience claims.

        The first claim of the list the token carries decides; it must name a configured audience
        (``aud`` may be a string or a list of strings, ``client_id`` a string), and a later claim cannot
        rescue a mismatch. Exact string comparison, as in the authorizer.
        """
        for claim_name in self.settings.audience_claims:
            if claim_name in claims:
                value = claims[claim_name]
                if claim_name == "aud" and isinstance(value, list):
                    values = list(value) if all(isinstance(item, str) for item in value) else []
                else:
                    values = [value] if isinstance(value, str) else []
                return next((a for a in self.settings.audiences if a in values), None)
        return None

    def _external_identity(self, claims: Dict[str, Any]) -> Tuple[str, str]:
        matched = self._matched_audience(claims)
        if matched is None:
            raise _Rejected("audience")
        if self.settings.user_id_claim:
            identity = claims.get(self.settings.user_id_claim)
        else:
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
                    # The audience is checked after the signature, through the claim(s) the provider
                    # reads it from (_cognito_identity, _external_identity); PyJWT reads only 'aud'.
                    audience=None,
                    options={
                        "require": ["exp", "iss"],
                        "verify_signature": True,
                        "verify_exp": True,
                        "verify_nbf": True,
                        "verify_iss": True,
                        "verify_aud": False,
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
