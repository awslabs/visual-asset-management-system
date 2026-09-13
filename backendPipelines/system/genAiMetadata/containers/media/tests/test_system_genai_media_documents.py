#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""PDF: page count (pageCount), document information and an ISO-8601 createdAt land in sys_document, the
text excerpt is bounded by maxTextChars and stops scanning pages once it is full, exactly the first two pages
are rasterised, and an unreadable PDF is described without failing."""

import os

import pytest
from PIL import Image

from media_extractors import documents
from media_extractors.common import RENDER_SKIPPED_ERROR
from system_genai_media_fixtures import make_ctx, minimal_pdf_bytes, write


@pytest.mark.unit
class TestExtractPdf:
    def test_three_page_document(self, tmp_path):
        path = write(tmp_path, "report.pdf", minimal_pdf_bytes(pages=3))
        result = documents.extract_pdf(path, make_ctx(tmp_path, "report.pdf"))
        sys_document = result.attributes["sys_document"]
        assert result.file_class == "document"
        assert sys_document["format"] == "PDF" and sys_document["decodable"] is True
        assert sys_document["pageCount"] == 3 and sys_document["pdfVersion"] == "1.4"
        assert "pages" not in sys_document
        assert sys_document["title"] == "Proto Title" and sys_document["author"] == "Proto Author"
        assert sys_document["creationDate"] == "D:20260102030405Z"
        assert sys_document["createdAt"] == "2026-01-02T03:04:05Z"
        assert "subject" not in sys_document and "modDate" not in sys_document
        assert (sys_document["pageWidthPt"], sys_document["pageHeightPt"]) == (300.0, 200.0)
        assert sys_document["hasText"] is True
        assert sys_document["pagesWithText"] == 3 and sys_document["pagesScannedForText"] == 3
        assert "page 1" in result.text_excerpt and "page 3" in result.text_excerpt
        assert result.facts["pages"] == "3 pages"
        assert result.facts["title"] == "Proto Title" and result.facts["author"] == "Proto Author"
        assert result.facts["created"] == "2026-01-02T03:04:05Z"
        assert [os.path.basename(p) for p in result.render_images] == ["media-01.png", "media-02.png"]
        for rendered in result.render_images:
            with Image.open(rendered) as image:
                assert image.format == "PNG" and image.size == (600, 400)
        assert result.render_skipped is None and result.warnings == []

    def test_single_page_renders_one_image(self, tmp_path):
        path = write(tmp_path, "one.pdf", minimal_pdf_bytes(pages=1))
        result = documents.extract_pdf(path, make_ctx(tmp_path, "one.pdf"))
        assert len(result.render_images) == 1
        assert result.facts["pages"] == "1 page"

    def test_text_budget_stops_the_page_scan(self, tmp_path):
        path = write(tmp_path, "long.pdf", minimal_pdf_bytes(pages=3))
        result = documents.extract_pdf(path, make_ctx(tmp_path, "long.pdf", max_text_chars=20))
        assert len(result.text_excerpt) <= 20
        assert result.attributes["sys_document"]["pagesScannedForText"] == 1
        assert result.attributes["sys_document"]["pageCount"] == 3

    def test_missing_creation_date_omits_created_at(self, tmp_path):
        path = write(tmp_path, "undated.pdf", minimal_pdf_bytes(pages=1, creation_date=None))
        result = documents.extract_pdf(path, make_ctx(tmp_path, "undated.pdf"))
        sys_document = result.attributes["sys_document"]
        assert "creationDate" not in sys_document and "createdAt" not in sys_document
        assert "created" not in result.facts and result.warnings == []

    def test_unparseable_creation_date_is_kept_raw_with_a_warning(self, tmp_path):
        path = write(tmp_path, "odd.pdf", minimal_pdf_bytes(pages=1, creation_date="Tuesday"))
        result = documents.extract_pdf(path, make_ctx(tmp_path, "odd.pdf"))
        sys_document = result.attributes["sys_document"]
        assert sys_document["creationDate"] == "Tuesday" and "createdAt" not in sys_document
        assert len(result.warnings) == 1 and "CreationDate" in result.warnings[0]

    def test_garbage_is_described_not_failed(self, tmp_path):
        path = write(tmp_path, "broken.pdf", b"%PDF-1.4 but nothing else")
        result = documents.extract_pdf(path, make_ctx(tmp_path, "broken.pdf"))
        assert result.attributes["sys_document"] == {"format": "PDF", "decodable": False, "encrypted": False}
        assert result.render_skipped == RENDER_SKIPPED_ERROR
        assert "could not be opened" in result.warnings[0]
        assert result.render_images == [] and result.text_excerpt == ""

    def test_one_page_that_will_not_rasterise_is_a_warning(self, tmp_path, monkeypatch):
        path = write(tmp_path, "report.pdf", minimal_pdf_bytes(pages=2))
        real_normalise = documents.normalise_for_vision
        calls = {"count": 0}

        def flaky(image, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("bitmap rejected")
            return real_normalise(image, *args, **kwargs)

        monkeypatch.setattr(documents, "normalise_for_vision", flaky)
        result = documents.extract_pdf(path, make_ctx(tmp_path, "report.pdf"))
        assert [os.path.basename(p) for p in result.render_images] == ["media-02.png"]
        assert len(result.warnings) == 1 and "page 1" in result.warnings[0]
        assert result.render_skipped is None

    def test_no_page_rasterised_is_an_error_skip(self, tmp_path, monkeypatch):
        path = write(tmp_path, "report.pdf", minimal_pdf_bytes(pages=2))
        monkeypatch.setattr(documents, "normalise_for_vision", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        result = documents.extract_pdf(path, make_ctx(tmp_path, "report.pdf"))
        assert result.render_images == []
        assert result.render_skipped == RENDER_SKIPPED_ERROR
        assert result.attributes["sys_document"]["pageCount"] == 2
        assert "page 1" in result.text_excerpt


@pytest.mark.unit
def test_pdf_date_to_iso():
    assert documents.pdf_date_to_iso("D:20260102030405Z") == "2026-01-02T03:04:05Z"
    assert documents.pdf_date_to_iso("D:20260102030405+02'00'") == "2026-01-02T03:04:05+02:00"
    assert documents.pdf_date_to_iso("D:20260102030405-05'") == "2026-01-02T03:04:05-05:00"
    assert documents.pdf_date_to_iso("D:20260102") == "2026-01-02T00:00:00"
    assert documents.pdf_date_to_iso("D:2026") == "2026-01-01T00:00:00"
    assert documents.pdf_date_to_iso("2026-01-02") is None
    assert documents.pdf_date_to_iso("D:20261340") is None
    assert documents.pdf_date_to_iso("") is None and documents.pdf_date_to_iso(None) is None
