/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The outcome of the files an upload processes asynchronously (files over 1 GiB). After a completion
 * that reported largeFileAsynchronousHandling, GET /uploads/{uploadId} is polled until every such file
 * has landed or failed, the wait limit passes, or the panel unmounts. Display only: it shows what the
 * API returned and decides nothing; a 400, 401, 403 or 404 stops the polling with a neutral note,
 * and any other error is retried on the next poll until the wait limit.
 */

import React, { useEffect, useRef, useState } from "react";
import { Alert, Box, SpaceBetween, StatusIndicator } from "@cloudscape-design/components";
import AssetUploadService, { UploadStatusResponse } from "../../services/AssetUploadService";
import { ApiError } from "../../services/apiClient";

export const ASYNC_POLL_INTERVAL_MS = 15_000;
export const ASYNC_POLL_LIMIT_MS = 60 * 60 * 1000;

type Fetcher = (
    uploadId: string,
    assetId: string,
    databaseId: string
) => Promise<UploadStatusResponse>;

interface Props {
    uploadIds: string[];
    assetId: string;
    databaseId: string;
    pollIntervalMs?: number;
    pollLimitMs?: number;
    fetchStatus?: Fetcher;
}

interface UploadView {
    status?: UploadStatusResponse;
    /** Polling stopped without an outcome: the upload keeps no status, or it could not be read. */
    unavailable?: boolean;
}

/** HTTP statuses that will not change on a later poll (no such upload, not readable, bad request). */
const FINAL_STATUS_CODES = [400, 401, 403, 404];

export const isFinalStatusError = (error: unknown) =>
    error instanceof ApiError && FINAL_STATUS_CODES.includes(error.status);

const isPending = (view: UploadView) =>
    !view.unavailable &&
    (!view.status ||
        view.status.status === "processing_async" ||
        (view.status.asyncFiles || []).some((f) => f.status === "PENDING"));

const FILE_INDICATOR = {
    PENDING: { type: "in-progress", text: "Processing" },
    SUCCEEDED: { type: "success", text: "In the asset" },
    FAILED: { type: "error", text: "Failed" },
} as const;

export default function AsyncUploadOutcome({
    uploadIds,
    assetId,
    databaseId,
    pollIntervalMs = ASYNC_POLL_INTERVAL_MS,
    pollLimitMs = ASYNC_POLL_LIMIT_MS,
    fetchStatus = (u, a, d) => AssetUploadService.getUploadStatus(u, a, d),
}: Props) {
    const [views, setViews] = useState<Record<string, UploadView>>({});
    const [timedOut, setTimedOut] = useState(false);
    const viewsRef = useRef(views);
    viewsRef.current = views;
    const idsKey = uploadIds.join(",");

    useEffect(() => {
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const startedAt = Date.now();

        const poll = async () => {
            const pending = uploadIds.filter((id) => isPending(viewsRef.current[id] || {}));
            if (pending.length === 0) return;
            const updates: Record<string, UploadView> = {};
            await Promise.all(
                pending.map(async (id) => {
                    try {
                        updates[id] = { status: await fetchStatus(id, assetId, databaseId) };
                    } catch (error) {
                        // A final answer stops polling; a transient error (a 5xx, a network error)
                        // keeps the last status read and is retried on the next poll.
                        if (isFinalStatusError(error)) {
                            updates[id] = { unavailable: true };
                        }
                    }
                })
            );
            if (cancelled) return;
            const next = { ...viewsRef.current, ...updates };
            viewsRef.current = next;
            setViews(next);
            if (!uploadIds.some((id) => isPending(next[id] || {}))) return;
            if (Date.now() - startedAt >= pollLimitMs) {
                setTimedOut(true);
                return;
            }
            timer = setTimeout(poll, pollIntervalMs);
        };
        void poll();
        return () => {
            cancelled = true;
            if (timer) clearTimeout(timer);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [idsKey, assetId, databaseId, pollIntervalMs, pollLimitMs]);

    if (uploadIds.length === 0) return null;

    const files = uploadIds.flatMap((id) =>
        (views[id]?.status?.asyncFiles || []).map((f) => ({ ...f, uploadId: id }))
    );
    const failed = files.filter((f) => f.status === "FAILED");
    const stillPending = uploadIds.some((id) => isPending(views[id] || {}));
    const unavailable = uploadIds.some((id) => views[id]?.unavailable);

    return (
        <div data-testid="async-upload-outcome">
            <SpaceBetween direction="vertical" size="s">
                {failed.length > 0 && (
                    <Alert type="error" header="Large files that did not reach the asset">
                        <ul>
                            {failed.map((f) => (
                                <li key={`${f.uploadId}:${f.relativeKey}`}>
                                    {f.relativeKey}
                                    {f.error ? `: ${f.error}` : ""}
                                </li>
                            ))}
                        </ul>
                        Upload these files again.
                    </Alert>
                )}
                {stillPending && !timedOut && (
                    <Alert type="info" header="Large files still processing">
                        Files over 1 GiB are copied into the asset after the upload. This panel
                        updates until each one has landed or failed.
                    </Alert>
                )}
                {timedOut && (
                    <Alert type="info" header="Large files still processing">
                        This page stopped checking after an hour. Check the asset's files later.
                    </Alert>
                )}
                {unavailable && (
                    <Box variant="small" color="text-body-secondary">
                        The status of some large files is not available here. Check the asset's
                        files to confirm they landed.
                    </Box>
                )}
                {files.length > 0 && (
                    <SpaceBetween direction="vertical" size="xxs">
                        <Box variant="awsui-key-label">Large files</Box>
                        {files.map((f) => {
                            const indicator = FILE_INDICATOR[f.status] || FILE_INDICATOR.PENDING;
                            return (
                                <SpaceBetween
                                    key={`${f.uploadId}:${f.relativeKey}`}
                                    direction="horizontal"
                                    size="xs"
                                >
                                    <StatusIndicator type={indicator.type}>
                                        {indicator.text}
                                    </StatusIndicator>
                                    <span>{f.relativeKey}</span>
                                </SpaceBetween>
                            );
                        })}
                    </SpaceBetween>
                )}
            </SpaceBetween>
        </div>
    );
}
