/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, {
    useEffect,
    useReducer,
    useState,
    useMemo,
    useCallback,
    useRef,
    DragEvent,
} from "react";
import {
    Container,
    FormField,
    Button,
    SpaceBetween,
    Toggle,
    Box,
    Spinner,
    TextContent,
} from "@cloudscape-design/components";

export interface FileInfo {
    path: string;
    handle: any;
}

interface MultiFileSelectState {
    directories: {
        [key: string]: FileInfo[];
    };
    files: FileInfo[];
}

interface MultiFileSelectAction {
    type: string;
    payload: any;
}

function multiFileSelectReducer(state: MultiFileSelectState, action: MultiFileSelectAction) {
    switch (action.type) {
        case "ADD_DIRECTORY":
            return {
                ...state,
                directories: {
                    ...state.directories,
                    [action.payload.directory]: action.payload.files,
                },
            };
        case "REMOVE_DIRECTORY":
            return {
                ...state,
                directories: {
                    ...state.directories,
                    [action.payload.name]: undefined,
                },
            };
        case "ADD_FILES":
            return {
                ...state,
                files: [...state.files, ...action.payload.fileHandles],
            };
        case "REMOVE_FILE":
            return {
                ...state,
                files: state.files.filter((file) => file.handle !== action.payload.fileHandle),
            };
        case "CLEAR_ALL":
            return {
                directories: {},
                files: [],
            };
        default:
            return state;
    }
}

const getAllFiles = (multiFileSelect: MultiFileSelectState): FileInfo[] => {
    let fileInfo: FileInfo[] = [];
    for (const directory in multiFileSelect.directories) {
        if (multiFileSelect.directories[directory]) {
            fileInfo = [...fileInfo, ...multiFileSelect.directories[directory]];
        }
    }
    for (const file of multiFileSelect.files) {
        fileInfo.push(file);
    }
    return fileInfo;
};

interface DragDropMultiFileSelectProps {
    onChange: (state: FileInfo[]) => void;
    label?: string;
    description?: string;
    key?: number;
    externalFileCount?: number;
}

export function DragDropMultiFileSelect({
    onChange,
    label,
    description,
    externalFileCount,
}: DragDropMultiFileSelectProps) {
    const initialState: MultiFileSelectState = {
        directories: {},
        files: [],
    };

    const [isMultiFile, setIsMultiFile] = useState(false);
    const [state, dispatch] = useReducer(multiFileSelectReducer, initialState);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [isDragOver, setIsDragOver] = useState(false);
    const [isDragActive, setIsDragActive] = useState(false);
    const dragCounterRef = useRef(0);
    const [lastExternalFileCount, setLastExternalFileCount] = useState(0);

    useEffect(() => {
        onChange(getAllFiles(state));
    }, [state, onChange]);

    // Clear internal state when external file count goes to 0 (files were removed externally)
    useEffect(() => {
        if (
            externalFileCount !== undefined &&
            externalFileCount === 0 &&
            lastExternalFileCount > 0
        ) {
            dispatch({ type: "CLEAR_ALL", payload: {} });
        }
        setLastExternalFileCount(externalFileCount || 0);
    }, [externalFileCount, lastExternalFileCount]);

    // Calculate total files count
    const totalFilesCount = useMemo(() => {
        return (
            state.files.length +
            Object.keys(state.directories).reduce((acc, directory) => {
                if (state.directories[directory]) {
                    return acc + state.directories[directory].length;
                } else {
                    return acc;
                }
            }, 0)
        );
    }, [state.files, state.directories]);

    const processDroppedItems = async (items: DataTransferItemList) => {
        const fileHandles: FileInfo[] = [];
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
        fileHandles: FileInfo[]
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

    const handleDrop = useCallback(async (e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();

        setIsDragActive(false);
        setIsDragOver(false);
        dragCounterRef.current = 0;

        try {
            setIsLoading(true);
            setError(null);

            const { directoryHandle, fileHandles } = await processDroppedItems(
                e.dataTransfer.items
            );

            if (fileHandles.length === 0) {
                throw new Error("No valid files found in the dropped items");
            }

            if (directoryHandle) {
                dispatch({
                    type: "ADD_DIRECTORY",
                    payload: {
                        directory: directoryHandle.name,
                        files: fileHandles,
                    },
                });
                setIsMultiFile(true);
            } else {
                dispatch({
                    type: "ADD_FILES",
                    payload: {
                        fileHandles: fileHandles,
                    },
                });
            }
        } catch (err: any) {
            console.error("Error processing dropped files:", err);
            setError(err.message || "Failed to process dropped files");
        } finally {
            setIsLoading(false);
        }
    }, []);

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

            const directoryHandle = await window.showDirectoryPicker();
            const fileHandles: FileInfo[] = [];

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

            dispatch({
                type: "ADD_DIRECTORY",
                payload: {
                    directory: directoryHandle.name,
                    files: fileHandles,
                },
            });
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
                const fileHandles = await window.showOpenFilePicker({
                    multiple: true,
                });

                if (fileHandles.length === 0) {
                    throw new Error("No files selected");
                }

                const processedHandles = fileHandles.map((handle) => ({
                    handle,
                    path: handle.name,
                }));

                dispatch({
                    type: "ADD_FILES",
                    payload: {
                        fileHandles: processedHandles,
                    },
                });
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

    // Clear all files function for external use
    const clearAll = useCallback(() => {
        dispatch({ type: "CLEAR_ALL", payload: {} });
    }, []);

    const dropZoneStyles = {
        border: isDragActive || isDragOver ? "2px solid #0972d3" : "2px dashed #879596",
        borderRadius: "8px",
        padding: "24px",
        textAlign: "center" as const,
        backgroundColor: isDragActive || isDragOver ? "#f2f8fd" : "#fafbfc",
        transition: "all 0.2s ease-in-out",
        cursor: isLoading ? "not-allowed" : "pointer",
        opacity: isLoading ? 0.6 : 1,
    };

    const displayDescription =
        description ||
        (totalFilesCount > 0
            ? `Total Files to Upload: ${totalFilesCount}`
            : "Select a folder or multiple files");

    return (
        <FormField
            label={label || "Asset Files"}
            description={displayDescription}
            errorText={error}
        >
            <SpaceBetween size="xs" direction="vertical">
                <Toggle onChange={() => setIsMultiFile(!isMultiFile)} checked={isMultiFile}>
                    {isMultiFile ? "Folder Upload" : "File Upload"}
                </Toggle>

                <div
                    style={dropZoneStyles}
                    onDragEnter={handleDragEnter}
                    onDragLeave={handleDragLeave}
                    onDragOver={handleDragOver}
                    onDrop={handleDrop}
                    onClick={() => {
                        if (!isLoading) {
                            if (isMultiFile) {
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
                            <Box fontSize="heading-s" color="text-label">
                                {isDragActive || isDragOver
                                    ? "Drop files here"
                                    : "Drag and drop files here"}
                            </Box>
                            <Box color="text-body-secondary">or click to browse</Box>

                            <SpaceBetween direction="horizontal" size="xs">
                                {isMultiFile ? (
                                    <Button
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            handleFolderSelect();
                                        }}
                                        disabled={isLoading}
                                        iconName="folder"
                                        variant="normal"
                                    >
                                        Choose Folder
                                    </Button>
                                ) : (
                                    <Button
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            handleFileSelect();
                                        }}
                                        disabled={isLoading}
                                        iconName="file"
                                        variant="normal"
                                    >
                                        Choose Files
                                    </Button>
                                )}
                            </SpaceBetween>
                        </SpaceBetween>
                    )}
                </div>
            </SpaceBetween>
        </FormField>
    );
}
