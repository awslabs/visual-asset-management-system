#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A same-format conversion does not write over its own source, and a format-changing one still
lands exactly where it always did.

**Why this pipeline can convert a file into its own format.** ``supported_formats`` in
``convert_input_output`` is ONE list, checked for both the input and the output, and
``vamsSchema/pipeline.json`` allows all ten of those extensions as input. Four of the built-in
templates pin a target format that is also an accepted input format -- ``convert-to-glb``,
``convert-to-gltf``, ``convert-to-obj``, ``convert-to-stl`` -- so choosing the template that names the
input file's own format is a same-format run, reachable with no custom configuration at all.

**Why that used to land on the operator's own file.** The output file name is the input's stem plus
the output extension, and the output keeps the input's subdirectory within the asset, so a same-format
run produces an output whose ASSET-RELATIVE path equals the input's.

**The staging prefix does not make that safe, and reasoning from it stops one hop early.** The
workflow hands the pipeline the per-execution staging prefix
``pipelines/{pipelineName}/{jobName}/output/{executionId}/files/``, so the STAGED key differs from the
input's. But staging is not where the output comes to rest:
``processWorkflowExecutionOutput.process_external_upload`` lists that prefix, STRIPS it to each file's
asset-relative path, applies the execution's output path extension
(``backend/common/workflows/outputPathExtension``, default ``"/"``, which inserts nothing -- and this
pipeline's ``vamsSchema/workflow.json`` declares no
``defaultOutputFileBaseExecutionPathExtension``), and passes the result to
``uploadFile.complete_external_upload`` as ``relativeKey``. That resolves against the asset's own
``assetLocation.Key``, so an output whose asset-relative path equals the input's becomes a new S3
VERSION of the operator's source file. The asset-relative path, not the staged key, is therefore what
has to differ, and the tests below assert the resolved write-back key rather than the staged copy.

**What makes it differ** is ``SAME_FORMAT_OUTPUT_SUBDIR``, added only when the output extension equals
the input's. Both the subdirectory and the file name still survive, so the output is a sibling of its
source rather than a rename of it. ``TestPlacementMatchesRapidPipeline`` pins the whole placement
against ``multi/rapidPipeline``'s own source, so the two conversion pipelines cannot drift into two
conventions inside one asset.
"""

import os
import sys
import ast
import json
import types
import importlib.util
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

_REPO_ROOT = os.path.abspath(os.path.join(_LAMBDA_DIR, "..", "..", "..", ".."))
_OPE_MODULE = os.path.join(_REPO_ROOT, "backend", "backend", "common", "workflows",
                           "outputPathExtension.py")
_PEER_MODULE = os.path.join(_REPO_ROOT, "backendPipelines", "multi", "rapidPipeline", "lambda",
                            "constructPipeline.py")

# Stub common.logger + trimesh so the module imports without powertools / the mesh library.
if "common" not in sys.modules:
    _common_pkg = types.ModuleType("common")
    _common_logger = types.ModuleType("common.logger")
    _common_logger.safeLogger = lambda **kw: MagicMock()
    _common_pkg.logger = _common_logger
    sys.modules["common"] = _common_pkg
    sys.modules["common.logger"] = _common_logger
if "trimesh" not in sys.modules:
    sys.modules["trimesh"] = MagicMock()

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

ASSET_BUCKET = "abkt"
ASSET_ID = "xidM"
# A multi-segment asset root (an external bucket's baseAssetsPrefix plus the asset id), so an
# assertion about the asset-relative path cannot be satisfied by dropping a single key segment.
ASSET_ROOT = f"org/area/{ASSET_ID}/"
# The per-execution output staging prefix the workflow ASL hands the pipeline.
FILES_PREFIX = "pipelines/p1/MJOB/output/E1/files/"
EXECUTION_INPUTS = "pipelines/workflowExecutionInputs/E1/pipeline1/"
MANIFEST_LOCATION = f"s3://{ASSET_BUCKET}/{EXECUTION_INPUTS}manifest.json"
CONFIG_LOCATION = f"s3://{ASSET_BUCKET}/{EXECUTION_INPUTS}config.json"


def _load():
    """Load the pipeline module from its 'lambda.py' file name (not a valid module name)."""
    spec = importlib.util.spec_from_file_location(
        "trimesh_pipeline_lambda", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


def _apply_output_path_extension():
    """The backend's pure output-path-extension helper, loaded by path (no backend package, no
    boto3), so the write-back placement asserted here is the production one rather than a copy."""
    assert os.path.exists(_OPE_MODULE), _OPE_MODULE
    spec = importlib.util.spec_from_file_location("_ope_for_3dbasic_placement", _OPE_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.apply_output_path_extension


def _peer_placement():
    """rapidPipeline's placement constant and helpers, compiled from that pipeline's own source.

    Only the three definitions are taken, so none of that module's imports (boto3, manifestHelper,
    its logger) run and neither ``sys.path`` nor ``sys.modules`` is touched -- while the comparison is
    still against the peer's real code rather than a duplicate of it kept in this file.
    """
    assert os.path.exists(_PEER_MODULE), _PEER_MODULE
    with open(_PEER_MODULE, encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source)
    wanted = ("SAME_FORMAT_OUTPUT_SUBDIR", "relative_subdir_from_manifest_path",
              "output_relative_subdir")
    segments = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            segments[node.name] = ast.get_source_segment(source, node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    segments[target.id] = ast.get_source_segment(source, node)
    missing = [name for name in wanted if name not in segments]
    assert not missing, (
        f"multi/rapidPipeline no longer defines {missing}, so the two conversion pipelines can no "
        f"longer be compared and their output placement has to be reconciled by hand")
    namespace = {}
    exec(compile("\n\n".join(segments[name] for name in wanted),  # noqa: S102 - pure helpers
                 _PEER_MODULE, "exec"), namespace)
    return namespace


def _same_format_subdir():
    """The folder the module under test adds for a same-format conversion, read off the module so a
    rename of it does not silently stop being asserted. An empty value is rejected here, since it is
    exactly the value that would restore the collision."""
    subdir = _load().SAME_FORMAT_OUTPUT_SUBDIR.strip("/")
    assert subdir, "a same-format output needs a non-empty folder or it resolves onto its own source"
    return subdir


class _Run:
    """What one run of the pipeline actually read and wrote."""

    def __init__(self, input_key, uploaded, downloaded, fetched_keys):
        self.input_key = input_key
        self.uploaded = uploaded
        self.downloaded = downloaded
        self.fetched_keys = fetched_keys

    @property
    def uploaded_key(self):
        return self.uploaded["key"]

    @property
    def uploaded_uri(self):
        return f"s3://{self.uploaded['bucket']}/{self.uploaded['key']}"

    @property
    def input_uri(self):
        return f"s3://{ASSET_BUCKET}/{self.input_key}"

    @property
    def output_relative_path(self):
        """The output's path relative to the output-files prefix -- the value the backend's
        write-back keys on, and what it resolves against the asset root."""
        key = self.uploaded_key
        assert key.startswith(FILES_PREFIX), key
        return key[len(FILES_PREFIX):]

    def write_back_key(self, extension="/"):
        """The asset-bucket key the workflow's write-back resolves this output to.

        This is the hop a trace that stops at the staged upload never reaches, and it is where a
        collision actually lands. ``"/"`` is the default extension and the one this pipeline's
        built-in workflow runs under, since it declares no
        ``defaultOutputFileBaseExecutionPathExtension``.
        """
        relative_key = _apply_output_path_extension()(self.output_relative_path, extension)
        return f"{ASSET_ROOT}{relative_key}"


def _run(relative_path, output_type):
    """Drive ``lambda_handler`` with a workflow manifest and a template configuration, and report
    what it read and wrote. Nothing is hand-passed: the subdirectory that places the output is the one
    the module derives from the manifest itself."""
    module = _load()
    input_key = f"{ASSET_ROOT}{relative_path.lstrip('/')}"
    manifest = {
        "inputFiles": [{
            "bucket": ASSET_BUCKET,
            "key": input_key,
            "relativePath": relative_path,
            "assetId": ASSET_ID,
            "databaseId": "dbM",
            "assetRootS3Key": ASSET_ROOT,
        }],
        "outputs": {"bucket": ASSET_BUCKET, "files": FILES_PREFIX},
    }
    configuration = {"outputType": output_type}
    fetched_keys = []

    def get_object(Bucket, Key):  # noqa: N803 - boto3 kwarg names
        fetched_keys.append(Key)
        if Key.endswith("manifest.json"):
            payload = json.dumps(manifest)
        elif Key.endswith("config.json"):
            payload = json.dumps(configuration)
        else:
            raise AssertionError(f"unexpected key {Key}")
        body = payload.encode("utf-8")
        return {"Body": MagicMock(read=lambda b=body: b)}

    stub_s3 = MagicMock()
    stub_s3.get_object.side_effect = get_object

    uploaded = {}
    downloaded = {}

    def _upload(bucket, key, path):
        uploaded["bucket"], uploaded["key"] = bucket, key
        return key

    def _download(bucket, key, path):
        downloaded["bucket"], downloaded["key"] = bucket, key
        return path

    with patch.object(module, "s3_client", stub_s3), \
            patch.object(module, "download", MagicMock(side_effect=_download)), \
            patch.object(module, "uploadV2", MagicMock(side_effect=_upload)), \
            patch.object(module, "trimesh", MagicMock()):
        response = module.lambda_handler({"body": json.dumps({
            "inputManifestS3Location": MANIFEST_LOCATION,
            "inputConfigurationS3Location": CONFIG_LOCATION,
        })}, MagicMock())

    assert response["statusCode"] == 200, response
    # An unresolved manifest or configuration would leave nothing uploaded and make every assertion
    # below vacuous, so the run is required to have written an object.
    assert uploaded, "the run uploaded nothing"
    return _Run(input_key, uploaded, downloaded, fetched_keys)


@pytest.mark.unit
class TestSameFormatConversionDoesNotWriteItsOwnSource:
    """The four built-in templates whose target format is also an accepted input format."""

    SAME_FORMAT_TEMPLATES = [
        (".glb", "convert-to-glb"),
        (".gltf", "convert-to-gltf"),
        (".obj", "convert-to-obj"),
        (".stl", "convert-to-stl"),
    ]

    @pytest.mark.parametrize("extension,template", SAME_FORMAT_TEMPLATES)
    def test_the_write_back_never_resolves_to_the_input_object(self, extension, template):
        run = _run(f"/parts/housing/model{extension}", extension)
        assert run.uploaded_key != run.input_key, template
        assert run.uploaded_uri != run.input_uri, template
        # The load-bearing one: a differing STAGED key is not enough, because the write-back strips
        # the staging prefix before resolving the output against the asset root.
        assert run.write_back_key() != run.input_key, template
        # Also under a configured output path extension, so a deployment that sets
        # defaultOutputFileBaseExecutionPathExtension is covered rather than being the only thing
        # standing between a same-format run and a mutated input.
        assert run.write_back_key("/YOLO/") != run.input_key, template

    def test_the_asset_relative_path_differs_from_the_input_by_exactly_one_folder(self):
        """The inversion of the collision, decomposed rather than a bare ``!=`` so it cannot be
        satisfied by dropping the subdirectory or by renaming the file -- the two wrong ways to make
        the paths differ."""
        run = _run("/parts/housing/model.glb", ".glb")
        input_relative = run.input_key[len(ASSET_ROOT):]
        assert input_relative == "parts/housing/model.glb"
        assert run.output_relative_path != input_relative
        assert run.output_relative_path == f"parts/housing/{_same_format_subdir()}/model.glb"
        # The difference is ONLY the inserted folder: same subdirectory, same file name.
        assert os.path.dirname(run.output_relative_path).startswith("parts/housing/")
        assert os.path.basename(run.output_relative_path) == os.path.basename(input_relative)
        assert run.write_back_key() == \
            f"{ASSET_ROOT}parts/housing/{_same_format_subdir()}/model.glb"
        assert run.write_back_key("/YOLO/") == \
            f"{ASSET_ROOT}parts/housing/{_same_format_subdir()}/YOLO/model.glb"

    def test_a_same_format_input_at_the_asset_root_also_avoids_its_source(self):
        """The asset root is the common case and has no subdirectory to hang the folder off, so it is
        the case an implementation that only qualifies a non-empty subdirectory would miss."""
        run = _run("/model.stl", ".stl")
        assert run.input_key == f"{ASSET_ROOT}model.stl"
        assert run.output_relative_path == f"{_same_format_subdir()}/model.stl"
        assert run.write_back_key() != run.input_key
        assert "//" not in run.uploaded_key

    def test_the_output_filename_is_not_renamed_or_uniquified(self):
        """The distinct LOCATION is what makes the output a sibling rather than a new version, so the
        file name is untouched -- at the staged key and at the resolved write-back key alike."""
        run = _run("/parts/housing/model.obj", ".obj")
        assert os.path.basename(run.uploaded_key) == "model.obj"
        assert os.path.basename(run.uploaded_key) == os.path.basename(run.input_key)
        assert os.path.basename(run.write_back_key()) == os.path.basename(run.input_key)
        assert "MJOB" not in run.output_relative_path
        assert "E1" not in run.output_relative_path

    def test_the_upload_destination_is_not_the_object_the_download_read(self):
        """Read off the two S3 calls the run actually made rather than inferred from the keys."""
        run = _run("/parts/housing/model.glb", ".glb")
        assert (run.downloaded["bucket"], run.downloaded["key"]) == (ASSET_BUCKET, run.input_key)
        assert (run.uploaded["bucket"], run.uploaded["key"]) != \
            (run.downloaded["bucket"], run.downloaded["key"])

    def test_output_lands_under_the_reserved_pipeline_staging_prefix(self):
        """Necessary but NOT sufficient -- the write-back strips this prefix, so it is not what keeps
        the keys apart. Kept as its own assertion so a future change of the STAGING destination to the
        asset root fails here. ``pipelines/`` is a reserved asset-bucket prefix; no asset's own files
        live under it."""
        run = _run("/parts/housing/model.glb", ".glb")
        assert run.uploaded_key.startswith("pipelines/")
        assert run.uploaded_key.startswith(FILES_PREFIX)
        assert not run.input_key.startswith("pipelines/")

    def test_an_uppercase_source_extension_re_exported_is_also_same_format(self):
        """The output file name carries the lower-cased target extension, so a ``.STL`` source
        re-exported as ``.stl`` would otherwise land beside its source differing from it by extension
        case alone. It is treated as the same format, matching the case-insensitive extension handling
        the rest of the conversion already uses."""
        run = _run("/parts/housing/model.STL", ".stl")
        assert run.output_relative_path == f"parts/housing/{_same_format_subdir()}/model.stl"
        assert run.write_back_key() != run.input_key

    def test_the_manifest_it_was_handed_is_the_one_read(self):
        """The pointer was not merely present: the objects it names are the ones read, so the
        subdirectory above comes from the threaded manifest rather than from a coincidence."""
        run = _run("/parts/housing/model.glb", ".glb")
        assert f"{EXECUTION_INPUTS}manifest.json" in run.fetched_keys, run.fetched_keys
        assert f"{EXECUTION_INPUTS}config.json" in run.fetched_keys, run.fetched_keys


@pytest.mark.unit
class TestFormatChangingConversionIsUnchanged:
    """The control. This pipeline works today for a conversion that changes the file extension, and
    that placement is untouched: no added folder, output directly beside its source. This is what
    keeps the folder specific to the same-format case rather than a blanket change of where every
    conversion writes."""

    def test_output_lands_directly_beside_its_source(self):
        run = _run("/parts/housing/model.obj", ".glb")
        assert run.uploaded_key == f"{FILES_PREFIX}parts/housing/model.glb"
        assert run.output_relative_path == "parts/housing/model.glb"
        assert _same_format_subdir() not in run.output_relative_path
        assert run.write_back_key() == f"{ASSET_ROOT}parts/housing/model.glb"
        assert run.write_back_key() != run.input_key

    def test_an_asset_root_input_stays_at_the_output_root(self):
        """A root-level input must NOT gain a subdirectory. An unconditionally inserted segment emits
        a doubled or empty one here, and the asset root is the common case."""
        run = _run("/model.obj", ".glb")
        assert run.uploaded_key == f"{FILES_PREFIX}model.glb"
        assert "//" not in run.uploaded_key
        assert not run.uploaded_key.endswith("/")
        assert run.write_back_key() == f"{ASSET_ROOT}model.glb"

    def test_output_prefix_nests_inside_the_preserved_subdirectory(self):
        """The output-prefix requirement: the extension goes immediately before the file name, so the
        output lands BESIDE the source file rather than at the asset root."""
        run = _run("/parts/housing/model.obj", ".glb")
        assert run.write_back_key("/YOLO/") == f"{ASSET_ROOT}parts/housing/YOLO/model.glb"
        assert "//" not in run.write_back_key("/YOLO/")

    def test_two_sources_with_the_same_basename_do_not_collide(self):
        """Two distinct sources produce two distinct outputs, at the staged key and at the resolved
        write-back key alike."""
        first = _run("/a/model.obj", ".glb")
        second = _run("/b/model.obj", ".glb")
        assert first.uploaded_key != second.uploaded_key
        assert first.write_back_key() != second.write_back_key()
        assert first.uploaded_key == f"{FILES_PREFIX}a/model.glb"
        assert second.uploaded_key == f"{FILES_PREFIX}b/model.glb"

    def test_an_uppercase_source_extension_still_converts_beside_its_source(self):
        """A format-changing conversion of an upper-case source keeps landing directly beside it, so
        the same-format assertion above is about the format rather than about letter case."""
        run = _run("/parts/housing/model.STL", ".glb")
        assert run.uploaded_key == f"{FILES_PREFIX}parts/housing/model.glb"
        assert _same_format_subdir() not in run.output_relative_path


@pytest.mark.unit
class TestPlacementMatchesRapidPipeline:
    """The two conversion pipelines place a converted file the same way, asserted against
    ``multi/rapidPipeline``'s own source. A change to either one fails here rather than producing two
    conventions inside one asset."""

    @pytest.mark.parametrize("relative_path", [
        "/parts/housing/model.obj",
        "/model.obj",
        "model.obj",
        "/a/b/c/model.obj",
        "/parts/housing/",
        "",
        None,
    ])
    def test_subdirectory_derivation_matches(self, relative_path):
        assert _load().relative_subdir_from_manifest_path(relative_path) == \
            _peer_placement()["relative_subdir_from_manifest_path"](relative_path)

    def test_the_same_format_folder_matches(self):
        assert _load().SAME_FORMAT_OUTPUT_SUBDIR == \
            _peer_placement()["SAME_FORMAT_OUTPUT_SUBDIR"]

    @pytest.mark.parametrize("subdir,input_extension,output_extension", [
        ("parts/housing", ".obj", ".glb"),
        ("parts/housing", ".glb", ".glb"),
        ("", ".glb", ".glb"),
        ("", ".obj", ".glb"),
        ("/parts/housing/", ".stl", ".stl"),
        (None, ".gltf", ".gltf"),
        (None, ".obj", ".glb"),
    ])
    def test_output_subdirectory_matches(self, subdir, input_extension, output_extension):
        assert _load().output_relative_subdir(subdir, input_extension, output_extension) == \
            _peer_placement()["output_relative_subdir"](subdir, input_extension, output_extension)
