#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The vendored modules are byte-identical to their canonical sources.

A pipeline code asset cannot import the backend package, so ``manifestHelper.py`` and
``vectorsearch/embeddings.py`` are copied in. A copy that drifts is invisible at deploy: the Lambda
imports cleanly and behaves differently from the module the backend tests exercised."""

import glob
import hashlib
import os

import pytest

import sysgenai_harness as h

_CANONICAL_EMBEDDINGS = os.path.join(h.REPO_ROOT, "backend", "backend", "common", "vectorsearch",
                                     "embeddings.py")
_VENDORED_EMBEDDINGS = os.path.join(h.LAMBDA_DIR, "vectorsearch", "embeddings.py")
_MANIFEST_HELPER = os.path.join(h.LAMBDA_DIR, "manifestHelper.py")
_LOGGER = os.path.join(h.LAMBDA_DIR, "customLogging", "logger.py")


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.md5(handle.read()).hexdigest()


@pytest.mark.unit
class TestVendoredCopies:
    def test_embeddings_is_byte_identical_to_the_canonical_module(self):
        assert os.path.isfile(_CANONICAL_EMBEDDINGS), (
            f"{_CANONICAL_EMBEDDINGS} is missing: WP00 (the canonical embedding adapter) has not "
            "landed; land it before this pipeline")
        assert os.path.isfile(_VENDORED_EMBEDDINGS), _VENDORED_EMBEDDINGS
        assert _digest(_VENDORED_EMBEDDINGS) == _digest(_CANONICAL_EMBEDDINGS), (
            "vectorsearch/embeddings.py drifted from backend/backend/common/vectorsearch/embeddings.py;"
            " edit the canonical file and cp it here")

    def test_embeddings_exposes_the_registry_names(self):
        embeddings = h.load_local("vectorsearch.embeddings")
        assert embeddings.TITAN_V2_MAX_INPUT_CHARS == 30_000
        assert embeddings.NOVA_MME_TEXT_MAX_CHARS == 8_192
        assert embeddings.COHERE_V3_MAX_CHARS == 2_048
        for name in ("EmbeddingModelError", "slug_model_id", "truncate_for_model", "embed_text",
                     "round_vector"):
            assert hasattr(embeddings, name), name
        assert issubclass(embeddings.EmbeddingModelError, Exception)

    def test_manifest_helper_is_identical_to_every_other_vendored_copy(self):
        copies = sorted(glob.glob(os.path.join(h.REPO_ROOT, "backendPipelines", "**", "lambda",
                                               "manifestHelper.py"), recursive=True))
        assert len(copies) >= 10, copies
        digests = {_digest(path) for path in copies}
        assert len(digests) == 1, (
            "manifestHelper.py copies differ; the byte-identity rule requires one hash across the tree")
        assert _digest(_MANIFEST_HELPER) in digests

    def test_manifest_helper_exposes_the_results_prefix(self):
        """WP05 adds ``outputS3AssetResultsPath`` to ``resolve_inputs``; this pipeline writes its
        analysis summary and failure status there, so a pre-WP05 copy is unusable."""
        mh = h.load_local("manifestHelper")
        manifest = {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/models/pump.glb", "assetId": "xidM",
                            "databaseId": "dbM", "relativePath": "/models/pump.glb", "versionId": "v1"}],
            "outputs": {"bucket": "abkt", "files": "pipelines/p/j/output/E1/files/",
                        "metadata": "pipelines/p/j/output/E1/metadata/",
                        "results": "pipelines/p/j/output/E1/results/"},
            "auxBucket": "aux", "auxTempPrefix": "pipelines/system-genai-metadata/E1/",
        }
        resolved = mh.resolve_inputs({}, manifest)
        assert resolved["outputS3AssetResultsPath"] == "s3://abkt/pipelines/p/j/output/E1/results/"

    def test_custom_logging_is_the_common_variant(self):
        source = os.path.join(h.REPO_ROOT, "backendPipelines", "3dRecon", "splatToolbox", "lambda",
                              "customLogging", "logger.py")
        assert _digest(_LOGGER) == _digest(source)
