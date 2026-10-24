#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The vendored manifestHelper resolves the execution's results prefix as ``outputS3AssetResultsPath``.

The manifest's ``outputs`` block carries five bucket-relative prefixes; the helper reconstructs four of
them into the ``s3://`` form pipelines forward. The fifth, ``results``, is where a pipeline writes
structured result documents and the reserved ``execution.status.json``, so it is resolved beside the
other four under the same naming and with the same legacy-payload fallback.
"""

import importlib.util
import os
import sys

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HELPER_PATH = os.path.join(_LAMBDA_DIR, "manifestHelper.py")


def _load_helper():
    """This pipeline's manifestHelper, executed under a name only this suite uses so another
    pipeline's identically named copy cannot shadow it (backendPipelines/CLAUDE.md, Test Conventions)."""
    spec = importlib.util.spec_from_file_location(
        "preview3dthumbnail_manifestHelper_results_undertest", _HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    loaded = os.path.normcase(os.path.normpath(os.path.abspath(module.__file__)))
    assert loaded == os.path.normcase(os.path.normpath(_HELPER_PATH)), module.__file__
    return module


mh = _load_helper()

BUCKET = "run-bucket"
RUN = "pipelines/p1/JOB/output/E1/"
RESULTS = RUN + "results/"


def _manifest(**outputs):
    base = {"bucket": BUCKET, "files": RUN + "files/", "previews": RUN + "previews/",
            "metadata": RUN + "metadata/", "results": RESULTS}
    base.update(outputs)
    return {"schemaVersion": 1,
            "inputFiles": [{"relativePath": "/scan.e57", "databaseId": "db", "assetId": "a1",
                            "bucket": "abkt", "key": "a1/scan.e57"}],
            "outputs": base}


@pytest.mark.unit
class TestOutputS3AssetResultsPath:
    def test_reconstructed_from_the_manifest_results_prefix(self):
        resolved = mh.resolve_inputs({}, _manifest())
        assert resolved["outputS3AssetResultsPath"] == f"s3://{BUCKET}/{RESULTS}"

    def test_keeps_the_trailing_slash_so_an_object_name_can_be_appended(self):
        resolved = mh.resolve_inputs({}, _manifest())
        assert resolved["outputS3AssetResultsPath"].endswith("/")
        assert (resolved["outputS3AssetResultsPath"] + "execution.status.json"
                == f"s3://{BUCKET}/{RESULTS}execution.status.json")

    def test_sits_beside_the_other_output_paths(self):
        resolved = mh.resolve_inputs({}, _manifest())
        for key in ("outputS3AssetFilesPath", "outputS3AssetPreviewPath",
                    "outputS3AssetMetadataPath", "outputS3AssetResultsPath"):
            assert resolved[key].startswith(f"s3://{BUCKET}/{RUN}"), key

    def test_the_legacy_payload_value_is_the_fallback(self):
        legacy = {"outputS3AssetResultsPath": "s3://legacy/pipelines/p1/JOB/output/E1/results/"}
        assert mh.resolve_inputs(legacy, None)["outputS3AssetResultsPath"] == legacy["outputS3AssetResultsPath"]

    def test_a_manifest_without_a_results_prefix_keeps_the_legacy_value(self):
        legacy = {"outputS3AssetResultsPath": "s3://legacy/results/"}
        resolved = mh.resolve_inputs(legacy, _manifest(results=""))
        assert resolved["outputS3AssetResultsPath"] == "s3://legacy/results/"

    def test_absent_everywhere_resolves_to_an_empty_string(self):
        assert mh.resolve_inputs({}, None)["outputS3AssetResultsPath"] == ""
        assert mh.resolve_inputs({}, _manifest(results=""))["outputS3AssetResultsPath"] == ""
