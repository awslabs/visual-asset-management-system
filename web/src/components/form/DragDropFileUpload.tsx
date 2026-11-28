/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The shared upload control of the Create Asset and Add Files pages (#1462).
 *
 * Two explicit actions, Add files and Add folder, and one drop zone that takes files and folders
 * together. Every way of adding items ends in UploadRoot[] (see uploadPaths.ts), and the pages
 * derive the final asset paths from those roots with planUploadPaths, so a picked folder and a
 * dropped folder always land at the same path. This control never builds asset paths itself.
 */

import React, { useState, useRef, useCallback, DragEvent } from "react";
import { Box, Button, FormField, SpaceBetween, Spinner } from "@cloudscape-design/components";
import {
    UploadRoot,
    countRootFiles,
    rootFromDirectoryHandle,
    rootsFromDataTransferItems,
    rootsFromInputFiles,
} from "./uploadPaths";

export interface UploadPickerProps {
    label: string;
    description?: string;
    errorText?: string;
    disabled?: boolean;
    accept?: string;
    /** Called with the items added by one action (pick or drop). */
    onAdd: (roots: UploadRoot[]) => void;
}

const isUserCancellation = (err: any) =>
    err?.name === "AbortError" ||
    err?.message?.includes("aborted") ||
    err?.message?.includes("cancelled") ||
    err?.message?.includes("canceled") ||
    err?.code === 20; // DOMException.ABORT_ERR

// webkitdirectory is not in React's input typings; it is a boolean attribute every current
// browser supports (Chrome, Edge, Firefox, Safari).
const directoryInputAttributes = { webkitdirectory: "", directory: "" } as any;

export default function DragDropFileUpload({
    label,
    description,
    errorText,
    disabled = false,
    accept,
    onAdd,
}: UploadPickerProps) {
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [status, setStatus] = useState("");
    const [isDragOver, setIsDragOver] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const folderInputRef = useRef<HTMLInputElement>(null);
    const dragCounterRef = useRef(0);

    const deliver = useCallback(
        (roots: UploadRoot[], emptyMessage: string) => {
            const count = countRootFiles(roots);
            if (count === 0) {
                setError(emptyMessage);
                setStatus("");
                return;
            }
            setError(null);
            setStatus(`Added ${count} ${count === 1 ? "file" : "files"}.`);
            onAdd(roots);
        },
        [onAdd]
    );

    const handleDragEnter = useCallback((e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
        dragCounterRef.current++;
        if (e.dataTransfer?.items && e.dataTransfer.items.length > 0) setIsDragOver(true);
    }, []);

    const handleDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
        dragCounterRef.current--;
        if (dragCounterRef.current <= 0) {
            dragCounterRef.current = 0;
            setIsDragOver(false);
        }
    }, []);

    const handleDragOver = useCallback(
        (e: DragEvent<HTMLDivElement>) => {
            e.preventDefault();
            e.stopPropagation();
            if (e.dataTransfer) e.dataTransfer.dropEffect = disabled ? "none" : "copy";
            setIsDragOver(true);
        },
        [disabled]
    );

    const handleDrop = useCallback(
        async (e: DragEvent<HTMLDivElement>) => {
            e.preventDefault();
            e.stopPropagation();
            setIsDragOver(false);
            dragCounterRef.current = 0;
            if (disabled || isLoading) return;

            try {
                setIsLoading(true);
                setError(null);
                setStatus("Processing dropped items...");
                const roots = await rootsFromDataTransferItems(e.dataTransfer.items);
                deliver(roots, "No files found in the dropped items.");
            } catch (err: any) {
                console.error("Error processing dropped items:", err);
                setError(err?.message || "Failed to process the dropped items.");
                setStatus("");
            } finally {
                setIsLoading(false);
            }
        },
        [disabled, isLoading, deliver]
    );

    const openFolderInput = () => folderInputRef.current?.click();

    // Set once showDirectoryPicker has failed in this control: later clicks go straight to the
    // webkitdirectory input, synchronously inside the click, where the browser always allows it.
    const pickerBrokenRef = useRef(false);

    const handleAddFolder = async () => {
        // showDirectoryPicker is only an enhancement; the webkitdirectory input works everywhere
        // and the two give identical roots. Without it (or once it has failed) the input is
        // opened before any await, so the click keeps its user activation.
        const picker = (window as any).showDirectoryPicker;
        if (typeof picker !== "function" || pickerBrokenRef.current) {
            openFolderInput();
            return;
        }
        try {
            const dirHandle = await picker.call(window);
            setIsLoading(true);
            setError(null);
            setStatus("Processing folder...");
            const root = await rootFromDirectoryHandle(dirHandle);
            deliver([root], "The selected folder holds no files.");
        } catch (err: any) {
            if (isUserCancellation(err)) {
                setStatus("");
                return;
            }
            console.warn("Folder picker failed, using the folder input instead:", err);
            pickerBrokenRef.current = true;
            // The browser may refuse to open a dialog this long after the click, and that refusal
            // cannot be detected, so always say what to do; choosing a folder replaces the text.
            setStatus(
                "The folder picker could not open. Select Add folder again to choose a folder."
            );
            openFolderInput();
        } finally {
            setIsLoading(false);
        }
    };

    const handleInputChange =
        (ref: React.RefObject<HTMLInputElement>, emptyMessage: string) =>
        (e: React.ChangeEvent<HTMLInputElement>) => {
            try {
                const files = e.target.files ? Array.from(e.target.files) : [];
                if (files.length === 0) return; // cancelled
                deliver(rootsFromInputFiles(files), emptyMessage);
            } catch (err: any) {
                console.error("Error reading the selected items:", err);
                setError(err?.message || "Failed to read the selected items.");
            } finally {
                // Reset so the same item can be added again.
                if (ref.current) ref.current.value = "";
            }
        };

    const active = isDragOver && !disabled;
    const dropZoneStyles: React.CSSProperties = {
        border: active ? "2px solid var(--vams-color-info)" : "2px dashed var(--vams-border-input)",
        borderRadius: "8px",
        padding: "24px",
        textAlign: "center",
        backgroundColor: active ? "var(--vams-bg-hover)" : "var(--vams-bg-secondary)",
        transition: "all 0.2s ease-in-out",
        opacity: disabled ? 0.6 : 1,
    };

    return (
        <FormField label={label} description={description} errorText={errorText || error}>
            <div
                role="group"
                aria-label="Upload drop zone: drop files or folders here"
                aria-disabled={disabled || undefined}
                data-testid="upload-drop-zone"
                style={dropZoneStyles}
                onDragEnter={handleDragEnter}
                onDragLeave={handleDragLeave}
                onDragOver={handleDragOver}
                onDrop={handleDrop}
            >
                <SpaceBetween direction="vertical" size="m" alignItems="center">
                    {isLoading ? (
                        <Spinner />
                    ) : (
                        <Box fontSize="heading-s" color="text-label">
                            {active ? "Release to add" : "Drop files or folders here"}
                        </Box>
                    )}
                    <Box color="text-body-secondary">or</Box>
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            onClick={() => fileInputRef.current?.click()}
                            disabled={disabled || isLoading}
                            iconName="file"
                            ariaLabel="Add files"
                        >
                            Add files
                        </Button>
                        <Button
                            onClick={handleAddFolder}
                            disabled={disabled || isLoading}
                            iconName="folder"
                            ariaLabel="Add folder"
                        >
                            Add folder
                        </Button>
                    </SpaceBetween>
                </SpaceBetween>
                <div aria-live="polite" style={{ marginTop: "8px" }}>
                    <Box color="text-body-secondary" fontSize="body-s">
                        {status}
                    </Box>
                </div>

                <input
                    type="file"
                    ref={fileInputRef}
                    data-testid="upload-files-input"
                    style={{ display: "none" }}
                    multiple
                    accept={accept}
                    tabIndex={-1}
                    aria-hidden="true"
                    onChange={handleInputChange(fileInputRef, "No files were selected.")}
                />
                <input
                    type="file"
                    ref={folderInputRef}
                    data-testid="upload-folder-input"
                    style={{ display: "none" }}
                    multiple
                    tabIndex={-1}
                    aria-hidden="true"
                    {...directoryInputAttributes}
                    onChange={handleInputChange(
                        folderInputRef,
                        "The selected folder holds no files."
                    )}
                />
            </div>
        </FormField>
    );
}
