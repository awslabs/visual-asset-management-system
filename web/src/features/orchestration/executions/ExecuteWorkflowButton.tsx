/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import Dialog from "../components/Dialog";
import SearchableSelect from "../components/SearchableSelect";
import ExecuteWizard from "../wizard/ExecuteWizard";
import { btnPrimary, btnSecondary } from "../components/controlStyles";
import { useAllWorkflows } from "../api/queries";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import type { ExecutionScope } from "../api/queries";

interface ExecuteWorkflowButtonProps {
    /** The board's scope — used to preset the input asset when launching from an asset. */
    scope: ExecutionScope;
}

/**
 * "Execute workflow" button + workflow-picker dialog + execute wizard, rendered inside the
 * ExecutionsBoard toolbar. Available wherever the board is shown (global Executions page and the
 * asset Workflows tab). When launched from an asset scope, the chosen workflow's wizard is preset
 * with that asset as the input.
 */
const ExecuteWorkflowButton: React.FC<ExecuteWorkflowButtonProps> = ({ scope }) => {
    const [pickerOpen, setPickerOpen] = useState(false);
    const [wizardOpen, setWizardOpen] = useState(false);
    const [selected, setSelected] = useState("");

    const { can } = useAllowedRoutes();
    const canExecute = can("POST", "/workflows/{workflowDatabaseId}/{workflowId}/execute");

    // The asset's database (asset scope) scopes the workflow list; global/workflow scope lists all.
    const assetDatabaseId = scope.kind === "asset" ? scope.databaseId : undefined;
    const presetAsset =
        scope.kind === "asset"
            ? { databaseId: scope.databaseId, assetId: scope.assetId }
            : undefined;

    const { data: dbWorkflows = [] } = useAllWorkflows(assetDatabaseId);
    const { data: globalWorkflows = [] } = useAllWorkflows("GLOBAL");

    const allWorkflows = React.useMemo(
        () => [...dbWorkflows, ...globalWorkflows],
        [dbWorkflows, globalWorkflows]
    );

    const options = React.useMemo(
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

    const selectedWorkflow = React.useMemo(() => {
        if (!selected) return null;
        const [dbId, wfId] = selected.split(":");
        return allWorkflows.find((wf) => wf.databaseId === dbId && wf.workflowId === wfId) || null;
    }, [selected, allWorkflows]);

    if (!canExecute) return null;

    return (
        <>
            <button
                onClick={() => {
                    // Clear any prior selection so the picker always opens fresh (a previous open
                    // must not leave its workflow pre-selected).
                    setSelected("");
                    setPickerOpen(true);
                }}
                className={btnPrimary}
            >
                Execute workflow
            </button>

            <Dialog
                open={pickerOpen}
                onOpenChange={setPickerOpen}
                title="Execute a workflow"
                footer={
                    <>
                        <button onClick={() => setPickerOpen(false)} className={btnSecondary}>
                            Cancel
                        </button>
                        <button
                            onClick={() => {
                                setPickerOpen(false);
                                setWizardOpen(true);
                            }}
                            disabled={!selectedWorkflow}
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
                </div>
            </Dialog>

            {wizardOpen && selectedWorkflow && (
                <ExecuteWizard
                    open={wizardOpen}
                    onClose={() => setWizardOpen(false)}
                    workflow={selectedWorkflow}
                    databaseId={selectedWorkflow.databaseId}
                    presetAsset={presetAsset}
                />
            )}
        </>
    );
};

export default ExecuteWorkflowButton;
