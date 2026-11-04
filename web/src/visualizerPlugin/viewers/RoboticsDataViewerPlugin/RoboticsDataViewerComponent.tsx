/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Robotics Data Viewer: frames the Lichtblick web app that web/customInstalls/robotics/ builds into
 * /viewers/robotics/ and opens the selected robotics logs in it (.mcap and ROS 1 .bag by HTTP Range
 * request, ROS 2 .db3 and PX4 .ulg/.ulog downloaded whole).
 *
 * Each file is presigned under its own database and asset, so a selection may span assets and each
 * file is authorized on its own: a denied, archived or missing file is shown as that file's state
 * while the others open. The presigned URLs reach the framed app only in the URL fragment of the
 * iframe `src` (see roboticsDeepLink.ts), and none is ever logged.
 */

import React, { useEffect, useMemo, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Spinner from "@cloudscape-design/components/spinner";
import { downloadAsset } from "../../../services/APIService";
import { ViewerPluginProps } from "../../core/types";
import {
    buildDeepLink,
    checkSelection,
    fileNameOf,
    maxWholeFileBytes,
    roboticsBasePath,
    wholeFileOverLimitMessage,
} from "./roboticsDeepLink";
import styles from "./RoboticsDataViewer.module.css";

interface SelectedFile {
    key: string;
    assetId: string;
    databaseId: string;
    versionId?: string;
    assetVersionId?: string;
    /** The file's size when the file list carries it (multi-file selections). */
    size?: number;
}

interface FileError {
    name: string;
    message: string;
}

/** One file's download URL, or why it could not be prepared. */
type PreparedFile = { name: string; url: string } | { name: string; error: string };

type Phase = "preparing" | "loading" | "ready" | "failed";

/** What the user is told when a file's download URL could not be prepared. Generic on purpose:
 *  the backend's denial reason is not echoed. */
const describeFailure = (status: unknown, message: unknown): string => {
    if (status === 401 || status === 403) return "You are not authorized to view this file.";
    if (status === 410) return "This file version has been archived and is not available.";
    if (status === 404) return "This file or version could not be found.";
    return typeof message === "string" && message.length > 0
        ? message
        : "The file could not be prepared for viewing.";
};

const RoboticsDataViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    multiFileKeys,
    multiFiles,
    versionId,
    assetVersionId,
    customParameters,
}) => {
    const basePath = roboticsBasePath(customParameters);
    const maxBytes = maxWholeFileBytes(customParameters);

    // The files to open, each under its own asset (falling back to the top-level pair). The asset
    // version applies to the files of the asset it belongs to.
    const files: SelectedFile[] = useMemo(() => {
        const assetVersionFor = (fileAssetId: string, fileDatabaseId: string) =>
            assetVersionId && fileAssetId === assetId && fileDatabaseId === databaseId
                ? assetVersionId
                : undefined;
        if (multiFileKeys && multiFileKeys.length > 0) {
            return multiFileKeys.map((key, i) => {
                const fileAssetId = multiFiles?.[i]?.assetId || assetId;
                const fileDatabaseId = multiFiles?.[i]?.databaseId || databaseId;
                return {
                    key,
                    assetId: fileAssetId,
                    databaseId: fileDatabaseId,
                    versionId: multiFiles?.[i]?.versionId,
                    assetVersionId: assetVersionFor(fileAssetId, fileDatabaseId),
                    size: multiFiles?.[i]?.size,
                };
            });
        }
        if (!assetKey) return [];
        return [
            {
                key: assetKey,
                assetId,
                databaseId,
                versionId,
                assetVersionId: assetVersionFor(assetId, databaseId),
            },
        ];
    }, [assetId, databaseId, assetKey, multiFileKeys, multiFiles, versionId, assetVersionId]);

    // A stable identity for the selection, so a re-render with equal props does not re-presign.
    const selection = JSON.stringify({ files, basePath, maxBytes });

    const [phase, setPhase] = useState<Phase>("preparing");
    const [message, setMessage] = useState<string | null>(null);
    const [fileErrors, setFileErrors] = useState<FileError[]>([]);
    const [iframeSrc, setIframeSrc] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        const { files: selected } = JSON.parse(selection) as { files: SelectedFile[] };
        setIframeSrc(null);
        setFileErrors([]);
        setMessage(null);

        // Refused before any URL is requested: one format per selection, and the formats that
        // open one file at a time.
        const check = checkSelection(selected.map((file) => file.key));
        if (!check.ok) {
            setMessage(check.message);
            setPhase("failed");
            return;
        }
        // A whole-file selection over the limit, when the file list carries every size, is refused
        // here, before any URL is requested. Without the sizes the framed app refuses it by the
        // responses' Content-Length, and reports that in its Alerts tab.
        if (check.source === "vams-download") {
            const overLimit = wholeFileOverLimitMessage(
                selected.map((file) => ({ name: fileNameOf(file.key), size: file.size })),
                maxBytes
            );
            if (overLimit) {
                setMessage(overLimit);
                setPhase("failed");
                return;
            }
        }
        setPhase("preparing");

        const prepare = async () => {
            const results = await Promise.all(
                selected.map(async (file): Promise<PreparedFile> => {
                    const name = fileNameOf(file.key);
                    try {
                        const params: Record<string, string> = {
                            databaseId: file.databaseId,
                            assetId: file.assetId,
                            key: file.key,
                            downloadType: "assetFile",
                        };
                        if (file.versionId) params.versionId = file.versionId;
                        if (file.assetVersionId) params.assetVersionId = file.assetVersionId;
                        const response: any = await downloadAsset(params);
                        if (
                            Array.isArray(response) &&
                            response[0] === true &&
                            typeof response[1] === "string"
                        ) {
                            return { name, url: response[1] as string };
                        }
                        const [, failureMessage, status] = Array.isArray(response) ? response : [];
                        return { name, error: describeFailure(status, failureMessage) };
                    } catch (err: any) {
                        return { name, error: describeFailure(err?.status, undefined) };
                    }
                })
            );
            if (cancelled) return;

            const urls = results.flatMap((result) => ("url" in result ? [result.url] : []));
            const errors: FileError[] = results.flatMap((result) =>
                "error" in result ? [{ name: result.name, message: result.error }] : []
            );
            setFileErrors(errors);
            if (urls.length === 0) {
                setMessage(
                    selected.length === 1
                        ? errors[0]?.message ?? "The file could not be prepared for viewing."
                        : "None of the selected files could be prepared for viewing."
                );
                setPhase("failed");
                return;
            }
            setIframeSrc(buildDeepLink(basePath, check.source, urls, maxBytes));
            setPhase("loading");
        };

        prepare().catch(() => {
            if (cancelled) return;
            setMessage("The files could not be prepared for viewing.");
            setPhase("failed");
        });

        return () => {
            cancelled = true;
        };
    }, [selection, basePath, maxBytes]);

    const showErrorsBanner = iframeSrc !== null && fileErrors.length > 0;

    return (
        <div className={styles.container}>
            {showErrorsBanner && (
                <div className={styles.banner} data-testid="robotics-viewer-file-errors">
                    <Alert type="warning" header="Some files could not be opened">
                        <ul className={styles.fileErrors}>
                            {fileErrors.map((fileError) => (
                                <li key={fileError.name}>
                                    <strong>{fileError.name}</strong>: {fileError.message}
                                </li>
                            ))}
                        </ul>
                    </Alert>
                </div>
            )}
            <div className={styles.frameArea}>
                {iframeSrc && (
                    <iframe
                        // A new document per selection: changing only the fragment of a framed
                        // page's URL would not reload it.
                        key={iframeSrc}
                        title="Robotics Data Viewer (Lichtblick)"
                        src={iframeSrc}
                        className={styles.iframe}
                        allow="fullscreen"
                        data-testid="robotics-viewer-iframe"
                        onLoad={() => setPhase("ready")}
                    />
                )}
                {phase !== "ready" && (
                    <div className={styles.overlay}>
                        {phase === "failed" ? (
                            <div role="alert" data-testid="robotics-viewer-error">
                                <div>{message}</div>
                                {fileErrors.length > 1 && (
                                    <ul className={styles.fileErrors}>
                                        {fileErrors.map((fileError) => (
                                            <li key={fileError.name}>
                                                <strong>{fileError.name}</strong>:{" "}
                                                {fileError.message}
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </div>
                        ) : (
                            <>
                                <Spinner size="large" />
                                <div>
                                    {phase === "preparing"
                                        ? "Preparing files..."
                                        : "Loading the Robotics Data Viewer..."}
                                </div>
                            </>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};

export default RoboticsDataViewerComponent;
