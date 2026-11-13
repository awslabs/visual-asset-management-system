# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Natural-language search hands out no file content, so the quarantine block has nothing to guard.

Like keyword search, `POST /search/nlp` lists quarantined assets the caller may read: a hit carries
the index fields (identity, name, type, tags, size, version, preview key) and the vector facts, never
a URL or the embedded text (`sourceText` stays in the vector table). The thumbnail the web shows for
a hit is fetched through the guarded download route, which serves preview images for a quarantined
asset and refuses its files.
"""

import json

import pytest

from tests.handlers.osVectorSearch.test_vectorSearchService import _load_service


@pytest.mark.unit
def test_a_file_hit_carries_no_url_and_no_embedded_text(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_DISABLED", "true")
    m = _load_service()
    item = {"databaseId": "db1", "assetId": "a1", "filePath": "/manual.pdf", "versionId": "v1",
            "fileExt": "pdf", "fileSize": 10, "previewFileKey": "/manual.pdf.previewFile.png",
            "sourceText": "CONFIDENTIAL torque values", "embeddingModelId": "m", "indexedAt": "t",
            "segmentKind": "chunk", "segmentKey": "c3", "segmentLabel": "page 3"}
    hit = m._file_hit({"item": item, "asset": {"assetName": "Pump", "tags": []}, "distance": 0.2,
                       "segmentHits": 1, "bestSegment": m._segment_ref(item)})
    text = json.dumps(hit)
    assert "CONFIDENTIAL" not in text
    assert "sourceText" not in text
    assert "http" not in text.lower() and "presigned" not in text.lower()
    assert hit["_source"]["str_previewfilekey"] == "/manual.pdf.previewFile.png"
