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


def _logged_first_argument(event):
    """Invoke the handler and return the first positional argument it gave logger.info."""
    recorder = MagicMock()
    with patch.object(imp, "logger", recorder), \
         patch.object(imp, "register_bundle", return_value={"ids": {}, "applied": []}), \
         patch.object(imp, "_physical_id", return_value="pid"):
        imp.lambda_handler(event, MagicMock(log_stream_name="stream"))
    assert recorder.info.call_args_list, "the handler must log the event it received"
    return recorder.info.call_args_list[0].args[0]


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
