/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import {
    fetchPhysnaViewerMetadata,
    PhysnaViewerMetadataResponse,
} from "../../../services/APIService";
import { ViewerPluginProps } from "../../core/types";
import styles from "./PhysnaViewer.module.css";

type ThemeMode = "light" | "dark";

/** Ready state drives the iframe; anything else shows a Cloudscape message. */
type ViewerRuntimeState =
    | { kind: "loading" }
    | {
          kind: "ready";
          tenantId: string;
          physnaAssetId: string;
          viewerToken: string;
          physnaApiBase: string;
      }
    | { kind: "indexing"; message: string }
    | { kind: "failed"; message: string }
    | { kind: "error"; message: string };

const detectTheme = (): ThemeMode =>
    document.body.classList.contains("awsui-dark-mode") ? "dark" : "light";

const ensureLeadingSlash = (value: string): string => {
    if (!value) return "/";
    return value.startsWith("/") ? value : `/${value}`;
};

// How often we re-check the viewer endpoint while Physna is still indexing.
// The backend's lookup + state read is fast, but we don't want to hammer it.
const INDEXING_POLL_INTERVAL_MS = 30_000;

const buildPhysnaViewerUrl = (
    state: Extract<ViewerRuntimeState, { kind: "ready" }>,
    theme: ThemeMode
): string => {
    // Physna's viewer URL shape, per their integration docs:
    //   {base}/tenants/{tenantId}/viewer/asset
    //       ?assetId=<physnaAssetId>
    //       &token=<viewerToken>
    //       &theme=<light|dark>
    //       &parentOrigin=<embedding origin>
    const params = new URLSearchParams({
        assetId: state.physnaAssetId,
        token: state.viewerToken,
        theme,
        parentOrigin: window.location.origin,
    });
    // `physnaApiBase` from the backend is trimmed of any trailing slash, so
    // we can safely concatenate the path segment directly.
    return `${state.physnaApiBase}/tenants/${state.tenantId}/viewer/asset?${params.toString()}`;
};

const PhysnaViewerComponent: React.FC<ViewerPluginProps> = ({ assetId, databaseId, assetKey }) => {
    const [state, setState] = useState<ViewerRuntimeState>({ kind: "loading" });
    const [theme, setTheme] = useState<ThemeMode>(detectTheme());

    // Cancel token for in-flight polls / re-fetches. Incremented on every
    // trigger so stale responses can be discarded when the user navigates
    // between assets or the theme changes mid-fetch.
    const requestIdRef = useRef(0);
    const pollTimeoutRef = useRef<number | null>(null);

    const clearPoll = useCallback(() => {
        if (pollTimeoutRef.current !== null) {
            window.clearTimeout(pollTimeoutRef.current);
            pollTimeoutRef.current = null;
        }
    }, []);

    const fetchMetadata = useCallback(
        async (thisRequestId: number) => {
            if (!assetId || !databaseId || !assetKey) {
                setState({
                    kind: "error",
                    message: "Missing required asset, database, or file information.",
                });
                return;
            }
            setState({ kind: "loading" });
            const [ok, data] = await fetchPhysnaViewerMetadata({
                databaseId,
                assetId,
                relativePath: ensureLeadingSlash(assetKey),
            });
            // A fresher request has been kicked off — drop this result.
            if (thisRequestId !== requestIdRef.current) return;

            if (!ok) {
                setState({
                    kind: "error",
                    message: typeof data === "string" ? data : "Failed to load the Physna viewer.",
                });
                return;
            }
            const meta = data as PhysnaViewerMetadataResponse;
            switch (meta.status) {
                case "ready":
                    if (
                        !meta.tenantId ||
                        !meta.physnaAssetId ||
                        !meta.viewerToken ||
                        !meta.physnaApiBase
                    ) {
                        setState({
                            kind: "error",
                            message: "Physna viewer response missing required fields.",
                        });
                        return;
                    }
                    setState({
                        kind: "ready",
                        tenantId: meta.tenantId,
                        physnaAssetId: meta.physnaAssetId,
                        viewerToken: meta.viewerToken,
                        physnaApiBase: meta.physnaApiBase,
                    });
                    return;
                case "indexing":
                case "not_synced": {
                    // Both states resolve on their own once Physna finishes
                    // ingesting the file — schedule a re-check rather than
                    // forcing the user to reload.
                    const label =
                        meta.status === "indexing"
                            ? "Physna is still indexing this file. Checking again in 30s…"
                            : "This file has not been synced to Physna yet. Checking again in 30s…";
                    setState({ kind: "indexing", message: label });
                    clearPoll();
                    pollTimeoutRef.current = window.setTimeout(() => {
                        requestIdRef.current += 1;
                        fetchMetadata(requestIdRef.current);
                    }, INDEXING_POLL_INTERVAL_MS);
                    return;
                }
                case "failed":
                    setState({
                        kind: "failed",
                        message:
                            meta.message ||
                            `Physna reported a permanent failure: ${meta.physnaState || "unknown"}`,
                    });
                    return;
                case "unsupported":
                case "not_found":
                case "forbidden":
                case "invalid_request":
                case "method_not_allowed":
                case "request_failed":
                case "upstream_unavailable":
                case "internal_error":
                default:
                    setState({
                        kind: "error",
                        message: meta.message || "Unable to load the Physna viewer.",
                    });
                    return;
            }
        },
        [assetId, databaseId, assetKey, clearPoll]
    );

    // Initial load + reload whenever asset identity changes.
    useEffect(() => {
        requestIdRef.current += 1;
        const thisId = requestIdRef.current;
        fetchMetadata(thisId);
        return () => {
            // Invalidate any in-flight poll/request on unmount or asset swap.
            requestIdRef.current += 1;
            clearPoll();
        };
    }, [assetId, databaseId, assetKey, fetchMetadata, clearPoll]);

    // Watch for theme changes on body.classList. Theme is a pure URL param
    // of the Physna viewer, so no refetch is needed — just recompute the src.
    useEffect(() => {
        const observer = new MutationObserver(() => {
            const next = detectTheme();
            setTheme((prev) => (prev === next ? prev : next));
        });
        observer.observe(document.body, {
            attributes: true,
            attributeFilter: ["class"],
        });
        return () => observer.disconnect();
    }, []);

    const iframeSrc = useMemo(() => {
        if (state.kind !== "ready") return null;
        return buildPhysnaViewerUrl(state, theme);
    }, [state, theme]);

    if (state.kind === "loading") {
        return (
            <div className={styles.container}>
                <div className={styles.statusWrap}>
                    <StatusIndicator type="loading">Loading Physna Viewer…</StatusIndicator>
                </div>
            </div>
        );
    }
    if (state.kind === "indexing") {
        return (
            <div className={styles.container}>
                <div className={styles.statusWrap}>
                    <StatusIndicator type="in-progress">{state.message}</StatusIndicator>
                </div>
            </div>
        );
    }
    if (state.kind === "failed") {
        return (
            <div className={styles.container}>
                <div className={styles.statusWrap}>
                    <Alert type="warning" header="Physna cannot render this file">
                        {state.message}
                    </Alert>
                </div>
            </div>
        );
    }
    if (state.kind === "error") {
        return (
            <div className={styles.container}>
                <div className={styles.statusWrap}>
                    <Alert type="error" header="Physna Viewer error">
                        {state.message}
                    </Alert>
                </div>
            </div>
        );
    }

    return (
        <div className={styles.container}>
            {iframeSrc && (
                <iframe
                    src={iframeSrc}
                    title="Physna Viewer"
                    className={styles.frame}
                    // VAMS ships a Cross-Origin Isolation (COI) service worker
                    // that sets `Cross-Origin-Embedder-Policy: require-corp` on
                    // the parent document so WASM-based viewers (Potree,
                    // Needle USD, etc.) can use SharedArrayBuffer. Under
                    // `require-corp`, cross-origin iframes are blocked
                    // unless the embedded document opts in with
                    // `Cross-Origin-Resource-Policy: cross-origin` — which
                    // Physna's `/viewer/asset` response does NOT send
                    // (observed: `cross-origin-resource-policy: not-set`).
                    // Marking the iframe ``credentialless`` opts this
                    // single frame out of COEP's embedding requirement
                    // (strips credentials, loads in a fresh ephemeral
                    // context) without disabling COI for the rest of the
                    // app, so other viewers keep working. Supported in
                    // Chromium 110+; unsupported browsers gracefully
                    // ignore the attribute.
                    // @ts-expect-error — `credentialless` is valid per
                    // HTML spec but not in React 17's IframeHTMLAttributes.
                    // eslint-disable-next-line react/no-unknown-property
                    credentialless="true"
                />
            )}
        </div>
    );
};

export default PhysnaViewerComponent;
