/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Select from "@cloudscape-design/components/select";
import ErrorBoundary from "../../common/ErrorBoundary";
import ExecutionsBoard from "../../../features/orchestration/executions/ExecutionsBoard";
import ExecuteWizard from "../../../features/orchestration/wizard/ExecuteWizard";
import { useWorkflows } from "../../../features/orchestration/api/queries";

interface AssetExecutionsTabProps {
    databaseId: string;
    assetId: string;
    isActive: boolean;
}

export const AssetExecutionsTab: React.FC<AssetExecutionsTabProps> = ({
    databaseId,
    assetId,
    isActive,
}) => {
    const [wizardOpen, setWizardOpen] = useState(false);
    const [selectedWorkflow, setSelectedWorkflow] = useState<any | null>(null);

    // Fetch workflows for the database + GLOBAL
    const { data: dbWorkflows = [] } = useWorkflows(databaseId);
    const { data: globalWorkflows = [] } = useWorkflows("GLOBAL");

    const allWorkflows = React.useMemo(() => {
        return [...dbWorkflows, ...globalWorkflows];
    }, [dbWorkflows, globalWorkflows]);

    const workflowOptions = React.useMemo(() => {
        return allWorkflows.map((wf) => ({
            label: `${wf.workflowName} (${wf.databaseId})`,
            value: `${wf.databaseId}:${wf.workflowId}`,
        }));
    }, [allWorkflows]);

    const handleExecute = () => {
        if (selectedWorkflow) {
            setWizardOpen(true);
        }
    };

    const selectedWorkflowObj = React.useMemo(() => {
        if (!selectedWorkflow) return null;
        const [dbId, wfId] = selectedWorkflow.value.split(":");
        return allWorkflows.find((wf) => wf.databaseId === dbId && wf.workflowId === wfId) || null;
    }, [selectedWorkflow, allWorkflows]);

    return (
        <ErrorBoundary componentName="Asset Executions">
            <SpaceBetween size="m">
                {/* Execute workflow control */}
                <div
                    style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "12px",
                        paddingTop: "8px",
                    }}
                >
                    <Select
                        selectedOption={selectedWorkflow}
                        onChange={({ detail }) => setSelectedWorkflow(detail.selectedOption)}
                        options={workflowOptions}
                        placeholder="Select a workflow to execute"
                        filteringType="auto"
                        disabled={workflowOptions.length === 0}
                    />
                    <Button
                        variant="primary"
                        onClick={handleExecute}
                        disabled={!selectedWorkflow || workflowOptions.length === 0}
                    >
                        Execute Workflow
                    </Button>
                </div>

                {/* Executions board */}
                {isActive && (
                    <ExecutionsBoard scope={{ kind: "asset", databaseId, assetId }} />
                )}
            </SpaceBetween>

            {/* Execute wizard */}
            {wizardOpen && selectedWorkflowObj && (
                <ExecuteWizard
                    open={wizardOpen}
                    onClose={() => setWizardOpen(false)}
                    workflow={selectedWorkflowObj}
                    databaseId={databaseId}
                    presetAsset={{ databaseId, assetId }}
                />
            )}
        </ErrorBoundary>
    );
};

export default AssetExecutionsTab;
