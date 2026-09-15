#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""No module of this pipeline renders its whole state, event or task token into a log line.

Every hop of the pipeline carries the parent workflow's Step Functions task token in its state
(`sfnExternalTaskToken` on the nested-invoke payload, `externalSfnTaskToken` on the state machine
input and every Lambda event after it). The vendored logger redacts those keys and the token's shape,
but a handler that logs `f"Event: {event}"` or `json.dumps(sfn_input)` depends on that redaction
holding across every spelling and every rendering; the rule here is that no log call renders one of
these variables whole in the first place -- identifiers, keys and counts are logged instead.

The check walks the AST of every module under `lambda/` and `containers/` (tests excluded): a
logging call is an offender when a positional argument or a keyword value is one of the guarded
names, an f-string interpolating one, `json.dumps(<name>)`, `str(<name>)` / `repr(<name>)` of one,
or `<name>` concatenated into a string. `event.get("assetId")`, `sorted(event)` and
`event["jobName"]` are attribute/subscript/function forms that render a part of the value and pass.

`test_the_rule_catches_each_forbidden_rendering` is the control: every shape the rule names is
shown to be reported on a synthetic module, so a pass on the real tree is a pass of the rule and not
of a scanner that matches nothing.
"""

import ast
import os
import textwrap

import pytest

import sysgenai_harness as h

_SCAN_ROOTS = (
    os.path.join(h.PIPELINE_ROOT, "lambda"),
    os.path.join(h.PIPELINE_ROOT, "containers"),
)
_SKIP_DIRS = {"tests", "__pycache__", ".pytest_cache", "customLogging"}

# Names whose value is (or carries) the whole pipeline state or the task token.
GUARDED_NAMES = frozenset({
    "event", "state", "sfn_input", "sfn_response", "payload", "detail",
    "task_token", "external_sfn_task_token", "externalSfnTaskToken", "sfnExternalTaskToken",
    "TaskToken", "taskToken",
})
_LOGGING_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
_LOGGER_NAMES = {"logger", "log", "logging", "LOGGER"}
# Callables that render their argument whole.
_RENDERERS = {"str", "repr", "format"}


def _guarded_name(node):
    return isinstance(node, ast.Name) and node.id in GUARDED_NAMES


def _renders_whole(node):
    """True when `node` evaluates to a whole guarded value or a rendering of one."""
    if _guarded_name(node):
        return True
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) and _renders_whole(v.value) for v in node.values)
    if isinstance(node, ast.Call):
        func = node.func
        # json.dumps(x), str(x), repr(x)
        if isinstance(func, ast.Attribute) and func.attr == "dumps" and node.args:
            return _renders_whole(node.args[0])
        if isinstance(func, ast.Name) and func.id in _RENDERERS and node.args:
            return _renders_whole(node.args[0])
        # "text {}".format(x)
        if isinstance(func, ast.Attribute) and func.attr == "format":
            return any(_renders_whole(a) for a in node.args)
        return False
    if isinstance(node, ast.BinOp):
        # "... " + x, "%s" % x
        return _renders_whole(node.left) or _renders_whole(node.right)
    return False


def _is_logging_call(call):
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _LOGGING_METHODS
        and isinstance(func.value, ast.Name)
        and func.value.id in _LOGGER_NAMES
    )


def offenders_in(source, filename="<module>"):
    """`(lineno, rendering)` for every logging call in `source` that renders a guarded value whole."""
    tree = ast.parse(source, filename=filename)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_logging_call(node)):
            continue
        for arg in node.args:
            if _renders_whole(arg):
                found.append((node.lineno, ast.unparse(arg)))
        for kw in node.keywords:
            if _renders_whole(kw.value):
                found.append((node.lineno, f"{kw.arg}={ast.unparse(kw.value)}"))
    return found


def _modules():
    for root_dir in _SCAN_ROOTS:
        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for name in sorted(files):
                if name.endswith(".py"):
                    yield os.path.join(root, name)


@pytest.mark.unit
class TestNoWholeStateLogs:
    def test_the_scan_covers_the_handlers(self):
        rel = {os.path.relpath(m, h.PIPELINE_ROOT).replace(os.sep, "/") for m in _modules()}
        for expected in ("lambda/openPipeline.py", "lambda/constructPipeline.py",
                         "lambda/generateMetadata.py", "lambda/generateEmbedding.py",
                         "lambda/pipelineEnd.py", "lambda/vamsExecuteSystemGenAiMetadataPipeline.py",
                         "containers/media/handler.py", "containers/media/segment_handler.py",
                         "containers/blender/handler.py"):
            assert expected in rel, f"{expected} is not under the scanned roots"

    def test_no_module_logs_a_whole_event_state_or_token(self):
        report = []
        for path in _modules():
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            for lineno, rendering in offenders_in(source, path):
                report.append(f"{os.path.relpath(path, h.PIPELINE_ROOT)}:{lineno}: {rendering}")
        assert report == [], (
            "log calls that render the whole state/event/token (log ids, keys and counts instead):\n  "
            + "\n  ".join(report)
        )

    def test_the_rule_catches_each_forbidden_rendering(self):
        """[control] every rendering the rule names is reported on a synthetic module."""
        source = textwrap.dedent('''
            logger.info(event)                                   # 2 bare name
            logger.info(f"Event: {event}")                       # 3 f-string
            logger.info(f"SFN Input: {json.dumps(sfn_input)}")   # 4 dumps inside f-string
            logger.info(json.dumps(state))                       # 5 dumps
            logger.info("token: " + task_token)                  # 6 concatenation
            logger.info("Event %s" % event)                      # 7 percent
            logger.info("Event {}".format(event))                # 8 str.format
            logger.info(str(sfn_response))                       # 9 str()
            logger.info("Event", event=event)                    # 10 keyword value
            logger.error(event["error"])                         # ok: a part
            logger.info("Event", assetId=event.get("assetId"))   # ok: a part
            logger.info("Keys", keys=sorted(event))              # ok: a function of the value
            logger.info(f"Job {event['jobName']}")               # ok: a part
            logger.info(f"Context: {context}")                   # ok: not guarded
        ''')
        lines = sorted(lineno for lineno, _ in offenders_in(source))
        assert lines == [2, 3, 4, 5, 6, 7, 8, 9, 10]
