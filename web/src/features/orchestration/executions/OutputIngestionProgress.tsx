/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * "Ingesting outputs (n of m)": the progress of a RUNNING execution whose output files over 1 GiB
 * are being copied into the asset (GET /workflows/executions/{id}/details returns executionPhase
 * INGESTING_OUTPUTS and outputIngestion while that lasts, and neither field for any other run).
 * Display only: it renders what the API returned and decides nothing. Tailwind, like the rest of the
 * orchestration module.
 */

import React from "react";
import type { OutputIngestion, OutputIngestionFile, OutputIngestionFileStatus } from "../types";

export const INGESTING_OUTPUTS_PHASE = "INGESTING_OUTPUTS";

const FILE_STATUS: Record<OutputIngestionFileStatus, { label: string; className: string }> = {
    QUEUED: {
        label: "Queued",
        className: "bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300",
    },
    RUNNING: {
        label: "Copying",
        className: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
    },
    SUCCEEDED: {
        label: "Landed",
        className: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
    },
    FAILED: {
        label: "Failed",
        className: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
    },
    CANCELLED: {
        label: "Cancelled",
        className: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300",
    },
};

export function formatIngestionBytes(bytes: number): string {
    if (!bytes || bytes < 0) return "0 B";
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return `${Math.round((bytes / Math.pow(1024, i)) * 10) / 10} ${units[i]}`;
}

function percent(done: number, total: number): number {
    if (!total || total <= 0) return 0;
    return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
}

/**
 * Bytes copied so far: the run's bytesDone counts only files that have landed, so a file still
 * copying adds its planned share (bytesTotal x partsDone / partsTotal). Only the listed files (at
 * most 100) can add theirs, so a truncated list reads low, never high. Capped at bytesTotal.
 */
export function copiedIngestionBytes(outputIngestion: OutputIngestion): number {
    const landed = outputIngestion.bytesDone || 0;
    const files = Array.isArray(outputIngestion.files) ? outputIngestion.files : [];
    const inProgress = files.reduce((sum, file) => {
        if (file.status !== "RUNNING" || !file.partsTotal || file.partsTotal <= 0) return sum;
        const share = Math.min(Math.max(file.partsDone || 0, 0), file.partsTotal) / file.partsTotal;
        return sum + Math.floor((file.bytesTotal || 0) * share);
    }, 0);
    const total = outputIngestion.bytesTotal || 0;
    const copied = landed + inProgress;
    return total > 0 ? Math.min(copied, total) : copied;
}

function ProgressBar({ value, label }: { value: number; label: string }) {
    return (
        <div
            role="progressbar"
            aria-label={label}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={value}
            className="h-2 w-full rounded bg-gray-200 dark:bg-gray-700 overflow-hidden"
        >
            <div className="h-2 bg-blue-600 dark:bg-blue-400" style={{ width: `${value}%` }} />
        </div>
    );
}

export function FileStatusLabel({ status }: { status: string }) {
    const style = FILE_STATUS[status as OutputIngestionFileStatus] ?? {
        label: status || "Unknown",
        className: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300",
    };
    return (
        <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${style.className}`}>
            {style.label}
        </span>
    );
}

/**
 * Marks an output file row the large object copy engine copied into the asset (the details and paged
 * files routes return ingestionStatus "SUCCEEDED" on such a row). Text, not colour alone.
 */
export function IngestedBadge({ ingestionStatus }: { ingestionStatus?: string }) {
    if (ingestionStatus !== "SUCCEEDED") return null;
    return (
        <span
            className="ml-2 inline-block px-1.5 py-0.5 rounded text-xs font-sans font-medium bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300 whitespace-nowrap"
            title="This file was over 1 GiB and was copied into the asset after the pipeline finished"
        >
            Ingested by copy engine
        </span>
    );
}

interface Props {
    executionPhase?: string;
    outputIngestion?: OutputIngestion;
    /** The quick view shows only the summary. */
    compact?: boolean;
}

const OutputIngestionProgress: React.FC<Props> = ({ executionPhase, outputIngestion, compact }) => {
    if (executionPhase !== INGESTING_OUTPUTS_PHASE || !outputIngestion) {
        return null;
    }
    const filesTotal = outputIngestion.filesTotal || 0;
    const filesDone = outputIngestion.filesDone || 0;
    const files: OutputIngestionFile[] = Array.isArray(outputIngestion.files)
        ? outputIngestion.files
        : [];
    const summary = `Ingesting outputs (${filesDone} of ${filesTotal})`;
    // The bar follows the bytes copied, the same share as the line under it, so one large file
    // still copying moves it; a run that reports no byte total falls back to the files done.
    const bytesTotal = outputIngestion.bytesTotal || 0;
    const copiedBytes = copiedIngestionBytes(outputIngestion);
    const summaryValue =
        bytesTotal > 0 ? percent(copiedBytes, bytesTotal) : percent(filesDone, filesTotal);
    return (
        <section
            aria-label="Output ingestion"
            data-testid="output-ingestion-progress"
            className="orch-outline mt-4 p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded"
        >
            <p className="text-sm font-semibold text-blue-900 dark:text-blue-200 mb-1">{summary}</p>
            <ProgressBar value={summaryValue} label={summary} />
            <p className="text-xs text-blue-800 dark:text-blue-300 mt-1">
                {formatIngestionBytes(copiedBytes)} of {formatIngestionBytes(bytesTotal)} copied
                into the asset. The execution stays running until every file has landed.
            </p>
            {!compact && files.length > 0 && (
                <table className="mt-3 w-full text-sm" aria-label="Output files being copied">
                    <thead>
                        <tr className="text-left text-xs text-gray-600 dark:text-gray-400">
                            <th className="py-1 pr-3 font-medium">File</th>
                            <th className="py-1 pr-3 font-medium">Status</th>
                            <th className="py-1 pr-3 font-medium">Progress</th>
                            <th className="py-1 pr-3 font-medium">Size</th>
                            <th className="py-1 font-medium">Reason</th>
                        </tr>
                    </thead>
                    <tbody>
                        {files.map((file) => {
                            const value =
                                file.status === "SUCCEEDED"
                                    ? 100
                                    : percent(file.partsDone || 0, file.partsTotal || 0);
                            return (
                                <tr
                                    key={file.relativeKey}
                                    className="border-t border-blue-100 dark:border-blue-800"
                                >
                                    <td className="py-1 pr-3 font-mono break-all">
                                        {file.relativeKey}
                                    </td>
                                    <td className="py-1 pr-3">
                                        <FileStatusLabel status={file.status} />
                                    </td>
                                    <td className="py-1 pr-3 w-40">
                                        {file.partsTotal > 0 || file.status === "SUCCEEDED" ? (
                                            <div className="flex items-center gap-2">
                                                <ProgressBar
                                                    value={value}
                                                    label={`${file.relativeKey} ${value}% copied`}
                                                />
                                                <span className="text-xs whitespace-nowrap">
                                                    {value}%
                                                </span>
                                            </div>
                                        ) : (
                                            <span className="text-xs text-gray-600 dark:text-gray-400">
                                                Not started
                                            </span>
                                        )}
                                    </td>
                                    <td className="py-1 pr-3 whitespace-nowrap">
                                        {formatIngestionBytes(file.bytesTotal || 0)}
                                    </td>
                                    <td className="py-1 text-red-700 dark:text-red-400">
                                        {file.error || ""}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            )}
            {!compact && outputIngestion.filesTruncated && (
                <p className="text-xs text-blue-800 dark:text-blue-300 mt-2">
                    Showing the first {files.length} files; the counts above cover every file.
                </p>
            )}
        </section>
    );
};

export default OutputIngestionProgress;
