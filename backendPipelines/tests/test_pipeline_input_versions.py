#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every built-in pipeline reads its input at the S3 version the run recorded at launch.

The workflow writes a ``versionId`` on each manifest input file: the version selected at launch, or the
one current when the run started (``""`` for a folder selection or an unversioned bucket, ``"null"`` for
an object written before versioning was enabled). ``manifestHelper.resolve_inputs`` exposes the first
input file's version as ``inputVersionId`` (``""`` when there is none to send), and each pipeline carries
it the way it carries ``inputBucketRegion``: ``vamsExecute`` message -> ``openPipeline`` state machine
input -> ``constructPipeline`` definition -> container, which passes it as ``VersionId`` on every read of
the input. A pipeline that drops it reads whatever is current when its compute starts, which is a
different file whenever a newer version was uploaded after launch.

Durable (root CLAUDE.md Rule 13): a pipeline added later must carry the version too, so the handler
lists are checked against the tree rather than pinned to today's pipelines.
"""

import importlib.util
import os
import re

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# vamsExecute handlers whose pipelines take no input file (inputFileArity "none" on every
# registration): there is no input version to carry.
NO_INPUT_FILE_VAMS_EXECUTE = (
    "backendPipelines/genAi/nvidia/cosmos/predict/lambda/vamsExecuteCosmosText2WorldPipeline.py",
)

# Multi-file pipelines whose constructPipeline re-reads the manifest and puts each input file's own
# versionId on the container definition, so no single inputVersionId travels the chain.
PER_ENTRY_VERSION_PIPELINES = (
    "backendPipelines/genAi/videoSopBom/lambda",
)

# Pipelines whose state machine input is built by a module other than openPipeline.py.
OPEN_PIPELINE_SFN_INPUT_BUILDERS = {
    "backendPipelines/multi/rapidPipelineEKS/lambda": "consolidated_handler.py",
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

    @pytest.mark.parametrize("version_id", ["", "null", None, 7])
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
        assert helper.input_version_id({"versionId": "null"}) == ""
        assert helper.input_version_id({}) == ""
        assert helper.input_version_id(None) == ""


class TestEveryPipelineCarriesTheVersion:
    def test_every_vams_execute_handler_forwards_it(self):
        missing = [relative for relative in _lambda_files("vamsExecute")
                   if relative not in NO_INPUT_FILE_VAMS_EXECUTE
                   and relative.rsplit("/", 1)[0] not in PER_ENTRY_VERSION_PIPELINES
                   and not re.search(r"resolved\[['\"]inputVersionId['\"]\]", _read(relative))]
        assert not missing, f"vamsExecute handlers that drop the input version: {missing}"

    def test_the_exempt_handlers_exist(self):
        handlers = _lambda_files("vamsExecute")
        assert all(relative in handlers for relative in NO_INPUT_FILE_VAMS_EXECUTE)

    def test_every_open_pipeline_puts_it_on_the_state_machine_input(self):
        missing = []
        for relative in _lambda_files("openPipeline"):
            directory = relative.rsplit("/", 1)[0]
            if directory in PER_ENTRY_VERSION_PIPELINES:
                continue
            builder = f"{directory}/{OPEN_PIPELINE_SFN_INPUT_BUILDERS.get(directory, 'openPipeline.py')}"
            if not re.search(r"['\"]inputVersionId['\"]", _read(builder)):
                missing.append(builder)
        assert not missing, f"state machine inputs without the input version: {missing}"

    def test_every_construct_pipeline_reads_it(self):
        def carries(relative):
            source = _read(relative)
            if relative.rsplit("/", 1)[0] in PER_ENTRY_VERSION_PIPELINES:
                return re.search(r"entry\.get\(['\"]versionId['\"]\)", source) is not None
            return "inputVersionId" in source
        missing = [relative for relative in _lambda_files("constructPipeline") if not carries(relative)]
        assert not missing, f"constructPipeline handlers that drop the input version: {missing}"
