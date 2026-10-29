#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Content chunks: 1,600-character windows with a 200-character overlap, each end snapped to the last paragraph
or sentence break in the window's final fifth, at most 1,000 chunks per file version with the shortfall
counted, labels that carry the page or sheet the chunk starts on, and keys that are the chunk's ordinal
zero-padded to six digits — pinned equal to the backend's documentIds builder by loading it by path."""

import dataclasses
import importlib.util
import os

import pytest

import sysgenai_harness as h

cc = h.load_local("contentChunks")

_DOCUMENT_IDS = os.path.join(h.REPO_ROOT, "backend", "backend", "common", "indexing", "documentIds.py")


def _backend_document_ids():
    assert os.path.isfile(_DOCUMENT_IDS), (
        f"{_DOCUMENT_IDS} is missing: the shared identity helpers have not landed")
    spec = importlib.util.spec_from_file_location("sysgenai_backend_documentIds_chunks", _DOCUMENT_IDS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "build_text_chunk_key"), (
        "documentIds.py has no build_text_chunk_key: the segment key builders have not landed")
    return module


@pytest.mark.unit
class TestConstantsAndKeys:
    def test_constants_match_the_registry(self):
        backend = _backend_document_ids()
        assert cc.CONTENT_EMBED_MAX_FILE_BYTES == 50 * 1024 * 1024
        assert cc.CONTENT_TEXT_MAX_CHARS == 2_000_000
        assert cc.CONTENT_CHUNK_CHARS == 1_600
        assert cc.CONTENT_CHUNK_OVERLAP_CHARS == 200
        assert cc.CONTENT_CHUNK_MAX == 1_000
        assert cc.SEGMENT_KIND == "textChunk" and cc.SEGMENT_KIND in backend.SEGMENT_KINDS

    def test_key_is_pinned_to_the_backend_builder(self):
        backend = _backend_document_ids()
        for index in (0, 1, 12, 200, 999_999):
            assert cc.build_text_chunk_key(index) == backend.build_text_chunk_key(index), index
        for bad in (-1, 1_000_000):
            with pytest.raises(ValueError):
                cc.build_text_chunk_key(bad)
            with pytest.raises(ValueError):
                backend.build_text_chunk_key(bad)

    def test_key_shape_and_sort(self):
        keys = [cc.build_text_chunk_key(index) for index in (1, 12, 200)]
        assert keys == ["c000001", "c000012", "c000200"] == sorted(keys)
        assert all(len(key.encode("utf-8")) <= _backend_document_ids().SEGMENT_KEY_MAX_BYTES for key in keys)

    def test_chunk_dataclass_fields(self):
        assert [field.name for field in dataclasses.fields(cc.Chunk)] == ["index", "start", "end", "text", "page"]
        chunk = cc.Chunk(index=1, start=0, end=5, text="hello", page=None)
        assert (chunk.index, chunk.start, chunk.end, chunk.text, chunk.page) == (1, 0, 5, "hello", None)


@pytest.mark.unit
class TestChunking:
    def test_short_text_is_one_chunk(self):
        chunks, dropped = cc.chunk_text("Hello world.", None)
        assert chunks == [cc.Chunk(index=1, start=0, end=12, text="Hello world.", page=None)] and dropped == 0

    def test_windows_advance_by_the_window_minus_the_overlap(self):
        chunks, dropped = cc.chunk_text("x" * 4000, None)
        assert dropped == 0
        assert [(chunk.index, chunk.start, chunk.end) for chunk in chunks] == [(1, 0, 1600), (2, 1400, 3000), (3, 2800, 4000)]
        assert [len(chunk.text) for chunk in chunks] == [1600, 1600, 1200]

    def test_the_end_snaps_to_the_last_paragraph_break_in_the_final_fifth(self):
        text = "a" * 1500 + "\n\n" + "b" * 2000
        chunks, _dropped = cc.chunk_text(text, None)
        assert chunks[0].end == 1502 and chunks[0].text == "a" * 1500
        assert chunks[1].start == 1302

    def test_the_end_snaps_to_a_sentence_break_when_there_is_no_paragraph_break(self):
        text = "a" * 1449 + ". " + "b" * 2000
        chunks, _dropped = cc.chunk_text(text, None)
        assert chunks[0].end == 1451 and chunks[0].text.endswith("a.")
        assert chunks[1].start == 1251

    def test_a_break_before_the_final_fifth_is_ignored(self):
        text = "a" * 1000 + "\n\n" + "b" * 3000
        chunks, _dropped = cc.chunk_text(text, None)
        assert chunks[0].end == 1600

    def test_labels_carry_page_numbers(self):
        pages = [{"page": 1, "start": 0}, {"page": 2, "start": 1000}]
        chunks, _dropped = cc.chunk_text("x" * 3000, pages)
        assert [(chunk.index, chunk.page) for chunk in chunks] == [(1, 1), (2, 2)]
        assert cc.chunk_label(2, len(chunks), chunks[1].page) == "chunk 2/2 \u00b7 page 2"

    def test_labels_carry_sheet_names(self):
        pages = [{"page": 1, "start": 0, "name": "Parts"}, {"page": 2, "start": 100, "name": "Q1"}]
        chunks, _dropped = cc.chunk_text("n" * 150, pages)
        assert len(chunks) == 1 and chunks[0].page == "Parts"
        assert cc.chunk_label(1, 1, chunks[0].page) == "chunk 1/1 \u00b7 sheet Parts"

    def test_label_without_pages(self):
        assert cc.chunk_label(12, 200, None) == "chunk 12/200"
        assert cc.chunk_label(1, 1, "") == "chunk 1/1"
        # Malformed page entries are skipped, so a chunk falls back to no page rather than a wrong one.
        chunks, _dropped = cc.chunk_text("short text", [{"start": "x"}, "not a dict", {"page": True, "start": 0}])
        assert chunks[0].page is None

    def test_the_cap_drops_and_counts(self, monkeypatch):
        monkeypatch.setattr(cc, "CONTENT_CHUNK_MAX", 3)
        chunks, dropped = cc.chunk_text("x" * 14000, None)
        assert [chunk.index for chunk in chunks] == [1, 2, 3] and dropped == 7
        assert [cc.build_text_chunk_key(chunk.index) for chunk in chunks] == ["c000001", "c000002", "c000003"]

    def test_empty_and_whitespace_text_produce_no_chunks(self):
        assert cc.chunk_text("", None) == ([], 0)
        assert cc.chunk_text("   \n\n ", None) == ([], 0)
        assert cc.chunk_text(None, None) == ([], 0)

    def test_whitespace_only_windows_are_skipped_without_taking_an_ordinal(self):
        text = "a" * 100 + " " * 3000 + "b" * 10
        chunks, dropped = cc.chunk_text(text, None)
        assert dropped == 0
        assert [(chunk.index, chunk.text) for chunk in chunks] == [(1, "a" * 100), (2, "b" * 10)]
