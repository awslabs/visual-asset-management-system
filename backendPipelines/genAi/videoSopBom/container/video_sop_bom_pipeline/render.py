# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 13: every deliverable rendered from the merged/finalized data (no model calls, no AWS calls).
Each JSON deliverable that has a shipped schema is validated against it before it is written;
asset.metadata.json is assembled from typed key/value entries and written without a schema."""

import csv
import json
import os
import re

import jsonschema

from . import OUTPUT_FOLDER, load_schema
from .outputPathExtension import apply_output_path_extension
from .timeline import map_global_to_local
from .vocab import (
    BATTERY_TYPES,
    BOM_MD_COLUMNS,
    DISPLAY_TYPES,
    IC_PACKAGE_TYPES,
    IC_TYPES,
    LAB_SUMMARY_DEFAULT_EXISTING_BOM,
    LAB_SUMMARY_SECTIONS,
    LCA_BOM_COLUMNS,
    MATERIAL_TYPES,
    METHOD_FOR_WEIGHT,
    PART_TYPES,
    PCB_BOARD_FINISHES,
    PCB_TYPES,
    PRIMARY_TECHNIQUES,
    SECONDARY_TECHNIQUES,
    match_material_type,
    match_part_type,
    normalize_vocab_value,
)
from .windows import format_timestamp

BOM_MD_HEADER = "| " + " | ".join(BOM_MD_COLUMNS) + " |"
BOM_MD_SEPARATOR = "| " + " | ".join("---" for _ in BOM_MD_COLUMNS) + " |"
BOM_MD_INDENT = "&nbsp;" * 4
NUMERIC_METADATA_KEYS = ("sopBom_videoCount", "sopBom_totalDurationSeconds", "sopBom_componentCount", "sopBom_stepCount", "sopBom_totalMassG")
SUMMARY_MAX_BYTES = 51200
SLUG_MAX_CHARS = 24

# finalRow columns whose lcaRow type is an enum (or enum|null): each value is normalised against its
# vocabulary; a miss is recorded in vocabularyMisses and material_notes.
ENUM_COLUMNS = (
    ("part_type", PART_TYPES),
    ("material_or_component_type", MATERIAL_TYPES),
    ("primary_manufacturing_process", PRIMARY_TECHNIQUES),
    ("secondary_manufacturing_process", SECONDARY_TECHNIQUES),
    ("method_for_weight", METHOD_FOR_WEIGHT),
    ("ic_type", IC_TYPES),
    ("ic_package_type", IC_PACKAGE_TYPES),
    ("pcb_type", PCB_TYPES),
    ("pcb_board_finish", PCB_BOARD_FINISHES),
    ("display_type", DISPLAY_TYPES),
    ("battery_type", BATTERY_TYPES),
)
# The two ForeSIE-required enum columns: lcaRow types them anyOf [enum, null], so a miss is written as
# null; an empty model value in either is recorded as a miss too (the raw value goes to material_notes
# and analysis-report.vocabularyMisses).
REQUIRED_ENUM_COLUMNS = ("part_type", "material_or_component_type")
# finalRow columns copied as they come (their finalRow types already match lcaRow's).
COPIED_COLUMNS = ("material_composition", "mass_g_per_unit", "pcb_layers", "battery_capacity_wh")
# Leading characters a spreadsheet application reads as the start of a formula when it opens a CSV cell.
FORMULA_LEAD_CHARS = ("=", "+", "-", "@", "\t", "\r")


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")[:SLUG_MAX_CHARS].strip("-")
    return slug or "product"


def match_vocab(value, vocabulary):
    """The canonical entry of `vocabulary` for `value` under vocab's rule (NFKC/ASCII fold, trimmed,
    case-insensitive), else None. `match_part_type` / `match_material_type` are this rule pre-indexed."""
    key = normalize_vocab_value(value).lower()
    if not key:
        return None
    for entry in vocabulary:
        if normalize_vocab_value(entry).lower() == key:
            return entry
    return None


def _match(column, value, vocabulary):
    if column == "part_type":
        return match_part_type(value)
    if column == "material_or_component_type":
        return match_material_type(value)
    return match_vocab(value, vocabulary)


def _append_note(notes, addition):
    return f"{notes}; {addition}" if notes else addition


def _country(value):
    code = str(value or "").strip().upper()
    return code if re.fullmatch(r"[A-Z]{2}", code) else None


def build_bom_rows(final_rows, config, product_name):
    """One 66-column lcaRow per finalize row, valid against bom_row_schema.json: null for every blank
    cell, enum columns normalised through the vocabulary, the observable band columns carried over.
    Vocabulary misses are written as null, keep the raw value in material_notes and are returned for
    analysis-report.vocabularyMisses."""
    base = int(config.get("partLevelBase", "0"))
    slug = slugify(product_name)
    rows = []
    misses = []
    for sequence, source in enumerate(final_rows, start=1):
        notes = source.get("material_notes") or ""
        row = {column: None for column in LCA_BOM_COLUMNS}
        for column, vocabulary in ENUM_COLUMNS:
            raw = source.get(column)
            canonical = _match(column, raw, vocabulary) if raw not in (None, "") else None
            if canonical is None and (raw not in (None, "") or column in REQUIRED_ENUM_COLUMNS):
                misses.append({"row": sequence, "field": column, "value": "" if raw is None else str(raw)})
                notes = _append_note(notes, f"{column} (raw): {raw}")
            row[column] = canonical
        for column in COPIED_COLUMNS:
            row[column] = source.get(column)
        row.update({
            "part_level": int(source.get("part_level", 0)) + base,
            "lab_part_number": f"{slug}-{sequence:03d}",
            "manufacturer_part_number": source.get("manufacturer_part_number") or "",
            "alternative": "Yes" if str(source.get("alternative") or "").strip().lower() == "yes" else "No",
            "part_description": source.get("part_description") or "",
            "qty": source.get("qty", 1),
            "material_notes": notes or None,
            "manufacturing_country": _country(source.get("manufacturing_country")),
        })
        rows.append(row)
    return rows, misses


def _spreadsheet_safe(value):
    """`value` with a leading apostrophe when it is a str that begins with a formula-lead character, so a
    spreadsheet application shows the cell as text instead of evaluating it; any other value unchanged."""
    if isinstance(value, str) and value[:1] in FORMULA_LEAD_CHARS:
        return "'" + value
    return value


def write_bom_csv(path, rows):
    """Header row + data rows only; UTF-8 without BOM, LF line endings. Text data cells that begin with a
    formula-lead character carry a leading apostrophe; the header row and non-text cells are written as they are."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(LCA_BOM_COLUMNS)
        for row in rows:
            writer.writerow([_spreadsheet_safe("" if row.get(column) is None else row.get(column, "")) for column in LCA_BOM_COLUMNS])


def _md(value):
    return str("" if value is None else value).replace("|", "\\|")


def write_bom_md(path, rows, part_level_base=0):
    lines = [BOM_MD_HEADER, BOM_MD_SEPARATOR]
    for row in rows:
        level = int(row["part_level"])
        indent = BOM_MD_INDENT * max(0, level - part_level_base)
        lines.append("| {} | {} | {}{} | {} | {} | {} | {} |".format(
            level, _md(row["part_type"]), indent, _md(row["part_description"]), _md(row["qty"]),
            _md(row["material_or_component_type"]), _md(row["mass_g_per_unit"]), _md(row["primary_manufacturing_process"]),
        ))
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def build_sop(steps, final, product_name, product_name_from_narration, source_videos, timeline, frame_refs, bom_rows,
              title=None):
    """sop.json: deterministic title (`<product> — teardown SOP` unless `title` is given); per-step video
    mapping, frame_ref, bom_refs (lab_part_number) and the cross-window dependency edges appended to the
    within-window dependencies."""
    refs_by_step = {}
    for entry in final.get("step_bom_refs", []) or []:
        numbers = []
        for index in entry.get("row_indexes", []) or []:
            if 0 <= index < len(bom_rows):
                numbers.append(bom_rows[index]["lab_part_number"])
        refs_by_step[entry.get("step")] = numbers
    edges_by_step = {}
    for edge in final.get("dependency_edges", []) or []:
        edges_by_step.setdefault(edge.get("step"), []).append({"step": edge.get("depends_on_step"), "text": edge.get("text", "")})
    out_steps = []
    for step in steps:
        number = step["step"]
        video_index, local_t = map_global_to_local(timeline, float(step.get("timestamp_seconds", 0.0)))
        dependencies = list(step.get("dependencies", []) or [])
        for edge in edges_by_step.get(number, []):
            if edge not in dependencies:
                dependencies.append(edge)
        out_steps.append({
            "step": number,
            "action": step.get("action", ""),
            "component": step.get("component", ""),
            "fasteners": list(step.get("fasteners", []) or []),
            "locations": list(step.get("locations", []) or []),
            "dependencies": dependencies,
            "tools": list(step.get("tools", []) or []),
            "motion": step.get("motion") or {"allowed": [], "restricted": []},
            "force": step.get("force") or {"amount": None, "indicator": None},
            "failure_modes": list(step.get("failure_modes", []) or []),
            "notes": step.get("notes", ""),
            "timestamp_seconds": float(step.get("timestamp_seconds", 0.0)),
            "video_index": video_index,
            "local_timestamp_seconds": round(local_t, 3),
            "frame_ref": frame_refs.get(number),
            "bom_refs": refs_by_step.get(number, []),
        })
    return {
        "title": title or f"{product_name} — teardown SOP",
        "product_name": product_name,
        "product_name_from_narration": product_name_from_narration or "",
        "source_videos": list(source_videos),
        "summary": final.get("summary", ""),
        "safety_notes": list(final.get("safety_notes", []) or []),
        "steps": out_steps,
    }


def write_sop_md(path, sop):
    lines = [f"# {sop['title']}", "", f"Product: {sop['product_name']}"]
    if sop.get("product_name_from_narration"):
        lines.append(f"Product name from narration: {sop['product_name_from_narration']}")
    lines += ["", f"Source videos: {', '.join(sop.get('source_videos', []))}", "", "## Summary", "", sop.get("summary", ""), ""]
    if sop.get("safety_notes"):
        lines += ["## Safety notes", ""] + [f"- {note}" for note in sop["safety_notes"]] + [""]
    for step in sop.get("steps", []):
        lines += [f"## Step {step['step']} — {step['action']}: {step['component']}", ""]
        lines.append(f"- At [{format_timestamp(step['timestamp_seconds'])}] (video {step['video_index'] + 1}, {step['local_timestamp_seconds']:.1f}s)")
        for label, key in (("Fasteners", "fasteners"), ("Locations", "locations"), ("Tools", "tools"), ("Failure modes", "failure_modes"), ("BOM refs", "bom_refs")):
            if step.get(key):
                lines.append(f"- {label}: {', '.join(str(v) for v in step[key])}")
        if step.get("dependencies"):
            rendered = [f"step {d['step']}" if d.get("step") else d.get("text", "") for d in step["dependencies"]]
            lines.append(f"- Dependencies: {', '.join(rendered)}")
        motion = step.get("motion") or {}
        if motion.get("allowed") or motion.get("restricted"):
            lines.append(f"- Motion: allowed {', '.join(motion.get('allowed', [])) or '-'}; restricted {', '.join(motion.get('restricted', [])) or '-'}")
        force = step.get("force") or {}
        if force.get("amount") or force.get("indicator"):
            lines.append(f"- Force: {force.get('amount') or '-'} ({force.get('indicator') or 'no indicator'})")
        if step.get("notes"):
            lines.append(f"- Notes: {step['notes']}")
        if step.get("frame_ref"):
            lines.append(f"- Frame: {step['frame_ref']}")
        lines.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines).rstrip("\n") + "\n")


def compute_lab_summary(final_lab, rows, config, product_name, product_name_from_narration):
    """Sections 1.1-1.9; total mass, component count and the materials breakdown come from the BOM rows."""
    by_material = {}
    total = 0.0
    for row in rows:
        mass = row.get("mass_g_per_unit")
        if mass is None:
            continue
        contribution = float(mass) * float(row.get("qty") or 1)
        total += contribution
        material = row.get("material_or_component_type") or "unspecified"
        by_material[material] = by_material.get(material, 0.0) + contribution
    breakdown = [
        {"material": material, "mass_g": round(mass, 3), "percentage": round(100.0 * mass / total, 2) if total else 0.0}
        for material, mass in sorted(by_material.items(), key=lambda item: (-item[1], item[0]))
    ]
    lab = dict(final_lab)
    lab.update({
        "product_name": product_name,
        "product_name_from_narration": product_name_from_narration or "",
        "total_mass_g": round(total, 3),
        "component_count": len(rows),
        "materials_breakdown": breakdown,
        "contributors": config.get("contributors", "") or "",
    })
    return lab


def _prose(body):
    return body if isinstance(body, str) else "\n".join(f"- {item}" for item in body)


def _lab_section_body(number, lab):
    """The Markdown body of one Lab Summary.csv section (numbering per vocab.LAB_SUMMARY_SECTIONS)."""
    if number == "1.1":
        return lab.get("background", "")
    if number == "1.2":
        return lab.get("materials_methodology", "")
    if number == "1.3":
        return lab.get("safety_considerations", "")
    if number == "1.4":
        lines = [lab.get("product_description", "")]
        if lab.get("product_source_url"):
            lines.append(f"Sourced by: {lab['product_source_url']}")
        return "\n".join(lines)
    if number == "1.5":
        provided = "Provided by the supplier." if lab.get("existing_bom_provided") else LAB_SUMMARY_DEFAULT_EXISTING_BOM
        notes = lab.get("existing_bom_notes", "")
        return provided if not notes or notes == provided else f"{provided}\n{notes}"
    if number == "1.6":
        lines = [f"Total mass (g): {lab.get('total_mass_g', 0.0)}", f"Component count: {lab.get('component_count', 0)}"]
        if lab.get("materials_breakdown"):
            lines += ["", "| Material | Mass (g) | Share (%) |", "| --- | --- | --- |"]
            lines += [f"| {_md(entry['material'])} | {entry['mass_g']} | {entry['percentage']} |" for entry in lab["materials_breakdown"]]
        return "\n".join(lines)
    if number == "1.7":
        return lab.get("comparative_analysis", "")
    if number == "1.8":
        return lab.get("primary_manufacturing_processes", "")
    return _prose(lab.get("key_observations", ""))


def write_lab_summary_md(path, lab):
    """Sections 1.1-1.9 numbered and titled per vocab.LAB_SUMMARY_SECTIONS; 1.6 carries the computed totals table."""
    lines = [f"# Lab summary — {lab.get('product_name', '')}", ""]
    if lab.get("contributors"):
        lines += [f"Contributors: {lab['contributors']}", ""]
    for number, title in LAB_SUMMARY_SECTIONS:
        lines += [f"## {number} {title}", "", _lab_section_body(number, lab), ""]
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines).rstrip("\n") + "\n")


def write_json(path, obj, schema_name=None):
    """Write `obj`; when `schema_name` (a shipped schema stem) is given, validate against it first."""
    if schema_name:
        jsonschema.validate(obj, load_schema(schema_name))
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(obj, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_analysis_report(path, report):
    write_json(path, report, "analysis_report_schema")


def asset_path(definition, filename):
    """The asset-relative path a file lands at after the platform inserts the run leaf, with the leading
    slash asset paths carry (`/sop-bom/<executionId>/<filename>`, like inputFiles[].relativePath): the
    vendored helper drops a leading slash, so it is restored here — the form the frames `path`,
    sop `frame_ref` and summary `paths` patterns require."""
    extension = definition["outputTarget"]["fileBaseExecutionPathExtension"]
    return "/" + apply_output_path_extension(OUTPUT_FOLDER + filename, extension)


def write_asset_metadata(path, definition, values):
    entries = []
    for key, value in values.items():
        value_type = "number" if key in NUMERIC_METADATA_KEYS else "string"
        rendered = str(value) if value_type == "number" else ("" if value is None else str(value))
        entries.append({"metadataKey": key, "metadataValue": rendered, "metadataValueType": value_type})
    write_json(path, {"type": "metadata", "updateType": "update", "metadata": entries})


def write_summary(path, summary):
    jsonschema.validate(summary, load_schema("summary_schema"))
    encoded = json.dumps(summary, indent=2, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > SUMMARY_MAX_BYTES:
        raise ValueError(f"results summary is {len(encoded.encode('utf-8'))} bytes, above the {SUMMARY_MAX_BYTES}-byte results budget")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded + "\n")


# --- Profile artifacts -----------------------------------------------------------------------------------
# A workload profile names extra deliverables: each is the finalize output field `sourceField`, written as
# `<name>.json` and, when a renderer is named, as a readable `<name>.md`. The renderers take whatever shape
# the profile's prompt asked for, so they are generic: a list of objects becomes a table, an object becomes
# headed sections, and the step/summary shapes the shipped profiles use get a dedicated layout.

ARTIFACT_RENDERERS = ("table", "steps-timeline", "summary-card")
_STEP_LIST_KEYS = ("steps", "entries")
_STEP_NUMBER_KEYS = ("step_number", "step")
_STEP_LABEL_KEYS = ("action", "description", "activity", "title")
_TITLE_KEYS = ("title", "report_title")
_SUMMARY_KEYS = ("summary", "executive_summary")
_MAX_SECTION_DEPTH = 4


def _label(key):
    text = str(key).replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "Value"


def _inline(value):
    """One line of text: strings as written (line breaks folded), a list of plain values comma-separated,
    other scalars and containers as JSON."""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list) and all(not isinstance(item, (dict, list)) for item in value):
        return ", ".join(_inline(item) for item in value)
    return json.dumps(value, ensure_ascii=False)


def _cell(value):
    """A table cell: blank for a missing or null value, pipes escaped."""
    return "" if value is None else _inline(value).replace("|", "\\|")


def _paragraph(value):
    return value.strip() if isinstance(value, str) else _inline(value)


def _first(mapping, keys):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return key, value
    return None, None


def _table_lines(rows):
    if not rows:
        return ["_No entries._", ""]
    if all(isinstance(row, dict) for row in rows):
        columns = []
        for row in rows:
            columns.extend(key for key in row if key not in columns)
        lines = ["| " + " | ".join(_cell(_label(column)) for column in columns) + " |",
                 "| " + " | ".join("---" for _ in columns) + " |"]
        lines += ["| " + " | ".join(_cell(row.get(column)) for column in columns) + " |" for row in rows]
        return lines + [""]
    return [f"- {_inline(row)}" for row in rows] + [""]


def _section_lines(key, value, level, depth=1):
    """A scalar is one labelled line, a list a table or bullets, an object a headed subsection."""
    heading = "#" * min(level, 6) + " " + _label(key)
    if isinstance(value, dict):
        if depth >= _MAX_SECTION_DEPTH:
            return [heading, "", _inline(value), ""]
        lines = [heading, ""]
        for child, child_value in value.items():
            lines += _section_lines(child, child_value, level + 1, depth + 1)
        return lines
    if isinstance(value, list):
        return [heading, ""] + _table_lines(value)
    return [f"**{_label(key)}:** {_inline(value)}", ""]


def _table_md(name, value):
    lines = [f"# {_label(name)}", ""]
    if isinstance(value, list):
        return lines + _table_lines(value)
    for key, child in value.items():
        lines += _section_lines(key, child, 2)
    return lines


def _step_heading(index, item):
    number_key, number = _first(item, _STEP_NUMBER_KEYS)
    label_key, label = _first(item, _STEP_LABEL_KEYS)
    heading = f"## {number if number_key else index}. {_inline(label) if label_key else 'Entry'}"
    timestamp = item.get("timestamp_seconds")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        heading += f" [{format_timestamp(float(timestamp))}]"
    return heading, {number_key, label_key, "timestamp_seconds"}


def _timeline_md(name, value):
    data = {"steps": value} if isinstance(value, list) else value
    _, title = _first(data, _TITLE_KEYS)
    lines = [f"# {_inline(title) if title else _label(name)}", ""]
    summary_key, summary = _first(data, _SUMMARY_KEYS)
    if summary_key:
        lines += [_paragraph(summary), ""]
    list_key = next((key for key in _STEP_LIST_KEYS if isinstance(data.get(key), list)), None)
    for index, item in enumerate(data.get(list_key) or [], start=1):
        if not isinstance(item, dict):
            lines += [f"## {index}. {_inline(item)}", ""]
            continue
        heading, used = _step_heading(index, item)
        lines += [heading, ""]
        lines += [f"- {_label(key)}: {_inline(child)}" for key, child in item.items()
                  if key not in used and child not in (None, "", [], {})]
        lines.append("")
    for key, child in data.items():
        if key not in (list_key, summary_key) and key not in _TITLE_KEYS:
            lines += _section_lines(key, child, 2)
    return lines


def _card_md(name, value):
    if not isinstance(value, dict):
        return _table_md(name, value)
    title_key, title = _first(value, _TITLE_KEYS)
    lines = [f"# {_inline(title) if title_key else _label(name)}", ""]
    summary_key, summary = _first(value, _SUMMARY_KEYS)
    if summary_key:
        lines += [_paragraph(summary), ""]
    used = {title_key, summary_key}
    if isinstance(value.get("stats"), list):
        used.add("stats")
        lines += [f"- **{_inline(stat.get('label', ''))}:** {_inline(stat.get('value', ''))}"
                  for stat in value["stats"] if isinstance(stat, dict)] + [""]
    if isinstance(value.get("sections"), list):
        used.add("sections")
        for section in value["sections"]:
            if isinstance(section, dict):
                lines += [f"## {_inline(section.get('heading', ''))}", "", _paragraph(section.get("content", "")), ""]
    for key, child in value.items():
        if key not in used:
            lines += _section_lines(key, child, 2)
    return lines


_RENDERERS = {"table": _table_md, "steps-timeline": _timeline_md, "summary-card": _card_md}


def render_artifact_md(name, value, renderer):
    """The Markdown text of one artifact under `renderer` (one of ARTIFACT_RENDERERS)."""
    return "\n".join(_RENDERERS[renderer](name, value)).rstrip("\n") + "\n"


def write_artifact(files_dir, artifact, value):
    """`<name>.json` with the field as the model returned it, plus `<name>.md` when the artifact names a
    renderer. Returns the file names written."""
    name = artifact["name"]
    names = [f"{name}.json"]
    write_json(os.path.join(files_dir, f"{name}.json"), value)
    renderer = artifact.get("renderer")
    if renderer:
        with open(os.path.join(files_dir, f"{name}.md"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_artifact_md(name, value, renderer))
        names.append(f"{name}.md")
    return names
