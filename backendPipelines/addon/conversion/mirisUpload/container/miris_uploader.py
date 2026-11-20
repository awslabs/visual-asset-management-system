#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Miris HTTP client for the upload pipeline.

The upload flow:

  1. POST   /v1/asset                  -> start_upload (returns id + temporary upload credentials)
  2. (the caller does the SigV4 S3 PUT to those temporary credentials out-of-band)
  3. PUT    /v1/asset/upload/{id}      -> mark_upload_complete (status=completed)
  4. GET    /v1/asset/{id}             -> poll_until_terminal
  5. POST   /v1/asset/{id}/generate    -> trigger_generate_best_effort

Auth uses the `Miris-API-Key` header. Redirects are never followed, so the key header never
reaches another host; a 3xx response fails the call like any other non-2xx response.

Promotion to `streamable` is a separate processing step after `preview`: the pipeline polls until
`preview` (or already-`streamable`) and then asks for promotion best effort, with manual promotion
in the Miris Portal as the fallback.

Idempotent calls (GET, PUT) are retried with exponential backoff on a transient request
failure (a connection error, a timeout, a response cut off mid-body), 429 or a 5xx. POST /v1/asset is not: it creates an asset, so a retry after a lost
response would create a second one.
"""
import random
import time
from typing import Callable, Optional

import requests

# Per Miris REST API reference, terminal asset states are:
#   preview, streamable, error, failed
TERMINAL_SUCCESS_STATES = ("preview", "streamable")
TERMINAL_ERROR_STATES = ("error", "failed")

RETRYABLE_STATUS_CODES = (429, 500, 502, 503, 504)
# Request failures that a retry cannot fix: the request itself is malformed.
NON_TRANSIENT_REQUEST_ERRORS = (
    requests.exceptions.URLRequired,
    requests.exceptions.MissingSchema,
    requests.exceptions.InvalidSchema,
    requests.exceptions.InvalidURL,
    requests.exceptions.InvalidHeader,
    requests.exceptions.TooManyRedirects,
)
DEFAULT_MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 2
BACKOFF_CAP_SECONDS = 30

_REDACTED_KEYS = ("access_key_id", "secret_key", "session_token")


def _redact_response(resp: dict) -> dict:
    """Return a copy of `resp` with secret-triple keys replaced by `<redacted>`."""
    return {k: ("<redacted>" if k in _REDACTED_KEYS else v) for k, v in resp.items()}


class MirisError(Exception):
    """Raised on any non-2xx Miris API response from a required call."""


class MirisClient:
    def __init__(
        self,
        base_url: str,
        integration_key: str,
        timeout_seconds: int = 60,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._session = requests.Session()
        self._session.headers["Miris-API-Key"] = integration_key
        self._session.headers["Content-Type"] = "application/json"

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff for retry `attempt` (1-based), jittered over the upper half of the step."""
        step = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        return step / 2 + random.uniform(0, step / 2)  # nosec B311 - jitter, not security

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Send an idempotent request, retrying transient failures. Returns the last response, or
        raises MirisError when the last attempt failed in transit or the request is malformed."""
        attempt = 1
        while True:
            try:
                r = self._session.request(
                    method, url, timeout=self._timeout, allow_redirects=False, **kwargs
                )
            except NON_TRANSIENT_REQUEST_ERRORS as e:
                raise MirisError(f"{method} {url} request failed: {e}") from e
            except requests.RequestException as e:
                if attempt >= self._max_attempts:
                    raise MirisError(f"{method} {url} request failed: {e}") from e
            else:
                if r.status_code not in RETRYABLE_STATUS_CODES or attempt >= self._max_attempts:
                    return r
            self._sleep(self._backoff(attempt))
            attempt += 1

    @staticmethod
    def _raise_for_status(r: requests.Response, label: str) -> None:
        if r.status_code >= 300:
            # The status code only: a response body can echo request details or account data.
            raise MirisError(f"{label} failed [{r.status_code}]")

    # 1. POST /v1/asset
    def start_upload(
        self,
        name: str,
        content_path: str,
        total_bytes: int,
        tags: list,
    ) -> dict:
        url = f"{self.base_url}/v1/asset"
        body = {
            "name": name,
            "content_path": content_path,
            "file_count": 1,
            "input_type": "usd",
            "output_type": "static",
            "tags": tags,
            "total_bytes": total_bytes,
        }
        try:
            r = self._session.post(url, json=body, timeout=self._timeout, allow_redirects=False)
        except requests.RequestException as e:
            raise MirisError(f"POST /v1/asset connection failure: {e}") from e
        self._raise_for_status(r, "POST /v1/asset")
        return r.json()

    # 3. PUT /v1/asset/upload/{id}
    def mark_upload_complete(self, asset_id: str) -> Optional[dict]:
        r = self._request(
            "PUT", f"{self.base_url}/v1/asset/upload/{asset_id}", json={"status": "completed"}
        )
        self._raise_for_status(r, f"PUT /v1/asset/upload/{asset_id}")
        return r.json() if r.text else None

    # 4. GET /v1/asset/{id}
    def get_asset(self, asset_id: str) -> dict:
        r = self._request("GET", f"{self.base_url}/v1/asset/{asset_id}")
        self._raise_for_status(r, f"GET /v1/asset/{asset_id}")
        try:
            return r.json()
        except ValueError as e:
            raise MirisError(f"GET /v1/asset/{asset_id} returned an unreadable body: {e}") from e

    def poll_until_terminal(
        self,
        asset_id: str,
        timeout_seconds: int,
        poll_interval_seconds: int = 10,
    ) -> dict:
        deadline = time.monotonic() + timeout_seconds
        last: dict = {}
        while True:
            last = self.get_asset(asset_id)
            state = last.get("state", "")
            if state in TERMINAL_SUCCESS_STATES:
                return last
            if state in TERMINAL_ERROR_STATES:
                raise MirisError(
                    f"Asset {asset_id} reached terminal error state {state!r}"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Asset {asset_id} did not reach a terminal state within "
                    f"{timeout_seconds}s; last state {state!r}"
                )
            if poll_interval_seconds > 0:
                self._sleep(poll_interval_seconds)

    # 5. POST /v1/asset/{id}/generate  (best-effort)
    def trigger_generate_best_effort(self, asset_id: str) -> Optional[dict]:
        """Kick off streamable promotion, as the `miris asset generate` CLI command does. Returns
        the parsed body on 2xx and None on any failure, so the upload still succeeds with the asset
        at `preview`."""
        url = f"{self.base_url}/v1/asset/{asset_id}/generate"
        try:
            r = self._session.post(url, timeout=self._timeout, allow_redirects=False)
            if r.status_code >= 300:
                return None
            return r.json() if r.text else {}
        except (requests.RequestException, ValueError):
            return None
