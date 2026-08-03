/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo, useState } from "react";
import Dialog from "../components/Dialog";
import SearchableSelect from "../components/SearchableSelect";
import ExecuteWizard, { validateInputSelection } from "../wizard/ExecuteWizard";
import RestrictionSummary from "../wizard/RestrictionSummary";
import { resolveRestrictions, stepsFromWorkflow } from "../wizard/resolveRestrictions";
import { btnPrimary, btnSecondary } from "../components/controlStyles";
import { useAllWorkflows, useAllPipelines } from "../api/queries";
import type { ExecuteInputFile, Pipeline, Workflow } from "../types";

interface ExecuteWorkflowModalProps {
    open: boolean;
    onClose: () => void;
    /** Scopes the workflow list; GLOBAL workflows are always offered alongside. */
    databaseId?: string;
    /** Preselects the input asset for the wizard. */
    assetId?: string;
    /**
     * Files the launch should run on, when the caller already knows them (the asset file manager's
     * Automation action). Supplying these lets the picker validate the selection against the chosen
     * workflow immediately, and the wizard opens with the files already filled in.
     */
    presetInputFiles?: ExecuteInputFile[];
}

/**
 * Workflow picker, then the execute wizard.
 *
 * Shared by the Executions board toolbar and the asset file manager's Automation group. When the
 * caller supplies `presetInputFiles`, the picker checks them against the chosen workflow's arity,
 * asset scope, and file filters up front — so an incompatible workflow is rejected here rather than
 * two steps later, which is the point of launching from a known selection.
 */
const ExecuteWorkflowModal: React.FC<ExecuteWorkflowModalProps> = ({
    open,
    onClose,
    databaseId,
    assetId,
    presetInputFiles,
}) => {
    const [selected, setSelected] = useState("");
    const [wizardOpen, setWizardOpen] = useState(false);

    const { data: dbWorkflows = [] } = useAllWorkflows(databaseId);
    // The GLOBAL catalog is fetched separately ONLY when scoped to a database: the unscoped list
    // (`/workflows`) already returns every workflow the caller can see, GLOBAL included. Fetching it
    // again there produced a list with each GLOBAL workflow twice — and duplicate option keys break
    // the picker's list reconciliation, which is why typing in its search appeared to do nothing.
    const { data: globalWorkflows = [] } = useAllWorkflows("GLOBAL", undefined, !!databaseId);
    const allWorkflows = useMemo(() => {
        // Deduplicated defensively as well: a workflow must never appear twice even if both scopes
        // return it.
        const seen = new Set<string>();
        return [...dbWorkflows, ...globalWorkflows].filter((wf) => {
            const key = `${wf.databaseId}:${wf.workflowId}`;
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
        });
    }, [dbWorkflows, globalWorkflows]);

    // Referenced pipelines' systemConfig, needed to resolve what each workflow accepts. Both scopes
    // load because a database workflow may reference GLOBAL pipelines as well as its own.
    const { data: dbPipelines = [] } = useAllPipelines(databaseId, false, !!databaseId);
    const { data: globalPipelines = [] } = useAllPipelines("GLOBAL");
    const pipelinesByKey = useMemo(() => {
        const map: Record<string, Pipeline> = {};
        [...dbPipelines, ...globalPipelines].forEach((p: Pipeline) => {
            map[`${p.databaseId}:${p.pipelineId}`] = p;
        });
        return map;
    }, [dbPipelines, globalPipelines]);

    const options = useMemo(
        () =>
            allWorkflows
                .filter((wf) => wf.enabled && !wf.archived)
                .map((wf) => ({
                    value: `${wf.databaseId}:${wf.workflowId}`,
                    label: wf.workflowName || wf.workflowId,
                    detail: wf.databaseId,
                })),
        [allWorkflows]
    );

    const selectedWorkflow: Workflow | null = useMemo(() => {
        if (!selected) return null;
        const [dbId, wfId] = selected.split(":");
        return allWorkflows.find((wf) => wf.databaseId === dbId && wf.workflowId === wfId) || null;
    }, [selected, allWorkflows]);

    const steps = useMemo(
        () => (selectedWorkflow ? stepsFromWorkflow(selectedWorkflow, pipelinesByKey) : []),
        [selectedWorkflow, pipelinesByKey]
    );

    const restrictions = useMemo(
        () => (selectedWorkflow ? resolveRestrictions(selectedWorkflow.systemConfig, steps) : null),
        [selectedWorkflow, steps]
    );

    // Validate a supplied selection against the chosen workflow. Reuses the wizard's own check, so
    // the verdict here and on the wizard's input step cannot disagree.
    const presetErrors = useMemo(() => {
        if (!selectedWorkflow || !presetInputFiles?.length) return [];
        return validateInputSelection(
            selectedWorkflow.systemConfig,
            steps.map((step, index) => ({
                label: `Pipeline "${
                    selectedWorkflow.specifiedPipelines?.[index]?.pipelineId || index + 1
                }"`,
                systemConfig: step.systemConfig,
                templateOverrides: step.templateOverrides,
            })),
            presetInputFiles
        );
    }, [selectedWorkflow, steps, presetInputFiles]);

    // A workflow that cannot accept the selection must not be carried into the wizard.
    const canContinue = !!selectedWorkflow && presetErrors.length === 0;

    const handleClose = () => {
        setSelected("");
        onClose();
    };

    return (
        <>
            <Dialog
                open={open && !wizardOpen}
                onOpenChange={(next) => !next && handleClose()}
                title="Execute a workflow"
                footer={
                    <>
                        <button onClick={handleClose} className={btnSecondary}>
                            Cancel
                        </button>
                        <button
                            onClick={() => setWizardOpen(true)}
                            disabled={!canContinue}
                            className={btnPrimary}
                        >
                            Continue
                        </button>
                    </>
                }
            >
                {/* Reserve vertical room so the search dropdown opens within the dialog instead of
                    forcing the whole modal to scroll. */}
                <div className="min-h-[22rem]">
                    {presetInputFiles && presetInputFiles.length > 0 && (
                        <div className="mb-3 text-sm">
                            <span className="text-text-secondary">
                                Running on {presetInputFiles.length}{" "}
                                {presetInputFiles.length === 1 ? "selection" : "selections"}:
                            </span>{" "}
                            <span className="font-mono text-xs text-text-primary">
                                {presetInputFiles
                                    .slice(0, 3)
                                    .map((f) => f.relativeFileKey)
                                    .join(", ")}
                                {presetInputFiles.length > 3 &&
                                    ` +${presetInputFiles.length - 3} more`}
                            </span>
                        </div>
                    )}

                    <label className="block">
                        <span className="block text-sm font-medium mb-1 text-text-primary">
                            Workflow
                        </span>
                        <SearchableSelect
                            ariaLabel="Workflow"
                            value={selected}
                            onChange={setSelected}
                            placeholder="Search workflows…"
                            options={options}
                        />
                    </label>

                    {/* What the chosen workflow accepts, so the user learns it here rather than after
                        picking files. Compact: the full breakdown is on the wizard's input step. */}
                    {selectedWorkflow && restrictions && (
                        <div className="mt-3 space-y-1">
                            {selectedWorkflow.description && (
                                <p className="text-sm text-text-secondary">
                                    {selectedWorkflow.description}
                                </p>
                            )}
                            <RestrictionSummary compact restrictions={restrictions} />
                        </div>
                    )}

                    {presetErrors.length > 0 && (
                        <div
                            role="alert"
                            className="mt-3 rounded border border-red-400 bg-red-50 p-2 text-sm text-red-900 dark:border-red-700 dark:bg-red-900/20 dark:text-red-200"
                        >
                            <p className="font-semibold">
                                This workflow cannot run on the current selection:
                            </p>
                            <ul className="list-disc list-inside mt-1">
                                {presetErrors.map((err, i) => (
                                    <li key={i}>{err}</li>
                                ))}
                            </ul>
                        </div>
                    )}
                </div>
            </Dialog>

            {wizardOpen && selectedWorkflow && (
                <ExecuteWizard
                    open={wizardOpen}
                    onClose={() => {
                        setWizardOpen(false);
                        handleClose();
                    }}
                    workflow={selectedWorkflow}
                    databaseId={selectedWorkflow.databaseId}
                    presetAsset={databaseId && assetId ? { databaseId, assetId } : undefined}
                    presetInputFiles={presetInputFiles}
                />
            )}
        </>
    );
};

export default ExecuteWorkflowModal;
