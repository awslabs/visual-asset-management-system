# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every line a Lambda writes carries the handler's own service name.

Every Powertools logger in a Lambda process is one underlying logger with one formatter, so the
service name it carries labels the handler's lines and the lines of every shared module the handler
imports. Shared modules therefore set no name of their own, and the logger created in the Lambda
entrypoint module (the runtime's `_HANDLER`) keeps its name even when another handler module is
imported as a library later.

The suite's conftest replaces `safeLogger` with a stand-in, so the real handlers are imported in a
fresh interpreter, in the order a Lambda cold start runs.
"""

import ast
import json
import os
import subprocess
import sys
import textwrap

import pytest

BACKEND_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

# Shared code that runs inside other Lambdas' processes and logs under their service name.
SHARED_PACKAGES = ("common", "models", "customLogging", "customConfigCommon")

_SCRIPT = textwrap.dedent(
    """
    import importlib, os, sys
    sys.path.insert(0, {src!r})
    from common.resourceNames import ResourceKeys, ResourceParamKey
    for key in vars(ResourceKeys).values():
        if isinstance(key, ResourceParamKey):
            os.environ.setdefault(key.env_var_names[0], "test-" + key.param_key.replace("/", "-"))
    handler = importlib.import_module({module!r})
    handler.logger.info("handler line")
    for extra in {late_imports!r}:
        importlib.import_module(extra)
    handler.logger.info("handler line after late imports")
    import common.s3, common.dynamodb, models.common, customLogging.auditLogging
    common.s3.logger.info("shared common line")
    models.common.logger.info("shared models line")
    customLogging.auditLogging.logger.info("shared audit line")
    """
)


def _run(module, late_imports=(), entrypoint=True):
    script = _SCRIPT.format(src=BACKEND_SRC, module=module, late_imports=list(late_imports))
    # No AWS credentials or config reach the child, so nothing it imports can call AWS.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "POWERTOOLS_", "_HANDLER"))}
    env.update(
        AWS_REGION="us-east-1",
        AWS_DEFAULT_REGION="us-east-1",
        AWS_EC2_METADATA_DISABLED="true",
        AWS_CONFIG_FILE=os.devnull,
        AWS_SHARED_CREDENTIALS_FILE=os.devnull,
        SNS_TOPIC_ARN="arn:aws:sns:us-east-1:000000000000:test-topic",
    )
    if entrypoint:
        env["_HANDLER"] = module + ".lambda_handler"
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, env=env
    )
    assert result.returncode == 0, result.stderr[-2000:]
    lines = {}
    for raw in result.stdout.splitlines():
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        lines[line.get("message")] = line
    return lines


_PROBES = (
    "handler line",
    "handler line after late imports",
    "shared common line",
    "shared models line",
    "shared audit line",
)

# A sample across handler styles: `service=` and `service_name=` callers, a handler whose package
# __init__ creates a logger of its own first, one that lazily imports other handler modules, and the
# indexing queue handler that uses the batch item failure helper.
SAMPLE_HANDLERS = (
    ("handlers.sendEmail.sendEmail", "SendEmail"),
    ("handlers.comments.addComment", "AddComment"),
    ("handlers.auth.pretokengenv2", "PreTokenGen"),
    ("handlers.addon.physna.physnaViewer", "PhysnaViewer"),
    ("handlers.databases.databaseService", "DatabaseService"),
    ("handlers.workflows.workflowService", "WorkflowService"),
    ("handlers.metadata.metadataService", "MetadataService"),
    ("handlers.indexing.snsQueuing", "SnsQueuing"),
)


@pytest.mark.parametrize("module,service", SAMPLE_HANDLERS)
def test_handler_and_shared_module_lines_carry_the_handler_service_name(module, service):
    lines = _run(module)

    for probe in _PROBES:
        assert probe in lines, f"{probe!r} was not logged"
        assert lines[probe].get("service_name") == service, (probe, lines[probe])
        assert lines[probe].get("service") == service, (probe, lines[probe])


def test_handler_module_imported_lazily_as_a_library_does_not_relabel_the_entrypoint():
    # physnaViewer imports handlers.auth and handlers.authz on first use; both create loggers.
    lines = _run(
        "handlers.addon.physna.physnaViewer",
        late_imports=("handlers.auth", "handlers.authz", "handlers.comments.addComment"),
    )

    assert lines["handler line after late imports"]["service_name"] == "PhysnaViewer"
    assert lines["shared common line"]["service_name"] == "PhysnaViewer"


def test_without_the_runtime_handler_variable_the_entrypoint_name_still_wins_at_cold_start():
    # Local runs have no _HANDLER: the entrypoint module body runs after its imports, so its name
    # is the last one set, including over a package __init__ that names a logger of its own.
    lines = _run("handlers.auth.pretokengenv2", entrypoint=False)

    assert {lines[probe]["service_name"] for probe in _PROBES} == {"PreTokenGen"}


def _named_safe_logger_calls(path):
    with open(path, encoding="utf-8") as source:
        tree = ast.parse(source.read(), filename=path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "safeLogger":
            continue
        if node.args or any(kw.arg in ("service", "service_name") for kw in node.keywords):
            yield node.lineno


def test_no_shared_module_sets_a_service_name():
    offenders = []
    for package in SHARED_PACKAGES:
        for root, _dirs, files in os.walk(os.path.join(BACKEND_SRC, package)):
            for filename in files:
                if filename.endswith(".py"):
                    path = os.path.join(root, filename)
                    offenders.extend(
                        f"{os.path.relpath(path, BACKEND_SRC)}:{line}"
                        for line in _named_safe_logger_calls(path)
                    )

    assert offenders == [], (
        "Shared modules log under the calling handler's service name; call safeLogger() without "
        "service/service_name: " + ", ".join(offenders)
    )
