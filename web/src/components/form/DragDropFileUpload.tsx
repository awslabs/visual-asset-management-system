/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useRef, useCallback, DragEvent } from "react";
import {
    Box,
    Button,
    FormField,
    SpaceBetween,
    Spinner,
    StatusIndicator,
    TextContent,
} from "@cloudscape-design/components";

interface DragDropFileUploadProps {
    label: string;
    description?: string;
    errorText?: string;
    onSelect: (directoryHandle: any, fileHandles: any[]) => void;
    multiFile: boolean;
    disabled?: boolean;
    selectionMode: "folder" | "files" | "both";
    accept?: string;
    children?: React.ReactNode;
}

interface FileHandle {
    handle: any;
    path: string;
}

export default function DragDropFileUpload({
    label,
    description,
    errorText,
    onSelect,
    multiFile,
    disabled = false,
    selectionMode = "both",
    accept,
    children,
}: DragDropFileUploadProps) {
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [isDragOver, setIsDragOver] = useState(false);
    const [isDragActive, setIsDragActive] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const dragCounterRef = useRef(0);

    const processFileEntry = async (
        entry: any,
        path: string = "",
        fileHandles: FileHandle[] = []
    ): Promise<FileHandle[]> => {
        if (entry.kind === "file") {
            const file = entry;
            fileHandles.push({
                handle: file,
                path: path ? `${path}/${file.name}` : file.name,
            });
            return fileHandles;
        } else if (entry.kind === "directory") {
            const directoryReader = entry.createReader();
            const entries = await new Promise<any[]>((resolve) => {
                directoryReader.readEntries((entries: any[]) => resolve(entries));
            });

            for (const childEntry of entries) {
                await processFileEntry(
                    childEntry,
                    path ? `${path}/${entry.name}` : entry.name,
                    fileHandles
                );
            }
            return fileHandles;
        }
        return fileHandles;
    };

    const processDroppedItems = async (items: DataTransferItemList) => {
        const fileHandles: FileHandle[] = [];
        let directoryHandle = null;

        for (let i = 0; i < items.length; i++) {
            const item = items[i];

            if (item.kind === "file") {
                // Check if it's a directory entry (modern browsers)
                if (item.webkitGetAsEntry) {
                    const entry = item.webkitGetAsEntry();
                    if (entry) {
                        if (entry.isDirectory) {
                            // For directories, we'll treat the first one as the main directory
                            if (!directoryHandle) {
                                directoryHandle = { name: entry.name };
                            }
                            await processDirectoryEntry(entry, entry.name, fileHandles);
                        } else if (entry.isFile) {
                            fileHandles.push({
                                handle: item.getAsFile(),
                                path: entry.name,
                            });
                        }
                    }
                } else {
                    // Fallback for browsers without webkitGetAsEntry
                    const file = item.getAsFile();
                    if (file) {
                        fileHandles.push({
                            handle: file,
                            path: file.name,
                        });
                    }
                }
            }
        }

        return { directoryHandle, fileHandles };
    };

    const processDirectoryEntry = async (
        dirEntry: any,
        path: string,
        fileHandles: FileHandle[]
    ): Promise<void> => {
        return new Promise((resolve, reject) => {
            const dirReader = dirEntry.createReader();

            const readEntries = () => {
                dirReader.readEntries(async (entries: any[]) => {
                    if (entries.length === 0) {
                        resolve();
                        return;
                    }

                    for (const entry of entries) {
                        if (entry.isFile) {
                            entry.file((file: File) => {
                                fileHandles.push({
                                    handle: file,
                                    path: `${path}/${file.name}`,
                                });
                            });
                        } else if (entry.isDirectory) {
                            await processDirectoryEntry(
                                entry,
                                `${path}/${entry.name}`,
                                fileHandles
                            );
                        }
                    }

                    // Continue reading if there are more entries
                    readEntries();
                }, reject);
            };

            readEntries();
        });
    };

    const handleDragEnter = useCallback((e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();

        dragCounterRef.current++;

        if (e.dataTransfer.items && e.dataTransfer.items.length > 0) {
            setIsDragActive(true);
        }
    }, []);

    const handleDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();

        dragCounterRef.current--;

        if (dragCounterRef.current === 0) {
            setIsDragActive(false);
            setIsDragOver(false);
        }
    }, []);

    const handleDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();

        if (e.dataTransfer) {
            e.dataTransfer.dropEffect = "copy";
        }

        setIsDragOver(true);
    }, []);

    const handleDrop = useCallback(
        async (e: DragEvent<HTMLDivElement>) => {
            e.preventDefault();
            e.stopPropagation();

            setIsDragActive(false);
            setIsDragOver(false);
            dragCounterRef.current = 0;

            if (disabled) return;

            try {
                setIsLoading(true);
                setError(null);

                const { directoryHandle, fileHandles } = await processDroppedItems(
                    e.dataTransfer.items
                );

                if (fileHandles.length === 0) {
                    throw new Error("No valid files found in the dropped items");
                }

                onSelect(directoryHandle, fileHandles);
            } catch (err: any) {
                console.error("Error processing dropped files:", err);
                setError(err.message || "Failed to process dropped files");
            } finally {
                setIsLoading(false);
            }
        },
        [disabled, onSelect]
    );

    const handleFolderSelect = async () => {
        try {
            setIsLoading(true);
            setError(null);

            // Check if the File System Access API is available
            if (!window.showDirectoryPicker) {
                throw new Error(
                    "Folder selection is not supported in this browser. Please use Chrome, Edge, or another browser that supports the File System Access API."
                );
            }

            // Show directory picker
            const directoryHandle = await window.showDirectoryPicker();
            const fileHandles: FileHandle[] = [];

            // Process all files in the directory recursively
            for await (const entry of directoryHandle.values()) {
                if (entry.kind === "file") {
                    fileHandles.push({
                        handle: entry,
                        path: entry.name,
                    });
                } else if (entry.kind === "directory") {
                    // Process subdirectory
                    const subDirPath = entry.name;
                    const processSubDir = async (dirHandle: any, currentPath: string) => {
                        for await (const subEntry of dirHandle.values()) {
                            if (subEntry.kind === "file") {
                                fileHandles.push({
                                    handle: subEntry,
                                    path: `${currentPath}/${subEntry.name}`,
                                });
                            } else if (subEntry.kind === "directory") {
                                await processSubDir(subEntry, `${currentPath}/${subEntry.name}`);
                            }
                        }
                    };
                    await processSubDir(entry, subDirPath);
                }
            }

            if (fileHandles.length === 0) {
                throw new Error("No files found in the selected folder");
            }

            onSelect(directoryHandle, fileHandles);
        } catch (err: any) {
            // Check for user cancellation in multiple ways
            const isUserCancellation =
                err.name === "AbortError" ||
                err.message?.includes("aborted") ||
                err.message?.includes("cancelled") ||
                err.message?.includes("canceled") ||
                err.code === 20; // DOMException.ABORT_ERR

            if (!isUserCancellation) {
                console.error("Error selecting folder:", err);
                setError(err.message || "Failed to select folder");
            } else {
                console.log("User cancelled folder selection");
            }
        } finally {
            setIsLoading(false);
        }
    };

    const handleFileSelect = async () => {
        try {
            setIsLoading(true);
            setError(null);

            // Check if the File System Access API is available
            if (window.showOpenFilePicker) {
                // Modern approach using File System Access API
                const fileHandles = await window.showOpenFilePicker({
                    multiple: multiFile,
                });

                if (fileHandles.length === 0) {
                    throw new Error("No files selected");
                }

                const processedHandles = fileHandles.map((handle) => ({
                    handle,
                    path: handle.name,
                }));

                onSelect(null, processedHandles);
            } else {
                // Fallback to traditional file input
                if (fileInputRef.current) {
                    fileInputRef.current.click();
                }
            }
        } catch (err: any) {
            // Check for user cancellation in multiple ways
            const isUserCancellation =
                err.name === "AbortError" ||
                err.message?.includes("aborted") ||
                err.message?.includes("cancelled") ||
                err.message?.includes("canceled") ||
                err.code === 20; // DOMException.ABORT_ERR

            if (!isUserCancellation) {
                console.error("Error selecting files:", err);
                setError(err.message || "Failed to select files");
            } else {
                console.log("User cancelled file selection");
            }
        } finally {
            setIsLoading(false);
        }
    };

    const handleTraditionalFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
        try {
            const files = e.target.files;
            if (!files || files.length === 0) {
                return;
            }

            const fileHandles = Array.from(files).map((file) => ({
                handle: file,
                path: file.name,
            }));

            onSelect(null, fileHandles);
        } catch (err: any) {
            console.error("Error with traditional file select:", err);
            setError(err.message || "Failed to process selected files");
        } finally {
            // Reset the input so the same file can be selected again
            if (fileInputRef.current) {
                fileInputRef.current.value = "";
            }
        }
    };

    const dropZoneStyles = {
        border: isDragActive || isDragOver ? "2px solid #0972d3" : "2px dashed #879596",
        borderRadius: "8px",
        padding: "24px",
        textAlign: "center" as const,
        backgroundColor: isDragActive || isDragOver ? "#f2f8fd" : "#fafbfc",
        transition: "all 0.2s ease-in-out",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.6 : 1,
    };

    return (
        <FormField label={label} description={description} errorText={errorText || error}>
            <div
                style={dropZoneStyles}
                onDragEnter={handleDragEnter}
                onDragLeave={handleDragLeave}
                onDragOver={handleDragOver}
                onDrop={handleDrop}
                onClick={() => {
                    if (!disabled && !isLoading) {
                        if (selectionMode === "folder") {
                            handleFolderSelect();
                        } else {
                            handleFileSelect();
                        }
                    }
                }}
            >
                {isLoading ? (
                    <SpaceBetween direction="vertical" size="s">
                        <Spinner />
                        <TextContent>
                            <span>Processing files...</span>
                        </TextContent>
                    </SpaceBetween>
                ) : (
                    <SpaceBetween direction="vertical" size="m">
                        {children || (
                            <>
                                <Box fontSize="heading-s" color="text-label">
                                    {isDragActive || isDragOver
                                        ? "Drop files here"
                                        : "Drag and drop files here"}
                                </Box>
                                <Box color="text-body-secondary">or click to browse</Box>
                            </>
                        )}

                        <SpaceBetween direction="horizontal" size="xs">
                            {selectionMode !== "files" && (
                                <Button
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        handleFolderSelect();
                                    }}
                                    disabled={disabled || isLoading}
                                    iconName="folder"
                                    variant="normal"
                                >
                                    Select Folder
                                </Button>
                            )}

                            {selectionMode !== "folder" && (
                                <Button
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        handleFileSelect();
                                    }}
                                    disabled={disabled || isLoading}
                                    iconName="file"
                                    variant="normal"
                                >
                                    Select {multiFile ? "Files" : "File"}
                                </Button>
                            )}
                        </SpaceBetween>
                    </SpaceBetween>
                )}

                {/* Hidden traditional file input as fallback */}
                <input
                    type="file"
                    ref={fileInputRef}
                    style={{ display: "none" }}
                    multiple={multiFile}
                    accept={accept}
                    onChange={handleTraditionalFileSelect}
                />
            </div>
        </FormField>
    );
}
