#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The input file's subdirectory survives the openPipeline -> constructPipeline hop.

``constructPipeline`` runs as the first state of the pipeline's own state machine, and its event is
``openPipeline``'s ``sfn_input`` verbatim -- ``ConstructPipelineTask`` declares no ``inputPath``, so
the state machine input is the whole contract. A value the state machine input does not carry cannot
be recovered downstream at any cost, which is why every test here drives BOTH lambdas in sequence and
hands ``constructPipeline`` the dict ``openPipeline`` actually passed to ``start_execution``.

Constructing ``constructPipeline``'s event by hand cannot catch that class of defect: such a test
passes on a payload production never sends, so the output-key composition reads as correct while the
pipeline still writes every file at the root of the output-files prefix.

The keys are asserted through ``manifestHelper.manifest_location`` -- the same function
``constructPipeline`` calls -- so a rename on either side of the hop fails here rather than silently
resolving to no subdirectory.

The hop ABOVE this one, ``vamsExecuteRapidPipeline`` -> ``openPipeline``, is covered by
``test_output_relative_subdir.py``, which drives all three lambdas in sequence. Those assertions are
the single ratchet for the whole chain, so nothing here duplicates them.
"""

import os
import sys
import json
import types
import shlex
import importlib
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

# Stub customLogging so the lambdas import without aws_lambda_powertools.
if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

# openPipeline and vamsExecuteRapidPipeline read these at import time. The values match the other
# test modules in this directory so whichever module imports first, both see the same environment.
for _k, _v in {
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:RapidPipeline",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.gltf,.fbx,.obj",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/RapidPipeline",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/RapidPipeline:*",
}.items():
    os.environ.setdefault(_k, _v)

import manifestHelper as mh  # noqa: E402

BUCKET = "abkt"
ASSET_ID = "xidM"
# A multi-segment asset root (an external bucket's baseAssetsPrefix plus the asset id), so a
# derivation that merely drops the first key segment yields "area/xidM/..." and fails here.
ASSET_ROOT = f"org/area/{ASSET_ID}/"
FILES_PREFIX = "pipelines/p1/MJOB/output/E1/files/"
EXECUTION_INPUTS = "pipelines/workflowExecutionInputs/E1/pipeline1/"
MANIFEST_LOCATION = f"s3://{BUCKET}/{EXECUTION_INPUTS}manifest.json"
CONFIG_LOCATION = f"s3://{BUCKET}/{EXECUTION_INPUTS}config.json"
JOB_STAMP = "PipelineJob_"


def _load(module_name):
    if module_name in sys.modules:
        return importlib.reload(sys.modules[module_name])
    return importlib.import_module(module_name)


def _apply_output_path_extension():
    """Load the pure backend output-path-extension helper by path (no backend package, no boto3)."""
    import importlib.util
    module_path = os.path.abspath(os.path.join(
        _LAMBDA_DIR, "..", "..", "..", "..",
        "backend", "backend", "common", "workflows", "outputPathExtension.py"))
    assert os.path.exists(module_path), module_path
    spec = importlib.util.spec_from_file_location("_ope_for_threading_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.apply_output_path_extension


def _open_pipeline_event(relative_path, files_prefix=FILES_PREFIX, **overrides):
    """The payload the vamsExecute lambda invokes openPipeline with, for an input file at
    ``relative_path`` within the asset."""
    event = {
        "inputS3AssetFilePath": f"s3://{BUCKET}/{ASSET_ROOT}{relative_path.lstrip('/')}",
        "outputS3AssetFilesPath": f"s3://{BUCKET}/{files_prefix}",
        "outputS3AssetPreviewPath": f"s3://{BUCKET}/pipelines/p1/MJOB/output/E1/previews/",
        "outputS3AssetMetadataPath": f"s3://{BUCKET}/pipelines/p1/MJOB/output/E1/metadata/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/rapidPipeline/E1/",
        "inputMetadataS3Location": f"s3://{BUCKET}/pipelines/workflowExecutionInputs/E1/metadata.json",
        "inputConfigurationS3Location": CONFIG_LOCATION,
        "inputManifestS3Location": MANIFEST_LOCATION,
        "assetId": ASSET_ID,
        "sfnExternalTaskToken": "tok-123",
        "outputFileType": ".glb",
        "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
    }
    event.update(overrides)
    return event


def _start_execution_stub():
    import datetime
    return MagicMock(return_value={
        "executionArn": "arn:aws:states:us-east-1:1:execution:RapidPipeline:PipelineJob_x",
        "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
    })


def _state_machine_input(event):
    """The state machine input openPipeline starts the pipeline's own state machine with."""
    module = _load("openPipeline")
    start = _start_execution_stub()
    with patch.object(module.sfn, "start_execution", start), \
            patch.object(module.events_client, "put_events", MagicMock()):
        response = module.lambda_handler(event, MagicMock())
    assert response["statusCode"] == 200, response
    return json.loads(start.call_args.kwargs["input"])


def _s3_stub(relative_path, config=None, manifest_error=None):
    """An S3 stub serving the pipeline's manifest and its input configuration."""
    manifest = {
        "inputFiles": [{
            "bucket": BUCKET,
            "key": f"{ASSET_ROOT}{relative_path.lstrip('/')}",
            "relativePath": relative_path,
            "assetId": ASSET_ID,
            "databaseId": "dbM",
            "assetRootS3Key": ASSET_ROOT,
        }],
        "outputs": {"bucket": BUCKET, "files": FILES_PREFIX},
        "auxBucket": "aux",
        "auxTempPrefix": "pipelines/rapidPipeline/E1/",
    }

    def get_object(Bucket, Key):  # noqa: N803 - boto3 kwarg names
        if Key.endswith("manifest.json"):
            if manifest_error:
                raise Exception(manifest_error)
            body = json.dumps(manifest).encode("utf-8")
        elif Key.endswith("config.json"):
            body = json.dumps(config or {}).encode("utf-8")
        else:
            raise Exception(f"unexpected key {Key}")
        return {"Body": MagicMock(read=lambda b=body: b)}

    client = MagicMock()
    client.get_object.side_effect = get_object
    client.put_object = MagicMock()
    return client


def _container_command(state_machine_input, relative_path, config=None, manifest_error=None):
    """The container command constructPipeline emits for the given state machine input."""
    module = _load("constructPipeline")
    s3 = _s3_stub(relative_path, config, manifest_error)
    with patch.object(module, "s3", s3):
        out = module.lambda_handler(state_machine_input, MagicMock())
    return out["commands"][2], s3


def _through_both_hops(relative_path, config=None, manifest_error=None, **event_overrides):
    """(state machine input, container command, s3 stub) for an input at ``relative_path``, driven
    through openPipeline and then constructPipeline with no hand-built event in between."""
    event = _open_pipeline_event(relative_path, **event_overrides)
    state_machine_input = _state_machine_input(event)
    command, s3 = _container_command(state_machine_input, relative_path, config, manifest_error)
    return state_machine_input, command, s3


def _final_copy(command):
    """(source, destination) of the LAST ``aws s3 cp`` in the emitted /bin/sh command.

    Tokenized with shlex so values read exactly as the shell would pass them: a value that was not
    shell-quoted splits into extra tokens here rather than being silently accepted.
    """
    tokens = shlex.split(command)
    last_cp = max(i for i, token in enumerate(tokens) if token == "cp")
    return tokens[last_cp + 1], tokens[last_cp + 2]


def _uploaded_key(command):
    """The single S3 key the emitted command writes, applying ``aws s3 cp`` destination semantics:
    a destination ending in ``/`` takes the source file's own name, anything else is the key."""
    source, destination = _final_copy(command)
    assert destination.startswith(f"s3://{BUCKET}/"), destination
    key = destination[len(f"s3://{BUCKET}/"):]
    if key.endswith("/") or key == "":
        key += os.path.basename(source)
    return key


@pytest.mark.unit
class TestStateMachineInputCarriesTheIdentifier:
    """The hop itself: the value openPipeline puts in the state machine input is the value
    constructPipeline reads, asserted through the reader's own accessor."""

    def test_manifest_location_read_back_by_the_construct_pipeline_accessor(self):
        state_machine_input = _state_machine_input(_open_pipeline_event("/sub/dir/model.obj"))
        assert mh.manifest_location(state_machine_input) == MANIFEST_LOCATION

    def test_asset_id_is_carried_under_the_key_construct_pipeline_reads(self):
        state_machine_input = _state_machine_input(_open_pipeline_event("/sub/dir/model.obj"))
        assert state_machine_input.get("assetId") == ASSET_ID

    def test_absent_identifier_is_carried_as_empty_rather_than_omitted(self):
        """openPipeline forwards the keys unconditionally, so a payload without them yields the
        documented empty value instead of a KeyError in the state machine input."""
        event = _open_pipeline_event("/sub/dir/model.obj")
        del event["inputManifestS3Location"]
        del event["assetId"]
        state_machine_input = _state_machine_input(event)
        assert state_machine_input["inputManifestS3Location"] == ""
        assert state_machine_input["assetId"] == ""

    def test_construct_pipeline_actually_reads_the_manifest_it_was_handed(self):
        """The manifest object named in the state machine input is the one fetched, so the
        subdirectory below comes from the threaded pointer rather than from a coincidence."""
        _, _, s3 = _through_both_hops("/sub/dir/model.obj")
        fetched = [call.kwargs for call in s3.get_object.call_args_list]
        assert {"Bucket": BUCKET, "Key": f"{EXECUTION_INPUTS}manifest.json"} in fetched


@pytest.mark.unit
class TestSubdirectorySurvivesBothHops:

    def test_output_lands_in_the_input_file_own_subdirectory(self):
        _, command, _ = _through_both_hops("/sub/dir/model.obj")
        key = _uploaded_key(command)
        assert key == f"{FILES_PREFIX}sub/dir/model.glb"
        assert "//" not in key

    def test_deep_subdirectory_is_preserved_whole(self):
        _, command, _ = _through_both_hops("/parts/housing/inner/model.obj")
        assert _uploaded_key(command) == f"{FILES_PREFIX}parts/housing/inner/model.glb"

    def test_input_at_the_asset_root_still_writes_at_the_output_root(self):
        """Control: a root-level input must NOT gain a subdirectory. A fix that always inserts one
        emits a doubled or empty segment here, which is the common case for most assets."""
        _, command, _ = _through_both_hops("/model.obj")
        key = _uploaded_key(command)
        assert key == f"{FILES_PREFIX}model.glb"
        assert "//" not in key
        assert not key.endswith("/")

    def test_same_basename_in_two_subdirectories_produces_distinct_keys(self):
        _, first_command, _ = _through_both_hops("/a/model.obj")
        _, second_command, _ = _through_both_hops("/b/model.obj")
        first, second = _uploaded_key(first_command), _uploaded_key(second_command)
        assert first == f"{FILES_PREFIX}a/model.glb"
        assert second == f"{FILES_PREFIX}b/model.glb"
        assert first != second

    def test_threaded_asset_id_alone_survives_both_hops(self):
        """With no manifest pointer the assetId carried through the same hop locates the
        subdirectory, and locates the asset root by NAME so a multi-segment base prefix is not
        mistaken for it."""
        _, command, _ = _through_both_hops("/sub/dir/model.obj", inputManifestS3Location="")
        assert _uploaded_key(command) == f"{FILES_PREFIX}sub/dir/model.glb"

    def test_files_prefix_without_a_trailing_slash_gains_exactly_one(self):
        _, command, _ = _through_both_hops(
            "/sub/dir/model.obj", files_prefix=FILES_PREFIX.rstrip("/"))
        key = _uploaded_key(command)
        assert key == f"{FILES_PREFIX}sub/dir/model.glb"
        assert "//" not in key

    def test_destination_never_ends_in_a_bare_directory_name(self):
        """`aws s3 cp file s3://b/pre/sub/dir` (no trailing slash) writes ONE object named 'dir' and
        every later file in the run overwrites it."""
        _, command, _ = _through_both_hops("/sub/dir/model.obj")
        source, destination = _final_copy(command)
        assert destination.endswith("/") or destination.endswith(f"/{os.path.basename(source)}"), \
            destination
        assert f"{FILES_PREFIX}sub/dir" in destination, destination

    def test_subdirectory_stays_one_shell_quoted_token(self):
        """The emitted command runs under /bin/sh, so a subdirectory carrying a space, a quote or
        shell metacharacters must remain a single inert literal."""
        _, command, _ = _through_both_hops("/pa rt's/$(whoami)/model.obj")
        assert _uploaded_key(command) == f"{FILES_PREFIX}pa rt's/$(whoami)/model.glb"
        assert "whoami" not in shlex.split(command)


@pytest.mark.unit
class TestComposesWithTheConfiguredOutputPrefixFolder:
    """The owner's "must work with the output prefix folder too" requirement, proved offline.

    The pipeline writes ``filesPrefix + subdir + filename``; the execution's output base-path
    extension is inserted immediately before the filename at write-back
    (``common/workflows/outputPathExtension``), applied to the produced key with the files prefix
    stripped (``executionOutputs._output_file_entry``). Both halves are composed here, so the exact
    final relative path is asserted rather than assumed.
    """

    def _relative_path_of(self, command):
        key = _uploaded_key(command)
        assert key.startswith(FILES_PREFIX), key
        return key[len(FILES_PREFIX):]

    def test_no_prefix_folder_keeps_the_subdirectory_and_nothing_else(self):
        _, command, _ = _through_both_hops("/sub/dir/model.obj")
        relative = self._relative_path_of(command)
        assert relative == "sub/dir/model.glb"
        assert _apply_output_path_extension()(relative, "/") == "sub/dir/model.glb"

    def test_prefix_folder_nests_inside_the_preserved_subdirectory(self):
        _, command, _ = _through_both_hops("/sub/dir/model.obj")
        composed = _apply_output_path_extension()(self._relative_path_of(command), "/YOLO/")
        assert composed == "sub/dir/YOLO/model.glb"
        assert "//" not in composed

    def test_prefix_folder_authored_without_a_leading_slash_composes_the_same(self):
        _, command, _ = _through_both_hops("/sub/dir/model.obj")
        relative = self._relative_path_of(command)
        assert _apply_output_path_extension()(relative, "YOLO/") == "sub/dir/YOLO/model.glb"

    def test_multi_segment_prefix_folder_composes_without_a_doubled_separator(self):
        _, command, _ = _through_both_hops("/sub/dir/model.obj")
        composed = _apply_output_path_extension()(self._relative_path_of(command), "/run/2026/")
        assert composed == "sub/dir/run/2026/model.glb"
        assert "//" not in composed

    def test_prefix_folder_on_a_root_level_input_stays_at_the_root(self):
        """Control for the compositions above: a root-level source composes to 'YOLO/model.glb', so
        a failure above is the dropped subdirectory rather than the helper."""
        _, command, _ = _through_both_hops("/model.obj")
        relative = self._relative_path_of(command)
        assert _apply_output_path_extension()(relative, "/YOLO/") == "YOLO/model.glb"


@pytest.mark.unit
class TestNoRenamingOrUniquifying:
    """Subdirectories are what separate identical basenames, so nothing in a produced key may carry
    a job name, an execution id, an asset id, a timestamp or a counter."""

    @pytest.mark.parametrize("relative_path,expected_relative", [
        ("/model.obj", "model.glb"),
        ("/sub/model.obj", "sub/model.glb"),
        ("/sub/dir/model.obj", "sub/dir/model.glb"),
        ("/parts/housing/inner/model.obj", "parts/housing/inner/model.glb"),
    ])
    def test_produced_key_is_the_files_prefix_the_subdirectory_and_the_name(
            self, relative_path, expected_relative):
        state_machine_input, command, _ = _through_both_hops(relative_path)
        key = _uploaded_key(command)
        assert key == f"{FILES_PREFIX}{expected_relative}"
        relative = key[len(FILES_PREFIX):]
        assert os.path.basename(key) == "model.glb"
        assert state_machine_input["jobName"] not in key
        assert JOB_STAMP not in key
        assert "E1" not in relative
        assert ASSET_ID not in relative

    def test_no_produced_path_in_the_command_renames_the_file(self):
        """The whole emitted command, not just the destination: the local rpdx output name is the
        input stem plus the converted extension, so no hop invents a unique name."""
        _, command, _ = _through_both_hops("/sub/dir/model.obj")
        source, _ = _final_copy(command)
        assert os.path.basename(source) == "model.glb"
        assert JOB_STAMP not in shlex.split(command)[-1]


@pytest.mark.unit
class TestDegradesToTheOutputRootRatherThanGuessing:

    def test_unreadable_manifest_falls_back_to_the_threaded_asset_id(self):
        """The manifest read is best-effort: this state has no catch (``ConstructPipelineTask``
        declares none, so a raise here fails the state machine and leaves the workflow's task token
        unreported for its full 4-hour timeout), and the threaded assetId is the second source in the
        ladder. Both identifiers travel in the same state machine input, so a manifest that cannot be
        read still resolves the subdirectory rather than flattening to the output root.

        Locating the asset root by NAME is not a guess: assetId is an explicit workflow state
        variable. The root fallback below is reserved for the case where NOTHING locates the file.
        """
        _, command, _ = _through_both_hops("/sub/dir/model.obj", manifest_error="AccessDenied")
        assert _uploaded_key(command) == f"{FILES_PREFIX}sub/dir/model.glb"

    def test_unreadable_manifest_and_no_asset_id_writes_at_the_output_root(self):
        """Both sources exhausted: the manifest cannot be read AND no assetId was threaded, so there
        is nothing left to locate the file with and the output degrades to the root."""
        _, command, _ = _through_both_hops(
            "/sub/dir/model.obj", manifest_error="AccessDenied", assetId="")
        assert _uploaded_key(command) == f"{FILES_PREFIX}model.glb"

    def test_neither_identifier_writes_at_the_output_root(self):
        _, command, _ = _through_both_hops(
            "/sub/dir/model.obj", inputManifestS3Location="", assetId="")
        assert _uploaded_key(command) == f"{FILES_PREFIX}model.glb"

    def test_asset_id_absent_from_the_key_writes_at_the_output_root(self):
        _, command, _ = _through_both_hops(
            "/sub/dir/model.obj", inputManifestS3Location="", assetId="notinkey")
        assert _uploaded_key(command) == f"{FILES_PREFIX}model.glb"

    def test_a_payload_carrying_neither_identifier_writes_at_the_root(self):
        """An upstream payload that omits both keys entirely — the pre-threading invoke payload
        shape — resolves to the output root all the way down rather than raising anywhere.

        This pins the FAILURE mode the threading fixes: it is the whole point of the payload keys in
        ``vamsExecuteRapidPipeline.execute_pipeline`` that they exist, since openPipeline forwards
        only the keys it is handed. That the real handler now supplies both is asserted end to end in
        ``test_output_relative_subdir.py``; here the payload is stripped deliberately.
        """
        event = _open_pipeline_event("/sub/dir/model.obj")
        upstream_payload = {key: value for key, value in event.items()
                            if key not in ("inputManifestS3Location", "assetId")}
        state_machine_input = _state_machine_input(upstream_payload)
        command, _ = _container_command(state_machine_input, "/sub/dir/model.obj")
        assert _uploaded_key(command) == f"{FILES_PREFIX}model.glb"
