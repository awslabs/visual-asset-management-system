#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The converted output keeps the input file's subdirectory within the asset (FIX-051).

Asset files live at ``{assetRootS3Key}{relative_subdir}/{filename}`` and the workflow hands the
pipeline an output-files PREFIX. Building the upload destination from that prefix alone flattens
every output to the root of the prefix, so ``/parts/housing/model.obj`` and ``/spares/model.obj``
converge on one key and the second run silently replaces the first.

These tests drive ``constructPipeline`` at its own boundary, where the emitted ``aws s3 cp``
destination is the whole contract, and compare keys after resolving that command's semantics: a
destination ending in ``/`` takes the source file's name, anything else is the object key verbatim.
Naming the file is therefore never required to place it correctly, which is what keeps the fix free
of any renaming or uniquifying of the output filename.

The output PATH EXTENSION (the configured output prefix folder) is applied by the backend at
write-back rather than by the pipeline — ``common/workflows/outputPathExtension`` inserts it
immediately before the final filename, and the output relativePath it is applied to is the produced
key with the files prefix stripped (``executionOutputs._output_file_entry``). So the composition
tests below take the key this pipeline emits and run it through that same helper, proving the two
compose to ``{subdir}/{extension}/{filename}`` with no doubled or empty separator.
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

# Stub customLogging so the lambda imports without aws_lambda_powertools.
if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for _k, _v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"}.items():
    os.environ.setdefault(_k, _v)

BUCKET = "abkt"
FILES_PREFIX = "pipelines/p1/MJOB/output/E1/files/"
MANIFEST_LOCATION = f"s3://{BUCKET}/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json"
CONFIG_LOCATION = f"s3://{BUCKET}/pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
ASSET_ID = "xidM"
# A multi-segment asset root (an external bucket's baseAssetsPrefix plus the asset id). Every input
# key below is built from it, so a derivation that merely drops the FIRST key segment produces a
# subdirectory of "area/xidM/..." and fails these assertions.
ASSET_ROOT = f"org/area/{ASSET_ID}/"


def _load():
    if "constructPipeline" in sys.modules:
        return importlib.reload(sys.modules["constructPipeline"])
    return importlib.import_module("constructPipeline")


def _apply_output_path_extension():
    """Load the pure backend output-path-extension helper by path (no backend package, no boto3)."""
    import importlib.util
    module_path = os.path.abspath(os.path.join(
        _LAMBDA_DIR, "..", "..", "..", "..",
        "backend", "backend", "common", "workflows", "outputPathExtension.py"))
    assert os.path.exists(module_path), module_path
    spec = importlib.util.spec_from_file_location("_ope_for_subdir_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.apply_output_path_extension


def _final_copy(command):
    """(source, destination) of the LAST ``aws s3 cp`` in the emitted /bin/sh command.

    Tokenized with shlex so values are read exactly as the shell would pass them: a subdirectory
    that was not shell-quoted splits into extra tokens here rather than being silently accepted.
    """
    tokens = shlex.split(command)
    last_cp = max(i for i, token in enumerate(tokens) if token == "cp")
    return tokens[last_cp + 1], tokens[last_cp + 2]


def _uploaded_key(command):
    """The single S3 key the emitted command writes, applying ``aws s3 cp`` destination semantics."""
    source, destination = _final_copy(command)
    assert destination.startswith(f"s3://{BUCKET}/"), destination
    key = destination[len(f"s3://{BUCKET}/"):]
    if key.endswith("/") or key == "":
        key += os.path.basename(source)
    return key


def _event(relative_path, files_prefix=FILES_PREFIX, **overrides):
    """A constructPipeline event for an input file at ``relative_path`` within the asset."""
    event = {
        "jobName": "PipelineJob_20260101_000000_000_ab12cd34",
        "inputS3AssetFilePath": f"s3://{BUCKET}/{ASSET_ROOT}{relative_path.lstrip('/')}",
        "outputS3AssetFilesPath": f"s3://{BUCKET}/{files_prefix}",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/rapidPipeline/E1/",
        "inputManifestS3Location": MANIFEST_LOCATION,
        "inputConfigurationS3Location": CONFIG_LOCATION,
        "inputMetadataS3Location": f"s3://{BUCKET}/pipelines/workflowExecutionInputs/E1/metadata.json",
        "externalSfnTaskToken": "tok-123",
        "outputFileType": ".glb",
    }
    event.update(overrides)
    return event


def _s3(relative_path, config=None, manifest_error=None):
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


def _run(relative_path, config=None, manifest_error=None, **event_overrides):
    """The container command constructPipeline emits for an input file at ``relative_path``."""
    module = _load()
    with patch.object(module, "s3", _s3(relative_path, config, manifest_error)):
        out = module.lambda_handler(_event(relative_path, **event_overrides), MagicMock())
    return out["commands"][2]


@pytest.mark.unit
class TestOutputKeyPreservesSubdirectory:

    def test_output_lands_in_the_input_file_own_subdirectory(self):
        """An input at '/sub/dir/model.obj' converts to '<filesPrefix>sub/dir/model.glb' — under the
        SAME relative subdirectory, not at the root of the output-files prefix."""
        key = _uploaded_key(_run("/sub/dir/model.obj"))
        assert key == f"{FILES_PREFIX}sub/dir/model.glb"
        assert "//" not in key

    def test_deep_subdirectory_is_preserved_whole(self):
        key = _uploaded_key(_run("/parts/housing/inner/model.obj"))
        assert key == f"{FILES_PREFIX}parts/housing/inner/model.glb"

    def test_input_at_the_asset_root_still_writes_at_the_output_root(self):
        """Control: a root-level input must NOT gain a subdirectory. A fix that always inserts one
        emits a doubled or empty segment here, which is the common case for most assets."""
        key = _uploaded_key(_run("/model.obj"))
        assert key == f"{FILES_PREFIX}model.glb"
        assert "//" not in key
        assert not key.endswith("/")

    def test_same_basename_in_two_subdirectories_produces_distinct_keys(self):
        """The collision this finding is about: two distinct files sharing a basename must not
        converge on one output key."""
        first = _uploaded_key(_run("/a/model.obj"))
        second = _uploaded_key(_run("/b/model.obj"))
        assert first == f"{FILES_PREFIX}a/model.glb"
        assert second == f"{FILES_PREFIX}b/model.glb"
        assert first != second

    def test_destination_never_ends_in_a_bare_directory_name(self):
        """`aws s3 cp file s3://b/pre/sub/dir` (no trailing slash) writes ONE object named 'dir' and
        every later file in the run overwrites it, so the destination either carries the trailing
        slash or names the file outright."""
        source, destination = _final_copy(_run("/sub/dir/model.obj"))
        assert destination.endswith("/") or destination.endswith(f"/{os.path.basename(source)}"), \
            destination
        assert f"{FILES_PREFIX}sub/dir" in destination, destination


@pytest.mark.unit
class TestComposesWithTheConfiguredOutputPrefixFolder:
    """The owner's "must work with the output prefix folder too" requirement, proved offline.

    The pipeline writes ``filesPrefix + subdir + filename`` and nothing else; the execution's output
    base-path extension is inserted immediately before the filename at write-back. Writing the
    subdirectory anywhere else would put the folder at the wrong level.
    """

    def _relative_path_of(self, command):
        key = _uploaded_key(command)
        assert key.startswith(FILES_PREFIX), key
        return key[len(FILES_PREFIX):]

    def test_prefix_folder_nests_inside_the_preserved_subdirectory(self):
        """'/sub/dir/model.obj' with an output prefix folder of '/YOLO/' composes to exactly
        'sub/dir/YOLO/model.glb' — beside the source file, not at the asset root."""
        relative = self._relative_path_of(_run("/sub/dir/model.obj"))
        composed = _apply_output_path_extension()(relative, "/YOLO/")
        assert composed == "sub/dir/YOLO/model.glb"
        assert "//" not in composed

    def test_prefix_folder_authored_without_slashes_composes_the_same(self):
        """The stored extension is normalized to a single leading slash, so an extension authored as
        'YOLO/' composes identically — this is where a doubled separator would appear."""
        relative = self._relative_path_of(_run("/sub/dir/model.obj"))
        assert _apply_output_path_extension()(relative, "YOLO/") == "sub/dir/YOLO/model.glb"

    def test_multi_segment_prefix_folder_composes_without_a_doubled_separator(self):
        relative = self._relative_path_of(_run("/sub/dir/model.obj"))
        composed = _apply_output_path_extension()(relative, "/run/2026/")
        assert composed == "sub/dir/run/2026/model.glb"
        assert "//" not in composed

    def test_prefix_folder_on_a_root_level_input_stays_at_the_root(self):
        """Control for the compositions above: a root-level source composes to 'YOLO/model.glb', so
        a failure above is the dropped subdirectory rather than the helper."""
        relative = self._relative_path_of(_run("/model.obj"))
        assert _apply_output_path_extension()(relative, "/YOLO/") == "YOLO/model.glb"

    def test_no_prefix_folder_leaves_the_preserved_subdirectory_untouched(self):
        relative = self._relative_path_of(_run("/sub/dir/model.obj"))
        assert _apply_output_path_extension()(relative, "/") == "sub/dir/model.glb"


@pytest.mark.unit
class TestNoRenamingOrUniquifying:
    """The output filename is the input's own stem plus the converted extension, and nothing else.

    Separating identical basenames by subdirectory is the whole point of the fix, so nothing in the
    produced key may carry a job name, an execution id, a timestamp or a counter.
    """

    @pytest.mark.parametrize("relative_path", [
        "/model.obj",
        "/sub/model.obj",
        "/sub/dir/model.obj",
        "/parts/housing/inner/model.obj",
    ])
    def test_produced_key_basename_is_the_input_stem_plus_the_output_extension(self, relative_path):
        event = _event(relative_path)
        key = _uploaded_key(_run(relative_path))
        assert os.path.basename(key) == "model.glb"
        assert event["jobName"] not in key
        assert "E1" not in key[len(FILES_PREFIX):]
        assert ASSET_ID not in key[len(FILES_PREFIX):]

    def test_produced_key_is_the_files_prefix_the_subdirectory_and_the_name(self):
        """Stated as an exact identity so a uniquifying segment anywhere in the key fails."""
        key = _uploaded_key(_run("/sub/dir/model.obj"))
        assert key.split("/") == FILES_PREFIX.rstrip("/").split("/") + ["sub", "dir", "model.glb"]


@pytest.mark.unit
class TestSubdirectoryResolutionSources:

    def test_threaded_asset_id_locates_the_subdirectory_without_a_manifest(self):
        """A payload carrying assetId instead of a manifest pointer resolves the same subdirectory,
        and locates the asset root by NAME so a multi-segment base prefix is not mistaken for it."""
        command = _run("/sub/dir/model.obj", assetId=ASSET_ID, inputManifestS3Location="")
        assert _uploaded_key(command) == f"{FILES_PREFIX}sub/dir/model.glb"

    def test_asset_id_absent_from_the_key_writes_at_the_output_root(self):
        """An asset whose base location key does not contain its id yields no subdirectory rather
        than a guessed depth."""
        command = _run("/sub/dir/model.obj", assetId="notinkey", inputManifestS3Location="")
        assert _uploaded_key(command) == f"{FILES_PREFIX}model.glb"

    def test_payload_with_neither_source_writes_at_the_output_root(self):
        command = _run("/sub/dir/model.obj", inputManifestS3Location="")
        assert _uploaded_key(command) == f"{FILES_PREFIX}model.glb"

    def test_unreadable_manifest_degrades_to_the_output_root(self):
        """The manifest read is best-effort: this state has no catch, so raising here would fail the
        state machine and leave the workflow's task token unreported for its full timeout."""
        command = _run("/sub/dir/model.obj", manifest_error="AccessDenied")
        assert _uploaded_key(command) == f"{FILES_PREFIX}model.glb"


@pytest.mark.unit
class TestSubdirectoryStaysShellInert:
    """The emitted command runs under /bin/sh, so the injected subdirectory must be one quoted
    literal — the same guarantee the surrounding filename and key interpolations already carry."""

    def test_subdirectory_with_a_space_and_a_quote_survives_tokenization(self):
        key = _uploaded_key(_run("/pa rt's/housing/model.obj"))
        assert key == f"{FILES_PREFIX}pa rt's/housing/model.glb"

    def test_metacharacter_subdirectory_introduces_no_command(self):
        command = _run("/$(whoami);rm -rf x/model.obj")
        assert _uploaded_key(command) == f"{FILES_PREFIX}$(whoami);rm -rf x/model.glb"
        # The metacharacters stayed inside ONE token, so the shell sees no new command and no stray
        # argument. An unquoted subdirectory would split '-rf' and 'x/' into their own tokens.
        assert "-rf" not in shlex.split(command)


@pytest.mark.unit
class TestOutputObjectPrefixSeparators:
    """The prefix/subdirectory join, asserted directly — one separator, never two, never none."""

    @pytest.mark.parametrize("files_key,subdir,expected", [
        ("a/files/", "", "a/files/"),
        ("a/files/", "sub/dir", "a/files/sub/dir/"),
        ("a/files", "sub/dir", "a/files/sub/dir/"),
        ("a/files", "", "a/files/"),
        ("a/files/", "/sub/dir/", "a/files/sub/dir/"),
        ("a/files/", None, "a/files/"),
        ("", "sub", "sub/"),
        ("", "", ""),
    ])
    def test_exactly_one_separator_joins_each_part(self, files_key, subdir, expected):
        assert _load().output_object_prefix(files_key, subdir) == expected

    @pytest.mark.parametrize("relative_path,expected", [
        ("/model.obj", ""),
        ("model.obj", ""),
        ("/sub/model.obj", "sub"),
        ("/sub/dir/model.obj", "sub/dir"),
        ("", ""),
        (None, ""),
    ])
    def test_relative_subdir_from_manifest_path(self, relative_path, expected):
        assert _load().relative_subdir_from_manifest_path(relative_path) == expected

    def test_files_prefix_without_a_trailing_slash_gains_exactly_one(self):
        key = _uploaded_key(_run("/sub/dir/model.obj", files_prefix=FILES_PREFIX.rstrip("/")))
        assert key == f"{FILES_PREFIX}sub/dir/model.glb"
        assert "//" not in key
