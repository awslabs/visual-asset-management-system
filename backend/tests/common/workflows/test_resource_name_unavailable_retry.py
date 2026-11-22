# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A workflow step waits out a refused resource-name lookup instead of failing the run (#1187).

The scaffolding states of every workflow (process-output, the large-output wait, finalize, the interim
pipeline tracking and the error handler) invoke VAMS Lambdas that resolve their resource names at
import. When Parameter Store throttles a burst of cold starts, the resolver refuses the lookup with
``ResourceNameUnavailableError``, the Lambda's init fails and Step Functions reports that error name
for the task. The states retry it with long jittered back-off, ahead of any ``States.ALL`` retrier,
while the pipeline task states (whose functions do not use ``common.resourceNames``) are unchanged.

The retriers are matched the way Step Functions matches them: the first retrier whose ErrorEquals
names the error, or a wildcard that covers it, decides.
"""

import ast
import json
import os

import pytest

from backend.backend.common.workflows import stepfunctions_builder as sfb
from backend.backend.common.workflows import workflowAsl as wa
from backend.backend.common.workflows import workflowAslBuilder as asl
from backend.tests.common.workflows.test_pipeline_task_retry_policy import _matching_retrier

ERROR = "ResourceNameUnavailableError"
BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

# The handlers behind the scaffolding states.
SCAFFOLDING_HANDLERS = (
    "handlers/workflows/sfn/processWorkflowExecutionOutput.py",
    "handlers/workflows/sfn/interimPipelineTracking.py",
    "handlers/workflows/sfn/handleExecutionError.py",
)

_TYPES = {
    "Lambda": ({"resourceId": "arn:fn", "resourceType": "Lambda"}, {}),
    "SQS": ({"resourceId": "https://sqs.us-east-1.amazonaws.com/1/q", "resourceType": "SQS"}, {}),
    "EventBridge": ({"resourceId": "default", "resourceType": "EventBridge"}, {}),
    "DeadlineCloud": ({
        "resourceType": "DeadlineCloud",
        "deadlineFarmId": "farm-" + "0" * 32,
        "deadlineQueueId": "queue-" + "0" * 32,
        "deadlineTemplate": "name: VamsJob\nspecificationVersion: jobtemplate-2023-09",
        "deadlineTemplateType": "YAML",
    }, {"waitForCallback": "Enabled", "taskTimeout": "7200"}),
}


def _definition(exec_type="Lambda", n_pipelines=1):
    resource, extra = _TYPES[exec_type]
    pipelines = [dict({
        "name": f"p{i}", "pipelineId": f"p{i}", "outputType": "assetFile",
        "pipelineExecutionType": exec_type, "pipelineType": "standardFile", "databaseId": "db",
        "waitForCallback": "Disabled", "userProvidedResource": json.dumps(resource),
    }, **extra) for i in range(n_pipelines)]
    definition, _jobs = asl.generate_workflow_asl(
        pipelines, "db", "wf", process_workflow_output_function="t-po",
        interim_tracking_function="t-interim", error_handler_function="t-err", aws_partition="aws")
    return definition


def _scaffolding_states(definition):
    """{kind: [state, ...]} for every state that invokes a scaffolding Lambda."""
    kinds = {}
    for name, state in definition["States"].items():
        if name.startswith("process-outputs-"):
            kind = "process-output"
        elif name.startswith("interim-"):
            kind = "interim"
        elif name in (asl.AWAIT_INGESTION_STATE_ID, asl.FINALIZE_OUTPUTS_STATE_ID, "HandleExecutionError"):
            kind = name
        else:
            continue
        kinds.setdefault(kind, []).append(state)
    return kinds


def _pipeline_states(definition):
    return [state for name, state in definition["States"].items() if name.startswith("step")]


_CASES = [(t, n) for t in _TYPES for n in (1, 3)]
_IDS = [f"{t}-{n}" for t, n in _CASES]


@pytest.mark.unit
class TestEveryScaffoldingStateWaitsOutARefusal:
    @pytest.mark.parametrize("exec_type,n", _CASES, ids=_IDS)
    def test_the_error_is_matched_by_the_resource_name_retrier(self, exec_type, n):
        kinds = _scaffolding_states(_definition(exec_type, n))
        expected = {"process-output", asl.AWAIT_INGESTION_STATE_ID, asl.FINALIZE_OUTPUTS_STATE_ID,
                    "HandleExecutionError"} | ({"interim"} if n > 1 else set())
        assert set(kinds) == expected
        assert sum(len(states) for states in kinds.values()) == len(expected) + max(0, n - 2)
        for kind, states in kinds.items():
            for state in states:
                assert _matching_retrier(state["Retry"], ERROR) == asl.RESOURCE_NAME_UNAVAILABLE_RETRIER, kind

    @pytest.mark.parametrize("exec_type,n", _CASES, ids=_IDS)
    def test_pipeline_task_states_are_unchanged(self, exec_type, n):
        steps = _pipeline_states(_definition(exec_type, n))
        assert len(steps) == n
        for state in steps:
            assert all(ERROR not in r["ErrorEquals"] for r in state["Retry"])

    def test_the_error_name_is_the_resolver_class_name(self):
        # Step Functions reports a Lambda's unhandled error by its errorType, the exception class name.
        from common.resourceNames import ResourceNameUnavailableError
        assert ResourceNameUnavailableError.__name__ == asl.RESOURCE_NAME_UNAVAILABLE_ERROR == ERROR


@pytest.mark.unit
class TestPositionAndTheExistingRetriers:
    def test_output_states_keep_throttling_first_and_then_their_own_retriers(self):
        kinds = _scaffolding_states(_definition())
        [po] = kinds["process-output"]
        assert [r["ErrorEquals"] for r in po["Retry"]] == [
            asl.THROTTLE_RETRIER["ErrorEquals"], [ERROR], asl.ENGINE_BUSY_RETRIER["ErrorEquals"],
            asl.PROCESS_OUTPUT_RETRY_ERRORS]
        for kind in (asl.AWAIT_INGESTION_STATE_ID, asl.FINALIZE_OUTPUTS_STATE_ID):
            [state] = kinds[kind]
            assert state["Retry"][0] == asl.THROTTLE_RETRIER
            assert state["Retry"][1] == asl.RESOURCE_NAME_UNAVAILABLE_RETRIER
            assert state["Retry"][2] == asl.ENGINE_BUSY_RETRIER
        assert po["Retry"][3]["MaxAttempts"] == 3 and po["Retry"][3]["IntervalSeconds"] == 5

    def test_interim_and_error_handler_put_it_ahead_of_their_catch_all(self):
        kinds = _scaffolding_states(_definition(n_pipelines=2))
        [interim] = kinds["interim"]
        [handler] = kinds["HandleExecutionError"]
        default_interim = sfb.create_interim_tracking_state("i", "f", {}, "$.x", "h")["Retry"]
        default_handler = sfb.create_error_handler_state("h", "f", {}, "fail")["Retry"]
        assert interim["Retry"] == [asl.RESOURCE_NAME_UNAVAILABLE_RETRIER, *default_interim]
        assert handler["Retry"] == [asl.RESOURCE_NAME_UNAVAILABLE_RETRIER, *default_handler]
        # Every other error still reaches the state's own States.ALL retrier.
        assert _matching_retrier(interim["Retry"], "Lambda.ServiceException") == default_interim[0]
        assert _matching_retrier(handler["Retry"], "Exception") == default_handler[0]

    def test_the_retrier_is_copied_per_state(self):
        kinds = _scaffolding_states(_definition(n_pipelines=3))
        retriers = [r for states in kinds.values() for s in states for r in s["Retry"] if r["ErrorEquals"] == [ERROR]]
        assert len(retriers) == len({id(r) for r in retriers}) == 6
        assert all(r is not asl.RESOURCE_NAME_UNAVAILABLE_RETRIER for r in retriers)


@pytest.mark.unit
class TestReach:
    def test_the_attempts_outlast_the_observed_throttle(self):
        retrier = asl.RESOURCE_NAME_UNAVAILABLE_RETRIER
        assert retrier["JitterStrategy"] == "FULL"
        caps = [min(retrier["IntervalSeconds"] * retrier["BackoffRate"] ** n, retrier["MaxDelaySeconds"])
                for n in range(retrier["MaxAttempts"])]
        # With full jitter each delay is uniform in [0, cap]. On a test deployment the burst kept
        # Parameter Store throttled for about 150 s: the mean total wait is well past it and the
        # longest one stays bounded.
        assert sum(caps) / 2 >= 240
        assert sum(caps) <= 15 * 60
        assert retrier["MaxDelaySeconds"] <= 60


# ASL Retrier field rules (Amazon States Language specification, "Retrying after an error").
_RETRIER_FIELDS = {"ErrorEquals", "IntervalSeconds", "BackoffRate", "MaxAttempts", "MaxDelaySeconds",
                   "JitterStrategy"}


def _assert_valid_retry_list(retriers):
    assert isinstance(retriers, list) and retriers
    for index, retrier in enumerate(retriers):
        assert set(retrier) <= _RETRIER_FIELDS, retrier
        names = retrier["ErrorEquals"]
        assert isinstance(names, list) and names and all(isinstance(n, str) and n for n in names)
        if "States.ALL" in names:
            assert names == ["States.ALL"] and index == len(retriers) - 1, retriers
        for field in ("IntervalSeconds", "MaxDelaySeconds"):
            if field in retrier:
                assert isinstance(retrier[field], int) and 1 <= retrier[field] <= 31622401
        if "MaxAttempts" in retrier:
            assert isinstance(retrier["MaxAttempts"], int) and 0 <= retrier["MaxAttempts"] <= 99999999
        if "BackoffRate" in retrier:
            assert retrier["BackoffRate"] >= 1.0
        if "JitterStrategy" in retrier:
            assert retrier["JitterStrategy"] in ("FULL", "NONE")


@pytest.mark.unit
class TestTheDefinitionStaysValid:
    @pytest.mark.parametrize("exec_type,n", _CASES, ids=_IDS)
    def test_every_retry_list_follows_the_asl_rules(self, exec_type, n):
        definition = _definition(exec_type, n)
        retry_lists = [s["Retry"] for s in definition["States"].values() if "Retry" in s]
        assert len(retry_lists) >= 5
        for retriers in retry_lists:
            _assert_valid_retry_list(retriers)
        json.dumps(definition)


@pytest.mark.unit
class TestDeployedWorkflowsPickItUp:
    def test_the_schema_version_moved(self):
        assert asl.ASL_SCHEMA_VERSION == wa.ASL_SCHEMA_VERSION == 4
        assert "aslSchemaVersion=4" in _definition()["Comment"]

    def test_a_workflow_deployed_before_the_retrier_is_redeployed_at_launch(self):
        record = {"databaseId": "db", "pipelineId": "p1", "executionConfig": {"executionType": "Lambda"}}
        deployed = {"workflowVersionArn": "arn:sm:1", "aslSchemaVersion": "3",
                    "pipelineFingerprints": wa.workflow_pipeline_fingerprints([record]),
                    "outputIngestionWaitHours": str(wa._max_wait_hours())}
        assert wa.fingerprints_match(deployed, [record]) is True
        assert wa.deployment_matches(deployed, [record]) is False


def _resolver_calls(tree):
    """(line, inside a function, inside a try that catches Exception) for every resolver call."""
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if name not in ("get_table_name", "get_bucket_name", "get_log_group_name", "get_resource_name"):
            continue
        current, in_function, caught = node, False, False
        while current in parents:
            parent = parents[current]
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                in_function = True
            # A try inside the function: one around the function itself does not guard the lookup.
            if isinstance(parent, ast.Try) and current in parent.body and not in_function:
                caught = caught or any(h.type is None or ast.unparse(h.type) in ("Exception", "BaseException")
                                       for h in parent.handlers)
            current = parent
        found.append((node.lineno, in_function, caught))
    return found


@pytest.mark.unit
class TestTheRefusalCanOnlyComeFromInit:
    """The retrier is safe because a scaffolding handler raises ResourceNameUnavailableError only while
    its module loads, before its handler runs. A lookup added inside a function would be refused after
    the handler may have written something; this pins that every such lookup is inside a try that
    catches it."""

    @pytest.mark.parametrize("relative_path", SCAFFOLDING_HANDLERS)
    def test_names_are_resolved_at_import_and_lazy_lookups_are_caught(self, relative_path):
        with open(os.path.join(BACKEND, *relative_path.split("/")), encoding="utf-8") as handle:
            calls = _resolver_calls(ast.parse(handle.read()))
        assert any(not in_function for _line, in_function, _caught in calls), "no import-time lookup"
        uncaught = [line for line, in_function, caught in calls if in_function and not caught]
        assert uncaught == [], f"{relative_path} resolves a name inside a function outside a try: {uncaught}"
