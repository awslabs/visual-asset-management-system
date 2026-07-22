/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useWorkflows, useWorkflowMutations, useDatabases } from "../api/queries";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import CategoryGroupedList from "../components/CategoryGroupedList";
import FilterBar, { type FilterValue } from "../components/FilterBar";
import ContextMenu, { type ContextMenuItem } from "../components/ContextMenu";
import ArchiveConfirmDialog from "../components/ArchiveConfirmDialog";
import DatabasePickerDialog from "../components/DatabasePickerDialog";
import ExecuteWizard from "../wizard/ExecuteWizard";
import { btnPrimary, btnSecondary, control } from "../components/controlStyles";
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
    const [dbPickerOpen, setDbPickerOpen] = useState(false);
    // How the list is grouped/sorted: by category (default) or by database.
    const [groupBy, setGroupBy] = useState<"category" | "database">("category");

    const { data, isLoading, error, fetchNextPage, hasNextPage, isFetchingNextPage } = useWorkflows(
        databaseId,
        includeArchived
    );
    const workflows = React.useMemo(
        () => data?.pages?.flatMap((page: any) => page.Items) ?? [],
        [data]
    );

    // When a search/facet filter is active, drain the remaining server pages so filtering
    // covers the whole set. Idle browsing stays paginated (Load more).
    const filterActive = !!filters.searchText || Object.values(filters.facets).some((v) => !!v);
    React.useEffect(() => {
        if (filterActive && hasNextPage && !isFetchingNextPage) {
            fetchNextPage();
        }
    }, [filterActive, hasNextPage, isFetchingNextPage, fetchNextPage]);

    const { loading: permissionsLoading, can } = useAllowedRoutes();
    const { archiveWorkflow } = useWorkflowMutations();

    // Databases for the "Database" filter facet — global (database-less) page only.
    const { data: databases } = useDatabases(!databaseId);
    const databaseOptions = React.useMemo(() => {
        if (databaseId) return [];
        const ids = new Set<string>();
        (databases || []).forEach((d: any) => d?.databaseId && ids.add(d.databaseId));
        workflows.forEach((w) => w.databaseId && ids.add(w.databaseId));
        return Array.from(ids)
            .sort()
            .map((id) => ({ label: id, value: id }));
    }, [databases, databaseId, workflows]);

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

        // Database facet (global page only).
        if (filters.facets.databaseId && w.databaseId !== filters.facets.databaseId) {
            return false;
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
        // executionCount comes from the list response (computed server-side per page); omit the
        // label when the backend did not supply it.
        const executionCount = workflow.executionCount;

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
                // A disabled (or archived) workflow cannot be executed, so gray the action out and
                // block the wizard from opening rather than letting it launch and fail server-side.
                disabled: !workflow.enabled || workflow.archived,
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
            <div className="flex items-center justify-between p-3 bg-surface-container border border-border-default rounded hover:bg-surface-hover">
                <div className="flex-1">
                    <div className="flex items-center gap-2">
                        <span className="font-semibold text-text-primary">
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
                    <div className="text-sm text-text-secondary mt-1">{workflow.workflowId}</div>
                    <div className="text-sm text-text-secondary mt-1">
                        <span className="mr-3">Database: {workflow.databaseId}</span>
                        {workflow.category && (
                            <span className="mr-3">Category: {workflow.category}</span>
                        )}
                        <span className="mr-3">Pipelines: {pipelineCount}</span>
                        {executionCount !== undefined && executionCount !== null && (
                            <span className="mr-3">Executions: {executionCount}</span>
                        )}
                        {workflow.subDashboardUrl && (
                            <a
                                href={workflow.subDashboardUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-blue-600 dark:text-blue-400 hover:underline"
                            >
                                Dashboard
                            </a>
                        )}
                    </div>
                </div>
                <ContextMenu
                    items={contextMenuItems}
                    trigger={
                        <button
                            aria-label={`Actions for ${workflow.workflowName}`}
                            className="bg-transparent border-0 px-2 py-1 rounded text-lg leading-none text-text-secondary hover:text-gray-700 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700 cursor-pointer"
                        >
                            ⋮
                        </button>
                    }
                />
            </div>
        );
    };

    const loading = isLoading || permissionsLoading;
    const canCreateWorkflow = can("POST", "/database/{databaseId}/workflows");

    return (
        <div className="orchestration-root p-6 space-y-4 bg-surface min-h-full">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-bold text-text-primary">Workflows</h1>
                {canCreateWorkflow && (
                    <button
                        onClick={() => {
                            // From a database-scoped page go straight to create; from the global
                            // page pick a target database first (workflows are database-scoped).
                            if (databaseId) {
                                navigate(`/databases/${databaseId}/workflows/create`);
                            } else {
                                setDbPickerOpen(true);
                            }
                        }}
                        className={btnPrimary}
                    >
                        Create Workflow
                    </button>
                )}
            </div>

            <div className="flex items-start gap-2 flex-wrap justify-between">
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
                        ...(databaseOptions.length > 0
                            ? [{ key: "databaseId", label: "Database", options: databaseOptions }]
                            : []),
                    ]}
                />
                <div className="flex items-center gap-2 flex-wrap justify-end">
                    <label className="flex items-center gap-2 text-sm text-text-primary whitespace-nowrap">
                        Group by
                        <select
                            aria-label="Group by"
                            value={groupBy}
                            onChange={(e) => setGroupBy(e.target.value as "category" | "database")}
                            className={control}
                        >
                            <option value="category">Category</option>
                            <option value="database">Database</option>
                        </select>
                    </label>
                    <label className="flex items-center gap-2 text-sm text-text-primary whitespace-nowrap">
                        <input
                            type="checkbox"
                            checked={includeArchived}
                            onChange={(e) => setIncludeArchived(e.target.checked)}
                            className="rounded"
                        />
                        Include Archived
                    </label>
                </div>
            </div>

            {loading ? (
                <div className="p-8 text-center text-text-secondary">Loading workflows...</div>
            ) : error ? (
                <div className="p-4 text-vams-error">Error loading workflows: {String(error)}</div>
            ) : filteredWorkflows.length === 0 ? (
                <div className="p-8 text-center text-text-secondary">
                    No workflows found matching the current filters.
                </div>
            ) : (
                <CategoryGroupedList
                    items={filteredWorkflows}
                    groupBy={(w) =>
                        groupBy === "database"
                            ? w.databaseId || "Unknown database"
                            : w.category || "Uncategorized"
                    }
                    renderItem={renderWorkflowCard}
                />
            )}

            {!loading && hasNextPage && filterActive && (
                <div className="flex justify-center mt-4 text-sm text-text-secondary">
                    Searching all workflows…
                </div>
            )}

            {!loading && hasNextPage && !filterActive && (
                <div className="flex justify-center mt-4">
                    <button
                        onClick={() => fetchNextPage()}
                        disabled={isFetchingNextPage}
                        className={btnSecondary}
                    >
                        {isFetchingNextPage ? "Loading more..." : "Load more"}
                    </button>
                </div>
            )}

            {archiveConfirmWorkflow && (
                <ArchiveConfirmDialog
                    entityName={archiveConfirmWorkflow.workflowName}
                    open={!!archiveConfirmWorkflow}
                    onConfirm={() => handleArchive(archiveConfirmWorkflow)}
                    onCancel={() => setArchiveConfirmWorkflow(null)}
                />
            )}

            {executeWorkflow && (
                <ExecuteWizard
                    open={!!executeWorkflow}
                    onClose={() => setExecuteWorkflow(null)}
                    workflow={executeWorkflow}
                    databaseId={executeWorkflow.databaseId}
                />
            )}

            <DatabasePickerDialog
                open={dbPickerOpen}
                onOpenChange={setDbPickerOpen}
                title="Create workflow in database"
                onSelect={(db) => {
                    setDbPickerOpen(false);
                    navigate(`/databases/${db}/workflows/create`);
                }}
            />
        </div>
    );
};

export default WorkflowsPage;
