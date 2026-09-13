/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useMemo, useEffect, useRef } from "react";
import Dialog, { DialogFooter } from "../components/Dialog";
import Callout from "../components/Callout";
import { btnPrimary, btnSecondary, btnSuccess } from "../components/controlStyles";
import { useToast, toastErrorMessage } from "../components/ToastProvider";
import WizardInputStage from "./WizardInputStage";
import WizardPipelineStage from "./WizardPipelineStage";
import WizardReviewStage from "./WizardReviewStage";
import WizardRail, { RailStep } from "./WizardRail";
import RequirementsStrip from "./RequirementsStrip";
import { buildRailSteps } from "./railSteps";
import {
    workflowBlockedReason as blockedReasonOf,
    MAX_INPUT_FILES_PER_EXECUTION,
    tooManyInputFilesText,
} from "./reviewBlockers";
import { resolveRestrictions } from "./resolveRestrictions";
import {
    useWorkflow,
    useAllPipelines,
    useExecuteWorkflow,
    usePrefetchPipelineTemplates,
} from "../api/queries";
import { resolvePipelineParams } from "./resolveTemplate";
import type {
    Workflow,
    WorkflowSystemConfig,
    PipelineSystemConfig,
    ExecuteInputFile,
    ExecuteRequest,
    MetadataSourceAsset,
    PipelineExecutionParameters,
} from "../types";

interface ExecuteWizardProps {
    open: boolean;
    onClose: () => void;
    workflow: Workflow;
    databaseId: string;
    presetAsset?: { databaseId: string; assetId: string };
    /**
     * Files to start with, when the caller already knows the selection (the asset file manager's
     * Automation action). Takes precedence over presetAsset's empty seed row: those files ARE the
     * selection, so the input step opens complete and only needs confirming.
     */
    presetInputFiles?: ExecuteInputFile[];
}

/** The wizard's steps and footer, for hosting inside a Dialog another component owns. */
export interface ExecuteWizardBodyProps extends Omit<ExecuteWizardProps, "open"> {
    /** True while the enclosing dialog shows another step; the body stays mounted, footer-less. */
    hidden: boolean;
    /** Steps the enclosing dialog completed before this body's own (the workflow picker). */
    leadingSteps?: RailStep[];
    /** Receives a leading step's id when its rail row is clicked. */
    onJumpTo?: (stepId: string) => void;
}

export interface PipelineStageData {
    pipelineId: string;
    templateId?: string;
    templateName?: string;
    tags: { key: string; value: any }[];
    customTemplateOverride?: string;
    customEditedBody?: string;
    /** The selected template's `overrides` block, merged over the pipeline systemConfig. */
    templateOverrides?: Record<string, any>;
    errors: string[];
    params: any;
    mode?: 1 | 2 | 3 | 4 | 5;
}

// ---------------------------------------------------------------------------
// Input-selection validation — mirrors backend common/workflows/executionValidation.py so an
// invalid selection is reported in the wizard instead of surfacing as a launch-time 400.
// ---------------------------------------------------------------------------

type InputFileFilters = { allow?: string[]; exclude?: string[] };

// The effective-config merge lives in the pure resolveRestrictions module (a component module is the
// wrong home for it: mocking this file in a test would strip the function out from under other
// importers). Re-exported here because existing callers import it from the wizard.
import { resolveEffectivePipelineConfig } from "./resolveRestrictions";

export { resolveEffectivePipelineConfig };

const isWholeAssetKey = (key?: string) => key === "/";

const isFolderKey = (key?: string) => {
    const k = key || "";
    return k.endsWith("/") && k !== "" && k !== "/";
};

/** The two equivalent extension forms, '*.ext' (canonical) and '.ext' (shorthand). */
const isExtensionPattern = (pattern: string): boolean => {
    if (pattern.includes("/")) return false;
    let ext: string;
    if (pattern.startsWith("*.")) ext = pattern.slice(1);
    else if (pattern.startsWith(".")) ext = pattern;
    else return false;
    const body = ext.slice(1);
    return body.length > 0 && /^[a-zA-Z0-9]+$/.test(body);
};

/** fnmatch-equivalent glob: '*' matches any run of characters (separators included), '?' one
 *  character, '[seq]'/'[!seq]' a character class. Everything else is literal. */
const globToRegExp = (pattern: string): RegExp => {
    let out = "";
    let i = 0;
    while (i < pattern.length) {
        const c = pattern[i];
        i += 1;
        if (c === "*") {
            out += ".*";
        } else if (c === "?") {
            out += ".";
        } else if (c === "[") {
            const close = pattern.indexOf("]", i + 1);
            if (close === -1) {
                out += "\\[";
            } else {
                let body = pattern.slice(i, close).replace(/\\/g, "\\\\");
                i = close + 1;
                if (body.startsWith("!")) body = `^${body.slice(1)}`;
                out += `[${body}]`;
            }
        } else {
            out += c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        }
    }
    return new RegExp(`^${out}$`);
};

/** Case-insensitive match of a file key against allow/exclude patterns (extension, glob or exact). */
export function matchesAnyPattern(relativeFileKey: string, patterns?: string[]): boolean {
    const fk = (relativeFileKey || "").toLowerCase();
    const name = fk.replace(/\/+$/, "").split("/").pop() || "";
    for (const pattern of patterns || []) {
        if (!pattern) continue;
        const pat = pattern.toLowerCase();
        if (isExtensionPattern(pattern)) {
            const ext = pat.startsWith("*.") ? pat.slice(1) : pat;
            if (name.endsWith(ext)) return true;
        } else if (globToRegExp(pat).test(fk) || globToRegExp(pat).test(name)) {
            return true;
        } else if (pat === fk) {
            return true;
        }
    }
    return false;
}

const nonExtensionPatterns = (patterns?: string[]): string[] =>
    (patterns || []).filter((p) => p && !isExtensionPattern(p));

/**
 * The subset of files passing an {allow, exclude} filter. Empty allow means allow-all.
 *
 * A whole-asset ('/') or folder selection names a container rather than a file, so an extension
 * pattern cannot describe it: extension patterns are dropped from both lists for those entries while
 * path/name globs and exact keys still apply. An allow list made up only of extension patterns
 * therefore admits a container selection, leaving its admissibility to the assetScope gates.
 */
export function applyInputFileFilters(
    files: ExecuteInputFile[],
    filters?: InputFileFilters
): ExecuteInputFile[] {
    const allow = filters?.allow || [];
    const exclude = filters?.exclude || [];
    return (files || []).filter((file) => {
        const fk = file.relativeFileKey || "";
        const isContainer = fk === "" || isWholeAssetKey(fk) || isFolderKey(fk);
        const entryAllow = isContainer ? nonExtensionPatterns(allow) : allow;
        const entryExclude = isContainer ? nonExtensionPatterns(exclude) : exclude;
        if (entryAllow.length > 0 && !matchesAnyPattern(fk, entryAllow)) return false;
        if (entryExclude.length > 0 && matchesAnyPattern(fk, entryExclude)) return false;
        return true;
    });
}

/** The registration shorthand `wholeAsset` folded into the canonical key; an explicit canonical
 *  key wins. */
const normalizeAssetScope = (
    assetScope?: Record<string, boolean>
): Record<string, boolean | undefined> => {
    const scope: Record<string, boolean | undefined> = { ...(assetScope || {}) };
    if ("wholeAsset" in scope) {
        const value = scope.wholeAsset;
        delete scope.wholeAsset;
        if (!("wholeAssetAllowed" in scope)) scope.wholeAssetAllowed = value;
    }
    return scope;
};

/**
 * Asset-span and whole-asset/folder checks for one assetScope, prefixed with `subject`.
 * `declaredOnly` limits the checks to the keys the scope actually declares — a pipeline's assetScope
 * may be a partial declaration layered under the workflow gate, so an omitted key defers to the
 * workflow rather than denying.
 */
function scopeErrors(
    assetScope: Record<string, boolean> | undefined,
    inputs: ExecuteInputFile[],
    subject: string,
    declaredOnly = false
): string[] {
    const scope = normalizeAssetScope(assetScope);
    const declared = (key: string) => !declaredOnly || key in scope;
    const entries = inputs || [];
    const assetIds = new Set(entries.filter((f) => f.assetId).map((f) => f.assetId));
    const messages: string[] = [];

    if (declared("singleAssetOnly") && scope.singleAssetOnly && assetIds.size > 1) {
        messages.push(`${subject} allows a single asset only, but inputs span multiple assets.`);
    }
    if (declared("crossAssetAllowed") && !scope.crossAssetAllowed && assetIds.size > 1) {
        messages.push(
            `${subject} does not allow cross-asset inputs, but inputs span multiple assets.`
        );
    }
    if (
        declared("wholeAssetAllowed") &&
        !scope.wholeAssetAllowed &&
        entries.some((f) => isWholeAssetKey(f.relativeFileKey))
    ) {
        messages.push(`${subject} does not allow whole-asset ('/') selection.`);
    }
    if (
        declared("folderAllowed") &&
        !scope.folderAllowed &&
        entries.some((f) => isFolderKey(f.relativeFileKey))
    ) {
        messages.push(`${subject} does not allow folder selection.`);
    }
    return messages;
}

const arityViolation = (arity: string, count: number): string | null => {
    if (arity === "none") return count > 0 ? "expects no input files" : null;
    if (arity === "one") {
        if (count === 0) return "requires exactly one input file but none were provided";
        if (count > 1) return "accepts a single input file but multiple were provided";
        return null;
    }
    if (arity === "multi") {
        return count === 0 ? "requires at least one input file but none were provided" : null;
    }
    return null;
};

export interface PipelineInputConstraints {
    label: string;
    systemConfig?: PipelineSystemConfig;
    templateOverrides?: Record<string, any>;
    /** False while this step requires a template that has not been chosen yet, so its overrides may
     *  still narrow the resolution. */
    templateKnown?: boolean;
}

/**
 * Workflow-level and per-pipeline checks on the selected input files. Returns the human-readable
 * errors the launch would otherwise fail on, plus the incomplete-row check the request model
 * enforces (an asset and a file must both be chosen).
 */
export function validateInputSelection(
    workflowSystemConfig: WorkflowSystemConfig | undefined,
    pipelineConstraints: PipelineInputConstraints[],
    inputFiles: ExecuteInputFile[]
): string[] {
    const errors: string[] = [];
    const wsc = workflowSystemConfig || {};
    const scope = wsc.assetScope || {};
    const inputs = inputFiles || [];

    // Rows the request model would reject outright.
    if (inputs.some((f) => !f.assetId)) {
        errors.push("Every input row needs an asset.");
    }
    if (inputs.some((f) => f.assetId && !f.relativeFileKey)) {
        errors.push("Every input row needs a file selection.");
    }
    if (inputs.length > MAX_INPUT_FILES_PER_EXECUTION) {
        errors.push(tooManyInputFilesText(inputs.length));
    }

    const workflowArityError = arityViolation(wsc.inputFileArity || "one", inputs.length);
    if (workflowArityError) {
        errors.push(`Workflow ${workflowArityError}.`);
    }

    // Asset span. The assetScope accepts the canonical `*Allowed` keys and the `wholeAsset` shorthand.
    errors.push(...scopeErrors(scope, inputs, "Workflow"));

    // Workflow input filters.
    const wfFilters = wsc.inputFileFilters || {};
    if (
        (wfFilters.allow?.length || wfFilters.exclude?.length) &&
        applyInputFileFilters(inputs, wfFilters).length !== inputs.length
    ) {
        errors.push("One or more input files fail the workflow input-file filters.");
    }

    // The workflow gate is the outer boundary of what an execution may carry, so each pipeline is
    // judged against the files the WORKFLOW admits rather than the raw selection. Mirrors the
    // backend's two-stage order in common/workflows/executionValidation.py; judging on the raw list
    // both invents errors (a pipeline blamed for a file the workflow already dropped) and hides them
    // (a pipeline's arity satisfied by a file that never reaches it).
    const workflowInputs = applyInputFileFilters(inputs, wfFilters);

    // Per-pipeline effective config (the chosen template's overrides merged over the pipeline's).
    pipelineConstraints.forEach(({ label, systemConfig, templateOverrides }) => {
        const effective = resolveEffectivePipelineConfig(systemConfig, templateOverrides);
        const arity = effective.inputFileArity || "one";
        // A 'none' pipeline never consumes files, whatever the workflow selected.
        if (arity === "none") return;

        const pipelineInputs = applyInputFileFilters(workflowInputs, effective.inputFileFilters);
        if (workflowInputs.length > 0 && pipelineInputs.length === 0) {
            errors.push(
                `${label} requires input files but its input-file filters exclude all selected inputs.`
            );
            return;
        }
        // Which filter emptied the list decides the message: naming the workflow's filters when they
        // are the cause points at the actual misconfiguration.
        if (inputs.length > 0 && workflowInputs.length === 0) {
            errors.push(
                `${label} requires input files but the workflow's input-file filters exclude every ` +
                    `selected input, so no file reaches it.`
            );
            return;
        }
        errors.push(...scopeErrors(effective.assetScope, pipelineInputs, label, true));
        const pipelineArityError = arityViolation(arity, pipelineInputs.length);
        if (pipelineArityError) {
            errors.push(`${label} ${pipelineArityError}.`);
        }
    });

    return errors;
}

/**
 * The individual errors inside a rejection message. A backend 400 may carry a LIST of reasons — one
 * per pipeline of a multi-step launch — which `apiClient` flattens into newline-joined text, so the
 * lines are split back out and rendered as a list. Anything else yields a single line.
 */
export function launchErrorLines(message: string): string[] {
    return message
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
}

export const ExecuteWizardBody: React.FC<ExecuteWizardBodyProps> = ({
    onClose,
    workflow,
    databaseId,
    presetAsset,
    presetInputFiles,
    hidden,
    leadingSteps,
    onJumpTo,
}) => {
    const toast = useToast();
    const { data: workflowData, isLoading: workflowLoading } = useWorkflow(
        workflow.databaseId,
        workflow.workflowId
    );
    const effectiveWorkflow = workflowData || workflow;

    // Fetch all pipelines for this workflow
    const pipelineIds = effectiveWorkflow.specifiedPipelines.map((p) => p.pipelineId);
    const pipelineDbIds = effectiveWorkflow.specifiedPipelines.map(
        (p) => p.pipelineDatabaseId || databaseId
    );
    // Archived pipelines are included so an existing workflow's reference to one still resolves —
    // otherwise an archived step is indistinguishable from a deleted one and reports "not found",
    // which names neither the real reason nor the way to fix it (mirrors WorkflowBuilder).
    const { data: allPipelines, isLoading: pipelinesLoading } = useAllPipelines(undefined, true);

    // The workflow definition (and thus its pipeline references) and the pipeline catalog both
    // load asynchronously. Until both resolve we cannot know the pipeline list, so the wizard shows
    // a loading state rather than prematurely rendering "no pipelines"/"pipeline not found".
    const dataLoading = workflowLoading || pipelinesLoading || !allPipelines;

    // Aligned 1:1 with `specifiedPipelines`, holding `undefined` where a reference did not resolve.
    // Three consumers read this positionally (the step labels, renderStage and WizardReviewStage), so
    // compacting it would slide every later step onto the previous one's record — labelling step 2
    // with its own id while handing it step 3's templates and systemConfig.
    const pipelines = useMemo(() => {
        if (!allPipelines) return [];
        return pipelineIds.map((id, idx) => {
            const dbId = pipelineDbIds[idx];
            return allPipelines.find((p) => p.pipelineId === id && p.databaseId === dbId);
        });
    }, [allPipelines, pipelineIds, pipelineDbIds]);

    const executeWorkflow = useExecuteWorkflow();

    // Input stage data. When launched from an asset (presetAsset), seed the first input row with
    // that database+asset so the user only has to pick the file(s) — but the asset is NOT locked to
    // the whole asset: the file remains selectable (and, for a whole-asset-disallowed pipeline, must
    // be a specific file). relativeFileKey starts empty so the file picker requires an explicit
    // choice rather than silently defaulting to the whole asset.
    // A supplied selection wins: the caller resolved the actual files, so there is nothing to pick.
    const [inputFiles, setInputFiles] = useState<ExecuteInputFile[]>(
        presetInputFiles && presetInputFiles.length > 0
            ? presetInputFiles
            : presetAsset
            ? [
                  {
                      databaseId: presetAsset.databaseId,
                      assetId: presetAsset.assetId,
                      relativeFileKey: "",
                  },
              ]
            : []
    );
    // Metadata sources: entities the run reads stored metadata from. Kept in their OWN state (never
    // folded into inputFiles) because they are not input files — they carry no file key, take no part
    // in arity or the input-file filters, and travel in their own request fields.
    const [metadataSourceAssets, setMetadataSourceAssets] = useState<MetadataSourceAsset[]>([]);
    const [metadataSourceDatabaseId, setMetadataSourceDatabaseId] = useState<string | undefined>(
        undefined
    );
    const [outputAssetId, setOutputAssetId] = useState<string | undefined>(undefined);
    const [outputDatabaseId, setOutputDatabaseId] = useState<string | undefined>(undefined);
    // undefined = untouched (still eligible for the workflow's default); a string is the user's own
    // choice, including "" which deliberately means the asset root.
    const [outputPathPrefix, setOutputPathPrefix] = useState<string | undefined>(undefined);
    const outputPathPrefixSeeded = useRef(false);

    // Pipeline stage data (one entry per pipeline)
    const [pipelineData, setPipelineData] = useState<Record<string, PipelineStageData>>({});

    // Current stage
    const [currentStageId, setCurrentStageId] = useState<string>("input");

    // Readiness gating for the Inputs step. `inputsTouched` flips only from DOM events (a single
    // capture handler on the step root), never from the effects that seed the prefix, drop
    // arity-none rows or auto-fill the output asset; `nextBlockedOnInputs` flips when Next is
    // pressed with unmet requirements. Both reset with the body (keyed remount).
    const [inputsTouched, setInputsTouched] = useState(false);
    const [nextBlockedOnInputs, setNextBlockedOnInputs] = useState(false);

    // Launch outcome. An error keeps the wizard open with the server message; warnings mean the run
    // launched but has caveats worth reading before the dialog closes.
    const [launchError, setLaunchError] = useState<string | null>(null);
    const [launchWarnings, setLaunchWarnings] = useState<string[]>([]);

    // Compute offending (disabled/archived) pipelines
    const offendingPipelines = useMemo(() => {
        const offenders: Array<{ pipelineId: string; pipelineName: string; reason: string }> = [];
        effectiveWorkflow.specifiedPipelines.forEach((ref) => {
            const pipeline = pipelines.find(
                (p) =>
                    p?.pipelineId === ref.pipelineId &&
                    p?.databaseId === (ref.pipelineDatabaseId || databaseId)
            );
            if (!pipeline) {
                offenders.push({
                    pipelineId: ref.pipelineId,
                    pipelineName: ref.pipelineId,
                    reason: "not found",
                });
            } else if (pipeline.archived) {
                offenders.push({
                    pipelineId: ref.pipelineId,
                    pipelineName: pipeline.pipelineName,
                    reason: "archived",
                });
            } else if (!pipeline.enabled) {
                offenders.push({
                    pipelineId: ref.pipelineId,
                    pipelineName: pipeline.pipelineName,
                    reason: "disabled",
                });
            }
        });
        return offenders;
    }, [effectiveWorkflow.specifiedPipelines, pipelines, databaseId]);

    // Compute validation errors for all pipelines
    const validationErrors = useMemo(() => {
        const errors: Record<string, string[]> = {};

        effectiveWorkflow.specifiedPipelines.forEach((ref) => {
            const compositeKey = `${ref.pipelineDatabaseId || databaseId}:${ref.pipelineId}`;
            const data = pipelineData[compositeKey];
            if (data && data.errors) {
                errors[compositeKey] = data.errors;
            } else {
                errors[compositeKey] = data?.templateId ? [] : ["Template not selected"];
            }
        });

        return errors;
    }, [effectiveWorkflow.specifiedPipelines, pipelineData, databaseId]);

    // Warm every pipeline's template list (and its already-chosen template's detail) as soon as the
    // pipeline list is known, which is while the user is still on the Input step. Each pipeline step
    // otherwise started its own fetch on arrival and rendered empty for seconds with no indication it
    // was loading. The default template id is included because a step renders its form from the
    // single-template detail, not the list — without it, arriving at an already-configured step still
    // waited on a second serial request.
    const templatePrefetchTargets = useMemo(
        () =>
            effectiveWorkflow.specifiedPipelines.map((ref) => {
                const pipelineDatabaseId = ref.pipelineDatabaseId || databaseId;
                const compositeKey = `${pipelineDatabaseId}:${ref.pipelineId}`;
                return {
                    databaseId: pipelineDatabaseId,
                    pipelineId: ref.pipelineId,
                    // Whatever the step would preselect: the run's own choice when revisiting,
                    // otherwise the workflow ref's default.
                    defaultTemplateId:
                        pipelineData[compositeKey]?.templateId ||
                        ref.defaultTemplateId ||
                        undefined,
                };
            }),
        [effectiveWorkflow.specifiedPipelines, pipelineData, databaseId]
    );
    usePrefetchPipelineTemplates(templatePrefetchTargets);

    // When the workflow allows output override and the selected inputs span more than one asset,
    // the output asset cannot be inferred from a single input asset, so it must be chosen explicitly
    // before launch. (Results-only workflows write no asset output, so this never applies.)
    const isResultsOnly = effectiveWorkflow.systemConfig?.outputTarget?.locationType === "none";

    // Pre-fill the output path prefix with the workflow's stored default, so the form shows the
    // layout a run will actually get instead of an empty box. The stored value is UNRESOLVED, so what
    // is shown (and sent) still carries its {{tag}} placeholders — the backend resolves them at
    // launch. Seeded once: an edit, including clearing the field, is never overwritten.
    useEffect(() => {
        if (outputPathPrefixSeeded.current || !workflowData) return;
        outputPathPrefixSeeded.current = true;
        const stored = workflowData.systemConfig?.defaultOutputFileBaseExecutionPathExtension || "";
        if (stored) setOutputPathPrefix(stored);
    }, [workflowData]);
    const allowOutputOverride =
        effectiveWorkflow.systemConfig?.outputTarget?.allowOverride || false;
    const distinctInputAssetCount = React.useMemo(
        () =>
            new Set(inputFiles.filter((f) => f.assetId).map((f) => `${f.databaseId}:${f.assetId}`))
                .size,
        [inputFiles]
    );
    const outputAssetMissing =
        !isResultsOnly && allowOutputOverride && distinctInputAssetCount > 1 && !outputAssetId;

    // A 'none'-arity workflow consumes no files, so any seeded input row (presetAsset launch) is
    // dropped once the workflow definition resolves — the Input step offers no way to remove it and
    // the backend rejects a request that carries one.
    const workflowArity = effectiveWorkflow.systemConfig?.inputFileArity || "one";
    React.useEffect(() => {
        if (workflowArity === "none" && inputFiles.length > 0) {
            setInputFiles([]);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [workflowArity, inputFiles.length]);

    // Effective per-pipeline constraints for the input-selection check: the pipeline's own
    // systemConfig with the selected template's overrides merged over the overridable keys.
    const pipelineInputConstraints = useMemo(
        () =>
            effectiveWorkflow.specifiedPipelines.map((ref) => {
                // Matched on identity rather than on the index into `pipelines`, so this reference
                // carries its own pipeline's systemConfig whatever the catalogue returned.
                const pipeline = pipelines.find(
                    (p) =>
                        p?.pipelineId === ref.pipelineId &&
                        p?.databaseId === (ref.pipelineDatabaseId || databaseId)
                );
                const compositeKey = `${ref.pipelineDatabaseId || databaseId}:${ref.pipelineId}`;
                return {
                    label: `Pipeline "${pipeline?.pipelineName || ref.pipelineId}"`,
                    systemConfig: pipeline?.systemConfig,
                    templateOverrides: pipelineData[compositeKey]?.templateOverrides,
                    // A require-template step with nothing chosen yet can still be narrowed by that
                    // template's overrides, so the Input step's summary says it is indicative rather
                    // than presenting the pipeline's own rules as the final answer.
                    templateKnown:
                        !pipeline?.systemConfig?.requireTemplate ||
                        !!pipelineData[compositeKey]?.templateId,
                };
            }),
        [effectiveWorkflow.specifiedPipelines, pipelines, pipelineData, databaseId]
    );

    // A disabled or archived workflow cannot run whatever is selected. The Inputs step is then its
    // banner, with no picker to act on, so the selection rules are not raised on top of it — the
    // workflow's state is the one blocker the rail, the Review list and the Launch gate report.
    const workflowBlockedReason = blockedReasonOf(effectiveWorkflow);

    // Workflow + per-pipeline arity/scope/filter checks on the selection, so an invalid selection is
    // reported here rather than as a launch-time 400.
    const inputSelectionErrors = useMemo(
        () =>
            workflowBlockedReason
                ? []
                : validateInputSelection(
                      effectiveWorkflow.systemConfig,
                      pipelineInputConstraints,
                      inputFiles
                  ),
        [
            workflowBlockedReason,
            effectiveWorkflow.systemConfig,
            pipelineInputConstraints,
            inputFiles,
        ]
    );

    const hasValidationErrors =
        !!workflowBlockedReason ||
        Object.values(validationErrors).some((errs) => errs.length > 0) ||
        offendingPipelines.length > 0 ||
        inputSelectionErrors.length > 0 ||
        outputAssetMissing;

    // What the workflow accepts, resolved through every step's chosen template — the strip under
    // the title, shown from Inputs onward (the Workflow step's rows carry the same facts).
    const restrictions = useMemo(
        () =>
            resolveRestrictions(
                effectiveWorkflow.systemConfig,
                pipelineInputConstraints.map((c) => ({
                    systemConfig: c.systemConfig,
                    templateOverrides: c.templateOverrides,
                    templateKnown: c.templateKnown,
                }))
            ),
        [effectiveWorkflow.systemConfig, pipelineInputConstraints]
    );

    // Step list: Inputs -> Pipeline1 -> Pipeline2 -> ... -> Review, each with its rail chip.
    const steps = useMemo(
        () =>
            buildRailSteps({
                specifiedPipelines: effectiveWorkflow.specifiedPipelines,
                pipelines,
                databaseId,
                pipelineData,
                offendingPipelines,
                inputSelectionErrors,
                outputAssetMissing,
                workflowBlockedReason,
            }),
        [
            effectiveWorkflow.specifiedPipelines,
            pipelines,
            databaseId,
            pipelineData,
            offendingPipelines,
            inputSelectionErrors,
            outputAssetMissing,
            workflowBlockedReason,
        ]
    );
    const currentIndex = steps.findIndex((s) => s.id === currentStageId);

    const handleNext = () => {
        if (currentStageId === "input" && inputSelectionErrors.length > 0) {
            setNextBlockedOnInputs(true);
        }
        if (currentIndex < steps.length - 1) {
            setCurrentStageId(steps[currentIndex + 1].id);
        }
    };

    const handleBack = () => {
        if (currentIndex > 0) {
            setCurrentStageId(steps[currentIndex - 1].id);
        }
    };

    // Rail rows and Review "Edit" links jump to one of this body's steps; a leading step belongs
    // to the enclosing dialog.
    const jumpTo = (stepId: string) => {
        if (steps.some((s) => s.id === stepId)) setCurrentStageId(stepId);
        else onJumpTo?.(stepId);
    };

    const handleLaunch = async () => {
        setLaunchError(null);
        setLaunchWarnings([]);

        // Build ExecuteRequest
        const pipelineExecutionParameters: Record<string, PipelineExecutionParameters> = {};

        effectiveWorkflow.specifiedPipelines.forEach((ref, idx) => {
            const compositeKey = `${ref.pipelineDatabaseId || databaseId}:${ref.pipelineId}`;
            const data = pipelineData[compositeKey] || {
                pipelineId: ref.pipelineId,
                tags: [],
                errors: [],
                params: {},
            };

            // Use resolved params from the stage (already handles customEditedBody -> customTemplateOverride for mode 5)
            // Backend keys by pipelineId only, so map composite key to pipelineId for API payload
            pipelineExecutionParameters[ref.pipelineId] = data.params;
        });

        const body: ExecuteRequest = {
            inputFiles,
            outputAssetId,
            outputDatabaseId,
            pipelineExecutionParameters,
            triggerType: "manual",
        };
        if (outputPathPrefix !== undefined) {
            body.outputFileBaseExecutionPathExtension = outputPathPrefix;
        }
        // Metadata sources travel in their own fields. Sent only when complete: a half-filled picker
        // row (a database chosen, no asset yet) is not a selection, and the request model rejects it.
        const completeSourceAssets = metadataSourceAssets.filter((s) => s.databaseId && s.assetId);
        if (completeSourceAssets.length > 0) {
            body.metadataSourceAssets = completeSourceAssets;
        }
        if (metadataSourceDatabaseId) {
            body.metadataSourceDatabaseId = metadataSourceDatabaseId;
        }

        try {
            const result = await executeWorkflow.mutateAsync({
                workflowDatabaseId: effectiveWorkflow.databaseId,
                workflowId: effectiveWorkflow.workflowId,
                body,
            });

            // Warnings are non-blocking: the run has launched, so they are shown in place and the
            // wizard stays open until the user dismisses it.
            const warnings =
                result &&
                typeof result === "object" &&
                "warnings" in result &&
                Array.isArray((result as any).warnings)
                    ? ((result as any).warnings as string[])
                    : [];

            if (warnings.length > 0) {
                // The run HAS launched; the warnings are shown in place. A toast also confirms the
                // launch, because the wizard staying open reads like nothing happened.
                setLaunchWarnings(warnings);
                toast.warning("Execution started", { description: warnings[0] });
                return;
            }

            // The wizard closes on success, so the toast is the only confirmation the run started.
            toast.success("Execution started", {
                description: `${
                    effectiveWorkflow.workflowName || effectiveWorkflow.workflowId
                } was launched.`,
            });
            onClose();
        } catch (err) {
            // Keep the message inline on the Review step (next to the Launch button that failed) AND
            // raise a toast, so it is visible even if the user has scrolled away from the banner.
            const message = toastErrorMessage(err, "Execution failed.");
            setLaunchError(message);
            // The toast is a single line of text, so several reasons are joined with punctuation —
            // the line breaks the inline panel renders as list items would collapse to spaces here
            // and run the reasons together. The panel carries the readable form.
            toast.error("Execution failed", {
                description: launchErrorLines(message).join("; ") || message,
            });
        }
    };

    const markInputsTouched = () => {
        if (!inputsTouched) setInputsTouched(true);
    };

    const renderStage = () => {
        if (currentStageId === "input") {
            const showReadiness =
                inputSelectionErrors.length > 0 && (inputsTouched || nextBlockedOnInputs);
            return (
                // One capture handler pair marks the step as interacted with; effects that write
                // input state do not go through here, so a preset launch opens clean.
                <div
                    className="space-y-4"
                    onChangeCapture={markInputsTouched}
                    onClickCapture={markInputsTouched}
                >
                    {showReadiness && (
                        <Callout tone="info" aria-live="polite" title="To continue">
                            <ul className="list-disc list-inside mt-1">
                                {inputSelectionErrors.map((err, idx) => (
                                    <li key={idx}>{err}</li>
                                ))}
                            </ul>
                        </Callout>
                    )}
                    <WizardInputStage
                        workflow={effectiveWorkflow}
                        databaseId={databaseId}
                        presetAsset={presetAsset}
                        showPresetHint={
                            !!presetAsset && !(presetInputFiles && presetInputFiles.length > 0)
                        }
                        inputFiles={inputFiles}
                        metadataSourceAssets={metadataSourceAssets}
                        metadataSourceDatabaseId={metadataSourceDatabaseId}
                        outputAssetId={outputAssetId}
                        outputDatabaseId={outputDatabaseId}
                        outputPathPrefix={outputPathPrefix}
                        onInputFilesChange={setInputFiles}
                        onMetadataSourceAssetsChange={setMetadataSourceAssets}
                        onMetadataSourceDatabaseIdChange={setMetadataSourceDatabaseId}
                        onOutputAssetIdChange={setOutputAssetId}
                        onOutputDatabaseIdChange={setOutputDatabaseId}
                        onOutputPathPrefixChange={setOutputPathPrefix}
                        offendingPipelines={offendingPipelines}
                        pipelineConstraints={pipelineInputConstraints}
                    />
                </div>
            );
        }

        if (currentStageId === "review") {
            return (
                <WizardReviewStage
                    workflow={effectiveWorkflow}
                    databaseId={databaseId}
                    pipelines={pipelines}
                    pipelineData={pipelineData}
                    inputFiles={inputFiles}
                    metadataSourceAssets={metadataSourceAssets}
                    metadataSourceDatabaseId={metadataSourceDatabaseId}
                    outputAssetId={outputAssetId}
                    outputDatabaseId={outputDatabaseId}
                    outputPathPrefix={outputPathPrefix}
                    validationErrors={validationErrors}
                    inputSelectionErrors={inputSelectionErrors}
                    outputAssetMissing={outputAssetMissing}
                    offendingPipelines={offendingPipelines}
                    workflowBlockedReason={workflowBlockedReason}
                    onEdit={jumpTo}
                />
            );
        }

        // Pipeline stage
        const pipelineIndex = parseInt(currentStageId.replace("pipeline-", ""), 10);
        const pipeline = pipelines[pipelineIndex];
        const ref = effectiveWorkflow.specifiedPipelines[pipelineIndex];

        if (!pipeline) {
            return <div className="text-vams-error">Pipeline not found</div>;
        }

        const compositeKey = `${ref.pipelineDatabaseId || databaseId}:${ref.pipelineId}`;

        return (
            // Key by the composite pipeline key so switching between pipeline steps mounts a FRESH
            // stage instance — its local template/tag/override state must never bleed across
            // pipelines (each pipeline's config is independent).
            <WizardPipelineStage
                key={compositeKey}
                workflow={effectiveWorkflow}
                pipeline={pipeline}
                pipelineRef={ref}
                data={pipelineData[compositeKey]}
                onChange={(data) => {
                    setPipelineData((prev) => ({
                        ...prev,
                        [compositeKey]: data,
                    }));
                }}
            />
        );
    };

    const launched = launchWarnings.length > 0;
    const railSteps = [...(leadingSteps || []), ...steps];

    // While workflow/pipeline data is still loading, the body is a spinner with no footer, so the
    // user cannot step through stages that have no data yet. Once the run has launched with
    // warnings the only remaining action is closing the dialog. Nothing renders into the footer
    // while another step of the enclosing dialog is showing.
    const footer = launched ? (
        <button onClick={onClose} className={btnPrimary}>
            Close
        </button>
    ) : (
        <>
            {currentIndex > 0 && (
                <button onClick={handleBack} className={btnSecondary}>
                    Back
                </button>
            )}
            {currentIndex < steps.length - 1 && (
                <button onClick={handleNext} className={btnPrimary}>
                    Next
                </button>
            )}
            {currentIndex === steps.length - 1 && (
                <button
                    onClick={handleLaunch}
                    disabled={executeWorkflow.isPending || hasValidationErrors}
                    className={btnSuccess}
                >
                    {executeWorkflow.isPending ? "Launching..." : "Launch"}
                </button>
            )}
        </>
    );

    return (
        <>
            {dataLoading ? (
                <div className="flex items-center justify-center min-h-[400px]">
                    <div className="text-center">
                        <div className="inline-block animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 dark:border-blue-400 mb-3" />
                        <p className="text-text-secondary">Loading workflow pipelines…</p>
                    </div>
                </div>
            ) : launched ? (
                <Callout tone="warning">
                    <strong>Execution launched with warnings:</strong>
                    <ul className="list-disc list-inside mt-2">
                        {launchWarnings.map((warning, idx) => (
                            <li key={idx}>{warning}</li>
                        ))}
                    </ul>
                </Callout>
            ) : (
                <div className="space-y-4">
                    <RequirementsStrip restrictions={restrictions} />
                    <div className="flex flex-col gap-4 md:flex-row md:gap-6">
                        <WizardRail
                            steps={railSteps}
                            currentId={currentStageId}
                            onJumpTo={jumpTo}
                        />
                        <div className="min-w-0 flex-1 space-y-4">
                            {renderStage()}
                            {launchError && (
                                <Callout tone="error" role="alert">
                                    <strong>Execution failed:</strong>
                                    {/* One entry per rejection reason — a multi-step launch is
                                        rejected per pipeline, and run together the reasons read as
                                        one sentence. */}
                                    <ul className="list-disc list-inside mt-2">
                                        {launchErrorLines(launchError).map((line, idx) => (
                                            <li key={idx}>{line}</li>
                                        ))}
                                    </ul>
                                </Callout>
                            )}
                        </div>
                    </div>
                </div>
            )}
            {!hidden && !dataLoading && <DialogFooter>{footer}</DialogFooter>}
        </>
    );
};

/**
 * The execute wizard in its own dialog. Kept for callers that already hold the workflow; the
 * Executions board and the file manager compose `ExecuteWizardBody` behind a workflow picker in
 * `ExecuteWorkflowModal`.
 */
const ExecuteWizard: React.FC<ExecuteWizardProps> = ({ open, onClose, workflow, ...rest }) => (
    <Dialog open={open} onOpenChange={onClose} size="lg" title={`Execute ${workflow.workflowName}`}>
        <ExecuteWizardBody onClose={onClose} workflow={workflow} hidden={false} {...rest} />
    </Dialog>
);

export default ExecuteWizard;
