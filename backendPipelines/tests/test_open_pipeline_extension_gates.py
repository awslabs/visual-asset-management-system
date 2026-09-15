#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The input-extension gate of every pipeline's openPipeline handler.

`ALLOWED_INPUT_FILEEXTENSIONS` arrives as one comma-joined string from the pipeline's CDK construct,
so the gate has to test membership of the PARSED list. `in` applied to the joined string is
substring containment: every strict prefix of a listed extension passes it (`.mp` against
`.zip,.mp4,.mov`), as does every span crossing a comma (`.stl,.obj`). A file admitted that way
provisions the pipeline's compute — a GPU Batch job on several of these — and is then rejected by the
container.

Every pipeline is covered by the same cases so the property holds for a new pipeline copied from an
existing one, which is how the loose form spread in the first place.
"""

import importlib.util
import os
import sys
import types
import datetime
import re
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Every pipeline shipping an openPipeline handler with this gate, and the construct that supplies its
# allow list. Named explicitly rather than globbed: a glob that stops matching reads as "all pipelines
# pass".
PIPELINES = (
    ("splatToolbox", "backendPipelines/3dRecon/splatToolbox",
     "infra/lib/nestedStacks/pipelines/3dRecon/splatToolbox/constructs/splatToolbox-construct.ts"),
    ("coordinateTransform", "backendPipelines/conversion/coordinateTransform",
     "infra/lib/nestedStacks/pipelines/conversion/coordinateTransform/constructs/"
     "coordinateTransform-construct.ts"),
    ("metadata3dLabeling", "backendPipelines/genAi/metadata3dLabeling",
     "infra/lib/nestedStacks/pipelines/genAi/metadata3dLabeling/constructs/"
     "metadata3dLabeling-construct.ts"),
    ("modelOps", "backendPipelines/multi/modelOps",
     "infra/lib/nestedStacks/pipelines/multi/modelOps/constructs/modelOps-construct.ts"),
    ("rapidPipeline", "backendPipelines/multi/rapidPipeline",
     "infra/lib/nestedStacks/pipelines/multi/rapidPipeline/constructs/rapidPipeline-construct.ts"),
    ("preview3dThumbnail", "backendPipelines/preview/3dThumbnail",
     "infra/lib/nestedStacks/pipelines/preview/3dThumbnail/constructs/"
     "preview3dThumbnail-construct.ts"),
    ("pcPotreeViewer", "backendPipelines/preview/pcPotreeViewer",
     "infra/lib/nestedStacks/pipelines/preview/pcPotreeViewer/constructs/"
     "pcPotreeViewer-construct.ts"),
)

PIPELINE_IDS = [name for name, _, _ in PIPELINES]

# The allow list every case runs against. Three members, so the joined string carries substrings that
# name no format: prefixes of a member and spans crossing a comma.
_ALLOWED = ".stl,.obj,.glb"

# Each is a substring of _ALLOWED that names no supported format. The last one carries the comma, so
# it is a span of the joined list that no member could ever be. A span reaching across a comma into
# the NEXT member is not representable here: os.path.splitext takes the extension from the last dot,
# so 'capture.stl,.obj' has the listed extension '.obj'.
_SUBSTRINGS_THAT_ARE_NOT_FORMATS = (".s", ".st", ".o", ".ob", ".g", ".gl", ".stl,")

_LISTED = (".stl", ".obj", ".glb", ".STL", ".Obj")

_TOKEN = "tok-extension-gate"

# customLogging is a per-pipeline package with an identical logger in each. Stubbed once so loading
# seven handlers does not depend on which pipeline's copy sys.path resolves first.
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
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/VAMSStateMachine-Test",
    "STATE_MACHINE_LOG_GROUP_ARN":
        "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/VAMSStateMachine-Test:*",
}.items():
    os.environ.setdefault(_k, _v)


def _load(name, lambda_dir_rel, allowed=_ALLOWED):
    """This pipeline's openPipeline module, executed fresh under a name only this suite uses.

    Every pipeline ships a module called openPipeline (and its own manifestHelper), so
    `import openPipeline` in one pytest process resolves to whichever lambda directory leads on
    sys.path — a suite can then assert against another pipeline's file and pass. Loading by path
    under a unique name and asserting the origin is what rules that out.
    """
    lambda_dir = os.path.join(_REPO_ROOT, *lambda_dir_rel.split("/"), "lambda")
    path = os.path.join(lambda_dir, "openPipeline.py")
    assert os.path.isfile(path), path

    # Each handler reads these at import, so they only need to hold while the module executes. They are
    # restored afterwards: another pipeline's suite sets its own allow list once at import and reads it
    # back on every load, so a value left behind here fails that suite instead of this one.
    overrides = {
        "STATE_MACHINE_ARN": f"arn:aws:states:us-east-1:1:stateMachine:{name}",
        "ALLOWED_INPUT_FILEEXTENSIONS": allowed,
    }
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)

    module_name = f"{name}_openPipeline_undertest"
    # manifestHelper is vendored per pipeline; drop any previously loaded copy so each handler
    # imports its own rather than the first one cached.
    sys.modules.pop("manifestHelper", None)
    sys.path.insert(0, lambda_dir)
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(lambda_dir)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    loaded = os.path.normcase(os.path.normpath(os.path.abspath(mod.__file__)))
    assert loaded == os.path.normcase(os.path.normpath(path)), (
        f"module shadow: loaded {mod.__file__}, expected {path}")
    return mod


def _event(file_uri):
    """A payload carrying every field any of these handlers reads directly."""
    return {
        "inputS3AssetFilePath": file_uri,
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/JOB/output/E1/files/",
        "outputS3AssetPreviewPath": "s3://abkt/pipelines/p1/JOB/output/E1/previews/",
        "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/JOB/output/E1/metadata/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xidM/model/pipeline",
        "inputMetadataS3Location": "s3://abkt/xidM/metadata.json",
        "inputConfigurationS3Location": "s3://abkt/xidM/config.json",
        "outputFileType": ".glb",
        "sfnExternalTaskToken": _TOKEN,
        "externalSfnTaskToken": _TOKEN,
        "orchestrationEventPrefix": "vams.test.execution.E1.pipeline.P1",
    }


def _invoke(mod, file_uri):
    """Run the handler with Step Functions and EventBridge stubbed.

    Returns (response, start_execution mock, send_task_failure mock)."""
    start = MagicMock(return_value={
        "executionArn": "arn:aws:states:us-east-1:1:execution:Pipeline:PipelineJob_x",
        "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
    })
    send_failure = MagicMock()
    with patch.object(mod.sfn, "start_execution", start), \
            patch.object(mod.sfn, "send_task_failure", send_failure), \
            patch.object(mod.events_client, "put_events", MagicMock()):
        resp = mod.lambda_handler(_event(file_uri), MagicMock())
    return resp, start, send_failure


def _cdk_allow_list(construct_rel):
    """The allow-list string the pipeline's construct passes to its openPipeline builder.

    Collects every quoted segment of the declaration so a value written across several lines reads
    the same as a single-line one.
    """
    path = os.path.join(_REPO_ROOT, *construct_rel.split("/"))
    assert os.path.isfile(path), path
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    match = re.search(r"const allowed(?:Input)?(?:File)?Extensions\s*=\s*([^;]*);", source)
    assert match, f"no allow-list declaration found in {path}"
    return "".join(re.findall(r'"([^"]*)"', match.group(1)))


@pytest.mark.unit
@pytest.mark.parametrize("name,lambda_dir,construct", PIPELINES, ids=PIPELINE_IDS)
class TestExtensionGate:
    @pytest.mark.parametrize("extension", _SUBSTRINGS_THAT_ARE_NOT_FORMATS)
    def test_substring_of_the_joined_allow_list_is_rejected(
            self, name, lambda_dir, construct, extension):
        mod = _load(name, lambda_dir)
        resp, start, send_failure = _invoke(mod, f"s3://abkt/xidM/capture{extension}")

        assert resp["statusCode"] == 400
        # The gate is what has to reject it: no state machine execution, so no compute is provisioned.
        start.assert_not_called()
        # The workflow task waits on the callback token, so the rejection is reported rather than
        # only returned.
        assert send_failure.call_count == 1
        assert send_failure.call_args.kwargs["taskToken"] == _TOKEN

    @pytest.mark.parametrize("extension", _LISTED)
    def test_listed_extension_is_accepted(self, name, lambda_dir, construct, extension):
        """The positive control: an exact member still runs, in either case.

        Without it, a gate that rejected everything would satisfy the assertions above.
        """
        mod = _load(name, lambda_dir)
        resp, start, send_failure = _invoke(mod, f"s3://abkt/xidM/capture{extension}")

        assert resp["statusCode"] == 200
        start.assert_called_once()
        send_failure.assert_not_called()

    def test_no_extension_is_rejected(self, name, lambda_dir, construct):
        mod = _load(name, lambda_dir)
        resp, start, _ = _invoke(mod, "s3://abkt/xidM/capture")

        assert resp["statusCode"] == 400
        start.assert_not_called()

    def test_whitespace_around_list_members_is_tolerated(self, name, lambda_dir, construct):
        """A list written with spaces still matches, so the gate does not reject a supported format
        on formatting alone."""
        mod = _load(name, lambda_dir, allowed=".stl, .obj , .glb")
        resp, start, _ = _invoke(mod, "s3://abkt/xidM/capture.obj")

        assert resp["statusCode"] == 200
        start.assert_called_once()

    def test_cdk_allow_list_is_multi_member(self, name, lambda_dir, construct):
        """The deployed allow list really is a comma-joined string of several extensions.

        This is what makes the substring hazard live rather than theoretical: a single-member list
        has no cross-comma substring, so the cases above would be exercising a shape the deployment
        never has.
        """
        allowed = _cdk_allow_list(construct)

        assert "," in allowed, allowed
        members = [member.strip() for member in allowed.split(",")]
        assert len(members) >= 2
        for member in members:
            assert member.startswith("."), members
