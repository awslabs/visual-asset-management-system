/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Workflow, Pipeline } from "../types";
import { metadataEnabled, isOpenAllowList } from "../wizard/resolveRestrictions";

export interface ValidationResult {
    errors: string[];
    warnings: string[];
}

/**
 * Job name pattern. The job name becomes both a Step Functions state name and an S3 output-path
 * segment, and the backend validates it with the shared id validator, so it carries that exact
 * pattern (SpecifiedPipelineInput in backend/backend/models/workflows.py).
 */
export const JOB_NAME_PATTERN = /^[-_a-zA-Z0-9]{3,63}$/;

/** The metadata-input gates the backend compares between a workflow and its pipelines. */
const METADATA_KEYS = [
    "assetMetadata",
    "fileMetadata",
    "fileAttributes",
    "databaseMetadata",
] as const;

/** The two extension forms the backend recognises: '*.ext' (canonical) and '.ext' (shorthand). */
const EXTENSION_PATTERN = /^\*?\.[a-zA-Z0-9]+$/;

/**
 * Whether two allow patterns can admit a common file (mirrors _patterns_may_overlap in
 * common/workflows/executionValidation.py): two extension patterns compare by extension, and
 * anything carrying a wildcard is treated as possibly overlapping.
 */
function patternsMayOverlap(patternA: string, patternB: string): boolean {
    const a = (patternA || "").toLowerCase();
    const b = (patternB || "").toLowerCase();
    if (EXTENSION_PATTERN.test(patternA || "") && EXTENSION_PATTERN.test(patternB || "")) {
        return a.replace(/^\*/, "") === b.replace(/^\*/, "");
    }
    if (a === b) return true;
    return ["*", "?", "["].some((ch) => a.includes(ch) || b.includes(ch));
}

/**
 * The pipeline allow-patterns the workflow's EXCLUDE list would suppress (mirrors
 * _excluded_pipeline_allows in common/workflows/executionValidation.py). Decidable only for
 * extension-vs-extension comparisons; a wildcard on either side is left alone rather than guessed at,
 * since a false warning on every glob-filtered workflow would train users to ignore the panel.
 */
function excludedPipelineAllows(
    workflowExclude: string[] | undefined,
    pipelineAllow: string[] | undefined
): string[] {
    const ext = (p: string) => p.toLowerCase().replace(/^\*/, "");
    return (pipelineAllow || []).filter(
        (allowed) =>
            allowed &&
            EXTENSION_PATTERN.test(allowed) &&
            (workflowExclude || []).some(
                (excluded) =>
                    excluded && EXTENSION_PATTERN.test(excluded) && ext(excluded) === ext(allowed)
            )
    );
}

/** True when every pipeline reference names a pipeline (the picker starts each card empty). */
export function allPipelineRefsSelected(refs?: { pipelineId?: string }[]): boolean {
    return !!refs && refs.every((ref) => !!ref.pipelineId);
}

/**
 * The key a reference resolves through — the same `databaseId:pipelineId` composite the backend keys
 * a workflow's per-step params, resolved configs and filtered inputs by (pipeline_composite_key in
 * common/workflows/executionRecords.py).
 */
export function pipelineRefKey(ref: { pipelineId?: string; pipelineDatabaseId?: string }): string {
    return ref.pipelineDatabaseId
        ? `${ref.pipelineDatabaseId}:${ref.pipelineId}`
        : ref.pipelineId || "";
}

/** The scope selections a workflow can grant and a pipeline can decline. */
const SCOPE_SELECTIONS = [
    { key: "wholeAssetAllowed", label: "selecting a whole asset" },
    { key: "folderAllowed", label: "selecting a folder" },
] as const;

/**
 * One assetScope selection flag. `wholeAsset` is the shorthand the vamsSchema registration bundles
 * emit for `wholeAssetAllowed`; an explicit canonical key wins, exactly as normalize_asset_scope in
 * common/workflows/executionValidation.py resolves the pair. `undefined` means the scope declares
 * nothing about the selection, which is neither a grant nor a refusal.
 */
function scopeGrant(
    scope: Record<string, boolean> | undefined,
    key: (typeof SCOPE_SELECTIONS)[number]["key"]
): boolean | undefined {
    if (!scope) return undefined;
    if (key === "wholeAssetAllowed") {
        if (scope.wholeAssetAllowed !== undefined) return !!scope.wholeAssetAllowed;
        if (scope.wholeAsset !== undefined) return !!scope.wholeAsset;
        return undefined;
    }
    return scope.folderAllowed === undefined ? undefined : !!scope.folderAllowed;
}

/** Whether a step takes input files at all — mirrors the backend's `_arity` default of 'one'. */
function consumesInputFiles(pipeline: Pipeline): boolean {
    return (pipeline.systemConfig?.inputFileArity || "one") !== "none";
}

export interface ValidateWorkflowOptions {
    /**
     * False while the pipeline list is still loading. `pipelinesById` is then incomplete, so a
     * reference that is merely not fetched yet must not be reported as unresolvable.
     */
    pipelinesLoaded?: boolean;
}

/**
 * Validates a workflow before save, checking for structural errors and configuration warnings.
 */
export function validateWorkflow(
    // Accepts an in-progress create body too (workflowId may be null/absent before save); the
    // validation rules never read the workflow id.
    wf: Omit<Workflow, "workflowId"> & { workflowId?: string | null },
    pipelinesById: Record<string, Pipeline>,
    options: ValidateWorkflowOptions = {}
): ValidationResult {
    const pipelinesLoaded = options.pipelinesLoaded !== false;
    const errors: string[] = [];
    const warnings: string[] = [];

    // ERROR: At least one pipeline required
    if (!wf.specifiedPipelines || wf.specifiedPipelines.length < 1) {
        errors.push("At least one pipeline is required");
    }

    // ERROR: writing to an asset with no input files needs a selectable output asset at execute time
    if (
        wf.systemConfig?.outputTarget?.locationType === "asset" &&
        (wf.systemConfig?.inputFileArity ?? "one") === "none" &&
        !wf.systemConfig?.outputTarget?.allowOverride
    ) {
        errors.push(
            "A workflow with no input files (inputFileArity 'none') that writes to an asset must allow output override so an output asset can be chosen at execution time"
        );
    }

    // ERROR: workflowId pattern validation
    if (wf.workflowId && !/^[-_a-zA-Z0-9]{3,63}$/.test(wf.workflowId)) {
        errors.push(
            "workflowId must be 3-63 characters and contain only letters, numbers, hyphens, and underscores"
        );
    }

    // ERROR: subDashboardUrl must be an absolute http(s) URL (mirrors the backend rule; blocks
    // javascript:/data:/relative URLs that would otherwise be a stored-XSS link in the UI).
    if (wf.subDashboardUrl && !/^https?:\/\//i.test(wf.subDashboardUrl)) {
        errors.push("Sub-Dashboard URL must be an absolute http:// or https:// URL");
    }

    // Per-reference rules, then archived/disabled pipelines and arity mismatches
    if (wf.specifiedPipelines) {
        // First position each job name and each pipeline reference was seen at, so a repeat can name
        // the card it collides with rather than just reporting that a collision exists.
        const jobNameFirstSeen = new Map<string, number>();
        const refFirstSeen = new Map<string, number>();

        wf.specifiedPipelines.forEach((ref, i) => {
            const position = i + 1;

            // ERROR: an added card with nothing selected (the backend rejects an empty pipelineId)
            if (!ref.pipelineId) {
                errors.push(`Pipeline #${position} has no pipeline selected`);
            }

            // ERROR: the job name becomes a Step Functions state name and an S3 path segment
            if (ref.jobName && !JOB_NAME_PATTERN.test(ref.jobName)) {
                errors.push(
                    `Pipeline #${position} job name must be 3-63 characters and contain only letters, numbers, hyphens, and underscores`
                );
            }

            // ERROR: two steps sharing a job name collapse into ONE state machine state and one of
            // the pipelines never runs — create_state_machine keys its states by name, so the second
            // overwrites the first. Compared case-insensitively because the name is also an S3 path
            // segment, where two casings of one name are two folders that read as the same step.
            if (ref.jobName) {
                const nameKey = ref.jobName.toLowerCase();
                const seenAt = jobNameFirstSeen.get(nameKey);
                if (seenAt === undefined) {
                    jobNameFirstSeen.set(nameKey, position);
                } else {
                    errors.push(
                        `Pipeline #${position} job name '${ref.jobName}' is already used by pipeline ` +
                            `#${seenAt}; each step needs its own job name because it names the step ` +
                            `in the state machine and its output folder`
                    );
                }
            }

            const compositeKey = pipelineRefKey(ref);

            // ERROR: the same pipeline listed twice. The backend keys per-step execute params,
            // resolved configs and filtered inputs by this composite, so the later step overwrites
            // the earlier one and only one of them runs.
            if (ref.pipelineId) {
                const seenAt = refFirstSeen.get(compositeKey);
                if (seenAt === undefined) {
                    refFirstSeen.set(compositeKey, position);
                } else {
                    errors.push(
                        `Pipeline #${position} ('${ref.pipelineId}') is already used by pipeline ` +
                            `#${seenAt}; a workflow may reference each pipeline only once`
                    );
                }
            }

            const pipeline = pipelinesById[compositeKey];
            if (!pipeline) {
                // ERROR: a reference that resolves to nothing. The card renders as an empty picker
                // and the backend rejects the save with a message that names no position, so the
                // author is told which one here.
                if (ref.pipelineId && pipelinesLoaded) {
                    errors.push(
                        `Pipeline #${position} references '${compositeKey}', which is not an ` +
                            `available pipeline; it may have been deleted or is not accessible to you`
                    );
                }
                return;
            }

            // ERROR: the backend rejects a workflow that references an archived pipeline
            if (pipeline.archived === true) {
                errors.push(
                    `Pipeline #${position} ('${ref.pipelineId}') is archived and cannot be used in a workflow`
                );
            }

            // Warn if disabled
            if (pipeline.enabled === false) {
                warnings.push(`Pipeline '${ref.pipelineId}' is disabled and may not be executable`);
            }

            // Arity incompatibility. Mirrors validate_workflow_save: a pipeline that needs at least
            // one file gets none from a 'none' workflow, and a single-file pipeline may be handed
            // several by a 'multi' workflow. ('one' feeding a 'multi' pipeline is satisfied.) The
            // 'none' case describes the first step only: a later step also receives every earlier
            // step's output, so a generator step can feed it in a workflow that selects no files.
            const workflowArity = wf.systemConfig?.inputFileArity ?? "one";
            const pipelineArity = pipeline.systemConfig?.inputFileArity;

            if (pipelineArity) {
                const incompatible =
                    (workflowArity === "none" && pipelineArity !== "none" && i === 0) ||
                    (workflowArity === "multi" && pipelineArity === "one");

                if (incompatible) {
                    warnings.push(
                        `Pipeline '${ref.pipelineId}' requires inputFileArity '${pipelineArity}' but workflow provides '${workflowArity}'`
                    );
                }
            }

            // A metadata input the pipeline uses but the workflow gate turns off. Both sides read
            // through metadataEnabled: a key either map omits carries its builder default (ON), so
            // only an explicit `false` on the workflow is a gate. Reading the raw values instead would
            // warn that the workflow suppresses a type it actually provides — the same rule
            // validate_workflow_save applies in common/workflows/executionValidation.py.
            const pipelineMetadata = pipeline.systemConfig?.metadataInputs || {};
            const workflowMetadata = wf.systemConfig?.metadataInputs || {};
            METADATA_KEYS.forEach((key) => {
                if (
                    metadataEnabled(pipelineMetadata, key) &&
                    !metadataEnabled(workflowMetadata, key)
                ) {
                    warnings.push(
                        `Pipeline '${ref.pipelineId}' uses ${key} but the workflow's metadata input for ${key} is off; the pipeline will run without it`
                    );
                }
            });

            // An asset-selection the workflow grants but this step refuses. Every level must accept
            // the selection (the run is submitted once and each step is checked against it), so the
            // option never appears in the execute wizard and the workflow setting does nothing. Only
            // a file-consuming step counts: an arity-'none' step receives no input files, so the
            // backend never applies its scope to the selection.
            if (consumesInputFiles(pipeline)) {
                SCOPE_SELECTIONS.forEach(({ key, label }) => {
                    if (
                        scopeGrant(wf.systemConfig?.assetScope, key) === true &&
                        scopeGrant(pipeline.systemConfig?.assetScope, key) === false
                    ) {
                        warnings.push(
                            `The workflow allows ${label} but pipeline '${ref.pipelineId}' does ` +
                                `not, so the option will not be offered at execution time`
                        );
                    }
                });
            }

            // Two independent ways the workflow's filters can starve this pipeline of its input. Both
            // describe the first step only: the workflow's filters bound the run's selection, and a
            // later step also receives every earlier step's output, which those filters never narrow
            // (a later step reading only the type an earlier step writes is a valid chain).
            const workflowAllow = wf.systemConfig?.inputFileFilters?.allow || [];
            const pipelineAllow = pipeline.systemConfig?.inputFileFilters?.allow || [];

            // (1) The workflow's allow-list admits nothing the pipeline accepts.
            if (
                i === 0 &&
                workflowAllow.length > 0 &&
                pipelineAllow.length > 0 &&
                !workflowAllow.some((w) => pipelineAllow.some((p) => patternsMayOverlap(w, p)))
            ) {
                warnings.push(
                    `Pipeline '${ref.pipelineId}' input-file filters may exclude everything the workflow filters allow`
                );
            }

            // (2) The workflow EXCLUDES a type the pipeline accepts. Distinct from (1): the
            // allow-lists can overlap perfectly and an exclude still removes the file afterwards,
            // since exclude is applied second. Separate message because the fix is a different field.
            const suppressed =
                i === 0
                    ? excludedPipelineAllows(
                          wf.systemConfig?.inputFileFilters?.exclude,
                          pipelineAllow
                      )
                    : [];
            if (suppressed.length > 0) {
                const remaining = pipelineAllow.filter((p) => !suppressed.includes(p));
                const detail =
                    remaining.length === 0
                        ? "leaving it no accepted input type"
                        : `leaving it only ${remaining.join(", ")}`;
                warnings.push(
                    `Pipeline '${ref.pipelineId}' accepts ${suppressed.join(", ")} but the ` +
                        `workflow's input-file filters exclude ` +
                        `${suppressed.length === 1 ? "that" : "those"}, ${detail}`
                );
            }
        });

        // Later steps under the runtime model. Launch judges only the first step against the run's
        // selection; from the second step on, a step receives the selection plus every earlier step's
        // output, narrowed by its own filters and arity-checked when the run reaches it
        // (interimPipelineTracking.narrow_next_pipeline_inputs). A later step that reads only the type
        // an earlier step writes is therefore valid. What fails is a single-file later step whose
        // filters admit BOTH the selected type and a type only an earlier step can supply: once an
        // earlier step writes such a file the step is handed two and the run fails at that step.
        // Pipelines declare no output types, so a later-step pattern that cannot match a selectable
        // type stands in for "an earlier step's output", and an open list admits whatever is written.
        // Template overrides are chosen per execution and are not known here.
        const workflowArity = wf.systemConfig?.inputFileArity ?? "one";
        const firstRef = wf.specifiedPipelines[0];
        const firstPipeline = firstRef ? pipelinesById[pipelineRefKey(firstRef)] : undefined;
        if (workflowArity !== "none" && firstPipeline) {
            const selected = selectablePatterns(wf, firstPipeline);
            wf.specifiedPipelines.slice(1).forEach((ref) => {
                const pipeline = pipelinesById[pipelineRefKey(ref)];
                if (!pipeline || (pipeline.systemConfig?.inputFileArity || "one") !== "one") return;
                const message = bothTypesWarning(ref.pipelineId, pipeline, selected);
                if (message) warnings.push(message);
            });
        }
    }

    return { errors, warnings };
}

/**
 * The file types a run's selection can carry, as allow patterns, or `null` when nothing narrows them.
 * Launch judges the selection against the workflow's filters and the FIRST step's only, so those two
 * bound it: the first step's allow list (when it takes files and names types), kept to the patterns
 * the workflow's allow list may admit, else the workflow's own allow list. Patterns either level's
 * exclude list suppresses are dropped (decidable for extension patterns only).
 */
function selectablePatterns(
    wf: Pick<Workflow, "systemConfig">,
    firstPipeline: Pipeline
): string[] | null {
    const workflowFilters = wf.systemConfig?.inputFileFilters || {};
    const firstFilters = consumesInputFiles(firstPipeline)
        ? firstPipeline.systemConfig?.inputFileFilters || {}
        : {};
    const nonBlank = (patterns?: string[]) => (patterns || []).filter((p) => !!(p || "").trim());
    const workflowAllow = isOpenAllowList(workflowFilters.allow)
        ? null
        : nonBlank(workflowFilters.allow);
    const firstAllow = isOpenAllowList(firstFilters.allow) ? null : nonBlank(firstFilters.allow);

    let patterns: string[] | null;
    if (firstAllow && workflowAllow) {
        patterns = firstAllow.filter((p) => workflowAllow.some((w) => patternsMayOverlap(w, p)));
    } else {
        patterns = firstAllow || workflowAllow;
    }
    if (!patterns) return null;
    const excluded = [...(workflowFilters.exclude || []), ...(firstFilters.exclude || [])];
    return patterns.filter((p) => excludedPipelineAllows(excluded, [p]).length === 0);
}

/** Whether two allow patterns certainly name the same type: equal, or the same extension. */
function samePattern(patternA: string, patternB: string): boolean {
    const a = (patternA || "").toLowerCase();
    const b = (patternB || "").toLowerCase();
    if (EXTENSION_PATTERN.test(patternA || "") && EXTENSION_PATTERN.test(patternB || "")) {
        return a.replace(/^\*/, "") === b.replace(/^\*/, "");
    }
    return a === b;
}

/**
 * The warning for one single-file later step, or `null`. It is raised only when the step's filters
 * certainly admit a selectable type AND a type only an earlier step can supply (or any type, for an
 * open allow list), so a glob that merely might overlap does not raise it.
 */
function bothTypesWarning(
    pipelineId: string,
    pipeline: Pipeline,
    selected: string[] | null
): string | null {
    const filters = pipeline.systemConfig?.inputFileFilters || {};
    const allow = (filters.allow || []).filter((p) => !!(p || "").trim());
    const exclude = filters.exclude || [];
    const open = isOpenAllowList(filters.allow);

    let admittedSelected: string[];
    let outputTypes: string;
    if (selected === null) {
        // Any type can be selected, so only a step that admits everything certainly takes the
        // selected file — and then it also takes whatever an earlier step writes.
        if (!open || exclude.some((p) => !!(p || "").trim())) return null;
        admittedSelected = [];
        outputTypes = "any file an earlier step writes";
    } else {
        admittedSelected = selected.filter(
            (p) =>
                excludedPipelineAllows(exclude, [p]).length === 0 &&
                (open || allow.some((q) => samePattern(q, p)))
        );
        if (admittedSelected.length === 0) return null;
        if (open) {
            outputTypes = "any file an earlier step writes";
        } else {
            const beyond = allow.filter((q) => !selected.some((p) => patternsMayOverlap(q, p)));
            if (beyond.length === 0) return null;
            outputTypes = `${beyond.join(", ")}, which only an earlier step can supply`;
        }
    }
    const selectedText = selected === null ? "any type" : admittedSelected.join(", ");
    return (
        `Pipeline '${pipelineId}' takes a single input file, but its filters admit both the run's ` +
        `selected file type (${selectedText}) and ${outputTypes}. A step after the first receives ` +
        `the selection plus every earlier step's output, so when an earlier step writes such a file ` +
        `this step is handed two and the run fails when it reaches the step`
    );
}
