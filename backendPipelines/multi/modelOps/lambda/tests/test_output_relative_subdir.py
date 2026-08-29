#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""FIX-051 for multi/modelOps: the container is told BOTH where to read its input and where to write
its result, and the result lands beside its source rather than at the asset root.

modelOps hands the container a configuration document instead of an ``aws s3 cp`` command line, so
every location it can reach is a block in that document. ``constructPipeline`` injects two:

*   ``state`` — the INPUT object, as ``bucket`` / ``prefix`` / ``name`` / ``extension``. Its
    ``extension`` is the input file's, which is what identifies this block as the source rather than
    the destination.
*   ``output`` — the DESTINATION, in the same four fields, built from the workflow's output-files
    prefix. Without it the only location in the document is the input's own prefix inside the live
    asset, so nothing the container produces can reach the prefix the workflow's process-output step
    reads, and a run reports SUCCESS having ingested nothing.

The assertions therefore test the property that does not depend on how either block is shaped: each
must ROUND-TRIP to the key it is meant to address — the actual input object for ``state``, and a
staged key under the workflow's output-files prefix that preserves the input's asset-relative
subdirectory for ``output``. Both halves of one contract: an input the container can locate, and an
output that lands beside its source rather than at the asset root, where two same-named sources in
different folders would overwrite each other.

The same-format case is asserted separately. ModelOps optimizes as well as converts, and the shipped
templates target ``.glb`` / ``.gltf`` / ``.usdz`` — all three of which the pipeline also ACCEPTS as
input — so an output that kept both the input's subdirectory and its file name would have an
asset-relative path equal to the input's. The staged key differing is not enough: process-output
STRIPS the staging prefix and resolves what remains against the output asset's own location, so an
equal asset-relative path lands a new S3 version of the operator's source object. The write-back
resolution is asserted here through the backend's own ``apply_output_path_extension``.

FIX-051 covers all three conversion pipelines (rapidPipeline, rapidPipelineEKS, modelOps), which must
agree with the proven reference in ``conversion/3dBasic/lambdaContainer/lambda.py``.

The tests drive the FULL chain — vamsExecute -> openPipeline -> constructPipeline — because each hop
enumerates the fields it forwards, so asserting only on the last hop would let a fix pass while
openPipeline drops the threaded field in the middle. That constructPipeline really receives
openPipeline's Step Functions input verbatim is a property of the EMITTED state machine (its task
declares no payload, so ``"Payload.$": "$"``) and is asserted against the synthesized template in
``infra/test/multiPipelineConstructPipelinePayload.test.ts``.
"""

import os
import sys
import json
import types
import shlex
import importlib
import datetime
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

_REPO_ROOT = os.path.abspath(os.path.join(_LAMBDA_DIR, "..", "..", "..", ".."))

# Stub customLogging so the lambdas import without aws_lambda_powertools.
if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for k, v in {
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:ModelOps",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.gltf,.fbx,.obj",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/ModelOps",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/ModelOps:*",
}.items():
    os.environ.setdefault(k, v)

import vamsExecuteModelOps  # noqa: E402,F401
import openPipeline  # noqa: E402,F401
import constructPipeline  # noqa: E402,F401

FILES_PREFIX = "pipelines/p1/MJOB/output/E1/files/"
BUCKET = "abkt"
AUX_BUCKET = "aux"
ASSET_ID = "xidM"
# A multi-segment asset root (an external bucket's baseAssetsPrefix plus the asset id), so a
# derivation that merely drops the first key segment yields "area/xidM/..." and fails here.
ASSET_ROOT = f"org/area/{ASSET_ID}/"


def _load(name):
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


def _apply_output_path_extension():
    """Load the pure backend output-path-extension helper by path (no backend package, no boto3)."""
    import importlib.util
    module_path = os.path.join(
        _REPO_ROOT, "backend", "backend", "common", "workflows", "outputPathExtension.py")
    assert os.path.exists(module_path), module_path
    spec = importlib.util.spec_from_file_location("_ope_for_modelops_placement_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.apply_output_path_extension


def construct_same_format_subdir():
    """The folder constructPipeline adds for a conversion that does not change the file extension,
    read off the module under test so a rename of it does not silently stop being asserted. An empty
    value is rejected here, since it is exactly the value that would restore the collision."""
    module = sys.modules.get("constructPipeline") or importlib.import_module("constructPipeline")
    subdir = module.SAME_FORMAT_OUTPUT_SUBDIR.strip("/")
    assert subdir, "a same-format output needs a non-empty folder or it resolves onto its own source"
    return subdir


def _rendered_config(command):
    """The ModelOps configuration JSON the emitted command pipes into the handler.

    Tokenized with shlex first, so a value that was not shell-quoted fails here rather than being
    silently accepted — the config is derived from the asset key and is passed as one inert literal.
    """
    tokens = shlex.split(command)
    printf = tokens.index("printf")
    return json.loads(tokens[printf + 2])


def _addressed_key(block):
    """The S3 key a ``bucket``/``prefix``/``name``/``extension`` block addresses."""
    prefix = (block.get("prefix") or "").strip("/")
    name = block.get("name") or ""
    extension = (block.get("extension") or "").lstrip(".")
    leaf = f"{name}.{extension}" if extension else name
    return f"{prefix}/{leaf}" if prefix else leaf


class _Chain:
    """vamsExecute -> openPipeline -> constructPipeline, driven by one workflow manifest.

    No hop's event is hand-built: each is the object the previous hop actually passed to its AWS
    client.
    """

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/modelOps",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
        }

    def _manifest(self, relative_path):
        key = f"{ASSET_ROOT}{relative_path.lstrip('/')}"
        return {
            "inputFiles": [{
                "bucket": BUCKET,
                "key": key,
                "relativePath": relative_path,
                "assetId": ASSET_ID,
                "databaseId": "dbM",
                "assetRootS3Key": ASSET_ROOT,
            }],
            "outputs": {"bucket": BUCKET, "files": FILES_PREFIX},
            "auxBucket": AUX_BUCKET,
            "auxTempPrefix": "pipelines/modelOps/E1/",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }

    def _s3(self, manifest, config):
        def get_object(Bucket, Key):  # noqa: N803 - boto3 kwarg names
            if Key.endswith("manifest.json"):
                body = json.dumps(manifest).encode("utf-8")
            elif Key.endswith("config.json"):
                body = json.dumps(config).encode("utf-8")
            else:
                raise Exception(f"unexpected key {Key}")
            return {"Body": MagicMock(read=lambda b=body: b)}

        s3 = MagicMock()
        s3.get_object.side_effect = get_object
        s3.put_object = MagicMock()
        return s3

    def input_key(self, relative_path):
        return f"{ASSET_ROOT}{relative_path.lstrip('/')}"

    def sfn_input(self, relative_path, config=None):
        """The Step Functions input openPipeline started the state machine with — the event
        constructPipeline receives, since the task forwards the whole state input."""
        manifest = self._manifest(relative_path)
        config = {"outputType": ".glb"} if config is None else config

        # Hop 1: vamsExecute resolves the manifest and invokes openPipeline.
        execute_mod = _load("vamsExecuteModelOps")
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(execute_mod, "s3_client", self._s3(manifest, config)), \
                patch.object(execute_mod.lambda_client, "invoke", invoke):
            response = execute_mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert response["statusCode"] == 200, response
        open_event = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

        # Hop 2: openPipeline builds the Step Functions input.
        open_mod = _load("openPipeline")
        start = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:ModelOps:PipelineJob_x",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
        })
        with patch.object(open_mod.sfn, "start_execution", start), \
                patch.object(open_mod.events_client, "put_events", MagicMock()):
            response = open_mod.lambda_handler(open_event, MagicMock())
        assert response["statusCode"] == 200, response
        return open_event, json.loads(start.call_args.kwargs["input"])

    def run(self, relative_path, config=None):
        """Emitted container command for an input file at ``relative_path`` within the asset."""
        manifest = self._manifest(relative_path)
        config = {"outputType": ".glb"} if config is None else config
        _, sfn_input = self.sfn_input(relative_path, config)

        # Hop 3: constructPipeline emits the container command.
        construct_mod = _load("constructPipeline")
        with patch.object(construct_mod, "s3", self._s3(manifest, config)):
            out = construct_mod.lambda_handler(sfn_input, MagicMock())
        commands = out["commands"]
        assert isinstance(commands, list), commands
        return commands[2]

    def state(self, relative_path, config=None):
        return _rendered_config(self.run(relative_path, config)).get("state", {})

    def output(self, relative_path, config=None):
        return _rendered_config(self.run(relative_path, config)).get("output", {})

    def output_relative_path(self, relative_path, config=None):
        """The output block's key relative to the files prefix — the value the workflow's write-back
        keys on after stripping that prefix."""
        key = _addressed_key(self.output(relative_path, config))
        assert key.startswith(FILES_PREFIX), key
        return key[len(FILES_PREFIX):]

    def write_back_key(self, relative_path, config=None, extension="/"):
        """The asset-bucket key the workflow's write-back resolves this output to.

        Mirrors production: ``processWorkflowExecutionOutput.process_external_upload`` strips the
        staging files prefix, applies the execution's output path extension, and passes the result as
        ``relativeKey``; ``uploadFile.complete_external_upload`` resolves that against the asset's own
        ``assetLocation.Key``. ``"/"`` is the default extension and the one this pipeline's built-in
        workflow runs under, since its ``vamsSchema/workflow.json`` declares no
        ``defaultOutputFileBaseExecutionPathExtension``.
        """
        relative_key = _apply_output_path_extension()(
            self.output_relative_path(relative_path, config), extension)
        return f"{ASSET_ROOT}{relative_key}"


@pytest.mark.unit
class TestTheAssetIdSurvivesEveryHop(_Chain):
    """The placement reads the workflow's ``assetId``, and each hop forwards an explicit key list — so
    a key missing from any one of them is unrecoverable downstream while the later hops still read as
    correct."""

    def test_the_open_pipeline_invoke_payload_carries_the_asset_id(self):
        open_event, _ = self.sfn_input("/parts/housing/model.obj")
        assert open_event["assetId"] == ASSET_ID

    def test_open_pipeline_forwards_the_same_value_it_was_handed(self):
        """Compared against hop 1's payload rather than the constant, so a rename on either side of
        the hop fails here instead of silently resolving to no subdirectory."""
        open_event, sfn_input = self.sfn_input("/parts/housing/model.obj")
        assert sfn_input["assetId"] == open_event["assetId"]


@pytest.mark.unit
class TestStateBlockPreservesRelativeSubdirectory(_Chain):

    def test_chain_is_wired_root_level_input(self):
        """Harness control for FIX-051: with the input at the asset ROOT the state block already
        round-trips, so a failure in the subdirectory tests below is the defect, not a broken chain.
        Also the negative control against a fix that appends an empty segment unconditionally."""
        state = self.state("/model.obj")
        assert _addressed_key(state) == f"{ASSET_ROOT}model.obj"
        assert state["bucket"] == BUCKET
        assert "//" not in _addressed_key(state)

    def test_state_block_round_trips_to_the_actual_input_key(self):
        """FIX-051: the state block must address the real source object. For
        '<root>/parts/housing/model.obj' the prefix + name + extension must reassemble that key, not
        '<root>/model.obj'."""
        assert _addressed_key(self.state("/parts/housing/model.obj")) == \
            self.input_key("/parts/housing/model.obj")

    def test_same_basename_in_two_subdirectories_does_not_collide(self):
        """FIX-051: the collision this finding is about — '/a/model.obj' and '/b/model.obj' are two
        distinct files and must be described by two distinct state blocks."""
        first = self.state("/a/model.obj")
        second = self.state("/b/model.obj")
        assert _addressed_key(first) != _addressed_key(second)
        assert _addressed_key(first) == self.input_key("/a/model.obj")
        assert _addressed_key(second) == self.input_key("/b/model.obj")

    def test_injected_subdirectory_stays_a_single_inert_literal(self):
        """FIX-051: the config JSON is passed as one shlex.quote'd literal because json.dumps does not
        escape a single quote. A folder name carrying a quote, a space and shell metacharacters must
        survive tokenization intact — otherwise the change reopens the injection surface."""
        state = self.state("/pa rt's/$(whoami)/model.obj")
        assert _addressed_key(state) == self.input_key("/pa rt's/$(whoami)/model.obj")


@pytest.mark.unit
class TestOutputBlockNamesTheWorkflowDestination(_Chain):
    """The half of FIX-051 that decides whether anything the container produces is ever ingested: the
    document must name a destination, and it must be the workflow's output-files prefix."""

    def test_the_output_block_is_a_destination_the_workflow_reads(self):
        output = self.output("/parts/housing/model.obj")
        assert output, "the container document names no output destination at all"
        assert output["bucket"] == BUCKET
        # `pipelines/` is a reserved asset-bucket prefix; no asset's own files live under it, so this
        # is necessary (not sufficient) for process-output to find the result.
        assert _addressed_key(output).startswith(FILES_PREFIX)

    def test_the_output_is_not_the_live_asset_prefix_the_input_was_read_from(self):
        """The defect this replaces: with only the input's own prefix in the document, the container's
        one available location is inside the live asset."""
        output_key = _addressed_key(self.output("/parts/housing/model.obj"))
        assert not output_key.startswith(ASSET_ROOT)
        assert output_key != _addressed_key(self.state("/parts/housing/model.obj"))

    def test_the_output_preserves_the_input_relative_subdirectory(self):
        """FIX-051: '/parts/housing/model.obj' converted to .glb must stage at
        '<prefix>parts/housing/model.glb', so the write-back lands it beside its source."""
        assert _addressed_key(self.output("/parts/housing/model.obj")) == \
            f"{FILES_PREFIX}parts/housing/model.glb"
        assert self.output_relative_path("/parts/housing/model.obj") == "parts/housing/model.glb"

    def test_the_output_carries_the_target_extension_not_the_input_one(self):
        """The two blocks are input and output, and the extension is what tells them apart."""
        assert self.output("/parts/housing/model.obj")["extension"] == "glb"
        assert self.state("/parts/housing/model.obj")["extension"] == "obj"

    def test_the_write_back_places_the_converted_file_beside_its_source(self):
        """The end of the chain a trace stopping at the staged key never reaches: after process-output
        strips the staging prefix, the result resolves next to the source it was made from."""
        assert self.write_back_key("/parts/housing/model.obj") == \
            f"{ASSET_ROOT}parts/housing/model.glb"
        # A deployment that configures an output path extension gets it immediately before the file
        # name, so the file still lands inside the source's own folder.
        assert self.write_back_key("/parts/housing/model.obj", extension="/YOLO/") == \
            f"{ASSET_ROOT}parts/housing/YOLO/model.glb"

    def test_output_at_the_asset_root_gains_no_empty_segment(self):
        """Control: a root-level input must NOT gain a subdirectory. An unconditionally inserted
        segment emits a doubled or empty one here, and the asset root is the common case."""
        output_key = _addressed_key(self.output("/model.obj"))
        assert output_key == f"{FILES_PREFIX}model.glb"
        assert "//" not in output_key

    def test_same_basename_in_two_subdirectories_stages_to_two_destinations(self):
        first = _addressed_key(self.output("/a/model.obj"))
        second = _addressed_key(self.output("/b/model.obj"))
        assert first != second
        assert first == f"{FILES_PREFIX}a/model.glb"
        assert second == f"{FILES_PREFIX}b/model.glb"

    def test_the_output_destination_stays_a_single_inert_literal(self):
        """The destination is derived from the input key too, so it goes through the same quoting."""
        output = self.output("/pa rt's/$(whoami)/model.obj")
        assert _addressed_key(output) == f"{FILES_PREFIX}pa rt's/$(whoami)/model.glb"

    def test_a_direct_invoke_with_no_workflow_output_prefix_falls_back_to_the_aux_path(self):
        """A direct/local invoke carries no workflow output location. The auxiliary working path is
        the documented fallback, so the document still names a destination rather than defaulting to
        the live asset prefix."""
        _, sfn_input = self.sfn_input("/parts/housing/model.obj")
        sfn_input["outputS3AssetFilesPath"] = f"s3://{BUCKET}/"
        construct_mod = _load("constructPipeline")
        with patch.object(construct_mod, "s3",
                          self._s3(self._manifest("/parts/housing/model.obj"),
                                   {"outputType": ".glb"})):
            command = construct_mod.lambda_handler(sfn_input, MagicMock())["commands"][2]
        output = _rendered_config(command)["output"]
        assert output["bucket"] == AUX_BUCKET
        assert _addressed_key(output).endswith("parts/housing/model.glb")
        assert not _addressed_key(output).startswith(ASSET_ROOT)


@pytest.mark.unit
class TestSameFormatConversionDoesNotWriteItsOwnSource(_Chain):
    """ModelOps optimizes as well as converts, and all three shipped target formats are also accepted
    input formats, so ``.glb -> .glb`` is reachable by template. Preserving the subdirectory and
    keeping the file name would make the output's ASSET-RELATIVE path equal the input's — and that
    path, not the staged key, is what the write-back resolves against the output asset's location."""

    def test_the_write_back_never_resolves_to_the_input_object(self):
        relative_path = "/parts/housing/model.glb"
        input_key = self.input_key(relative_path)
        assert _addressed_key(self.output(relative_path)) != input_key
        # The load-bearing one: the staged key differing is not enough, because the write-back strips
        # the staging prefix. Also asserted for a configured output path extension, so a deployment
        # that sets one is covered rather than being what stands between an optimize run and a mutated
        # input.
        assert self.write_back_key(relative_path) != input_key
        assert self.write_back_key(relative_path, extension="/YOLO/") != input_key

    def test_the_asset_relative_path_differs_from_the_input_by_exactly_one_folder(self):
        """The inversion of the collision, asserted as a decomposition rather than a bare ``!=`` so it
        cannot be satisfied by dropping the subdirectory or by renaming the file — the two wrong ways
        to make the paths differ."""
        relative_path = "/parts/housing/model.glb"
        subdir = construct_same_format_subdir()
        assert self.output_relative_path(relative_path) == f"parts/housing/{subdir}/model.glb"
        assert self.write_back_key(relative_path) == \
            f"{ASSET_ROOT}parts/housing/{subdir}/model.glb"
        assert self.output(relative_path)["name"] == self.state(relative_path)["name"]

    def test_a_same_format_input_at_the_asset_root_also_avoids_its_source(self):
        """The asset root is the common case and has no subdirectory to hang the folder off, so it is
        the case an implementation that only qualifies a non-empty subdirectory would miss."""
        subdir = construct_same_format_subdir()
        assert self.output_relative_path("/model.glb") == f"{subdir}/model.glb"
        assert self.write_back_key("/model.glb") != self.input_key("/model.glb")
        assert "//" not in _addressed_key(self.output("/model.glb"))

    def test_a_format_changing_conversion_still_lands_directly_beside_its_source(self):
        """Control for the cases above: with the extensions differing there is no collision to avoid,
        so no folder is added. This is what makes the folder specific to the same-format case rather
        than a blanket change of where every conversion writes."""
        relative_path = "/parts/housing/model.obj"
        assert self.output_relative_path(relative_path) == "parts/housing/model.glb"
        assert construct_same_format_subdir() not in self.output_relative_path(relative_path)
        assert self.write_back_key(relative_path) == f"{ASSET_ROOT}parts/housing/model.glb"

    def test_a_run_naming_no_output_type_falls_back_to_the_input_extension(self):
        """A configuration with no outputType at all: the target format falls back to the input's, so
        it is the same-format case and takes the folder too. The config still reaches the container
        with its own keys intact — outputType is READ, never removed, because it is what selects the
        target format for the handler."""
        relative_path = "/parts/housing/model.obj"
        config = {"settings": {"quality": "high"}}
        subdir = construct_same_format_subdir()
        assert self.output_relative_path(relative_path, config) == \
            f"parts/housing/{subdir}/model.obj"
        assert self.write_back_key(relative_path, config) != self.input_key(relative_path)
        assert _rendered_config(self.run(relative_path, config))["settings"] == {"quality": "high"}

    def test_a_dotless_output_type_is_still_the_same_format(self):
        """`outputType` arrives as caller data, so its shape is not guaranteed.

        The input extension is derived from the S3 key and carries its dot; `outputType` is whatever
        the template or the caller wrote, and the emitted state/output blocks strip the dot
        themselves - so both spellings are already treated as equivalent elsewhere. Compared raw,
        "obj" against ".obj" reads as a format CHANGE, the folder is skipped, and the write-back
        resolves onto the operator's source object. This is the destructive case, reached through a
        value the operator controls.

        Asserted on the FOLDER and on the write-back differing from the input, not on the output's
        exact file name: a caller writing "OBJ" gets "model.OBJ", and pinning that spelling would
        pin incidental behaviour rather than the separation the folder provides.
        """
        relative_path = "/parts/housing/model.obj"
        subdir = construct_same_format_subdir()
        for spelling in ("obj", ".obj", "OBJ", ".OBJ"):
            config = {"outputType": spelling}
            output_relative = self.output_relative_path(relative_path, config)
            assert output_relative.startswith(f"parts/housing/{subdir}/"), (
                f"outputType={spelling!r} skipped the {subdir} folder: {output_relative}"
            )
            assert self.write_back_key(relative_path, config) != self.input_key(relative_path), (
                f"outputType={spelling!r} wrote onto the input object"
            )

    def test_the_target_format_selector_still_reaches_the_container(self):
        """outputType is the only value distinguishing the three shipped templates, so it must remain
        in the document handed to the handler."""
        assert _rendered_config(self.run("/parts/housing/model.obj"))["outputType"] == ".glb"
