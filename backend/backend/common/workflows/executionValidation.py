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

# Keys a template may override on the pipeline systemConfig.
TEMPLATE_OVERRIDABLE_KEYS = ("inputFileArity", "metadataInputs", "assetScope", "inputFileFilters")

_METADATA_KEYS = ("assetMetadata", "fileMetadata", "fileAttributes")

# assetScope shorthand -> canonical key. The pipeline registration schemas (vamsSchema/pipeline.json)
# spell whole-asset support as `wholeAsset`; the canonical record/UI vocabulary is the four *Allowed
# booleans. Both spellings are accepted on a stored config and evaluate identically.
_ASSET_SCOPE_SHORTHAND = {"wholeAsset": "wholeAssetAllowed"}


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


def _normalized_extension(pattern):
    """An extension pattern reduced to its '.ext' form, lower-cased."""
    pat = pattern.lower()
    return pat[1:] if pat.startswith("*.") else pat


def _patterns_may_overlap(pattern_a, pattern_b):
    """Whether two allow patterns can admit a common file. The two extension forms ('*.glb', '.glb')
    compare by extension; anything carrying a wildcard is treated as possibly overlapping, since a
    pattern-to-pattern comparison cannot decide it."""
    a = (pattern_a or "").lower()
    b = (pattern_b or "").lower()
    if _is_extension_pattern(pattern_a) and _is_extension_pattern(pattern_b):
        return _normalized_extension(pattern_a) == _normalized_extension(pattern_b)
    if a == b:
        return True
    return any(ch in a or ch in b for ch in ("*", "?", "["))


def _non_extension_patterns(patterns):
    """The subset of patterns that are not extension patterns (path/name globs and exact keys)."""
    return [p for p in patterns or [] if p and not _is_extension_pattern(p)]


def apply_input_file_filters(selected_inputs, input_file_filters):
    """Return the subset of selected_inputs that passes an {allow, exclude} filter. Empty allow means
    allow-all; exclude is applied after allow. Each input is a dict with 'relativeFileKey'.

    A whole-asset ('/') or folder selection names a container rather than a file, so an extension
    pattern cannot describe it: extension patterns are dropped from both lists for those entries
    while path/name globs and exact keys still apply. An allow list made up only of extension
    patterns therefore admits a container selection, leaving its admissibility to the assetScope
    whole-asset / folder gates."""
    filters = input_file_filters or {}
    allow = filters.get("allow") or []
    exclude = filters.get("exclude") or []
    result = []
    for item in selected_inputs or []:
        fk = item.get("relativeFileKey", "")
        is_container = _is_whole_asset(fk) or _is_folder(fk)
        entry_allow = _non_extension_patterns(allow) if is_container else allow
        entry_exclude = _non_extension_patterns(exclude) if is_container else exclude
        if entry_allow and not _matches_any(fk, entry_allow):
            continue
        if entry_exclude and _matches_any(fk, entry_exclude):
            continue
        result.append(item)
    return result


def _pipeline_label(pipeline_database_id, pipeline_id):
    """Message label for one pipeline, composite-keyed so same-id pipelines across databases are
    distinguishable."""
    return (f"pipeline '{pipeline_database_id}:{pipeline_id}'" if pipeline_database_id
            else f"pipeline '{pipeline_id}'")


def _arity(system_config):
    """The declared inputFileArity, defaulting to 'one' for an absent or null value."""
    return (system_config or {}).get("inputFileArity") or "one"


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


def normalize_asset_scope(asset_scope):
    """Canonicalize an assetScope map: the registration shorthand `wholeAsset` becomes
    `wholeAssetAllowed`. An explicit canonical key already present wins. Returns a new dict."""
    scope = dict(asset_scope or {})
    for shorthand, canonical in _ASSET_SCOPE_SHORTHAND.items():
        if shorthand in scope:
            value = scope.pop(shorthand)
            scope.setdefault(canonical, value)
    return scope


def _scope_errors(asset_scope, inputs, subject, declared_only=False):
    """Asset-span and whole-asset/folder selection checks for one assetScope. `subject` prefixes each
    message ('Workflow' or a pipeline label). `declared_only` limits the checks to the keys the scope
    actually declares — a pipeline's assetScope may be a partial declaration layered under the
    workflow gate, so an omitted key defers to the workflow rather than denying."""
    scope = normalize_asset_scope(asset_scope)
    entries = inputs or []
    messages = []

    def declared(key):
        return (not declared_only) or key in scope

    asset_ids = {i.get("assetId", "") for i in entries if i.get("assetId")}
    if declared("singleAssetOnly") and scope.get("singleAssetOnly") and len(asset_ids) > 1:
        messages.append(f"{subject} allows a single asset only, but inputs span multiple assets.")
    if declared("crossAssetAllowed") and not scope.get("crossAssetAllowed", False) and len(asset_ids) > 1:
        messages.append(f"{subject} does not allow cross-asset inputs, but inputs span multiple assets.")
    if (declared("wholeAssetAllowed") and not scope.get("wholeAssetAllowed", False)
            and any(_is_whole_asset(i.get("relativeFileKey", "")) for i in entries)):
        messages.append(f"{subject} does not allow whole-asset ('/') selection.")
    if (declared("folderAllowed") and not scope.get("folderAllowed", False)
            and any(_is_folder(i.get("relativeFileKey", "")) for i in entries)):
        messages.append(f"{subject} does not allow folder selection.")
    return messages


def _validate_workflow_level(workflow_system_config, selected_inputs, output_target, errors):
    """Workflow-level hard checks (mutates `errors`)."""
    wsc = workflow_system_config or {}
    scope = normalize_asset_scope(wsc.get("assetScope"))
    inputs = selected_inputs or []
    count = len(inputs)

    # Arity.
    arity_error = _arity_violation(_arity(wsc), count)
    if arity_error:
        errors.append(f"Workflow {arity_error}.")

    # Asset span + whole-asset / folder selections.
    asset_ids = {i.get("assetId", "") for i in inputs if i.get("assetId")}
    errors.extend(_scope_errors(scope, inputs, "Workflow"))

    # Workflow input filters (hard).
    if apply_input_file_filters(inputs, wsc.get("inputFileFilters")) != inputs and (
        wsc.get("inputFileFilters", {}).get("allow") or wsc.get("inputFileFilters", {}).get("exclude")
    ):
        errors.append("One or more input files fail the workflow input-file filters.")

    # Output-target override: supplied when not allowed is IGNORED (not an error). A required output
    # (multi + crossAsset) missing IS an error.
    ot = output_target or {}
    allow_override = (wsc.get("outputTarget") or {}).get("allowOverride", False)
    if allow_override and scope.get("crossAssetAllowed") and _arity(wsc) == "multi":
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
        label = _pipeline_label(pdb, pid)
        psc = pipeline.get("systemConfig") or {}

        # Disabled / archived gate.
        if pipeline.get("enabled") is False:
            errors.append(f"{label} is disabled and cannot run in this workflow.")
        if pipeline.get("archived") is True:
            errors.append(f"{label} is archived and cannot run in this workflow.")

        arity = _arity(psc)

        # A 'none' pipeline never consumes files: it receives no inputs regardless of what the
        # workflow selected.
        if arity == "none":
            filtered[pid] = []
            continue

        # Filter the workflow inputs down to what this pipeline accepts.
        pipeline_inputs = apply_input_file_filters(selected_inputs, psc.get("inputFileFilters"))
        filtered[pid] = pipeline_inputs

        # A file-requiring pipeline whose filters exclude all selected inputs is a hard error.
        if selected_inputs and not pipeline_inputs:
            errors.append(
                f"{label} requires input files but its input-file filters exclude all selected inputs."
            )
            continue

        # The pipeline's own (possibly template-overridden) assetScope, applied to the inputs it
        # receives. Only the keys it declares are checked; the rest defer to the workflow gate.
        errors.extend(_scope_errors(
            psc.get("assetScope"), pipeline_inputs, label, declared_only=True))

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

    pipeline_configs: list of {pipelineId, pipelineDatabaseId, enabled, archived, systemConfig}.
    trigger: optional {undefaultedRequiredTagsByTemplateId: {templateId: [tagKey, ...]}} for the
      trigger-default check. The workflow save path passes no trigger; trigger saves run the hard
      equivalent in common/workflows/triggerTemplateValidation.
    """
    errors = []
    warnings = []
    wsc = workflow_system_config or {}
    wf_metadata = wsc.get("metadataInputs") or {}
    wf_arity = _arity(wsc)
    wf_filters = wsc.get("inputFileFilters") or {}

    for pipeline in pipeline_configs or []:
        pid = pipeline.get("pipelineId", "")
        pdb = pipeline.get("pipelineDatabaseId", "")
        label = _pipeline_label(pdb, pid)
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
        if wf_arity == "multi" and _arity(psc) == "one":
            warnings.append(
                f"{label} accepts a single input file but the workflow allows multiple; "
                "multi-file executions may fail this pipeline."
            )

        # Filter shadowing: workflow allow-list disjoint from pipeline allow-list.
        wf_allow = wf_filters.get("allow") or []
        p_allow = (psc.get("inputFileFilters") or {}).get("allow") or []
        overlap = any(_patterns_may_overlap(w, p) for w in wf_allow for p in p_allow)
        if wf_allow and p_allow and not overlap:
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
