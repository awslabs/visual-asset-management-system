/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useRef, useMemo } from "react";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Pagination from "@cloudscape-design/components/pagination";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import ErrorBoundary from "../../common/ErrorBoundary";
import { WorkflowExecutionListDefinition } from "../../list/list-definitions/WorkflowExecutionListDefinition";
import { fetchDatabaseWorkflows, fetchWorkflowExecutions } from "../../../services/APIService";
import { useNavigate } from "react-router";
import { useStatusMessage } from "../../common/StatusMessage";
import { useCollection } from "@cloudscape-design/collection-hooks";

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
    const [refreshing, setRefreshing] = useState(false);
    const [allItems, setAllItems] = useState<any[]>([]);
    const [reload, setReload] = useState(false);
    const [backgroundRefresh, setBackgroundRefresh] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [hasIncompleteJobs, setHasIncompleteJobs] = useState(false);
    const [initialLoadComplete, setInitialLoadComplete] = useState(false);
    const [expandedItems, setExpandedItems] = useState<any[]>([]);
    const refreshTimerRef = useRef<NodeJS.Timeout | null>(null);
    const initialLoadDoneRef = useRef<boolean>(false);

    // Build parent items and children map from the flat allItems list
    const { parentItems, childrenMap } = useMemo(() => {
        const parents: any[] = [];
        const children = new Map<string, any[]>();

        for (const item of allItems) {
            if (item.parentId) {
                const existing = children.get(item.parentId) || [];
                existing.push(item);
                children.set(item.parentId, existing);
            } else {
                parents.push(item);
            }
        }

        return { parentItems: parents, childrenMap: children };
    }, [allItems]);

    // Auto-expand all parents when data changes
    useEffect(() => {
        setExpandedItems(parentItems);
    }, [parentItems]);

    // Convert WorkflowExecutionListDefinition column definitions to Cloudscape format
    const columnDefinitions = useMemo(() => {
        const listDef = WorkflowExecutionListDefinition;
        return listDef.columnDefinitions.map(({ id, header, CellWrapper, sortingField }: any) => ({
            id,
            header,
            cell: (item: any) => <CellWrapper item={item}>{item[id]}</CellWrapper>,
            sortingField,
        }));
    }, []);

    const visibleColumns = WorkflowExecutionListDefinition.visibleColumns;

    // Calculate execution count (exclude parent workflow rows)
    const executionCount = allItems.filter((item) => item.parentId).length;

    // Use Cloudscape collection hooks for sorting/pagination
    const { items, collectionProps, paginationProps, filterProps, filteredItemsCount } =
        useCollection(parentItems, {
            sorting: {
                defaultState: {
                    sortingColumn: { sortingField: "startDate" },
                    isDescending: true,
                },
            },
            pagination: { pageSize: 15 },
            filtering: {},
            selection: {},
        });

    // Trigger a refresh when refreshTrigger changes (after workflow execution)
    useEffect(() => {
        if (refreshTrigger !== undefined && refreshTrigger > 0) {
            console.log("WorkflowTab: refreshTrigger changed to", refreshTrigger);
            setReload(true);
        }
    }, [refreshTrigger]);

    // Trigger initial load when tab becomes active for the first time
    useEffect(() => {
        if (isActive && !initialLoadComplete) {
            setReload(true);
        }
    }, [isActive, initialLoadComplete]);

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
            // Show refreshing state for manual and triggered reloads, not for background refreshes
            if (!backgroundRefresh) {
                setRefreshing(true);
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
                    const newRows: any[] = [];

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
                            // Preserve the inputAssetFileKey from the execution data
                            newParentRowChild.inputAssetFileKey = execution.inputAssetFileKey || "";

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
                        // Only add workflows that have executions
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

                            // Get executions for this workflow
                            const workflowExecutions = workflowMap.get(workflowKey) || [];
                            console.log(
                                `Executions for workflow ${workflowKey}:`,
                                workflowExecutions
                            );

                            // Only add workflow if it has executions
                            if (workflowExecutions.length > 0) {
                                // Add the workflow parent row
                                const newParentRow = Object.assign({}, workflow);

                                // Set the name property to the workflowKey for unique identification
                                // This is what child executions will reference in their parentId
                                newParentRow.name = workflowKey;

                                // Set a displayName property that will be shown in the UI
                                newParentRow.displayName = `${workflowId} (${workflowDatabaseId})`;
                                newRows.push(newParentRow);

                                // Add all executions for this workflow
                                newRows.push(...workflowExecutions);
                            }
                        }

                        console.log("Final newRows:", newRows);

                        // Update state to track if we have incomplete jobs
                        setHasIncompleteJobs(hasRunningJobs);
                    } catch (execError) {
                        console.error("Error fetching workflow executions:", execError);
                    }

                    setAllItems(newRows);
                    setRefreshing(false);
                    setReload(false);
                    setBackgroundRefresh(false);
                    setInitialLoadComplete(true);
                } else if (
                    typeof workflows === "string" &&
                    (workflows as string).indexOf("not found") !== -1
                ) {
                    setError(
                        "Workflow data not found. The requested asset may have been deleted or you may not have permission to access it."
                    );
                    setRefreshing(false);
                    setReload(false);
                    setBackgroundRefresh(false);
                    setInitialLoadComplete(true);
                }
            } catch (error: any) {
                console.error("Error fetching workflows:", error);
                setError(`Failed to load workflow data: ${error.message || "Unknown error"}`);
                setRefreshing(false);
                setReload(false);
                setBackgroundRefresh(false);
                setInitialLoadComplete(true);

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
            setInitialLoadComplete(false);
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

    // Remove ref from collectionProps to avoid React warning
    const { ref, ...tableCollectionProps } = collectionProps as any;

    return (
        <ErrorBoundary componentName="Workflows">
            <Table
                {...tableCollectionProps}
                items={items}
                loading={refreshing}
                trackBy="name"
                columnDefinitions={columnDefinitions}
                visibleColumns={visibleColumns}
                expandableRows={{
                    getItemChildren: (item: any) => childrenMap.get(item.name) || [],
                    isItemExpandable: (item: any) => (childrenMap.get(item.name) || []).length > 0,
                    expandedItems,
                    onExpandableItemToggle: ({ detail: { item, expanded } }: any) => {
                        setExpandedItems((prev) =>
                            expanded
                                ? [...prev, item]
                                : prev.filter((i: any) => i.name !== item.name)
                        );
                    },
                }}
                header={
                    <>
                        <div
                            style={{
                                position: "absolute",
                                right: "20px",
                                top: "10px",
                                zIndex: 1,
                            }}
                        >
                            <Button
                                variant="primary"
                                onClick={() => {
                                    onExecuteWorkflow();
                                }}
                            >
                                Execute Workflow
                            </Button>
                        </div>
                        <Header
                            counter={
                                items.length !== executionCount
                                    ? `(${
                                          allItems.filter((item) => item.parentId).length
                                      }/${executionCount})`
                                    : `(${executionCount})`
                            }
                        >
                            Workflow Executions
                        </Header>
                    </>
                }
                pagination={<Pagination {...paginationProps} />}
                filter={
                    <div style={{ padding: "0 0 16px 0" }}>
                        <Button
                            iconName="refresh"
                            variant="icon"
                            onClick={() => setReload(true)}
                            loading={refreshing}
                            ariaLabel="Refresh data"
                        />
                    </div>
                }
                empty={
                    <div style={{ textAlign: "center", padding: "20px" }}>
                        <b>No workflow executions</b>
                        <p>No workflow executions to display.</p>
                    </div>
                }
            />
        </ErrorBoundary>
    );
};

export default WorkflowTab;
