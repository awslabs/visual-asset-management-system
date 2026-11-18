/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * TIFF/TGA viewer. Browsers draw neither format, so the file is downloaded, decoded in the page
 * with pure-JavaScript decoders (no WebAssembly, no `eval`, no CSP change) and drawn to a canvas.
 *
 * Above the byte or pixel cap, or when the file cannot be decoded, the file's generated preview
 * (`fileInfo.previewFile`, written by the media preview pipeline) is shown with a notice; with no
 * preview the viewer says why in plain words. A refused download (403, 410, 404, 401) shows the
 * shared message and never falls back: the API decided, and the viewer does not look for another
 * route to the content.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Box, Select, SpaceBetween, Spinner } from "@cloudscape-design/components";
import { downloadAsset } from "../../../services/APIService";
import { downloadFailureMessage } from "../../core/streamFailure";
import { ViewerPluginProps } from "../../core/types";
import {
    DecodedRaster,
    RasterDecodeError,
    RasterKind,
    formatBytes,
    limitsFromParameters,
    rasterKindOf,
} from "./rasterDecode";
import { RasterSession, openRasterSession } from "./rasterSession";

class DownloadRefused extends Error {}

/** Reads a response body, stopping with a too-large error once it passes `maxBytes`. */
async function readBounded(response: Response, maxBytes: number): Promise<ArrayBuffer> {
    const tooLarge = () =>
        new RasterDecodeError(
            "too-large",
            `This file is larger than the ${formatBytes(maxBytes)} the viewer opens.`
        );
    const declared = Number(response.headers?.get?.("Content-Length"));
    if (Number.isFinite(declared) && declared > maxBytes) throw tooLarge();
    const reader = response.body?.getReader?.();
    if (!reader) {
        const whole = await response.arrayBuffer();
        if (whole.byteLength > maxBytes) throw tooLarge();
        return whole;
    }
    const parts: Uint8Array[] = [];
    let total = 0;
    for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (total > maxBytes) {
            await reader.cancel();
            throw tooLarge();
        }
        parts.push(value);
    }
    const out = new Uint8Array(total);
    let at = 0;
    for (const part of parts) {
        out.set(part, at);
        at += part.byteLength;
    }
    return out.buffer;
}

/** Lets the browser paint the "Decoding" state before a long synchronous decode starts. */
const nextFrame = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

const CHECKERBOARD: React.CSSProperties = {
    backgroundColor: "#d0d0d0",
    backgroundImage:
        "conic-gradient(#a8a8a8 25%, transparent 25% 50%, #a8a8a8 50% 75%, transparent 75%)",
    backgroundSize: "16px 16px",
};

type Phase =
    | { kind: "loading"; text: string }
    | { kind: "error"; message: string }
    | { kind: "fallback"; notice: string; previewUrl?: string }
    | { kind: "ready" };

const RasterImageViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
    assetVersionId,
    fileInfo,
    customParameters,
}) => {
    const limits = useMemo(() => limitsFromParameters(customParameters), [customParameters]);
    const kind: RasterKind | null = rasterKindOf(assetKey);
    const previewKey = fileInfo?.previewFile;
    const fileSize = fileInfo?.size;
    const fileName = (assetKey ?? "").split("/").pop() ?? "";

    const canvasRef = useRef<HTMLCanvasElement>(null);
    const sessionRef = useRef<RasterSession | null>(null);
    const [phase, setPhase] = useState<Phase>({ kind: "loading", text: "Loading image..." });
    const [decoded, setDecoded] = useState<DecodedRaster | null>(null);
    const [page, setPage] = useState(0);
    const [pageError, setPageError] = useState<string | null>(null);
    const [pages, setPages] = useState<{ index: number; width: number; height: number }[]>([]);

    const signedUrl = useCallback(
        async (key: string, withVersion: boolean) => {
            const response: any = await downloadAsset({
                assetId,
                databaseId,
                key,
                versionId: withVersion ? versionId : undefined,
                assetVersionId: assetVersionId as any,
                downloadType: "assetFile",
            });
            if (!Array.isArray(response) || response[0] === false) {
                const message = downloadFailureMessage(response, "Failed to download file");
                throw message === "Failed to download file"
                    ? new Error(message)
                    : new DownloadRefused(message);
            }
            return response[1] as string;
        },
        [assetId, databaseId, versionId, assetVersionId]
    );

    /** The generated preview with a notice, or the notice alone when there is none to show. */
    const showFallback = useCallback(
        async (reason: string, isCancelled: () => boolean) => {
            let previewUrl: string | undefined;
            if (previewKey) {
                try {
                    previewUrl = await signedUrl(previewKey, false);
                } catch {
                    previewUrl = undefined;
                }
            }
            if (isCancelled()) return;
            setPhase({
                kind: "fallback",
                previewUrl,
                notice: previewUrl
                    ? `${reason} Showing its generated preview instead.`
                    : `${reason} No preview has been generated for it. Download the file to open it in another application.`,
            });
        },
        [previewKey, signedUrl]
    );

    // Download, inspect and decode the first image or page.
    useEffect(() => {
        let cancelled = false;
        const abort = new AbortController();
        const isCancelled = () => cancelled;
        sessionRef.current?.dispose();
        sessionRef.current = null;
        setDecoded(null);
        setPage(0);
        setPages([]);
        setPageError(null);

        const run = async () => {
            if (!assetKey || !kind) {
                setPhase({ kind: "error", message: "This file is not a TIFF or TGA image." });
                return;
            }
            setPhase({ kind: "loading", text: "Loading image..." });
            try {
                if (typeof fileSize === "number" && fileSize > limits.maxBytes) {
                    throw new RasterDecodeError(
                        "too-large",
                        `This file is larger than the ${formatBytes(
                            limits.maxBytes
                        )} the viewer opens.`
                    );
                }
                const url = await signedUrl(assetKey, true);
                if (cancelled) return;
                let response: Response;
                try {
                    response = await fetch(url, { signal: abort.signal });
                } catch (networkError) {
                    if (abort.signal.aborted) return;
                    throw new DownloadRefused(
                        "The file could not be downloaded. Check your connection and open it again."
                    );
                }
                if (!response.ok) {
                    throw new DownloadRefused(
                        response.status === 403 || response.status === 404
                            ? "The file could not be read. Open it again to refresh the link."
                            : "Failed to download file"
                    );
                }
                const buffer = await readBounded(response, limits.maxBytes);
                if (cancelled) return;
                setPhase({ kind: "loading", text: "Decoding image..." });
                await nextFrame();
                if (cancelled) return;

                const session = await openRasterSession(buffer, kind, limits);
                if (cancelled) {
                    session.dispose();
                    return;
                }
                sessionRef.current = session;
                setPages(session.pages);
                const result = await session.decode(0);
                if (cancelled) return;
                setDecoded(result);
                setPhase({ kind: "ready" });
            } catch (error) {
                if (cancelled) return;
                if (error instanceof DownloadRefused) {
                    setPhase({ kind: "error", message: error.message });
                } else if (error instanceof RasterDecodeError) {
                    console.warn("TIFF/TGA viewer fell back:", error.reason, error.message);
                    await showFallback(error.message, isCancelled);
                } else {
                    console.warn("TIFF/TGA viewer fell back:", error);
                    await showFallback(
                        "This file could not be opened in the browser.",
                        isCancelled
                    );
                }
            }
        };
        run();
        return () => {
            cancelled = true;
            abort.abort();
            sessionRef.current?.dispose();
            sessionRef.current = null;
        };
    }, [assetKey, kind, limits, fileSize, signedUrl, showFallback]);

    // Draw the decoded pixels.
    useEffect(() => {
        const canvas = canvasRef.current;
        if (!decoded || !canvas) return;
        canvas.width = decoded.width;
        canvas.height = decoded.height;
        const context = canvas.getContext("2d");
        if (!context) {
            setPhase({
                kind: "error",
                message: "This browser could not create the drawing surface.",
            });
            return;
        }
        context.putImageData(
            new ImageData(decoded.data as any, decoded.width, decoded.height),
            0,
            0
        );
    }, [decoded, phase.kind]);

    const selectPage = async (index: number) => {
        const session = sessionRef.current;
        if (!session || index === page) return;
        setPage(index);
        setPageError(null);
        try {
            const result = await session.decode(index);
            if (sessionRef.current === session) setDecoded(result);
        } catch (error) {
            if (sessionRef.current !== session) return;
            setPageError(
                error instanceof RasterDecodeError
                    ? error.message
                    : "This page could not be decoded."
            );
        }
    };

    const centered: React.CSSProperties = {
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        height: "100%",
        fontSize: "16px",
    };
    // In the file viewer modal the viewer sits on the striped container background, so its own text
    // is drawn on a theme panel to stay readable in both themes.
    const panel: React.CSSProperties = {
        background: "var(--vams-bg-primary)",
        padding: "12px 16px",
        borderRadius: "8px",
    };

    if (phase.kind === "loading") {
        return (
            <div style={centered} data-testid="raster-viewer-loading">
                <div
                    style={{ ...panel, color: "var(--vams-text-secondary)" }}
                    data-testid="raster-viewer-loading-panel"
                >
                    <SpaceBetween direction="horizontal" size="xs">
                        <Spinner />
                        <span>{phase.text}</span>
                    </SpaceBetween>
                </div>
            </div>
        );
    }
    if (phase.kind === "error") {
        return (
            <div style={centered}>
                <div style={{ ...panel, color: "var(--vams-color-error)" }} role="alert">
                    {phase.message}
                </div>
            </div>
        );
    }
    if (phase.kind === "fallback") {
        return (
            <SpaceBetween size="s">
                <Alert type="info" data-testid="raster-viewer-fallback-notice">
                    {phase.notice}
                </Alert>
                {phase.previewUrl && (
                    <div style={{ ...centered, height: "auto" }}>
                        <img
                            src={phase.previewUrl}
                            alt="Generated preview"
                            data-testid="raster-viewer-fallback-preview"
                            style={{ maxWidth: "100%", maxHeight: "70vh" }}
                        />
                    </div>
                )}
            </SpaceBetween>
        );
    }

    const pageOptions = pages.map((p) => ({
        value: String(p.index),
        label: `Page ${p.index + 1} of ${pages.length}`,
        description: `${p.width} × ${p.height} px`,
    }));

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "8px" }}>
            {pageOptions.length > 1 && (
                <div data-testid="raster-viewer-page-picker" style={{ maxWidth: "320px" }}>
                    <Select
                        ariaLabel="TIFF page"
                        selectedOption={pageOptions[page] ?? null}
                        options={pageOptions}
                        onChange={({ detail }) => selectPage(Number(detail.selectedOption.value))}
                    />
                </div>
            )}
            {pageError && (
                <Alert type="warning" data-testid="raster-viewer-page-error">
                    {pageError}
                </Alert>
            )}
            <div
                style={{
                    flex: 1,
                    minHeight: 0,
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    overflow: "auto",
                    ...CHECKERBOARD,
                }}
            >
                <canvas
                    ref={canvasRef}
                    role="img"
                    aria-label={`${kind === "tga" ? "TGA" : "TIFF"} image ${fileName}`}
                    data-testid="raster-viewer-canvas"
                    data-width={decoded?.width}
                    data-height={decoded?.height}
                    data-format={kind ?? ""}
                    style={{ maxWidth: "100%", maxHeight: "100%" }}
                />
            </div>
            {decoded && (
                <div style={{ ...panel, padding: "4px 8px", alignSelf: "flex-start" }}>
                    <Box
                        variant="small"
                        color="text-body-secondary"
                        data-testid="raster-viewer-info"
                    >
                        {kind === "tga" ? "TGA" : "TIFF"} · {decoded.width} × {decoded.height} px
                        {typeof fileSize === "number" ? ` · ${formatBytes(fileSize)}` : ""}
                        {decoded.conversion ? ` · ${decoded.conversion}` : ""}
                    </Box>
                </div>
            )}
        </div>
    );
};

export default RasterImageViewerComponent;
