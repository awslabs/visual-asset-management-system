#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Converted output keeps the input file's subdirectory within the asset (FIX-051).

Asset files live at ``{assetRootS3Key}{relative_subdir}/{filename}``, and the workflow hands a
pipeline an output-files PREFIX — the per-execution pipeline staging prefix
``pipelines/{pipelineName}/{jobName}/output/{executionId}/files/``. Building the upload destination
from that prefix alone flattens every output to the root of the prefix, so two sources that share a
basename in different folders converge on one key and the second silently replaces the first.

3dBasic already does this correctly (``lambdaContainer/lambda.py`` derives
``relative_subdir_from_manifest_path`` and inserts it before the filename), so these assertions mirror
``conversion/3dBasic/lambdaContainer/tests/test_conversion_contract.py::
test_output_preserves_relative_subdirectory``.

The tests drive the FULL chain — vamsExecute -> openPipeline -> constructPipeline — because each hop
enumerates the fields it forwards, and a value one hop's payload omits cannot be recovered downstream
at any cost. Asserting only on constructPipeline lets the composition read as correct while an
upstream hop drops the identifier in the middle, which is exactly how this shipped inert: the
output-key composition and openPipeline's forwarding were both correct while
``vamsExecuteRapidPipeline.execute_pipeline``'s invoke payload named neither identifier. Nothing here
names the threaded field, so any threading shape satisfies them.

The destination is compared after resolving the ``aws s3 cp`` semantics: a destination ending in
``/`` takes the source basename, anything else is the object key verbatim. That is what pins the
trailing-slash contract — ``aws s3 cp file s3://b/a/parts/housing`` writes ONE object named
``housing``, and every later file in the run would overwrite it.
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

# vamsExecute + openPipeline read these at import time (boto3 clients + module-level env).
for k, v in {
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:RapidPipeline",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.gltf,.fbx,.obj",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/RapidPipeline",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/RapidPipeline:*",
}.items():
    os.environ.setdefault(k, v)

import vamsExecuteRapidPipeline  # noqa: E402,F401  (module-level so a reload keeps one instance)
import openPipeline  # noqa: E402,F401
import constructPipeline  # noqa: E402,F401

FILES_PREFIX = "pipelines/p1/MJOB/output/E1/files/"
BUCKET = "abkt"


def _load(name):
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


def _final_copy(command):
    """(source, destination) of the LAST ``aws s3 cp`` in the emitted /bin/sh command.

    Tokenized with shlex so the values are compared as the shell would pass them. A value that was
    not shell-quoted therefore fails here rather than being silently accepted, which is what keeps a
    subdirectory containing a space or a quote from reopening the injection surface the surrounding
    code guards with shlex.quote().
    """
    tokens = shlex.split(command)
    last_cp = max(i for i, token in enumerate(tokens) if token == "cp")
    return tokens[last_cp + 1], tokens[last_cp + 2]


def _uploaded_key(command):
    """The single S3 key the emitted command writes, applying ``aws s3 cp`` destination semantics."""
    source, destination = _final_copy(command)
    assert destination.startswith(f"s3://{BUCKET}/"), destination
    key = destination[len(f"s3://{BUCKET}/"):]
    if key.endswith("/"):
        key += os.path.basename(source)
    return key


class _Chain:
    """vamsExecute -> openPipeline -> constructPipeline, driven by one workflow manifest."""

    def _body(self):
        return {
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/legacy/rapidPipeline",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "outputType": ".glb",
        }

    def _manifest(self, relative_path):
        """A manifest whose single input file sits at ``relative_path`` within asset ``xidM``."""
        key = f"xidM/{relative_path.lstrip('/')}"
        return {
            "inputFiles": [{
                "bucket": BUCKET,
                "key": key,
                "relativePath": relative_path,
                "assetId": "xidM",
                "databaseId": "dbM",
                "assetRootS3Key": "xidM/",
            }],
            "outputs": {"bucket": BUCKET, "files": FILES_PREFIX},
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/rapidPipeline/E1/",
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

    def run(self, relative_path, config=None):
        """Emitted container command for an input file at ``relative_path`` within the asset."""
        manifest = self._manifest(relative_path)
        config = {} if config is None else config

        # Hop 1: vamsExecute resolves the manifest and invokes openPipeline.
        execute_mod = _load("vamsExecuteRapidPipeline")
        invoke = MagicMock(return_value={"StatusCode": 200})
        with patch.object(execute_mod, "s3_client", self._s3(manifest, config)), \
                patch.object(execute_mod.lambda_client, "invoke", invoke):
            response = execute_mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert response["statusCode"] == 200, response
        open_event = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))

        # Hop 2: openPipeline builds the Step Functions input.
        open_mod = _load("openPipeline")
        start = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:RapidPipeline:PipelineJob_x",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
        })
        with patch.object(open_mod.sfn, "start_execution", start), \
                patch.object(open_mod.events_client, "put_events", MagicMock()):
            response = open_mod.lambda_handler(open_event, MagicMock())
        assert response["statusCode"] == 200, response
        sfn_input = json.loads(start.call_args.kwargs["input"])

        # Hop 3: constructPipeline emits the container command.
        construct_mod = _load("constructPipeline")
        with patch.object(construct_mod, "s3", self._s3(manifest, config)):
            out = construct_mod.lambda_handler(sfn_input, MagicMock())
        return out["commands"][2]


@pytest.mark.unit
class TestOutputPreservesRelativeSubdirectory(_Chain):

    def test_chain_is_wired_root_level_input(self):
        """Harness control for FIX-051: with the input at the asset ROOT the three hops already
        produce the correct key, so a failure in the subdirectory tests below is the defect and not
        a broken chain. Also the negative control against an unconditional f"{prefix}{subdir}/",
        which would emit a doubled or empty segment here."""
        key = self._uploaded(self.run("/model.obj"))
        assert key == f"{FILES_PREFIX}model.glb"
        assert "//" not in key

    def _uploaded(self, command):
        return _uploaded_key(command)

    def test_output_key_preserves_the_input_relative_subdirectory(self):
        """'/parts/housing/model.obj' converts to '<prefix>parts/housing/model.glb'.

        Mirrors 3dBasic's test_output_preserves_relative_subdirectory so the three conversion
        pipelines and the reference implementation agree on one contract.
        """
        assert self._uploaded(self.run("/parts/housing/model.obj")) == \
            f"{FILES_PREFIX}parts/housing/model.glb"

    def test_same_basename_in_two_subdirectories_does_not_collide(self):
        """The collision this finding is about — '/a/model.obj' and '/b/model.obj' are two
        distinct files and must produce two distinct output keys."""
        first = self._uploaded(self.run("/a/model.obj"))
        second = self._uploaded(self.run("/b/model.obj"))
        assert first != second
        assert first == f"{FILES_PREFIX}a/model.glb"
        assert second == f"{FILES_PREFIX}b/model.glb"

    def test_destination_ends_in_the_filename_not_a_bare_directory(self):
        """`aws s3 cp file s3://b/a/parts/housing` (no trailing slash) writes a single
        object NAMED 'housing' and every later file in the run overwrites it, so the destination must
        either carry the trailing slash or name the file outright."""
        source, destination = _final_copy(self.run("/parts/housing/model.obj"))
        assert destination.endswith("/") or destination.endswith(f"/{os.path.basename(source)}"), \
            destination
        assert destination.rstrip("/").endswith("parts/housing") or \
            destination.endswith("parts/housing/model.glb"), destination

    def test_injected_subdirectory_is_shell_quoted(self):
        """Every value interpolated into the /bin/sh command chain is shlex.quote'd. A folder
        name with a space and a single quote must survive tokenization intact — if the injected
        subdirectory is not quoted, shlex sees extra tokens (or an unbalanced quote) and this fails."""
        key = self._uploaded(self.run("/pa rt's/housing/model.obj"))
        assert key == f"{FILES_PREFIX}pa rt's/housing/model.glb"

    def test_metacharacter_subdirectory_stays_inert(self):
        """A folder name containing shell metacharacters must remain a single inert literal
        — no extra command is introduced into the chain."""
        command = self.run("/$(whoami);rm -rf x/model.obj")
        key = self._uploaded(command)
        assert key == f"{FILES_PREFIX}$(whoami);rm -rf x/model.glb"
        # The metacharacters stayed inside ONE token, so the shell never sees a new command or a
        # stray argument. An unquoted subdirectory would split '-rf' and 'x/' into their own tokens.
        tokens = shlex.split(command)
        assert "-rf" not in tokens, tokens


@pytest.mark.unit
class TestComposesWithOutputPathExtension(_Chain):
    """The owner's 'must work with the output prefix folder' requirement, proved offline.

    ``common/workflows/outputPathExtension.apply_output_path_extension`` inserts the execution's
    output base-path extension immediately BEFORE the final filename, and the output relativePath is
    derived by stripping the files prefix (``executionOutputs._output_file_entry``). So the pipeline
    must write under ``files_prefix + subdir + filename`` and nothing else — writing the subdirectory
    ABOVE the files prefix would put the extension in the wrong place.
    """

    @staticmethod
    def _apply_output_path_extension():
        """Load the pure backend helper by path (no backend package import, no boto3)."""
        import importlib.util
        module_path = os.path.abspath(os.path.join(
            _LAMBDA_DIR, "..", "..", "..", "..",
            "backend", "backend", "common", "workflows", "outputPathExtension.py"))
        assert os.path.exists(module_path), module_path
        spec = importlib.util.spec_from_file_location("_ope_for_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.apply_output_path_extension

    def test_output_prefix_folder_nests_inside_the_preserved_subdirectory(self):
        """An execution whose output base-path extension is '/YOLO/' places the folder
        beside the source file — 'parts/housing/YOLO/model.glb', not 'YOLO/model.glb'."""
        key = _uploaded_key(self.run("/parts/housing/model.obj"))
        relative_path = key[len(FILES_PREFIX):]
        assert self._apply_output_path_extension()(relative_path, "/YOLO/") == \
            "parts/housing/YOLO/model.glb"

    def test_output_prefix_folder_still_composes_for_a_root_level_input(self):
        """Control for the composition above: a root-level source keeps composing to 'YOLO/model.glb'
        both before and after FIX-051, so the assertion above fails on the dropped subdirectory
        rather than on the helper."""
        key = _uploaded_key(self.run("/model.obj"))
        relative_path = key[len(FILES_PREFIX):]
        assert self._apply_output_path_extension()(relative_path, "/YOLO/") == "YOLO/model.glb"
