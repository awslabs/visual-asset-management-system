#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A workbook's read is bounded in time and memory, whatever the size of its shared-strings part (D113).

openpyxl's read-only reader loads a workbook's whole shared-strings table (every distinct text value) into memory
before the first row is read, so a workbook of tens of millions of strings ran the MEDIA function out of time. The
extractor now streams the table only as far as the rows it reads need, takes a sheet's row count from its declared
dimension (a scan bounded by XLSX_MAX_ROWS_COUNTED when there is none), reads a sheet only as far as its sample,
count and text need, and does not read a shared-strings part over XLSX_SHARED_STRINGS_MAX_BYTES at all (its size
comes from the ZIP directory): the sheet is then described without its text cells, and sys_data says so.

The large parts here are built by hand: a small workbook's sharedStrings.xml with filler strings appended (the
strings its cells reference come first, as a writer stores them), and a generated sheet of numeric rows.
"""

import io
import time
import tracemalloc
import zipfile
from xml.sax.saxutils import escape

import pytest
from openpyxl.worksheet import _read_only
from openpyxl.worksheet._reader import WorkSheetParser

from media_extractors import office
from media_extractors.common import DATA_SAMPLE_ROWS
from system_genai_media_fixtures import make_ctx, write, xlsx_bytes

_STRINGS_PART = "xl/sharedStrings.xml"
_SHEET_PART = "xl/worksheets/sheet1.xml"
_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
# Bounds the extraction must stay inside, whatever the part's size: peak Python allocation and wall time.
_PEAK_BYTES_BOUND = 32 * 1024 * 1024
_SECONDS_BOUND = 5.0
# The shared-strings read bound, pinned: a part over it is not read.
_STRINGS_CAP = 64 * 1024 * 1024

_PARTS_ROWS = [["name", "qty", "price", "site"]] + [
    [f"part-{index:02d}", index * 10, round(index * 0.25, 2), "north" if index % 2 else "south"]
    for index in range(1, 13)]
_SHEETS = (("Parts", _PARTS_ROWS), ("Q1", [["month", "total"], ["Jan", 100], ["Feb", 140]]))


def _rewritten(data, replacements):
    """A copy of the package with the named members replaced or added, deflated at the fastest level."""
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as target:
        for info in source.infolist():
            body = replacements.get(info.filename)
            target.writestr(info.filename, source.read(info.filename) if body is None else body)
        for name, body in replacements.items():
            if name not in source.NameToInfo:
                target.writestr(name, body)
    return out.getvalue()


def _shared_strings_workbook(sheets=_SHEETS):
    """The workbook xlsx_bytes writes (openpyxl stores text inline), rewritten the way Excel stores it: every text
    cell a `t="s"` index into one shared-strings part, the strings in order of first use, each sheet declaring its
    dimension."""
    data = xlsx_bytes(sheets=sheets)
    strings, index = [], {}
    replacements = {}
    for number, (_name, rows) in enumerate(sheets, start=1):
        width = max(len(row) for row in rows)
        body = []
        for row_number, row in enumerate(rows, start=1):
            cells = []
            for column, value in enumerate(row):
                ref = f"{'ABCDEFGH'[column]}{row_number}"
                if isinstance(value, str):
                    if value not in index:
                        index[value] = len(strings)
                        strings.append(value)
                    cells.append(f'<c r="{ref}" t="s"><v>{index[value]}</v></c>')
                elif value is not None:
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            body.append(f'<row r="{row_number}">{"".join(cells)}</row>')
        replacements[f"xl/worksheets/sheet{number}.xml"] = (
            f'<worksheet xmlns="{_MAIN_NS}"><dimension ref="A1:{"ABCDEFGH"[width - 1]}{len(rows)}"/>'
            f'<sheetData>{"".join(body)}</sheetData></worksheet>').encode("utf-8")
    replacements[_STRINGS_PART] = (
        f'<sst xmlns="{_MAIN_NS}" count="{len(strings)}" uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{escape(text)}</t></si>" for text in strings) + "</sst>").encode("utf-8")
    package = zipfile.ZipFile(io.BytesIO(data))
    replacements["[Content_Types].xml"] = package.read("[Content_Types].xml").replace(
        b"</Types>", b'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-'
                     b'officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
    replacements["xl/_rels/workbook.xml.rels"] = package.read("xl/_rels/workbook.xml.rels").replace(
        b"</Relationships>", b'<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                             b'relationships/sharedStrings" Target="sharedStrings.xml" Id="rId9"/></Relationships>')
    return _rewritten(data, replacements)


def _padded(data, filler_count, filler="<si><t>x</t></si>"):
    """The package with ``filler_count`` unreferenced strings appended to its shared-strings part."""
    part = zipfile.ZipFile(io.BytesIO(data)).read(_STRINGS_PART)
    head, _, tail = part.decode("utf-8").rpartition("</sst>")
    return _rewritten(data, {_STRINGS_PART: (head + filler * filler_count + "</sst>" + tail).encode("utf-8")})


def _strings_part_size(data):
    return zipfile.ZipFile(io.BytesIO(data)).getinfo(_STRINGS_PART).file_size


def _numeric_sheet(rows, dimension=True):
    """A first sheet of ``rows`` data rows below a header of inline strings, two numeric columns per row."""
    head = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="{_MAIN_NS}">'
            + (f'<dimension ref="A1:B{rows + 1}"/>' if dimension else "") + "<sheetData>"
            + '<row r="1"><c r="A1" t="inlineStr"><is><t>reading</t></is></c>'
              '<c r="B1" t="inlineStr"><is><t>value</t></is></c></row>')
    body = "".join(f'<row r="{index}"><c r="A{index}"><v>{index - 1}</v></c><c r="B{index}"><v>{index * 0.5}</v></c>'
                   f"</row>" for index in range(2, rows + 2))
    return (head + body + "</sheetData></worksheet>").encode("utf-8")


def _extract(tmp_path, name, data, **ctx):
    path = write(tmp_path, name, data)
    return office.extract_xlsx(path, make_ctx(tmp_path, name, **ctx))


def _bounded_extract(tmp_path, name, data, **ctx):
    """The result, the peak bytes allocated and the seconds taken by one extraction."""
    path = write(tmp_path, name, data)
    context = make_ctx(tmp_path, name, **ctx)
    tracemalloc.start()
    started = time.monotonic()
    try:
        result = office.extract_xlsx(path, context)
        elapsed = time.monotonic() - started
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return result, peak, elapsed


def _comparable(result):
    return (result.attributes, result.text_excerpt, result.facts, result.full_text, result.page_offsets,
            result.full_text_truncated)


@pytest.fixture
def parsed_rows(monkeypatch):
    """Counts the sheet rows openpyxl's read-only sheets parse."""
    counter = {"rows": 0}

    class CountingParser(WorkSheetParser):
        def parse(self):
            for item in super().parse():
                counter["rows"] += 1
                yield item

    monkeypatch.setattr(_read_only, "WorkSheetParser", CountingParser)
    return counter


@pytest.mark.unit
class TestSharedStringsAreStreamed:
    @pytest.mark.parametrize("capture_full_text", [False, True], ids=["sample", "full-text"])
    def test_a_large_shared_strings_part_is_read_only_as_far_as_the_rows_need(self, tmp_path, capture_full_text):
        small = _shared_strings_workbook()
        large = _padded(small, 2_000_000)
        assert 32 * 1024 * 1024 < _strings_part_size(large) <= _STRINGS_CAP
        expected = _extract(tmp_path, "small.xlsx", small, capture_full_text=capture_full_text)
        result, peak, elapsed = _bounded_extract(tmp_path, "large.xlsx", large, capture_full_text=capture_full_text)
        assert peak < _PEAK_BYTES_BOUND, f"peak {peak:,} bytes"
        assert elapsed < _SECONDS_BOUND, f"{elapsed:.1f} s"
        assert _comparable(result) == _comparable(expected)
        sys_data = result.attributes["sys_data"]
        assert sys_data["columns"] == ["name", "qty", "price", "site"] and sys_data["rowCount"] == 12
        assert sys_data["sampleRows"][0] == ["part-01", "10", "0.25", "north"] and "stringValues" not in sys_data
        assert result.warnings == []

    def test_strings_are_read_in_order_and_repeated_values_resolve(self, tmp_path):
        result = _extract(tmp_path, "parts.xlsx", _shared_strings_workbook())
        sample = result.attributes["sys_data"]["sampleRows"]
        assert [row[3] for row in sample] == ["north", "south", "north", "south", "north"]
        assert len(sample) == DATA_SAMPLE_ROWS


@pytest.mark.unit
class TestSharedStringsOverTheCap:
    def test_the_cap_is_fixed(self):
        assert office.XLSX_SHARED_STRINGS_MAX_BYTES == _STRINGS_CAP

    def test_a_part_over_the_cap_is_not_read_and_sys_data_says_so(self, tmp_path):
        small = _shared_strings_workbook()
        large = _padded(small, 4_000_000)
        assert _strings_part_size(large) > _STRINGS_CAP
        expected = _extract(tmp_path, "small.xlsx", small).attributes["sys_data"]
        result, peak, elapsed = _bounded_extract(tmp_path, "large.xlsx", large)
        assert peak < _PEAK_BYTES_BOUND, f"peak {peak:,} bytes"
        assert elapsed < _SECONDS_BOUND, f"{elapsed:.1f} s"
        sys_data = result.attributes["sys_data"]
        assert sys_data["stringValues"] == "skipped"
        assert f"{_strings_part_size(large):,} bytes" in sys_data["stringValuesNote"]
        # The counts stand; text cells are blank and numbers keep their values.
        for key in ("sheetCount", "sheetNames", "columnCount", "rowCount", "rowCountExact"):
            assert sys_data[key] == expected[key], key
        assert sys_data["columns"] == ["", "", "", ""]
        assert sys_data["sampleRows"] == [["", row[1], row[2], ""] for row in expected["sampleRows"]]
        assert any("text cell values not read" in warning for warning in result.warnings)

    def test_the_cap_is_read_from_the_zip_directory(self, tmp_path, monkeypatch):
        data = _shared_strings_workbook()
        monkeypatch.setattr(office, "XLSX_SHARED_STRINGS_MAX_BYTES", _strings_part_size(data) - 1)
        sys_data = _extract(tmp_path, "parts.xlsx", data, capture_full_text=True).attributes["sys_data"]
        assert sys_data["stringValues"] == "skipped" and sys_data["rowCount"] == 12
        monkeypatch.setattr(office, "XLSX_SHARED_STRINGS_MAX_BYTES", _strings_part_size(data))
        sys_data = _extract(tmp_path, "parts.xlsx", data).attributes["sys_data"]
        assert "stringValues" not in sys_data and sys_data["columns"][0] == "name"

    def test_a_sheet_of_inline_strings_and_numbers_reads_as_before(self, tmp_path):
        data = _rewritten(_shared_strings_workbook(), {_SHEET_PART: _numeric_sheet(8)})
        sys_data = _extract(tmp_path, "readings.xlsx", data).attributes["sys_data"]
        assert sys_data["columns"] == ["reading", "value"] and sys_data["rowCount"] == 8
        assert sys_data["sampleRows"][0] == ["1", "1.0"]


@pytest.mark.unit
class TestRowCounts:
    def test_a_declared_dimension_gives_the_count_without_a_scan(self, tmp_path, parsed_rows):
        data = _rewritten(_shared_strings_workbook(), {_SHEET_PART: _numeric_sheet(300_000)})
        result, _peak, elapsed = _bounded_extract(tmp_path, "readings.xlsx", data)
        sys_data = result.attributes["sys_data"]
        assert sys_data["rowCount"] == office.XLSX_MAX_ROWS_COUNTED and sys_data["rowCountExact"] is False
        assert sys_data["sampleRows"] == [[str(index), str((index + 1) * 0.5)] for index in range(1, 6)]
        assert result.facts["rows"] == "200,000 rows (counting stopped)"
        # The header, the sample and the second sheet's rows: no scan to the counting bound.
        assert parsed_rows["rows"] <= 2 * (DATA_SAMPLE_ROWS + 1), parsed_rows
        assert elapsed < _SECONDS_BOUND, f"{elapsed:.1f} s"

    def test_a_declared_count_within_the_bound_is_exact(self, tmp_path, parsed_rows):
        data = _rewritten(_shared_strings_workbook(), {_SHEET_PART: _numeric_sheet(40_000)})
        sys_data = _extract(tmp_path, "readings.xlsx", data).attributes["sys_data"]
        assert sys_data["rowCount"] == 40_000 and sys_data["rowCountExact"] is True
        assert parsed_rows["rows"] <= 2 * (DATA_SAMPLE_ROWS + 1), parsed_rows

    @pytest.mark.parametrize("rows, counted, exact", [(1_500, 1_000, False), (700, 700, True)])
    def test_without_a_dimension_the_scan_stops_at_the_bound(self, tmp_path, monkeypatch, parsed_rows, rows,
                                                             counted, exact):
        monkeypatch.setattr(office, "XLSX_MAX_ROWS_COUNTED", 1_000)
        data = _rewritten(_shared_strings_workbook(), {_SHEET_PART: _numeric_sheet(rows, dimension=False)})
        sys_data = _extract(tmp_path, "readings.xlsx", data).attributes["sys_data"]
        assert sys_data["rowCount"] == counted and sys_data["rowCountExact"] is exact
        assert sys_data["sampleRows"][0] == ["1", "1.0"]
        # The scan reads one row past the bound to know the count stopped there, then ends.
        assert parsed_rows["rows"] <= min(rows, 1_000) + 2 + 3, parsed_rows
