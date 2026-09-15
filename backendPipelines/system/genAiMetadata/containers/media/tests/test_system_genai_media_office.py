#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Office formats: a Word document yields its paragraphs and table cells in order with the core properties and
the page count Word recorded; a presentation yields slide text and notes with one page entry per slide; a
workbook yields its first sheet's columns and rows as sys_data and every sheet as full text with one named page
entry per sheet. Nothing is rasterised, a package that will not open is described rather than failed, and the
full text is captured only when the context asks for it."""

import datetime

import pytest

from media_extractors import office
from system_genai_media_fixtures import ENGLISH_TEXT, docx_bytes, make_ctx, pptx_bytes, write, xlsx_bytes


@pytest.mark.unit
class TestDocx:
    def test_paragraphs_and_table_cells_in_order(self, tmp_path):
        path = write(tmp_path, "brief.docx", docx_bytes())
        result = office.extract_docx(path, make_ctx(tmp_path, "brief.docx", capture_full_text=True))
        assert result.file_class == "document"
        sys_document = result.attributes["sys_document"]
        assert sys_document["format"] == "DOCX" and sys_document["decodable"] is True
        assert result.full_text == "Alpha paragraph.\nBeta paragraph.\nname\tqty\nbolt\t10"
        assert result.text_excerpt == result.full_text
        assert sys_document["hasText"] is True and sys_document["wordCount"] == 8
        assert result.render_images == [] and result.render_skipped is None and result.warnings == []

    def test_page_count_comes_from_the_extended_properties(self, tmp_path):
        path = write(tmp_path, "brief.docx", docx_bytes(pages=3))
        result = office.extract_docx(path, make_ctx(tmp_path, "brief.docx"))
        assert result.attributes["sys_document"]["pageCount"] == 3
        assert result.facts["pages"] == "3 pages" and result.facts["words"] == "8 words"

    def test_page_count_is_absent_without_a_recorded_value(self, tmp_path):
        path = write(tmp_path, "brief.docx", docx_bytes(pages=None))
        result = office.extract_docx(path, make_ctx(tmp_path, "brief.docx"))
        assert "pageCount" not in result.attributes["sys_document"] and "pages" not in result.facts

    def test_excerpt_is_bounded_and_full_text_is_whole(self, tmp_path):
        path = write(tmp_path, "long.docx", docx_bytes(paragraphs=tuple([ENGLISH_TEXT.strip()] * 20), table=None))
        result = office.extract_docx(path, make_ctx(tmp_path, "long.docx", max_text_chars=50, capture_full_text=True))
        assert len(result.text_excerpt) <= 50
        assert len(result.full_text) > 1000 and result.full_text_truncated is False
        assert result.page_offsets == []

    def test_full_text_is_captured_only_when_asked(self, tmp_path):
        path = write(tmp_path, "brief.docx", docx_bytes())
        result = office.extract_docx(path, make_ctx(tmp_path, "brief.docx"))
        assert result.full_text == "" and result.page_offsets == [] and result.text_excerpt.startswith("Alpha")

    def test_properties_and_created_at(self, tmp_path):
        path = write(tmp_path, "brief.docx", docx_bytes())
        result = office.extract_docx(path, make_ctx(tmp_path, "brief.docx"))
        sys_document = result.attributes["sys_document"]
        assert sys_document["title"] == "Proto Title" and sys_document["author"] == "Proto Author"
        assert sys_document["createdAt"] == "2026-01-02T03:04:05Z"
        assert result.facts["title"] == "Proto Title" and result.facts["author"] == "Proto Author"
        assert result.facts["created"] == "2026-01-02T03:04:05Z"

    def test_undecodable_docx_is_described_not_failed(self, tmp_path):
        path = write(tmp_path, "broken.docx", b"PK\x03\x04 not a package")
        result = office.extract_docx(path, make_ctx(tmp_path, "broken.docx", capture_full_text=True))
        assert result.attributes["sys_document"] == {"format": "DOCX", "decodable": False}
        assert "could not be opened" in result.warnings[0]
        assert result.render_skipped is None and result.text_excerpt == "" and result.full_text == ""

    def test_a_long_document_stops_collecting_at_the_content_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(office, "CONTENT_TEXT_MAX_CHARS", 100)
        path = write(tmp_path, "long.docx", docx_bytes(paragraphs=tuple([ENGLISH_TEXT.strip()] * 20), table=None))
        result = office.extract_docx(path, make_ctx(tmp_path, "long.docx", max_text_chars=50, capture_full_text=True))
        assert len(result.full_text) == 100 and result.full_text_truncated is True
        assert len(result.text_excerpt) <= 50
        # The count describes the text that was collected; nothing beyond the cap was read into memory.
        assert result.attributes["sys_document"]["wordCount"] == len(office._WORD.findall(result.full_text))


@pytest.mark.unit
class TestPptx:
    def test_slide_text_notes_and_one_page_entry_per_slide(self, tmp_path):
        path = write(tmp_path, "deck.pptx", pptx_bytes())
        result = office.extract_pptx(path, make_ctx(tmp_path, "deck.pptx", capture_full_text=True))
        assert result.file_class == "document"
        sys_document = result.attributes["sys_document"]
        assert sys_document["slideCount"] == 3 and sys_document["pageCount"] == 3
        assert "Notes: Notes 1" in result.full_text and "Notes: Notes 3" in result.full_text
        assert [entry["page"] for entry in result.page_offsets] == [1, 2, 3]
        assert result.page_offsets[0]["start"] == 0
        assert result.full_text[result.page_offsets[1]["start"]:].startswith("Title 2")
        assert result.full_text[result.page_offsets[2]["start"]:].startswith("Title 3")
        assert all("name" not in entry for entry in result.page_offsets)
        assert "Title 1" in result.text_excerpt and result.full_text_truncated is False

    def test_slide_properties_and_facts(self, tmp_path):
        path = write(tmp_path, "deck.pptx", pptx_bytes())
        result = office.extract_pptx(path, make_ctx(tmp_path, "deck.pptx"))
        sys_document = result.attributes["sys_document"]
        assert sys_document["format"] == "PPTX" and sys_document["decodable"] is True
        assert sys_document["title"] == "Proto Deck" and sys_document["author"] == "Proto Author"
        assert sys_document["createdAt"] == "2026-01-02T03:04:05Z"
        assert sys_document["hasText"] is True and sys_document["wordCount"] > 0
        assert result.facts["slides"] == "3 slides"
        assert result.render_images == [] and result.render_skipped is None and result.full_text == ""

    def test_undecodable_pptx_is_described_not_failed(self, tmp_path):
        path = write(tmp_path, "broken.pptx", b"PK\x03\x04 not a package")
        result = office.extract_pptx(path, make_ctx(tmp_path, "broken.pptx"))
        assert result.attributes["sys_document"] == {"format": "PPTX", "decodable": False}
        assert "could not be opened" in result.warnings[0] and result.render_skipped is None

    def test_a_long_deck_stops_collecting_at_the_content_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(office, "CONTENT_TEXT_MAX_CHARS", 20)
        path = write(tmp_path, "deck.pptx", pptx_bytes())
        result = office.extract_pptx(path, make_ctx(tmp_path, "deck.pptx", capture_full_text=True))
        # The first slide's text ("Title 1\nBody 1\nNotes: Notes 1", 29 characters) is cut at the cap; no later
        # slide is collected, and the slide count is the presentation's, not the number of slides kept.
        assert result.full_text == "Title 1\nBody 1\nNotes" and result.full_text_truncated is True
        assert result.page_offsets == [{"page": 1, "start": 0}]
        sys_document = result.attributes["sys_document"]
        assert sys_document["slideCount"] == 3 and sys_document["pageCount"] == 3


@pytest.mark.unit
class TestXlsx:
    def test_first_sheet_columns_rows_and_sheet_count(self, tmp_path):
        path = write(tmp_path, "parts.xlsx", xlsx_bytes())
        result = office.extract_xlsx(path, make_ctx(tmp_path, "parts.xlsx"))
        assert result.file_class == "data"
        sys_data = result.attributes["sys_data"]
        assert sys_data["format"] == "XLSX" and sys_data["decodable"] is True
        assert sys_data["sheetCount"] == 2 and sys_data["sheetNames"] == ["Parts", "Q1"]
        assert sys_data["columnCount"] == 3 and sys_data["columns"] == ["name", "qty", "price"]
        assert sys_data["rowCount"] == 2 and sys_data["rowCountExact"] is True
        assert sys_data["sampleRows"] == [["bolt", "10", "0.25"], ["nut", "20", ""]]
        assert result.text_excerpt == "name,qty,price\nbolt,10,0.25\nnut,20,\n"
        assert (result.facts["sheets"], result.facts["columns"], result.facts["rows"]) == ("2 sheets", "3 columns", "2 rows")
        assert result.render_images == [] and result.render_skipped is None and result.full_text == ""

    def test_full_text_per_sheet_with_named_page_entries(self, tmp_path):
        path = write(tmp_path, "parts.xlsx", xlsx_bytes())
        result = office.extract_xlsx(path, make_ctx(tmp_path, "parts.xlsx", capture_full_text=True))
        first = result.full_text.split("\n\n")[0]
        assert first == "Sheet: Parts\nname\tqty\tprice\nbolt\t10\t0.25\nnut\t20\t"
        assert "Sheet: Q1\nmonth\ttotal\nJan\t100" in result.full_text
        assert result.page_offsets == [{"page": 1, "start": 0, "name": "Parts"},
                                       {"page": 2, "start": len(first) + 2, "name": "Q1"}]
        assert result.full_text_truncated is False

    def test_empty_cells_render_empty_and_values_are_strings(self, tmp_path):
        rows = [["when", "note"], [datetime.datetime(2026, 1, 2, 3, 4, 5), None]]
        path = write(tmp_path, "when.xlsx", xlsx_bytes(sheets=(("When", rows),)))
        result = office.extract_xlsx(path, make_ctx(tmp_path, "when.xlsx"))
        assert result.attributes["sys_data"]["sampleRows"] == [["2026-01-02T03:04:05", ""]]
        assert result.attributes["sys_data"]["sheetCount"] == 1

    def test_full_text_is_cut_at_the_content_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(office, "CONTENT_TEXT_MAX_CHARS", 20)
        path = write(tmp_path, "parts.xlsx", xlsx_bytes())
        result = office.extract_xlsx(path, make_ctx(tmp_path, "parts.xlsx", capture_full_text=True))
        assert len(result.full_text) == 20 and result.full_text_truncated is True
        assert result.page_offsets == [{"page": 1, "start": 0, "name": "Parts"}]

    def test_undecodable_xlsx_is_described_not_failed(self, tmp_path):
        path = write(tmp_path, "broken.xlsx", b"PK\x03\x04 not a package")
        result = office.extract_xlsx(path, make_ctx(tmp_path, "broken.xlsx"))
        assert result.file_class == "data"
        assert result.attributes["sys_data"] == {"format": "XLSX", "decodable": False}
        assert "could not be opened" in result.warnings[0] and result.render_skipped is None

    def test_rows_beyond_the_counting_cap_are_not_counted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(office, "XLSX_MAX_ROWS_COUNTED", 5)
        rows = [["n"]] + [[str(index)] for index in range(8)]
        path = write(tmp_path, "many.xlsx", xlsx_bytes(sheets=(("Many", rows),)))
        result = office.extract_xlsx(path, make_ctx(tmp_path, "many.xlsx"))
        sys_data = result.attributes["sys_data"]
        assert sys_data["rowCount"] == 5 and sys_data["rowCountExact"] is False
        assert sys_data["sampleRows"] == [["0"], ["1"], ["2"], ["3"], ["4"]]
        assert result.facts["rows"] == "5 rows (counting stopped)"
        # The text has its own cap: with the count and the sample satisfied, the loop still reads rows for it.
        result = office.extract_xlsx(path, make_ctx(tmp_path, "many.xlsx", capture_full_text=True))
        assert result.attributes["sys_data"]["rowCount"] == 5
        assert result.full_text == "Sheet: Many\nn\n" + "\n".join(str(index) for index in range(8))
        assert result.full_text_truncated is False
