#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The identifiers that place a converted file survive EVERY hop, and the placement is safe.

Two things are asserted here that a per-hop unit test cannot reach.

**The value identity across the whole chain.** ``vamsExecuteRapidPipeline.execute_pipeline`` builds its
invoke payload from an explicit key list, ``openPipeline`` builds ``sfn_input`` from another explicit
key list, and ``ConstructPipelineTask`` declares no ``inputPath`` -- so ``constructPipeline``'s event is
``sfn_input`` verbatim. Three explicit enumerations in a row means a key missing from any one of them is
unrecoverable downstream, and the two later hops read as correct while the pipeline still flattens
every output. Every test below therefore drives all three lambdas in sequence and asserts that the
value ``execute_pipeline`` put in ``messagePayload`` is the same value ``constructPipeline`` read --
captured from the real ``lambda_client.invoke`` and ``sfn.start_execution`` calls, never hand-built.

**That a same-extension conversion does not write over its own source.** rapidPipeline OPTIMIZES as
well as converts: ``.glb`` is both an accepted input (``vamsSchema/pipeline.json`` ``inputFileFilters``)
and the target of the ``rapid-pipeline-to-glb`` template, and with no ``outputType`` at all the output
extension falls back to the input's own. So a run that preserved the input's subdirectory AND kept its
filename unqualified would produce an output whose ASSET-RELATIVE path equals the input's.

**The staging prefix does not make that safe, and reasoning from it stops one hop early.** The
workflow hands the pipeline the per-execution staging prefix ``pipelines/{pipelineName}/{jobName}/
output/{executionId}/files/`` (``executionRecords.pipeline_output_prefixes``), so the STAGED key
differs from the input's. But staging is not where the output comes to rest:
``processWorkflowExecutionOutput`` lists that prefix, STRIPS it to each file's asset-relative path,
applies the execution's output path extension (``common/workflows/outputPathExtension``, default
``"/"`` -- which inserts nothing, and this pipeline's ``vamsSchema/workflow.json`` declares no
``defaultOutputFileBaseExecutionPathExtension``), and hands it to ``uploadFile.
complete_external_upload`` as ``relativeKey``. That resolves to
``normalize_s3_path(assetLocation.Key, relativeKey)`` -- so an output whose asset-relative path equals
the input's becomes a new S3 VERSION of the operator's own source file. The asset-relative path,
not the staged key, is therefore what has to differ.

What makes it differ is the pipeline's ``SAME_FORMAT_OUTPUT_SUBDIR`` folder, added only when the
output extension equals the input's. Both the subdirectory and the file name are still preserved, so
the output is a sibling of its source rather than a rename of it. The tests below assert the
write-back resolution itself, not just the staged copy.
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

# Stub customLogging so the lambdas import without aws_lambda_powertools.
if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

# The three lambdas read these at import time. Values match the other modules in this directory so
# whichever imports first, all of them see the same environment.
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
# The per-execution pipeline output staging prefix the workflow ASL hands the pipeline.
FILES_PREFIX = "pipelines/p1/MJOB/output/E1/files/"
EXECUTION_INPUTS = "pipelines/workflowExecutionInputs/E1/pipeline1/"
MANIFEST_LOCATION = f"s3://{BUCKET}/{EXECUTION_INPUTS}manifest.json"
CONFIG_LOCATION = f"s3://{BUCKET}/{EXECUTION_INPUTS}config.json"


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
    spec = importlib.util.spec_from_file_location("_ope_for_full_chain_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.apply_output_path_extension


def construct_same_format_subdir():
    """The folder ``constructPipeline`` adds for a conversion that does not change the file extension,
    read off the module under test so a rename of it does not silently stop being asserted. An empty
    value is rejected here, since it is exactly the value that would restore the collision."""
    module = sys.modules.get("constructPipeline") or importlib.import_module("constructPipeline")
    subdir = module.SAME_FORMAT_OUTPUT_SUBDIR.strip("/")
    assert subdir, "a same-format output needs a non-empty folder or it resolves onto its own source"
    return subdir


class _ChainRun:
    """What each hop of one full-chain run actually passed on."""

    def __init__(self, invoke_payload, sfn_input, command, input_key, construct_s3):
        self.invoke_payload = invoke_payload      # what execute_pipeline invoked openPipeline with
        self.sfn_input = sfn_input                # what openPipeline started the state machine with
        self.command = command                    # what constructPipeline emitted for the container
        self.input_key = input_key                # the source object's bucket-relative key
        self.construct_s3 = construct_s3          # constructPipeline's S3 client stub

    def final_copy(self):
        """(source, destination) of the LAST ``aws s3 cp`` in the emitted /bin/sh command.

        Tokenized with shlex so values read exactly as the shell would pass them: a value that was
        not shell-quoted splits into extra tokens here rather than being silently accepted.
        """
        tokens = shlex.split(self.command)
        last_cp = max(i for i, token in enumerate(tokens) if token == "cp")
        return tokens[last_cp + 1], tokens[last_cp + 2]

    @property
    def uploaded_key(self):
        """The single S3 key the emitted command writes, applying ``aws s3 cp`` destination
        semantics: a destination ending in ``/`` takes the source file's own name, anything else is
        the object key verbatim."""
        source, destination = self.final_copy()
        assert destination.startswith(f"s3://{BUCKET}/"), destination
        key = destination[len(f"s3://{BUCKET}/"):]
        if key.endswith("/") or key == "":
            key += os.path.basename(source)
        return key

    @property
    def uploaded_uri(self):
        return f"s3://{BUCKET}/{self.uploaded_key}"

    @property
    def input_uri(self):
        return f"s3://{BUCKET}/{self.input_key}"

    @property
    def output_relative_path(self):
        """The output's path relative to the files prefix -- the value the backend's write-back keys
        on (``executionOutputs._output_file_entry``), and what it resolves against the asset root."""
        key = self.uploaded_key
        assert key.startswith(FILES_PREFIX), key
        return key[len(FILES_PREFIX):]

    def write_back_key(self, extension="/"):
        """The asset-bucket key the workflow's write-back resolves this output to.

        This is the hop a trace that stops at the staged ``aws s3 cp`` never reaches, and it is where
        a collision actually lands. Mirrors production:
        ``processWorkflowExecutionOutput.process_external_upload`` strips the staging files prefix,
        applies the execution's output path extension, and passes the result as ``relativeKey``;
        ``uploadFile.complete_external_upload`` then resolves that against the asset's own
        ``assetLocation.Key`` with ``normalize_s3_path``. ``"/"`` is the default extension and the one
        this pipeline's built-in workflow runs under, since it declares no
        ``defaultOutputFileBaseExecutionPathExtension``.
        """
        relative_key = _apply_output_path_extension()(self.output_relative_path, extension)
        return f"{ASSET_ROOT}{relative_key}"

    @property
    def fetched_keys(self):
        return [call.kwargs.get("Key") for call in self.construct_s3.get_object.call_args_list]


class _Chain:
    """vamsExecute -> openPipeline -> constructPipeline, driven by one workflow manifest.

    No hop's event is hand-built: each is the object the previous hop actually passed to its AWS
    client, read back off the mock.
    """

    def _body(self, output_type=".glb"):
        body = {
            "TaskToken": "tok-123",
            "inputManifestS3Location": MANIFEST_LOCATION,
            "inputConfigurationS3Location": CONFIG_LOCATION,
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/rapidPipeline",
        }
        # A run that names no output type at all: the output extension then falls back to the input
        # file's own, which is the default same-extension (optimize-in-place) case.
        if output_type is not None:
            body["outputType"] = output_type
        return body

    def _manifest(self, relative_path):
        return {
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
            "inputMetadataS3Location": f"s3://{BUCKET}/pipelines/workflowExecutionInputs/E1/metadata.json",
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

        client = MagicMock()
        client.get_object.side_effect = get_object
        client.put_object = MagicMock()
        return client

    def run(self, relative_path, output_type=".glb", config=None):
        manifest = self._manifest(relative_path)
        config = {} if config is None else config

        # Hop 1: vamsExecute resolves the manifest and invokes openPipeline.
        execute_mod = _load("vamsExecuteRapidPipeline")
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(execute_mod, "s3_client", self._s3(manifest, config)), \
                patch.object(execute_mod.lambda_client, "invoke", invoke):
            response = execute_mod.lambda_handler(
                {"body": json.dumps(self._body(output_type))}, MagicMock())
        assert response["statusCode"] == 200, response
        invoke_payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

        # Hop 2: openPipeline builds the state machine input.
        open_mod = _load("openPipeline")
        start = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:RapidPipeline:PipelineJob_x",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
        })
        with patch.object(open_mod.sfn, "start_execution", start), \
                patch.object(open_mod.events_client, "put_events", MagicMock()):
            response = open_mod.lambda_handler(invoke_payload, MagicMock())
        assert response["statusCode"] == 200, response
        sfn_input = json.loads(start.call_args.kwargs["input"])

        # Hop 3: constructPipeline emits the container command from that state machine input.
        construct_mod = _load("constructPipeline")
        construct_s3 = self._s3(manifest, config)
        with patch.object(construct_mod, "s3", construct_s3):
            out = construct_mod.lambda_handler(sfn_input, MagicMock())

        return _ChainRun(invoke_payload, sfn_input, out["commands"][2],
                         f"{ASSET_ROOT}{relative_path.lstrip('/')}", construct_s3)


@pytest.mark.unit
class TestTheIdentifiersSurviveEveryHop(_Chain):
    """The value ``execute_pipeline`` puts in ``messagePayload`` is the value ``constructPipeline``
    reads -- asserted hop by hop on the same run, so no single hop can satisfy it alone."""

    def test_execute_pipeline_payload_carries_both_identifiers(self):
        """Hop 1. This is the hop that shipped inert: the invoke payload is an explicit key list, and
        for three waves it named neither identifier, so nothing downstream could forward one."""
        run = self.run("/parts/housing/model.obj")
        assert run.invoke_payload["inputManifestS3Location"] == MANIFEST_LOCATION
        assert run.invoke_payload["assetId"] == ASSET_ID

    def test_open_pipeline_forwards_the_same_values_it_was_handed(self):
        """Hop 2. Compared against hop 1's payload rather than the constant, so a rename on either
        side of the hop fails here instead of silently resolving to no subdirectory."""
        run = self.run("/parts/housing/model.obj")
        assert run.sfn_input["inputManifestS3Location"] == \
            run.invoke_payload["inputManifestS3Location"]
        assert run.sfn_input["assetId"] == run.invoke_payload["assetId"]

    def test_construct_pipeline_reads_them_through_its_own_accessor(self):
        """Hop 3. Read back through ``manifestHelper.manifest_location`` -- the function
        constructPipeline itself calls -- so the assertion tracks the reader, not the key name."""
        run = self.run("/parts/housing/model.obj")
        assert mh.manifest_location(run.sfn_input) == run.invoke_payload["inputManifestS3Location"]
        assert run.sfn_input.get("assetId") == run.invoke_payload["assetId"]

    def test_construct_pipeline_actually_fetched_the_manifest_it_was_handed(self):
        """The pointer was not merely present: the object it names is the one read, so the
        subdirectory below comes from the threaded value rather than from a coincidence."""
        run = self.run("/parts/housing/model.obj")
        assert f"{EXECUTION_INPUTS}manifest.json" in run.fetched_keys, run.fetched_keys

    def test_the_threaded_values_place_the_output(self):
        """The payoff: the end of the chain is the composed key, asserted exactly."""
        run = self.run("/parts/housing/model.obj")
        assert run.uploaded_key == f"{FILES_PREFIX}parts/housing/model.glb"


@pytest.mark.unit
class TestSubdirectoryPreservedWithAndWithoutAnOutputPrefix(_Chain):
    """The owner's requirement, end to end: preserved subdirectory, and correct composition with a
    configured output prefix folder. The composed key is asserted exactly in both cases."""

    def test_subdirectory_preserved_with_no_output_prefix(self):
        run = self.run("/parts/housing/model.obj")
        assert run.uploaded_key == f"{FILES_PREFIX}parts/housing/model.glb"
        assert run.output_relative_path == "parts/housing/model.glb"
        assert _apply_output_path_extension()(run.output_relative_path, "/") == \
            "parts/housing/model.glb"
        assert "//" not in run.uploaded_key

    def test_output_prefix_nests_inside_the_preserved_subdirectory(self):
        """The extension goes immediately before the filename, so it lands BESIDE the source file
        rather than at the asset root."""
        run = self.run("/parts/housing/model.obj")
        composed = _apply_output_path_extension()(run.output_relative_path, "/YOLO/")
        assert composed == "parts/housing/YOLO/model.glb"
        assert "//" not in composed

    def test_input_at_the_asset_root_stays_at_the_output_root(self):
        """Control: a root-level input must NOT gain a subdirectory. An unconditionally inserted
        segment emits a doubled or empty one here, and the asset root is the common case."""
        run = self.run("/model.obj")
        assert run.uploaded_key == f"{FILES_PREFIX}model.glb"
        assert "//" not in run.uploaded_key
        assert not run.uploaded_key.endswith("/")

    def test_input_at_the_asset_root_composes_with_an_output_prefix(self):
        """Control for the composition above: a root-level source composes to 'YOLO/model.glb', so a
        failure there is the dropped subdirectory rather than the helper."""
        run = self.run("/model.obj")
        assert _apply_output_path_extension()(run.output_relative_path, "/YOLO/") == "YOLO/model.glb"


@pytest.mark.unit
class TestSameExtensionConversionDoesNotWriteItsOwnSource(_Chain):
    """rapidPipeline optimizes as well as converts, so ``.glb -> .glb`` is reachable both by template
    and by the no-outputType fallback. Preserving the subdirectory and keeping the filename would make
    the output's ASSET-RELATIVE path equal the input's, and that -- not the staged key -- is what the
    write-back resolves against the asset root. So what must be proved is that the RESOLVED write-back
    key differs from the input's key, under the default output path extension the built-in workflow
    actually runs with.

    One consequence is recorded here as a decision. An equal relative path is also what
    ``executionOutputs.resolve_manifest_input_files`` matches on to hand the NEXT pipeline in a chained
    workflow the converted file IN PLACE OF the original, so a same-extension output no longer shadows
    its input that way -- it is appended as an additional input instead. That is already what every
    format-changing conversion does (``model.obj`` -> ``model.glb`` never matched the input's relative
    path either), so the same-extension case now behaves like the common case rather than being the one
    shape that silently rewrites the operator's source object. Shadowing and self-overwrite are the
    same condition and cannot be separated; data integrity wins.
    """

    @pytest.mark.parametrize("output_type,label", [
        (".glb", "template names the input's own format"),
        (None, "no output type at all, so the extension falls back to the input's"),
    ])
    def test_output_key_is_never_the_input_key(self, output_type, label):
        run = self.run("/parts/housing/model.glb", output_type=output_type)
        assert run.uploaded_key != run.input_key, label
        assert run.uploaded_uri != run.input_uri, label
        # The load-bearing one: the staged key differing is not enough, because the write-back strips
        # the staging prefix. Asserted for both routes that reach a same-extension conversion.
        assert run.write_back_key() != run.input_key, label

    def test_the_command_never_copies_the_converted_file_onto_its_source(self):
        """Read off the emitted command rather than inferred: the destination of the upload is not the
        object the download read, so the container cannot version the user's input."""
        run = self.run("/parts/housing/model.glb")
        tokens = shlex.split(run.command)
        assert run.input_uri in tokens, tokens          # the download source, still the input
        _, destination = run.final_copy()
        assert destination != run.input_uri
        assert not run.input_uri.startswith(destination.rstrip("/") + "/")

    def test_output_lands_under_the_reserved_pipeline_staging_prefix(self):
        """Necessary but NOT sufficient -- the write-back strips this prefix, so it is not what keeps
        the keys apart. Kept as its own assertion so a future change of the STAGING destination to the
        asset root fails here. ``pipelines/`` is a reserved asset-bucket prefix; no asset's own files
        live under it."""
        run = self.run("/parts/housing/model.glb")
        assert run.uploaded_key.startswith("pipelines/")
        assert run.uploaded_key.startswith(FILES_PREFIX)
        assert not run.input_key.startswith("pipelines/")

    def test_the_output_filename_is_not_renamed_or_uniquified(self):
        """The owner forbids solving the collision by renaming: the distinct LOCATION is what makes
        the output a sibling rather than a new version, so the name must be untouched -- at the staged
        key and at the resolved write-back key alike."""
        run = self.run("/parts/housing/model.glb")
        assert os.path.basename(run.uploaded_key) == "model.glb"
        assert os.path.basename(run.uploaded_key) == os.path.basename(run.input_key)
        assert os.path.basename(run.write_back_key()) == os.path.basename(run.input_key)
        assert "PipelineJob_" not in run.uploaded_key
        assert "E1" not in run.output_relative_path

    def test_the_asset_relative_path_differs_from_the_input_by_exactly_one_folder(self):
        """The inversion of the collision. The asset-relative path is what the write-back resolves
        against the asset root, so it must NOT equal the input's -- and the whole difference must be
        the added folder, proving the subdirectory and the file name both survived.

        Asserted as a decomposition rather than a bare ``!=`` so it cannot be satisfied by dropping
        the subdirectory or by renaming the file, which are the two wrong ways to make the paths
        differ.
        """
        run = self.run("/parts/housing/model.glb")
        input_relative = run.input_key[len(ASSET_ROOT):]
        assert input_relative == "parts/housing/model.glb"
        assert run.output_relative_path != input_relative
        assert run.output_relative_path == \
            f"parts/housing/{construct_same_format_subdir()}/model.glb"
        # The difference is ONLY the inserted folder: same subdirectory, same file name.
        assert os.path.dirname(run.output_relative_path).startswith("parts/housing/")
        assert os.path.basename(run.output_relative_path) == os.path.basename(input_relative)

    def test_the_write_back_does_not_resolve_to_the_input_object(self):
        """The end of the real chain, and the assertion the staging-prefix argument could not make:
        the key the write-back resolves this output to is not the operator's source object.

        Also asserted for a configured output path extension, so a deployment that sets
        ``defaultOutputFileBaseExecutionPathExtension`` is covered rather than being the only thing
        standing between an optimize run and a mutated input.
        """
        run = self.run("/parts/housing/model.glb")
        assert run.input_key == f"{ASSET_ROOT}parts/housing/model.glb"
        assert run.write_back_key() != run.input_key
        assert run.write_back_key() == \
            f"{ASSET_ROOT}parts/housing/{construct_same_format_subdir()}/model.glb"
        assert run.write_back_key("/YOLO/") != run.input_key
        assert run.write_back_key("/YOLO/") == \
            f"{ASSET_ROOT}parts/housing/{construct_same_format_subdir()}/YOLO/model.glb"

    def test_a_same_extension_input_at_the_asset_root_also_avoids_its_source(self):
        """The asset root is the common case and has no subdirectory to hang the folder off, so it is
        the case an implementation that only qualifies non-empty subdirectories would miss."""
        run = self.run("/model.glb")
        assert run.input_key == f"{ASSET_ROOT}model.glb"
        assert run.output_relative_path == f"{construct_same_format_subdir()}/model.glb"
        assert run.write_back_key() != run.input_key
        assert "//" not in run.uploaded_key

    def test_a_different_extension_still_lands_beside_it(self):
        """Control for the cases above: with the extensions differing there is no collision to avoid,
        so the output keeps landing DIRECTLY beside its source with no added folder. This is what
        makes the folder above specific to the same-extension case rather than a blanket change of
        where every conversion writes."""
        run = self.run("/parts/housing/model.obj", output_type=".glb")
        assert run.output_relative_path == "parts/housing/model.glb"
        assert construct_same_format_subdir() not in run.output_relative_path
        assert run.input_key.endswith("parts/housing/model.obj")
        assert run.write_back_key() == f"{ASSET_ROOT}parts/housing/model.glb"
        assert run.write_back_key() != run.input_key
