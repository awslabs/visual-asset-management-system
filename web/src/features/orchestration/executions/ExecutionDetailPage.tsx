/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { useLocation } from "react-router-dom";
import { useExecutionDetails, useExecutionDetailMetadata } from "../api/queries";
import type { DetailMetadataCollection } from "../api/executions";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import { toastErrorMessage } from "../components/ToastProvider";
import StatusBadge from "../components/StatusBadge";
import ConfigEditor from "../components/ConfigEditor";
import Breadcrumb from "../components/Breadcrumb";
import DataTable from "../components/DataTable";
import InfoTooltip from "../components/InfoTooltip";
import { OUTPUTS_SCOPE_HELP } from "./outputsHelp";
import ExecutionLogViewer from "./ExecutionLogViewer";
import type { ExecutionStatus } from "../types";
import { type ColumnDef } from "@tanstack/react-table";
import { PREVIEW_FILE_PATTERN } from "../../../common/constants/fileFormats";

interface ExecutionDetailPageProps {
    executionId: string;
}

type TabKey = "inputs" | "pipelines" | "outputs" | "settings" | "logs";

/** Bordered section container so detail content reads as grouped cards, not floating labels. */
const Card: React.FC<{
    title?: string;
    children: React.ReactNode;
    className?: string;
    /** Rendered beside the title — used for the outputs-scope help icon. */
    titleAdornment?: React.ReactNode;
}> = ({ title, children, className = "", titleAdornment }) => (
    <section
        className={`orch-outline bg-surface-container border border-border-default rounded-lg shadow-sm ${className}`}
    >
        {title && (
            <header className="orch-outline px-4 py-2 border-b border-border-default flex items-center gap-2">
                <h2 className="text-base font-bold text-text-primary">{title}</h2>
                {titleAdornment}
            </header>
        )}
        <div className="p-3">{children}</div>
    </section>
);

/**
 * Marker on a section whose rows the server bounded. It sits in the section's own header rather than
 * only in the page-level warning, so a shortened table cannot be read as the complete set while
 * scrolled away from that banner.
 */
const TruncatedBadge: React.FC<{ label?: string }> = ({ label = "Partial" }) => (
    <span
        className="orch-outline px-2 py-0.5 text-xs font-bold bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300 rounded"
        title="This section holds fewer rows than the execution produced. The remaining rows are not retrievable through this view."
    >
        {label}
    </span>
);

/** Tier-1 route behind the paged metadata read; the panel's contents depend on it, never the tab. */
const DETAILS_METADATA_ROUTE = "/workflows/executions/{executionId}/details/metadata";

/**
 * The paged route's name for each metadata collection the details view embeds. A collection absent
 * here has no paged counterpart — the file and results collections stay inline, so for those the
 * truncation flag is the only signal the view is incomplete.
 */
const PAGED_METADATA_COLLECTION: Record<string, DetailMetadataCollection> = {
    inputMetadata: "input",
    inputDatabaseMetadata: "inputDatabase",
    "outputs.metadata": "output",
};

/** Rows per client-side page in the detail tables. */
const TABLE_PAGE_SIZE = 25;

interface MetadataSectionProps {
    executionId: string;
    /** The details-response collection name ("inputMetadata", "outputs.metadata", ...). */
    collectionName: string;
    /** Section heading, given the number of rows currently rendered. */
    title: (count: number) => string;
    columns: ColumnDef<any, any>[];
    /** The rows the details response embedded, already in render shape. */
    inlineRows: any[];
    /** Maps the paged route's rows into the same render shape as `inlineRows`. */
    mapPagedRows: (items: any[]) => any[];
    truncated: boolean;
    /** Tier-1 permission for the paged route. */
    canPage: boolean;
    emptyText: string;
    titleAdornment?: React.ReactNode;
}

/**
 * A metadata table that escalates to the paged route when the details view returned the collection
 * bounded.
 *
 * The details response caps each collection, so a large run's table would otherwise be a silent subset
 * of itself. When a collection comes back flagged, its rows are re-read through
 * `GET .../details/metadata`, which walks every pipeline step of the run — so the table shows the
 * collection, not the first slice of it. An unflagged collection is rendered from the details response
 * and costs no extra request.
 *
 * Paging is in two layers: the table pages locally over what is loaded, and "Load more rows" fetches
 * the next server page. The section is only free of the partial marker once the walk has reached the
 * last page — a bounded read, a failed one, or one the caller lacks Tier-1 for all stay marked.
 */
const MetadataSection: React.FC<MetadataSectionProps> = ({
    executionId,
    collectionName,
    title,
    columns,
    inlineRows,
    mapPagedRows,
    truncated,
    canPage,
    emptyText,
    titleAdornment,
}) => {
    const collection = PAGED_METADATA_COLLECTION[collectionName];
    const escalated = truncated && canPage && !!collection;
    const paged = useExecutionDetailMetadata(executionId, collection || "input", escalated);
    const pagedRows = React.useMemo(
        () => mapPagedRows(((paged.data?.pages as any[]) || []).flatMap((p: any) => p.Items || [])),
        [paged.data, mapPagedRows]
    );

    const pagedAvailable = escalated && !!paged.data;
    // On a failed escalation the details view's own subset is still the best available answer, so it is
    // shown with the failure stated inline rather than replaced by an empty table.
    const rows = pagedAvailable ? pagedRows : inlineRows;
    const fullyRetrieved = pagedAvailable && !paged.isError && !paged.hasNextPage;
    const stillPartial = truncated && !fullyRetrieved;

    return (
        <Card
            title={title(rows.length)}
            titleAdornment={
                stillPartial || titleAdornment ? (
                    <>
                        {stillPartial && <TruncatedBadge />}
                        {titleAdornment}
                    </>
                ) : undefined
            }
        >
            {escalated && paged.isLoading && (
                <p className="text-text-secondary mb-2">Loading the complete set…</p>
            )}
            {escalated && paged.isError && (
                <p role="alert" className="text-sm text-vams-error mb-2">
                    Could not load the complete set: {toastErrorMessage(paged.error)} The rows below
                    are the subset the detail view returned.
                </p>
            )}
            {stillPartial && !escalated && (
                <p className="text-sm text-yellow-700 dark:text-yellow-400 mb-2">
                    {canPage
                        ? "This execution produced more rows than this view returns. The rows below are a subset."
                        : "This execution produced more rows than this view returns, and you do not have permission to page the complete set. The rows below are a subset."}
                </p>
            )}
            {rows.length > 0 ? (
                <DataTable columns={columns} rows={rows} pageSize={TABLE_PAGE_SIZE} flush />
            ) : (
                !(escalated && paged.isLoading) && (
                    <p className="text-text-secondary">{emptyText}</p>
                )
            )}
            {escalated && paged.hasNextPage && (
                <div className="flex items-center gap-3 mt-3 text-sm">
                    <button
                        onClick={() => paged.fetchNextPage()}
                        disabled={paged.isFetchingNextPage}
                        className="px-3 py-1.5 border border-border-input rounded text-text-primary hover:bg-surface-hover disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        {paged.isFetchingNextPage ? "Loading…" : "Load more rows"}
                    </button>
                    <span className="text-text-secondary">
                        {rows.length} rows loaded · more available
                    </span>
                </div>
            )}
        </Card>
    );
};

/**
 * Stated where a bounded FILE collection is rendered. The file collections have no paged route to
 * escalate to, so the flag is the reader's only signal that rows are missing — and unlike a metadata
 * section, there is nothing more to load.
 */
const NoEscalationNote: React.FC = () => (
    <p className="text-sm text-yellow-700 dark:text-yellow-400 mb-2">
        This execution produced more files than this view returns. The rows below are a subset, and
        the remaining rows are not retrievable through this view.
    </p>
);

/**
 * The output path prefix the run actually wrote under. Stored RESOLVED, so any {{tag}} in the
 * workflow's default was already substituted at launch. An empty value or a bare "/" both mean the
 * asset root, which reads better spelled out than as a lone slash.
 */
const outputPathPrefixText = (prefix?: string): string =>
    !prefix || prefix === "/" ? "None (asset root)" : prefix;

/** True when a settings object carries anything worth rendering. */
const hasSettings = (config?: Record<string, any>): boolean =>
    !!config && Object.keys(config).length > 0;

/** Human label for a stored (camelCase) systemConfig key. */
const SETTING_LABELS: Record<string, string> = {
    inputFileArity: "Input file count",
    assetScope: "Asset selection rules",
    metadataInputs: "Metadata provided",
    inputFileFilters: "Input file filters",
    concurrencyRestriction: "Concurrency restriction",
    outputTarget: "Output destination",
    allowWorkflowTriggerChaining: "Allow workflow trigger chaining",
    defaultOutputFileBaseExecutionPathExtension: "Default output path prefix",
    requireTemplate: "Requires a template",
    allowCustomTemplateOverride: "Allows a custom configuration",
    auxPreviewPipelineSuffix: "Auxiliary preview suffix",
};

/** One settings value, rendered readably rather than as raw JSON. */
const settingValue = (value: any): string => {
    if (value === null || value === undefined || value === "") return "None";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (Array.isArray(value)) return value.length ? value.join(", ") : "None";
    if (typeof value === "object") {
        const entries = Object.entries(value);
        // A boolean map (assetScope / metadataInputs) reads best as the list of what is enabled.
        if (entries.length && entries.every(([, v]) => typeof v === "boolean")) {
            const on = entries.filter(([, v]) => v).map(([k]) => k);
            return on.length ? on.join(", ") : "None";
        }
        return JSON.stringify(value);
    }
    return String(value);
};

/**
 * A settings block. When `overrides` is supplied, each key the template changed is flagged — that is what
 * explains why a step's effective settings differ from its pipeline's own defaults.
 */
const SettingsGrid: React.FC<{
    config?: Record<string, any>;
    overrides?: Record<string, any>;
}> = ({ config, overrides }) => {
    if (!hasSettings(config)) {
        return <p className="text-text-secondary">No settings recorded.</p>;
    }
    return (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2">
            {Object.keys(config as Record<string, any>)
                .sort()
                .map((key) => (
                    <div key={key} className="text-sm">
                        <span className="text-text-secondary">{SETTING_LABELS[key] || key}:</span>{" "}
                        <span className="text-text-primary">
                            {settingValue((config as Record<string, any>)[key])}
                        </span>
                        {overrides && key in overrides && (
                            <span className="ml-2 text-xs font-bold text-blue-600 dark:text-blue-400">
                                overridden
                            </span>
                        )}
                    </div>
                ))}
        </div>
    );
};

/** Header progress summary: "Pipeline N of M · <status>" + a segmented bar colored per pipeline. */
const TERMINAL_OK = new Set(["SUCCEEDED", "COMPLETE"]);
const TERMINAL_BAD = new Set(["FAILED", "ABORTED", "TIMED_OUT"]);

const PipelineProgress: React.FC<{ pipelines: any[] }> = ({ pipelines }) => {
    if (!pipelines || pipelines.length === 0) return null;
    const total = pipelines.length;
    const done = pipelines.filter((p) => TERMINAL_OK.has(p.executionStatus)).length;
    // The "current" pipeline: first RUNNING, else first non-terminal (NEW), else the last one.
    const runningIdx = pipelines.findIndex((p) => p.executionStatus === "RUNNING");
    const queuedIdx = pipelines.findIndex(
        (p) => !TERMINAL_OK.has(p.executionStatus) && !TERMINAL_BAD.has(p.executionStatus)
    );
    const currentIdx = runningIdx >= 0 ? runningIdx : queuedIdx >= 0 ? queuedIdx : total - 1;
    const current = pipelines[currentIdx] || {};
    const allDone = done === total;

    const segColor = (status: string) =>
        TERMINAL_OK.has(status)
            ? "bg-green-500"
            : TERMINAL_BAD.has(status)
            ? "bg-red-500"
            : status === "RUNNING"
            ? "bg-blue-500 animate-pulse"
            : "bg-gray-300 dark:bg-gray-600"; // NEW / queued

    return (
        <div className="flex items-center gap-2">
            <span className="text-sm text-text-secondary">
                {allDone
                    ? `${total} pipeline${total > 1 ? "s" : ""} complete`
                    : `Pipeline ${currentIdx + 1} of ${total}${
                          current.executionStatus ? ` · ${current.executionStatus}` : ""
                      }`}
            </span>
            <span className="flex items-center gap-0.5" aria-hidden="true">
                {pipelines.map((p, i) => (
                    <span
                        key={i}
                        title={`${p.name || p.pipelineId || `Pipeline ${i + 1}`}: ${
                            p.executionStatus || "NEW"
                        }`}
                        className={`inline-block h-1.5 w-5 rounded-full ${segColor(
                            p.executionStatus
                        )}`}
                    />
                ))}
            </span>
        </div>
    );
};

const TABS: { key: TabKey; label: string; hidden?: boolean }[] = [
    { key: "inputs", label: "Inputs" },
    { key: "pipelines", label: "Pipelines" },
    { key: "outputs", label: "Outputs" },
    { key: "settings", label: "Settings" },
    { key: "logs", label: "Logs" },
];

/**
 * Column sets for the (potentially long) input/output file and metadata tables.
 *
 * Module constants rather than render-body arrays: `DataTable` passes `columns` straight into
 * `useReactTable`, which rebuilds its column model whenever the array identity changes. The page polls
 * every few seconds while a run is in flight, so a fresh array per render would make every poll
 * re-render every visible cell of three tables.
 */
const inputFileColumns: ColumnDef<any, any>[] = [
    { accessorKey: "databaseId", header: "Database" },
    { accessorKey: "assetId", header: "Asset" },
    {
        accessorKey: "inputAssetFileKey",
        header: "File",
        // The stored key is asset-root-relative and begins with the assetId segment
        // ("/{assetId}/folder/file.laz"). The row already names the asset in its own column, so
        // show only the path within the asset.
        cell: (c) => {
            const f = c.row.original as any;
            const key: string = c.getValue() || "";
            const prefix = `/${f.assetId}`;
            const shown = f.assetId && key.startsWith(prefix) ? key.slice(prefix.length) : key;
            return <span className="font-mono text-sm">{shown || "/"}</span>;
        },
    },
    // The concrete S3 version the run read (resolved at launch). Blank for folder/whole-asset
    // selections, which have no single version.
    { accessorKey: "versionId", header: "S3 version", cell: (c) => c.getValue() || "—" },
    {
        id: "open",
        header: "",
        cell: (c) => {
            const f = c.row.original;
            if (!f.assetId || !f.databaseId) return null;
            // Whole-asset/folder selections have no single file to deep-link to → open the asset.
            const key = f.inputAssetFileKey;
            const isFile = key && key !== "/" && !key.endsWith("/");
            return (
                <a
                    href={buildFileManagerLink(f.databaseId, f.assetId, isFile ? key : "")}
                    className="text-sm text-blue-600 dark:text-blue-400 hover:underline whitespace-nowrap"
                >
                    {isFile ? "Open in file manager" : "Open asset"}
                </a>
            );
        },
    },
];

// Metadata rows are recorded per pipeline, each pipeline's rows describing the entities IT reads, so
// the same entity legitimately appears once per pipeline and the Pipeline column is what tells those
// rows apart.
const metadataColumns: ColumnDef<any, any>[] = [
    { accessorKey: "assetId", header: "Asset" },
    { accessorKey: "filePath", header: "File" },
    { accessorKey: "pipelineId", header: "Pipeline", cell: (c) => c.getValue() || "—" },
    // Metadata and file attributes are separate metadataInputs a pipeline can be granted
    // independently, so a row says which of the two it came from.
    {
        accessorKey: "source",
        header: "Source",
        cell: (c) => (c.getValue() === "attributes" ? "Attribute" : "Metadata"),
    },
    { accessorKey: "key", header: "Key" },
    {
        accessorKey: "value",
        header: "Value",
        cell: (c) => <MetadataValueCell value={c.getValue()} />,
    },
];

// Database metadata belongs to a source database, not an asset, so the asset/file columns above
// have nothing to show for it. It belongs to every pipeline of the run — database metadata is
// envelope-global — so a multi-step run repeats each row once per pipeline.
const databaseMetadataColumns: ColumnDef<any, any>[] = [
    { accessorKey: "databaseId", header: "Database" },
    { accessorKey: "pipelineId", header: "Pipeline", cell: (c) => c.getValue() || "—" },
    { accessorKey: "key", header: "Key" },
    {
        accessorKey: "value",
        header: "Value",
        cell: (c) => <MetadataValueCell value={c.getValue()} />,
    },
];

const outputFileColumns: ColumnDef<any, any>[] = [
    { accessorKey: "relativeFilePath", header: "Path" },
    { accessorKey: "pipelineId", header: "Pipeline", cell: (c) => c.getValue() || "—" },
    // No Asset/Database column: every output row shares the execution's single output target,
    // which the header states once. The row identifies the FILE (path + version).
    {
        accessorKey: "assetFileVersionId",
        header: "Version",
        cell: (c) => c.getValue() || "—",
    },
    {
        accessorKey: "fileSize",
        header: "Size",
        cell: (c) =>
            c.getValue() !== undefined && c.getValue() !== null ? formatBytes(c.getValue()) : "—",
    },
    {
        id: "open",
        header: "",
        cell: (c) => {
            const f = c.row.original;
            if (!f.assetId || !f.databaseId) return null;
            // Preview files ({baseFile}.previewFile.{ext}) are viewed via their base file, so
            // link to the base path rather than the preview file itself.
            return (
                <a
                    href={buildFileManagerLink(
                        f.databaseId,
                        f.assetId,
                        baseFilePathForPreview(f.relativeFilePath)
                    )}
                    className="text-sm text-blue-600 dark:text-blue-400 hover:underline whitespace-nowrap"
                >
                    Open in file manager
                </a>
            );
        },
    },
];

const outputMetadataColumns: ColumnDef<any, any>[] = [
    { accessorKey: "targetFilePath", header: "File" },
    // Output metadata is recorded per pipeline execution, so two steps writing the same key onto the
    // same file are two rows identical but for the producing pipeline.
    { accessorKey: "pipelineId", header: "Pipeline", cell: (c) => c.getValue() || "—" },
    { accessorKey: "metadataKey", header: "Key" },
    {
        accessorKey: "metadataValue",
        header: "Value",
        cell: (c) => <MetadataValueCell value={c.getValue()} />,
    },
];

/** Label/value pair used inside the detail cards. */
const Field: React.FC<{ label: string; children: React.ReactNode; mono?: boolean }> = ({
    label,
    children,
    mono,
}) => (
    <div className="text-sm">
        <span className="text-text-secondary">{label}:</span>{" "}
        <span className={mono ? "font-mono break-all" : "break-all"}>{children}</span>
    </div>
);

const ExecutionDetailPage: React.FC<ExecutionDetailPageProps> = ({ executionId }) => {
    const { data: execution, isLoading, error } = useExecutionDetails(executionId);
    const { can } = useAllowedRoutes();
    const location = useLocation();

    // Initialize the active tab from the ?tab= query param so the "Logs" row action (and any
    // shared/deep link) opens directly on the requested tab instead of the default Inputs tab.
    const initialTab = React.useMemo<TabKey>(() => {
        const t = new URLSearchParams(location.search).get("tab");
        return t === "pipelines" || t === "outputs" || t === "settings" || t === "logs"
            ? t
            : "inputs";
    }, [location.search]);
    const [activeTab, setActiveTab] = useState<TabKey>(initialTab);

    const canViewLogs = can("GET", "/workflows/executions/{executionId}/logs");
    // The paged metadata read carries the same Tier-2 rule as details, but is its own Tier-1 route — a
    // deployment whose constraints omit it answers 403. Checked here so a truncated section explains
    // that its complete set is out of reach instead of escalating into a failed request.
    const canPageMetadata = can("GET", DETAILS_METADATA_ROUTE);

    // Per-pipeline Monaco editor state: { [pipelineIdx]: true if editor is visible }
    const [expandedEditors, setExpandedEditors] = useState<Record<number, boolean>>({});

    // The metadata collections flatten to one row per key, so a real asset's records expand into tens of
    // thousands of row objects. Memoized on the response, which the poll replaces only when the payload
    // actually changed (TanStack's structural sharing keeps the identity otherwise), so a tick against an
    // unchanged run re-flattens nothing.
    const inputMetadataRows = React.useMemo(
        () => flattenInputMetadata(execution?.inputMetadata || []),
        [execution?.inputMetadata]
    );
    const databaseMetadataRows = React.useMemo(
        () => flattenInputMetadata(execution?.inputDatabaseMetadata || []),
        [execution?.inputDatabaseMetadata]
    );
    // Collections the server reports as partial. Looked up per section so each one is marked on its own
    // evidence — the two metadata collections share one read server-side, so both can be named at once.
    const truncatedSet = React.useMemo(
        () => new Set(execution?.truncatedCollections || []),
        [execution?.truncatedCollections]
    );
    const isTruncated = React.useCallback(
        (collection: string) => truncatedSet.has(collection),
        [truncatedSet]
    );
    // A truncated metadata collection is re-read through the paged route, so the banner must not call it
    // a subset — the sections that stay a subset are the ones with no paged counterpart (the file and
    // results collections), plus any metadata collection this caller cannot page.
    const { escalatedCollections, cappedInline } = React.useMemo(() => {
        const all = execution?.truncatedCollections || [];
        const escalated = all.filter((c) => !!PAGED_METADATA_COLLECTION[c] && canPageMetadata);
        return {
            escalatedCollections: escalated,
            cappedInline: all.filter((c) => !escalated.includes(c)),
        };
    }, [execution?.truncatedCollections, canPageMetadata]);

    if (isLoading) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <div className="text-center">
                    <div className="orch-outline inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 dark:border-blue-400 mb-4" />
                    <p>Loading execution details...</p>
                </div>
            </div>
        );
    }

    if (error || !execution) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <div className="text-center">
                    <p className="text-vams-error text-xl mb-2">Error Loading Execution</p>
                    <p className="text-text-secondary">
                        {error ? String(error) : "Execution not found"}
                    </p>
                </div>
            </div>
        );
    }

    const duration =
        execution.executionStartDate && execution.executionStopDate
            ? calculateDuration(execution.executionStartDate, execution.executionStopDate)
            : null;

    // Breadcrumb trail: Executions › {workflow's executions} › this execution. The middle crumb
    // deep-links to the executions list pre-filtered to this execution's workflow.
    const workflowExecutionsHref = `/executions?workflowId=${encodeURIComponent(
        execution.workflowId || ""
    )}&workflowDatabaseId=${encodeURIComponent(execution.workflowDatabaseId || "")}`;

    return (
        <div className="orchestration-root orchestration-page min-h-screen bg-surface text-text-primary space-y-4">
            {/* Breadcrumb: Executions › {workflow's executions} › this execution. Uses the workflow
                name (falling back to its id). */}
            <Breadcrumb
                items={[
                    { label: "Executions", to: "/executions" },
                    ...(execution.workflowId
                        ? [
                              {
                                  label: (execution as any).workflowName || execution.workflowId,
                                  to: workflowExecutionsHref,
                              },
                          ]
                        : []),
                    { label: execution.workflowExecutionId },
                ]}
            />

            {/* Header card */}
            <Card>
                <div className="flex items-center gap-3 mb-4 flex-wrap">
                    <h1 className="text-text-primary">Execution Detail</h1>
                    <StatusBadge status={execution.executionStatus as ExecutionStatus} />
                    <PipelineProgress pipelines={execution.pipelines || []} />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2">
                    <Field label="Execution ID" mono>
                        {execution.workflowExecutionId}
                    </Field>
                    <Field label="Workflow ID" mono>
                        {execution.workflowId}
                    </Field>
                    <Field label="Database ID" mono>
                        {execution.workflowDatabaseId}
                    </Field>
                    <Field label="Trigger">
                        {execution.triggerType || "N/A"}{" "}
                        {execution.triggeredByUserId && `by ${execution.triggeredByUserId}`}
                    </Field>
                    <Field label="Start">
                        {execution.executionStartDate
                            ? formatDate(execution.executionStartDate)
                            : "N/A"}
                    </Field>
                    <Field label="Stop">
                        {execution.executionStopDate
                            ? formatDate(execution.executionStopDate)
                            : "N/A"}
                    </Field>
                    {duration && <Field label="Duration">{duration}</Field>}
                    {execution.executionGroupId && (
                        <Field label="Group ID" mono>
                            {execution.executionGroupId}
                        </Field>
                    )}
                    {/* The output target is broken out into its three parts, matching the executions
                        list columns, so the destination is readable field-by-field rather than as one
                        composite string. */}
                    <Field label="Output Type">
                        {execution.outputLocationType === "none"
                            ? "Results only (no asset output)"
                            : execution.outputLocationType || "N/A"}
                    </Field>
                    <Field label="Output Database ID" mono>
                        {execution.outputDatabaseId || "N/A"}
                    </Field>
                    <Field label="Output Asset ID">
                        {execution.outputAssetId ? (
                            execution.outputDatabaseId ? (
                                // Link the output asset to its asset view.
                                <a
                                    href={buildFileManagerLink(
                                        execution.outputDatabaseId,
                                        execution.outputAssetId,
                                        ""
                                    )}
                                    className="font-mono text-blue-600 dark:text-blue-400 hover:underline"
                                >
                                    {execution.outputAssetId}
                                </a>
                            ) : (
                                <span className="font-mono">{execution.outputAssetId}</span>
                            )
                        ) : (
                            "N/A"
                        )}
                    </Field>
                    {/* Always shown, alongside the other three output fields: "no prefix" is itself
                        information about where the run wrote, and hiding the row made the answer
                        indistinguishable from the field not existing. */}
                    <Field label="Output Path Prefix" mono>
                        {outputPathPrefixText(execution.outputFileBaseExecutionPathExtension)}
                    </Field>
                </div>
                {execution.executionError && (
                    <div className="orch-outline mt-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded">
                        <p className="text-sm font-semibold text-red-800 dark:text-red-300 mb-1">
                            Execution Error:
                        </p>
                        {/* <pre> preserves multi-line / structured error text; a <p> collapsed the
                            whitespace and made stack traces / JSON errors unreadable. */}
                        <pre className="text-sm text-red-700 dark:text-red-400 whitespace-pre-wrap break-words font-mono">
                            {execution.executionError}
                        </pre>
                    </div>
                )}
                {execution.truncatedCollections && execution.truncatedCollections.length > 0 && (
                    <div className="orch-outline mt-4 p-3 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded">
                        <p className="text-sm font-semibold text-yellow-800 dark:text-yellow-300 mb-1">
                            Warning:
                        </p>
                        {cappedInline.length > 0 && (
                            <p className="text-sm text-yellow-700 dark:text-yellow-400 mt-1">
                                This execution produced more rows than this view returns, so these
                                sections are a subset: {cappedInline.join(", ")}. Each one is also
                                marked where it is shown.
                            </p>
                        )}
                        {escalatedCollections.length > 0 && (
                            <p className="text-sm text-yellow-700 dark:text-yellow-400 mt-1">
                                These sections held more rows than the detail view returns and are
                                read separately, a page at a time: {escalatedCollections.join(", ")}
                                .
                            </p>
                        )}
                    </div>
                )}
            </Card>

            {/* Tabs. Styled after Cloudscape's Tabs: the strip sits on the container surface with a
                bottom divider, the selected tab is lifted onto that surface with an accent underline,
                and unselected tabs shade on hover — otherwise the buttons read as floating text. */}
            <div className="orch-outline border-b border-border-default">
                <nav className="flex gap-1" role="tablist">
                    {TABS.filter((t) => !t.hidden).map((t) => (
                        <button
                            key={t.key}
                            role="tab"
                            aria-selected={activeTab === t.key}
                            onClick={() => setActiveTab(t.key)}
                            className={`orch-outline px-3 py-2 -mb-px border-b-2 font-bold text-sm rounded-t transition-colors ${
                                activeTab === t.key
                                    ? "border-blue-600 dark:border-blue-400 text-blue-600 dark:text-blue-400"
                                    : "border-transparent text-text-secondary hover:text-text-primary hover:bg-surface-hover"
                            }`}
                        >
                            {t.label}
                        </button>
                    ))}
                </nav>
            </div>

            {/* Tab Content. The parent's space-y-4 already places 16px below the tab strip — the same
                gap Cloudscape's Tabs uses (padding-block: 16px) — so this wrapper adds NOTHING. The
                previous `-mt-4 pt-4` cancelled that margin and then re-added it as padding, which is
                why the gap never shrank. */}
            <div>
                {activeTab === "inputs" && (
                    <div className="space-y-4">
                        <Card
                            title={`Input Files (${execution.inputFiles?.length || 0})`}
                            titleAdornment={
                                isTruncated("inputFiles") ? <TruncatedBadge /> : undefined
                            }
                        >
                            {isTruncated("inputFiles") && <NoEscalationNote />}
                            {execution.inputFiles && execution.inputFiles.length > 0 ? (
                                <DataTable
                                    columns={inputFileColumns}
                                    rows={execution.inputFiles}
                                    pageSize={TABLE_PAGE_SIZE}
                                    flush
                                />
                            ) : (
                                <p className="text-text-secondary">No input files</p>
                            )}
                        </Card>
                        {/* Widest entity first — database, then asset/file — matching the order the
                            pipeline and workflow forms present the metadata toggles in. The database's
                            own metadata is a separate collection because it belongs to no asset, so it
                            has no asset/file column to sit under. */}
                        <MetadataSection
                            executionId={executionId}
                            collectionName="inputDatabaseMetadata"
                            title={(n) => `Input Database Metadata (${n})`}
                            columns={databaseMetadataColumns}
                            inlineRows={databaseMetadataRows}
                            mapPagedRows={flattenInputMetadata}
                            truncated={isTruncated("inputDatabaseMetadata")}
                            canPage={canPageMetadata}
                            emptyText="No input database metadata"
                        />
                        <MetadataSection
                            executionId={executionId}
                            collectionName="inputMetadata"
                            title={(n) => `Input Asset and File Metadata (${n})`}
                            columns={metadataColumns}
                            inlineRows={inputMetadataRows}
                            mapPagedRows={flattenInputMetadata}
                            truncated={isTruncated("inputMetadata")}
                            canPage={canPageMetadata}
                            emptyText="No input asset or file metadata"
                        />
                    </div>
                )}

                {activeTab === "pipelines" && (
                    <div className="space-y-4">
                        {execution.pipelines && execution.pipelines.length > 0 ? (
                            execution.pipelines.map((pipeline: any, idx: number) => (
                                <Card key={idx}>
                                    <div className="flex items-center gap-3 mb-3">
                                        <h3 className="text-lg font-semibold">
                                            {pipeline.name ||
                                                pipeline.pipelineId ||
                                                "Unknown Pipeline"}
                                        </h3>
                                        {pipeline.executionStatus && (
                                            <StatusBadge
                                                status={pipeline.executionStatus as ExecutionStatus}
                                            />
                                        )}
                                        {pipeline.endStatePipeline && (
                                            <span className="px-2 py-1 text-sm bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200 rounded">
                                                End state
                                            </span>
                                        )}
                                    </div>
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mb-4">
                                        <Field label="Pipeline ID" mono>
                                            {pipeline.pipelineId}
                                        </Field>
                                        {pipeline.pipelineType && (
                                            <Field label="Type">{pipeline.pipelineType}</Field>
                                        )}
                                        {pipeline.executionStartDate && (
                                            <Field label="Start">
                                                {formatDate(pipeline.executionStartDate)}
                                            </Field>
                                        )}
                                        {pipeline.executionStopDate && (
                                            <Field label="Stop">
                                                {formatDate(pipeline.executionStopDate)}
                                            </Field>
                                        )}
                                    </div>

                                    {/* Template Snapshot */}
                                    {(pipeline.templateId ||
                                        pipeline.templateTags ||
                                        pipeline.customTemplateOverrideUsed !== undefined) && (
                                        <div className="orch-outline mb-4 p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded">
                                            <h4 className="text-sm font-semibold text-blue-800 dark:text-blue-300 mb-2">
                                                Template Snapshot
                                            </h4>
                                            {pipeline.templateId && (
                                                <div className="text-sm mb-1">
                                                    <span className="text-blue-700 dark:text-blue-400">
                                                        Template ID:
                                                    </span>{" "}
                                                    <span className="font-mono">
                                                        {pipeline.templateId}
                                                    </span>
                                                </div>
                                            )}
                                            {Array.isArray(pipeline.templateTags) &&
                                                pipeline.templateTags.length > 0 && (
                                                    <div className="text-sm mb-1">
                                                        <span className="text-blue-700 dark:text-blue-400">
                                                            Tags:
                                                        </span>
                                                        <ul className="mt-1 ml-4 list-disc space-y-0.5">
                                                            {pipeline.templateTags.map(
                                                                (t: any, ti: number) => (
                                                                    <li
                                                                        key={ti}
                                                                        className="font-mono text-sm"
                                                                    >
                                                                        {t?.key}
                                                                        {" = "}
                                                                        {typeof t?.value ===
                                                                        "object"
                                                                            ? JSON.stringify(
                                                                                  t.value
                                                                              )
                                                                            : String(
                                                                                  t?.value ?? ""
                                                                              )}
                                                                    </li>
                                                                )
                                                            )}
                                                        </ul>
                                                    </div>
                                                )}
                                            {pipeline.customTemplateOverrideUsed !== undefined && (
                                                <div className="text-sm">
                                                    <span className="text-blue-700 dark:text-blue-400">
                                                        Custom Override:
                                                    </span>{" "}
                                                    {pipeline.customTemplateOverrideUsed
                                                        ? "Yes"
                                                        : "No"}
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    {/* Rendered Config Body — the exact configuration sent to this pipeline */}
                                    {pipeline.renderedConfig && (
                                        <div>
                                            <div className="flex items-center gap-2 mb-2">
                                                <h4 className="text-sm font-semibold">
                                                    Executed Configuration
                                                </h4>
                                                {pipeline.renderedConfigTruncated && (
                                                    <TruncatedBadge label="Truncated" />
                                                )}
                                            </div>
                                            {/* The body always goes to Amazon S3 for the pipeline to
                                                read, so a truncated inline copy still has a complete
                                                source to point at. */}
                                            {pipeline.renderedConfigTruncated && (
                                                <div className="orch-outline mb-2 p-3 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded">
                                                    <p className="text-sm text-yellow-800 dark:text-yellow-300">
                                                        The configuration below is a truncated copy.
                                                    </p>
                                                    {pipeline.renderedConfigLocation?.key && (
                                                        <p className="text-sm text-yellow-700 dark:text-yellow-400 mt-1">
                                                            Complete body in Amazon S3:{" "}
                                                            <span className="font-mono break-all">
                                                                s3://
                                                                {
                                                                    pipeline.renderedConfigLocation
                                                                        .bucket
                                                                }
                                                                /
                                                                {
                                                                    pipeline.renderedConfigLocation
                                                                        .key
                                                                }
                                                            </span>
                                                        </p>
                                                    )}
                                                </div>
                                            )}
                                            {!expandedEditors[idx] ? (
                                                <div>
                                                    <pre className="orch-outline text-sm overflow-auto p-3 bg-surface-secondary border border-border-default rounded max-h-[300px]">
                                                        {pipeline.renderedConfig}
                                                    </pre>
                                                    <button
                                                        onClick={() =>
                                                            setExpandedEditors((prev) => ({
                                                                ...prev,
                                                                [idx]: true,
                                                            }))
                                                        }
                                                        className="mt-2 px-3 py-1 text-sm text-blue-600 dark:text-blue-400 hover:underline"
                                                    >
                                                        View in editor
                                                    </button>
                                                </div>
                                            ) : (
                                                <div className="orch-outline border border-border-default rounded overflow-hidden">
                                                    <ConfigEditor
                                                        value={pipeline.renderedConfig}
                                                        language={pipeline.configFormat || "json"}
                                                        readOnly
                                                        height="300px"
                                                    />
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </Card>
                            ))
                        ) : (
                            <Card title="Pipeline Timeline">
                                <p className="text-text-secondary">No pipeline data</p>
                            </Card>
                        )}
                    </div>
                )}

                {activeTab === "outputs" && (
                    <div className="space-y-4">
                        {/* The output TARGET, before the file list: where this run wrote (or that it
                            wrote no asset at all), so the destination is stated on the tab that shows
                            the files rather than only in the page header. */}
                        <Card title="Output Target">
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-x-8 gap-y-2">
                                <Field label="Output Type">
                                    {execution.outputLocationType === "none"
                                        ? "Results only (no asset output)"
                                        : execution.outputLocationType || "N/A"}
                                </Field>
                                <Field label="Output Database ID" mono>
                                    {execution.outputDatabaseId || "N/A"}
                                </Field>
                                <Field label="Output Asset ID" mono>
                                    {execution.outputAssetId || "N/A"}
                                </Field>
                                <Field label="Output Path Prefix" mono>
                                    {outputPathPrefixText(
                                        execution.outputFileBaseExecutionPathExtension
                                    )}
                                </Field>
                            </div>
                        </Card>

                        {/* Files */}
                        {execution.outputs?.files && execution.outputs.files.length > 0 && (
                            <Card
                                title={`Output Files (${execution.outputs.files.length})`}
                                titleAdornment={
                                    <>
                                        {isTruncated("outputs.files") && <TruncatedBadge />}
                                        <InfoTooltip
                                            text={OUTPUTS_SCOPE_HELP}
                                            label="What this list includes"
                                        />
                                    </>
                                }
                            >
                                {isTruncated("outputs.files") && <NoEscalationNote />}
                                <DataTable
                                    columns={outputFileColumns}
                                    rows={execution.outputs.files}
                                    pageSize={TABLE_PAGE_SIZE}
                                    flush
                                />
                            </Card>
                        )}

                        {/* Metadata */}
                        {(!!execution.outputs?.metadata?.length ||
                            isTruncated("outputs.metadata")) && (
                            <MetadataSection
                                executionId={executionId}
                                collectionName="outputs.metadata"
                                title={(n) => `Output Metadata (${n})`}
                                columns={outputMetadataColumns}
                                inlineRows={execution.outputs?.metadata || []}
                                mapPagedRows={identityRows}
                                truncated={isTruncated("outputs.metadata")}
                                canPage={canPageMetadata}
                                emptyText="No output metadata"
                            />
                        )}

                        {/* Results */}
                        {execution.outputs?.results && execution.outputs.results.length > 0 && (
                            <Card
                                title="Output Results"
                                titleAdornment={
                                    isTruncated("outputs.results") ? <TruncatedBadge /> : undefined
                                }
                            >
                                <div className="space-y-2">
                                    {execution.outputs.results.map((result: any, idx: number) => (
                                        <div
                                            key={idx}
                                            className="orch-outline p-3 bg-surface-secondary border border-border-default rounded"
                                        >
                                            {result.resultsContentTruncated && (
                                                <div className="mb-2 text-sm text-yellow-600 dark:text-yellow-400">
                                                    (Content truncated)
                                                </div>
                                            )}
                                            <pre className="text-sm overflow-auto whitespace-pre-wrap break-words">
                                                {result.resultsContent ||
                                                    JSON.stringify(result, null, 2)}
                                            </pre>
                                        </div>
                                    ))}
                                </div>
                            </Card>
                        )}

                        {!execution.outputs?.files?.length &&
                            !execution.outputs?.metadata?.length &&
                            !isTruncated("outputs.metadata") &&
                            !execution.outputs?.results?.length && (
                                <Card
                                    title="Outputs"
                                    titleAdornment={
                                        <InfoTooltip
                                            text={OUTPUTS_SCOPE_HELP}
                                            label="What this list includes"
                                        />
                                    }
                                >
                                    {/* Said explicitly: a pipeline that wrote only to the auxiliary
                                        bucket lands here, and "No outputs" alone reads as a failure
                                        rather than as out-of-scope. */}
                                    <p className="text-text-secondary">
                                        No asset outputs were recorded for this execution. Files
                                        written to the auxiliary location are not tracked as
                                        outputs.
                                    </p>
                                </Card>
                            )}
                    </div>
                )}

                {activeTab === "settings" && (
                    <div className="space-y-4">
                        {/* Workflow level. Read LIVE from the workflow, so it is labelled "current":
                            a workflow edited since this run legitimately differs from what ran. */}
                        <Card title="Workflow settings (current)">
                            <p className="text-sm text-text-secondary mb-3">
                                Read from the workflow as it stands now. The per-step settings below
                                are the snapshot recorded when this execution ran, so the two can
                                differ if the workflow has been edited since.
                            </p>
                            <SettingsGrid config={execution.workflowSystemConfig} />
                        </Card>

                        {/* Per step: the settings that step actually ran under, and what its template
                            changed. This cannot be reconstructed later — a template may be edited or
                            archived after the run, which is why it is snapshotted. */}
                        {(execution.pipelines || []).length > 0 ? (
                            (execution.pipelines || []).map((pipeline: any, idx: number) => (
                                <Card
                                    key={`settings-${idx}`}
                                    title={`Step ${idx + 1}: ${
                                        pipeline.pipelineName || pipeline.pipelineId
                                    }`}
                                >
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2 mb-3">
                                        <Field label="Template" mono>
                                            {pipeline.templateId || "None"}
                                        </Field>
                                        <Field label="Custom configuration used">
                                            {pipeline.customTemplateOverrideUsed ? "Yes" : "No"}
                                        </Field>
                                    </div>
                                    {hasSettings(pipeline.effectiveSystemConfig) ? (
                                        <>
                                            <SettingsGrid
                                                config={pipeline.effectiveSystemConfig}
                                                overrides={pipeline.templateOverrides}
                                            />
                                            {hasSettings(pipeline.templateOverrides) && (
                                                <p className="text-sm text-text-secondary mt-3">
                                                    Values marked{" "}
                                                    <span className="font-bold text-blue-600 dark:text-blue-400">
                                                        overridden
                                                    </span>{" "}
                                                    were changed by the chosen template; the rest
                                                    come from the pipeline&apos;s own settings.
                                                </p>
                                            )}
                                        </>
                                    ) : (
                                        <p className="text-text-secondary">
                                            No settings recorded for this step. Executions from
                                            before settings capture was added show nothing here.
                                        </p>
                                    )}
                                </Card>
                            ))
                        ) : (
                            <Card title="Step settings">
                                <p className="text-text-secondary">No pipeline steps recorded.</p>
                            </Card>
                        )}
                    </div>
                )}

                {activeTab === "logs" && (
                    <Card title="Logs">
                        {canViewLogs ? (
                            <ExecutionLogViewer
                                executionId={executionId}
                                pipelines={execution.pipelines || []}
                            />
                        ) : (
                            // The tab stays reachable so the logs are discoverable, and says why they
                            // are unavailable rather than presenting an empty viewer.
                            <p className="text-text-secondary">
                                You do not have permission to view execution logs.
                            </p>
                        )}
                    </Card>
                )}
            </div>
        </div>
    );
};

// Metadata value cell: metadata values can be large, so long strings collapse with an inline
// "Show/Hide" toggle to keep the table scannable. Objects/arrays render as compact JSON.
const MetadataValueCell: React.FC<{ value: any }> = ({ value }) => {
    const [expanded, setExpanded] = React.useState(false);
    const text =
        value === null || value === undefined
            ? ""
            : typeof value === "string"
            ? value
            : JSON.stringify(value);
    const LONG = 120;
    const isLong = text.length > LONG;
    return (
        <div className="max-w-md">
            <span className="font-mono text-sm break-all whitespace-pre-wrap">
                {isLong && !expanded ? `${text.slice(0, LONG)}…` : text}
            </span>
            {isLong && (
                <button
                    onClick={() => setExpanded((e) => !e)}
                    className="ml-2 text-sm text-blue-600 dark:text-blue-400 hover:underline"
                >
                    {expanded ? "Show less" : "Show more"}
                </button>
            )}
        </div>
    );
};

/**
 * Rows already in render shape. The output-metadata collection is one key/value per row in both the
 * details response and the paged route, so it needs no reshaping.
 */
function identityRows(items: any[]): any[] {
    return items || [];
}

/**
 * Flatten input-metadata records ({pipelineId, databaseId, assetId, filePath, metadata:{k:v}}) to one
 * row per key/value. databaseId is carried through for the database-scoped collection, whose rows carry
 * no asset or file. pipelineId is the pipeline that read the entity: the records are per pipeline, so
 * dropping it collapses a run's pipelines into rows that cannot be told apart.
 */
// One table row per key, from BOTH of a record's content maps: `metadata` and `attributes`. The two are
// gated independently by a pipeline's metadataInputs (fileMetadata vs fileAttributes), so a record may
// legitimately carry attributes and no metadata — reading `metadata` alone would drop that record's row
// entirely rather than merely omitting a column, and no truncation flag would explain the absence.
// `source` says which map a row came from, so the two stay distinguishable once flattened together.
function flattenInputMetadata(records: any[]): any[] {
    const rows: any[] = [];
    (records || []).forEach((rec) => {
        const emit = (map: any, source: "metadata" | "attributes") => {
            Object.keys(map || {}).forEach((key) => {
                rows.push({
                    pipelineId: rec.pipelineId || "",
                    databaseId: rec.databaseId || "",
                    assetId: rec.assetId || "",
                    filePath: rec.filePath || "",
                    source,
                    key,
                    value: map[key],
                });
            });
        };
        emit(rec.metadata, "metadata");
        emit(rec.attributes, "attributes");
    });
    return rows;
}

// Helper functions

/**
 * Deep link to a file inside the View Assets file manager. Uses the HashRouter asset detail
 * route with a ?filePath query param (ViewAsset treats the query param as the authoritative
 * file selector). Links to the file, not a specific version.
 */
/**
 * A preview file is stored next to its base file as {baseFile}.previewFile.{ext}. Viewing goes to
 * the base file, so strip the ".previewFile.{ext}" suffix; non-preview paths are returned unchanged.
 */
function baseFilePathForPreview(relativeFilePath: string): string {
    if (!relativeFilePath) return relativeFilePath;
    const idx = relativeFilePath.indexOf(PREVIEW_FILE_PATTERN);
    return idx === -1 ? relativeFilePath : relativeFilePath.slice(0, idx);
}

function buildFileManagerLink(
    databaseId: string,
    assetId: string,
    relativeFilePath: string
): string {
    const path = `/#/databases/${encodeURIComponent(databaseId)}/assets/${encodeURIComponent(
        assetId
    )}`;
    if (!relativeFilePath) return path;
    return `${path}?filePath=${encodeURIComponent(relativeFilePath)}`;
}

function formatDate(dateString: string): string {
    try {
        return new Date(dateString).toLocaleString();
    } catch {
        return dateString;
    }
}

function calculateDuration(start: string, stop: string): string {
    try {
        const startMs = new Date(start).getTime();
        const stopMs = new Date(stop).getTime();
        const durationMs = stopMs - startMs;

        const seconds = Math.floor(durationMs / 1000);
        const minutes = Math.floor(seconds / 60);
        const hours = Math.floor(minutes / 60);

        if (hours > 0) {
            return `${hours}h ${minutes % 60}m`;
        } else if (minutes > 0) {
            return `${minutes}m ${seconds % 60}s`;
        } else {
            return `${seconds}s`;
        }
    } catch {
        return "N/A";
    }
}

function formatBytes(bytes: number): string {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + " " + sizes[i];
}

export default ExecutionDetailPage;
