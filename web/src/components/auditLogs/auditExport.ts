/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Collects an export a chunk at a time (the server cuts it into chunks of at most 4 MB and hands
 * back a token for the next) and joins the chunks into one file. The server writes the file's
 * content, including the CSV quoting and the guard against spreadsheet formulas; the page only
 * joins what it gets.
 */

import { exportAuditQuery } from "../../services/APIService";
import { AuditExportChunk, normalizeExportChunk } from "./auditSearchApiModel";

export type ExportFormat = "csv" | "json";

/** More chunks than this means something is wrong (a 10,000-row export is a few chunks). */
export const MAX_EXPORT_CHUNKS = 500;

export type ExportResult =
    | { ok: true; blob: Blob; fileName: string; chunks: number }
    | { ok: false; message: string; status?: number; cancelled?: boolean };

export const exportFileName = (format: ExportFormat, now: Date = new Date()): string =>
    `audit-log-${now
        .toISOString()
        .replace(/[-:]/g, "")
        .replace(/\.\d{3}Z$/, "Z")}.${format}`;

/** Joins CSV chunks (the header is on the first only) or JSON chunks (arrays of rows). */
export const joinChunks = (format: ExportFormat, chunks: AuditExportChunk[]): Blob => {
    if (format === "csv") {
        return new Blob([chunks.map((c) => c.text ?? "").join("")], {
            type: "text/csv;charset=utf-8",
        });
    }
    const rows: unknown[] = [];
    for (const chunk of chunks) {
        if (chunk.rows) {
            rows.push(...chunk.rows);
        } else if (chunk.text) {
            const parsed = JSON.parse(chunk.text);
            if (Array.isArray(parsed)) rows.push(...parsed);
        }
    }
    return new Blob([JSON.stringify(rows, null, 2)], { type: "application/json" });
};

export const collectExport = async ({
    queryId,
    format,
    isCancelled,
    onProgress,
    fetchChunk = exportAuditQuery as (args: any) => Promise<any[]>,
}: {
    queryId: string;
    format: ExportFormat;
    isCancelled: () => boolean;
    onProgress?: (chunks: number) => void;
    fetchChunk?: (args: {
        queryId: string;
        format: ExportFormat;
        startingToken?: string;
    }) => Promise<any[]>;
}): Promise<ExportResult> => {
    const chunks: AuditExportChunk[] = [];
    let token: string | undefined;
    do {
        if (isCancelled()) return { ok: false, message: "Export cancelled.", cancelled: true };
        const answer = await fetchChunk({ queryId, format, startingToken: token });
        if (isCancelled()) return { ok: false, message: "Export cancelled.", cancelled: true };
        if (answer[0] !== true) return { ok: false, message: answer[1], status: answer[2] };
        const chunk = normalizeExportChunk(answer[1]);
        chunks.push(chunk);
        onProgress?.(chunks.length);
        token = chunk.nextToken;
        if (chunks.length >= MAX_EXPORT_CHUNKS && token) {
            return {
                ok: false,
                message: "The export is too large to collect here. Narrow the search.",
            };
        }
    } while (token);
    try {
        return {
            ok: true,
            blob: joinChunks(format, chunks),
            fileName: exportFileName(format),
            chunks: chunks.length,
        };
    } catch {
        return { ok: false, message: "The export could not be read." };
    }
};

/** Starts a browser download of `blob`. */
export const downloadBlob = (blob: Blob, fileName: string): void => {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
};
