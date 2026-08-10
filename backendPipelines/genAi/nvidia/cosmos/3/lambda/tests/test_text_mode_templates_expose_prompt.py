#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests that every text-input cosmos3 template lets the operator supply the prompt.

openPipeline rejects a no-input-file run whose prompt is empty, and the prompt reaches it only through
the rendered configuration. So a text-mode template that neither declares a PROMPT tag nor references
{{PROMPT}} in its body is launchable only after someone sets COSMOS3_PROMPT on the asset first — the
execute screen renders no prompt field at all. Both halves are asserted because a declared tag the
body never references is silently dropped, and a referenced tag that is not declared never renders.

The prompt tags must stay OPTIONAL with no default: a blank value is what hands the run over to the
COSMOS3_PROMPT asset-metadata fallback (manifestHelper.resolve_input_setting)."""

import glob
import json
import os

import pytest

_SCHEMA_ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "vamsSchema"))

# Mirrors openPipeline.INPUT_FILE_MODES plus its super-image2video variant gate: any other mode takes
# no input file, so the prompt is the only input the run has.
_INPUT_FILE_MODES = ("image2video", "video2video", "transfer")
_INPUT_FILE_VARIANTS = ("super-image2video",)

_PROMPT_TAGS = ("PROMPT", "NEGATIVE_PROMPT")


def _templates():
    found = []
    for path in sorted(glob.glob(os.path.join(_SCHEMA_ROOT, "*", "templates", "*.json"))):
        with open(path, encoding="utf-8") as handle:
            found.append((path, json.load(handle)))
    return found


def _text_mode_templates():
    result = []
    for path, template in _templates():
        body = json.loads(template.get("configBody") or "{}")
        if body.get("TASK_MODE") in _INPUT_FILE_MODES:
            continue
        if body.get("MODEL_VARIANT") in _INPUT_FILE_VARIANTS:
            continue
        result.append((path, template, body))
    return result


@pytest.mark.unit
class TestTextModeTemplatesExposePrompt:
    def test_bundle_discovery_finds_the_shipped_templates(self):
        assert len(_templates()) >= 4, f"template discovery broke: {_SCHEMA_ROOT}"
        assert _text_mode_templates(), "no text-mode template found; the audit would be vacuous"

    def test_every_text_mode_template_declares_and_references_the_prompt_tags(self):
        offenders = []
        for path, template, body in _text_mode_templates():
            declared = {f.get("tagKey") for f in template.get("tagSchema") or []}
            config_body = template.get("configBody") or ""
            name = os.path.basename(path)
            for tag in _PROMPT_TAGS:
                if tag not in declared:
                    offenders.append(f"{name}: {tag} not declared in tagSchema")
                if "{{" + tag + "}}" not in config_body:
                    offenders.append(f"{name}: {{{{{tag}}}}} not referenced in configBody")
                if tag in body and body[tag] != "{{" + tag + "}}":
                    offenders.append(f"{name}: {tag} is hardcoded to {body[tag]!r}")

        assert not offenders, (
            "these text-mode templates cannot be given a prompt at execute time: " f"{offenders}")

    def test_prompt_tags_stay_optional_so_asset_metadata_can_supply_them(self):
        offenders = []
        for path, template, _body in _text_mode_templates():
            name = os.path.basename(path)
            for field in template.get("tagSchema") or []:
                if field.get("tagKey") not in _PROMPT_TAGS:
                    continue
                if field.get("required"):
                    offenders.append(f"{name}: {field['tagKey']} is required")
                if field.get("default") is not None:
                    offenders.append(f"{name}: {field['tagKey']} declares a default")
                if field.get("type") != "string":
                    offenders.append(f"{name}: {field['tagKey']} type is {field.get('type')!r}")

        assert not offenders, (
            "a required or defaulted prompt tag closes the COSMOS3_PROMPT metadata fallback: "
            f"{offenders}")
