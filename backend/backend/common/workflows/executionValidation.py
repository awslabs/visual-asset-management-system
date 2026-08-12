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

from common.workflows.executionRecords import metadata_input_enabled

# Keys a template may override on the pipeline systemConfig.
TEMPLATE_OVERRIDABLE_KEYS = ("inputFileArity", "metadataInputs", "assetScope", "inputFileFilters")

_METADATA_KEYS = ("assetMetadata", "fileMetadata", "fileAttributes", "databaseMetadata")

# The metadata keys scoped to an input FILE. assetMetadata and databaseMetadata describe an entity, so
# they are collectable regardless of arity; these two have nothing to describe without an input file.
_FILE_SCOPED_METADATA_KEYS = ("fileMetadata", "fileAttributes")

# A stored metadataInputs map may omit keys, so every read of one resolves an absent key to its builder
# default (METADATA_INPUT_DEFAULTS) rather than to False — the execute path collects on the same rule,
# so what these checks report is what a run actually gathers.
_metadata_enabled = metadata_input_enabled

# Patterns that match every file. In an ALLOW list these mean allow-all, which is also what an absent
# or empty allow list means — so an allow list consisting only of these is "open" and defers to the
# next level down the chain. In an EXCLUDE list they are rejected at save time (they would exclude
# everything); see _validate_input_file_filters in models/pipelines.py.
MATCH_EVERYTHING_PATTERNS = ("*", "**", "*.*", "/*", "/**")

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


def _excluded_pipeline_allows(workflow_exclude, pipeline_allow):
    """The pipeline allow-patterns the workflow's EXCLUDE list would suppress.

    Decidable only for extension-vs-extension comparisons: '*.glb' excluded kills a pipeline that
    only accepts '*.glb'. A wildcard on either side cannot be resolved pattern-to-pattern, so it is
    left alone rather than guessed at — a false warning on every glob-filtered workflow would train
    users to ignore the panel."""
    suppressed = []
    for allowed in pipeline_allow or []:
        if not allowed or not _is_extension_pattern(allowed):
            continue
        for excluded in workflow_exclude or []:
            if (excluded and _is_extension_pattern(excluded)
                    and _normalized_extension(excluded) == _normalized_extension(allowed)):
                suppressed.append(allowed)
                break
    return suppressed


def is_open_allow_list(allow):
    """True when an allow list places no restriction: absent, empty, or made up only of
    match-everything patterns. An open allow list at one level of the chain defers the decision to the
    next level down (workflow -> pipeline -> template override), which is what lets a permissive
    workflow host restrictive pipelines."""
    patterns = [p.strip() for p in (allow or []) if p and p.strip()]
    return not patterns or all(p in MATCH_EVERYTHING_PATTERNS for p in patterns)


def apply_input_file_filters(selected_inputs, input_file_filters):
    """Return the subset of selected_inputs that passes an {allow, exclude} filter. Empty allow means
    allow-all; exclude is applied after allow. Each input is a dict with 'relativeFileKey'.

    A whole-asset ('/') or folder selection names a container rather than a file, so an extension
    pattern cannot describe it: extension patterns are dropped from both lists for those entries
    while path/name globs and exact keys still apply. An allow list made up only of extension
    patterns therefore admits a container selection, leaving its admissibility to the assetScope
    whole-asset / folder gates."""
    filters = input_file_filters or {}
    # An allow list of only match-everything patterns is treated exactly like an absent one, so '*'
    # reads as "no restriction at this level" rather than as a pattern to match against. Without this
    # the two spellings of the same intent behave differently on container selections, where
    # extension patterns are stripped: a ['*'] allow list would survive stripping and match, while
    # ['*.glb'] would be stripped to nothing and fall through to allow-all.
    allow = [] if is_open_allow_list(filters.get("allow")) else (filters.get("allow") or [])
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


def _dedupe(patterns):
    """Patterns with duplicates removed, first occurrence order preserved. Compared case-insensitively
    because the matcher is case-insensitive, so '*.GLB' and '*.glb' are the same restriction."""
    seen = set()
    result = []
    for pattern in patterns or []:
        text = (pattern or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def aggregate_input_file_filters(workflow_system_config, pipeline_system_configs):
    """The file restriction a workflow effectively imposes, for display alongside its systemConfig.

    Resolution follows the chain's own precedence:
      - The workflow's allow list, when it is NOT open, IS the answer — it is the outer boundary, so
        nothing a pipeline allows can widen it.
      - When the workflow's allow list is open (absent/empty/'*'), the restriction is whatever its
        pipelines impose, so the pipelines' allow lists are unioned. A union is correct because the
        pipelines are alternatives from a file's point of view: a file usable by ANY pipeline in the
        workflow is a file the workflow can do something with.

    Excludes are unioned across the workflow and every pipeline regardless, since an exclusion at any
    level removes the file.

    Returns {allow, exclude, source, includesTemplateOverrides: False}. `source` is 'workflow' or
    'pipelines' so a caller can label where the restriction came from.

    IMPORTANT: template `overrides` are deliberately NOT folded in — a template is chosen per
    execution, so the aggregate cannot know them. This value is therefore indicative for browsing, and
    must not be used to validate a concrete selection; the execute path resolves the real effective
    config per pipeline (resolve_effective_pipeline_config) instead."""
    wsc = workflow_system_config or {}
    wf_filters = wsc.get("inputFileFilters") or {}
    wf_allow = wf_filters.get("allow") or []

    excludes = list(wf_filters.get("exclude") or [])
    pipeline_allows = []
    for psc in pipeline_system_configs or []:
        filters = (psc or {}).get("inputFileFilters") or {}
        pipeline_allows.extend(filters.get("allow") or [])
        excludes.extend(filters.get("exclude") or [])

    if not is_open_allow_list(wf_allow):
        allow, source = list(wf_allow), "workflow"
    else:
        # Any pipeline being open means the union is open, so it collapses to no restriction.
        open_pipeline = any(
            is_open_allow_list(((psc or {}).get("inputFileFilters") or {}).get("allow"))
            for psc in pipeline_system_configs or []
        )
        allow = [] if (open_pipeline or not pipeline_allows) else pipeline_allows
        source = "pipelines"

    return {
        "allow": _dedupe(allow),
        "exclude": _dedupe(excludes),
        "source": source,
        "includesTemplateOverrides": False,
    }


def aggregate_metadata_inputs(workflow_system_config, pipeline_system_configs,
                              template_overrides=None):
    """The metadata inputs a workflow's steps will actually receive, for display alongside its
    systemConfig.

    A metadata type reaches a pipeline only when BOTH the workflow gate has it on and the pipeline
    asks for it. The two levels do different jobs: the workflow gate is INTAKE (what the run gathers
    into the shared envelope at all) and the pipeline's own value is DELIVERY (what that step is
    handed, narrowed per step by executionRecords.narrow_metadata_envelope). So each key is
    (workflow gate AND any pipeline wants it), and a key the workflow gates off is reported as off
    however many pipelines want it.

    Returns {assetMetadata, fileMetadata, fileAttributes, databaseMetadata,
    gatedOffByWorkflow: [keys]} where `gatedOffByWorkflow` names the types a pipeline asked for but
    the workflow suppressed — the case worth showing, since the pipeline runs without data it
    declared it uses.

    A map that omits a key carries that key's builder default, so a config stored before a key existed
    reports the toggle the execute path actually collects on.

    `template_overrides` is the ordered list of each pipeline's chosen template `overrides` map,
    positionally matching `pipeline_system_configs`. Supplying it folds the overrides into each
    pipeline's effective config — overrides genuinely change delivery, so a view computed without them
    can differ from what a run delivers. `includesTemplateOverrides` reports whether they were
    supplied: a workflow-level view has no single template choice (templates are chosen per execution,
    or per trigger via defaultTemplateIds), so that caller reports False truthfully rather than
    implying a resolution it did not perform."""
    wsc = workflow_system_config or {}
    gate = wsc.get("metadataInputs") or {}
    configs = list(pipeline_system_configs or [])
    overrides_list = list(template_overrides or [])
    effective_configs = [
        resolve_effective_pipeline_config(
            psc or {}, overrides_list[i] if i < len(overrides_list) else None)
        for i, psc in enumerate(configs)
    ]
    result = {}
    gated_off = []
    for key in _METADATA_KEYS:
        wanted = any(
            _metadata_enabled((psc or {}).get("metadataInputs"), key)
            for psc in effective_configs
        )
        allowed = _metadata_enabled(gate, key)
        result[key] = bool(allowed and wanted)
        if wanted and not allowed:
            gated_off.append(key)
    result["gatedOffByWorkflow"] = gated_off
    result["includesTemplateOverrides"] = bool(overrides_list)
    return result


def arity_none_metadata_warnings(system_config, label=""):
    """Warnings (never errors) for a systemConfig that declares inputFileArity 'none' while asking for
    the file-scoped metadata types. Those types describe an input file, so an arity-none run has
    nothing to collect them from and they are inert; the config is authored, not broken, and several
    shipped pipelines carry it. `label` prefixes the message ('' for an unlabeled single config)."""
    sc = system_config or {}
    if _arity(sc) != "none":
        return []
    metadata = sc.get("metadataInputs") or {}
    inert = [k for k in _FILE_SCOPED_METADATA_KEYS if _metadata_enabled(metadata, k)]
    if not inert:
        return []
    prefix = f"{label} " if label else ""
    return [
        f"{prefix}uses {', '.join(inert)} but expects no input files (inputFileArity 'none'), so "
        f"{'that type has' if len(inert) == 1 else 'those types have'} no effect. Asset and database "
        "metadata are still collected from any metadata sources the run names."
    ]


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
    """Core evaluation shared by execute-time and save-time. Returns (errors, warnings, filtered).

    Two-stage by design: the workflow's own inputFileFilters narrow the selection FIRST, and every
    pipeline is then judged against that narrowed list rather than against the raw selection. The
    workflow gate is the outer boundary of what an execution may carry, so a file it excludes is not
    available to any pipeline and must not count toward one's arity or filters. Judging a pipeline on
    the raw list can both invent errors (a pipeline blamed for a file the workflow already dropped)
    and hide them (a pipeline's arity satisfied by a file that never reaches it)."""
    errors = []
    warnings = []
    filtered = {}

    _validate_workflow_level(workflow_system_config, selected_inputs, output_target, errors)

    workflow_inputs = apply_input_file_filters(
        selected_inputs, (workflow_system_config or {}).get("inputFileFilters"))

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

        # Filter the WORKFLOW-ADMITTED inputs down to what this pipeline accepts.
        pipeline_inputs = apply_input_file_filters(workflow_inputs, psc.get("inputFileFilters"))
        filtered[pid] = pipeline_inputs

        # A file-requiring pipeline left with nothing is a hard error. Which filter emptied the list
        # decides the message: naming the workflow's filters when they are the cause points at the
        # actual misconfiguration instead of blaming a pipeline whose own filters were never reached.
        if workflow_inputs and not pipeline_inputs:
            errors.append(
                f"{label} requires input files but its input-file filters exclude all selected inputs."
            )
            continue
        if selected_inputs and not workflow_inputs:
            errors.append(
                f"{label} requires input files but the workflow's input-file filters exclude every "
                "selected input, so no file reaches it."
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
      - inert file-scoped metadata: arity 'none' with fileMetadata/fileAttributes asked for,
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

    warnings.extend(arity_none_metadata_warnings(wsc, "the workflow"))

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
            if (_metadata_enabled(p_metadata, meta_key)
                    and not _metadata_enabled(wf_metadata, meta_key)):
                warnings.append(
                    f"{label} uses {meta_key} but the workflow's metadata input for {meta_key} is "
                    "off; the pipeline will run without it."
                )

        # File-scoped metadata a pipeline asks for that its own arity leaves nothing to collect from.
        warnings.extend(arity_none_metadata_warnings(psc, label))

        # Arity mismatch (surfaces the execute matrix's multi-vs-single case at save time).
        if wf_arity == "multi" and _arity(psc) == "one":
            warnings.append(
                f"{label} accepts a single input file but the workflow allows multiple; "
                "multi-file executions may fail this pipeline."
            )

        # Filter shadowing, two independent ways a workflow can starve a pipeline of its input:
        p_filters = psc.get("inputFileFilters") or {}
        p_allow = p_filters.get("allow") or []

        # (1) The workflow's allow-list admits nothing the pipeline accepts.
        wf_allow = wf_filters.get("allow") or []
        overlap = any(_patterns_may_overlap(w, p) for w in wf_allow for p in p_allow)
        if wf_allow and p_allow and not overlap:
            warnings.append(
                f"{label} input-file filters may exclude everything the workflow filters allow."
            )

        # (2) The workflow EXCLUDES a type the pipeline accepts. Distinct from (1): the allow-lists
        # can overlap perfectly and an exclude still removes the file afterwards, since exclude is
        # applied second. Worth a separate message because the fix is a different field.
        suppressed = _excluded_pipeline_allows(wf_filters.get("exclude"), p_allow)
        if suppressed:
            remaining = [p for p in p_allow if p not in suppressed]
            detail = (
                "leaving it no accepted input type" if not remaining
                else f"leaving it only {', '.join(remaining)}"
            )
            warnings.append(
                f"{label} accepts {', '.join(suppressed)} but the workflow's input-file filters "
                f"exclude {'that' if len(suppressed) == 1 else 'those'}, {detail}."
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
