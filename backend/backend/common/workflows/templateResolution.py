# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Execute-time template resolution.

Pure module (no AWS/env deps) implementing the per-pipeline template-resolution 5-case contract. It
turns a pipeline's execute-time parameters (templateId + templateTags, or a customTemplateOverride)
into the config body that gets written as the per-pipeline config S3 file and rendered per task at
launch.

Two tag layers share the {{tagName}} syntax:
  - USER tags: declared in the template tag schema (or free-form for an override), filled from the
    caller's templateTags. Resolved HERE.
  - SYSTEM tags: the built-in tags (common/workflows/templateTags.SYSTEM_TAG_NAMES + the metadata_
    prefix) that the renderer (common/workflows/templateRender) resolves per pipeline task against
    the manifest + execution context at launch. Left in place HERE.

So resolution substitutes only the user tags and leaves system tags as {{...}} placeholders; an
"unmatched" {{tag}} — one that is neither a filled user tag nor a reserved system tag — is an error
(the Q1 contract: extra provided tags are ignored; an unmatched body tag errors).
"""

import json
import re

from common.workflows.templateTags import is_reserved_tag_key
from common.workflows import templateTagSchema as tts

# Same tag pattern as templateRender: {{ tagName }} with tolerated inner whitespace.
_TAG_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


class TemplateResolutionError(Exception):
    """Raised when a pipeline's execute-time parameters cannot be resolved to a config body."""

    def __init__(self, errors):
        self.errors = errors if isinstance(errors, list) else [errors]
        super().__init__("; ".join(self.errors))


def _filled_from_raw_tags(provided_tags):
    """Build a {key: value} map from caller-provided [{key, value}] tags with NO schema (the
    override-without-template case). Reserved system tag keys are rejected (comment 5d); every other
    provided value is taken as-is. Returns (errors, filled)."""
    errors = []
    filled = {}
    for entry in provided_tags or []:
        if not isinstance(entry, dict) or "key" not in entry:
            continue
        key = entry["key"]
        if is_reserved_tag_key(key):
            errors.append(f"tag '{key}' is a reserved system tag and cannot be supplied by the caller")
            continue
        filled[key] = entry.get("value")
    return errors, filled


def _substitute_user_tags(text, filled_tags):
    """Replace {{tag}} occurrences of the provided/declared USER tags in text, leaving reserved
    system tags in place. Returns (rendered_text, errors). A {{tag}} that is neither a filled user
    tag nor a reserved system tag is an unmatched-tag error.

    A string tag value is substituted JSON-string-escaped without surrounding quotes (so it sits
    inside the template's own quotes, matching templateRender's scalar kind); a non-string value
    (int/float/bool/list) is substituted as a JSON literal (no surrounding quotes)."""
    if not text:
        return text or "", []
    errors = []
    found = set(_TAG_PATTERN.findall(text))
    for name in sorted(found):
        if name in filled_tags:
            continue
        if is_reserved_tag_key(name):
            continue  # a system tag — resolved later by templateRender
        errors.append(f"unmatched template tag '{{{{{name}}}}}' has no provided value")
    if errors:
        return text, errors

    def _replace(match):
        name = match.group(1)
        if name not in filled_tags:
            return match.group(0)  # system tag: leave for the launch-time renderer
        value = filled_tags[name]
        if isinstance(value, str):
            return json.dumps(value)[1:-1]  # scalar: escaped, quotes stripped
        return json.dumps(value)            # json literal (list/number/bool)

    return _TAG_PATTERN.sub(_replace, text), []


def resolve_pipeline_config(pipeline_system_config, template_row, tag_schema_fields, params):
    """Resolve one pipeline's execute-time parameters to a rendered config body.

    Args:
      pipeline_system_config: the pipeline's systemConfig (reads requireTemplate,
        allowCustomTemplateOverride).
      template_row: {templateId, configBody, configFormat, allowCustomEdit} for the chosen template,
        or None when no templateId was supplied.
      tag_schema_fields: the template's declared tag-schema fields list (or None).
      params: {templateId?, templateTags:[{key,value}], customTemplateOverride?} for this pipeline.

    Returns (errors, result_dict) where result_dict is
      {templateId, renderedConfig, templateTags(filled), customTemplateOverrideUsed, configFormat}.
    Errors is a list (empty = resolved).
    """
    psc = pipeline_system_config or {}
    params = params or {}
    require_template = bool(psc.get("requireTemplate"))
    allow_override = bool(psc.get("allowCustomTemplateOverride"))

    template_id = params.get("templateId")
    override = params.get("customTemplateOverride")
    provided_tags = params.get("templateTags") or []

    # ---- Case selection ----
    if template_id:
        if template_row is None:
            return [f"template '{template_id}' not found for this pipeline"], None
        config_format = template_row.get("configFormat", "json")
        # A run may customize a template-backed config when EITHER the pipeline allows a custom
        # template override OR the chosen template itself allows custom edit (allowCustomEdit). The
        # web presents both as one "Customize configuration" toggle; the backend accepts an edited
        # body under either grant.
        allow_template_edit = bool(template_row.get("allowCustomEdit"))
        # Cases 1 & 2: validate tags against the schema; render override body (case 2) or stored body.
        if override is not None:
            if not (allow_override or allow_template_edit):
                return ["this pipeline does not allow a custom template override"], None
            body = override
            override_used = True
        else:
            body = template_row.get("configBody", "")
            override_used = False
        errors, filled = tts.validate_tags(tag_schema_fields or [], provided_tags)
        if errors:
            return errors, None
        rendered, render_errors = _substitute_user_tags(body, filled)
        if render_errors:
            return render_errors, None
        return [], {
            "templateId": template_id, "renderedConfig": rendered, "templateTags": provided_tags,
            "customTemplateOverrideUsed": override_used, "configFormat": config_format,
        }

    if override is not None:
        # Case 3: override, no templateId. Requires allowCustomTemplateOverride AND not requireTemplate.
        if not allow_override:
            return ["this pipeline does not allow a custom template override"], None
        if require_template:
            return ["this pipeline requires a template; a template-less override is not allowed"], None
        # No schema: take provided tags AS-IS (reserved keys still rejected). Every {{tag}} in the
        # override must have a provided value (or be a system tag) — enforced by _substitute_user_tags.
        errors, filled = _filled_from_raw_tags(provided_tags)
        if errors:
            return errors, None
        rendered, render_errors = _substitute_user_tags(override, filled)
        if render_errors:
            return render_errors, None
        return [], {
            "templateId": "", "renderedConfig": rendered, "templateTags": provided_tags,
            "customTemplateOverrideUsed": True, "configFormat": "raw",
        }

    # Case 4: no template, no override. Only allowed when the pipeline does not requireTemplate.
    if require_template:
        return ["this pipeline requires a template (templateId) for execution"], None
    # Only system/execution variables apply; no user config body.
    return [], {
        "templateId": "", "renderedConfig": "", "templateTags": [],
        "customTemplateOverrideUsed": False, "configFormat": "raw",
    }
