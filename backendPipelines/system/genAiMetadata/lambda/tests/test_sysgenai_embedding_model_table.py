#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The embedding model table ships beside every vendored copy of the embedding adapter.

``vectorsearch/embeddings.py`` reads its families, widths and text limits from
``embeddingModels.json`` in its own directory, and the deployment's configuration validation reads the
canonical file in the backend. A copy that is missing fails the Lambda at import; a copy that drifts
accepts or truncates differently from what the deployment validated."""

import hashlib
import json
import os

import pytest

import sysgenai_harness as h

_CANONICAL_TABLE = os.path.join(h.REPO_ROOT, "backend", "backend", "common", "vectorsearch",
                                "embeddingModels.json")
_COPIES = (
    os.path.join(h.LAMBDA_DIR, "vectorsearch", "embeddingModels.json"),
    os.path.join(h.PIPELINE_ROOT, "containers", "media", "vectorsearch", "embeddingModels.json"),
)


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.md5(handle.read()).hexdigest()


@pytest.mark.unit
class TestEmbeddingModelTable:
    def test_every_copy_is_byte_identical_to_the_canonical_table(self):
        assert os.path.isfile(_CANONICAL_TABLE), _CANONICAL_TABLE
        for copy in _COPIES:
            assert os.path.isfile(copy), copy
            assert _digest(copy) == _digest(_CANONICAL_TABLE), (
                f"{copy} drifted from backend/backend/common/vectorsearch/embeddingModels.json; edit the "
                "canonical file and cp it here")

    def test_the_vendored_adapter_reads_the_table_beside_it(self):
        embeddings = h.load_local("vectorsearch.embeddings")
        assert embeddings.MODEL_TABLE_PATH == _COPIES[0]
        with open(_COPIES[0], encoding="utf-8") as handle:
            families = json.load(handle)["families"]
        assert {entry["family"]: tuple(entry["dimensions"]) for entry in families} == embeddings._DIMENSIONS
