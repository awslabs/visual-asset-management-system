# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for customLogging.logger.mask_sensitive_data and the audit event echo.

Two properties are pinned here:

  - **Content-bearing payload keys are redacted, keys survive.** A pipeline template `configBody` and
    its tag values carry free-form content (prompts, model configuration), and the audit writer echoes
    the whole request event onto every entry. The key stays so the trail still shows the field was
    submitted; the value does not reach CloudWatch. A stringified JSON `body` is parsed so a raw string
    payload cannot slip past the key walk.
  - **The walk covers lists.** A list of dicts is a normal request shape (`templateTags`, batch file
    entries), and a walk that only recursed into dicts passed those through verbatim.
"""

import json

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.customLogging.logger import (
    mask_sensitive_data,
    CONTENT_KEYS,
    REDACTED,
    SENSITIVE_KEYS,
)
from backend.backend.customLogging import auditLogging

_TEMPLATE_BODY = '{"prompt": "a photoreal render of the customer facility"}'


@pytest.mark.unit
class TestMaskSensitiveData:
    def test_credential_keys_still_redacted(self):
        event = {
            "headers": {"authorization": "Bearer abc.def.ghi", "idJwtToken": "eyJhbGci"},
            "Credentials": {"AccessKeyId": "AKIAIOSFODNN7EXAMPLE", "SessionToken": "FQoGZXIvYXdz"},
        }
        out = mask_sensitive_data(event)
        assert out["headers"]["authorization"] == REDACTED
        assert out["headers"]["idJwtToken"] == REDACTED
        assert out["Credentials"] == REDACTED
        assert "abc.def.ghi" not in json.dumps(out)
        assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(out)

    def test_uppercase_authorization_header_redacted(self):
        out = mask_sensitive_data({"headers": {"Authorization": "Bearer abc.def.ghi"}})
        assert out["headers"]["Authorization"] == REDACTED

    def test_non_sensitive_keys_survive(self):
        event = {
            "requestContext": {"http": {"method": "PUT", "path": "/pipelines/templates/t1"}},
            "pathParameters": {"templateId": "t1"},
            "queryStringParameters": None,
        }
        assert mask_sensitive_data(event) == event

    def test_content_keys_redacted_key_kept(self):
        event = {"configBody": _TEMPLATE_BODY, "tagValues": {"PROMPT": "secret"}, "templateId": "t1"}
        out = mask_sensitive_data(event)
        assert set(out) == {"configBody", "tagValues", "templateId"}
        assert out["configBody"] == REDACTED
        assert out["tagValues"] == REDACTED
        assert out["templateId"] == "t1"

    def test_every_template_body_field_is_redacted(self):
        """A template body reaches the API under several field names, and each one carries the same
        free-form caller content. Redacting only the one a template record uses leaves the execute
        request's own override — the same body, submitted a different way — in the log verbatim."""
        event = {
            "configBody": _TEMPLATE_BODY,
            "customTemplateOverride": _TEMPLATE_BODY,
            "webFormJson": '{"fields": [{"name": "prompt"}]}',
            "inputInstructions": "Describe the asset for the model.",
            "templateId": "t1",
        }
        out = mask_sensitive_data(event)
        for field in ("configBody", "customTemplateOverride", "webFormJson", "inputInstructions"):
            assert out[field] == REDACTED, f"{field} was echoed"
        assert out["templateId"] == "t1"

    def test_execute_request_override_redacted_inside_a_json_string_body(self):
        # The execute request delivers the override nested under a per-pipeline parameters map, inside a
        # body that arrives as a JSON STRING — the shape an API Gateway event actually carries.
        raw = json.dumps({"pipelineParameters": [
            {"pipelineId": "p1", "customTemplateOverride": _TEMPLATE_BODY}]})
        out = mask_sensitive_data({"body": raw})
        assert _TEMPLATE_BODY not in out["body"]
        assert REDACTED in out["body"]
        assert "pipelineId" in out["body"]

    def test_dict_body_content_redacted(self):
        event = {"body": {"templateId": "t1", "configBody": _TEMPLATE_BODY}}
        out = mask_sensitive_data(event)
        assert out["body"]["configBody"] == REDACTED
        assert out["body"]["templateId"] == "t1"

    def test_json_string_body_content_redacted(self):
        event = {"body": json.dumps({"templateId": "t1", "configBody": _TEMPLATE_BODY})}
        out = mask_sensitive_data(event)
        assert "photoreal render" not in out["body"]
        assert json.loads(out["body"]) == {"templateId": "t1", "configBody": REDACTED}

    def test_json_string_body_without_content_unchanged(self):
        raw = json.dumps({"workflowId": "w1", "assetIds": ["a1", "a2"]})
        assert mask_sensitive_data({"body": raw})["body"] == raw

    def test_unparseable_string_body_naming_content_key_redacted_whole(self):
        event = {"body": 'configBody=' + _TEMPLATE_BODY}
        assert mask_sensitive_data(event)["body"] == REDACTED

    def test_plain_string_body_unchanged(self):
        assert mask_sensitive_data({"body": "assetId=a1&versionId=v2"})["body"] == "assetId=a1&versionId=v2"

    def test_nested_list_of_dicts_redacted(self):
        event = {
            "templates": [
                {"templateId": "t1", "configBody": _TEMPLATE_BODY},
                {"templateId": "t2", "headers": {"authorization": "Bearer abc.def.ghi"}},
            ]
        }
        out = mask_sensitive_data(event)
        assert out["templates"][0]["configBody"] == REDACTED
        assert out["templates"][0]["templateId"] == "t1"
        assert out["templates"][1]["headers"]["authorization"] == REDACTED
        assert "photoreal render" not in json.dumps(out)
        assert "abc.def.ghi" not in json.dumps(out)

    def test_deeply_nested_list_in_list(self):
        out = mask_sensitive_data({"steps": [[{"configBody": _TEMPLATE_BODY}]]})
        assert out["steps"][0][0]["configBody"] == REDACTED

    def test_top_level_list_does_not_raise(self):
        assert mask_sensitive_data([{"configBody": _TEMPLATE_BODY}]) == [{"configBody": REDACTED}]

    def test_scalar_input_returned_unchanged(self):
        assert mask_sensitive_data("plain message") == "plain message"
        assert mask_sensitive_data(None) is None
        assert mask_sensitive_data(7) == 7

    def test_unmaskable_structure_never_raises(self):
        class Exploding(dict):
            def items(self):
                raise RuntimeError("cannot iterate")

        assert mask_sensitive_data(Exploding()) == REDACTED

    def test_key_lists_are_disjoint(self):
        assert not set(SENSITIVE_KEYS) & set(CONTENT_KEYS)


@pytest.mark.unit
class TestAuditEventEcho:
    """The audit writer appends the masked event to every entry, so the echo is the leak path."""

    def _write(self, event):
        # auditLogging resolves mask_sensitive_data from customLogging.logger, which the test
        # conftest replaces with a pass-through mock; bind the real masker for these assertions.
        mock_logs = MagicMock()
        with patch.object(auditLogging, "cloudwatch_logs", mock_logs), \
                patch.object(auditLogging, "mask_sensitive_data", mask_sensitive_data):
            auditLogging._write_batch_to_cloudwatch("audit-group", ["[ACTIONS][type: x] [user: u]"], event)
        assert mock_logs.put_log_events.called
        return mock_logs.put_log_events.call_args.kwargs["logEvents"]

    def test_template_body_not_echoed(self):
        event = {
            "requestContext": {"http": {"method": "PUT", "path": "/pipelines/templates/t1"}},
            "headers": {"authorization": "Bearer abc.def.ghi"},
            "body": json.dumps({"templateId": "t1", "configBody": _TEMPLATE_BODY}),
        }
        log_events = self._write(event)
        assert len(log_events) == 1
        message = log_events[0]["message"]
        assert "photoreal render" not in message
        assert "abc.def.ghi" not in message
        # The echo stays useful: the operation, the route and the submitted field are all still there.
        assert "[ACTIONS][type: x]" in message
        assert "/pipelines/templates/t1" in message
        assert "configBody" in message
        assert REDACTED in message

    def test_tag_values_list_not_echoed(self):
        event = {"body": json.dumps({"templateTags": [{"tagName": "PROMPT", "value": "secret prompt"}]})}
        message = self._write(event)[0]["message"]
        assert "secret prompt" not in message
        assert "templateTags" in message
