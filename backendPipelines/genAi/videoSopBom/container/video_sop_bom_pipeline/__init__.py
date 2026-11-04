# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Container package for the Video SOP/BOM Extraction pipeline.

The contracts — JSON Schemas under `schemas/`, prompt files under `prompts/`, the vocabulary in
`vocab.py` — ship inside this package and are read through `load_schema` / `load_prompt`, so the
runtime and the unit tests validate the same bytes.
"""

import json
import os

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMAS_DIR = os.path.join(PACKAGE_DIR, "schemas")
PROMPTS_DIR = os.path.join(PACKAGE_DIR, "prompts")

SCHEMA_NAMES = (
    "config_schema",
    "definition_schema",
    "sop_schema",
    "bom_row_schema",
    "lab_summary_schema",
    "frames_schema",
    "timeline_schema",
    "analysis_report_schema",
    "summary_schema",
    "window_extraction_schema",
    "vision_schema",
    "finalize_schema",
)

PROMPT_NAMES = ("window_extraction", "vision_verification", "finalize", "system_boundary")

# The marker the container puts in its SendTaskSuccess payload; pipelineEnd succeeds on the external
# token only when `$.batchResult.reporter` equals it.
REPORTER = "video_sop_bom_pipeline"

# The one flat folder every deliverable is written under; the platform inserts the run leaf
# `/{{executionId}}/` before the file name.
OUTPUT_FOLDER = "sop-bom/"

# The `{{TOKEN}}` slots each prompt carries. The renderer substitutes every token of a prompt;
# `system_boundary` is sent verbatim as the Converse system prompt and has none.
PROMPT_TOKENS = {
    "window_extraction": (
        "WINDOW_INDEX", "WINDOW_COUNT", "WINDOW_START_HMS", "WINDOW_END_HMS",
        "PART_TYPES", "MATERIAL_TYPES", "MAX_KEY_FRAMES", "TRANSCRIPT",
        "ADDITIONAL_INSTRUCTIONS",
    ),
    "vision_verification": (
        "WINDOW_STEPS", "WINDOW_COMPONENTS", "FRAME_LIST", "ADDITIONAL_INSTRUCTIONS",
    ),
    "finalize": (
        "PRODUCT_NAME", "PART_LEVEL_BASE", "PART_TYPES_WITH_DESCRIPTIONS", "MATERIAL_TYPES",
        "PRIMARY_TECHNIQUES", "MERGED_STEPS", "MERGED_COMPONENTS", "VISION_RESULTS",
        "ADDITIONAL_INSTRUCTIONS",
    ),
    "system_boundary": (),
}


def load_schema(name):
    """The parsed JSON Schema `schemas/<name>.json`; `name` must be one of SCHEMA_NAMES."""
    if name not in SCHEMA_NAMES:
        raise KeyError(f"unknown schema {name!r}; expected one of {SCHEMA_NAMES}")
    with open(os.path.join(SCHEMAS_DIR, f"{name}.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_prompt(name):
    """The prompt text `prompts/<name>.md`; `name` must be one of PROMPT_NAMES."""
    if name not in PROMPT_NAMES:
        raise KeyError(f"unknown prompt {name!r}; expected one of {PROMPT_NAMES}")
    with open(os.path.join(PROMPTS_DIR, f"{name}.md"), "r", encoding="utf-8", newline=None) as fh:
        return fh.read()
