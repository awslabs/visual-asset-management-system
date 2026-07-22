/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useNavigate } from "react-router-dom";
import QuickView from "../components/QuickView";
import StatusBadge from "../components/StatusBadge";
import { useExecutionDetails } from "../api/queries";

interface ExecutionQuickViewProps {
    open: boolean;
    onClose: () => void;
    executionId: string;
}

/** Bordered group used throughout the quick-view panel so data reads as cards, not a text block. */
const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
    <div className="rounded-lg border border-border-default bg-surface-container p-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-text-secondary mb-2">
            {title}
        </div>
        {children}
    </div>
);

/** Label/value row. */
const Row: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
    <div className="flex justify-between gap-3 py-0.5">
        <span className="text-text-secondary">{label}</span>
        <span className="text-text-primary text-right break-all">{value}</span>
    </div>
);

const MoreNote: React.FC<{ count?: number }> = ({ count }) => (
    <div className="text-xs text-text-secondary mt-1">
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
                        <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 p-3 min-w-0">
                            <div className="font-semibold text-red-800 dark:text-red-300 mb-1">
                                Error
                            </div>
                            <pre className="text-xs text-red-700 dark:text-red-400 whitespace-pre-wrap break-words max-w-full overflow-x-auto font-mono">
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
                                        className="px-2 py-1 text-xs rounded bg-surface-secondary text-text-primary border border-border-default"
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
                                            className="font-mono text-xs break-all text-text-primary"
                                        >
                                            {f.assetId ? `${f.assetId}:` : ""}
                                            {f.inputAssetFileKey || f.relativeFilePath || "—"}
                                        </li>
                                    ))}
                            </ul>
                            {inputFiles.length > QUICK_VIEW_LIMIT && (
                                <MoreNote count={inputFiles.length - QUICK_VIEW_LIMIT} />
                            )}
                        </Section>
                    )}

                    {/* Outputs */}
                    {(outputFiles.length > 0 || outputResults.length > 0) && (
                        <Section title={`Outputs (${outputFiles.length + outputResults.length})`}>
                            <ul className="space-y-1">
                                {outputFiles
                                    .slice(0, QUICK_VIEW_LIMIT)
                                    .map((f: any, idx: number) => (
                                        <li
                                            key={`f-${idx}`}
                                            className="font-mono text-xs break-all text-text-primary"
                                        >
                                            {f.relativeFilePath || "—"}
                                            {f.assetFileVersionId
                                                ? ` (v ${f.assetFileVersionId})`
                                                : ""}
                                        </li>
                                    ))}
                                {outputResults
                                    .slice(0, Math.max(0, QUICK_VIEW_LIMIT - outputFiles.length))
                                    .map((r: any, idx: number) => (
                                        <li
                                            key={`r-${idx}`}
                                            className="font-mono text-xs break-all text-text-primary"
                                        >
                                            {r.relativeFilePath || "result"}
                                        </li>
                                    ))}
                            </ul>
                            {outputFiles.length + outputResults.length > QUICK_VIEW_LIMIT && (
                                <MoreNote />
                            )}
                        </Section>
                    )}

                    {details.truncatedCollections && details.truncatedCollections.length > 0 && (
                        <div className="text-xs text-yellow-700 dark:text-yellow-400">
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
