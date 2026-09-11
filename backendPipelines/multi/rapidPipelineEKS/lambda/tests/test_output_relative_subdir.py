#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""FIX-051 for multi/rapidPipelineEKS: the converted output keeps the input file's subdirectory
within the asset, and never resolves onto the input object itself.

Asset files live at ``{assetRootS3Key}{relative_subdir}/{filename}``, and the workflow hands a
pipeline an output-files PREFIX pointing at the asset root. The EKS CONSTRUCT_PIPELINE operation
builds its upload destination from that prefix plus ``relative_subdir_from_asset_id``, so the
subdirectory survives only while the ``assetId`` reaches the operation.

**The event this operation receives is not the Step Functions input.** Unlike its rapidPipeline peer,
whose ``ConstructPipelineTask`` forwards the whole state input, the EKS ``ConstructPipeline`` task
declares an EXPLICIT ``payload`` object in
``infra/lib/nestedStacks/pipelines/multi/rapidPipelineEKS/constructs/rapidPipelineEKS-construct.ts``
— an enumeration of fields, so a field openPipeline threads but the task omits is unreachable inside
the operation whatever the lambdas do. Hop 3 below therefore does not hand the operation the state
machine input: it PROJECTS that input through the task payload declared in the construct, so the
event under test is the one the state machine actually sends. A field dropped from the construct
fails these tests instead of passing them.

Both halves of the placement are asserted:

*   the output keeps the input's subdirectory, so two sources sharing a basename in different folders
    cannot converge on one output key; and
*   a conversion that does NOT change the file extension gains ``SAME_FORMAT_OUTPUT_SUBDIR``, because
    the output otherwise keeps both the input's subdirectory and its file name — making its
    ASSET-RELATIVE path equal the input's. The staged key differing is not enough: the workflow's
    process-output step STRIPS the staging prefix and resolves what remains against the output
    asset's own location, so an equal asset-relative path lands a new S3 version of the operator's
    source object. The write-back resolution itself is asserted here, through the backend's own
    ``apply_output_path_extension``.

FIX-051 covers all three conversion pipelines (rapidPipeline, rapidPipelineEKS, modelOps), which must
agree with the proven reference in ``conversion/3dBasic/lambdaContainer/lambda.py``.

Destinations are compared after resolving ``aws s3 cp`` semantics: a destination ending in ``/`` takes
the source basename, anything else is the object key verbatim. Both of the handler's branches are
covered — the single-file ``-e <file>`` copy and the ``.all`` glob loop that uploads to a prefix.
"""

import os
import re
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
# The construct that DEFINES the state machine, and therefore the CONSTRUCT_PIPELINE task payload.
_CONSTRUCT_TS = os.path.join(
    _REPO_ROOT, "infra", "lib", "nestedStacks", "pipelines", "multi", "rapidPipelineEKS",
    "constructs", "rapidPipelineEKS-construct.ts")

# Stub customLogging so the lambdas import without aws_lambda_powertools.
if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for k, v in {
    "OPEN_PIPELINE_FUNCTION_NAME_EKS": "test-open-pipeline-eks",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:RapidPipelineEKS",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.gltf,.fbx,.obj,.stl,.ply,.usd,.usdz,.dae,.abc",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/RapidPipelineEKS",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/RapidPipelineEKS:*",
    "CONTAINER_IMAGE_URI": "123456789012.dkr.ecr.us-east-1.amazonaws.com/rapid-pipeline:latest",
    "EKS_CLUSTER_NAME": "test-cluster",
    "KUBERNETES_NAMESPACE": "default",
}.items():
    os.environ.setdefault(k, v)

import vamsExecuteRapidPipelineEKS  # noqa: E402,F401
import openPipeline  # noqa: E402,F401
import consolidated_handler  # noqa: E402,F401

FILES_PREFIX = "pipelines/p1/MJOB/output/E1/files/"
BUCKET = "abkt"
ASSET_ID = "xidM"
# A multi-segment asset root (an external bucket's baseAssetsPrefix plus the asset id), so a
# derivation that merely drops the first key segment yields "area/xidM/..." and fails here.
ASSET_ROOT = f"org/area/{ASSET_ID}/"


def _ctx():
    ctx = MagicMock()
    ctx.aws_request_id = "req-test"
    ctx.function_name = "test-fn"
    ctx.function_version = "1"
    ctx.get_remaining_time_in_millis.return_value = 300000
    return ctx


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
    spec = importlib.util.spec_from_file_location("_ope_for_eks_placement_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.apply_output_path_extension


def construct_same_format_subdir():
    """The folder the operation adds for a conversion that does not change the file extension, read
    off the module under test so a rename of it does not silently stop being asserted. An empty value
    is rejected here, since it is exactly the value that would restore the collision."""
    module = sys.modules.get("consolidated_handler") or \
        importlib.import_module("consolidated_handler")
    subdir = module.SAME_FORMAT_OUTPUT_SUBDIR.strip("/")
    assert subdir, "a same-format output needs a non-empty folder or it resolves onto its own source"
    return subdir


def _split_top_level(body):
    """Split an object-literal body on its top-level commas, ignoring commas inside nested
    brackets or string literals."""
    entries, depth, quote, current = [], 0, None, []
    for character in body:
        if quote:
            current.append(character)
            if character == quote:
                quote = None
            continue
        if character in "\"'`":
            quote = character
        elif character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == "," and depth == 0:
            entries.append("".join(current))
            current = []
            continue
        current.append(character)
    entries.append("".join(current))
    return entries


def construct_pipeline_task_payload_spec():
    """The CONSTRUCT_PIPELINE task payload DECLARED IN THE CDK CONSTRUCT, as
    ``{payloadKey: ("ref", statePath) | ("literal", value)}``.

    This is the production event shape: ``tasks.LambdaInvoke`` with an explicit ``payload`` sends
    exactly these keys, so a field the construct omits does not exist inside the operation no matter
    what openPipeline put in the state machine input. Parsed from the construct source rather than
    restated here, so the two cannot drift.
    """
    source = open(_CONSTRUCT_TS, encoding="utf-8").read()
    task_start = source.index('new tasks.LambdaInvoke(this, "ConstructPipeline"')
    open_index = source.index("{", source.index("payload: sfn.TaskInput.fromObject(", task_start))
    depth, end_index = 0, None
    for index in range(open_index, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                end_index = index
                break
    assert end_index is not None, "could not brace-match the ConstructPipeline payload object"

    body = re.sub(r"//[^\n]*", "", source[open_index + 1:end_index])
    spec = {}
    for entry in _split_top_level(body):
        entry = " ".join(entry.split())
        if not entry:
            continue
        key, separator, value = entry.partition(":")
        assert separator, entry
        key = key.strip().strip('"')
        reference = re.fullmatch(r'sfn\.JsonPath\.stringAt\(\s*"\$\.([A-Za-z0-9_]+)"\s*\)',
                                 value.strip())
        spec[key] = ("ref", reference.group(1)) if reference \
            else ("literal", json.loads(value.strip()))

    # Positive control for the parser: a parse that silently matched nothing (or the wrong task)
    # would make every test below pass against an empty event, so the shape is asserted here.
    assert spec.get("operation") == ("literal", "CONSTRUCT_PIPELINE"), spec
    assert spec.get("inputS3AssetFilePath") == ("ref", "inputS3AssetFilePath"), spec
    assert spec.get("outputS3AssetFilesPath") == ("ref", "outputS3AssetFilesPath"), spec
    assert len(spec) >= 8, spec
    return spec


def state_machine_construct_pipeline_event(sfn_input):
    """The event the STATE MACHINE sends to CONSTRUCT_PIPELINE for a given Step Functions input:
    the declared payload, resolved against that input.

    A declared path missing from the input is an error rather than an omission — Step Functions fails
    the task with States.Runtime when a Parameters path does not resolve — so it is asserted rather
    than defaulted.
    """
    event = {}
    for key, (kind, value) in construct_pipeline_task_payload_spec().items():
        if kind == "literal":
            event[key] = value
            continue
        assert value in sfn_input, \
            f"the task payload reads $.{value}, which openPipeline did not put in the SFN input"
        event[key] = sfn_input[value]
    return event


def _copies(command):
    """Every ``aws s3 cp <src> <dst>`` in the emitted command, shell-tokenized.

    shlex tokenization is what makes a missing shlex.quote() visible: an unquoted subdirectory with a
    space splits into extra tokens instead of being silently accepted.
    """
    tokens = shlex.split(command)
    out = []
    for i, token in enumerate(tokens):
        if token == "cp" and i + 2 < len(tokens):
            out.append((tokens[i + 1], tokens[i + 2]))
    return out


def _uploaded_key(command):
    """The single S3 key written by the emitted command, applying ``aws s3 cp`` semantics."""
    uploads = [(source, destination) for source, destination in _copies(command)
               if destination.startswith("s3://")]
    assert len(uploads) == 1, uploads
    source, destination = uploads[0]
    assert destination.startswith(f"s3://{BUCKET}/"), destination
    key = destination[len(f"s3://{BUCKET}/"):]
    if key.endswith("/"):
        key += os.path.basename(source)
    return key


def _output_relative_path(command):
    """The uploaded key's path relative to the files prefix — the value the workflow's write-back
    keys on after stripping that prefix."""
    key = _uploaded_key(command)
    assert key.startswith(FILES_PREFIX), key
    return key[len(FILES_PREFIX):]


def _write_back_key(command, extension="/"):
    """The asset-bucket key the workflow's write-back resolves this output to.

    Mirrors production: ``processWorkflowExecutionOutput.process_external_upload`` strips the staging
    files prefix, applies the execution's output path extension, and passes the result as
    ``relativeKey``; ``uploadFile.complete_external_upload`` resolves that against the asset's own
    ``assetLocation.Key``. ``"/"`` is the default extension and the one this pipeline's built-in
    workflow runs under, since its ``vamsSchema/workflow.json`` declares no
    ``defaultOutputFileBaseExecutionPathExtension``.
    """
    relative_key = _apply_output_path_extension()(_output_relative_path(command), extension)
    return f"{ASSET_ROOT}{relative_key}"


class _Chain:
    """vamsExecute -> openPipeline -> CONSTRUCT_PIPELINE, driven by one workflow manifest.

    No hop's event is hand-built: hops 1 and 2 are read back off the AWS client the previous hop
    called, and hop 3 is the state machine's declared task payload resolved against hop 2's input.
    """

    def _body(self, output_type):
        body = {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            # The legacy path fields are still required parameters on this handler; the manifest
            # resolved above overrides each of them.
            "inputS3AssetFilePath": "s3://abkt/legacy/pump.glb",
            "outputS3AssetFilesPath": "s3://abkt/legacy/files/",
            "outputS3AssetPreviewPath": "s3://abkt/legacy/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/legacy/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/eks/p1/",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
        }
        # A run that names no output type at all: the output extension then falls back to the input
        # file's own, which is the default same-extension (optimize-in-place) case.
        if output_type is not None:
            body["outputType"] = output_type
        return body

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
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/rapidPipelineEKS/E1/",
            "auxPreviewPipelineSuffix": "",
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

    def run(self, relative_path, output_type=".gltf", config=None):
        """Emitted container command for an input file at ``relative_path`` within the asset."""
        manifest = self._manifest(relative_path)
        config = {} if config is None else config

        # Hop 1: vamsExecute resolves the manifest and invokes openPipeline.
        execute_mod = _load("vamsExecuteRapidPipelineEKS")
        invoke = MagicMock(return_value={"StatusCode": 200, "Payload": MagicMock(read=lambda: b"")})
        with patch.object(execute_mod, "s3_client", self._s3(manifest, config)), \
                patch.object(execute_mod.lambda_client, "invoke", invoke):
            response = execute_mod.lambda_handler(
                {"body": json.dumps(self._body(output_type))}, _ctx())
        assert response["statusCode"] == 200, response
        open_event = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

        # Hop 2: openPipeline builds the Step Functions input.
        open_mod = _load("openPipeline")
        start = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:RapidPipelineEKS:PipelineJobEKS_x",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
        })
        with patch.object(open_mod.sfn, "start_execution", start), \
                patch.object(open_mod.events_client, "put_events", MagicMock()):
            response = open_mod.lambda_handler(open_event, _ctx())
        assert response["statusCode"] == 200, response
        sfn_input = json.loads(start.call_args.kwargs["input"])

        # Hop 3: the CONSTRUCT_PIPELINE operation emits the container command — from the event the
        # STATE MACHINE sends, which is its declared task payload resolved against that input, not
        # the input itself.
        construct_mod = _load("consolidated_handler")
        with patch.object(construct_mod, "s3", self._s3(manifest, config)):
            out = construct_mod.lambda_handler(
                state_machine_construct_pipeline_event(sfn_input), _ctx())
        assert "error" not in out, out
        container = out["jobManifest"]["spec"]["template"]["spec"]["containers"][0]
        return container["args"][0]


@pytest.mark.unit
class TestTheStateMachineTaskPayloadCarriesWhatPlacesTheOutput(_Chain):
    """The hop that shipped inert. The operation's placement logic reads ``assetId``, and the task
    payload is an explicit enumeration — so the payload declared in the construct is what decides
    whether the fix runs in production at all."""

    def test_the_declared_task_payload_reads_the_asset_id(self):
        spec = construct_pipeline_task_payload_spec()
        assert spec.get("assetId") == ("ref", "assetId"), spec

    def test_every_field_the_operation_places_output_from_is_declared(self):
        """Set containment, not an exact payload: the task may carry MORE than these (it already
        carries preview/metadata paths and a token), but every field the placement reads has to be
        there."""
        declared = set(construct_pipeline_task_payload_spec())
        assert {"inputS3AssetFilePath", "outputS3AssetFilesPath", "assetId",
                "inputConfigurationS3Location", "outputFileType"} <= declared, declared

    def test_open_pipeline_supplies_every_state_path_the_payload_reads(self):
        """The other side of the same hop: a declared path that openPipeline never puts in the state
        machine input fails the task at runtime with States.Runtime, so the two enumerations are
        compared against each other rather than against a restated list."""
        manifest = self._manifest("/parts/housing/pump.glb")
        open_mod = _load("openPipeline")
        start = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:RapidPipelineEKS:PipelineJobEKS_x",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
        })
        execute_mod = _load("vamsExecuteRapidPipelineEKS")
        invoke = MagicMock(return_value={"StatusCode": 200, "Payload": MagicMock(read=lambda: b"")})
        with patch.object(execute_mod, "s3_client", self._s3(manifest, {})), \
                patch.object(execute_mod.lambda_client, "invoke", invoke):
            execute_mod.lambda_handler({"body": json.dumps(self._body(".gltf"))}, _ctx())
        with patch.object(open_mod.sfn, "start_execution", start), \
                patch.object(open_mod.events_client, "put_events", MagicMock()):
            open_mod.lambda_handler(
                json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8")), _ctx())
        sfn_input = json.loads(start.call_args.kwargs["input"])

        referenced = {path for kind, path in construct_pipeline_task_payload_spec().values()
                      if kind == "ref"}
        assert referenced, "the payload declares no state references at all"
        assert referenced <= set(sfn_input), sorted(referenced - set(sfn_input))
        assert sfn_input["assetId"] == ASSET_ID


@pytest.mark.unit
class TestOutputPreservesRelativeSubdirectory(_Chain):

    def test_chain_is_wired_root_level_input(self):
        """Harness control for FIX-051: with the input at the asset ROOT the three hops already
        produce the correct key, so a failure in the subdirectory tests below is the defect, not a
        broken chain. Also the negative control against an unconditional f"{prefix}{subdir}/", which
        would emit a doubled or empty segment here."""
        key = _uploaded_key(self.run("/pump.glb"))
        assert key == f"{FILES_PREFIX}pump-gltf.gltf"
        assert "//" not in key

    def test_output_key_preserves_the_input_relative_subdirectory(self):
        """FIX-051: '/parts/housing/pump.glb' must convert to
        '<prefix>parts/housing/pump-gltf.gltf'."""
        assert _uploaded_key(self.run("/parts/housing/pump.glb")) == \
            f"{FILES_PREFIX}parts/housing/pump-gltf.gltf"

    def test_same_basename_in_two_subdirectories_does_not_collide(self):
        """FIX-051: the collision this finding is about — two distinct sources must produce two
        distinct output keys."""
        first = _uploaded_key(self.run("/a/pump.glb"))
        second = _uploaded_key(self.run("/b/pump.glb"))
        assert first != second
        assert first == f"{FILES_PREFIX}a/pump-gltf.gltf"
        assert second == f"{FILES_PREFIX}b/pump-gltf.gltf"

    def test_the_write_back_places_the_converted_file_beside_its_source(self):
        """The end of the chain the staged key never reaches: after process-output strips the staging
        prefix, the converted file resolves next to the source it was made from."""
        command = self.run("/parts/housing/pump.glb")
        assert _write_back_key(command) == f"{ASSET_ROOT}parts/housing/pump-gltf.gltf"
        # A deployment that configures an output path extension gets it immediately before the file
        # name, so the file still lands inside the source's own folder.
        assert _write_back_key(command, "/YOLO/") == \
            f"{ASSET_ROOT}parts/housing/YOLO/pump-gltf.gltf"

    def test_all_formats_glob_upload_preserves_the_subdirectory(self):
        """FIX-051: the '.all' branch uploads every produced file to a PREFIX in a shell loop, so the
        subdirectory has to be part of that prefix — and it must keep its trailing slash or every
        file in the loop overwrites one object."""
        command = self.run("/parts/housing/pump.glb", output_type=".all")
        destinations = [destination for _, destination in _copies(command)
                        if destination.startswith("s3://")]
        assert destinations, command
        # The loop destination is the prefix with the shell's "$file" appended, so the assertion is on
        # the prefix segment: it must carry the subdirectory AND its trailing slash.
        assert any(f"{FILES_PREFIX}parts/housing/" in destination
                   for destination in destinations), destinations

    def test_injected_subdirectory_is_shell_quoted(self):
        """FIX-051: every value interpolated into the /bin/sh command chain is shlex.quote'd. A folder
        name with a space and a single quote must survive tokenization intact — an unquoted
        subdirectory yields extra tokens (or an unbalanced quote) and fails here."""
        assert _uploaded_key(self.run("/pa rt's/housing/pump.glb")) == \
            f"{FILES_PREFIX}pa rt's/housing/pump-gltf.gltf"


@pytest.mark.unit
class TestSameFormatConversionDoesNotWriteItsOwnSource(_Chain):
    """rpdx optimizes as well as converts, so ``.glb -> .glb`` is reachable both by a template naming
    the input's own format and by the no-outputType fallback. In that case the output keeps the
    input's subdirectory AND its file name, so preserving the subdirectory alone would make the
    output's ASSET-RELATIVE path equal the input's — and that path, not the staged key, is what the
    write-back resolves against the output asset's location.

    One consequence is recorded here as a decision. An equal relative path is also what
    ``executionOutputs.resolve_manifest_input_files`` matches on to hand the NEXT pipeline in a
    chained workflow the converted file IN PLACE OF the original, so a same-format output no longer
    shadows its input that way — it is appended as an additional input instead. That is already what
    every format-changing conversion does, so the same-format case now behaves like the common case
    rather than being the one shape that silently rewrites the operator's source object. Shadowing
    and self-overwrite are the same condition and cannot be separated; data integrity wins.
    """

    @pytest.mark.parametrize("output_type,label", [
        (".glb", "template names the input's own format"),
        (None, "no output type at all, so the extension falls back to the input's"),
    ])
    def test_the_write_back_never_resolves_to_the_input_object(self, output_type, label):
        command = self.run("/parts/housing/pump.glb", output_type=output_type)
        input_key = self.input_key("/parts/housing/pump.glb")
        assert _uploaded_key(command) != input_key, label
        # The load-bearing one: the staged key differing is not enough, because the write-back strips
        # the staging prefix. Asserted for both routes that reach a same-format conversion, and for a
        # configured output path extension so a deployment that sets one is covered too.
        assert _write_back_key(command) != input_key, label
        assert _write_back_key(command, "/YOLO/") != input_key, label

    def test_the_asset_relative_path_differs_from_the_input_by_exactly_one_folder(self):
        """The inversion of the collision, asserted as a decomposition rather than a bare ``!=`` so it
        cannot be satisfied by dropping the subdirectory or by renaming the file — the two wrong ways
        to make the paths differ."""
        command = self.run("/parts/housing/pump.glb", output_type=".glb")
        subdir = construct_same_format_subdir()
        assert _output_relative_path(command) == f"parts/housing/{subdir}/pump.glb"
        assert _write_back_key(command) == f"{ASSET_ROOT}parts/housing/{subdir}/pump.glb"
        # Same subdirectory, same file name: the whole difference is the inserted folder.
        assert os.path.dirname(_output_relative_path(command)).startswith("parts/housing/")
        assert os.path.basename(_output_relative_path(command)) == "pump.glb"

    def test_the_command_never_copies_the_converted_file_onto_its_source(self):
        """Read off the emitted command rather than inferred: the upload destination is not the object
        the download read, so the container cannot version the operator's input."""
        command = self.run("/parts/housing/pump.glb", output_type=".glb")
        input_uri = f"s3://{BUCKET}/{self.input_key('/parts/housing/pump.glb')}"
        tokens = shlex.split(command)
        assert input_uri in tokens, tokens          # the download source, still the input
        uploads = [destination for _, destination in _copies(command)
                   if destination.startswith("s3://")]
        for destination in uploads:
            assert destination != input_uri
            assert not input_uri.startswith(destination.rstrip("/") + "/")

    def test_a_same_format_input_at_the_asset_root_also_avoids_its_source(self):
        """The asset root is the common case and has no subdirectory to hang the folder off, so it is
        the case an implementation that only qualifies a non-empty subdirectory would miss."""
        command = self.run("/pump.glb", output_type=".glb")
        subdir = construct_same_format_subdir()
        assert _output_relative_path(command) == f"{subdir}/pump.glb"
        assert _write_back_key(command) != self.input_key("/pump.glb")
        assert "//" not in _uploaded_key(command)

    def test_the_all_formats_upload_set_cannot_land_on_the_input(self):
        """``.all`` produces every supported format — one of which is the input's own — and its upload
        loop globs the working directory, which also holds the DOWNLOADED input file. So the uploaded
        set always contains an object at the input's own name and takes the folder unconditionally."""
        command = self.run("/parts/housing/pump.glb", output_type=".all")
        subdir = construct_same_format_subdir()
        destinations = [destination for _, destination in _copies(command)
                        if destination.startswith("s3://")]
        assert destinations, command
        for destination in destinations:
            assert f"{FILES_PREFIX}parts/housing/{subdir}/" in destination, destination
        # The write-back of any file the loop uploads therefore cannot be the input's own key.
        assert _apply_output_path_extension()(f"parts/housing/{subdir}/pump.glb", "/") != \
            self.input_key("/parts/housing/pump.glb")[len(ASSET_ROOT):]

    def test_a_format_changing_conversion_still_lands_directly_beside_its_source(self):
        """Control for the cases above: with the extensions differing there is no collision to avoid,
        so no folder is added. This is what makes the folder specific to the same-format case rather
        than a blanket change of where every conversion writes."""
        command = self.run("/parts/housing/pump.glb", output_type=".gltf")
        assert _output_relative_path(command) == "parts/housing/pump-gltf.gltf"
        assert construct_same_format_subdir() not in _output_relative_path(command)
        assert _write_back_key(command) == f"{ASSET_ROOT}parts/housing/pump-gltf.gltf"
