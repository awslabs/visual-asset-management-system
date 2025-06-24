/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useRef, useCallback } from "react";
import {
    Box,
    Button,
    FormField,
    SpaceBetween,
    Spinner,
    StatusIndicator,
    TextContent,
} from "@cloudscape-design/components";
import { FileUploadTableItem } from "../../pages/AssetUpload/FileUploadTable";

interface EnhancedFileSelectorProps {
    label: string;
    description?: string;
    errorText?: string;
    onSelect: (directoryHandle: any, fileHandles: any[]) => void;
    multiFile: boolean;
    disabled?: boolean;
    selectionMode: "folder" | "files" | "both";
}

interface FileHandle {
    handle: any;
    path: string;
}

export default function EnhancedFileSelector({
    label,
    description,
    errorText,
    onSelect,
    multiFile,
    disabled = false,
    selectionMode = "both",
}: EnhancedFileSelectorProps) {
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

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
            console.error("Error selecting folder:", err);
            setError(err.message || "Failed to select folder");
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
            // User cancelled selection - don't show error
            if (err.name !== "AbortError") {
                console.error("Error selecting files:", err);
                setError(err.message || "Failed to select files");
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

    return (
        <FormField label={label} description={description} errorText={errorText || error}>
            <SpaceBetween direction="horizontal" size="xs">
                {selectionMode !== "files" && (
                    <Button
                        onClick={handleFolderSelect}
                        disabled={disabled || isLoading}
                        iconName="folder"
                    >
                        {isLoading ? <Spinner /> : "Select Folder"}
                    </Button>
                )}

                {selectionMode !== "folder" && (
                    <Button
                        onClick={handleFileSelect}
                        disabled={disabled || isLoading}
                        iconName="file"
                    >
                        {isLoading ? <Spinner /> : `Select ${multiFile ? "Files" : "File"}`}
                    </Button>
                )}

                {/* Hidden traditional file input as fallback */}
                <input
                    type="file"
                    ref={fileInputRef}
                    style={{ display: "none" }}
                    multiple={multiFile}
                    onChange={handleTraditionalFileSelect}
                />
            </SpaceBetween>
        </FormField>
    );
}
