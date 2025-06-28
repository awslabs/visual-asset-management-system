/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useRef } from "react";
import { Button, Container, Header, SpaceBetween } from "@cloudscape-design/components";
import ErrorBoundary from "../../common/ErrorBoundary";
import { LoadingSpinner } from "../../common/LoadingSpinner";
import { WorkflowExecutionListDefinition } from "../../list/list-definitions/WorkflowExecutionListDefinition";
import { fetchDatabaseWorkflows, fetchWorkflowExecutions } from "../../../services/APIService";
import { useNavigate } from "react-router";
import { useStatusMessage } from "../../common/StatusMessage";
import RelatedTableList from "../../list/RelatedTableList";

interface WorkflowTabProps {
    databaseId: string;
    assetId: string;
    isActive: boolean;
    onExecuteWorkflow: () => void;
    refreshTrigger?: number; // When this value changes, the tab will refresh
}

export const WorkflowTab: React.FC<WorkflowTabProps> = ({
    databaseId,
    assetId,
    isActive,
    onExecuteWorkflow,
    refreshTrigger,
}) => {
    const navigate = useNavigate();
    const { showMessage } = useStatusMessage();
    const [loading, setLoading] = useState(true);
    const [allItems, setAllItems] = useState<any[]>([]);
    const [reload, setReload] = useState(true);
    const [backgroundRefresh, setBackgroundRefresh] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [hasIncompleteJobs, setHasIncompleteJobs] = useState(false);
    const refreshTimerRef = useRef<NodeJS.Timeout | null>(null);
    const initialLoadDoneRef = useRef<boolean>(false);

    const WorkflowHeaderControls = () => {
        return (
            <div
                style={{
                    position: "absolute",
                    right: "20px",
                    top: "10px",
                    zIndex: 1,
                }}
            >
                <SpaceBetween direction="horizontal" size="xs">
                    <Button iconName="refresh" onClick={() => setReload(true)}>
                        Refresh
                    </Button>
                    <Button
                        variant={"primary"}
                        onClick={() => {
                            // Call the parent's onExecuteWorkflow function
                            onExecuteWorkflow();
                            // Set hasIncompleteJobs to true to start the auto-refresh timer
                            setHasIncompleteJobs(true);
                            // Trigger an immediate refresh
                            setReload(true);
                        }}
                    >
                        Execute Workflow
                    </Button>
                </SpaceBetween>
            </div>
        );
    };

    // Trigger a refresh when refreshTrigger changes
    useEffect(() => {
        if (refreshTrigger !== undefined) {
            setReload(true);
        }
    }, [refreshTrigger]);

    // Fetch workflows and executions when the tab is active or when reload is triggered
    useEffect(() => {
        // Only fetch data when the tab is active or when we need to reload or background refresh
        if (!isActive && !reload && !backgroundRefresh) return;

        // Prevent duplicate API calls when the component mounts and the tab is active
        if (isActive && !reload && !backgroundRefresh && initialLoadDoneRef.current) return;

        // Mark that we've done the initial load for this tab activation
        if (isActive) {
            initialLoadDoneRef.current = true;
        }

        const getData = async () => {
            // Only show loading spinner for full reloads, not background refreshes
            if (!backgroundRefresh) {
                setLoading(true);
            }
            setError(null);

            try {
                // For background refreshes, we only need to fetch executions, not workflows
                let workflows;
                if (backgroundRefresh && allItems.length > 0) {
                    // Extract existing workflows from allItems
                    workflows = allItems.filter((item) => !item.parentId);
                } else {
                    // Fetch all workflows for the database for initial or manual refreshes
                    const workflowsDatabase = await fetchDatabaseWorkflows({ databaseId });
                    const workflowGlobal = await fetchDatabaseWorkflows({ databaseId: "GLOBAL" });
                    workflows = [...workflowsDatabase, ...workflowGlobal];
                }

                if (workflows && Array.isArray(workflows)) {
                    const newRows = [];

                    // Create a map to organize executions by workflow
                    const workflowMap = new Map();

                    // Fetch all executions for the asset in a single call
                    try {
                        const executions = await fetchWorkflowExecutions({
                            databaseId,
                            assetId,
                            // workflowId is not specified, so it will default to '' and fetch all executions
                        });

                        let hasRunningJobs = false;

                        // Handle the new API response format
                        // The executions are now in response.message.Items
                        const executionItems = Array.isArray(executions)
                            ? executions
                            : executions &&
                              executions.message &&
                              Array.isArray(executions.message.Items)
                            ? executions.message.Items
                            : [];

                        console.log("Fetched executions:", executionItems);

                        // Group executions by their workflow ID and workflowDatabaseId
                        for (let j = 0; j < executionItems.length; j++) {
                            const execution = executionItems[j];

                            // In the new format, workflowId and workflowDatabaseId are directly included in each execution
                            const workflowId = String(execution.workflowId || "");
                            const workflowDatabaseId = String(execution.workflowDatabaseId || "");

                            // Create a unique key combining workflowDatabaseId and workflowId
                            const workflowKey = `${workflowDatabaseId}:${workflowId}`;

                            console.log(`Execution ${j}:`, execution);
                            console.log(`Execution ${j} workflowId:`, workflowId);
                            console.log(`Execution ${j} workflowDatabaseId:`, workflowDatabaseId);

                            if (!workflowMap.has(workflowKey)) {
                                workflowMap.set(workflowKey, []);
                            }

                            const newParentRowChild = Object.assign({}, execution);

                            // Set the parentId to match with the workflow's unique key
                            newParentRowChild.parentId = workflowKey;
                            newParentRowChild.name = newParentRowChild.executionId;

                            if (newParentRowChild.stopDate === "") {
                                newParentRowChild.stopDate = "N/A";
                            }

                            // Check if this job is still running
                            if (
                                newParentRowChild.executionStatus !== "SUCCEEDED" &&
                                newParentRowChild.executionStatus !== "FAILED" &&
                                newParentRowChild.executionStatus !== "CANCELED"
                            ) {
                                hasRunningJobs = true;
                            }

                            workflowMap.get(workflowKey).push(newParentRowChild);
                        }

                        // Now add workflows and their executions in the correct order
                        for (let i = 0; i < workflows.length; i++) {
                            const workflow = workflows[i];
                            // Make sure workflowId is a string
                            const workflowId = String(workflow.workflowId || "");
                            const workflowDatabaseId = String(workflow.databaseId || "");

                            // Create a unique key combining workflowDatabaseId and workflowId
                            const workflowKey = `${workflowDatabaseId}:${workflowId}`;

                            console.log(`Workflow ${i}:`, workflow);
                            console.log(`Workflow ${i} workflowId:`, workflowId);
                            console.log(`Workflow ${i} workflowDatabaseId:`, workflowDatabaseId);

                            // Add the workflow parent row
                            const newParentRow = Object.assign({}, workflow);

                            // Set the name property to the workflowKey for unique identification
                            // This is what child executions will reference in their parentId
                            newParentRow.name = workflowKey;

                            // Set a displayName property that will be shown in the UI
                            newParentRow.displayName = `${workflowId} (${workflowDatabaseId})`;
                            newRows.push(newParentRow);

                            // Add all executions for this workflow
                            const workflowExecutions = workflowMap.get(workflowKey) || [];
                            console.log(
                                `Executions for workflow ${workflowKey}:`,
                                workflowExecutions
                            );
                            newRows.push(...workflowExecutions);
                        }

                        console.log("Final newRows:", newRows);

                        // Update state to track if we have incomplete jobs
                        setHasIncompleteJobs(hasRunningJobs);
                    } catch (execError) {
                        console.error("Error fetching workflow executions:", execError);
                    }

                    setAllItems(newRows);
                    setLoading(false);
                    setReload(false);
                    setBackgroundRefresh(false);
                } else if (
                    typeof workflows === "string" &&
                    (workflows as string).indexOf("not found") !== -1
                ) {
                    setError(
                        "Workflow data not found. The requested asset may have been deleted or you may not have permission to access it."
                    );
                    setLoading(false);
                    setReload(false);
                    setBackgroundRefresh(false);
                }
            } catch (error: any) {
                console.error("Error fetching workflows:", error);
                setError(`Failed to load workflow data: ${error.message || "Unknown error"}`);
                setLoading(false);
                setReload(false);
                setBackgroundRefresh(false);

                showMessage({
                    type: "error",
                    message: `Failed to load workflow data: ${error.message || "Unknown error"}`,
                    dismissible: true,
                });
            }
        };

        getData();
    }, [isActive, reload, backgroundRefresh, databaseId, assetId, showMessage]);

    // Reset the initialLoadDone flag when the tab becomes inactive
    useEffect(() => {
        if (!isActive) {
            initialLoadDoneRef.current = false;
        }
    }, [isActive]);

    // Set up auto-refresh timer when tab is active and there are incomplete jobs
    useEffect(() => {
        // Clear any existing timer
        if (refreshTimerRef.current) {
            clearInterval(refreshTimerRef.current);
            refreshTimerRef.current = null;
        }

        // If tab is active and there are incomplete jobs, set up a timer to refresh
        if (isActive && hasIncompleteJobs) {
            refreshTimerRef.current = setInterval(() => {
                // Use background refresh to avoid showing the loading spinner
                setBackgroundRefresh(true);
            }, 10000); // Refresh every 10 seconds
        }

        // Cleanup function to clear the timer when component unmounts or dependencies change
        return () => {
            if (refreshTimerRef.current) {
                clearInterval(refreshTimerRef.current);
                refreshTimerRef.current = null;
            }
        };
    }, [isActive, hasIncompleteJobs]);

    // If there's an error, show it
    if (error) {
        return (
            <ErrorBoundary componentName="Workflows">
                <Container header={<Header variant="h2">Workflow Executions</Header>}>
                    <div className="error-message">
                        {error}
                        <Button onClick={() => setReload(true)}>Retry</Button>
                    </div>
                </Container>
            </ErrorBoundary>
        );
    }

    return (
        <ErrorBoundary componentName="Workflows">
            {loading ? (
                <LoadingSpinner text="Loading workflow executions..." />
            ) : (
                <RelatedTableList
                    allItems={allItems}
                    loading={loading}
                    listDefinition={WorkflowExecutionListDefinition}
                    databaseId={databaseId}
                    setReload={setReload}
                    parentId={"parentId"}
                    //@ts-ignore
                    HeaderControls={WorkflowHeaderControls}
                />
            )}
        </ErrorBoundary>
    );
};

export default WorkflowTab;
