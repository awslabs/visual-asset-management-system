# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The import custom resource logs its event in a form safeLogger can still redact.

Guards S2-BACKEND-146: the handler logged ``f"Received event: {json.dumps(event)}"``. safeLogger's
redaction is key-driven (``mask_sensitive_data`` walks dicts/lists and only string-parses a ``body``
key), so a pre-rendered string lands under the log record's ``message`` key untouched -- and an
``inlineBundle`` carries exactly the CONTENT_KEYS the redaction exists for: template ``configBody``,
``webFormJson`` and ``inputInstructions``.

The property asserted is the one the finding is about: whatever the handler hands the logger, running
the REAL ``mask_sensitive_data`` over it leaves no template content. That holds for either remedy the
finding offers -- logging the event object so the key walk applies, or logging only identifiers -- and
fails for the rendered string it replaced, which is exercised as a positive control so the assertion
cannot be passing merely because the redaction would strip the value from any input.

The same property has to hold for a bundle delivered as a JSON STRING, which is the shape a
CloudFormation property naturally takes (the CDK construct already stringifies ``bundleS3Keys``) and
the shape ``assemble_bundle`` explicitly accepts. A string is one opaque value to the key walk, so
logging the event object is not on its own sufficient -- the bundle has to be walkable. The
registration path must keep receiving the event verbatim, so the log view may not be built by mutating
it.
"""

import json
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("PIPELINE_SERVICE_V2_FUNCTION_NAME", "t-pipe-v2")
os.environ.setdefault("PIPELINE_TEMPLATE_SERVICE_FUNCTION_NAME", "t-tpl")
os.environ.setdefault("WORKFLOW_SERVICE_V2_FUNCTION_NAME", "t-wf-v2")
os.environ.setdefault("WORKFLOW_TRIGGER_SERVICE_FUNCTION_NAME", "t-trig")
os.environ.setdefault("LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET", "t-artefacts")

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import importGlobalPipelineWorkflow as imp
from backend.backend.customLogging.logger import mask_sensitive_data, REDACTED

SECRET_PROMPT = "SECRET-PROMPT-a-photoreal-render-of-the-customer-facility"
SECRET_FORM = "SECRET-WEBFORM-schema"
SECRET_INSTRUCTIONS = "SECRET-INSTRUCTIONS-for-the-model"


def _event():
    """A registration event whose inlineBundle carries every content key at risk."""
    return {
        "RequestType": "Create",
        "ResourceProperties": {
            "inlineBundle": {
                "pipeline": {"pipelineId": "genai", "pipelineName": "GenAI"},
                "templates": [{
                    "templateId": "t1",
                    "configBody": SECRET_PROMPT,
                    "webFormJson": SECRET_FORM,
                    "inputInstructions": SECRET_INSTRUCTIONS,
                }],
            },
        },
    }


def _string_bundle_event(holder="ResourceProperties"):
    """The same event with the bundle delivered as a JSON string under the named property holder."""
    bundle = _event()["ResourceProperties"]["inlineBundle"]
    return {"RequestType": "Create" if holder == "ResourceProperties" else "Update",
            holder: {"inlineBundle": json.dumps(bundle)}}


def _invoke_handler(event):
    """Invoke the handler with the logger and the registration recorded. Returns (logger, register)."""
    recorder = MagicMock()
    register = MagicMock(return_value={"ids": {}, "applied": []})
    with patch.object(imp, "logger", recorder), \
         patch.object(imp, "register_bundle", register), \
         patch.object(imp, "archive_bundle", return_value={"ids": {}, "warnings": []}), \
         patch.object(imp, "archive_superseded_ids", return_value=[]), \
         patch.object(imp, "_physical_id", return_value="pid"):
        imp.lambda_handler(event, MagicMock(log_stream_name="stream"))
    return recorder, register


def _logged_first_argument(event):
    """Invoke the handler and return the first positional argument it gave logger.info."""
    recorder, _ = _invoke_handler(event)
    assert recorder.info.call_args_list, "the handler must log the event it received"
    return recorder.info.call_args_list[0].args[0]


def _masked(event):
    """The handler's log argument for this event, redacted and serialized as CloudWatch would see it."""
    return json.dumps(mask_sensitive_data(_logged_first_argument(event)))


@pytest.mark.unit
class TestImportEventLogging:

    def test_no_template_content_survives_the_real_redaction(self):
        logged = _logged_first_argument(_event())
        masked = json.dumps(mask_sensitive_data(logged))
        assert SECRET_PROMPT not in masked
        assert SECRET_FORM not in masked
        assert SECRET_INSTRUCTIONS not in masked

    def test_the_pre_fix_rendering_would_still_leak(self):
        """Positive control: the redaction is key-driven, so the assertion above is only meaningful
        because the rendered form it replaced is NOT redacted."""
        rendered = f"Received event: {json.dumps(_event(), default=str)}"
        assert SECRET_PROMPT in json.dumps(mask_sensitive_data(rendered))

    def test_the_content_keys_are_kept_with_a_redacted_value(self):
        """The current remedy logs the object, so the walk applies and the trail still shows WHICH
        fields were submitted -- a stronger record than omitting them."""
        logged = _logged_first_argument(_event())
        if not isinstance(logged, dict):
            pytest.skip("the handler logs identifiers only; there is no walked structure to check")
        template = mask_sensitive_data(logged)[
            "ResourceProperties"]["inlineBundle"]["templates"][0]
        assert template["configBody"] == REDACTED
        assert template["webFormJson"] == REDACTED
        assert template["inputInstructions"] == REDACTED
        assert template["templateId"] == "t1"

    def test_the_identifiers_needed_to_debug_a_deployment_are_still_logged(self):
        """Control against over-redaction: the record must still identify the request."""
        logged = json.dumps(mask_sensitive_data(_logged_first_argument(_event())))
        assert "Create" in logged
        assert "genai" in logged

    def test_a_direct_invoke_without_a_request_type_leaks_no_content_either(self):
        """The self-registration path is the one an external caller reaches, so it must not have a
        weaker logging shape."""
        event = {"inlineBundle": {"templates": [{"configBody": SECRET_PROMPT}]}}
        logged = _logged_first_argument(event)
        assert SECRET_PROMPT not in json.dumps(mask_sensitive_data(logged))


@pytest.mark.unit
class TestImportEventLoggingStringDeliveredBundle:
    """A bundle delivered as a JSON string is the shape CloudFormation produces for a structured
    property and the shape ``assemble_bundle`` accepts, so it must redact the same as the object form."""

    def test_a_json_string_bundle_leaks_no_template_content(self):
        masked = _masked(_string_bundle_event())
        assert SECRET_PROMPT not in masked
        assert SECRET_FORM not in masked
        assert SECRET_INSTRUCTIONS not in masked

    def test_an_update_leaks_no_content_from_the_prior_string_bundle(self):
        """An Update carries the previous revision's properties as OldResourceProperties, which holds a
        second copy of the bundle."""
        event = _string_bundle_event("OldResourceProperties")
        event["ResourceProperties"] = {}
        assert SECRET_PROMPT not in _masked(event)

    def test_a_direct_invoke_json_string_bundle_leaks_no_content(self):
        """On the self-registration path the bundle sits at the top level of the event, not under
        ResourceProperties."""
        bundle = _event()["ResourceProperties"]["inlineBundle"]
        assert SECRET_PROMPT not in _masked({"inlineBundle": json.dumps(bundle)})

    def test_a_string_bundle_that_does_not_parse_is_not_logged_verbatim(self):
        """A truncated bundle still carries the content keys, and nothing can walk it -- registration
        fails on it later, but the log line is written first."""
        truncated = '{"templates": [{"configBody": "' + SECRET_PROMPT + '"}'
        assert SECRET_PROMPT not in _masked({"ResourceProperties": {"inlineBundle": truncated}})

    def test_a_delete_is_never_blocked_by_a_bundle_the_log_view_cannot_parse(self):
        """A Delete archives best-effort so teardown is never blocked, and the log line is written
        before that safety net. ``json.loads`` raises past ValueError -- a value nested deeper than the
        interpreter's recursion limit raises RecursionError -- so a parse failure while building the
        log view must not become the exception that fails the stack delete."""
        depth = sys.getrecursionlimit() * 20
        event = {"RequestType": "Delete",
                 "ResourceProperties": {"inlineBundle": "[" * depth + "]" * depth}}
        recorder = MagicMock()
        with patch.object(imp, "logger", recorder), \
             patch.object(imp, "archive_bundle", return_value={"ids": {}, "warnings": []}):
            response = imp.lambda_handler(event, MagicMock(log_stream_name="stream"))
        assert "Data" in response
        logged = json.dumps(mask_sensitive_data(recorder.info.call_args_list[0].args[0]))
        assert "inlineBundle" in logged, "the record must still show a bundle was submitted"

    def test_the_key_walk_alone_does_not_redact_a_string_bundle(self):
        """Positive control on the mechanism: the redaction is key-driven, so ``inlineBundle`` holding a
        JSON string is opaque to it. Without this the assertions above could be passing because
        mask_sensitive_data strips the value from any input at all."""
        raw = _string_bundle_event()
        assert SECRET_PROMPT in json.dumps(mask_sensitive_data(raw))

    def test_the_ids_inside_a_string_bundle_are_still_logged(self):
        """Control against over-redaction: redacting the whole inlineBundle would also pass the
        assertions above, and would leave a deployment failure with nothing to identify it by."""
        masked = _masked(_string_bundle_event())
        assert "genai" in masked
        assert "t1" in masked
        assert REDACTED in masked

    def test_the_registration_still_receives_the_string_bundle_verbatim(self):
        """Blast radius: assemble_bundle parses a string inlineBundle itself, so the log view must be a
        copy -- rewriting the event in place would change what registration is handed."""
        event = _string_bundle_event()
        original = json.loads(json.dumps(event))
        _, register = _invoke_handler(event)
        assert event == original, "the handler must not mutate the event it was given"
        assert register.call_args.args[0]["inlineBundle"] == original[
            "ResourceProperties"]["inlineBundle"]
        assert isinstance(register.call_args.args[0]["inlineBundle"], str)

    def test_an_s3_key_delivered_bundle_still_logs_its_keys(self):
        """Positive control for the deploy path CDK actually uses: bundleS3Keys carries no content
        keys, so it must reach the log intact."""
        event = {"RequestType": "Create",
                 "ResourceProperties": {"bundleS3Keys": json.dumps(
                     {"pipeline": "vamsSchema/genai/pipeline.json"})}}
        masked = _masked(event)
        assert "vamsSchema/genai/pipeline.json" in masked
