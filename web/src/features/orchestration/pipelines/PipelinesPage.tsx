/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { usePipelines, useArchivePipeline } from "../api/queries";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import CategoryGroupedList from "../components/CategoryGroupedList";
import FilterBar, { type FilterValue } from "../components/FilterBar";
import ContextMenu, { type ContextMenuItem } from "../components/ContextMenu";
import Dialog from "../components/Dialog";
import PipelineForm from "./PipelineForm";
import TemplateEditor from "./TemplateEditor";
import type { Pipeline, ExecutionType } from "../types";

interface PipelinesPageProps {
    databaseId?: string;
}

const PipelinesPage: React.FC<PipelinesPageProps> = ({ databaseId }) => {
    const [includeArchived, setIncludeArchived] = useState(false);
    const [filters, setFilters] = useState<FilterValue>({
        searchText: "",
        facets: {},
    });
    const [createDialogOpen, setCreateDialogOpen] = useState(false);
    const [editPipeline, setEditPipeline] = useState<Pipeline | null>(null);
    const [templatePipeline, setTemplatePipeline] = useState<Pipeline | null>(null);
    const [archiveConfirmPipeline, setArchiveConfirmPipeline] = useState<Pipeline | null>(null);

    const { data: pipelines = [], isLoading, error } = usePipelines(databaseId, includeArchived);
    const { loading: permissionsLoading, can } = useAllowedRoutes();
    const archiveMutation = useArchivePipeline();

    // Filter pipelines
    const filteredPipelines = pipelines.filter((p) => {
        // Text search
        if (filters.searchText) {
            const searchLower = filters.searchText.toLowerCase();
            const matchesSearch =
                p.pipelineName.toLowerCase().includes(searchLower) ||
                p.pipelineId.toLowerCase().includes(searchLower) ||
                (p.description || "").toLowerCase().includes(searchLower);
            if (!matchesSearch) return false;
        }

        // Execution type facet
        if (filters.facets.executionType && filters.facets.executionType !== p.executionConfig.executionType) {
            return false;
        }

        // Enabled/archived facet
        if (filters.facets.enabledArchived) {
            if (filters.facets.enabledArchived === "enabled" && !p.enabled) return false;
            if (filters.facets.enabledArchived === "disabled" && p.enabled) return false;
            if (filters.facets.enabledArchived === "archived" && !p.archived) return false;
        }

        return true;
    });

    const handleArchive = async (pipeline: Pipeline) => {
        try {
            await archiveMutation.mutateAsync({
                databaseId: pipeline.databaseId,
                pipelineId: pipeline.pipelineId,
            });
            setArchiveConfirmPipeline(null);
        } catch (err) {
            console.error("Failed to archive pipeline:", err);
        }
    };

    const renderPipelineCard = (pipeline: Pipeline) => {
        const executionTypeBadge = (type: ExecutionType) => {
            const colors = {
                Lambda: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
                SQS: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
                EventBridge: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
                DeadlineCloud: "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200",
            };
            return (
                <span className={`px-2 py-1 text-xs font-semibold rounded ${colors[type]}`}>
                    {type}
                </span>
            );
        };

        const contextMenuItems: ContextMenuItem[] = [
            {
                label: "Edit",
                onSelect: () => setEditPipeline(pipeline),
                hidden: !can("PUT", "/database/{databaseId}/pipelines/{pipelineId}"),
            },
            {
                label: "Templates",
                onSelect: () => setTemplatePipeline(pipeline),
            },
            {
                label: "Archive",
                onSelect: () => setArchiveConfirmPipeline(pipeline),
                danger: true,
                hidden: !can("DELETE", "/database/{databaseId}/pipelines/{pipelineId}"),
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
                                    {pipeline.pipelineName}
                                </span>
                                {executionTypeBadge(pipeline.executionConfig.executionType)}
                                {!pipeline.enabled && (
                                    <span className="px-2 py-1 text-xs bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-300 rounded">
                                        Disabled
                                    </span>
                                )}
                                {pipeline.archived && (
                                    <span className="px-2 py-1 text-xs bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200 rounded">
                                        Archived
                                    </span>
                                )}
                            </div>
                            <div className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                                {pipeline.pipelineId}
                            </div>
                            <div className="text-sm text-gray-500 dark:text-gray-500 mt-1">
                                {pipeline.databaseId === "GLOBAL" ? "GLOBAL" : `Database: ${pipeline.databaseId}`}
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
        return <div className="p-4 text-gray-600 dark:text-gray-400">Loading pipelines...</div>;
    }

    if (error) {
        return <div className="p-4 text-red-600 dark:text-red-400">Error loading pipelines: {String(error)}</div>;
    }

    const canCreatePipeline = can("POST", "/database/{databaseId}/pipelines");

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Pipelines</h1>
                {canCreatePipeline && (
                    <button
                        onClick={() => setCreateDialogOpen(true)}
                        className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 dark:bg-blue-700 dark:hover:bg-blue-600"
                    >
                        Create Pipeline
                    </button>
                )}
            </div>

            <div className="flex items-center gap-2">
                <FilterBar
                    value={filters}
                    onChange={setFilters}
                    facets={[
                        {
                            key: "executionType",
                            label: "Execution Type",
                            options: [
                                { label: "Lambda", value: "Lambda" },
                                { label: "SQS", value: "SQS" },
                                { label: "EventBridge", value: "EventBridge" },
                                { label: "DeadlineCloud", value: "DeadlineCloud" },
                            ],
                        },
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

            {filteredPipelines.length === 0 ? (
                <div className="p-8 text-center text-gray-500 dark:text-gray-400">
                    No pipelines found matching the current filters.
                </div>
            ) : (
                <CategoryGroupedList
                    items={filteredPipelines}
                    groupBy={(p) => p.category || "Uncategorized"}
                    renderItem={renderPipelineCard}
                />
            )}

            {createDialogOpen && databaseId && (
                <Dialog
                    open={createDialogOpen}
                    onOpenChange={setCreateDialogOpen}
                    title="Create Pipeline"
                >
                    <PipelineForm
                        mode="create"
                        databaseId={databaseId}
                        onDone={() => setCreateDialogOpen(false)}
                    />
                </Dialog>
            )}

            {editPipeline && (
                <Dialog
                    open={!!editPipeline}
                    onOpenChange={(open) => !open && setEditPipeline(null)}
                    title="Edit Pipeline"
                >
                    <PipelineForm
                        mode="edit"
                        databaseId={editPipeline.databaseId}
                        initial={editPipeline}
                        onDone={() => setEditPipeline(null)}
                    />
                </Dialog>
            )}

            {templatePipeline && (
                <Dialog
                    open={!!templatePipeline}
                    onOpenChange={(open) => !open && setTemplatePipeline(null)}
                    title={`Templates for ${templatePipeline.pipelineName}`}
                >
                    <TemplateEditor
                        databaseId={templatePipeline.databaseId}
                        pipelineId={templatePipeline.pipelineId}
                    />
                </Dialog>
            )}

            {archiveConfirmPipeline && (
                <Dialog
                    open={!!archiveConfirmPipeline}
                    onOpenChange={(open) => !open && setArchiveConfirmPipeline(null)}
                    title="Archive Pipeline"
                    footer={
                        <>
                            <button
                                onClick={() => setArchiveConfirmPipeline(null)}
                                className="px-4 py-2 bg-gray-200 text-gray-800 rounded hover:bg-gray-300 dark:bg-gray-700 dark:text-gray-200 dark:hover:bg-gray-600"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={() => handleArchive(archiveConfirmPipeline)}
                                className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 dark:bg-red-700 dark:hover:bg-red-600"
                            >
                                Archive
                            </button>
                        </>
                    }
                >
                    <p className="text-gray-700 dark:text-gray-300">
                        Are you sure you want to archive <strong>{archiveConfirmPipeline.pipelineName}</strong>?
                        This action can be undone by including archived pipelines and unarchiving.
                    </p>
                </Dialog>
            )}
        </div>
    );
};

export default PipelinesPage;
