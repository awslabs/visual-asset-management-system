# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A trigger type other than ``manual`` is accepted only from a system launch.

``fileUpload`` records that the upload trigger dispatcher started the run; it invokes the execute handler
directly with a top-level ``lambdaCrossCall`` whose ``userName`` is SYSTEM_USER, which API Gateway cannot add
to a proxy event. Any other request naming it is refused with a 400 and a generic message before anything is
read or started; a user's re-run (a cross-call carrying the user's own name) is not a system launch.

A system trigger value (``System-<Identifier>``, #1135) is never a request value, whoever sends it: the
vector-search reindex launcher's runs are recorded as ``System-Reindex`` through the marked system-pipeline
call (``source`` ``vamsSystemPipeline`` and the top-level ``systemExecution``), and its request body is an
ordinary manual one. The earlier release's request value ``systemReindex`` is refused like any unknown one.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.handlers.workflows.test_execute_per_input_file_version_lock import (  # noqa: F401
    MOD, WF_DB, WF_ID, _TWO_FILES, _allow_enforcer, _event, ewv2,
)

from backend.backend.common.workflows import systemPipelines as sp
from backend.backend.common.workflows import triggerMatching as tm


def _cross_call(body, user_name):
    event = _event(body)
    event.pop("headers", None)
    event["lambdaCrossCall"] = {"userName": user_name}
    return event


def _run(event, tokens=("user1",)):
    launched = MagicMock(return_value=ewv2.success(body={"message": {"executionId": "e1"}}))
    with patch(f"{MOD}.CasbinEnforcer", return_value=_allow_enforcer()), \
            patch(f"{MOD}.request_to_claims", return_value={"tokens": list(tokens)}), \
            patch(f"{MOD}.execute_workflow", launched):
        response = ewv2.lambda_handler(event, MagicMock())
    return response, launched


@pytest.mark.unit
class TestSystemTriggerTypes:
    @pytest.mark.parametrize("trigger_type", ["fileUpload"])
    def test_a_user_request_naming_a_system_trigger_type_is_refused(self, trigger_type):
        response, launched = _run(_event(dict(_TWO_FILES, triggerType=trigger_type)))
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == ewv2.SYSTEM_TRIGGER_TYPE_MESSAGE
        assert trigger_type not in response["body"]
        assert not launched.called

    @pytest.mark.parametrize("trigger_type", ["fileUpload"])
    def test_a_users_own_cross_call_is_not_a_system_launch(self, trigger_type):
        # The execution re-run delegates as the calling user; it may not carry a system trigger type.
        response, launched = _run(_cross_call(dict(_TWO_FILES, triggerType=trigger_type), "alice"),
                                  tokens=("alice",))
        assert response["statusCode"] == 400 and not launched.called

    @pytest.mark.parametrize("trigger_type", ["fileUpload"])
    def test_a_system_launch_may_name_a_system_trigger_type(self, trigger_type):
        response, launched = _run(_cross_call(dict(_TWO_FILES, triggerType=trigger_type), "SYSTEM_USER"),
                                  tokens=("SYSTEM_USER",))
        assert response["statusCode"] == 200 and launched.call_count == 1
        assert launched.call_args.args[3].triggerType == trigger_type

    @pytest.mark.parametrize("trigger_type", ["systemReindex", "System-Reindex"])
    @pytest.mark.parametrize("user", ["alice", "SYSTEM_USER"])
    def test_a_reindex_trigger_value_in_the_request_is_refused_for_everyone(self, trigger_type, user):
        # The request model refuses it before the handler reads anything; a marked system call carries
        # the stored value in systemExecution, never in the body.
        event = _cross_call(dict(_TWO_FILES, triggerType=trigger_type), user)
        event["lambdaCrossCall"]["source"] = sp.SYSTEM_PIPELINE_SOURCE_MARKER
        response, launched = _run(event, tokens=(user,))
        assert response["statusCode"] == 400 and not launched.called

    @pytest.mark.parametrize("body", [dict(_TWO_FILES), dict(_TWO_FILES, triggerType="manual"),
                                      dict(_TWO_FILES, triggerType="")])
    def test_a_manual_run_is_accepted_from_anyone(self, body):
        response, launched = _run(_event(body))
        assert response["statusCode"] == 200 and launched.call_count == 1
        assert launched.call_args.args[3].triggerType == "manual"


@pytest.mark.unit
class TestEveryInternalCallerSendsTheSystemEnvelope:
    """The two internal callers that set a system trigger type build the envelope the handler accepts."""

    @staticmethod
    def _client():
        client = MagicMock()
        client.invoke.return_value = {"Payload": MagicMock(read=lambda: json.dumps({"statusCode": 200}).encode())}
        return client

    def test_the_upload_trigger_dispatcher(self):
        from tests.handlers.workflows.test_wb5b_trigger_dispatch import wd

        client = self._client()
        with patch.object(wd, "lambda_client", client):
            assert wd._invoke_execute(WF_DB, WF_ID, {"triggerType": "fileUpload"}) is True
        event = json.loads(client.invoke.call_args.kwargs["Payload"])
        assert ewv2._is_system_cross_call(event)
        assert json.loads(event["body"])["triggerType"] == "fileUpload"

    def test_the_vector_reindex_launcher_sends_the_marked_system_pipeline_call(self):
        from tests.handlers.osVectorSearch.vectorsearch_support import load_handler

        launcher = load_handler("systemWorkflowLauncher")
        launcher.lambda_client = self._client()
        with patch.object(launcher, "build_launch_body", return_value={"triggerType": "manual"}), \
                patch.object(launcher, "slot_candidates", return_value=[1]):
            launcher.launch({"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb",
                             "reindexRunId": "r1", "chunk": 0})
        event = json.loads(launcher.lambda_client.invoke.call_args.kwargs["Payload"])
        assert sp.is_system_pipeline_call(event)
        assert json.loads(event["body"])["triggerType"] == "manual"
        trigger, context = sp.resolve_system_execution(event)
        assert trigger == "System-Reindex" and sp.is_system_trigger(trigger)
        assert json.loads(context) == {"chunk": 0, "reindexRunId": "r1"}
        assert sp.resolve_concurrency_slots(event) == (launcher.reindex_slot_count, [1])
        assert "reindexSlots" not in event["lambdaCrossCall"]

    def test_the_trigger_body_names_the_upload_trigger_type(self):
        body = tm.build_trigger_execute_body({"defaultTemplateIds": {}}, "db1", "a1", "/f.glb")
        assert body["triggerType"] == "fileUpload"
