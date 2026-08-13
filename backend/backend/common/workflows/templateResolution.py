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
  - SYSTEM tags: the built-in tags (common/workflows/templateTags.SYSTEM_TAG_NAMES) that the renderer
    (common/workflows/templateRender) resolves per pipeline task against the manifest + execution
    context at launch. Left in place HERE.

So resolution substitutes only the user tags and leaves system tags as {{...}} placeholders; an
"unmatched" {{tag}} — one that is neither a filled user tag nor a reserved system tag — is an error
(the Q1 contract: extra provided tags are ignored; an unmatched body tag errors). A {{tag}} using the
reserved dynamic-metadata prefix (metadata_) is likewise an error: the prefix is reserved against
user tag keys but no renderer resolves it, so it can never render.
"""

import json
import re

from common.workflows.templateTags import METADATA_DYNAMIC_TAG_PREFIX, is_reserved_tag_key
from common.workflows.templateRender import escape_scalar
from common.workflows import templateTagSchema as tts

# Same tag pattern as templateRender: {{ tagName }} with tolerated inner whitespace.
_TAG_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

# The configFormat a template-less override body resolves under (it carries no declared format).
CONFIG_FORMAT_RAW = "raw"


class TemplateResolutionError(Exception):
    """Raised when a pipeline's execute-time parameters cannot be resolved to a config body."""

    def __init__(self, errors):
        self.errors = errors if isinstance(errors, list) else [errors]
        super().__init__("; ".join(self.errors))


def _filled_from_raw_tags(provided_tags):
    """Build a {key: value} map from caller-provided [{key, value}] tags with NO schema (the
    override-without-template case). Reserved system tag keys are rejected; every other
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


def _value_text(value):
    """A tag value as the text that would be substituted, for scanning purposes. `default=str` covers a
    stored DynamoDB numeric reaching this path alongside a request-supplied float."""
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _json_override_errors(body, config_format, tag_schema_fields):
    """Structural check for a caller-supplied override body. Returns a list of errors ([] when fine).

    Only json-format bodies are checked: yaml/openjd/xml/raw are passed through as text, exactly as at
    save time. This applies the same two-pass placeholder parse the template save path uses, so a body
    that would be refused when STORED is also refused when supplied at LAUNCH — the stored body has
    passed that gate and an override never had to.

    Returns errors rather than raising because every other failure in this module is reported that way,
    and the caller turns the list into a 400 naming the pipeline.
    """
    if config_format != "json" or not body:
        return []
    # Imported here rather than at module scope: models.pipelines imports this module's sibling
    # templateRender, and a module-level import would close the cycle back through models.
    from models.pipelines import _validate_json_config_body
    try:
        _validate_json_config_body(body, tag_schema_fields)
    except ValueError as e:
        return [f"customTemplateOverride is not valid: {e}"]
    return []


def _substitute_user_tags(text, filled_tags, config_format="json"):
    """Replace {{tag}} occurrences of the provided/declared USER tags in text, leaving reserved
    system tags in place. Returns (rendered_text, errors). A {{tag}} that is neither a filled user
    tag nor a reserved system tag is an unmatched-tag error.

    A string tag value is substituted escaped for `config_format` without surrounding quotes (so it
    sits inside the template's own quotes, using the same escaping the launch-time renderer applies to
    its scalar tags); a non-string value (int/float/bool/list) is substituted as a JSON literal (no
    surrounding quotes).

    A user value carrying its own {{...}} placeholder is an error: substitution is single-pass, so the
    placeholder would survive into the stored config and be resolved (or hard-fail as unknown) by the
    launch-time renderer."""
    if not text:
        return text or "", []
    errors = []
    found = set(_TAG_PATTERN.findall(text))
    for name in sorted(found):
        if name in filled_tags:
            if _TAG_PATTERN.search(_value_text(filled_tags[name])):
                errors.append(
                    f"template tag '{name}' has a value containing a template placeholder, which is "
                    f"not allowed"
                )
            continue
        if name.startswith(METADATA_DYNAMIC_TAG_PREFIX):
            # The dynamic metadata family is reserved but not resolved by the launch-time renderer;
            # rejecting it here names the tag in a 400 instead of failing the launch (or a later
            # pipeline task) on an unknown tag.
            errors.append(
                f"template tag '{{{{{name}}}}}' uses the reserved '{METADATA_DYNAMIC_TAG_PREFIX}' "
                f"prefix, which is not resolvable; use one of the metadata-content system tags "
                f"instead"
            )
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
            return escape_scalar(value, config_format)  # scalar: escaped, quotes stripped
        return json.dumps(value)                        # json literal (list/number/bool)

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

    `renderedConfig` is HALF-rendered by design — do not "finish" the render here. Substitution runs in
    two stages:
      1. HERE: the USER tags (a template's declared tagSchema values, supplied per execution) are
         substituted; reserved SYSTEM tags are deliberately LEFT IN PLACE (_substitute_user_tags).
      2. LATER, per step, against that step's manifest + execution context, writing the fully rendered
         body to pipeline{N}/config.json — which is what the pipeline actually reads:
           - step 1  : executeWorkflow._launch_workflow, via templateRender.render_config
           - steps 2+: sfn/interimPipelineTracking._render_next_pipeline_config, mid-run
    Resolving system tags at this stage would break that: their values (manifest paths, per-step
    metadata, job timestamps) do not exist until launch, and for steps 2+ not until the prior step has
    finished. A literal {{assetMetadataObject}} surviving into this return value is therefore CORRECT.

    Consequence for readers: the value persisted as the details response's `renderedConfig` is this
    stage-1 body, while its sibling `renderedConfigLocation` points at the stage-2 object. The two
    describe different things on purpose; see the field notes in api/workflows.md.
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
            # An override is a caller-supplied body arriving at LAUNCH, so it never passed the
            # save-time gate the stored body did. Without this check a json-format override could be
            # structurally broken, or could quote a typed tag's placeholder and deliver "150" where the
            # schema promised 150 — and every pipeline-side config reader treats an unparseable
            # configuration as "absent" and falls back to its defaults, so the run SUCCEEDS with the
            # caller's parameters silently dropped. Checked against the same tag schema the tags are
            # validated against, so a typed placeholder is judged by its declaration.
            body_errors = _json_override_errors(body, config_format, tag_schema_fields)
            if body_errors:
                return body_errors, None
        else:
            body = template_row.get("configBody", "")
            override_used = False
        errors, filled = tts.validate_tags(tag_schema_fields or [], provided_tags)
        if errors:
            return errors, None
        rendered, render_errors = _substitute_user_tags(body, filled, config_format)
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
        # Deliberately NOT structurally checked, unlike the template-backed override above. This case
        # has no template and therefore no declared configFormat: the body is resolved and reported as
        # `raw`, so it makes no claim to be JSON and there is nothing to hold it to. A pipeline that
        # needs a checked json body declares a template (requireTemplate), which routes to the case
        # above.
        #
        # No schema either: take provided tags AS-IS (reserved keys still rejected). Every {{tag}} in the
        # override must have a provided value (or be a system tag) — enforced by _substitute_user_tags.
        errors, filled = _filled_from_raw_tags(provided_tags)
        if errors:
            return errors, None
        rendered, render_errors = _substitute_user_tags(override, filled, CONFIG_FORMAT_RAW)
        if render_errors:
            return render_errors, None
        return [], {
            "templateId": "", "renderedConfig": rendered, "templateTags": provided_tags,
            "customTemplateOverrideUsed": True, "configFormat": CONFIG_FORMAT_RAW,
        }

    # Case 4: no template, no override. Only allowed when the pipeline does not requireTemplate.
    if require_template:
        return ["this pipeline requires a template (templateId) for execution"], None
    # Only system/execution variables apply; no user config body.
    return [], {
        "templateId": "", "renderedConfig": "", "templateTags": [],
        "customTemplateOverrideUsed": False, "configFormat": CONFIG_FORMAT_RAW,
    }
