# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 13 renderers: the byte-exact bom.csv header, row order and escaping, vocabulary normalisation,
the computed lab summary, post-extension asset-metadata paths and the results summary.

Run from the container directory:  python -m pytest tests/test_video_sop_bom_render.py -q
"""

import csv
import json
import os
import sys

import jsonschema
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from conftest import make_definition  # noqa: E402
from video_sop_bom_pipeline import load_schema  # noqa: E402
from video_sop_bom_pipeline.vocab import LCA_BOM_COLUMNS  # noqa: E402

CONFIG = {
    "mode": "full", "languageCode": "en-US", "videoOrder": "selection", "productName": "", "contributors": "Ada Lovelace (ada@example.com)",
    "maxKeyFrames": 60, "partLevelBase": "0", "generateLabSummary": True, "additionalInstructions": "",
}
COL = LCA_BOM_COLUMNS.index  # column positions come from the contract constant, never from literals
BOM_SCHEMA = load_schema("bom_row_schema")


def _final_row(description="rear cover", part_type="Enclosure", material="PC/ABS", process="Molding - Plastics", mass=42.0, qty=1, level=1, country=None, **band):
    """A finalRow as finalize_schema.json defines it; `band` adds observable band columns."""
    row = {
        "part_level": level, "part_type": part_type, "part_description": description, "qty": qty,
        "material_or_component_type": material, "mass_g_per_unit": mass, "primary_manufacturing_process": process,
        "manufacturing_country": country, "alternative": "No", "manufacturer_part_number": "", "material_notes": None,
    }
    row.update(band)
    # The fixture is itself a valid finalRow, so no test starts from a shape the model cannot return.
    jsonschema.validate(row, {"$ref": "#/$defs/finalRow", "$defs": load_schema("finalize_schema")["$defs"]})
    return row


class TestBomCsv:
    def test_header_bytes_are_the_66_column_contract_utf8_no_bom_lf(self, tmp_path):
        from video_sop_bom_pipeline import render

        path = str(tmp_path / "bom.csv")
        rows, _ = render.build_bom_rows([_final_row()], CONFIG, "Cognex In-Sight 2800")
        render.write_bom_csv(path, rows)
        with open(path, "rb") as handle:
            data = handle.read()
        assert not data.startswith(b"\xef\xbb\xbf")
        assert b"\r" not in data
        first_line = data.split(b"\n", 1)[0] + b"\n"
        assert first_line == (",".join(LCA_BOM_COLUMNS) + "\n").encode("utf-8")
        assert len(LCA_BOM_COLUMNS) == 66

    def test_an_empty_bom_is_exactly_the_header_line(self, tmp_path):
        from video_sop_bom_pipeline import render

        path = str(tmp_path / "bom.csv")
        render.write_bom_csv(path, [])
        with open(path, "rb") as handle:
            data = handle.read()
        assert data.count(b"\n") == 1 and data.endswith(b"\n")

    def test_row_order_escaping_and_66_fields_per_row(self, tmp_path):
        from video_sop_bom_pipeline import render

        path = str(tmp_path / "bom.csv")
        rows, _ = render.build_bom_rows([
            _final_row(description='Bracket, "L" shaped'),
            _final_row(description="Main PCBA", part_type="PCBA", material="PCBA - Assembly", process="SMT", mass=12.5, qty=1, level=2),
        ], CONFIG, "Cognex In-Sight 2800")
        render.write_bom_csv(path, rows)
        with open(path, "r", encoding="utf-8", newline="") as handle:
            parsed = list(csv.reader(handle))
        assert len(parsed) == 3 and all(len(line) == 66 for line in parsed)
        assert parsed[1][COL("part_description")] == 'Bracket, "L" shaped'
        assert [line[COL("lab_part_number")] for line in parsed[1:]] == ["cognex-in-sight-2800-001", "cognex-in-sight-2800-002"]
        assert parsed[2][COL("part_type")] == "PCBA" and parsed[2][COL("mass_g_per_unit")] == "12.5"
        assert parsed[2][COL("primary_manufacturing_process")] == "SMT"
        assert parsed[1][COL("alternative")] == "No"
        # A null JSON cell is an empty CSV cell.
        assert parsed[1][COL("manufacturing_country")] == "" and parsed[1][COL("material_notes")] == ""

    @pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
    def test_a_text_cell_beginning_with_a_formula_lead_character_gets_a_leading_apostrophe(self, lead):
        from video_sop_bom_pipeline import render

        assert render._spreadsheet_safe(lead + "SUM(A1:A9)") == "'" + lead + "SUM(A1:A9)"
        assert render._spreadsheet_safe(lead) == "'" + lead
        assert lead in render.FORMULA_LEAD_CHARS and len(render.FORMULA_LEAD_CHARS) == 6

    def test_other_text_empty_and_non_text_cells_are_unchanged(self):
        from video_sop_bom_pipeline import render

        assert render._spreadsheet_safe("Rear cover") == "Rear cover"
        assert render._spreadsheet_safe("Bracket, 'L' shaped") == "Bracket, 'L' shaped"
        assert render._spreadsheet_safe("") == ""
        # Only the first character decides: a formula-lead character later in the text is left alone.
        assert render._spreadsheet_safe("Rated 5 V @ 2 A") == "Rated 5 V @ 2 A"
        assert render._spreadsheet_safe(4) == 4 and render._spreadsheet_safe(-12.5) == -12.5 and render._spreadsheet_safe(0) == 0
        assert render._spreadsheet_safe(None) is None

    def test_formula_lead_cells_round_trip_with_the_apostrophe_and_the_header_is_untouched(self, tmp_path):
        from video_sop_bom_pipeline import render

        leads = ["=", "+", "-", "@", "\t", "\r"]
        rows, _ = render.build_bom_rows(
            [_final_row(description=lead + "cmd|' /C calc'!A0") for lead in leads]
            + [_final_row(description="Rear cover"), dict(_final_row(), manufacturer_part_number="=MPN-1")],
            CONFIG, "P",
        )
        # A numeric value that reaches the writer as text (a negative one here) is a text cell like any other;
        # the same value as a number is not touched.
        rows.append(dict(rows[0], material_composition="-5", mass_g_per_unit=-5))
        path = str(tmp_path / "bom.csv")
        render.write_bom_csv(path, rows)
        with open(path, "rb") as handle:
            data = handle.read()
        assert data.split(b"\n", 1)[0] == ",".join(LCA_BOM_COLUMNS).encode("utf-8")
        with open(path, "r", encoding="utf-8", newline="") as handle:
            parsed = list(csv.reader(handle))
        assert parsed[0] == list(LCA_BOM_COLUMNS) and len(parsed) == 1 + len(rows) and all(len(line) == 66 for line in parsed)
        for line, lead in zip(parsed[1:], leads):
            assert line[COL("part_description")] == "'" + lead + "cmd|' /C calc'!A0"
            assert line[COL("part_type")] == "Enclosure" and line[COL("lab_part_number")].startswith("p-")
        assert parsed[7][COL("part_description")] == "Rear cover" and parsed[7][COL("mass_g_per_unit")] == "42.0"
        assert parsed[8][COL("manufacturer_part_number")] == "'=MPN-1" and parsed[8][COL("part_description")] == "rear cover"
        assert parsed[9][COL("material_composition")] == "'-5" and parsed[9][COL("mass_g_per_unit")] == "-5"
        assert parsed[9][COL("part_level")] == "1" and parsed[9][COL("qty")] == "1"
        # Empty (null) cells stay empty rather than becoming a lone apostrophe.
        assert parsed[1][COL("manufacturing_country")] == "" and parsed[1][COL("material_notes")] == ""


class TestBomRows:
    def test_vocabulary_is_normalised_case_insensitively_and_misses_are_null_with_the_raw_value_kept(self):
        from video_sop_bom_pipeline import render

        rows, misses = render.build_bom_rows([
            _final_row(part_type="enclosure", material="pc/abs", process="molding - plastics"),
            _final_row(description="mystery part", part_type="Unobtainium", material="Adamantium", process="Transmutation"),
        ], CONFIG, "P")
        assert (rows[0]["part_type"], rows[0]["material_or_component_type"], rows[0]["primary_manufacturing_process"]) == ("Enclosure", "PC/ABS", "Molding - Plastics")
        assert rows[0]["material_notes"] is None
        assert rows[1]["part_type"] is None and rows[1]["material_or_component_type"] is None
        assert rows[1]["primary_manufacturing_process"] is None
        assert "part_type (raw): Unobtainium" in rows[1]["material_notes"]
        assert "material_or_component_type (raw): Adamantium" in rows[1]["material_notes"]
        assert "primary_manufacturing_process (raw): Transmutation" in rows[1]["material_notes"]
        assert [(m["row"], m["field"], m["value"]) for m in misses] == [
            (2, "part_type", "Unobtainium"), (2, "material_or_component_type", "Adamantium"), (2, "primary_manufacturing_process", "Transmutation"),
        ]
        # null is valid in both anyOf [enum, null] columns, so the miss row validates too (see the schema test below).
        jsonschema.validate({"rows": rows}, BOM_SCHEMA)

    def test_part_level_base_country_and_alternative_defaults(self):
        from video_sop_bom_pipeline import render

        # The model is schema-bound to "XX" or null; the renderer still folds case and drops anything else.
        rows, _ = render.build_bom_rows([
            dict(_final_row(level=0), manufacturing_country="us"),
            dict(_final_row(level=1), manufacturing_country="USA"),
        ], dict(CONFIG, partLevelBase="1"), "P")
        assert [row["part_level"] for row in rows] == [1, 2]
        assert [row["manufacturing_country"] for row in rows] == ["US", None]
        assert rows[0]["alternative"] == "No"
        assert tuple(rows[0]) == LCA_BOM_COLUMNS

    def test_rows_validate_against_the_shipped_lca_row_schema_without_filtering(self):
        from video_sop_bom_pipeline import render

        rows, misses = render.build_bom_rows([
            _final_row(),
            dict(_final_row(description="mystery part", part_type="Unobtainium", material="Adamantium", process=None), manufacturing_country="China"),
        ], CONFIG, "P")
        assert len(misses) == 2 and rows[1]["manufacturing_country"] is None
        # The whole bom.json document, every key of every row, against the shipped bom_row_schema.json.
        jsonschema.validate({"product_name": "P", "part_level_base": CONFIG["partLevelBase"], "rows": rows}, BOM_SCHEMA)
        # Negative control: a "" blank (the pre-fix rendering) is not a valid null.
        broken = dict(rows[0], mass_g_per_unit="")
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"rows": [broken]}, BOM_SCHEMA)

    def test_observable_band_columns_are_copied_normalised_and_reach_the_csv(self, tmp_path):
        from video_sop_bom_pipeline import render

        rows, misses = render.build_bom_rows([_final_row(
            description="Main PCBA", part_type="PCBA", material="PCBA - Assembly", process="SMT", pcb_type="rigid", pcb_layers=4,
            secondary_manufacturing_process="smelting", method_for_weight="Measured", battery_type="Battery-Unobtainium",
            material_composition=55.5, battery_capacity_wh=None,
        )], CONFIG, "P")
        row = rows[0]
        assert row["pcb_type"] == "Rigid" and row["pcb_layers"] == 4 and row["material_composition"] == 55.5
        assert row["secondary_manufacturing_process"] == "Smelting" and row["method_for_weight"] == "Measured"
        assert row["battery_type"] is None and "battery_type (raw): Battery-Unobtainium" in row["material_notes"]
        assert misses == [{"row": 1, "field": "battery_type", "value": "Battery-Unobtainium"}]
        jsonschema.validate({"rows": rows}, BOM_SCHEMA)
        path = str(tmp_path / "bom.csv")
        render.write_bom_csv(path, rows)
        with open(path, "r", encoding="utf-8", newline="") as handle:
            parsed = list(csv.reader(handle))
        assert parsed[1][COL("pcb_layers")] == "4" and parsed[1][COL("secondary_manufacturing_process")] == "Smelting"
        assert parsed[1][COL("battery_type")] == "" and parsed[1][COL("material_composition")] == "55.5"

    def test_slugify(self):
        from video_sop_bom_pipeline import render

        assert render.slugify("Cognex In-Sight 2800") == "cognex-in-sight-2800"
        assert render.slugify("   ") == "product"
        assert len(render.slugify("x" * 80)) == 24


class TestMarkdown:
    def test_bom_md_has_the_7_columns_and_indents_by_level(self, tmp_path):
        from video_sop_bom_pipeline import render

        rows, _ = render.build_bom_rows([_final_row(level=0, description="Camera"), _final_row(level=1, description="Rear | cover")], CONFIG, "P")
        path = str(tmp_path / "bom.md")
        render.write_bom_md(path, rows)
        lines = open(path, encoding="utf-8").read().splitlines()
        assert lines[0] == "| Part Level | Part Type | Description | Qty | Material | Mass (g) | Manufacturing Process |"
        assert lines[1] == "| --- | --- | --- | --- | --- | --- | --- |"
        assert lines[2].startswith("| 0 | Enclosure | Camera |")
        assert lines[3].startswith("| 1 | Enclosure | &nbsp;&nbsp;&nbsp;&nbsp;Rear \\| cover |")

    def test_an_empty_bom_md_is_the_header_only(self, tmp_path):
        from video_sop_bom_pipeline import render

        path = str(tmp_path / "bom.md")
        render.write_bom_md(path, [])
        assert open(path, encoding="utf-8").read().splitlines() == [render.BOM_MD_HEADER, render.BOM_MD_SEPARATOR]
        from video_sop_bom_pipeline.vocab import BOM_MD_COLUMNS

        assert render.BOM_MD_HEADER == "| " + " | ".join(BOM_MD_COLUMNS) + " |"

    def test_sop_md_lists_every_step_with_the_ideal_steps_fields(self, tmp_path):
        from video_sop_bom_pipeline import render

        sop = {
            "title": "Cognex In-Sight 2800 — teardown SOP", "product_name": "Cognex In-Sight 2800", "product_name_from_narration": "",
            "source_videos": ["/part1.mp4"], "summary": "Short.", "safety_notes": ["Unplug first."],
            "steps": [{
                "step": 1, "action": "remove", "component": "rear cover", "fasteners": ["4x Phillips #00 screws"], "locations": ["rear"],
                "dependencies": [{"step": None, "text": "power off"}], "tools": ["PH00 driver"], "motion": {"allowed": ["lift"], "restricted": ["do not twist"]},
                "force": {"amount": "light", "indicator": None}, "failure_modes": ["cracked clip"], "notes": "", "timestamp_seconds": 75.0,
                "video_index": 1, "local_timestamp_seconds": 0.0, "frame_ref": "/sop-bom/exec-0001/keyframe-0000-00h01m15s.jpg", "bom_refs": ["p-001"],
            }],
        }
        path = str(tmp_path / "sop.md")
        render.write_sop_md(path, sop)
        text = open(path, encoding="utf-8").read()
        assert text.startswith("# Cognex In-Sight 2800 — teardown SOP\n")
        assert "## Step 1 — remove: rear cover" in text
        assert "4x Phillips #00 screws" in text and "do not twist" in text and "cracked clip" in text and "p-001" in text
        assert "[00:01:15]" in text and "video 2" in text


class TestLabSummary:
    def test_totals_and_breakdown_are_computed_from_the_bom_not_the_model(self):
        from video_sop_bom_pipeline import render

        rows, _ = render.build_bom_rows([
            _final_row(description="cover", material="PC/ABS", mass=10.0, qty=2),
            _final_row(description="bracket", part_type="Mechanical Hardware", material="Steel", process="Forming - Metalwork", mass=30.0, qty=1),
        ], CONFIG, "P")
        final_lab = {
            "product_description": "d", "product_source_url": None, "background": "b", "materials_methodology": "m",
            "safety_considerations": "s", "existing_bom_provided": False, "existing_bom_notes": "n", "primary_manufacturing_processes": "p",
            "key_observations": ["k"], "comparative_analysis": "c", "total_mass_g": 999.0, "component_count": 99,
        }
        lab = render.compute_lab_summary(final_lab, rows, CONFIG, "Cognex In-Sight 2800", "In-Sight")
        assert lab["product_name"] == "Cognex In-Sight 2800" and lab["product_name_from_narration"] == "In-Sight"
        assert lab["total_mass_g"] == 50.0 and lab["component_count"] == 2
        assert lab["materials_breakdown"] == [{"material": "Steel", "mass_g": 30.0, "percentage": 60.0}, {"material": "PC/ABS", "mass_g": 20.0, "percentage": 40.0}]
        assert lab["contributors"] == "Ada Lovelace (ada@example.com)"
        assert lab["background"] == "b"
        jsonschema.validate(lab, load_schema("lab_summary_schema"))

    def test_lab_summary_md_has_the_nine_schema_sections(self, tmp_path):
        from video_sop_bom_pipeline import render
        from video_sop_bom_pipeline.vocab import LAB_SUMMARY_SECTIONS

        lab = render.compute_lab_summary({
            "product_description": "A smart camera.", "product_source_url": "https://example.com/p", "background": "b", "materials_methodology": "m",
            "safety_considerations": "s", "existing_bom_provided": False, "existing_bom_notes": "n", "primary_manufacturing_processes": "p",
            "key_observations": ["first", "second"], "comparative_analysis": "c",
        }, [], CONFIG, "P", "")
        path = str(tmp_path / "lab-summary.md")
        render.write_lab_summary_md(path, lab)
        text = open(path, encoding="utf-8").read()
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        assert headings == [f"## {number} {title}" for number, title in LAB_SUMMARY_SECTIONS]
        assert "## 1.4 Product information" in text and "## 1.6 Verified BOM Data Summary" in text
        assert text.index("A smart camera.") > text.index("## 1.4 ") and "Sourced by: https://example.com/p" in text
        assert text.index("Total mass (g): 0.0") > text.index("## 1.6 ") and "Component count: 0" in text
        assert "- first\n- second" in text


class TestJsonOutputs:
    def test_write_json_validates_against_the_shipped_schema(self, tmp_path):
        from video_sop_bom_pipeline import render
        from video_sop_bom_pipeline.timeline import build_timeline

        timeline = build_timeline([{"video_key": "/part1.mp4", "duration": 75.0}])
        render.write_json(str(tmp_path / "video-timeline.json"), timeline, "timeline_schema")
        assert json.load(open(str(tmp_path / "video-timeline.json"), encoding="utf-8"))["total_duration"] == 75.0
        with pytest.raises(jsonschema.ValidationError):
            render.write_json(str(tmp_path / "bad.json"), {"video_keys": []}, "timeline_schema")
        with pytest.raises(KeyError):
            render.write_json(str(tmp_path / "bad.json"), timeline, "video_timeline.json")  # a file name is not a stem

    def test_build_sop_is_deterministic_and_validates(self):
        from video_sop_bom_pipeline import render
        from video_sop_bom_pipeline.timeline import build_timeline

        timeline = build_timeline([{"video_key": "/part1.mp4", "duration": 75.0}, {"video_key": "/part2.MP4", "duration": 80.0}])
        steps = [{
            "step": 1, "timestamp_seconds": 100.0, "action": "remove", "component": "cover", "tools": [], "fasteners": [], "locations": [],
            "dependencies": [{"step": None, "text": "power off"}], "motion": {"allowed": [], "restricted": []}, "force": {"amount": None, "indicator": None},
            "failure_modes": [], "notes": "", "source_window": 0,
        }, {
            "step": 2, "timestamp_seconds": 120.0, "action": "lift", "component": "board", "tools": [], "fasteners": [], "locations": [],
            "dependencies": [], "motion": {"allowed": [], "restricted": []}, "force": {"amount": None, "indicator": None}, "failure_modes": [], "notes": "", "source_window": 0,
        }]
        rows, _ = render.build_bom_rows([_final_row()], CONFIG, "Cognex In-Sight 2800")
        final = {"summary": "S", "safety_notes": ["N"], "step_bom_refs": [{"step": 1, "row_indexes": [0]}],
                 "dependency_edges": [{"step": 2, "depends_on_step": 1, "text": "cover off"}, {"step": 2, "depends_on_step": None, "text": "bench cleared"}]}
        sop = render.build_sop(steps, final, "Cognex In-Sight 2800", "In-Sight", ["/part1.mp4", "/part2.MP4"], timeline, {1: "/sop-bom/exec-0001/keyframe-0000-00h01m40s.jpg"}, rows)
        assert sop["title"] == "Cognex In-Sight 2800 — teardown SOP"
        assert sop["steps"][0]["video_index"] == 1 and sop["steps"][0]["local_timestamp_seconds"] == 25.0
        assert sop["steps"][0]["bom_refs"] == ["cognex-in-sight-2800-001"]
        assert sop["steps"][0]["frame_ref"] == "/sop-bom/exec-0001/keyframe-0000-00h01m40s.jpg" and sop["steps"][1]["frame_ref"] is None
        assert sop["steps"][1]["dependencies"] == [{"step": 1, "text": "cover off"}, {"step": None, "text": "bench cleared"}]
        assert "source_window" not in sop["steps"][0]
        jsonschema.validate(sop, load_schema("sop_schema"))

    def test_asset_metadata_uses_post_extension_paths_and_typed_values(self, tmp_path):
        from video_sop_bom_pipeline import render
        from video_sop_bom_pipeline.outputPathExtension import apply_output_path_extension

        definition = make_definition()
        # The helper drops a leading slash and asset_path restores it, so every asset path reads /sop-bom/<exec>/<file>
        # (the form the frames `path`, sop `frame_ref` and summary `paths` patterns require).
        assert render.asset_path(definition, "sop.json") == "/sop-bom/exec-0001/sop.json" == "/" + apply_output_path_extension("sop-bom/sop.json", "/exec-0001/")
        path = str(tmp_path / "asset.metadata.json")
        render.write_asset_metadata(path, definition, {
            "sopBom_latestExecutionId": "exec-0001", "sopBom_mode": "full", "sopBom_videoCount": 2, "sopBom_totalDurationSeconds": 155.0,
            "sopBom_language": "en-US", "sopBom_transcriptPath": render.asset_path(definition, "transcript.json"),
            "sopBom_productName": "Cognex", "sopBom_componentCount": 1, "sopBom_stepCount": 2, "sopBom_totalMassG": 42.0,
            "sopBom_sopPath": render.asset_path(definition, "sop.json"), "sopBom_bomPath": render.asset_path(definition, "bom.csv"),
            "sopBom_labSummaryPath": render.asset_path(definition, "lab-summary.json"),
        })
        document = json.load(open(path, encoding="utf-8"))
        assert document["type"] == "metadata" and document["updateType"] == "update"
        entries = {entry["metadataKey"]: entry for entry in document["metadata"]}
        assert entries["sopBom_transcriptPath"] == {"metadataKey": "sopBom_transcriptPath", "metadataValue": "/sop-bom/exec-0001/transcript.json", "metadataValueType": "string"}
        assert entries["sopBom_videoCount"] == {"metadataKey": "sopBom_videoCount", "metadataValue": "2", "metadataValueType": "number"}
        assert entries["sopBom_totalMassG"]["metadataValueType"] == "number"
        assert len(entries) == 13

    def test_summary_is_validated_written_and_bounded(self, tmp_path):
        from video_sop_bom_pipeline import render

        definition = make_definition()
        summary = {
            "mode": "full", "status": "SUCCEEDED", "fileCount": 16,
            "bedrock": {"calls": 3, "inputTokens": 10, "outputTokens": 5},
            "limits": {"configured": {k: v for k, v in definition["limits"].items() if k != "maxKeyFramesCeiling"},
                       "observed": {"totalInputBytes": 1024, "totalDurationSeconds": 155.0, "language": "en-US", "subtitles": []}},
            "timings": {"stages": [{"name": "transcribe", "durationS": 1.5}], "elapsedS": 2.0},
            "warnings": [],
            "paths": {name: render.asset_path(definition, file) for name, file in
                      (("transcript", "transcript.json"), ("timeline", "video-timeline.json"), ("analysisReport", "analysis-report.json"))},
            "config": CONFIG,
        }
        path = str(tmp_path / "summary.json")
        render.write_summary(path, summary)
        assert json.load(open(path, encoding="utf-8"))["bedrock"]["calls"] == 3
        assert render.SUMMARY_MAX_BYTES == 51200
        # The shipped summary_schema.json is enforced before the size bound: a partial document is rejected.
        with pytest.raises(jsonschema.ValidationError):
            render.write_summary(path, {"mode": "full", "status": "SUCCEEDED", "bedrock": summary["bedrock"]})
        with pytest.raises(ValueError):
            render.write_summary(path, dict(summary, warnings=["x" * 60000]))
