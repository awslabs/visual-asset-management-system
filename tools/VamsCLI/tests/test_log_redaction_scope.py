"""The log redactor's boundary: a credential is masked, a pagination cursor is not.

FIX-010 follow-up to finding `S6-TOOLS-024`. `tests/test_log_redaction.py` pins that credentials do
not reach the log file. That property alone is satisfied by a redactor that masks **everything**,
which is the failure this file exists to catch: the `token` fragment in
`_SENSITIVE_KEY_FRAGMENTS` matches `starting_token` and `next_token`, so every `--starting-token` /
`--next-token` option — roughly two dozen command groups carry one — logged as
`{'starting_token': '***REDACTED***'}`. A stuck or looping pagination walk is then unreadable in the
one artifact that recorded it, because the cursor is precisely the value needed to resume or reproduce
the call.

Every assertion here therefore comes in a pair, and neither half passes on its own:

* a cursor key keeps its value verbatim — fails on a redactor that masks by the `token` substring;
* a credential key is masked — fails on a redactor that simply stopped masking `token` keys.

Two spelling notes the tests depend on. `_is_sensitive_key` normalizes a key by stripping ``_``,
``-`` and whitespace and lowercasing it, so `starting_token`, `startingToken` and `STARTING-TOKEN`
are one name; and the backend normalizes every paginated route onto `startingToken` in the request and
`NextToken` in the response (`backend/backend/handlers/assets/assetFiles.py` maps S3's
`NextContinuationToken` and `cognitoUserService.py` maps Cognito's `PaginationToken` onto it), so
those are the only cursor spellings that reach the CLI.

The `log_command_start` credential cases below are forward-looking rather than a reproduction: no
command that currently reaches `log_command_start` passes a credential option, because
`auth login` / `auth change-password` / `auth forgot-password` use the legacy `@requires_api_access`
decorator (which does not log) and `auth set-override` carries no decorator at all. They are asserted
because that function routes *every* Click kwarg of *every* decorated command through
`_is_sensitive_key`, which is what `S6-TOOLS-024` was raised about — migrating one of those commands
onto `@requires_setup_and_auth`, as `tools/VamsCLI/CLAUDE.md` Rule 5 directs for new commands, makes
the masking half live with no further change.
"""

import pytest

from vamscli.utils.logging import (
    REDACTED,
    _PAGINATION_CURSOR_KEYS,
    _is_sensitive_key,
    log_command_start,
    redact_sensitive,
    redact_to_text,
)


# A boto3-shaped DynamoDB cursor: base64 of the JSON `ExclusiveStartKey`. Used verbatim so the
# assertions also cover the value-shape scrubber, which masks `vams_…`, JWT, `Bearer …` and presigned
# URL shapes regardless of key name.
CURSOR_VALUE = "eyJFeGNsdXNpdmVTdGFydEtleSI6IHsiYXNzZXRJZCI6IHsiUyI6ICJhc3NldC0xIn19fQ=="

CREDENTIAL_VALUE = "s3cr3t-value-that-must-not-appear"


class TestPaginationCursorsStayReadable:
    """The over-redaction half. Each case fails if the `token` fragment decides the key."""

    @pytest.mark.parametrize(
        "key",
        [
            "starting_token",     # the Click kwarg, on ~24 command groups
            "startingToken",      # the request parameter it maps onto
            "STARTING-TOKEN",
            "next_token",         # `execution logs --next-token`
            "nextToken",
            "NextToken",          # the response field every paginated route returns
        ],
    )
    def test_cursor_keys_keep_their_value(self, key):
        out = redact_sensitive({key: CURSOR_VALUE})
        assert out[key] == CURSOR_VALUE, (
            f"the pagination cursor `{key}` was masked; a stuck pagination walk cannot be diagnosed "
            f"or resumed without it"
        )
        assert not _is_sensitive_key(key)

    def test_a_cursor_survives_the_value_shape_scrubber_too(self):
        # A base64 cursor starts with `eyJ` exactly as a JWT does, so the key-name decision alone is
        # not enough — the value must clear the JWT pattern as well (it has no `.` segments).
        assert CURSOR_VALUE in redact_to_text({"startingToken": CURSOR_VALUE})

    def test_the_cursor_names_are_stored_already_normalized(self):
        # `_is_sensitive_key` compares against a key with separators stripped and lowercased, so an
        # entry written as `starting_token` or `NextToken` would never match anything.
        for name in _PAGINATION_CURSOR_KEYS:
            assert name == name.lower()
            assert not any(character in name for character in "_- ")


class TestCredentialTokensStayMasked:
    """The under-redaction half. Each case fails if the exclusion widened to any `token` key."""

    @pytest.mark.parametrize(
        "key",
        [
            "token",              # `auth set-override --token`
            "access_token",
            "accessToken",
            "refresh_token",
            "refreshToken",
            "id_token",
            "idToken",
            "token_override",
            "tokenOverride",
            "apiKey",
            "api_key",
            "password",
            "client_secret",
        ],
    )
    def test_credential_keys_are_masked(self, key):
        out = redact_sensitive({key: CREDENTIAL_VALUE})
        assert out[key] == REDACTED, f"the credential `{key}` reached the log in the clear"
        assert CREDENTIAL_VALUE not in str(out)
        assert _is_sensitive_key(key)


class TestBothSidesOfTheBoundaryInOnePayload:
    """The pair asserted together, on the shape a paginated list call actually produces.

    Split across two tests, either half could be made to pass by a redactor that is wrong in the
    other direction. Here one rendered log line has to satisfy both.
    """

    def test_a_paginated_response_keeps_its_cursor_and_loses_its_credential(self):
        payload = {
            "Items": [{"apiKeyId": "key-abc123", "apiKey": "vams_LIVEONETIMESECRETVALUE01234"}],
            "NextToken": CURSOR_VALUE,
        }
        rendered = redact_to_text(payload)

        assert CURSOR_VALUE in rendered
        assert "vams_LIVEONETIMESECRETVALUE01234" not in rendered
        assert REDACTED in rendered
        # Control: the identifier survives, so the log line is still diagnostic rather than a row of
        # placeholders that happens to contain the cursor.
        assert "key-abc123" in rendered


class TestLogCommandStartArguments:
    """The reported site (`log_command_start`), which routes every Click kwarg through the decision.

    `mock_logging` is the autouse fixture that patches `get_logger`, so the arguments line is
    captured instead of written to a file.
    """

    def test_the_arguments_line_keeps_the_cursor_and_masks_the_credential(self, mock_logging):
        log_command_start(
            "list",
            {
                "database_id": "smoke-db",
                "page_size": 100,
                "max_items": 10000,
                "starting_token": CURSOR_VALUE,
                "auto_paginate": False,
                "json_output": True,
                "token_override": CREDENTIAL_VALUE,
            },
        )

        rendered = " ".join(str(call) for call in mock_logging.debug.call_args_list)
        # Control: an arguments line was logged at all. Without it every assertion below is vacuous.
        assert "Command arguments" in rendered
        assert "smoke-db" in rendered

        assert CURSOR_VALUE in rendered, (
            "the cursor was masked in the command arguments line, which is the log record a stuck "
            "pagination walk is diagnosed from"
        )
        assert CREDENTIAL_VALUE not in rendered
        assert REDACTED in rendered

    def test_a_cursor_valued_none_is_logged_as_none(self, mock_logging):
        # Click passes the option through as None when it is absent, and `None` in the log is the
        # evidence that the first page was requested rather than a resumed one.
        log_command_start("list", {"starting_token": None, "page_size": 100})

        rendered = " ".join(str(call) for call in mock_logging.debug.call_args_list)
        assert "'starting_token': None" in rendered
        assert REDACTED not in rendered
