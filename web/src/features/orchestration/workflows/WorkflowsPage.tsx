/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useWorkflows, useWorkflowMutations } from "../api/queries";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import CategoryGroupedList from "../components/CategoryGroupedList";
import FilterBar, { type FilterValue } from "../components/FilterBar";
import ContextMenu, { type ContextMenuItem } from "../components/ContextMenu";
import Dialog from "../components/Dialog";
import ExecuteWizard from "../wizard/ExecuteWizard";
import type { Workflow } from "../types";

interface WorkflowsPageProps {
    databaseId?: string;
}

const WorkflowsPage: React.FC<WorkflowsPageProps> = ({ databaseId }) => {
    const navigate = useNavigate();
    const [includeArchived, setIncludeArchived] = useState(false);
    const [filters, setFilters] = useState<FilterValue>({
        searchText: "",
        facets: {},
    });
    const [archiveConfirmWorkflow, setArchiveConfirmWorkflow] = useState<Workflow | null>(null);
    const [executeWorkflow, setExecuteWorkflow] = useState<Workflow | null>(null);

    const { data: workflows = [], isLoading, error } = useWorkflows(databaseId, includeArchived);
    const { loading: permissionsLoading, can } = useAllowedRoutes();
    const { archiveWorkflow } = useWorkflowMutations();

    // Filter workflows
    const filteredWorkflows = workflows.filter((w) => {
        // Text search
        if (filters.searchText) {
            const searchLower = filters.searchText.toLowerCase();
            const matchesSearch =
                w.workflowName.toLowerCase().includes(searchLower) ||
                w.workflowId.toLowerCase().includes(searchLower) ||
                (w.description || "").toLowerCase().includes(searchLower);
            if (!matchesSearch) return false;
        }

        // Enabled/archived facet
        if (filters.facets.enabledArchived) {
            if (filters.facets.enabledArchived === "enabled" && !w.enabled) return false;
            if (filters.facets.enabledArchived === "disabled" && w.enabled) return false;
            if (filters.facets.enabledArchived === "archived" && !w.archived) return false;
        }

        return true;
    });

    const handleArchive = async (workflow: Workflow) => {
        try {
            await archiveWorkflow.mutateAsync({
                databaseId: workflow.databaseId,
                workflowId: workflow.workflowId,
            });
            setArchiveConfirmWorkflow(null);
        } catch (err) {
            console.error("Failed to archive workflow:", err);
        }
    };

    const renderWorkflowCard = (workflow: Workflow) => {
        const pipelineCount = workflow.specifiedPipelines?.length || 0;
        // Execution count omitted: backend list response lacks per-workflow execution summary.
        // Querying per card would cause N+1 storm. Backend follow-up: add executionCount to Workflow list response.

        const contextMenuItems: ContextMenuItem[] = [
            {
                label: "Edit",
                onSelect: () =>
                    navigate(`/databases/${workflow.databaseId}/workflows/${workflow.workflowId}`),
                hidden: !can("PUT", "/database/{databaseId}/workflows/{workflowId}"),
            },
            {
                label: "Execute",
                onSelect: () => setExecuteWorkflow(workflow),
                hidden: !can("POST", "/workflows/{workflowDatabaseId}/{workflowId}/execute"),
            },
            {
                label: "View Executions",
                onSelect: () =>
                    navigate(
                        `/executions?workflowId=${workflow.workflowId}&workflowDatabaseId=${workflow.databaseId}`
                    ),
            },
            {
                label: "Archive",
                onSelect: () => setArchiveConfirmWorkflow(workflow),
                danger: true,
                hidden: !can("DELETE", "/database/{databaseId}/workflows/{workflowId}"),
            },
        ];

        return (
            <ContextMenu
                items={contextMenuItems}
                trigger={
                    <div className="flex items-center justify-between p-3 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded hover:bg-gray-50 dark:hover:bg-gray-750 cursor-pointer">
                        <div className="flex-1">
                            <div className="flex items-center gap-2">
                                <span className="font-semibold text-gray-900 dark:text-gray-100">
                                    {workflow.workflowName}
                                </span>
                                {!workflow.enabled && (
                                    <span className="px-2 py-1 text-xs bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-300 rounded">
                                        Disabled
                                    </span>
                                )}
                                {workflow.archived && (
                                    <span className="px-2 py-1 text-xs bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200 rounded">
                                        Archived
                                    </span>
                                )}
                            </div>
                            <div className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                                {workflow.workflowId}
                            </div>
                            <div className="text-sm text-gray-500 dark:text-gray-500 mt-1">
                                {workflow.category && (
                                    <span className="mr-3">Category: {workflow.category}</span>
                                )}
                                <span className="mr-3">Pipelines: {pipelineCount}</span>
                                {workflow.subDashboardUrl && (
                                    <a
                                        href={workflow.subDashboardUrl}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-blue-600 dark:text-blue-400 hover:underline"
                                        onClick={(e) => e.stopPropagation()}
                                    >
                                        Dashboard
                                    </a>
                                )}
                            </div>
                        </div>
                        <button className="px-2 py-1 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
                            ⋮
                        </button>
                    </div>
                }
            />
        );
    };

    if (isLoading || permissionsLoading) {
        return <div className="p-4 text-gray-600 dark:text-gray-400">Loading workflows...</div>;
    }

    if (error) {
        return (
            <div className="p-4 text-red-600 dark:text-red-400">
                Error loading workflows: {String(error)}
            </div>
        );
    }

    const canCreateWorkflow = can("POST", "/database/{databaseId}/workflows");

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Workflows</h1>
                {canCreateWorkflow && databaseId && (
                    <button
                        onClick={() => navigate(`/databases/${databaseId}/workflows/create`)}
                        className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 dark:bg-blue-700 dark:hover:bg-blue-600"
                    >
                        Create Workflow
                    </button>
                )}
            </div>

            <div className="flex items-center gap-2">
                <FilterBar
                    value={filters}
                    onChange={setFilters}
                    facets={[
                        {
                            key: "enabledArchived",
                            label: "Status",
                            options: [
                                { label: "Enabled", value: "enabled" },
                                { label: "Disabled", value: "disabled" },
                                { label: "Archived", value: "archived" },
                            ],
                        },
                    ]}
                />
                <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300 whitespace-nowrap">
                    <input
                        type="checkbox"
                        checked={includeArchived}
                        onChange={(e) => setIncludeArchived(e.target.checked)}
                        className="rounded"
                    />
                    Include Archived
                </label>
            </div>

            {filteredWorkflows.length === 0 ? (
                <div className="p-8 text-center text-gray-500 dark:text-gray-400">
                    No workflows found matching the current filters.
                </div>
            ) : (
                <CategoryGroupedList
                    items={filteredWorkflows}
                    groupBy={(w) => w.category || "Uncategorized"}
                    renderItem={renderWorkflowCard}
                />
            )}

            {archiveConfirmWorkflow && (
                <Dialog
                    open={!!archiveConfirmWorkflow}
                    onOpenChange={(open) => !open && setArchiveConfirmWorkflow(null)}
                    title="Archive Workflow"
                    footer={
                        <>
                            <button
                                onClick={() => setArchiveConfirmWorkflow(null)}
                                className="px-4 py-2 bg-gray-200 text-gray-800 rounded hover:bg-gray-300 dark:bg-gray-700 dark:text-gray-200 dark:hover:bg-gray-600"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={() => handleArchive(archiveConfirmWorkflow)}
                                className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 dark:bg-red-700 dark:hover:bg-red-600"
                            >
                                Archive
                            </button>
                        </>
                    }
                >
                    <p className="text-gray-700 dark:text-gray-300">
                        Are you sure you want to archive{" "}
                        <strong>{archiveConfirmWorkflow.workflowName}</strong>? This action can be
                        undone by including archived workflows and unarchiving.
                    </p>
                </Dialog>
            )}

            {executeWorkflow && (
                <ExecuteWizard
                    open={!!executeWorkflow}
                    onClose={() => setExecuteWorkflow(null)}
                    workflow={executeWorkflow}
                    databaseId={executeWorkflow.databaseId}
                />
            )}
        </div>
    );
};

export default WorkflowsPage;
