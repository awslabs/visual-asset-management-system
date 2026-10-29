#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""No Fargate `.sync` container writes the Step Functions task token into its log output.

The three Fargate pipeline containers -- coordinate transform, 3D thumbnail (whose image the system
GenAI metadata pipeline's Fargate render branch reuses), and the Potree point cloud viewer (PDAL +
Potree images share one entry point) -- receive their `PipelineDefinition` on argv, and that dataclass
carries `externalSfnTaskToken`: the bearer
credential the parent workflow's task waits on. Rendering the whole definition into a log line
(`f"Pipeline Definition: {definition}"`) therefore writes the raw token to stdout, which lands in the
container's CloudWatch group -- since the Fargate log-group change that group is VAMS-owned, KMS
encrypted, and retained for a year. The pipeline **Lambda** loggers redact the token
(`test_pipeline_logger_formatter.py`), but the containers use plain `logging` through their own
`utils/logging` module, so that redactor never reaches them.

Two checks per container:

- `test_run_logs_job_name_not_token` drives the real `core.run()` with a token-bearing definition and
  captures what the root logger emits up to the first stage step (the definition's `stages.pop(0)`
  is made to raise, so nothing past the entry log lines runs and no S3 or SFN call is reached). The
  token must be absent, the job name present.
- `test_no_log_call_interpolates_whole_definition` walks the `core.py` AST: no logging call may
  interpolate the bare `definition` name into an f-string or pass it as a positional argument. This
  catches a re-introduced whole-object log anywhere in the module, not only at the entry.

Each container is imported as a submodule of a synthetic package rooted at its container directory
(the `pcPotreeViewer` conftest technique), so its relative imports resolve to its own `utils` and the
three never collide in one interpreter. Heavy render libraries are stubbed where `core.py` reaches them
at import time; nothing here needs them.
"""

import ast
import base64
import dataclasses
import hashlib
import importlib
import importlib.machinery
import importlib.util
import logging
import os
import sys
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PIPELINES = os.path.join(_REPO_ROOT, "backendPipelines")

# A real task token is one run of URL-safe base64 several hundred characters long; 640 here.
TOKEN = base64.b64encode(hashlib.sha512(b"container-definition-log").digest() * 8).decode()
assert len(TOKEN) >= 600

JOB_NAME = "vams-container-definition-log-job"

# (id, container dir, dotted path of the module holding `run()`, third-party modules to stub)
# The container dir is the package root the image runs with (`python3 -m <pkg>`), so the dotted path
# is exactly the one the image imports, minus the package name.
CONTAINERS = (
    (
        "coordinateTransform",
        "conversion/coordinateTransform/container",
        "coord_transform_pipeline.core",
        (),
    ),
    (
        "3dThumbnail",
        "preview/3dThumbnail/container",
        "preview_pipeline.core",
        ("numpy", "pyvista", "vtk", "trimesh", "imageio", "PIL", "PIL.Image", "laspy", "pye57",
         "cadquery", "DracoPy", "open3d", "pxr", "scipy"),
    ),
    (
        "pcPotreeViewer",
        "preview/pcPotreeViewer/container",
        "pipelines.core",
        (),
    ),
)

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")  # nosec B105 - test-only placeholder


def _load_core(container_id, container_rel, dotted, stubs):
    """Import `<dotted>` as a submodule of a synthetic package rooted at the container directory.

    The package name is private to this suite and to the container, so two containers whose entry
    module shares a dotted name (`pipelines.core` exists in more than one image) cannot resolve to
    each other's files, and nothing here shadows a per-container suite's own package entry.
    """
    for name in stubs:
        sys.modules.setdefault(name, MagicMock())
    pkg_name = "container_definition_log_" + container_id
    if pkg_name not in sys.modules:
        spec = importlib.machinery.ModuleSpec(pkg_name, None, is_package=True)
        spec.submodule_search_locations = [os.path.join(_PIPELINES, container_rel)]
        sys.modules[pkg_name] = importlib.util.module_from_spec(spec)
    return importlib.import_module(f"{pkg_name}.{dotted}")


class _Stop(Exception):
    """Raised from the definition's stage list to end run() right after its entry log lines."""


class _StagesThatStop(list):
    def pop(self, *_):
        raise _Stop()


def _params(core):
    """The definition as constructPipeline hands it to the container, with a live-shaped token.

    Built from the container's OWN dataclass fields: the coordinate transform definition still carries
    the manifest inline (`inputMetadata` / `inputParameters`) where the other two carry S3
    locations, and a keyword the dataclass lacks would fail construction before the log line.
    """
    fields = dataclasses.fields(core.PipelineDefinition)
    params = {"jobName": JOB_NAME, "stages": _StagesThatStop([{"type": "unused"}])}
    for field in fields:
        if field.name in params or field.name in ("completedStages", "currentStage"):
            continue
        if field.name == "externalSfnTaskToken":
            params[field.name] = TOKEN
        elif field.name == "localTest":
            params[field.name] = "False"
        elif field.name.endswith("S3Location"):
            params[field.name] = f"s3://bucket/{field.name}.json"
        elif field.default is dataclasses.MISSING:
            params[field.name] = "{}"
    return params


def _stub_manifest_reads(core, monkeypatch):
    """The 3D thumbnail container reads the manifest from S3 before its first stage step; the read is
    not under test and must not reach the network."""
    manifest_io = getattr(core, "manifest_io", None)
    if manifest_io is None:
        return
    for name in ("fetch_metadata", "fetch_input_configuration"):
        if hasattr(manifest_io, name):
            monkeypatch.setattr(manifest_io, name, lambda *_: {})


@pytest.mark.parametrize("container_id,container_rel,dotted,stubs", CONTAINERS, ids=[c[0] for c in CONTAINERS])
def test_run_logs_job_name_not_token(container_id, container_rel, dotted, stubs, caplog, monkeypatch):
    core = _load_core(container_id, container_rel, dotted, stubs)
    _stub_manifest_reads(core, monkeypatch)

    caplog.set_level(logging.DEBUG)
    with pytest.raises(_Stop):
        core.run(_params(core))

    emitted = "\n".join(record.getMessage() for record in caplog.records)
    assert emitted, f"{container_id}: run() emitted nothing before its first stage step"
    assert TOKEN not in emitted, f"{container_id}: the raw task token reached the container log"
    assert "externalSfnTaskToken" not in emitted, (
        f"{container_id}: the definition was rendered whole into the container log"
    )
    assert JOB_NAME in emitted, f"{container_id}: the job name is no longer logged at entry"


def _names_in(node):
    """Every bare `Name` id reachable from `node` (f-string values, call arguments, formats)."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _is_logging_call(call):
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in ("debug", "info", "warning", "error", "exception", "critical", "log")
        and isinstance(func.value, ast.Name)
        and func.value.id in ("logger", "log", "logging")
    )


@pytest.mark.parametrize("container_id,container_rel,dotted,stubs", CONTAINERS, ids=[c[0] for c in CONTAINERS])
def test_no_log_call_interpolates_whole_definition(container_id, container_rel, dotted, stubs):
    path = os.path.join(_PIPELINES, container_rel, *dotted.split(".")) + ".py"
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)

    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_logging_call(node)):
            continue
        for arg in node.args:
            # An f-string `{definition}` is a FormattedValue whose value is the bare Name; a
            # positional `definition` is the bare Name itself. Either renders the whole dataclass.
            # `{definition.jobName}` is an Attribute whose value is the Name -- allowed, and excluded
            # here by looking only at direct FormattedValue values and direct arguments.
            direct = [arg] if isinstance(arg, ast.Name) else [
                fv.value for fv in ast.walk(arg) if isinstance(fv, ast.FormattedValue)
            ]
            if any(isinstance(d, ast.Name) and d.id == "definition" for d in direct):
                offenders.append(node.lineno)

    assert not offenders, (
        f"{container_id}: {os.path.relpath(path, _REPO_ROOT)} logs the whole PipelineDefinition "
        f"(carries externalSfnTaskToken) at line(s) {offenders}; log definition.jobName instead"
    )
