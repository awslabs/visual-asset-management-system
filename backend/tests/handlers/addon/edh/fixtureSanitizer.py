# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sanitizer for captured EDH API exchanges stored under ``fixtures/``.

Each fixture is one request/response envelope::

    {
      "name": "virtual_desktops_create",
      "capturedAt": "2026-09-10T14:02:11Z",
      "edhVersion": "26.4.0",
      "request":  {"method": "POST", "path": "/dcv/virtual_desktops/create",
                   "absolutePath": "/api/v1/dcv/virtual_desktops/create", "query": {},
                   "headers": {"X-EDH-USER": "vspikeaaaaaaaaaa"}, "tokenHeaderName": "X-EDH-TOKEN",
                   "body": {...}},
      "response": {"status": 200, "contentType": "application/json",
                   "elapsedSeconds": 1.234, "json": {...}},
      "expect":   {"sessionUuid": "..."}
    }

``request.path`` is relative to the ``--base-path`` when the path sent starts with it and the path
as sent otherwise; ``response.json`` is the EDH body as returned; ``expect`` carries the values a
consumer test asserts. The token header is recorded by name only; the root key is read from the
environment solely to refuse an output that still contains it. Sanitization is a fixed point on its
own output, so a committed fixture is re-verified by running it through ``sanitize_document`` again.

Usage (from ``backend/``)::

    python -m tests.handlers.addon.edh.fixtureSanitizer --name NAME --method GET --path /api/v1/...
        --base-path /api/v1 --status 200 --elapsed 0.42 --response-body raw.json
        --expect count=3 --out tests/.../fixtures/NAME.json
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

REDACTED = "<redacted>"
PLACEHOLDER_API_HOST = "edh-api.example.internal"
PLACEHOLDER_OTHER_HOST = "other.example.internal"
PLACEHOLDER_ACCOUNT = "123456789012"
PLACEHOLDER_IPV4 = "10.0.0.1"
PLACEHOLDER_EMAIL = "user@example.com"
PLACEHOLDER_HOSTS = frozenset({PLACEHOLDER_API_HOST, PLACEHOLDER_OTHER_HOST})
SANITIZED_ID_MARK = "00000"

SECRET_KEY_NAMES = frozenset(
    {
        "x-edh-token",
        "authtoken",
        "auth_token",
        "token",
        "api_root_key",
        "rootkey",
        "root_key",
        "password",
        "secret",
        "authorization",
        "cookie",
        "set-cookie",
        "apikey",
        "api_key",
        "x-api-key",
        "sessiontoken",
        "session_token",
        "authentication_token",
        "session_local_admin_password",
        "broker_session_id",
        "accesskeyid",
        "secretaccesskey",
        "bearer",
        "privatekey",
        "credentials",
    }
)

_AUTH_TOKEN_QUERY = re.compile(r"(authToken=)[^&#\s]+")
_ECR_REGISTRY = re.compile(r"\b\d{12}(\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com)\b", re.IGNORECASE)
_HOSTNAME = re.compile(
    r"\b(?:[A-Za-z0-9-]+\.)+(?:amazonaws\.com|internal|local|localdomain)\b", re.IGNORECASE
)
_ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
_IPV4 = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
_AWS_ID = re.compile(r"\b(i|ami|vol|sg|subnet|vpc|eni|snap|lt)-([0-9a-f]{8,17})\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


_ID_TAIL_ALPHABET = "abcdef"
_SANITIZED_ID = re.compile(rf"^{SANITIZED_ID_MARK}[{_ID_TAIL_ALPHABET}]{{12}}$")
_PLACEHOLDER_ECR_REGISTRY = re.compile(
    rf"{PLACEHOLDER_ACCOUNT}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com", re.IGNORECASE
)


def _replace_aws_id(match: "re.Match[str]") -> str:
    prefix, ident = match.group(1), match.group(2)
    if _SANITIZED_ID.match(ident):
        return match.group(0)
    digest = hashlib.sha256(ident.encode("utf-8")).hexdigest()
    # letters only: the replacement can never contain a digit run the account-id rule would match
    tail = "".join(_ID_TAIL_ALPHABET[int(ch, 16) % len(_ID_TAIL_ALPHABET)] for ch in digest[:12])
    return f"{prefix}-{SANITIZED_ID_MARK}{tail}"


def _replace_ecr_registry(match: "re.Match[str]") -> str:
    # only the account id in front of the registry is a deployment identifier; the region stays
    return PLACEHOLDER_ACCOUNT + match.group(1)


def _replace_hostname(match: "re.Match[str]") -> str:
    host = match.group(0)
    if host.lower() in PLACEHOLDER_HOSTS or _PLACEHOLDER_ECR_REGISTRY.fullmatch(host):
        return host
    return PLACEHOLDER_OTHER_HOST


def sanitize_string(value: str, host_map: Optional[Dict[str, str]] = None) -> str:
    for host, placeholder in (host_map or {}).items():
        value = re.sub(re.escape(host), placeholder, value, flags=re.IGNORECASE)
    value = _AUTH_TOKEN_QUERY.sub(r"\g<1>" + REDACTED, value)
    value = _EMAIL.sub(PLACEHOLDER_EMAIL, value)
    # runs ahead of the hostname rule, which recognizes the rewritten registry and leaves it whole
    value = _ECR_REGISTRY.sub(_replace_ecr_registry, value)
    value = _HOSTNAME.sub(_replace_hostname, value)
    value = _AWS_ID.sub(_replace_aws_id, value)
    value = _IPV4.sub(PLACEHOLDER_IPV4, value)
    value = _ACCOUNT_ID.sub(PLACEHOLDER_ACCOUNT, value)
    return value


def sanitize_document(doc: Any, host_map: Optional[Dict[str, str]] = None) -> Any:
    if isinstance(doc, dict):
        out: Dict[Any, Any] = {}
        for key, value in doc.items():
            if str(key).lower() in SECRET_KEY_NAMES:
                out[key] = REDACTED
            else:
                out[key] = sanitize_document(value, host_map)
        return out
    if isinstance(doc, list):
        return [sanitize_document(item, host_map) for item in doc]
    if isinstance(doc, str):
        return sanitize_string(doc, host_map)
    return doc


def load_body(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}


def relative_path(path: str, base_path: str) -> str:
    base = base_path.rstrip("/")
    if base and path.startswith(base):
        return path[len(base):] or "/"
    return path


def parse_expect(pairs: Iterable[str]) -> Dict[str, Any]:
    parsed: Dict[str, Any] = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        try:
            parsed[key] = json.loads(value)
        except json.JSONDecodeError:
            parsed[key] = value
    return parsed


def build_envelope(
    *,
    name: str,
    method: str,
    path: str,
    status: int,
    elapsed_seconds: float,
    response_body: Any,
    base_path: str = "",
    request_body: Any = None,
    edh_uid: Optional[str] = None,
    query: Optional[Dict[str, str]] = None,
    expect: Optional[Dict[str, Any]] = None,
    content_type: Optional[str] = None,
    edh_version: str = "",
    captured_at: Optional[str] = None,
    user_header_name: str = "X-EDH-USER",
    token_header_name: str = "X-EDH-TOKEN",
) -> dict:
    headers = {user_header_name: edh_uid} if edh_uid else {}
    return {
        "name": name,
        "capturedAt": captured_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "edhVersion": edh_version,
        "request": {
            "method": method.upper(),
            "path": relative_path(path, base_path),
            "absolutePath": path,
            "query": dict(query or {}),
            "headers": headers,
            "tokenHeaderName": token_header_name,
            "body": request_body,
        },
        "response": {
            "status": int(status),
            "contentType": content_type or "",
            "elapsedSeconds": round(float(elapsed_seconds), 3),
            "json": response_body,
        },
        "expect": dict(expect or {}),
    }


def host_map_from_environment(environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    environ = os.environ if environ is None else environ
    endpoint = environ.get("EDH_API_ENDPOINT", "")
    host = urlsplit(endpoint).hostname if endpoint else None
    return {host: PLACEHOLDER_API_HOST} if host else {}


def leaked_values(serialized: str, environ: Optional[Dict[str, str]] = None) -> List[str]:
    environ = os.environ if environ is None else environ
    leaks: List[str] = []
    root_key = environ.get("EDH_ROOT_KEY", "")
    if root_key and root_key in serialized:
        leaks.append("EDH_ROOT_KEY")
    lowered = serialized.lower()
    for host in host_map_from_environment(environ):
        if host.lower() in lowered:
            leaks.append("EDH_API_ENDPOINT host")
    return leaks


def _parse_pairs(pairs: Iterable[str], default_value: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        parsed[key] = value or default_value
    return parsed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sanitize one captured EDH exchange into a fixture envelope."
    )
    parser.add_argument("--name", required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--base-path", default=urlsplit(os.environ.get("EDH_API_ENDPOINT", "")).path)
    parser.add_argument("--status", required=True, type=int)
    parser.add_argument("--elapsed", required=True, type=float)
    parser.add_argument("--response-body", required=True, type=Path)
    parser.add_argument("--request-body", type=Path)
    parser.add_argument("--edh-uid")
    parser.add_argument("--query", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--expect", action="append", default=[], metavar="KEY=JSON_OR_STRING")
    parser.add_argument("--content-type", default="")
    parser.add_argument("--edh-version", default=os.environ.get("EDH_VERSION", ""))
    parser.add_argument("--user-header-name", default="X-EDH-USER")
    parser.add_argument("--token-header-name", default="X-EDH-TOKEN")
    parser.add_argument("--replace-host", action="append", default=[], metavar="HOST=PLACEHOLDER")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    # the management host always maps to the API placeholder, whatever --replace-host says about it
    host_map = _parse_pairs(args.replace_host, PLACEHOLDER_OTHER_HOST)
    host_map.update(host_map_from_environment())
    envelope = build_envelope(
        name=args.name,
        method=args.method,
        path=args.path,
        base_path=args.base_path,
        status=args.status,
        elapsed_seconds=args.elapsed,
        response_body=load_body(args.response_body),
        request_body=load_body(args.request_body) if args.request_body else None,
        edh_uid=args.edh_uid,
        query=_parse_pairs(args.query, ""),
        expect=parse_expect(args.expect),
        content_type=args.content_type,
        edh_version=args.edh_version,
        user_header_name=args.user_header_name,
        token_header_name=args.token_header_name,
    )
    serialized = json.dumps(sanitize_document(envelope, host_map), indent=2) + "\n"
    leaks = leaked_values(serialized)
    if leaks:
        print(f"refusing to write {args.out}: output still contains {', '.join(leaks)}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(serialized, encoding="utf-8")
    print(f"wrote {args.out} ({args.status}, {args.elapsed:.3f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
