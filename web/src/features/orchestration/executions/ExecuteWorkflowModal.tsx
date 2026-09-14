/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo, useState } from "react";
import Dialog from "../components/Dialog";
import Callout from "../components/Callout";
import { ExecuteWizardBody } from "../wizard/ExecuteWizard";
import WizardRail, { RailStep } from "../wizard/WizardRail";
import WorkflowPicker, {
    pipelineKeyFor,
    selectionErrorsFor,
    workflowKey,
} from "../wizard/WorkflowPicker";
import { btnPrimary, btnSecondary } from "../components/controlStyles";
import { useAllWorkflows, useAllPipelines, useWorkflow } from "../api/queries";
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
     * Automation action). Supplying these lets the picker validate the selection against each
     * workflow immediately, and the wizard opens with the files already filled in.
     */
    presetInputFiles?: ExecuteInputFile[];
    /**
     * The workflow to run, when the caller already knows it (a workflow card's Execute action, a
     * workflow-scoped Executions board). The Workflow step is omitted. Fetched directly rather than
     * looked up in the enabled-only list, so a disabled workflow still opens and says why it cannot run.
     */
    presetWorkflow?: { databaseId: string; workflowId: string };
}

/**
 * The execute dialog: the workflow picker as its first step, then the wizard's steps, all in one
 * dialog with one step rail. Shared by the Executions board toolbar, the asset file manager's
 * Automation group and the Workflows page.
 */
const ExecuteWorkflowModal: React.FC<ExecuteWorkflowModalProps> = ({
    open,
    onClose,
    databaseId,
    assetId,
    presetInputFiles,
    presetWorkflow,
}) => {
    const [selectedKey, setSelectedKey] = useState("");
    // The workflow at the moment Continue was pressed. Changing the selection afterwards leaves the
    // started body mounted (hidden) until Continue is pressed again.
    const [started, setStarted] = useState<Workflow | null>(null);
    const [stage, setStage] = useState<"workflow" | "wizard">(
        presetWorkflow ? "wizard" : "workflow"
    );

    const listsEnabled = !presetWorkflow;
    const { data: dbWorkflows = [] } = useAllWorkflows(databaseId, undefined, listsEnabled);
    // The GLOBAL catalog is fetched separately ONLY when scoped to a database: the unscoped list
    // (`/workflows`) already returns every workflow the caller can see, GLOBAL included. Fetching it
    // again there produced a list with each GLOBAL workflow twice — and duplicate option keys break
    // the picker's list reconciliation, which is why typing in its search appeared to do nothing.
    const { data: globalWorkflows = [] } = useAllWorkflows(
        "GLOBAL",
        undefined,
        !!databaseId && listsEnabled
    );
    const allWorkflows = useMemo(() => {
        // Deduplicated defensively as well: a workflow must never appear twice even if both scopes
        // return it.
        const seen = new Set<string>();
        return [...dbWorkflows, ...globalWorkflows].filter((wf) => {
            const key = workflowKey(wf);
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
        });
    }, [dbWorkflows, globalWorkflows]);

    // Referenced pipelines' systemConfig, needed to resolve what each workflow accepts. The same
    // read the wizard body makes — unscoped, so a database workflow's own steps resolve as well as
    // GLOBAL ones, and archived-inclusive, so a reference to an archived step still names it — and
    // on the same query key, so the body mounts on a cache hit rather than a second request.
    const { data: allPipelines = [] } = useAllPipelines(undefined, true, listsEnabled);
    const pipelinesByKey = useMemo(() => {
        const map: Record<string, Pipeline> = {};
        allPipelines.forEach((p: Pipeline) => {
            map[`${p.databaseId}:${p.pipelineId}`] = p;
        });
        return map;
    }, [allPipelines]);

    const workflowsOffered = useMemo(
        () => allWorkflows.filter((wf) => wf.enabled && !wf.archived),
        [allWorkflows]
    );
    const selectedWorkflow = useMemo(
        () => workflowsOffered.find((wf) => workflowKey(wf) === selectedKey) || null,
        [workflowsOffered, selectedKey]
    );
    // A workflow that cannot accept the selection must not be carried into the wizard.
    const canContinue =
        !!selectedWorkflow &&
        selectionErrorsFor(selectedWorkflow, pipelinesByKey, presetInputFiles).length === 0;

    const { data: presetWorkflowData, isError: presetFailed } = useWorkflow(
        presetWorkflow?.databaseId || "",
        presetWorkflow?.workflowId || ""
    );

    const startedWorkflow: Workflow | null = presetWorkflow ? presetWorkflowData || null : started;
    const startedKey = startedWorkflow ? workflowKey(startedWorkflow) : "";

    const handleClose = () => {
        setSelectedKey("");
        setStarted(null);
        setStage(presetWorkflow ? "wizard" : "workflow");
        onClose();
    };

    const handleContinue = () => {
        if (!selectedWorkflow) return;
        setStarted(selectedWorkflow);
        setStage("wizard");
    };

    const title =
        stage === "workflow" || !startedWorkflow
            ? "Execute a workflow"
            : `Execute ${startedWorkflow.workflowName || startedWorkflow.workflowId}`;

    // The picker owns the footer on its step; the body renders its own once it is showing. While a
    // preset workflow is still loading (or failed) the only action is Cancel.
    const footer =
        stage === "workflow" ? (
            <>
                <button onClick={handleClose} className={btnSecondary}>
                    Cancel
                </button>
                <button onClick={handleContinue} disabled={!canContinue} className={btnPrimary}>
                    Continue
                </button>
            </>
        ) : startedWorkflow ? undefined : (
            <button onClick={handleClose} className={btnSecondary}>
                Cancel
            </button>
        );

    // The rail on the Workflow step previews the journey for the selected workflow; every row after
    // Workflow is still ahead, so none is clickable.
    const previewRail: RailStep[] = [
        { id: "workflow", label: "Workflow" },
        { id: "input", label: "Inputs" },
        ...(selectedWorkflow?.specifiedPipelines || []).map((ref, idx) => ({
            id: `pipeline-${idx}`,
            label:
                pipelinesByKey[pipelineKeyFor(selectedWorkflow!, ref)]?.pipelineName ||
                `Pipeline ${idx + 1}`,
        })),
        { id: "review", label: "Review" },
    ];

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => !next && handleClose()}
            title={title}
            size="lg"
            footer={footer}
        >
            {stage === "workflow" && (
                <div className="flex flex-col gap-4 md:flex-row md:gap-6">
                    <WizardRail steps={previewRail} currentId="workflow" />
                    <div className="min-w-0 flex-1">
                        <WorkflowPicker
                            workflows={workflowsOffered}
                            pipelinesByKey={pipelinesByKey}
                            selectedKey={selectedKey}
                            onSelect={setSelectedKey}
                            presetInputFiles={presetInputFiles}
                        />
                    </div>
                </div>
            )}

            {stage === "wizard" &&
                !startedWorkflow &&
                (presetFailed ? (
                    <Callout tone="error" title="This workflow could not be loaded.">
                        Check that it still exists and that your role can read it.
                    </Callout>
                ) : (
                    <div className="flex items-center justify-center min-h-[240px]">
                        <div className="text-center">
                            <div className="inline-block animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 dark:border-blue-400 mb-3" />
                            <p className="text-text-secondary">Loading workflow…</p>
                        </div>
                    </div>
                ))}

            {/* Mounted once started and kept mounted while the picker shows again, so a jump back to
                the Workflow step and forward loses nothing. A Continue on a different workflow
                replaces it (the key changes). */}
            {startedWorkflow && (
                <div hidden={stage === "workflow"}>
                    <ExecuteWizardBody
                        key={startedKey}
                        onClose={handleClose}
                        workflow={startedWorkflow}
                        databaseId={startedWorkflow.databaseId}
                        presetAsset={databaseId && assetId ? { databaseId, assetId } : undefined}
                        presetInputFiles={presetInputFiles}
                        hidden={stage === "workflow"}
                        leadingSteps={
                            presetWorkflow
                                ? undefined
                                : [{ id: "workflow", label: "Workflow", done: true }]
                        }
                        onJumpTo={(stepId) => {
                            if (stepId === "workflow") setStage("workflow");
                        }}
                    />
                </div>
            )}
        </Dialog>
    );
};

export default ExecuteWorkflowModal;
