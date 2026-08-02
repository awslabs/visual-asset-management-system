/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, lazy, Suspense } from "react";
import ButtonDropdown from "@cloudscape-design/components/button-dropdown";
import type { ExecuteInputFile } from "../../../features/orchestration/types";

// The execute modal belongs to the orchestration module (Tailwind + Radix). It is lazy-loaded so the
// Cloudscape file-manager bundle does not pull in that module — and its React tree, which mounts under
// `.orchestration-root`, keeps Tailwind's utilities scoped away from the surrounding Cloudscape page.
const ExecuteWorkflowModal = lazy(
    () => import("../../../features/orchestration/executions/ExecuteWorkflowModal")
);

export interface AutomationActionsProps {
    databaseId: string;
    assetId: string;
    /**
     * The files this launch should run on, asset-relative with a leading '/'. A whole-asset selection
     * is the single entry '/'; a folder selection is the folder key with its trailing '/'.
     */
    inputFiles: ExecuteInputFile[];
    /** Disables the group with an explanation (e.g. an archived selection). */
    disabledReason?: string;
}

/**
 * "Automation" toolbar group for the asset file manager. Sits between Export and the
 * File/Asset/Folder operations group.
 *
 * Its one action launches the execute-workflow modal pre-filled with the current selection, so the
 * workflow picker can validate against the real files immediately rather than after the user
 * re-selects them inside the wizard.
 */
const AutomationActions: React.FC<AutomationActionsProps> = ({
    databaseId,
    assetId,
    inputFiles,
    disabledReason,
}) => {
    const [modalOpen, setModalOpen] = useState(false);

    return (
        <>
            <ButtonDropdown
                items={[
                    {
                        id: "execute-workflow",
                        text: "Execute Workflow",
                        iconName: "play",
                        disabled: !!disabledReason,
                        disabledReason,
                    },
                ]}
                onItemClick={({ detail }) => {
                    if (detail.id === "execute-workflow") setModalOpen(true);
                }}
            >
                Automation
            </ButtonDropdown>

            {/* Mounted only once opened: until then the orchestration chunk is never fetched. */}
            {modalOpen && (
                <Suspense fallback={null}>
                    <ExecuteWorkflowModal
                        open={modalOpen}
                        onClose={() => setModalOpen(false)}
                        databaseId={databaseId}
                        assetId={assetId}
                        presetInputFiles={inputFiles}
                    />
                </Suspense>
            )}
        </>
    );
};

export default AutomationActions;
