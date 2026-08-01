/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { usePipelines, useArchivePipeline, useDatabases } from "../api/queries";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import CategoryGroupedList from "../components/CategoryGroupedList";
import FilterBar, { FilterFacets, type FilterValue } from "../components/FilterBar";
import ContextMenu, { type ContextMenuItem } from "../components/ContextMenu";
import ArchiveConfirmDialog from "../components/ArchiveConfirmDialog";
import DatabasePickerDialog from "../components/DatabasePickerDialog";
import { btnPrimary, btnSecondary, control } from "../components/controlStyles";
import type { Pipeline, ExecutionType } from "../types";
import { appCache } from "../../../services/appCache";
import { useToast, toastErrorMessage } from "../components/ToastProvider";

interface PipelinesPageProps {
    databaseId?: string;
}

const PipelinesPage: React.FC<PipelinesPageProps> = ({ databaseId }) => {
    const toast = useToast();
    const navigate = useNavigate();
    const [includeArchived, setIncludeArchived] = useState(false);
    const [filters, setFilters] = useState<FilterValue>({
        searchText: "",
        facets: {},
    });
    // How the list is grouped/sorted: by category (default) or by database.
    const [groupBy, setGroupBy] = useState<"category" | "database">("category");
    // Create/edit are their own wizard pages (navigation), not dialogs. The global (database-less)
    // page picks a target database first (pipelines are database-scoped).
    const [dbPickerOpen, setDbPickerOpen] = useState(false);
    const [archiveConfirmPipeline, setArchiveConfirmPipeline] = useState<Pipeline | null>(null);
    // Message shown when an archive request is rejected (e.g. no Tier-2 access, transient 5xx).
    const [archiveError, setArchiveError] = useState<string | null>(null);

    // The server list omits archived pipelines unless asked for them, so selecting the Archived
    // status facet implies including them regardless of the checkbox.
    const archivedFacetSelected = filters.facets.enabledArchived === "archived";
    const fetchArchived = includeArchived || archivedFacetSelected;

    const {
        data,
        isLoading,
        error,
        fetchNextPage,
        hasNextPage,
        isFetchingNextPage,
        refetch,
        isFetching,
    } = usePipelines(databaseId, fetchArchived);
    const pipelines = React.useMemo(
        () => data?.pages?.flatMap((page: any) => page.Items) ?? [],
        [data]
    );
    const { loading: permissionsLoading, can } = useAllowedRoutes();
    const archiveMutation = useArchivePipeline();

    // Databases for the "Database" filter facet. Only offered on the global (database-less) page —
    // a database-scoped page already shows a single database's pipelines. GLOBAL is always
    // included so cross-database (GLOBAL) pipelines can be isolated.
    const { data: databases } = useDatabases(!databaseId);
    const databaseOptions = React.useMemo(() => {
        if (databaseId) return [];
        const ids = new Set<string>(["GLOBAL"]);
        (databases || []).forEach((d: any) => d?.databaseId && ids.add(d.databaseId));
        pipelines.forEach((p) => p.databaseId && ids.add(p.databaseId));
        return Array.from(ids)
            .sort()
            .map((id) => ({ label: id, value: id }));
    }, [databases, databaseId, pipelines]);

    // When a search/facet filter is active, results must cover the whole set — so drain the
    // remaining server pages. Idle browsing stays paginated (Load more).
    const filterActive = !!filters.searchText || Object.values(filters.facets).some((v) => !!v);
    React.useEffect(() => {
        if (filterActive && hasNextPage && !isFetchingNextPage) {
            fetchNextPage();
        }
    }, [filterActive, hasNextPage, isFetchingNextPage, fetchNextPage]);

    const config = appCache.getItem("config");
    const featuresEnabled = config?.featuresEnabled || [];
    // GovCloud is not re-checked here: getConfig() already refuses to synthesize a stack that enables
    // Deadline Cloud in GovCloud or any non-'aws' partition, so the feature flag cannot be present in
    // such a deployment.
    const showDeadlineCloud = featuresEnabled.includes("DEADLINECLOUD_PIPELINES");

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
        if (
            filters.facets.executionType &&
            filters.facets.executionType !== p.executionConfig.executionType
        ) {
            return false;
        }

        // Enabled/archived facet
        if (filters.facets.enabledArchived) {
            if (filters.facets.enabledArchived === "enabled" && !p.enabled) return false;
            if (filters.facets.enabledArchived === "disabled" && p.enabled) return false;
            if (filters.facets.enabledArchived === "archived" && !p.archived) return false;
        }

        // Database facet (global page only).
        if (filters.facets.databaseId && p.databaseId !== filters.facets.databaseId) {
            return false;
        }

        return true;
    });

    const handleArchive = async (pipeline: Pipeline) => {
        if (archiveMutation.isPending) return;
        setArchiveError(null);
        try {
            await archiveMutation.mutateAsync({
                databaseId: pipeline.databaseId,
                pipelineId: pipeline.pipelineId,
            });
            setArchiveConfirmPipeline(null);
            toast.success("Pipeline archived", {
                description: pipeline.pipelineName || pipeline.pipelineId,
            });
        } catch (err: any) {
            // Close the confirmation and report the failure both as a page banner (it persists while
            // the user decides what to do) and as a toast (it is visible immediately, wherever they
            // are scrolled) — the dialog itself has no error slot.
            setArchiveConfirmPipeline(null);
            toast.error("Archive failed", {
                description: `${pipeline.pipelineName || pipeline.pipelineId}: ${toastErrorMessage(
                    err
                )}`,
            });
            setArchiveError(
                `Failed to archive ${pipeline.pipelineName}: ${
                    err?.message || "the request was rejected."
                }`
            );
        }
    };

    const renderPipelineCard = (pipeline: Pipeline) => {
        const executionTypeBadge = (type: ExecutionType) => {
            const colors = {
                Lambda: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
                SQS: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
                EventBridge: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
                DeadlineCloud:
                    "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200",
            };
            return (
                <span className={`px-2 py-1 text-xs font-semibold rounded ${colors[type]}`}>
                    {type}
                </span>
            );
        };

        const isDeadlineCloudDisabled =
            pipeline.executionConfig.executionType === "DeadlineCloud" && !showDeadlineCloud;

        const contextMenuItems: ContextMenuItem[] = [
            {
                label: "Edit",
                onSelect: () =>
                    navigate(`/databases/${pipeline.databaseId}/pipelines/${pipeline.pipelineId}`),
                hidden:
                    !can("PUT", "/database/{databaseId}/pipelines/{pipelineId}") ||
                    isDeadlineCloudDisabled,
            },
            {
                label: "Templates",
                onSelect: () =>
                    navigate(
                        `/databases/${pipeline.databaseId}/pipelines/${pipeline.pipelineId}/templates`
                    ),
            },
            {
                label: "Archive",
                onSelect: () => setArchiveConfirmPipeline(pipeline),
                danger: true,
                hidden: !can("DELETE", "/database/{databaseId}/pipelines/{pipelineId}"),
            },
        ];

        return (
            <div className="orch-outline flex items-center justify-between px-3 py-1.5 bg-surface-container border border-border-default rounded hover:bg-surface-hover">
                <div className="flex-1">
                    <div className="flex items-center gap-2">
                        <span className="font-semibold text-text-primary">
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
                    <div className="text-sm text-text-secondary mt-1">{pipeline.pipelineId}</div>
                    {/* Metadata line standardized with the workflow card: Database / Category /
                        Templates count laid out inline. */}
                    <div className="text-sm text-text-secondary mt-1">
                        <span className="mr-3">
                            {pipeline.databaseId === "GLOBAL"
                                ? "Pipeline Database: GLOBAL"
                                : `Pipeline Database: ${pipeline.databaseId}`}
                        </span>
                        {pipeline.category && (
                            <span className="mr-3">Category: {pipeline.category}</span>
                        )}
                        {typeof pipeline.templateCount === "number" && (
                            <span className="mr-3">Templates: {pipeline.templateCount}</span>
                        )}
                    </div>
                </div>
                <ContextMenu
                    items={contextMenuItems}
                    trigger={
                        <button
                            aria-label={`Actions for ${pipeline.pipelineName}`}
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
    const canCreatePipeline = can("POST", "/database/{databaseId}/pipelines");

    return (
        <div className="orchestration-root orchestration-page space-y-4 bg-surface min-h-full">
            <div className="flex items-center justify-between">
                <h1 className="text-text-primary">Pipelines</h1>
                {canCreatePipeline && (
                    <button
                        onClick={() => {
                            // Database-scoped page: go straight to the create wizard. Global page:
                            // pick a target database first (pipelines are database-scoped).
                            if (databaseId) {
                                navigate(`/databases/${databaseId}/pipelines/create`);
                            } else {
                                setDbPickerOpen(true);
                            }
                        }}
                        className={btnPrimary}
                    >
                        Create Pipeline
                    </button>
                )}
            </div>

            <div className="flex items-start gap-2 flex-wrap justify-between">
                <FilterBar
                    value={filters}
                    onChange={setFilters}
                    onRefresh={() => refetch()}
                    refreshing={isFetching}
                />
                <div className="flex items-center gap-2 flex-wrap justify-end">
                    <FilterFacets
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
                                    ...(showDeadlineCloud ||
                                    pipelines.some(
                                        (p) => p.executionConfig.executionType === "DeadlineCloud"
                                    )
                                        ? [{ label: "DeadlineCloud", value: "DeadlineCloud" }]
                                        : []),
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
                            ...(databaseOptions.length > 0
                                ? [
                                      {
                                          key: "databaseId",
                                          label: "Pipeline Database",
                                          options: databaseOptions,
                                      },
                                  ]
                                : []),
                        ]}
                    />
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

            {archiveError && (
                <div className="p-3 bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400 rounded flex items-start justify-between gap-3">
                    <span>{archiveError}</span>
                    <button
                        onClick={() => setArchiveError(null)}
                        aria-label="Dismiss archive error"
                        className="bg-transparent border-0 text-lg leading-none cursor-pointer"
                    >
                        ×
                    </button>
                </div>
            )}

            {loading ? (
                <div className="p-8 text-center text-text-secondary">Loading pipelines...</div>
            ) : error ? (
                <div className="p-4 text-vams-error">Error loading pipelines: {String(error)}</div>
            ) : filteredPipelines.length === 0 ? (
                <div className="p-8 text-center text-text-secondary">
                    No pipelines found matching the current filters.
                </div>
            ) : (
                <CategoryGroupedList
                    items={filteredPipelines}
                    groupBy={(p) =>
                        groupBy === "database"
                            ? p.databaseId || "Unknown database"
                            : p.category || "Uncategorized"
                    }
                    renderItem={renderPipelineCard}
                    getKey={(p) => `${p.databaseId}:${p.pipelineId}`}
                />
            )}

            {!loading && hasNextPage && filterActive && (
                <div className="flex justify-center mt-4 text-sm text-text-secondary">
                    Searching all pipelines…
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

            <DatabasePickerDialog
                open={dbPickerOpen}
                onOpenChange={setDbPickerOpen}
                title="Create pipeline in database"
                onSelect={(db) => {
                    setDbPickerOpen(false);
                    navigate(`/databases/${db}/pipelines/create`);
                }}
            />

            {archiveConfirmPipeline && (
                <ArchiveConfirmDialog
                    entityName={archiveConfirmPipeline.pipelineName}
                    open={!!archiveConfirmPipeline}
                    onConfirm={() => handleArchive(archiveConfirmPipeline)}
                    onCancel={() => setArchiveConfirmPipeline(null)}
                />
            )}
        </div>
    );
};

export default PipelinesPage;
