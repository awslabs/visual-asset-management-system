#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Text and code: encoding, counts (lineCount, wordCount) and a language guess land in sys_text; HTML is
stripped to its visible text; notebooks join their cell sources; a .json tileset is promoted to tiles3d and a
GeoJSON .json to `data` with sys_geo; bytes that are not text demote the file to `other`; large files stream
their line count and flag the truncated excerpt; the tileset's root region is recorded as six floats or null."""

import json
import os

import pytest

from media_extractors import text, tiles3d
from media_extractors.common import RENDER_SKIPPED_UNSUPPORTED
from system_genai_media_fixtures import (
    ENGLISH_TEXT, geojson_point_dict, make_ctx, png_bytes, tileset_dict, write,
)


@pytest.mark.unit
class TestDecodeHead:
    def test_utf8_and_bom(self):
        assert text.decode_head("héllo".encode("utf-8")) == ("héllo", "utf_8")
        assert text.decode_head(b"\xef\xbb\xbfhi") == ("hi", "utf_8_sig")
        assert text.decode_head(b"") == ("", "utf_8")

    def test_utf16_with_bom(self):
        decoded, codec = text.decode_head("hi there".encode("utf-16"))
        assert decoded == "hi there" and codec == "utf_16"

    def test_legacy_single_byte_encoding_is_detected(self):
        decoded, codec = text.decode_head(
            "Grüße aus München und der Straße, mit vielen Umlauten im Text.".encode("latin-1"))
        assert decoded is not None and codec not in (None, "utf_8")

    def test_binary_is_not_text(self):
        assert text.decode_head(png_bytes(4, 4)) == (None, None)
        assert text.decode_head(b"\x00\x01\x02\x03 control bytes") == (None, None)


@pytest.mark.unit
class TestHelpers:
    def test_strip_html_drops_script_and_style_and_finds_the_title(self):
        markup = ("<html><head><title>Page &amp; Title</title><style>a{}</style></head>"
                  "<body><h1>Hi &amp; bye</h1><script>var x=1;</script><p>Para</p></body></html>")
        body, title = text.strip_html(markup)
        assert body == "Page & Title Hi & bye Para"
        assert title == "Page & Title"

    def test_notebook_text_joins_sources_and_counts_cells(self):
        notebook = {"cells": [
            {"cell_type": "markdown", "source": ["# Heading\n", "prose"]},
            {"cell_type": "code", "source": "print(1)"},
        ], "metadata": {"kernelspec": {"name": "python3"}}}
        body, facts = text.notebook_text(json.dumps(notebook))
        assert body == "# Heading\nprose\n\nprint(1)"
        assert facts == {"notebookCells": 2, "codeCells": 1, "markdownCells": 1, "kernel": "python3"}

    def test_notebook_text_falls_back_to_plain_text(self):
        assert text.notebook_text("not json") == ("not json", {})

    def test_guess_language(self):
        assert text.guess_language(ENGLISH_TEXT) == "en"
        assert text.guess_language("too short") == "unknown"
        assert text.guess_language(" ".join(["zqx"] * 50)) == "unknown"

    def test_count_newlines(self, tmp_path):
        path = write(tmp_path, "lines.txt", b"a\nb\nc")
        assert text.count_newlines(path) == 2


@pytest.mark.unit
class TestExtractText:
    def test_plain_english(self, tmp_path):
        path = write(tmp_path, "notes.txt", ENGLISH_TEXT.encode("utf-8"))
        result = text.extract_text(path, make_ctx(tmp_path, "notes.txt"))
        sys_text = result.attributes["sys_text"]
        assert result.file_class == "text"
        assert sys_text["kind"] == "plain" and sys_text["encoding"] == "utf_8"
        assert sys_text["lineCount"] == 1 and sys_text["wordCount"] == len(ENGLISH_TEXT.split())
        assert "lines" not in sys_text and "words" not in sys_text
        assert sys_text["chars"] == len(ENGLISH_TEXT) and sys_text["truncatedForAnalysis"] is False
        assert sys_text["language"] == "en" and "codeLanguage" not in sys_text
        assert result.facts["lines"] == "1 line" and result.facts["language"] == "en"
        assert result.facts["textKind"] == "plain" and result.facts["encoding"] == "utf_8"
        assert result.text_excerpt == ENGLISH_TEXT
        assert result.render_images == [] and result.render_skipped is None

    def test_code_reports_its_language_and_no_prose_guess(self, tmp_path):
        path = write(tmp_path, "tool.py", b"import os\n\ndef main():\n    return os.getcwd()\n")
        sys_text = text.extract_text(path, make_ctx(tmp_path, "tool.py")).attributes["sys_text"]
        assert sys_text["kind"] == "code" and sys_text["codeLanguage"] == "python"
        assert "language" not in sys_text
        assert sys_text["lineCount"] == 4

    def test_html_is_stripped(self, tmp_path):
        path = write(tmp_path, "page.html", b"<html><head><title>T</title></head><body><p>Hello <b>world</b></p></body></html>")
        result = text.extract_text(path, make_ctx(tmp_path, "page.html"))
        assert result.attributes["sys_text"]["kind"] == "html"
        assert result.attributes["sys_text"]["title"] == "T"
        assert result.text_excerpt == "T Hello world"
        assert result.facts["title"] == "T"

    def test_notebook(self, tmp_path):
        notebook = {"cells": [{"cell_type": "code", "source": "x = 1"}, {"cell_type": "markdown", "source": "# Hi"}]}
        path = write(tmp_path, "nb.ipynb", json.dumps(notebook).encode())
        result = text.extract_text(path, make_ctx(tmp_path, "nb.ipynb"))
        assert result.attributes["sys_text"]["kind"] == "notebook"
        assert result.attributes["sys_text"]["notebookCells"] == 2
        assert result.text_excerpt == "x = 1\n\n# Hi"

    def test_json_tileset_is_promoted(self, tmp_path):
        path = write(tmp_path, "tileset.json", json.dumps(tileset_dict()).encode())
        result = text.extract_text(path, make_ctx(tmp_path, "tileset.json"))
        assert result.file_class == "tiles3d"
        assert "sys_tiles3d" in result.attributes and "sys_text" not in result.attributes

    def test_json_geojson_is_reclassified_to_data(self, tmp_path):
        path = write(tmp_path, "sites.json", json.dumps(geojson_point_dict()).encode())
        result = text.extract_text(path, make_ctx(tmp_path, "sites.json"))
        assert result.file_class == "data"
        assert "sys_geo" in result.attributes and "sys_text" not in result.attributes
        assert result.attributes["sys_geo"]["footprint"] == {"type": "Point", "coordinates": [151.2, -33.9]}
        assert result.render_images == [] and result.render_skipped is None

    def test_json_tileset_larger_than_the_head_is_still_promoted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(text, "TEXT_HEAD_BYTES", 100)
        body = json.dumps(tileset_dict()).encode("utf-8")
        assert len(body) > 100, "the fixture must overflow the head for this test to mean anything"
        path = write(tmp_path, "tileset.json", body)
        result = text.extract_text(path, make_ctx(tmp_path, "tileset.json"))
        assert result.file_class == "tiles3d" and result.attributes["sys_tiles3d"]["tileCount"] == 3
        # Above the parse bound the file stays text, truncated, with the reason recorded.
        monkeypatch.setattr(text, "JSON_PARSE_MAX_BYTES", 50)
        result = text.extract_text(path, make_ctx(tmp_path, "tileset.json"))
        assert result.file_class == "text" and result.attributes["sys_text"]["truncatedForAnalysis"] is True
        assert "parse bound" in result.warnings[0]

    def test_plain_json_stays_text(self, tmp_path):
        path = write(tmp_path, "config.json", b'{"name": "vams", "items": [1, 2, 3]}')
        result = text.extract_text(path, make_ctx(tmp_path, "config.json"))
        assert result.file_class == "text" and result.attributes["sys_text"]["kind"] == "json"
        # Three words cannot carry a language guess; the key is absent rather than "unknown".
        assert "language" not in result.attributes["sys_text"] and "language" not in result.facts
        assert result.warnings == []

    def test_invalid_json_is_a_warning(self, tmp_path):
        path = write(tmp_path, "broken.json", b'{"name": ')
        result = text.extract_text(path, make_ctx(tmp_path, "broken.json"))
        assert result.file_class == "text"
        assert "does not parse" in result.warnings[0]

    def test_binary_bytes_demote_to_other(self, tmp_path):
        path = write(tmp_path, "blob.txt", png_bytes(4, 4))
        result = text.extract_text(path, make_ctx(tmp_path, "blob.txt"))
        assert result.file_class == "other"
        assert result.render_skipped == RENDER_SKIPPED_UNSUPPORTED
        assert result.attributes == {}

    def test_large_file_streams_lines_and_flags_truncation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(text, "TEXT_HEAD_BYTES", 100)
        body = ("line of text\n" * 40).encode("utf-8")
        path = write(tmp_path, "big.log", body)
        sys_text = text.extract_text(path, make_ctx(tmp_path, "big.log")).attributes["sys_text"]
        assert sys_text["truncatedForAnalysis"] is True
        # 40 newline-terminated lines are 40 lines, the same count the non-truncated path would report.
        assert sys_text["lineCount"] == 40 and "chars" not in sys_text
        assert sys_text["sizeBytes"] == len(body)
        unterminated = write(tmp_path, "big-unterminated.log", body + b"tail")
        assert text.extract_text(unterminated, make_ctx(tmp_path, "big-unterminated.log")).attributes["sys_text"]["lineCount"] == 41

    def test_excerpt_respects_max_text_chars(self, tmp_path):
        path = write(tmp_path, "notes.txt", ENGLISH_TEXT.encode("utf-8"))
        result = text.extract_text(path, make_ctx(tmp_path, "notes.txt", max_text_chars=50))
        assert len(result.text_excerpt) <= 50


@pytest.mark.unit
class TestTiles3d:
    def test_is_tileset(self):
        assert tiles3d.is_tileset(tileset_dict()) is True
        assert tiles3d.is_tileset({"asset": {}, "root": {}}) is False
        assert tiles3d.is_tileset({"geometricError": 1}) is False
        assert tiles3d.is_tileset([1, 2]) is False

    def test_walk_tiles(self):
        walk = tiles3d.walk_tiles(tileset_dict()["root"])
        assert walk == {"tileCount": 3, "maxDepth": 2, "contentCount": 2, "externalTilesets": 1,
                        "contentFormats": {".b3dm": 1, ".json": 1}, "refine": ["ADD"], "walkCapped": False}
        assert tiles3d.walk_tiles({})["tileCount"] == 0

    def test_extract_tiles3d(self, tmp_path):
        result = tiles3d.extract_tiles3d(tileset_dict(), make_ctx(tmp_path, "tileset.json"))
        sys_tiles3d = result.attributes["sys_tiles3d"]
        assert result.file_class == "tiles3d"
        assert sys_tiles3d["specVersion"] == "1.1" and sys_tiles3d["tilesetVersion"] == "2026-09"
        assert sys_tiles3d["geometricError"] == 500 and sys_tiles3d["rootGeometricError"] == 100
        assert sys_tiles3d["boundingVolumeType"] == "region"
        assert sys_tiles3d["hasRootTransform"] is True
        assert sys_tiles3d["extensionsUsed"] == ["3DTILES_metadata"]
        assert sys_tiles3d["tileCount"] == 3 and sys_tiles3d["externalTilesets"] == 1
        assert sys_tiles3d["region"] == [-1.32, 0.69, -1.31, 0.70, 0, 88]
        assert all(isinstance(value, float) for value in sys_tiles3d["region"])
        assert result.facts["tiles"] == "3 tiles" and result.facts["geometricError"] == "500"
        assert result.facts["boundingVolume"] == "region" and result.facts["tilesFormat"] == "3D Tiles 1.1"
        summary = json.loads(result.text_excerpt)
        assert summary["asset"]["version"] == "1.1" and "boundingVolume" in summary["root"]
        assert result.render_images == [] and result.render_skipped is None and result.warnings == []

    def test_region_is_null_for_a_box_bounding_volume(self, tmp_path):
        tileset = tileset_dict()
        tileset["root"]["boundingVolume"] = {"box": [0, 0, 0, 100, 0, 0, 0, 100, 0, 0, 0, 10]}
        result = tiles3d.extract_tiles3d(tileset, make_ctx(tmp_path, "tileset.json"))
        sys_tiles3d = result.attributes["sys_tiles3d"]
        # The key is present with null: the promotion reads it as "no region", not as "missing".
        assert "region" in sys_tiles3d and sys_tiles3d["region"] is None
        assert sys_tiles3d["boundingVolumeType"] == "box" and result.warnings == []
        assert tiles3d.region_of({"sphere": [0, 0, 0, 1]}) == (None, None)

    def test_malformed_region_is_null_with_a_warning(self, tmp_path):
        tileset = tileset_dict()
        tileset["root"]["boundingVolume"] = {"region": [1, 2, 3]}
        result = tiles3d.extract_tiles3d(tileset, make_ctx(tmp_path, "tileset.json"))
        assert result.attributes["sys_tiles3d"]["region"] is None
        assert len(result.warnings) == 1 and "six finite numbers" in result.warnings[0]
        assert tiles3d.region_of({"region": [1, 2, 3, 4, 5, "x"]})[0] is None
        assert tiles3d.region_of({"region": [1, 2, 3, 4, 5, float("nan")]})[0] is None


@pytest.mark.unit
class TestFullTextCapture:
    def test_full_text_is_captured_when_asked(self, tmp_path):
        path = write(tmp_path, "notes.txt", (ENGLISH_TEXT * 3).encode("utf-8"))
        result = text.extract_text(path, make_ctx(tmp_path, "notes.txt", max_text_chars=50, capture_full_text=True))
        assert result.full_text == ENGLISH_TEXT * 3
        assert result.full_text_truncated is False and result.page_offsets == []
        assert len(result.text_excerpt) <= 50
        assert text.extract_text(path, make_ctx(tmp_path, "notes.txt")).full_text == ""

    def test_a_head_window_cut_flags_the_full_text(self, tmp_path, monkeypatch):
        monkeypatch.setattr(text, "TEXT_HEAD_BYTES", 100)
        path = write(tmp_path, "big.log", ("line of text\n" * 40).encode("utf-8"))
        result = text.extract_text(path, make_ctx(tmp_path, "big.log", capture_full_text=True))
        assert result.full_text_truncated is True and len(result.full_text) == 100
        monkeypatch.setattr(text, "CONTENT_TEXT_MAX_CHARS", 30)
        result = text.extract_text(path, make_ctx(tmp_path, "big.log", capture_full_text=True))
        assert result.full_text_truncated is True and len(result.full_text) == 30
