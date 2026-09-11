/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Pipeline, Workflow, PipelineSystemConfig, WorkflowSystemConfig } from "../types";

/** Keys a template's `overrides` may replace on a pipeline's systemConfig. */
const TEMPLATE_OVERRIDABLE_KEYS = [
    "inputFileArity",
    "metadataInputs",
    "assetScope",
    "inputFileFilters",
] as const;

/**
 * A pipeline's systemConfig with the chosen template's `overrides` merged over the overridable keys.
 * Mirrors resolve_effective_pipeline_config in common/workflows/executionValidation.py.
 *
 * Lives here rather than in ExecuteWizard so it is importable without pulling in a React component —
 * a test that mocks the wizard module would otherwise strip this function out from under its callers.
 */
export function resolveEffectivePipelineConfig(
    systemConfig?: PipelineSystemConfig,
    templateOverrides?: Record<string, any>
): PipelineSystemConfig {
    const effective: Record<string, any> = { ...(systemConfig || {}) };
    const overrides = templateOverrides || {};
    TEMPLATE_OVERRIDABLE_KEYS.forEach((key) => {
        if (overrides[key] !== undefined && overrides[key] !== null) {
            effective[key] = overrides[key];
        }
    });
    return effective as PipelineSystemConfig;
}

/**
 * One assetScope selection rule, resolved across the workflow and every step.
 *
 * A selection is offered only when EVERY level accepts it: the run is submitted once and each step is
 * checked against it, so the narrowest level decides. This is the opposite of the metadataInputs
 * default — an OMITTED scope key is NOT a grant, matching `_scope_errors` in the backend, where a scope
 * that does not say whole-asset is allowed rejects a whole-asset selection. A level that declares
 * nothing at all (no assetScope block) is treated as declaring nothing to widen, so it neither grants
 * nor blocks; the workflow gate is what must grant it.
 *
 * A step whose arity is 'none' is skipped: it receives no input files whatever the run selected, so
 * the backend never applies its scope to the selection (`_evaluate` in executionValidation.py assigns
 * it an empty input list and continues before `_scope_errors`). Judging the selection against it would
 * withhold an option the backend accepts.
 *
 * `wholeAsset` is the shorthand the vamsSchema bundles emit for `wholeAssetAllowed`; both spellings are
 * accepted here exactly as the backend accepts them.
 */
function scopeKeyAllowedEverywhere(
    workflowSystemConfig: Record<string, any>,
    effectiveStepConfigs: Record<string, any>[],
    key: "wholeAssetAllowed" | "folderAllowed"
): boolean {
    const read = (scope: Record<string, any> | undefined): boolean | undefined => {
        if (!scope) return undefined;
        if (key === "wholeAssetAllowed") {
            if (scope.wholeAssetAllowed !== undefined) return !!scope.wholeAssetAllowed;
            if (scope.wholeAsset !== undefined) return !!scope.wholeAsset;
            return undefined;
        }
        return scope.folderAllowed === undefined ? undefined : !!scope.folderAllowed;
    };

    // The workflow must grant it: an absent or silent workflow scope is not a grant.
    if (read(workflowSystemConfig.assetScope) !== true) return false;
    // Any file-consuming step that explicitly declines it removes the option.
    return !effectiveStepConfigs.some(
        (cfg) => consumesInputFiles(cfg) && read(cfg?.assetScope) === false
    );
}

/** Whether a step takes input files at all — mirrors the backend's `_arity` default of 'one'. */
function consumesInputFiles(stepConfig?: Record<string, any>): boolean {
    return ((stepConfig || {}).inputFileArity || "one") !== "none";
}

/**
 * What a workflow will actually accept, resolved down the workflow -> pipeline -> template chain.
 *
 * This deliberately does NOT read the backend's `aggregateWorkflowPipelineInputFileFilters`. That
 * value is computed without template overrides (a template is chosen per execution, so the server
 * cannot know it), which makes it fine for browsing a workflow list but wrong for a screen where the
 * user is picking a template. Resolving here means the display can narrow as soon as a template is
 * chosen, and matches what the backend will enforce at launch.
 */

/** Patterns that match every file — an allow list of only these places no restriction. */
const MATCH_EVERYTHING = ["*", "**", "*.*", "/*", "/**"];

/** Mirror of executionValidation.is_open_allow_list: absent, empty, or only match-everything. */
export function isOpenAllowList(allow?: string[]): boolean {
    const patterns = (allow || []).map((p) => (p || "").trim()).filter(Boolean);
    return patterns.length === 0 || patterns.every((p) => MATCH_EVERYTHING.includes(p));
}

/** Dedupe preserving first-seen order, comparing case-insensitively (the matcher is). */
function dedupe(patterns: string[]): string[] {
    const seen = new Set<string>();
    const out: string[] = [];
    (patterns || []).forEach((raw) => {
        const text = (raw || "").trim();
        if (!text) return;
        const key = text.toLowerCase();
        if (seen.has(key)) return;
        seen.add(key);
        out.push(text);
    });
    return out;
}

export type FilterSource = "workflow" | "pipelines" | "none";

export interface ResolvedRestrictions {
    /** Accepted file patterns; empty means every file type is accepted. */
    allow: string[];
    /** Excluded file patterns; empty means nothing is excluded. */
    exclude: string[];
    /** Which level the allow list came from — lets the UI say where the restriction originates. */
    source: FilterSource;
    /** Metadata input types the steps will actually receive. */
    metadataInputs: string[];
    /** The same set as `metadataInputs`, as the canonical config keys rather than display labels. */
    metadataInputKeys: MetadataKey[];
    /** Metadata a pipeline asked for but the workflow gate suppresses (it runs without it). */
    metadataGatedOff: string[];
    /** How many input files the run takes. */
    arity: "none" | "one" | "multi";
    /** Where output goes. */
    outputType: "asset" | "none";
    /** True once every step's template is known, so the resolution is final rather than indicative. */
    templatesResolved: boolean;
    /**
     * Whether a whole-asset ('/') selection is accepted by EVERY level — the workflow, every step, and
     * each step's chosen template overrides. A run is submitted once and every step must accept the
     * selection, so the narrowest level wins.
     */
    wholeAssetAllowed: boolean;
    /** Whether a folder selection is accepted by every level, resolved the same way. */
    folderAllowed: boolean;
}

/** One step of the chain: its pipeline's config plus the overrides of whichever template is chosen. */
export interface StepConfig {
    systemConfig?: PipelineSystemConfig;
    /** The chosen template's `overrides`, when a template has been chosen for this step. */
    templateOverrides?: Record<string, any>;
    /** False when this step's template is not yet known, so overrides may still change the result. */
    templateKnown?: boolean;
}

export const METADATA_LABELS = {
    assetMetadata: "Asset metadata",
    fileMetadata: "File metadata",
    fileAttributes: "File attributes",
    databaseMetadata: "Database metadata",
};

export type MetadataKey = keyof typeof METADATA_LABELS;

/**
 * Every metadata key, widest entity first, so anything derived from this list — the resolved-metadata
 * summary and the forms' toggle rows — reads database -> asset -> file as the containment it describes.
 */
const METADATA_KEYS: MetadataKey[] = [
    "databaseMetadata",
    "assetMetadata",
    "fileMetadata",
    "fileAttributes",
];

/**
 * The value a metadataInputs map carries for a key it OMITS. A stored map may be partial two ways: a
 * record written before a key existed cannot carry it, and the API stores systemConfig wholesale, so a
 * client that sends only the keys it cares about persists exactly those. All four default ON, so a
 * configuration that says nothing about a metadata type gets it. Mirrors METADATA_INPUT_DEFAULTS in
 * common/workflows/executionRecords.py.
 */
export const METADATA_INPUT_DEFAULTS: Record<MetadataKey, boolean> = {
    assetMetadata: true,
    fileMetadata: true,
    fileAttributes: true,
    databaseMetadata: true,
};

/**
 * One metadataInputs toggle, resolving a key the map omits to its builder default. Every read of a
 * stored map goes through this — a form control bound with `|| false` instead would show an omitted
 * key as off and then persist that opt-out on save, silently dropping metadata the run was gathering.
 */
export function metadataEnabled(
    metadataInputs: Record<string, boolean> | undefined,
    key: MetadataKey
) {
    const value = (metadataInputs || {})[key];
    return value === undefined ? METADATA_INPUT_DEFAULTS[key] : !!value;
}

/**
 * Resolve the effective restrictions for a workflow and its steps.
 *
 * Allow resolution follows the chain's precedence, matching the backend:
 *   - a restrictive workflow allow list IS the answer (the outer boundary; no pipeline can widen it);
 *   - an open workflow allow list defers to the pipelines, whose lists are UNIONED, because from a
 *     file's point of view the steps are alternatives — a file any step accepts is a file the workflow
 *     can act on. One open step makes the whole thing open.
 * Excludes union across every level, since an exclusion anywhere removes the file.
 */
export function resolveRestrictions(
    workflowSystemConfig: WorkflowSystemConfig | undefined,
    steps: StepConfig[]
): ResolvedRestrictions {
    const wsc = workflowSystemConfig || {};
    const wfFilters = wsc.inputFileFilters || {};

    // Each step's EFFECTIVE config: its pipeline's, with the chosen template's overrides merged.
    const effective = (steps || []).map((step) =>
        resolveEffectivePipelineConfig(step.systemConfig, step.templateOverrides)
    );

    const excludes = [...(wfFilters.exclude || [])];
    const pipelineAllows: string[] = [];
    effective.forEach((cfg) => {
        pipelineAllows.push(...(cfg.inputFileFilters?.allow || []));
        excludes.push(...(cfg.inputFileFilters?.exclude || []));
    });

    let allow: string[];
    let source: FilterSource;
    if (!isOpenAllowList(wfFilters.allow)) {
        allow = [...(wfFilters.allow || [])];
        source = "workflow";
    } else if (
        effective.length > 0 &&
        effective.every((cfg) => !isOpenAllowList(cfg.inputFileFilters?.allow)) &&
        pipelineAllows.length > 0
    ) {
        allow = pipelineAllows;
        source = "pipelines";
    } else {
        // Nothing restricts, or at least one step accepts anything.
        allow = [];
        source = "none";
    }

    // A metadata type reaches a step only when the workflow gate loads it AND a step asks for it.
    const gate = wsc.metadataInputs || {};
    const metadataInputs: string[] = [];
    const metadataInputKeys: MetadataKey[] = [];
    const metadataGatedOff: string[] = [];
    METADATA_KEYS.forEach((key) => {
        const wanted = effective.some((cfg) => metadataEnabled(cfg.metadataInputs, key));
        const allowed = metadataEnabled(gate, key);
        if (wanted && allowed) {
            metadataInputs.push(METADATA_LABELS[key]);
            metadataInputKeys.push(key);
        }
        if (wanted && !allowed) metadataGatedOff.push(METADATA_LABELS[key]);
    });

    return {
        allow: dedupe(allow),
        exclude: dedupe(excludes),
        source,
        metadataInputs,
        metadataInputKeys,
        metadataGatedOff,
        arity: (wsc.inputFileArity || "one") as ResolvedRestrictions["arity"],
        outputType: (wsc.outputTarget?.locationType ||
            "asset") as ResolvedRestrictions["outputType"],
        templatesResolved: (steps || []).every((s) => s.templateKnown !== false),
        wholeAssetAllowed: scopeKeyAllowedEverywhere(wsc, effective, "wholeAssetAllowed"),
        folderAllowed: scopeKeyAllowedEverywhere(wsc, effective, "folderAllowed"),
    };
}

/**
 * Build the step list for a workflow from its pipeline records, with no template chosen yet.
 * Used by the workflow-picker modal, which shows the chain before templates are selected.
 */
export function stepsFromWorkflow(
    workflow: Pick<Workflow, "specifiedPipelines"> | undefined,
    pipelinesByKey: Record<string, Pipeline>
): StepConfig[] {
    return (workflow?.specifiedPipelines || []).map((ref) => {
        const key = ref.pipelineDatabaseId
            ? `${ref.pipelineDatabaseId}:${ref.pipelineId}`
            : ref.pipelineId;
        const pipeline = pipelinesByKey[key];
        return {
            systemConfig: pipeline?.systemConfig,
            // A step whose pipeline requires a template can still be narrowed by that template's
            // overrides, so the summary is marked indicative rather than final.
            templateKnown: !pipeline?.systemConfig?.requireTemplate,
        };
    });
}

/** One-line summary for a compact surface (the workflow picker). */
export function summarizeRestrictions(r: ResolvedRestrictions): string {
    const parts: string[] = [];
    parts.push(
        r.allow.length === 0
            ? "Any file type"
            : `${r.allow.length} file type${r.allow.length === 1 ? "" : "s"}`
    );
    parts.push(
        r.arity === "none" ? "no input files" : r.arity === "one" ? "1 file" : "1 or more files"
    );
    parts.push(r.outputType === "none" ? "results only" : "writes to an asset");
    return parts.join(" · ");
}
