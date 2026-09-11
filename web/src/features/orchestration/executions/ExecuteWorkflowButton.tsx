/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import ExecuteWorkflowModal from "./ExecuteWorkflowModal";
import { btnPrimary } from "../components/controlStyles";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import type { ExecutionScope } from "../api/queries";

interface ExecuteWorkflowButtonProps {
    /** The board's scope — used to preset the input asset when launching from an asset. */
    scope: ExecutionScope;
}

/**
 * "Execute workflow" button in the ExecutionsBoard toolbar. Available wherever the board is shown
 * (global Executions page and the asset Workflows tab). The picker + wizard live in
 * ExecuteWorkflowModal, shared with the asset file manager's Automation group.
 */
const ExecuteWorkflowButton: React.FC<ExecuteWorkflowButtonProps> = ({ scope }) => {
    const [open, setOpen] = useState(false);

    const { can } = useAllowedRoutes();
    const canExecute = can("POST", "/workflows/{workflowDatabaseId}/{workflowId}/execute");

    if (!canExecute) return null;

    // The asset's database (asset scope) scopes the workflow list; global/workflow scope lists all.
    const assetDatabaseId = scope.kind === "asset" ? scope.databaseId : undefined;
    const assetIdForPreset = scope.kind === "asset" ? scope.assetId : undefined;

    return (
        <>
            <button onClick={() => setOpen(true)} className={btnPrimary}>
                Execute workflow
            </button>

            {/* Keyed on the open count is unnecessary: the modal clears its own selection on close,
                so a re-open always starts fresh. */}
            {open && (
                <ExecuteWorkflowModal
                    open={open}
                    onClose={() => setOpen(false)}
                    databaseId={assetDatabaseId}
                    assetId={assetIdForPreset}
                />
            )}
        </>
    );
};

export default ExecuteWorkflowButton;
