#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every built-in pipeline reads its input at the S3 version the run recorded at launch.

The workflow writes a ``versionId`` on each manifest input file: the version selected at launch, or the
one current when the run started (``""`` for a folder selection or an unversioned bucket, ``"null"`` for
an object written while versioning was off, which is passed through as a real version id). ``manifestHelper.resolve_inputs`` exposes the first
input file's version as ``inputVersionId`` (``""`` when there is none to send), and each pipeline carries
it the way it carries ``inputBucketRegion``: ``vamsExecute`` message -> ``openPipeline`` state machine
input -> ``constructPipeline`` definition -> container, which passes it as ``VersionId`` on every read of
the input. A pipeline that drops it reads whatever is current when its compute starts, which is a
different file whenever a newer version was uploaded after launch.

Durable (root CLAUDE.md Rule 13): a pipeline added later must carry the version too, so the handler
lists are checked against the tree rather than pinned to today's pipelines.
"""

import importlib.util
import json
import os
import re

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# vamsExecute handlers whose pipelines take no input file (inputFileArity "none" on every
# registration): there is no input version to carry.
NO_INPUT_FILE_VAMS_EXECUTE = (
    "backendPipelines/genAi/nvidia/cosmos/predict/lambda/vamsExecuteCosmosText2WorldPipeline.py",
)

# Pipelines whose input is the whole asset (systemConfig.assetScope.wholeAsset on every registration),
# read as a prefix with `aws s3 sync`: a prefix has no object version, and the manifest records ""
# for it, so there is no input version to carry.
WHOLE_ASSET_PIPELINES = (
    "backendPipelines/genAi/nvidia/gr00t",
)

# Multi-file pipelines whose constructPipeline re-reads the manifest and puts each input file's own
# versionId on the container definition, so no single inputVersionId travels the chain.
PER_ENTRY_VERSION_PIPELINES = (
    "backendPipelines/genAi/videoSopBom/lambda",
)

# Multi-file pipelines whose CONTAINER re-reads the run manifest (the job command carries only its
# location, since AWS Batch bounds the command's size) and reads each input file's own versionId there.
# The check reads the named container module instead of constructPipeline.
CONTAINER_MANIFEST_PIPELINES = {
    "backendPipelines/utility/zipArchive/lambda": "backendPipelines/utility/zipArchive/container/zip_archive_pipeline/runner.py",
}

# Pipelines whose container definition is built by a module other than constructPipeline.py.
CONSTRUCT_PIPELINE_BUILDERS = {
    "backendPipelines/multi/rapidPipelineEKS/lambda/consolidated_handler.py",
}


def _read(relative):
    with open(os.path.join(_REPO_ROOT, *relative.split("/")), "r", encoding="utf-8") as f:
        return f.read()


def _lambda_files(prefix):
    return sorted(
        os.path.relpath(os.path.join(root, name), _REPO_ROOT).replace(os.sep, "/")
        for root, _, names in os.walk(os.path.join(_REPO_ROOT, "backendPipelines"))
        for name in names
        if name.startswith(prefix) and name.endswith(".py") and os.path.basename(root) == "lambda")


def _load_manifest_helper():
    path = os.path.join(_REPO_ROOT, "backendPipelines", "preview", "3dThumbnail", "lambda", "manifestHelper.py")
    spec = importlib.util.spec_from_file_location("manifestHelper_input_versions_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    return _load_manifest_helper()


def _manifest(*version_ids):
    files = [{"relativePath": f"/f{i}.glb", "bucket": "asset-bkt", "bucketRegion": "", "key": f"xid/f{i}.glb",
              "assetId": "xid", "databaseId": "db", "assetRootS3Key": "xid/", "auxPreviewPrefix": f"db/xid/f{i}",
              "versionId": version_id} for i, version_id in enumerate(version_ids)]
    return {"inputFiles": files, "outputs": {"bucket": "asset-bkt", "files": "o/files/"},
            "auxBucket": "aux-bkt", "auxTempPrefix": "pipelines/p/E1/", "systemConfig": {}}


class TestResolvedInputsCarryTheVersion:
    def test_the_first_input_files_version(self, helper):
        assert helper.resolve_inputs({}, _manifest("v-2", "v-1"))["inputVersionId"] == "v-2"

    def test_the_null_version_is_sent(self, helper):
        assert helper.resolve_inputs({}, _manifest("null", "v-1"))["inputVersionId"] == "null"

    @pytest.mark.parametrize("version_id", ["", None, 7])
    def test_no_version_to_send(self, helper, version_id):
        assert helper.resolve_inputs({}, _manifest(version_id))["inputVersionId"] == ""

    def test_a_manifest_written_without_versions(self, helper):
        manifest = _manifest("v-1")
        del manifest["inputFiles"][0]["versionId"]
        assert helper.resolve_inputs({}, manifest)["inputVersionId"] == ""

    def test_a_legacy_payload_and_a_file_less_manifest(self, helper):
        assert helper.resolve_inputs({"inputS3AssetFilePath": "s3://b/k"}, None)["inputVersionId"] == ""
        assert helper.resolve_inputs({}, _manifest())["inputVersionId"] == ""

    def test_input_version_id_reads_one_entry(self, helper):
        assert helper.input_version_id({"versionId": "abc"}) == "abc"
        # "null" is the version S3 reports for an object written while versioning was off: a real id.
        assert helper.input_version_id({"versionId": "null"}) == "null"
        assert helper.input_version_id({}) == ""
        assert helper.input_version_id(None) == ""


def _whole_asset(relative):
    return any(relative.startswith(f"{pipeline}/") for pipeline in WHOLE_ASSET_PIPELINES)


class TestEveryPipelineCarriesTheVersion:
    def test_every_vams_execute_handler_forwards_it(self):
        missing = [relative for relative in _lambda_files("vamsExecute")
                   if relative not in NO_INPUT_FILE_VAMS_EXECUTE
                   and not _whole_asset(relative)
                   and relative.rsplit("/", 1)[0] not in PER_ENTRY_VERSION_PIPELINES
                   and relative.rsplit("/", 1)[0] not in CONTAINER_MANIFEST_PIPELINES
                   and not re.search(r"resolved\[['\"]inputVersionId['\"]\]", _read(relative))]
        assert not missing, f"vamsExecute handlers that drop the input version: {missing}"

    def test_the_exempt_handlers_exist(self):
        handlers = _lambda_files("vamsExecute")
        assert all(relative in handlers for relative in NO_INPUT_FILE_VAMS_EXECUTE)
        assert all(os.path.isfile(os.path.join(_REPO_ROOT, *relative.split("/")))
                   for relative in CONSTRUCT_PIPELINE_BUILDERS)

    @pytest.mark.parametrize("pipeline", WHOLE_ASSET_PIPELINES)
    def test_the_whole_asset_pipelines_still_take_the_whole_asset(self, pipeline):
        # The exemption holds only while the pipeline registers as whole-asset and reads a prefix even
        # when one file is selected. A pipeline that starts reading a single object must carry its
        # version like the rest.
        schema = json.loads(_read(f"{pipeline}/vamsSchema/pipeline.json"))
        assert ((schema.get("systemConfig") or {}).get("assetScope") or {}).get("wholeAsset") is True

    def test_every_open_pipeline_puts_it_on_the_state_machine_input(self):
        missing = [relative for relative in _lambda_files("openPipeline")
                   if not _whole_asset(relative)
                   and relative.rsplit("/", 1)[0] not in PER_ENTRY_VERSION_PIPELINES
                   and relative.rsplit("/", 1)[0] not in CONTAINER_MANIFEST_PIPELINES
                   and not re.search(r"['\"]inputVersionId['\"]", _read(relative))]
        assert not missing, f"state machine inputs without the input version: {missing}"

    def test_every_construct_pipeline_reads_it(self):
        def carries(relative):
            source = _read(relative)
            if relative.rsplit("/", 1)[0] in PER_ENTRY_VERSION_PIPELINES:
                return re.search(r"entry\.get\(['\"]versionId['\"]\)", source) is not None
            if relative.rsplit("/", 1)[0] in CONTAINER_MANIFEST_PIPELINES:
                container = _read(CONTAINER_MANIFEST_PIPELINES[relative.rsplit("/", 1)[0]])
                return re.search(r"entry\.get\(['\"]versionId['\"]\)", container) is not None
            return "inputVersionId" in source
        builders = _lambda_files("constructPipeline") + sorted(CONSTRUCT_PIPELINE_BUILDERS)
        missing = [relative for relative in builders if not _whole_asset(relative) and not carries(relative)]
        assert not missing, f"constructPipeline handlers that drop the input version: {missing}"

    def test_every_open_pipeline_is_found(self):
        # A pipeline lambda directory with a vamsExecute handler but no openPipeline.py would leave
        # the state machine input check above with nothing to scan.
        open_dirs = {relative.rsplit("/", 1)[0] for relative in _lambda_files("openPipeline")}
        execute_dirs = {relative.rsplit("/", 1)[0] for relative in _lambda_files("vamsExecute")}
        assert execute_dirs <= open_dirs | set(PER_ENTRY_VERSION_PIPELINES), execute_dirs - open_dirs
