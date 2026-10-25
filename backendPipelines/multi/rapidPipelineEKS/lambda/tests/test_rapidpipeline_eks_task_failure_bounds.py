#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""What the multi/rapidPipelineEKS lambdas send AWS Step Functions `SendTaskFailure`, and that it fits.

`SendTaskFailure` accepts an `error` of at most 256 characters and a `cause` of at most 32768, and
the service rejects a longer value with a `ValidationException`. botocore checks only the minimum
length of a string, so an oversized value leaves the client and is refused by the service. Every
failure callback in these lambdas sits behind an `except` that logs and carries on, so a refused
callback does not fail loudly: the parent workflow's task token is never reported, its task waits for
its full taskTimeout, and the error the run ended with is never delivered.

The Step Functions client below enforces both limits the way the service does and records only the
calls it accepts, so an assertion on what it accepted is an assertion on what the parent workflow
receives. A MagicMock would record an oversized call exactly as readily as a valid one.

The last class is a guard over the source: every `send_task_failure` call in `consolidated_handler.py`
and `openPipeline.py` takes its `error` and `cause` from `taskFailureBounds.bounded_task_failure`. It
is a durable rule: any later edit can write a raw `error=` / `cause=` again.
"""

import ast
import importlib
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, ParamValidationError

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for _k, _v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "EKS_CLUSTER_NAME": "test-cluster",
    "CONTAINER_IMAGE_URI": "123456789012.dkr.ecr.us-east-1.amazonaws.com/rapid-pipeline:latest",
    "KUBERNETES_NAMESPACE": "default",
}.items():
    os.environ.setdefault(_k, _v)

# The SendTaskFailure limits, from the AWS Step Functions API reference. Stated here rather than read
# from the module under test, so a wrong constant there cannot make these tests agree with it.
ERROR_MAX_CHARS = 256
CAUSE_MAX_CHARS = 32768

TOKEN = "task-token-value-for-the-parent-workflow"
CAUSE_HEAD = "RPDX-CAUSE-HEAD Lambda error message comes first. "


class LimitEnforcingStepFunctions:
    """A Step Functions client that refuses the `SendTaskFailure` values the real one refuses.

    A value that is not a string is refused before any request is made, as botocore's parameter
    validation does (`None` included). A string over its limit is refused with the
    `ValidationException` the service returns. Only accepted calls are recorded as delivered.
    """

    def __init__(self):
        self.accepted_failures = []
        self.rejected_failures = []
        self.accepted_successes = []

    def send_task_failure(self, **kwargs):
        for name in ("error", "cause"):
            if name in kwargs and not isinstance(kwargs[name], str):
                self.rejected_failures.append(kwargs)
                raise ParamValidationError(
                    report=(f"Invalid type for parameter {name}, value: {kwargs[name]!r}, "
                            f"type: {type(kwargs[name])}, valid types: <class 'str'>"))
        for name, limit in (("error", ERROR_MAX_CHARS), ("cause", CAUSE_MAX_CHARS)):
            if len(kwargs.get(name) or "") > limit:
                self.rejected_failures.append(kwargs)
                raise ClientError(
                    {"Error": {
                        "Code": "ValidationException",
                        "Message": (f"1 validation error detected: Value at '{name}' failed to "
                                    f"satisfy constraint: Member must have length less than or "
                                    f"equal to {limit}"),
                    }},
                    "SendTaskFailure",
                )
        self.accepted_failures.append(kwargs)
        return {}

    def send_task_success(self, **kwargs):
        self.accepted_successes.append(kwargs)
        return {}


def _ctx():
    ctx = MagicMock()
    ctx.aws_request_id = "req-test"
    ctx.get_remaining_time_in_millis.return_value = 300000
    return ctx


def _load_handler():
    if "consolidated_handler" in sys.modules:
        return importlib.reload(sys.modules["consolidated_handler"])
    return importlib.import_module("consolidated_handler")


def _load_bounds():
    if "taskFailureBounds" in sys.modules:
        return importlib.reload(sys.modules["taskFailureBounds"])
    return importlib.import_module("taskFailureBounds")


def _long_cause(length):
    return (CAUSE_HEAD + "c" * length)[:length]


def _pipeline_end(error):
    """Run PIPELINE_END through the lambda entry point against the limit-enforcing client."""
    mod = _load_handler()
    sfn = LimitEnforcingStepFunctions()
    event = {
        "operation": "PIPELINE_END",
        "jobName": "PipelineJobEKS_x",
        "externalSfnTaskToken": TOKEN,
        "error": error,
    }
    with patch.object(mod, "sfn", sfn), patch.object(mod.time, "sleep", MagicMock()):
        result = mod.lambda_handler(event, _ctx())
    return result, sfn


@pytest.mark.unit
class TestPipelineEndDeliversTheFailure:
    def test_a_cause_over_the_limit_is_delivered_cut_and_marked(self):
        result, sfn = _pipeline_end({"Error": "PipelineExecutionFailed", "Cause": _long_cause(40000)})

        assert len(sfn.accepted_failures) == 1, (
            f"the parent workflow received {len(sfn.accepted_failures)} failures and "
            f"{len(sfn.rejected_failures)} calls were refused")
        sent = sfn.accepted_failures[0]
        assert sent["taskToken"] == TOKEN
        assert sent["error"] == "PipelineExecutionFailed"
        assert len(sent["cause"]) <= CAUSE_MAX_CHARS
        assert sent["cause"].startswith(CAUSE_HEAD), "the head of the cause, where the error is, was lost"
        assert sent["cause"].endswith("[truncated from 40000 characters]")
        assert result["statusCode"] == 200
        assert result["body"]["notification"] == {"success": True, "error": None}

    def test_a_cause_at_the_limit_is_delivered_unchanged(self):
        cause = _long_cause(CAUSE_MAX_CHARS)
        result, sfn = _pipeline_end({"Error": "PipelineExecutionFailed", "Cause": cause})

        assert len(sfn.accepted_failures) == 1
        assert sfn.accepted_failures[0]["cause"] == cause
        assert result["body"]["notification"]["success"] is True

    def test_an_error_name_over_the_limit_is_delivered_cut(self):
        error = "E" * 300
        result, sfn = _pipeline_end({"Error": error, "Cause": "short cause"})

        assert len(sfn.accepted_failures) == 1
        sent = sfn.accepted_failures[0]
        assert len(sent["error"]) <= ERROR_MAX_CHARS
        assert sent["error"].startswith("E" * 200)
        assert sent["error"].endswith("[truncated from 300 characters]")
        assert sent["cause"] == "short cause"
        assert result["body"]["notification"]["success"] is True

    def test_values_that_are_not_strings_are_delivered_as_text(self):
        # botocore refuses a None or a dict for a string parameter before sending anything, which
        # leaves the token unreported exactly as an oversized value does.
        result, sfn = _pipeline_end({"Error": None, "Cause": {"errorMessage": "boom"}})

        assert len(sfn.accepted_failures) == 1
        sent = sfn.accepted_failures[0]
        assert sent["error"] == "None"
        assert sent["cause"] == str({"errorMessage": "boom"})
        assert result["body"]["notification"]["success"] is True


@pytest.mark.unit
class TestCheckJobDeliversTheFailure:
    def test_an_oversized_status_check_error_is_delivered(self):
        # The CHECK_JOB exception path carries the raised error's text, which nothing else bounds.
        mod = _load_handler()
        sfn = LimitEnforcingStepFunctions()
        event = {
            "operation": "CHECK_JOB",
            "jobName": "PipelineJobEKS_x",
            "k8sJobName": "rapid-pipeline-job-abcd1234",
            "externalSfnTaskToken": TOKEN,
            "counter": 0,
        }
        with patch.object(mod, "sfn", sfn), \
                patch.object(mod, "check_job_status", side_effect=RuntimeError("k" * 40000)):
            mod.lambda_handler(event, _ctx())

        assert len(sfn.accepted_failures) == 1
        sent = sfn.accepted_failures[0]
        assert sent["error"] == "JobCheckError"
        assert len(sent["cause"]) <= CAUSE_MAX_CHARS
        assert sent["cause"].startswith("Failed to check job status after 1 attempts. Error: kkk")


@pytest.mark.unit
class TestOpenPipelineDeliversTheFailure:
    def test_an_error_over_the_limit_is_delivered_cut(self):
        # openPipeline puts its own message in `error`, so an input path in that message is enough to
        # pass 256 characters: an S3 key can be 1024 bytes long.
        env = {
            "AWS_REGION": "us-east-1",
            "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:123456789012:stateMachine:rapid-pipeline-x",
            "ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.gltf,.fbx,.obj,.stl,.ply,.usd,.usdz,.dae,.abc",
        }
        with patch.dict(os.environ, env):
            if "openPipeline" in sys.modules:
                mod = importlib.reload(sys.modules["openPipeline"])
            else:
                mod = importlib.import_module("openPipeline")

        input_uri = "s3://abkt/xidM/" + "k" * 600
        event = {
            "inputS3AssetFilePath": input_uri,
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/pipelines/p1/MJOB/output/E1/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/MJOB/output/E1/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/eks/p1/",
            "sfnExternalTaskToken": TOKEN,
        }
        sfn = LimitEnforcingStepFunctions()
        with patch.object(mod, "sfn", sfn), patch("time.sleep", MagicMock()):
            result = mod.lambda_handler(event, _ctx())

        assert result["statusCode"] == 400
        assert len(sfn.accepted_failures) == 1, (
            f"the parent workflow received {len(sfn.accepted_failures)} failures and "
            f"{len(sfn.rejected_failures)} calls were refused")
        sent = sfn.accepted_failures[0]
        assert sent["taskToken"] == TOKEN
        assert len(sent["error"]) <= ERROR_MAX_CHARS
        assert sent["error"].startswith("Pipeline Failure: Input file has no extension: s3://abkt/xidM/")
        assert "[truncated from " in sent["error"]
        assert sent["cause"].startswith("See AWS CloudWatch logs for detailed error cause.")
        assert "extension_validation" in sent["cause"]


@pytest.mark.unit
class TestBoundedTaskFailure:
    def test_values_within_the_limits_are_returned_unchanged(self):
        bounds = _load_bounds()
        error, cause = "E" * ERROR_MAX_CHARS, _long_cause(CAUSE_MAX_CHARS)
        assert bounds.bounded_task_failure(error, cause) == (error, cause)

    def test_a_cause_one_over_the_limit_is_cut_to_the_limit(self):
        bounds = _load_bounds()
        original = _long_cause(CAUSE_MAX_CHARS + 1)
        _, cause = bounds.bounded_task_failure("PipelineExecutionFailed", original)
        assert len(cause) == CAUSE_MAX_CHARS
        assert cause.endswith(f" ... [truncated from {CAUSE_MAX_CHARS + 1} characters]")
        kept = cause[: cause.index(" ... [truncated from ")]
        assert original.startswith(kept)

    def test_an_error_one_over_the_limit_is_cut_to_the_limit(self):
        bounds = _load_bounds()
        original = "E" * (ERROR_MAX_CHARS + 1)
        error, cause = bounds.bounded_task_failure(original, "cause")
        assert len(error) == ERROR_MAX_CHARS
        assert error.endswith(f" ... [truncated from {ERROR_MAX_CHARS + 1} characters]")
        assert error.startswith("E" * 200)
        assert cause == "cause"

    def test_a_very_long_value_is_cut_to_the_limit(self):
        bounds = _load_bounds()
        error, cause = bounds.bounded_task_failure("E" * 5_000_000, "c" * 5_000_000)
        assert len(error) == ERROR_MAX_CHARS
        assert len(cause) == CAUSE_MAX_CHARS

    def test_none_and_other_types_become_text(self):
        bounds = _load_bounds()
        assert bounds.bounded_task_failure(None, None) == ("None", "None")
        assert bounds.bounded_task_failure(42, {"k": "v"}) == ("42", "{'k': 'v'}")

    def test_the_module_limits_match_the_api_reference(self):
        bounds = _load_bounds()
        assert bounds.SEND_TASK_FAILURE_ERROR_MAX_CHARS == ERROR_MAX_CHARS
        assert bounds.SEND_TASK_FAILURE_CAUSE_MAX_CHARS == CAUSE_MAX_CHARS


# ---------------------------------------------------------------------------------------------------
# Guard over the source
# ---------------------------------------------------------------------------------------------------

HELPER = "bounded_task_failure"
HELPER_MODULE = "taskFailureBounds"

# Each file that reports a task failure, and the number of send_task_failure calls it holds. The
# counts keep the guard from passing because it found nothing to check.
GUARDED_SOURCES = (
    ("consolidated_handler.py", 4),
    ("openPipeline.py", 1),
)


def _parents(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing_function(node, parents):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _bindings(scope, name):
    """Every node under `scope` that binds `name`: assignment targets, parameters, imports, `as`."""
    found = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == name:
            found.append(node)
        elif isinstance(node, ast.arg) and node.arg == name:
            found.append(node)
        elif isinstance(node, ast.ExceptHandler) and node.name == name:
            found.append(node)
        elif isinstance(node, ast.alias) and (node.asname or node.name.split(".")[0]) == name:
            found.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.name == name:
            found.append(node)
    return found


def _is_helper_call(node):
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == HELPER


def _is_helper_slot(target, position, parents):
    """True when `target` is slot `position` of `error, cause = bounded_task_failure(...)`."""
    tup = parents.get(target)
    assign = parents.get(tup)
    return (
        isinstance(tup, ast.Tuple)
        and len(tup.elts) == 2
        and tup.elts[position] is target
        and isinstance(assign, ast.Assign)
        and assign.targets == [tup]
        and _is_helper_call(assign.value)
    )


def _from_helper(name_node, position, func, parents):
    """True when every binding of this name in `func` is slot `position` of a helper unpack.

    Every binding, not only the nearest: a function may report from two branches with the same
    names, and any other assignment to the name is a path by which an unbounded value can arrive.
    """
    if not isinstance(name_node, ast.Name):
        return False
    bindings = _bindings(func, name_node.id)
    return bool(bindings) and all(_is_helper_slot(b, position, parents) for b in bindings)


def unbounded_failure_calls(source):
    """Every `send_task_failure` call in `source` whose `error` or `cause` does not come from the
    helper, as `(line, reason)`, plus the number of calls examined."""
    tree = ast.parse(source)
    parents = _parents(tree)
    problems = []
    examined = 0

    imports_helper = any(
        isinstance(node, ast.ImportFrom) and node.module == HELPER_MODULE
        and any(alias.name == HELPER and alias.asname is None for alias in node.names)
        for node in tree.body
    )
    if not imports_helper:
        problems.append((0, f"does not import {HELPER} from {HELPER_MODULE} at module level"))
    helper_bindings = len(_bindings(tree, HELPER))
    if helper_bindings != 1:
        problems.append((0, f"binds the name {HELPER} {helper_bindings} times, expected once"))

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "send_task_failure"):
            continue
        examined += 1
        keywords = {kw.arg: kw.value for kw in node.keywords}
        if node.args or None in keywords:
            problems.append((node.lineno, "passes positional or ** arguments"))
            continue
        func = _enclosing_function(node, parents)
        if func is None:
            problems.append((node.lineno, "is not inside a function"))
            continue
        for position, name in enumerate(("error", "cause")):
            if name not in keywords:
                problems.append((node.lineno, f"sends no {name}"))
            elif not _from_helper(keywords[name], position, func, parents):
                problems.append((node.lineno, f"{name}= does not come from {HELPER}"))
    return problems, examined


@pytest.mark.unit
class TestEveryFailureCallIsBounded:
    @pytest.mark.parametrize("filename,expected_calls", GUARDED_SOURCES)
    def test_every_send_task_failure_takes_its_values_from_the_helper(self, filename, expected_calls):
        with open(os.path.join(_LAMBDA_DIR, filename), encoding="utf-8") as handle:
            problems, examined = unbounded_failure_calls(handle.read())
        assert examined == expected_calls, (
            f"{filename}: found {examined} send_task_failure calls, expected {expected_calls}. "
            f"Update GUARDED_SOURCES when a call is added or removed.")
        assert problems == [], f"{filename}: " + "; ".join(
            f"line {line} {reason}" for line, reason in problems)

    @pytest.mark.parametrize("snippet", [
        # A raw value.
        "def f(t, c):\n    sfn.send_task_failure(taskToken=t, error='E', cause=c)\n",
        # The helper's result overwritten before the call.
        "def f(t, e, c):\n    error, cause = bounded_task_failure(e, c)\n    cause = c\n"
        "    sfn.send_task_failure(taskToken=t, error=error, cause=cause)\n",
        # The two results swapped.
        "def f(t, e, c):\n    error, cause = bounded_task_failure(e, c)\n"
        "    sfn.send_task_failure(taskToken=t, error=cause, cause=error)\n",
        # Values passed through a ** mapping.
        "def f(t, kw):\n    sfn.send_task_failure(taskToken=t, **kw)\n",
    ])
    def test_the_guard_flags_a_value_that_bypasses_the_helper(self, snippet):
        source = f"from {HELPER_MODULE} import {HELPER}\n" + snippet
        problems, examined = unbounded_failure_calls(source)
        assert examined == 1
        assert problems, f"the guard did not flag {snippet!r}"

    def test_the_guard_accepts_the_bounded_form(self):
        source = (
            f"from {HELPER_MODULE} import {HELPER}\n"
            "def f(t, e, c):\n"
            f"    error, cause = {HELPER}(e, c)\n"
            "    sfn.send_task_failure(taskToken=t, error=error, cause=cause)\n"
        )
        assert unbounded_failure_calls(source) == ([], 1)
