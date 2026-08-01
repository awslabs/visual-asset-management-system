/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { useLocation } from "react-router-dom";
import { useExecutionDetails } from "../api/queries";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import StatusBadge from "../components/StatusBadge";
import ConfigEditor from "../components/ConfigEditor";
import Breadcrumb from "../components/Breadcrumb";
import DataTable from "../components/DataTable";
import ExecutionLogViewer from "./ExecutionLogViewer";
import type { ExecutionStatus } from "../types";
import { type ColumnDef } from "@tanstack/react-table";
import { PREVIEW_FILE_PATTERN } from "../../../common/constants/fileFormats";

interface ExecutionDetailPageProps {
    executionId: string;
}

type TabKey = "inputs" | "pipelines" | "outputs" | "logs";

/** Bordered section container so detail content reads as grouped cards, not floating labels. */
const Card: React.FC<{ title?: string; children: React.ReactNode; className?: string }> = ({
    title,
    children,
    className = "",
}) => (
    <section
        className={`bg-surface-container border border-border-default rounded-lg shadow-sm ${className}`}
    >
        {title && (
            <header className="px-4 py-2 border-b border-border-default">
                <h2 className="text-base font-bold text-text-primary">{title}</h2>
            </header>
        )}
        <div className="p-3">{children}</div>
    </section>
);

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
        return t === "pipelines" || t === "outputs" || t === "logs" ? t : "inputs";
    }, [location.search]);
    const [activeTab, setActiveTab] = useState<TabKey>(initialTab);

    const canViewLogs = can("GET", "/workflows/executions/{executionId}/logs");

    // Per-pipeline Monaco editor state: { [pipelineIdx]: true if editor is visible }
    const [expandedEditors, setExpandedEditors] = useState<Record<number, boolean>>({});

    if (isLoading) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-surface text-text-primary">
                <div className="text-center">
                    <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 dark:border-blue-400 mb-4" />
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

    const tabs: { key: TabKey; label: string; hidden?: boolean }[] = [
        { key: "inputs", label: "Inputs" },
        { key: "pipelines", label: "Pipelines" },
        { key: "outputs", label: "Outputs" },
        { key: "logs", label: "Logs", hidden: !canViewLogs },
    ];

    // Table column definitions for the (potentially long) input/output files + metadata lists.
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
    const inputMetadataRows = flattenInputMetadata(execution.inputMetadata || []);
    const metadataColumns: ColumnDef<any, any>[] = [
        { accessorKey: "assetId", header: "Asset" },
        { accessorKey: "filePath", header: "File" },
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
                c.getValue() !== undefined && c.getValue() !== null
                    ? formatBytes(c.getValue())
                    : "—",
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
        { accessorKey: "metadataKey", header: "Key" },
        {
            accessorKey: "metadataValue",
            header: "Value",
            cell: (c) => <MetadataValueCell value={c.getValue()} />,
        },
    ];

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
                    {execution.outputFileBaseExecutionPathExtension &&
                        execution.outputFileBaseExecutionPathExtension !== "/" && (
                            <Field label="Output path prefix" mono>
                                {execution.outputFileBaseExecutionPathExtension}
                            </Field>
                        )}
                </div>
                {execution.executionError && (
                    <div className="mt-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded">
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
                    <div className="mt-4 p-3 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded">
                        <p className="text-sm font-semibold text-yellow-800 dark:text-yellow-300 mb-1">
                            Warning:
                        </p>
                        <p className="text-sm text-yellow-700 dark:text-yellow-400 mt-1">
                            Some collections were truncated:{" "}
                            {execution.truncatedCollections.join(", ")}
                        </p>
                    </div>
                )}
            </Card>

            {/* Tabs. Styled after Cloudscape's Tabs: the strip sits on the container surface with a
                bottom divider, the selected tab is lifted onto that surface with an accent underline,
                and unselected tabs shade on hover — otherwise the buttons read as floating text. */}
            <div className="orch-outline border-b border-border-default">
                <nav className="flex gap-1" role="tablist">
                    {tabs
                        .filter((t) => !t.hidden)
                        .map((t) => (
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

            {/* Tab Content. Cloudscape's Tabs puts padding-block:16px between the tab bar and its
                panel and no inline padding; -mt-4 cancels the parent's space-y-4 so the two gaps are
                not additive, then pt-4 supplies exactly that 16px. */}
            <div className="-mt-4 pt-4">
                {activeTab === "inputs" && (
                    <div className="space-y-4">
                        <Card title={`Input Files (${execution.inputFiles?.length || 0})`}>
                            {execution.inputFiles && execution.inputFiles.length > 0 ? (
                                <DataTable
                                    columns={inputFileColumns}
                                    rows={execution.inputFiles}
                                    pageSize={25}
                                    flush
                                />
                            ) : (
                                <p className="text-text-secondary">No input files</p>
                            )}
                        </Card>
                        <Card title={`Input Metadata (${inputMetadataRows.length})`}>
                            {inputMetadataRows.length > 0 ? (
                                <DataTable
                                    columns={metadataColumns}
                                    rows={inputMetadataRows}
                                    pageSize={25}
                                    flush
                                />
                            ) : (
                                <p className="text-text-secondary">No input metadata</p>
                            )}
                        </Card>
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
                                        <div className="mb-4 p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded">
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
                                            <h4 className="text-sm font-semibold mb-2">
                                                Executed Configuration
                                            </h4>
                                            {pipeline.renderedConfigTruncated && (
                                                <p className="text-sm text-text-secondary mb-1">
                                                    Configuration was truncated for display.
                                                </p>
                                            )}
                                            {!expandedEditors[idx] ? (
                                                <div>
                                                    <pre className="text-sm overflow-auto p-3 bg-surface-secondary border border-border-default rounded max-h-[300px]">
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
                                                <div className="border border-border-default rounded overflow-hidden">
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
                            </div>
                        </Card>

                        {/* Files */}
                        {execution.outputs?.files && execution.outputs.files.length > 0 && (
                            <Card title={`Output Files (${execution.outputs.files.length})`}>
                                <DataTable
                                    columns={outputFileColumns}
                                    rows={execution.outputs.files}
                                    pageSize={25}
                                    flush
                                />
                            </Card>
                        )}

                        {/* Metadata */}
                        {execution.outputs?.metadata && execution.outputs.metadata.length > 0 && (
                            <Card title={`Output Metadata (${execution.outputs.metadata.length})`}>
                                <DataTable
                                    columns={outputMetadataColumns}
                                    rows={execution.outputs.metadata}
                                    pageSize={25}
                                    flush
                                />
                            </Card>
                        )}

                        {/* Results */}
                        {execution.outputs?.results && execution.outputs.results.length > 0 && (
                            <Card title="Output Results">
                                <div className="space-y-2">
                                    {execution.outputs.results.map((result: any, idx: number) => (
                                        <div
                                            key={idx}
                                            className="p-3 bg-surface-secondary border border-border-default rounded"
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
                            !execution.outputs?.results?.length && (
                                <Card title="Outputs">
                                    <p className="text-text-secondary">No outputs</p>
                                </Card>
                            )}
                    </div>
                )}

                {activeTab === "logs" && canViewLogs && (
                    <Card title="Logs">
                        <ExecutionLogViewer
                            executionId={executionId}
                            pipelines={execution.pipelines || []}
                        />
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

/** Flatten input-metadata records ({assetId, filePath, metadata:{k:v}}) to one row per key/value. */
function flattenInputMetadata(records: any[]): any[] {
    const rows: any[] = [];
    (records || []).forEach((rec) => {
        const md = rec.metadata || {};
        const keys = Object.keys(md);
        if (keys.length === 0) return;
        keys.forEach((key) => {
            rows.push({
                assetId: rec.assetId || "",
                filePath: rec.filePath || "",
                key,
                value: md[key],
            });
        });
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
