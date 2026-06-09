/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import Spinner from "@cloudscape-design/components/spinner";
import { downloadAsset } from "../../../services/APIService";
import { ViewerPluginProps } from "../../core/types";
import styles from "./SuperSplatViewer.module.css";

const DEFAULT_BASE_PATH = "/viewers/supersplat/index.html";

const SuperSplatViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
    assetVersionId,
    customParameters,
}) => {
    const initializationRef = useRef(false);
    const [iframeSrc, setIframeSrc] = useState<string | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [loadingMessage, setLoadingMessage] = useState("Preparing file...");
    const [error, setError] = useState<string | null>(null);

    const basePath = (customParameters && customParameters.basePath) || DEFAULT_BASE_PATH;

    useEffect(() => {
        // Reset guard when the target file/version changes so re-selection reloads.
        initializationRef.current = false;
    }, [assetKey, versionId, assetVersionId]);

    useEffect(() => {
        if (!assetKey || initializationRef.current) {
            return;
        }
        initializationRef.current = true;

        const loadFile = async () => {
            try {
                setIsLoading(true);
                setError(null);
                setLoadingMessage("Preparing file...");

                const downloadParams: any = {
                    databaseId,
                    assetId,
                    key: assetKey,
                    downloadType: "assetFile",
                };
                if (versionId) downloadParams.versionId = versionId;
                if (assetVersionId) downloadParams.assetVersionId = assetVersionId;

                const response = await downloadAsset(downloadParams);

                if (!response || !Array.isArray(response) || response[0] === false) {
                    const message =
                        response && Array.isArray(response) && response[1]
                            ? response[1]
                            : "Failed to prepare the file for viewing.";
                    console.log("SuperSplat: download failed -", message);
                    setError(message);
                    setIsLoading(false);
                    return;
                }

                const presignedUrl: string = response[1];
                const filename = assetKey.split("/").pop() || "model";

                // SuperSplat detects the splat format from the load URL's extension.
                // For a presigned S3 URL the real ".sog"/".ply"/etc. extension is buried
                // before the query string, and the SigV4 query contains literal "/" chars
                // (e.g. X-Amz-Credential=.../s3/aws4_request) that defeat SuperSplat's
                // basename parser -> "Unsupported input file type". Appending a
                // "#/<filename>" fragment puts the real extension as the final path
                // segment so format detection succeeds. The fragment is never sent to S3
                // (browsers strip it from network requests), so the presigned signature
                // and range/streaming behavior are unaffected.
                const loadUrl = `${presignedUrl}#/${filename}`;

                // SuperSplat decodes the ?load= and ?filename= params TWICE on startup:
                // once implicitly via URLSearchParams.getAll() and again via an explicit
                // decodeURIComponent() (see SuperSplat main.ts). A single encode is undone
                // by the first decode, then the second decode corrupts the presigned URL's
                // own percent-encoding (e.g. %2F/%2B/%3D in X-Amz-Credential and
                // X-Amz-Security-Token become raw "/"/"+"/"="), which breaks the SigV4
                // signature and yields an S3 400. Encode TWICE so the values survive both
                // of SuperSplat's decodes and reach S3 byte-for-byte as signed.
                const src =
                    `${basePath}?load=${encodeURIComponent(encodeURIComponent(loadUrl))}` +
                    `&filename=${encodeURIComponent(encodeURIComponent(filename))}`;

                setLoadingMessage("Loading SuperSplat editor...");
                setIframeSrc(src);
            } catch (err: any) {
                console.log("SuperSplat: initialization error -", err?.message || err);
                setError(err?.message || "An error occurred while loading the file.");
                setIsLoading(false);
            }
        };

        loadFile();
    }, [assetKey, assetId, databaseId, versionId, assetVersionId, basePath]);

    return (
        <div className={styles.container}>
            {iframeSrc && (
                <iframe
                    title="SuperSplat Editor"
                    src={iframeSrc}
                    className={styles.iframe}
                    allow="fullscreen; xr-spatial-tracking"
                    onLoad={() => setIsLoading(false)}
                />
            )}
            {(isLoading || error) && (
                <div className={styles.overlay}>
                    {error ? (
                        <div>{error}</div>
                    ) : (
                        <>
                            <Spinner size="large" />
                            <div>{loadingMessage}</div>
                        </>
                    )}
                </div>
            )}
        </div>
    );
};

export default SuperSplatViewerComponent;
