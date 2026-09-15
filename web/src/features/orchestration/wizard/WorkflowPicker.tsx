/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo, useRef, useState } from "react";
import Callout from "../components/Callout";
import InfoTooltip from "../components/InfoTooltip";
import RestrictionSummary, { PatternHelp, patternHelpText } from "./RestrictionSummary";
import { validateInputSelection } from "./ExecuteWizard";
import { resolveRestrictions, stepsFromWorkflow } from "./resolveRestrictions";
import type { ExecuteInputFile, Pipeline, SpecifiedPipelineRef, Workflow } from "../types";

/** The list key for a workflow: ids repeat across databases, so both parts are needed. */
export const workflowKey = (wf: Pick<Workflow, "databaseId" | "workflowId">) =>
    `${wf.databaseId}:${wf.workflowId}`;

/** The `pipelinesByKey` key of one step: the step's own database when it names one, else the
 *  workflow's. */
export const pipelineKeyFor = (wf: Pick<Workflow, "databaseId">, ref: SpecifiedPipelineRef) =>
    `${ref.pipelineDatabaseId || wf.databaseId}:${ref.pipelineId}`;

/** The distinct, non-empty categories of a workflow's pipelines, in step order. */
export function workflowCategories(
    wf: Pick<Workflow, "databaseId" | "specifiedPipelines">,
    pipelinesByKey: Record<string, Pipeline>
): string[] {
    const seen = new Set<string>();
    (wf.specifiedPipelines || []).forEach((ref) => {
        const category = pipelinesByKey[pipelineKeyFor(wf, ref)]?.category?.trim();
        if (category) seen.add(category);
    });
    return Array.from(seen);
}

/** Category chips shown on a row before the rest fold into a `+N` chip. */
const MAX_CATEGORY_CHIPS = 3;

/**
 * A supplied selection checked against one workflow. Reuses the wizard's own check, so the verdict
 * here and on the Inputs step cannot disagree. Empty when there is no preset selection.
 */
export function selectionErrorsFor(
    workflow: Workflow,
    pipelinesByKey: Record<string, Pipeline>,
    presetInputFiles?: ExecuteInputFile[]
): string[] {
    if (!presetInputFiles?.length) return [];
    const steps = stepsFromWorkflow(workflow, pipelinesByKey);
    return validateInputSelection(
        workflow.systemConfig,
        steps.map((step, index) => ({
            label: `Pipeline "${workflow.specifiedPipelines?.[index]?.pipelineId || index + 1}"`,
            systemConfig: step.systemConfig,
            templateOverrides: step.templateOverrides,
        })),
        presetInputFiles
    );
}

interface WorkflowPickerProps {
    /** Enabled, non-archived, deduplicated. */
    workflows: Workflow[];
    pipelinesByKey: Record<string, Pipeline>;
    selectedKey: string;
    onSelect: (key: string) => void;
    /** Files the launch will run on, when the caller already knows them. */
    presetInputFiles?: ExecuteInputFile[];
}

/**
 * The Workflow step: a search box over a list of workflow rows. Each row carries what the workflow
 * accepts, so the user learns it while choosing rather than after picking files; with a preset
 * selection every row also says whether it can run on it.
 */
const WorkflowPicker: React.FC<WorkflowPickerProps> = ({
    workflows,
    pipelinesByKey,
    selectedKey,
    onSelect,
    presetInputFiles,
}) => {
    const [query, setQuery] = useState("");

    const rows = useMemo(
        () =>
            workflows.map((wf) => ({
                workflow: wf,
                key: workflowKey(wf),
                restrictions: resolveRestrictions(
                    wf.systemConfig,
                    stepsFromWorkflow(wf, pipelinesByKey)
                ),
                categories: workflowCategories(wf, pipelinesByKey),
                errors: selectionErrorsFor(wf, pipelinesByKey, presetInputFiles),
            })),
        [workflows, pipelinesByKey, presetInputFiles]
    );

    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return rows;
        return rows.filter(({ workflow: wf, categories }) =>
            [
                wf.workflowName,
                wf.workflowId,
                wf.databaseId,
                wf.description,
                wf.category,
                ...categories,
            ].some((field) => (field || "").toLowerCase().includes(q))
        );
    }, [rows, query]);

    const selected = rows.find((row) => row.key === selectedKey);
    const hasPreset = !!presetInputFiles && presetInputFiles.length > 0;

    // One row is in the tab order — the selected one while the search still shows it, else the
    // first shown — and the arrow keys walk the rest, so the list costs one Tab however long it is.
    const tabbableKey = filtered.some((row) => row.key === selectedKey)
        ? selectedKey
        : filtered[0]?.key;
    const listRef = useRef<HTMLDivElement>(null);
    const moveFocus = (to: "next" | "prev" | "first" | "last") => {
        const rowEls = Array.from(
            listRef.current?.querySelectorAll<HTMLElement>('[role="option"]') || []
        );
        if (rowEls.length === 0) return;
        const last = rowEls.length - 1;
        const current = rowEls.indexOf(document.activeElement as HTMLElement);
        const next =
            to === "first"
                ? 0
                : to === "last"
                ? last
                : current === -1
                ? to === "next"
                    ? 0
                    : last
                : Math.max(0, Math.min(last, current + (to === "next" ? 1 : -1)));
        rowEls[next].focus();
    };
    const KEY_MOVES: Record<string, "next" | "prev" | "first" | "last"> = {
        ArrowDown: "next",
        ArrowUp: "prev",
        Home: "first",
        End: "last",
    };

    return (
        <div className="space-y-3">
            {hasPreset && (
                <div className="text-sm">
                    <span className="text-text-secondary">
                        Running on {presetInputFiles!.length}{" "}
                        {presetInputFiles!.length === 1 ? "selection" : "selections"}:
                    </span>{" "}
                    <span className="font-mono text-xs text-text-primary">
                        {presetInputFiles!
                            .slice(0, 3)
                            .map((f) => f.relativeFileKey)
                            .join(", ")}
                        {presetInputFiles!.length > 3 && ` +${presetInputFiles!.length - 3} more`}
                    </span>
                </div>
            )}

            <input
                type="text"
                aria-label="Workflow"
                placeholder="Type to search…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="orch-outline w-full rounded border border-border-input bg-surface-input px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-blue-500"
            />

            {/* role="option" rows are direct children of the listbox and hold no control of their
                own: an option's content is presentational, so the pattern detail rides on each row
                as hover text and the one focusable icon for it sits below the list. */}
            <div
                ref={listRef}
                role="listbox"
                aria-label="Workflows"
                onKeyDown={(e) => {
                    const move = KEY_MOVES[e.key];
                    if (move) {
                        e.preventDefault();
                        moveFocus(move);
                    }
                }}
                className="orch-outline max-h-[52vh] overflow-y-auto rounded-lg border border-border-default"
            >
                {filtered.length === 0 ? (
                    <p className="p-3 text-sm text-text-secondary">
                        {query.trim()
                            ? `No workflows match “${query.trim()}”.`
                            : "No workflows available."}
                    </p>
                ) : (
                    filtered.map(
                        ({ workflow: wf, key, restrictions, categories, errors }, index) => {
                            const isSelected = key === selectedKey;
                            const hiddenCategories = categories.slice(MAX_CATEGORY_CHIPS);
                            return (
                                <div
                                    key={key}
                                    role="option"
                                    tabIndex={key === tabbableKey ? 0 : -1}
                                    aria-selected={isSelected}
                                    title={patternHelpText(restrictions)}
                                    onClick={() => onSelect(key)}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter" || e.key === " ") {
                                            e.preventDefault();
                                            onSelect(key);
                                        }
                                    }}
                                    className={`orch-outline cursor-pointer px-3 py-2.5 hover:bg-surface-hover focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-500 ${
                                        index > 0 ? "border-t border-border-default" : ""
                                    } ${isSelected ? "bg-surface-selected" : ""}`}
                                >
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="truncate text-sm font-semibold text-text-primary">
                                            {wf.workflowName || wf.workflowId}
                                        </span>
                                        <span className="rounded-full bg-surface-secondary px-2 py-0.5 text-[11px] text-text-secondary">
                                            {wf.databaseId}
                                        </span>
                                        {/* The categories of the workflow's pipelines, capped so a long
                                        chain does not push the badges off the line. */}
                                        {categories.slice(0, MAX_CATEGORY_CHIPS).map((category) => (
                                            <span
                                                key={category}
                                                data-testid="workflow-category"
                                                className="rounded-full bg-surface-secondary px-2 py-0.5 text-[11px] text-text-secondary"
                                            >
                                                {category}
                                            </span>
                                        ))}
                                        {hiddenCategories.length > 0 && (
                                            <span
                                                data-testid="workflow-category-overflow"
                                                title={hiddenCategories.join(", ")}
                                                className="rounded-full bg-surface-secondary px-2 py-0.5 text-[11px] text-text-secondary"
                                            >
                                                +{hiddenCategories.length}
                                            </span>
                                        )}
                                        {hasPreset && (
                                            <span
                                                className={`ml-auto rounded-full px-2 py-0.5 text-[11px] ${
                                                    errors.length === 0
                                                        ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300"
                                                        : "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300"
                                                }`}
                                            >
                                                {errors.length === 0
                                                    ? "Compatible"
                                                    : "Not compatible"}
                                            </span>
                                        )}
                                    </div>
                                    {wf.description && (
                                        <p className="mt-0.5 line-clamp-2 text-xs text-text-secondary">
                                            {wf.description}
                                        </p>
                                    )}
                                    <div className="mt-1">
                                        <RestrictionSummary
                                            compact
                                            tooltip={false}
                                            restrictions={restrictions}
                                        />
                                    </div>
                                </div>
                            );
                        }
                    )
                )}
            </div>

            {/* The selected workflow's pattern detail, reachable from the keyboard: the rows carry
                it as hover text only. */}
            {selected && (
                <p className="flex flex-wrap items-center gap-1.5 text-xs text-text-secondary">
                    <span>
                        Selected:{" "}
                        <span className="font-medium text-text-primary">
                            {selected.workflow.workflowName || selected.workflow.workflowId}
                        </span>
                    </span>
                    <InfoTooltip
                        label="Which files this workflow accepts"
                        text={<PatternHelp r={selected.restrictions} />}
                    />
                </p>
            )}

            {selected && selected.errors.length > 0 && (
                <Callout
                    tone="error"
                    role="alert"
                    title="This workflow cannot run on the current selection:"
                >
                    <ul className="mt-1 list-inside list-disc">
                        {selected.errors.map((err, i) => (
                            <li key={i}>{err}</li>
                        ))}
                    </ul>
                </Callout>
            )}
        </div>
    );
};

export default WorkflowPicker;
