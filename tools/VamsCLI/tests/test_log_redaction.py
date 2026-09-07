"""Credentials must not reach the CLI log file.

The log file is a rotating on-disk artifact (up to 6 files) with no permission hardening, so anything
written there outlives the command and is readable by other local users on a shared host. These tests
pin the redaction applied at the three sinks that receive whole request/response payloads:
`output_result`, `log_api_request`, and `log_api_response`.

Note the negative controls: over-redaction would make the log useless for the debugging it exists
for, so S3 object keys and identifiers must survive.
"""

import pytest

from vamscli.utils.logging import (
    REDACTED,
    redact_sensitive,
    redact_to_text,
    scrub_text,
)


class TestKeyBasedRedaction:
    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "Password",
            "new_password",
            "old_password",
            "passwd",
            "token",
            "access_token",
            "refresh_token",
            "id_token",
            "idToken",
            "refreshToken",
            "REFRESH-TOKEN",
            "token_override",
            "tokenOverride",
            "apiKey",
            "api_key",
            "apikey",
            "clientSecret",
            "client_secret",
            "secret",
            "credentials",
            "Authorization",
            "cookie",
            "privateKey",
            "signature",
        ],
    )
    def test_sensitive_keys_are_redacted(self, key):
        out = redact_sensitive({key: "s3cr3t-value-that-must-not-appear"})
        assert out[key] == REDACTED
        assert "s3cr3t-value-that-must-not-appear" not in str(out)

    @pytest.mark.parametrize(
        "key",
        [
            # Negative controls. Redacting these would gut the log's usefulness: this CLI logs S3
            # object keys on nearly every file operation.
            "s3Key",
            "objectKey",
            "keyName",
            "bucketExistingKey",
            "key",
            "assetId",
            "databaseId",
            "bucketName",
            "assetName",
            "description",
            "filePath",
            # Keys that NAME or DESCRIBE a credential without carrying one. Redacting these costs
            # diagnostic value for no security gain.
            "apiKeyId",
            "apiKeyName",
            "tokenType",
            "tokenCount",
            "tokenExpiry",
            "secretArn",
            "credentialsSecretArn",
            "apiKeyStatus",
        ],
    )
    def test_non_secret_keys_survive(self, key):
        out = redact_sensitive({key: "assets/model.glb"})
        assert out[key] == "assets/model.glb"

    @pytest.mark.parametrize("key", ["apiKeyHash", "passwordHash"])
    def test_credential_derived_hashes_stay_redacted(self, key):
        # A hash is still credential-derived material, so the descriptive-suffix rule must not
        # accidentally let it through.
        out = redact_sensitive({key: "abc123hashvalue"})
        assert out[key] == REDACTED

    def test_nested_structures_are_walked(self):
        payload = {
            "Items": [
                {"assetId": "a1", "apiKey": "vams_AAAAAAAAAAAAAAAAAAAA"},
                {"assetId": "a2", "nested": {"refresh_token": "rt-value"}},
            ],
            "message": {"auth": {"access_token": "at-value"}},
        }
        text = redact_to_text(payload)
        assert "vams_AAAAAAAAAAAAAAAAAAAA" not in text
        assert "rt-value" not in text
        assert "at-value" not in text
        # Identifiers still present, so the log remains diagnostic.
        assert "a1" in text and "a2" in text

    def test_redaction_never_raises_on_odd_input(self):
        class Weird:
            def __str__(self):
                raise RuntimeError("boom")

        # Must not propagate: a logging failure may not fail the command.
        assert isinstance(redact_to_text(Weird()), str)

    def test_deeply_nested_input_terminates(self):
        node = {"leaf": "ok"}
        for _ in range(50):
            node = {"child": node}
        assert isinstance(redact_to_text(node), str)


class TestValueBasedScrubbing:
    """Backstop for credentials that arrive with no helpful key, e.g. already-rendered text."""

    def test_vams_api_key_shape_is_scrubbed(self):
        assert "vams_" not in scrub_text("key is vams_ABCDEFGHIJKLMNOPQRSTUVWX now")

    def test_jwt_shape_is_scrubbed(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r"
        assert jwt not in scrub_text(f"token={jwt}")

    def test_bearer_header_value_is_scrubbed(self):
        assert "abcdefghijklmnopqrstuvwxyz" not in scrub_text(
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"
        )

    def test_presigned_url_secrets_are_scrubbed(self):
        url = (
            "https://b.s3.amazonaws.com/k?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Signature=deadbeefcafe1234&X-Amz-Security-Token=tokvalue123"
        )
        out = scrub_text(url)
        assert "deadbeefcafe1234" not in out
        assert "tokvalue123" not in out
        # The object path stays readable — that is the part worth logging.
        assert "b.s3.amazonaws.com" in out

    def test_ordinary_text_is_untouched(self):
        msg = "Uploaded assets/model.glb (1234 bytes) to database smoke-db"
        assert scrub_text(msg) == msg


class TestPerKeyLogFiltersShareOnePredicate:
    """Every per-key log filter must use the fragment predicate, not its own exact list.

    S6-TOOLS-024. `log_config_info`, `log_config_diagnostic`, `log_auth_diagnostic` and
    `log_api_request`'s header filter each kept a list like
    `['password', 'token', 'secret', 'key']` and matched with `in`, so the parameter names this CLI
    actually declares were all misses: `new_password` and `old_password` (`auth login`,
    `auth change-password`, `auth forgot-password`), `token_override` (`auth login`) and
    `access_token`. The redaction visibly fired for `password` while the sibling secret passed
    through in the same line.
    """

    SECRETS = {
        "password": "Hunter2!",
        "new_password": "NewHunter3!",
        "old_password": "OldHunter1!",
        "token_override": "eyJhbGciOiJIUzI1NiJ9.payload.sig",
        "access_token": "at-live-value",
        "refresh_token": "rt-live-value",
        "apiKey": "vams_LIVEKEYVALUE0123456789",
    }
    # Negative control: these must survive, or the filter is just masking everything.
    SURVIVORS = {
        "starting_token": "cursor-abc",
        "tokenType": "Bearer",
        "apiKeyId": "key-1",
        "profile_name": "prod5",
        "api_gateway_url": "https://api.example.com/api",
    }

    @staticmethod
    def _capture(monkeypatch):
        from vamscli.utils import logging as vlog

        records = []

        class FakeLogger:
            def debug(self, msg, *a, **k):
                records.append(str(msg))

            def info(self, msg, *a, **k):
                records.append(str(msg))

            def error(self, msg, *a, **k):
                records.append(str(msg))

        monkeypatch.setattr(vlog, "get_logger", lambda: FakeLogger())
        monkeypatch.setattr(vlog, "_verbose_mode", False)
        return vlog, records

    def _assert_filtered(self, records, what):
        joined = " ".join(records)
        # Control first: a filter that logged nothing at all would satisfy every "not in" below.
        assert joined, f"{what} wrote nothing, so these assertions would be vacuous"
        for name, value in self.SECRETS.items():
            assert value not in joined, f"{what} leaked {name} in cleartext"
        for name, value in self.SURVIVORS.items():
            assert value in joined, f"{what} over-redacted {name}, destroying the log's usefulness"

    def test_log_config_info(self, monkeypatch):
        vlog, records = self._capture(monkeypatch)
        vlog.log_config_info({**self.SECRETS, **self.SURVIVORS})
        self._assert_filtered(records, "log_config_info")

    def test_log_config_diagnostic(self, monkeypatch):
        vlog, records = self._capture(monkeypatch)
        vlog.log_config_diagnostic({**self.SECRETS, **self.SURVIVORS}, profile_name="prod5")
        self._assert_filtered(records, "log_config_diagnostic")

    def test_log_auth_diagnostic(self, monkeypatch):
        vlog, records = self._capture(monkeypatch)
        vlog.log_auth_diagnostic("cognito", "success",
                                 details={**self.SECRETS, **self.SURVIVORS})
        self._assert_filtered(records, "log_auth_diagnostic")

    def test_log_command_start(self, monkeypatch):
        vlog, records = self._capture(monkeypatch)
        vlog.log_command_start("login", {**self.SECRETS, **self.SURVIVORS})
        self._assert_filtered(records, "log_command_start")

    def test_log_api_request_headers(self, monkeypatch):
        vlog, records = self._capture(monkeypatch)
        vlog.log_api_request(
            "POST", "https://api.example.com/assets",
            headers={
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
                "X-Api-Key": "vams_LIVEKEYVALUE0123456789",
                "Content-Type": "application/json",
                "User-Agent": "vamscli/2.6.0",
            },
        )
        joined = " ".join(records)
        assert joined
        assert "vams_LIVEKEYVALUE0123456789" not in joined
        assert "eyJhbGciOiJIUzI1NiJ9.payload.sig" not in joined
        # Non-credential headers are the reason the log is worth keeping.
        assert "application/json" in joined
        assert "vamscli/2.6.0" in joined


class TestSinkIntegration:
    """The redaction must be wired in at the sinks, not merely available."""

    def test_output_result_does_not_log_the_api_key(self, monkeypatch):
        captured = []
        import vamscli.utils.json_output as jo

        monkeypatch.setattr(jo, "log_info", lambda msg, *a, **k: captured.append(msg))
        jo.output_result(
            {"apiKeyId": "k1", "apiKey": "vams_SUPERSECRETVALUE1234"},
            json_output=True,
        )
        joined = " ".join(captured)
        assert "vams_SUPERSECRETVALUE1234" not in joined
        # The non-secret identifier is still logged, so the record remains useful.
        assert "k1" in joined

    def test_log_api_response_does_not_log_tokens(self, monkeypatch):
        from vamscli.utils import logging as vlog

        records = []

        class FakeLogger:
            def debug(self, msg, *a, **k):
                records.append(msg)

            def info(self, msg, *a, **k):
                records.append(msg)

        monkeypatch.setattr(vlog, "get_logger", lambda: FakeLogger())
        vlog.log_api_response(
            200,
            {"access_token": "at-secret", "refresh_token": "rt-secret", "userId": "u1"},
            0.1,
        )
        joined = " ".join(str(r) for r in records)
        assert "at-secret" not in joined
        assert "rt-secret" not in joined
        assert "u1" in joined

    def test_redaction_does_not_mutate_the_caller_s_object(self):
        # The whole design depends on redaction producing a COPY. If it ever redacted in place, the
        # console output below would print ***REDACTED*** and a user could never retrieve a newly
        # created API key — which is the one moment the value is ever shown.
        original = {"apiKeyId": "k1", "apiKey": "vams_SUPERSECRETVALUE1234"}
        redact_to_text(original)
        assert original["apiKey"] == "vams_SUPERSECRETVALUE1234"

    def test_log_api_request_does_not_log_the_password(self, monkeypatch):
        from vamscli.utils import logging as vlog

        records = []

        class FakeLogger:
            def debug(self, msg, *a, **k):
                records.append(msg)

            def info(self, msg, *a, **k):
                records.append(msg)

        monkeypatch.setattr(vlog, "get_logger", lambda: FakeLogger())
        vlog.log_api_request(
            "POST",
            "https://example/api/auth/login",
            headers={"Authorization": "Bearer x"},
            body={"username": "me@example.com", "password": "Hunter2!", "new_password": "Hunter3!"},
        )
        joined = " ".join(str(r) for r in records)
        assert "Hunter2!" not in joined
        assert "Hunter3!" not in joined
        assert "me@example.com" in joined


class TestApiKeyRemainsVisibleToTheUser:
    """
    Redaction applies to the LOG FILE only, never to what the user sees.

    A newly created API key is displayed exactly once — there is no endpoint that returns it again —
    so if redaction ever leaked into the console or `--json-output` path, the key would be
    unrecoverable and `api-key create` would be useless. These tests pin that boundary.
    """

    def test_json_output_still_prints_the_full_key(self, capsys, monkeypatch):
        import vamscli.utils.json_output as jo

        monkeypatch.setattr(jo, "log_info", lambda *a, **k: None)
        jo.output_result(
            {"apiKeyId": "k1", "apiKey": "vams_SUPERSECRETVALUE1234"},
            json_output=True,
        )
        out = capsys.readouterr().out
        assert "vams_SUPERSECRETVALUE1234" in out
        assert REDACTED not in out

    def test_human_output_still_prints_the_full_key(self, capsys, monkeypatch):
        import vamscli.utils.json_output as jo

        monkeypatch.setattr(jo, "log_info", lambda *a, **k: None)
        jo.output_result(
            {"apiKeyId": "k1", "apiKey": "vams_SUPERSECRETVALUE1234"},
            json_output=False,
            success_message="API key created",
            cli_formatter=lambda r: f"  {r.get('apiKey', 'N/A')}",
        )
        out = capsys.readouterr().out
        assert "vams_SUPERSECRETVALUE1234" in out
        assert REDACTED not in out

    def test_json_output_is_still_valid_parseable_json(self, capsys, monkeypatch):
        import json as _json
        import vamscli.utils.json_output as jo

        monkeypatch.setattr(jo, "log_info", lambda *a, **k: None)
        jo.output_result(
            {"apiKeyId": "k1", "apiKey": "vams_SUPERSECRETVALUE1234"},
            json_output=True,
        )
        parsed = _json.loads(capsys.readouterr().out)
        # The machine contract the external connectors parse must be unchanged.
        assert parsed["apiKey"] == "vams_SUPERSECRETVALUE1234"
        assert parsed["apiKeyId"] == "k1"
