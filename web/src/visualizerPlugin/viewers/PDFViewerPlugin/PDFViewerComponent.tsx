/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useMemo } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { Button, SpaceBetween, Box } from "@cloudscape-design/components";
import { downloadAsset } from "../../../services/APIService";
import { ViewerPluginProps } from "../../core/types";

// Configure PDF.js worker
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
    "pdfjs-dist/build/pdf.worker.min.mjs",
    import.meta.url
).toString();

const PDFViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
    onDeletePreview,
    isPreviewFile = false,
}) => {
    const [fileUrl, setFileUrl] = useState<string>("");
    const [numPages, setNumPages] = useState<number>(0);
    const [pageNumber, setPageNumber] = useState<number>(1);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<string | null>(null);
    const [scale, setScale] = useState<number>(1.0);
    const [pageWidth, setPageWidth] = useState<number | undefined>(undefined);

    // Memoize the file object to prevent unnecessary re-renders
    const file = useMemo(() => {
        if (!fileUrl) return null;
        return { url: fileUrl };
    }, [fileUrl]);

    useEffect(() => {
        const loadPDF = async () => {
            if (!assetKey) return;

            console.log("PDFViewerComponent loading file:", {
                assetId,
                databaseId,
                key: assetKey,
                versionId: isPreviewFile ? "" : versionId || "",
                downloadType: "assetFile",
                isPreviewFile,
            });

            try {
                setLoading(true);
                setError(null);

                const response = await downloadAsset({
                    assetId: assetId,
                    databaseId: databaseId,
                    key: assetKey || "",
                    versionId: isPreviewFile ? "" : versionId || "",
                    downloadType: "assetFile",
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        console.error("Error downloading PDF file:", response);
                        throw new Error("Failed to download PDF file");
                    } else {
                        console.log("Successfully loaded PDF URL:", response[1]);
                        setFileUrl(response[1]);
                    }
                } else {
                    throw new Error("Invalid response format");
                }
            } catch (error) {
                console.error("Error in PDF download:", error);
                setError(error instanceof Error ? error.message : "Failed to load PDF file");
            } finally {
                setLoading(false);
            }
        };

        loadPDF();
    }, [assetId, assetKey, databaseId, versionId, isPreviewFile]);

    const onDocumentLoadSuccess = ({ numPages }: { numPages: number }) => {
        setNumPages(numPages);
        setPageNumber(1);
        console.log(`PDF loaded successfully with ${numPages} pages`);
    };

    const onDocumentLoadError = (error: Error) => {
        console.error("PDF load error:", error);
        setError("Failed to load PDF document. The file may be corrupted or invalid.");
    };

    const changePage = (offset: number) => {
        setPageNumber((prevPageNumber) => {
            const newPageNumber = prevPageNumber + offset;
            return Math.max(1, Math.min(newPageNumber, numPages));
        });
    };

    const previousPage = () => changePage(-1);
    const nextPage = () => changePage(1);

    const zoomIn = () => setScale((prevScale) => Math.min(prevScale * 1.2, 3.0));
    const zoomOut = () => setScale((prevScale) => Math.max(prevScale / 1.2, 0.5));
    const resetZoom = () => setScale(1.0);
    const fitToWidth = () => {
        setScale(1.0);
        setPageWidth(undefined);
    };

    const goToPage = (page: number) => {
        if (page >= 1 && page <= numPages) {
            setPageNumber(page);
        }
    };

    if (loading) {
        return (
            <Box textAlign="center" padding="xl">
                <div>Loading PDF...</div>
            </Box>
        );
    }

    if (error) {
        return (
            <Box textAlign="center" padding="xl">
                <div style={{ color: "red" }}>Error: {error}</div>
            </Box>
        );
    }

    if (!file) {
        return (
            <Box textAlign="center" padding="xl">
                <div>No PDF file to display</div>
            </Box>
        );
    }

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", width: "100%" }}>
            {/* Controls */}
            <div
                style={{
                    padding: "10px",
                    borderBottom: "1px solid #e0e0e0",
                    backgroundColor: "#f5f5f5",
                    flexShrink: 0,
                }}
            >
                <SpaceBetween direction="horizontal" size="s">
                    {/* Page Navigation */}
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            iconName="angle-left"
                            variant="icon"
                            disabled={pageNumber <= 1}
                            onClick={previousPage}
                            ariaLabel="Previous page"
                        />
                        <div
                            style={{
                                display: "flex",
                                alignItems: "center",
                                minWidth: "120px",
                                justifyContent: "center",
                            }}
                        >
                            <span>
                                Page {pageNumber} of {numPages}
                            </span>
                        </div>
                        <Button
                            iconName="angle-right"
                            variant="icon"
                            disabled={pageNumber >= numPages}
                            onClick={nextPage}
                            ariaLabel="Next page"
                        />
                    </SpaceBetween>

                    {/* Zoom Controls */}
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            iconName="remove"
                            variant="icon"
                            disabled={scale <= 0.5}
                            onClick={zoomOut}
                            ariaLabel="Zoom out"
                        />
                        <div
                            style={{
                                display: "flex",
                                alignItems: "center",
                                minWidth: "60px",
                                justifyContent: "center",
                            }}
                        >
                            <span>{Math.round(scale * 100)}%</span>
                        </div>
                        <Button
                            iconName="add-plus"
                            variant="icon"
                            disabled={scale >= 3.0}
                            onClick={zoomIn}
                            ariaLabel="Zoom in"
                        />
                        <Button variant="normal" onClick={resetZoom}>
                            Reset
                        </Button>
                        <Button variant="normal" onClick={fitToWidth}>
                            Fit Width
                        </Button>
                    </SpaceBetween>

                    {/* Page Jump */}
                    <div style={{ display: "flex", alignItems: "center", gap: "5px" }}>
                        <span>Go to:</span>
                        <input
                            type="number"
                            min={1}
                            max={numPages}
                            value={pageNumber}
                            onChange={(e) => {
                                const page = parseInt(e.target.value);
                                if (!isNaN(page)) {
                                    goToPage(page);
                                }
                            }}
                            style={{
                                width: "60px",
                                padding: "4px",
                                border: "1px solid #ccc",
                                borderRadius: "4px",
                            }}
                        />
                    </div>
                </SpaceBetween>
            </div>

            {/* PDF Document */}
            <div
                style={{
                    flex: 1,
                    overflow: "auto",
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "flex-start",
                    padding: "20px",
                    backgroundColor: "#f9f9f9",
                }}
            >
                <Document
                    file={file}
                    onLoadSuccess={onDocumentLoadSuccess}
                    onLoadError={onDocumentLoadError}
                    loading={
                        <Box textAlign="center" padding="xl">
                            <div>Loading PDF document...</div>
                        </Box>
                    }
                    error={
                        <Box textAlign="center" padding="xl">
                            <div style={{ color: "red" }}>
                                Failed to load PDF document. Please check if the file is valid.
                            </div>
                        </Box>
                    }
                >
                    <Page
                        pageNumber={pageNumber}
                        scale={scale}
                        width={pageWidth}
                        loading={
                            <Box textAlign="center" padding="xl">
                                <div>Loading page...</div>
                            </Box>
                        }
                        error={
                            <Box textAlign="center" padding="xl">
                                <div style={{ color: "red" }}>Failed to load page {pageNumber}</div>
                            </Box>
                        }
                        renderTextLayer={false}
                        renderAnnotationLayer={false}
                    />
                </Document>
            </div>

            {/* Delete Preview Button */}
            {onDeletePreview && (
                <div
                    style={{
                        padding: "10px",
                        borderTop: "1px solid #e0e0e0",
                        backgroundColor: "#f5f5f5",
                        flexShrink: 0,
                    }}
                >
                    <Button iconName="remove" variant="link" onClick={onDeletePreview}>
                        Delete Preview File
                    </Button>
                </div>
            )}
        </div>
    );
};

export default PDFViewerComponent;
