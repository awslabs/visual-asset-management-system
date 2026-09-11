# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""``PhysnaClient`` retry attempts must carry the current token and the caller's headers.

Three defects in one loop, all invisible to an assertion on call counts:

* the 401 branch re-injected the header dict that already held the OLD bearer, and
  ``setdefault`` would not overwrite it, so the refresh-and-retry replayed the stale
  token and earned a second 401;
* ``kwargs.pop("headers")`` ran INSIDE the loop, so a 5xx or network retry rebuilt the
  headers from an empty dict and re-sent the JSON body with no ``Content-Type``;
* the Secrets Manager credential was memoized for the container's lifetime, so
  ``_ensure_token(force_refresh=True)`` re-presented the rotated-away credential.

Every tightening carries a control asserting the behaviour that must NOT change: the
happy-path request, the ``Accept`` default a caller can still override, the second-401
guard, and the credential cache holding within its window.
"""

import json

import pytest

# Module-level import ensures the real `backend.backend.handlers` package is populated
# in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.addon.physna import physnaCommon as _pc  # noqa: F401


class _FakeResponse:
    def __init__(self, status, body=None):
        self.status = status
        self.data = json.dumps(body if body is not None else {}).encode("utf-8")


def _install_client_fakes(monkeypatch, *, tokens, api_results):
    """Fake the token endpoint and the HTTP seam, recording each attempt's headers.

    tokens: access_token values handed out by successive token POSTs.
    api_results: per-attempt outcomes — an int status, or an Exception to raise.
    """
    import backend.backend.handlers.addon.physna.physnaCommon as pc

    pc._reset_client_state_for_tests()
    monkeypatch.setattr(
        pc,
        "_load_physna_credentials",
        lambda: {"clientId": "test-client", "clientSecret": "test-secret"},
    )

    token_iter = iter(tokens)
    result_iter = iter(api_results)
    recorded = {"tokens": 0, "headers": []}

    def fake_token_post(client_id, client_secret):
        recorded["tokens"] += 1
        return _FakeResponse(
            200, {"access_token": next(token_iter), "expires_in": 3600}
        )

    def fake_http_request(method, url, **kwargs):
        recorded["headers"].append(dict(kwargs.get("headers") or {}))
        result = next(result_iter)
        if isinstance(result, Exception):
            raise result
        return _FakeResponse(result, {"ok": True})

    monkeypatch.setattr(pc, "_http_post_token", fake_token_post)
    monkeypatch.setattr(pc, "_http_request", fake_http_request)
    monkeypatch.setattr(pc.time, "sleep", lambda seconds: None)
    return pc, recorded


@pytest.mark.unit
class TestRetryCarriesTheCurrentToken:
    """A 401 retry must present the token the refresh just fetched."""

    def test_401_retry_sends_the_refreshed_bearer(self, monkeypatch):
        pc, recorded = _install_client_fakes(
            monkeypatch, tokens=["tok-1", "tok-2"], api_results=[401, 200]
        )

        response = pc.PhysnaClient().request("GET", "/a")

        assert response.status == 200
        assert [h["Authorization"] for h in recorded["headers"]] == [
            "Bearer tok-1",
            "Bearer tok-2",
        ]

    def test_client_token_replaces_a_caller_supplied_authorization(self, monkeypatch):
        # The client owns the Physna credential, so its bearer wins on every attempt.
        pc, recorded = _install_client_fakes(
            monkeypatch, tokens=["tok-1"], api_results=[200]
        )

        pc.PhysnaClient().request(
            "GET", "/a", headers={"Authorization": "Bearer caller-supplied"}
        )

        assert recorded["headers"][0]["Authorization"] == "Bearer tok-1"

    def test_second_401_still_raises_auth_error(self, monkeypatch):
        # Control: one refresh, then give up — the retry budget is unchanged.
        pc, recorded = _install_client_fakes(
            monkeypatch, tokens=["tok-1", "tok-2"], api_results=[401, 401]
        )

        with pytest.raises(pc.PhysnaAuthError):
            pc.PhysnaClient().request("GET", "/a")

        assert len(recorded["headers"]) == 2
        assert recorded["tokens"] == 2


@pytest.mark.unit
class TestRetryKeepsCallerHeaders:
    """A retry re-sends the body, so it must re-send the body's content type."""

    def test_5xx_retry_keeps_the_caller_content_type(self, monkeypatch):
        pc, recorded = _install_client_fakes(
            monkeypatch, tokens=["tok-1"], api_results=[500, 200]
        )

        response = pc.PhysnaClient().request(
            "PATCH",
            "/tenants/t/assets/u",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )

        assert response.status == 200
        assert [h.get("Content-Type") for h in recorded["headers"]] == [
            "application/json",
            "application/json",
        ]

    def test_network_error_retry_keeps_the_caller_content_type(self, monkeypatch):
        pc, recorded = _install_client_fakes(
            monkeypatch,
            tokens=["tok-1"],
            api_results=[OSError("connection reset"), 200],
        )

        response = pc.PhysnaClient().request(
            "POST",
            "/tenants/t/metadata-fields",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )

        assert response.status == 200
        assert [h.get("Content-Type") for h in recorded["headers"]] == [
            "application/json",
            "application/json",
        ]

    def test_401_retry_keeps_the_caller_content_type(self, monkeypatch):
        # Control: the 401 branch already carried the caller's headers forward and
        # must keep doing so now that they are held outside the loop.
        pc, recorded = _install_client_fakes(
            monkeypatch, tokens=["tok-1", "tok-2"], api_results=[401, 200]
        )

        pc.PhysnaClient().request(
            "PATCH", "/x", body=b"{}", headers={"Content-Type": "application/json"}
        )

        assert [h.get("Content-Type") for h in recorded["headers"]] == [
            "application/json",
            "application/json",
        ]

    def test_single_attempt_sends_caller_headers_with_auth_and_accept(
        self, monkeypatch
    ):
        # Control: the ordinary one-attempt request is unchanged.
        pc, recorded = _install_client_fakes(
            monkeypatch, tokens=["tok-1"], api_results=[200]
        )

        response = pc.PhysnaClient().request(
            "POST", "/x", body=b"{}", headers={"Content-Type": "application/json"}
        )

        assert response.status == 200
        assert recorded["tokens"] == 1
        assert recorded["headers"] == [
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer tok-1",
                "Accept": "application/json",
            }
        ]

    def test_caller_can_still_override_accept(self, monkeypatch):
        # Control: only the Authorization header is client-owned; Accept stays a default.
        pc, recorded = _install_client_fakes(
            monkeypatch, tokens=["tok-1"], api_results=[200]
        )

        pc.PhysnaClient().request("GET", "/a", headers={"Accept": "text/csv"})

        assert recorded["headers"][0]["Accept"] == "text/csv"


@pytest.mark.unit
class TestCredentialCacheRefresh:
    """A rotated Physna credential must be picked up without a container recycle."""

    def _install_secret_fakes(self, monkeypatch):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pc._reset_client_state_for_tests()
        reads = {"n": 0}

        class FakeSecretsManager:
            def get_secret_value(self, SecretId=None):
                reads["n"] += 1
                return {
                    "SecretString": json.dumps(
                        {
                            "clientId": f"client-{reads['n']}",
                            "clientSecret": f"secret-{reads['n']}",
                        }
                    )
                }

        presented = []

        def fake_token_post(client_id, client_secret):
            presented.append(client_id)
            return _FakeResponse(200, {"access_token": "tok", "expires_in": 3600})

        monkeypatch.setattr(
            pc, "_get_secretsmanager_client", lambda: FakeSecretsManager()
        )
        monkeypatch.setattr(pc, "_http_post_token", fake_token_post)
        return pc, reads, presented

    def test_forced_refresh_rereads_the_secret(self, monkeypatch):
        pc, reads, presented = self._install_secret_fakes(monkeypatch)
        client = pc.PhysnaClient()

        client._ensure_token()
        client._ensure_token(force_refresh=True)

        assert reads["n"] == 2
        assert presented == ["client-1", "client-2"]

    def test_secret_is_reread_once_the_cache_ttl_lapses(self, monkeypatch):
        pc, reads, _presented = self._install_secret_fakes(monkeypatch)

        assert pc._load_physna_credentials()["clientId"] == "client-1"
        monkeypatch.setattr(
            pc,
            "_cached_secret_fetched_at",
            pc._cached_secret_fetched_at - (pc._SECRET_CACHE_TTL_SECONDS + 1),
        )

        assert pc._load_physna_credentials()["clientId"] == "client-2"
        assert reads["n"] == 2

    def test_secret_is_cached_within_the_ttl(self, monkeypatch):
        # Control: the cache still holds, so an unrotated deployment does not pay a
        # Secrets Manager read per token fetch.
        pc, reads, _presented = self._install_secret_fakes(monkeypatch)

        first = pc._load_physna_credentials()
        second = pc._load_physna_credentials()

        assert first == second == {"clientId": "client-1", "clientSecret": "secret-1"}
        assert reads["n"] == 1

    def test_cached_token_is_reused_without_touching_the_secret(self, monkeypatch):
        # Control: an unforced token check still short-circuits on the cached token.
        pc, reads, presented = self._install_secret_fakes(monkeypatch)
        client = pc.PhysnaClient()

        client._ensure_token()
        client._ensure_token()

        assert reads["n"] == 1
        assert presented == ["client-1"]
