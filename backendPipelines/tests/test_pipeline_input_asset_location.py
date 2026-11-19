#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pipelines take where an input sits in its asset from the manifest, not from the S3 key.

Each manifest input entry carries the file's asset-relative ``relativePath`` and its asset root
(``assetRootS3Key`` in the bucket ``assetRootBucket``). A pipeline whose outputs keep the input's
subfolder carries the first entry's values the way it carries ``inputVersionId``: ``vamsExecute`` copies
them as ``inputRelativePath``, ``inputAssetRootS3Key`` and ``inputAssetRootBucket``, ``openPipeline``
forwards them in the state-machine input, and ``constructPipeline`` hands them to the container, which
takes the subfolder from the folder of ``relativePath``. Searching the key for the asset ID stays only as
the fallback for a launch without them: the key of an asset created on an existing bucket folder does
not contain the asset ID, and from the second workflow step on an input is keyed under the run's output
folder.

Durable (root CLAUDE.md Rule 13): the chain checks below run over every pipeline in
``MANIFEST_PATH_PIPELINES``, and the container checks over every Cosmos entrypoint in the tree, so a
pipeline copied from one of these keeps the rule.
"""

import importlib.util
import json
import os
import re
import sys
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pipelines that carry the first input entry's location through vamsExecute -> openPipeline ->
# constructPipeline (or the module that builds the container definition).
MANIFEST_PATH_PIPELINES = {
    "preview/3dThumbnail": {
        "vamsExecute": ["vamsExecutePreview3dThumbnailPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py"},
    "conversion/coordinateTransform": {
        "vamsExecute": ["vamsExecuteCoordinateTransformPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py"},
    "genAi/nvidia/cosmos/3": {
        "vamsExecute": ["vamsExecuteCosmos3Pipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py"},
    "genAi/nvidia/cosmos/predict": {
        "vamsExecute": ["vamsExecuteCosmosVideo2WorldPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py"},
    "genAi/nvidia/cosmos/reason": {
        "vamsExecute": ["vamsExecuteCosmosReasonPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py"},
    "genAi/nvidia/cosmos/transfer": {
        "vamsExecute": ["vamsExecuteCosmosTransferPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py"},
    "genAi/cadStepAgent": {
        "vamsExecute": ["vamsExecuteCadStepAgentPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py",
        # The subfolder is resolved in the Lambda; the container receives it as relativeSubdir.
        "builderReads": ("inputRelativePath",)},
    "genAi/imageTo3d": {
        "vamsExecute": ["vamsExecuteImageTo3dPipeline.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py"},
    "multi/modelOps": {
        "vamsExecute": ["vamsExecuteModelOps.py"],
        "openPipeline": "openPipeline.py", "builder": "constructPipeline.py",
        "builderReads": ("inputRelativePath",)},
}

FIELDS = ("inputRelativePath", "inputAssetRootS3Key", "inputAssetRootBucket")
ENTRY_NAMES = {"inputRelativePath": "relativePath", "inputAssetRootS3Key": "assetRootS3Key",
               "inputAssetRootBucket": "assetRootBucket"}


def _lambda_path(pipeline, name):
    return os.path.join(_REPO_ROOT, "backendPipelines", *pipeline.split("/"), "lambda", name)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("pipeline", sorted(MANIFEST_PATH_PIPELINES))
class TestTheChainCarriesTheLocation:
    def test_vams_execute_copies_the_first_entrys_fields(self, pipeline):
        for name in MANIFEST_PATH_PIPELINES[pipeline]["vamsExecute"]:
            source = _read(_lambda_path(pipeline, name))
            for field, entry_name in ENTRY_NAMES.items():
                assert re.search(rf"['\"]{field}['\"]", source), (name, field)
                assert re.search(rf"\.get\(['\"]{entry_name}['\"]\)", source), (name, entry_name)

    def test_open_pipeline_forwards_them(self, pipeline):
        source = _read(_lambda_path(pipeline, MANIFEST_PATH_PIPELINES[pipeline]["openPipeline"]))
        for field in FIELDS:
            assert re.search(rf"['\"]{field}['\"]:\s*event\.get\(['\"]{field}['\"]", source), field

    def test_the_builder_reads_them(self, pipeline):
        source = _read(_lambda_path(pipeline, MANIFEST_PATH_PIPELINES[pipeline]["builder"]))
        for field in MANIFEST_PATH_PIPELINES[pipeline].get("builderReads", FIELDS):
            assert re.search(rf"event\.get\(['\"]{field}['\"]\)", source), field


# --------------------------------------------------------------------------------------------------
# Cosmos: the four constructPipeline Lambdas put the fields on the definition, and every container
# entrypoint takes the subfolder from relativePath.
# --------------------------------------------------------------------------------------------------

COSMOS_LAMBDAS = ("genAi/nvidia/cosmos/3", "genAi/nvidia/cosmos/predict", "genAi/nvidia/cosmos/reason",
                  "genAi/nvidia/cosmos/transfer")
_COSMOS_ROOT = os.path.join(_REPO_ROOT, "backendPipelines", "genAi", "nvidia", "cosmos")
COSMOS_CONTAINERS = sorted(
    os.path.relpath(root, _COSMOS_ROOT).replace(os.sep, "/")
    for root, _, names in os.walk(_COSMOS_ROOT)
    if "__main__.py" in names and os.sep + "tests" not in root)
# Sibling modules a Cosmos entrypoint imports by bare name, which every NVIDIA container defines.
_SIBLINGS = ("inference", "model_manager", "manifest_io", "manifestHelper", "customLogging",
             "customLogging.logger")


def _load_isolated(directory, file_name, alias):
    saved = {name: sys.modules.get(name) for name in _SIBLINGS + ("huggingface_hub",)}
    sys.path.insert(0, directory)
    try:
        for name in _SIBLINGS:
            sys.modules.pop(name, None)
        # The predict v1 entrypoint's model manager imports huggingface_hub at load; the path
        # helpers under test never call it.
        if importlib.util.find_spec("huggingface_hub") is None:
            stub = type(sys)("huggingface_hub")
            stub.snapshot_download = MagicMock()
            sys.modules["huggingface_hub"] = stub
        spec = importlib.util.spec_from_file_location(alias, os.path.join(directory, file_name))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(directory)
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return module


def test_every_cosmos_container_is_checked():
    assert COSMOS_CONTAINERS == ["3/container", "predict/containerv1", "predict/containerv2.5",
                                 "reason/container", "transfer/container"]


@pytest.mark.parametrize("pipeline", COSMOS_LAMBDAS)
def test_cosmos_construct_pipeline_puts_the_location_on_the_definition(pipeline):
    module = _load_isolated(os.path.dirname(_lambda_path(pipeline, "constructPipeline.py")),
                            "constructPipeline.py", f"construct_{pipeline.replace('/', '_')}")
    event = {"inputS3AssetFilePath": "s3://abkt/projects/site-a/clips/source.mp4",
             "outputS3AssetFilesPath": "s3://run-bkt/pipelines/p1/J/output/E1/files/", "assetId": "xidA",
             "inputRelativePath": "/clips/source.mp4", "inputAssetRootS3Key": "projects/site-a/",
             "inputAssetRootBucket": "abkt"}
    command = module.lambda_handler(event, MagicMock())["definition"]
    definition = json.loads(command[-1])
    assert {field: definition[field] for field in FIELDS} == {
        "inputRelativePath": "/clips/source.mp4", "inputAssetRootS3Key": "projects/site-a/",
        "inputAssetRootBucket": "abkt"}
    # An event from an older Lambda carries none, and the definition names them empty.
    del event["inputRelativePath"], event["inputAssetRootS3Key"], event["inputAssetRootBucket"]
    definition = json.loads(module.lambda_handler(event, MagicMock())["definition"][-1])
    assert [definition[field] for field in FIELDS] == ["", "", ""]


@pytest.fixture(scope="module", params=COSMOS_CONTAINERS)
def cosmos_entrypoint(request):
    directory = os.path.join(_COSMOS_ROOT, *request.param.split("/"))
    return _load_isolated(directory, "__main__.py", f"cosmos_entry_{request.param.replace('/', '_')}")


class TestCosmosSubfolder:
    def test_an_asset_on_an_existing_bucket_folder(self, cosmos_entrypoint):
        uri = "s3://abkt/projects/site-a/clips/day1/source.mp4"
        # The key does not contain the asset ID: the search alone writes at the top of the folder.
        assert cosmos_entrypoint.compute_relative_subdir(uri, "xidA") == ""
        assert cosmos_entrypoint.compute_relative_subdir(uri, "xidA", "/clips/day1/source.mp4") == "clips/day1/"

    def test_a_chained_input(self, cosmos_entrypoint):
        uri = "s3://run-bkt/pipelines/p0/J/output/E1/files/clips/source.mp4"
        assert cosmos_entrypoint.compute_relative_subdir(uri, "xidA") == ""
        assert cosmos_entrypoint.compute_relative_subdir(uri, "xidA", "/clips/source.mp4") == "clips/"

    def test_same_named_inputs_in_two_folders_keep_apart(self, cosmos_entrypoint):
        left = cosmos_entrypoint.compute_relative_subdir(
            "s3://abkt/projects/site-a/left/source.mp4", "xidA", "/left/source.mp4")
        right = cosmos_entrypoint.compute_relative_subdir(
            "s3://abkt/projects/site-a/right/source.mp4", "xidA", "/right/source.mp4")
        assert (left, right) == ("left/", "right/")

    def test_a_file_at_the_asset_root(self, cosmos_entrypoint):
        assert cosmos_entrypoint.compute_relative_subdir("s3://abkt/xidA/source.mp4", "xidA", "/source.mp4") == ""

    def test_without_the_manifest_the_key_is_searched(self, cosmos_entrypoint):
        assert cosmos_entrypoint.compute_relative_subdir("s3://abkt/xidA/clips/source.mp4", "xidA") == "clips/"
