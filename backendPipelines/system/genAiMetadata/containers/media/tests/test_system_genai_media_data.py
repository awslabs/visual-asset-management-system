#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tabular data: CSV columns, row counts, sample rows and inferred column types land in sys_data with the
sniffed delimiter; FCS parameters and event counts come from the TEXT segment without decoding the binary
DATA segment; bytes that are neither demote the file to `other`."""

import pytest

from media_extractors import data
from media_extractors.common import RENDER_SKIPPED_UNSUPPORTED
from system_genai_media_fixtures import csv_bytes, fcs_bytes, make_ctx, png_bytes, write


@pytest.mark.unit
class TestCsv:
    def test_header_rows_types_and_excerpt(self, tmp_path):
        path = write(tmp_path, "parts.csv", csv_bytes())
        result = data.extract_data(path, make_ctx(tmp_path, "parts.csv"))
        sys_data = result.attributes["sys_data"]
        assert result.file_class == "data"
        assert sys_data["format"] == "CSV" and sys_data["encoding"] == "utf_8"
        assert sys_data["delimiter"] == "," and sys_data["hasHeader"] is True
        assert sys_data["columns"] == ["name", "qty", "price"] and sys_data["columnCount"] == 3
        assert sys_data["rowCount"] == 2 and sys_data["rowCountExact"] is True
        assert sys_data["columnTypes"] == ["text", "number", "number"]
        assert sys_data["sampleRows"] == [["bolt", "10", "0.25"], ["nut", "20", "0.10"]]
        assert result.facts["columns"] == "3 columns" and result.facts["rows"] == "2 rows"
        assert result.text_excerpt == "name,qty,price\nbolt,10,0.25\nnut,20,0.10\n"
        assert result.render_images == [] and result.render_skipped is None

    def test_semicolon_delimiter_is_sniffed(self, tmp_path):
        path = write(tmp_path, "parts.csv", csv_bytes(delimiter=";"))
        sys_data = data.extract_data(path, make_ctx(tmp_path, "parts.csv")).attributes["sys_data"]
        assert sys_data["delimiter"] == ";" and sys_data["columns"] == ["name", "qty", "price"]

    def test_headerless_numeric_csv(self, tmp_path):
        path = write(tmp_path, "matrix.csv", b"1,2,3\n4,5,6\n7,8,9\n")
        sys_data = data.extract_data(path, make_ctx(tmp_path, "matrix.csv")).attributes["sys_data"]
        assert sys_data["hasHeader"] is False
        assert sys_data["columns"] == ["column1", "column2", "column3"]
        assert sys_data["rowCount"] == 3 and len(sys_data["sampleRows"]) == 3

    def test_sample_rows_are_capped(self, tmp_path):
        body = "a,b\n" + "".join(f"{index},{index * 2}\n" for index in range(50))
        path = write(tmp_path, "many.csv", body.encode())
        sys_data = data.extract_data(path, make_ctx(tmp_path, "many.csv")).attributes["sys_data"]
        assert sys_data["rowCount"] == 50 and len(sys_data["sampleRows"]) == data.DATA_SAMPLE_ROWS

    def test_row_count_cap_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(data, "DATA_MAX_ROWS_COUNTED", 10)
        body = "a,b\n" + "".join(f"{index},{index * 2}\n" for index in range(50))
        path = write(tmp_path, "many.csv", body.encode())
        result = data.extract_data(path, make_ctx(tmp_path, "many.csv"))
        assert result.attributes["sys_data"]["rowCount"] == 10
        assert result.attributes["sys_data"]["rowCountExact"] is False
        assert result.facts["rows"] == "10 rows (counting stopped)"

    def test_binary_demotes_to_other(self, tmp_path):
        path = write(tmp_path, "blob.csv", png_bytes(4, 4))
        result = data.extract_data(path, make_ctx(tmp_path, "blob.csv"))
        assert result.file_class == "other" and result.render_skipped == RENDER_SKIPPED_UNSUPPORTED

    def test_large_cell_within_the_field_limit_is_data(self, tmp_path):
        # 200,000 characters is above csv's 128 KiB default and below CSV_FIELD_SIZE_LIMIT.
        body = "id,blob\n1," + ("x" * 200_000) + "\n"
        path = write(tmp_path, "blobs.csv", body.encode())
        result = data.extract_data(path, make_ctx(tmp_path, "blobs.csv"))
        sys_data = result.attributes["sys_data"]
        assert result.file_class == "data" and sys_data["rowCount"] == 1 and sys_data["columns"] == ["id", "blob"]
        assert len(sys_data["sampleRows"][0][1]) == data.DATA_CELL_MAX_CHARS

    def test_cell_beyond_the_field_limit_demotes_to_other(self, tmp_path):
        body = "id,blob\n1," + ("x" * (data.CSV_FIELD_SIZE_LIMIT + 1)) + "\n"
        path = write(tmp_path, "huge.csv", body.encode())
        result = data.extract_data(path, make_ctx(tmp_path, "huge.csv"))
        assert result.file_class == "other" and result.render_skipped == RENDER_SKIPPED_UNSUPPORTED
        assert "CSV could not be parsed" in result.warnings[0] and result.attributes == {}

    def test_column_types(self):
        rows = [["x", "1", "", "2.5"], ["y", "2", "", "-3e2"]]
        assert data.column_types(rows, 4) == ["text", "number", "empty", "number"]


@pytest.mark.unit
class TestFcs:
    def test_parameters_and_events_from_the_text_segment(self, tmp_path):
        path = write(tmp_path, "sample.fcs", fcs_bytes())
        result = data.extract_data(path, make_ctx(tmp_path, "sample.fcs"))
        sys_data = result.attributes["sys_data"]
        assert result.file_class == "data"
        assert sys_data["format"] == "FCS" and sys_data["fcsVersion"] == "FCS3.0"
        assert sys_data["columns"] == ["FSC-A", "SSC-A", "FL1-A"] and sys_data["columnCount"] == 3
        assert sys_data["rowCount"] == 1234 and sys_data["rowCountExact"] is True
        assert sys_data["dataType"] == "F" and sys_data["mode"] == "L" and sys_data["byteOrder"] == "1,2,3,4"
        assert sys_data["sampleRows"] == []
        assert sys_data["keywords"] == {"$CYT": "ProtoCytometer"}
        assert result.facts["columns"] == "3 parameters" and result.facts["rows"] == "1,234 events"
        assert result.facts["cytometer"] == "ProtoCytometer"
        assert "parameters: FSC-A, SSC-A, FL1-A" in result.text_excerpt

    def test_parse_fcs_text_segment_keywords(self):
        keywords = data.parse_fcs_text_segment(fcs_bytes())
        assert keywords["$PAR"] == "3" and keywords["__version__"] == "FCS3.0"
        assert keywords["$P2N"] == "SSC-A" and keywords["$CYT"] == "ProtoCytometer"

    def test_truncated_or_foreign_bytes_demote_to_other(self, tmp_path):
        short = write(tmp_path, "short.fcs", b"FCS3.0" + b" " * 20)
        assert data.extract_data(short, make_ctx(tmp_path, "short.fcs")).file_class == "other"
        foreign = write(tmp_path, "foreign.fcs", png_bytes(4, 4))
        assert data.extract_data(foreign, make_ctx(tmp_path, "foreign.fcs")).file_class == "other"

    def test_bad_offsets_raise_in_the_parser(self):
        broken = bytearray(fcs_bytes())
        broken[18:26] = b"       5"  # TEXT end before TEXT start
        with pytest.raises(ValueError, match="offsets"):
            data.parse_fcs_text_segment(bytes(broken))
