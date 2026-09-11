# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset-links cross-call must log identifiers, not the payload it sends or the body it gets.

`get_asset_tree_via_lambda` built a cross-call payload that embeds `event['requestContext']` --
whose `authorizer` block is the caller's decoded token claims, every one of them coerced to a
string by the custom authorizer -- and then rendered it with
`logger.info(f"Payload: {json.dumps(payload)}")`. safeLogger's redaction is key-driven and walks
the log record's structure, so an already-rendered string reaches CloudWatch untouched: the
caller's email, sub and cognito:username were written in plain text on every first-page export.
The next log line did the same for the whole asset-links response body.

backend/CLAUDE.md Rule 9 names this exact bypass: "the redaction is key-driven, so an f-string
that interpolates a payload value bypasses it -- log identifiers and counts, never rendered
bodies".

The claim values below are what the assertions search for. Each is unique to one carrier -- the
authorizer context, or the response body -- so a failure names which of the two lines leaked.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

# Reuse the loader from the fail-closed suite: assetExportService cannot be imported normally
# because the root conftest registers a mock `handlers` package that shadows the real one.
from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
    _DB,
    _ASSET,
)

_CALLER_EMAIL = "leaked-caller@example.invalid"
_CALLER_SUB = "sub-4f2c-should-not-be-logged"
_CALLER_USERNAME = "cognito-username-should-not-be-logged"
_RESPONSE_BODY_MARKER = "asset-links-response-body-marker"
_CHILD_ASSET = "child-asset-1"


def _event_with_authorizer_claims():
    """The event shape a real export request arrives with, claims included.

    The authorizer returns a flat string map of every decoded claim (common/auth/authorizerCore.py
    builds it as `context[key] = str(value)`), which API Gateway delivers as
    requestContext.authorizer.
    """
    return {
        'requestContext': {
            'authorizer': {
                'email': _CALLER_EMAIL,
                'sub': _CALLER_SUB,
                'cognito:username': _CALLER_USERNAME,
                'vams:roles': '["admin"]',
                'vams:mfaEnabled': 'True',
            },
            'http': {'method': 'POST', 'path': f"/database/{_DB}/assets/{_ASSET}/export"},
        }
    }


def _links_response():
    """A 200 asset-links response carrying one child and a body marker."""
    body = {
        'assetId': _ASSET,
        'databaseId': _DB,
        'marker': _RESPONSE_BODY_MARKER,
        'children': [{'assetId': _CHILD_ASSET, 'databaseId': _DB, 'assetLinkId': 'link-1'}],
    }
    payload = json.dumps({'statusCode': 200, 'body': json.dumps(body)}).encode('utf-8')
    stream = MagicMock()
    stream.read.return_value = payload
    return {'StatusCode': 200, 'Payload': stream}


def _call_tree_helper():
    """Run get_asset_tree_via_lambda offline; return (body, every log line it emitted)."""
    m = _load_asset_export_service()
    log = MagicMock()
    lambda_client = MagicMock()
    lambda_client.invoke.return_value = _links_response()

    with patch.object(m, "logger", log), patch.object(m, "lambda_client", lambda_client):
        body = m.get_asset_tree_via_lambda(
            _DB, _ASSET, _event_with_authorizer_claims(), fetch_entire_subtrees=False)

    lines = []
    for level in ("debug", "info", "warning", "error", "exception"):
        for call in getattr(log, level).call_args_list:
            lines.append(f"{level}: {call}")
    return body, lines, lambda_client


@pytest.mark.unit
class TestTheCrossCallDoesNotLogTheCallersClaims:
    def test_no_emitted_line_carries_the_callers_email(self):
        """The distinguishing assertion: the payload log rendered the whole authorizer block."""
        _body, lines, _client = _call_tree_helper()
        offenders = [line for line in lines if _CALLER_EMAIL in line]
        assert offenders == [], (
            f"the caller's email reached the log: {offenders}. safeLogger cannot redact a value "
            f"already rendered into the message")

    def test_no_emitted_line_carries_the_other_token_claims(self):
        """sub and cognito:username travel in the same block; one assertion per carrier."""
        _body, lines, _client = _call_tree_helper()
        for claim in (_CALLER_SUB, _CALLER_USERNAME):
            offenders = [line for line in lines if claim in line]
            assert offenders == [], f"the claim {claim} reached the log: {offenders}"

    def test_no_emitted_line_carries_the_asset_links_response_body(self):
        """The sibling line logged the entire cross-call response verbatim."""
        _body, lines, _client = _call_tree_helper()
        offenders = [line for line in lines if _RESPONSE_BODY_MARKER in line]
        assert offenders == [], f"the response body reached the log: {offenders}"


@pytest.mark.unit
class TestTheCrossCallStillWorksAndStaysDiagnosable:
    """Positive controls. Deleting the two lines outright would satisfy the class above."""

    def test_the_tree_is_still_retrieved_and_parsed(self):
        body, _lines, client = _call_tree_helper()
        assert body['children'][0]['assetId'] == _CHILD_ASSET, body
        assert client.invoke.call_count == 1
        sent = json.loads(client.invoke.call_args.kwargs['Payload'].decode('utf-8'))
        assert sent['pathParameters'] == {'databaseId': _DB, 'assetId': _ASSET}, sent
        assert sent['requestContext']['http']['method'] == 'GET', (
            "the asset-links service only handles GET; the cross-call still rewrites the method")

    def test_the_log_still_names_the_call_and_its_subject(self):
        """Identifiers, counts and flags are what Rule 9 asks for -- and they must be there."""
        _body, lines, _client = _call_tree_helper()
        text = " | ".join(lines)
        assert f"{_DB}:{_ASSET}" in text, f"the log does not name the asset being read: {text}"
        assert "full_tree=False" in text, f"the log does not name the tree depth: {text}"
        assert "1 children" in text or "with 1 " in text, (
            f"the log does not report how many children came back: {text}")
