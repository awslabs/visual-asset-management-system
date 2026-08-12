/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useNavigate } from "react-router-dom";
import QuickView from "../components/QuickView";
import StatusBadge from "../components/StatusBadge";
import InfoTooltip from "../components/InfoTooltip";
import { OUTPUTS_SCOPE_HELP } from "./outputsHelp";
import { useExecutionDetails } from "../api/queries";

interface ExecutionQuickViewProps {
    open: boolean;
    onClose: () => void;
    executionId: string;
}

/** Bordered group used throughout the quick-view panel so data reads as cards, not a text block. */
const Section: React.FC<{
    title: string;
    children: React.ReactNode;
    /** Rendered beside the title — used for the outputs-scope help icon. */
    titleAdornment?: React.ReactNode;
}> = ({ title, children, titleAdornment }) => (
    <div className="orch-outline rounded-lg border border-border-default bg-surface-container p-3">
        <div className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-2">
            {title}
            {titleAdornment ? <span className="ml-2 normal-case">{titleAdornment}</span> : null}
        </div>
        {children}
    </div>
);

/** Label/value row. */
/**
 * An input file's path WITHIN its asset. The stored `inputAssetFileKey` is asset-root-relative and
 * begins with the assetId segment ("/{assetId}/folder/file.ext"), so it is stripped here — the panel
 * already identifies the execution, and repeating the id on every row (once from the key, once from a
 * prepended prefix) just crowded the path out of view.
 */
const inputPath = (f: any): string => {
    const key: string = f?.inputAssetFileKey || f?.relativeFilePath || "";
    if (!key) return "—";
    const prefix = `/${f?.assetId}`;
    return f?.assetId && key.startsWith(prefix) ? key.slice(prefix.length) || "/" : key;
};

const Row: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
    <div className="flex justify-between gap-3 py-0.5">
        <span className="text-text-secondary">{label}</span>
        <span className="text-text-primary text-right break-all">{value}</span>
    </div>
);

const MoreNote: React.FC<{ count?: number }> = ({ count }) => (
    <div className="text-sm text-text-secondary mt-1">
        {count ? `+ ${count} more` : "+ more"} — see full details
    </div>
);

const ExecutionQuickView: React.FC<ExecutionQuickViewProps> = ({ open, onClose, executionId }) => {
    const { data: details, isLoading, error } = useExecutionDetails(executionId);
    const navigate = useNavigate();

    const formatDate = (dateStr?: string) => {
        if (!dateStr) return "—";
        try {
            return new Date(dateStr).toLocaleString();
        } catch {
            return dateStr;
        }
    };

    const calculateDuration = (start?: string, stop?: string) => {
        if (!start) return "—";
        if (!stop) return "In progress";
        try {
            const startTime = new Date(start).getTime();
            const stopTime = new Date(stop).getTime();
            const durationMs = stopTime - startTime;
            const seconds = Math.floor(durationMs / 1000);
            const minutes = Math.floor(seconds / 60);
            const hours = Math.floor(minutes / 60);
            if (hours > 0) return `${hours}h ${minutes % 60}m`;
            if (minutes > 0) return `${minutes}m ${seconds % 60}s`;
            return `${seconds}s`;
        } catch {
            return "—";
        }
    };

    // Quick view is a summary — cap each list so a large execution can't blow out the pane.
    const QUICK_VIEW_LIMIT = 10;
    const inputFiles = details?.inputFiles || [];
    const outputFiles = details?.outputs?.files || [];
    const outputResults = details?.outputs?.results || [];
    // Metadata outputs were omitted here, so a metadata-producing pipeline (the GenAI labelers) looked
    // like it had produced nothing. All three kinds the backend returns are listed, each labelled so a
    // reader can tell a written file from a metadata record from a result.
    const outputMetadata = details?.outputs?.metadata || [];
    const outputTotal = outputFiles.length + outputMetadata.length + outputResults.length;

    return (
        <QuickView open={open} onClose={onClose} title="Execution Details">
            {isLoading && <div className="text-text-secondary">Loading...</div>}
            {error && <div className="text-vams-error">Failed to load execution details</div>}
            {details && (
                <div className="space-y-3 text-sm">
                    {/* Status & timing */}
                    <Section title="Status">
                        <div className="flex items-center gap-2 mb-2">
                            <StatusBadge status={details.executionStatus} />
                        </div>
                        <Row label="Started" value={formatDate(details.executionStartDate)} />
                        <Row label="Stopped" value={formatDate(details.executionStopDate)} />
                        <Row
                            label="Duration"
                            value={calculateDuration(
                                details.executionStartDate,
                                details.executionStopDate
                            )}
                        />
                        <Row label="Trigger" value={details.triggerType || "—"} />
                        {details.triggeredByUserId && (
                            <Row label="User" value={details.triggeredByUserId} />
                        )}
                    </Section>

                    {/* Error */}
                    {details.executionError && (
                        <div className="orch-outline rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 p-3 min-w-0">
                            <div className="font-semibold text-red-800 dark:text-red-300 mb-1">
                                Error
                            </div>
                            <pre className="text-sm text-red-700 dark:text-red-400 whitespace-pre-wrap break-words max-w-full overflow-x-auto font-mono">
                                {details.executionError}
                            </pre>
                        </div>
                    )}

                    {/* Pipelines */}
                    {details.pipelines && details.pipelines.length > 0 && (
                        <Section title={`Pipelines (${details.pipelines.length})`}>
                            <div className="flex flex-wrap gap-1.5">
                                {details.pipelines.map((pipeline: any, idx: number) => (
                                    <span
                                        key={idx}
                                        className="orch-outline px-2 py-1 text-sm rounded bg-surface-secondary text-text-primary border border-border-default"
                                    >
                                        {pipeline.name ||
                                            pipeline.pipelineId ||
                                            `Pipeline ${idx + 1}`}
                                        {pipeline.executionStatus &&
                                            ` · ${pipeline.executionStatus}`}
                                    </span>
                                ))}
                            </div>
                        </Section>
                    )}

                    {/* Inputs */}
                    {inputFiles.length > 0 && (
                        <Section title={`Inputs (${inputFiles.length})`}>
                            <ul className="space-y-1">
                                {inputFiles
                                    .slice(0, QUICK_VIEW_LIMIT)
                                    .map((f: any, idx: number) => (
                                        <li
                                            key={idx}
                                            className="font-mono text-sm break-all text-text-primary"
                                        >
                                            {inputPath(f)}
                                        </li>
                                    ))}
                            </ul>
                            {inputFiles.length > QUICK_VIEW_LIMIT && (
                                <MoreNote count={inputFiles.length - QUICK_VIEW_LIMIT} />
                            )}
                        </Section>
                    )}

                    {/* Output target — stated before the file list so the panel says WHERE the run
                        wrote, not just what it wrote. Shown even with no output files (a results-only
                        run has a target of "none"). */}
                    <Section title="Output Target">
                        <Row
                            label="Output Type"
                            value={
                                details.outputLocationType === "none"
                                    ? "Results only (no asset output)"
                                    : details.outputLocationType || "—"
                            }
                        />
                        <Row label="Output Database ID" value={details.outputDatabaseId || "—"} />
                        <Row label="Output Asset ID" value={details.outputAssetId || "—"} />
                        {/* The RESOLVED prefix, i.e. what the run actually wrote under — any {{tag}}
                            in the workflow's default was substituted at launch. "/" means the outputs
                            went to the asset root, which reads more clearly than a bare slash. */}
                        <Row
                            label="Output Path Prefix"
                            value={
                                !details.outputFileBaseExecutionPathExtension ||
                                details.outputFileBaseExecutionPathExtension === "/"
                                    ? "None (asset root)"
                                    : details.outputFileBaseExecutionPathExtension
                            }
                        />
                    </Section>

                    {/* Outputs */}
                    {outputTotal > 0 && (
                        <Section
                            title={`Outputs (${outputTotal})`}
                            titleAdornment={
                                <InfoTooltip
                                    text={OUTPUTS_SCOPE_HELP}
                                    label="What this list includes"
                                />
                            }
                        >
                            <ul className="space-y-1">
                                {outputFiles
                                    .slice(0, QUICK_VIEW_LIMIT)
                                    .map((f: any, idx: number) => (
                                        <li
                                            key={`f-${idx}`}
                                            className="font-mono text-sm break-all text-text-primary"
                                        >
                                            {f.relativeFilePath || "—"}
                                            {f.assetFileVersionId
                                                ? ` (v ${f.assetFileVersionId})`
                                                : ""}
                                        </li>
                                    ))}
                                {outputMetadata
                                    .slice(0, Math.max(0, QUICK_VIEW_LIMIT - outputFiles.length))
                                    .map((m: any, idx: number) => (
                                        <li
                                            key={`m-${idx}`}
                                            className="font-mono text-sm break-all text-text-primary"
                                        >
                                            {m.relativeFilePath || "metadata"}
                                            <span className="ml-1 font-sans text-text-secondary">
                                                (metadata)
                                            </span>
                                        </li>
                                    ))}
                                {outputResults
                                    .slice(
                                        0,
                                        Math.max(
                                            0,
                                            QUICK_VIEW_LIMIT -
                                                outputFiles.length -
                                                outputMetadata.length
                                        )
                                    )
                                    .map((r: any, idx: number) => (
                                        <li
                                            key={`r-${idx}`}
                                            className="font-mono text-sm break-all text-text-primary"
                                        >
                                            {r.relativeFilePath || "result"}
                                            <span className="ml-1 font-sans text-text-secondary">
                                                (result)
                                            </span>
                                        </li>
                                    ))}
                            </ul>
                            {outputTotal > QUICK_VIEW_LIMIT && <MoreNote />}
                        </Section>
                    )}

                    {details.truncatedCollections && details.truncatedCollections.length > 0 && (
                        <div className="text-sm text-yellow-700 dark:text-yellow-400">
                            Note: some collections were truncated:{" "}
                            {details.truncatedCollections.join(", ")}
                        </div>
                    )}

                    <div className="pt-2">
                        <button
                            onClick={() => {
                                navigate(`/executions/${executionId}`);
                                onClose();
                            }}
                            className="text-blue-600 dark:text-blue-400 hover:underline"
                        >
                            Open full details →
                        </button>
                    </div>
                </div>
            )}
        </QuickView>
    );
};

export default ExecutionQuickView;
