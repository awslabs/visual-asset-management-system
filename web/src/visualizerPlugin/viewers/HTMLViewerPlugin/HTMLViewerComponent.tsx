/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { downloadAsset } from "../../../services/APIService";
import { ViewerPluginProps } from "../../core/types";

const HTMLViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
    assetVersionId,
}) => {
    const [htmlUrl, setHtmlUrl] = useState<string>("");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // A frame refused by Content-Security-Policy fires no error on the element,
    // so without this the panel is simply blank. frame-src has to allow the
    // origin the document is served from.
    useEffect(() => {
        const originOf = (url: string): string | null => {
            try {
                return new URL(url, window.location.href).origin;
            } catch {
                return null;
            }
        };
        const onViolation = (event: SecurityPolicyViolationEvent) => {
            if (!event.violatedDirective?.startsWith("frame-src")) return;
            if (!htmlUrl || !event.blockedURI) return;
            // A blocked blob: URL is reported as the bare scheme, with no origin to compare, and the
            // document is framed from a blob URL. Match on the scheme in that case; comparing origins
            // would silently drop the violation and leave the panel blank with no explanation, which
            // is the very thing this handler exists to prevent.
            const isBlob = event.blockedURI.startsWith("blob") && htmlUrl.startsWith("blob:");
            const blocked = originOf(event.blockedURI);
            if (!isBlob && (!blocked || blocked !== originOf(htmlUrl))) return;
            setError(
                "This document was blocked by the site's content security policy " +
                    "(frame-src). Ask an administrator to allow the storage endpoint."
            );
        };
        document.addEventListener("securitypolicyviolation", onViolation);
        return () => document.removeEventListener("securitypolicyviolation", onViolation);
    }, [htmlUrl]);

    useEffect(() => {
        // The blob URL is revoked when this effect is torn down, so switching files or closing the
        // viewer does not leak one per document. `cancelled` stops a slow fetch from writing state
        // after that teardown, which would otherwise install a URL nothing will ever revoke.
        let cancelled = false;
        let objectUrl: string | null = null;

        const loadHTML = async () => {
            if (!assetKey) return;

            try {
                setLoading(true);
                setError(null);

                console.log("HTMLViewerComponent loading file:", {
                    assetId,
                    databaseId,
                    key: assetKey,
                    assetVersionId: assetVersionId,
                    downloadType: "assetFile",
                });

                const response = await downloadAsset({
                    assetId: assetId,
                    databaseId: databaseId,
                    key: assetKey,
                    versionId: versionId,
                    assetVersionId: assetVersionId as any,
                    downloadType: "assetFile",
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        console.error("Error downloading HTML file:", response);
                        throw new Error("Failed to download HTML file");
                    } else {
                        // Fetch the bytes and frame them as a typed Blob rather than pointing the
                        // iframe at the presigned URL.
                        //
                        // VAMS stores asset files with a generic content type — an uploaded .html
                        // arrives as `binary/octet-stream` — and neither the object nor the presigned
                        // URL declares otherwise. A frame served that type renders nothing at all: no
                        // error event, no CSP violation, no console output, just an empty panel. The
                        // bytes are correct and the request succeeds, which is why this reads as the
                        // viewer being broken rather than as a metadata problem.
                        //
                        // Declaring the type here fixes documents already stored, needs no migration,
                        // and keeps the signed URL out of the DOM. blob: is already allowed by
                        // frame-src, and the sandbox below still withholds allow-same-origin, so the
                        // framed document keeps its opaque origin.
                        const fetched = await fetch(response[1]);
                        if (!fetched.ok) {
                            throw new Error(`Storage returned ${fetched.status} for this document`);
                        }
                        const markup = await fetched.text();
                        if (cancelled) return;
                        // The URL is a presigned S3 GET: signature and session
                        // token included. Log the key, never the URL.
                        console.log("Successfully loaded HTML file:", assetKey);
                        objectUrl = URL.createObjectURL(new Blob([markup], { type: "text/html" }));
                        setHtmlUrl(objectUrl);
                    }
                } else {
                    throw new Error("Invalid response format");
                }
            } catch (error) {
                console.error("Error in HTML download:", error);
                if (!cancelled) {
                    setError(error instanceof Error ? error.message : "Failed to load HTML");
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        };

        loadHTML();

        return () => {
            cancelled = true;
            if (objectUrl) URL.revokeObjectURL(objectUrl);
        };
    }, [assetId, assetKey, databaseId, versionId, assetVersionId]);

    if (loading) {
        return (
            <div
                style={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    height: "100%",
                    fontSize: "16px",
                    color: "#666",
                }}
            >
                Loading HTML document...
            </div>
        );
    }

    if (error) {
        return (
            <div
                style={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    height: "100%",
                    fontSize: "16px",
                    color: "#d13212",
                }}
            >
                Error: {error}
            </div>
        );
    }

    return (
        <div
            style={{
                width: "100%",
                height: "100%",
                border: "none",
            }}
        >
            <iframe
                src={htmlUrl}
                style={{
                    width: "100%",
                    height: "100%",
                    border: "none",
                }}
                title="HTML Document Viewer"
                // allow-same-origin together with allow-scripts hands the framed
                // document its real origin instead of an opaque one, which is what
                // makes uploaded markup able to reach that origin's storage — and
                // would make it able to reach the app's own storage on any
                // deployment that serves asset content same-origin. An uploaded
                // document also has no reason to open windows.
                sandbox="allow-scripts allow-forms"
                referrerPolicy="no-referrer"
                onError={() => setError("Failed to load HTML document")}
            />
        </div>
    );
};

export default HTMLViewerComponent;
