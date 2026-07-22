# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cross-entity execution validation + workflow-save consistency checks.

Pure module (no AWS/env deps, mirrors the other common/workflows record modules) so it unit-tests in
isolation. It encodes the workflow↔pipeline hard/soft matrix that the execute handler runs before
launch, and the lighter consistency checks the workflow create/update handler runs at save time.

Separation of concerns: this module decides *validity* from already-resolved config dicts + the
selected inputs. It does NOT read DynamoDB/S3, resolve templates, or launch executions — callers
resolve the effective per-pipeline config (pipeline systemConfig with the chosen template's
`overrides` merged over the four overridable keys) and pass it in.

Effective-config precedence (applied by resolve_effective_pipeline_config): pipeline systemConfig is
the base; a chosen template's `overrides` replaces, per key, only `inputFileArity`, `metadataInputs`,
`assetScope`, `inputFileFilters` (never `requireTemplate` / `allowCustomTemplateOverride`).
"""

import fnmatch

# Keys a template may override on the pipeline systemConfig (comment 2e).
TEMPLATE_OVERRIDABLE_KEYS = ("inputFileArity", "metadataInputs", "assetScope", "inputFileFilters")

_METADATA_KEYS = ("assetMetadata", "fileMetadata", "fileAttributes")


def resolve_effective_pipeline_config(pipeline_system_config, template_overrides=None):
    """Merge a chosen template's `overrides` over the pipeline systemConfig for the overridable keys
    only. Returns a new dict; inputs are not mutated. An absent/empty override leaves the pipeline
    value unchanged."""
    effective = dict(pipeline_system_config or {})
    overrides = template_overrides or {}
    for key in TEMPLATE_OVERRIDABLE_KEYS:
        if key in overrides and overrides[key] is not None:
            effective[key] = overrides[key]
    return effective


def _is_whole_asset(relative_file_key):
    """A whole-asset selection is the asset root '/'."""
    return (relative_file_key or "") in ("", "/")


def _is_folder(relative_file_key):
    """A folder selection ends with '/' and is not the asset root."""
    fk = relative_file_key or ""
    return fk.endswith("/") and fk not in ("", "/")


def _is_extension_pattern(pattern):
    """True for the two equivalent extension forms: '*.ext' (canonical) and '.ext' (shorthand), where
    ext is alphanumeric with no path separator. These match a file by its extension. A general glob
    ('*skip*', '*.previewFile.*', '/models/*') or an exact path is NOT an extension pattern."""
    if "/" in pattern:
        return False
    if pattern.startswith("*."):
        ext = pattern[1:]  # '*.ext' -> '.ext'
    elif pattern.startswith("."):
        ext = pattern  # '.ext'
    else:
        return False
    body = ext[1:]
    return bool(body) and body.isalnum()


def _matches_any(relative_file_key, patterns):
    """True when the file key matches any allow/exclude pattern. Matching is CASE-INSENSITIVE and
    OS-independent. A pattern matches by:
      - extension: '*.glb' (canonical) or '.glb' (shorthand) — matches a file by its extension;
      - glob/wildcard: any other fnmatch pattern ('*skip*', '*.previewFile.*', '/models/*');
      - exact key: a full relative key ('/models/a.glb')."""
    fk = (relative_file_key or "").lower()
    name = fk.rstrip("/").split("/")[-1]
    for pattern in patterns or []:
        if not pattern:
            continue
        pat = pattern.lower()
        if _is_extension_pattern(pattern):
            ext = pat[1:] if pat.startswith("*.") else pat  # normalize to '.ext'
            if name.endswith(ext):
                return True
        # fnmatchcase on already-lowercased strings: case-insensitive matching that is still
        # OS-independent (plain fnmatch would apply OS-specific os.path.normcase, differing between
        # Windows dev and Linux Lambda).
        elif fnmatch.fnmatchcase(fk, pat) or fnmatch.fnmatchcase(name, pat):
            return True
        elif pat == fk:
            return True
    return False


def apply_input_file_filters(selected_inputs, input_file_filters):
    """Return the subset of selected_inputs that passes an {allow, exclude} filter. Empty allow means
    allow-all; exclude is applied after allow. Each input is a dict with 'relativeFileKey'."""
    filters = input_file_filters or {}
    allow = filters.get("allow") or []
    exclude = filters.get("exclude") or []
    result = []
    for item in selected_inputs or []:
        fk = item.get("relativeFileKey", "")
        if allow and not _matches_any(fk, allow):
            continue
        if exclude and _matches_any(fk, exclude):
            continue
        result.append(item)
    return result


def _arity_violation(arity, count):
    """Return an error fragment when input count violates an arity, else None. arity: none|one|multi."""
    if arity == "none":
        return "expects no input files" if count > 0 else None
    if arity == "one":
        if count == 0:
            return "requires exactly one input file but none were provided"
        if count > 1:
            return "accepts a single input file but multiple were provided"
        return None
    if arity == "multi":
        return "requires at least one input file but none were provided" if count == 0 else None
    return None


def _validate_workflow_level(workflow_system_config, selected_inputs, output_target, errors):
    """Workflow-level hard checks (mutates `errors`)."""
    wsc = workflow_system_config or {}
    scope = wsc.get("assetScope") or {}
    inputs = selected_inputs or []
    count = len(inputs)

    # Arity.
    arity_error = _arity_violation(wsc.get("inputFileArity", "one"), count)
    if arity_error:
        errors.append(f"Workflow {arity_error}.")

    # Asset span.
    asset_ids = {i.get("assetId", "") for i in inputs if i.get("assetId")}
    if scope.get("singleAssetOnly") and len(asset_ids) > 1:
        errors.append("Workflow allows a single asset only, but inputs span multiple assets.")
    if not scope.get("crossAssetAllowed", False) and len(asset_ids) > 1:
        errors.append("Workflow does not allow cross-asset inputs, but inputs span multiple assets.")

    # Whole-asset / folder selections.
    if not scope.get("wholeAssetAllowed", False) and any(
        _is_whole_asset(i.get("relativeFileKey", "")) for i in inputs
    ):
        errors.append("Workflow does not allow whole-asset ('/') selection.")
    if not scope.get("folderAllowed", False) and any(
        _is_folder(i.get("relativeFileKey", "")) for i in inputs
    ):
        errors.append("Workflow does not allow folder selection.")

    # Workflow input filters (hard).
    if apply_input_file_filters(inputs, wsc.get("inputFileFilters")) != inputs and (
        wsc.get("inputFileFilters", {}).get("allow") or wsc.get("inputFileFilters", {}).get("exclude")
    ):
        errors.append("One or more input files fail the workflow input-file filters.")

    # Output-target override: supplied when not allowed is IGNORED (not an error). A required output
    # (multi + crossAsset) missing IS an error.
    ot = output_target or {}
    allow_override = (wsc.get("outputTarget") or {}).get("allowOverride", False)
    if allow_override and scope.get("crossAssetAllowed") and wsc.get("inputFileArity") == "multi":
        if not ot.get("outputAssetId") and len(asset_ids) != 1:
            errors.append(
                "Workflow requires an output asset when override is allowed and inputs do not share "
                "a single asset."
            )


def _evaluate(workflow_system_config, pipeline_effective_configs, selected_inputs, output_target):
    """Core evaluation shared by execute-time and save-time. Returns (errors, warnings, filtered)."""
    errors = []
    warnings = []
    filtered = {}

    _validate_workflow_level(workflow_system_config, selected_inputs, output_target, errors)

    for pipeline in pipeline_effective_configs or []:
        pid = pipeline.get("pipelineId", "")
        pdb = pipeline.get("pipelineDatabaseId", "")
        # Label by the composite pipeline key so same-id pipelines across databases are
        # distinguishable in error messages.
        label = f"pipeline '{pdb}:{pid}'" if pdb else f"pipeline '{pid}'"
        psc = pipeline.get("systemConfig") or {}

        # Disabled / archived gate (comment 5c).
        if pipeline.get("enabled") is False:
            errors.append(f"{label} is disabled and cannot run in this workflow.")
        if pipeline.get("archived") is True:
            errors.append(f"{label} is archived and cannot run in this workflow.")

        arity = psc.get("inputFileArity", "one")

        # A 'none' pipeline never consumes files: it receives no inputs regardless of what the
        # workflow selected (matrix row: single/multi file + pipeline none -> pass no files, soft).
        if arity == "none":
            filtered[pid] = []
            continue

        # Filter the workflow inputs down to what this pipeline accepts.
        pipeline_inputs = apply_input_file_filters(selected_inputs, psc.get("inputFileFilters"))
        filtered[pid] = pipeline_inputs

        # Filter-to-empty on a file-requiring pipeline is a HARD error (locked decision 7).
        if selected_inputs and not pipeline_inputs:
            errors.append(
                f"{label} requires input files but its input-file filters exclude all selected inputs."
            )
            continue

        arity_error = _arity_violation(arity, len(pipeline_inputs))
        if arity_error:
            errors.append(f"{label} {arity_error}.")

    return errors, warnings, filtered


def validate_execution(
    workflow_system_config,
    pipeline_effective_configs,
    selected_inputs,
    output_target=None,
):
    """Execute-time cross-entity validation. Returns (errors, per_pipeline_filtered_inputs).

    - workflow_system_config: WorkflowRecordV2['systemConfig'].
    - pipeline_effective_configs: ordered list; each {pipelineId, pipelineDatabaseId, enabled,
      archived, systemConfig{...}} with the chosen template's overrides already merged
      (resolve_effective_pipeline_config).
    - selected_inputs: execute-request inputFiles[] ({databaseId, assetId, relativeFileKey, versionId}).
    - output_target: {outputAssetId, outputDatabaseId} requested (honored only when the workflow
      allows override).

    Edge cases err toward erroring; the matrix is the single source of truth and is refined over time.
    """
    errors, _warnings, filtered = _evaluate(
        workflow_system_config, pipeline_effective_configs, selected_inputs, output_target
    )
    return errors, filtered


def validate_workflow_save(workflow_system_config, pipeline_configs, trigger=None):
    """Workflow create/update consistency checks. Returns (errors, warnings).

    Unlike validate_execution this has no concrete inputs; it compares the workflow's systemConfig
    against its included pipelines' declared systemConfig to surface authoring issues early:
      - metadata mismatch: a pipeline needs a metadata type the workflow gate has off,
      - arity mismatch: workflow multi vs pipeline single,
      - filter shadowing: workflow filters that would exclude everything a pipeline needs,
      - trigger-default sanity: a default template whose required tags are not all defaulted.

    pipeline_configs: list of {pipelineId, pipelineDatabaseId, enabled, archived, systemConfig,
      requiredTagsUndefaultedByTemplateId?} — the last optional map supports the trigger check.
    trigger: optional {defaultTemplateIds: {'db:id': templateId}} for the trigger-default check.
    """
    errors = []
    warnings = []
    wsc = workflow_system_config or {}
    wf_metadata = wsc.get("metadataInputs") or {}
    wf_arity = wsc.get("inputFileArity", "one")
    wf_filters = wsc.get("inputFileFilters") or {}

    for pipeline in pipeline_configs or []:
        pid = pipeline.get("pipelineId", "")
        label = f"pipeline '{pid}'"
        psc = pipeline.get("systemConfig") or {}

        if pipeline.get("enabled") is False:
            warnings.append(f"{label} is disabled; it will not run until re-enabled.")
        if pipeline.get("archived") is True:
            errors.append(f"{label} is archived and cannot be part of a workflow.")

        # Metadata mismatch: pipeline wants a metadata type the workflow gate turned off.
        p_metadata = psc.get("metadataInputs") or {}
        for meta_key in _METADATA_KEYS:
            if p_metadata.get(meta_key) and not wf_metadata.get(meta_key):
                warnings.append(
                    f"{label} uses {meta_key} but the workflow's metadata input for {meta_key} is "
                    "off; the pipeline will run without it."
                )

        # Arity mismatch (surfaces the execute matrix's multi-vs-single case at save time).
        if wf_arity == "multi" and psc.get("inputFileArity") == "one":
            warnings.append(
                f"{label} accepts a single input file but the workflow allows multiple; "
                "multi-file executions may fail this pipeline."
            )

        # Filter shadowing: workflow allow-list disjoint from pipeline allow-list.
        wf_allow = wf_filters.get("allow") or []
        p_allow = (psc.get("inputFileFilters") or {}).get("allow") or []
        if wf_allow and p_allow and not (set(wf_allow) & set(p_allow)):
            warnings.append(
                f"{label} input-file filters may exclude everything the workflow filters allow."
            )

    # Trigger-default sanity: each default template must have all required tags defaulted.
    if trigger:
        undefaulted = trigger.get("undefaultedRequiredTagsByTemplateId") or {}
        for template_id, missing in undefaulted.items():
            if missing:
                warnings.append(
                    f"trigger default template '{template_id}' has required tags without defaults "
                    f"({', '.join(missing)}); auto-trigger would fail for it."
                )

    return errors, warnings
