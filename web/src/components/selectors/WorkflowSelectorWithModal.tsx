/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    Modal,
    Select,
    SpaceBetween,
    FormField,
    Button,
    Box,
    Alert,
    Icon,
    TextFilter,
    Popover,
    Spinner,
} from "@cloudscape-design/components";
import React, { useEffect, useState, useMemo } from "react";
import { useNavigate } from "react-router";
import { fetchDatabaseWorkflows, runWorkflow } from "../../services/APIService";
import { fetchAssetS3Files } from "../../services/APIService";
import Synonyms from "../../synonyms";

// Helper function to check if a file has an extension (is a file, not a folder)
const isFile = (file) => {
    return file.fileName && file.fileName.includes(".");
};

// Build a tree structure from flat file list
interface TreeNode {
    name: string;
    path: string; // relative path for display
    key: string | null; // S3 key for files, null for folders
    isFolder: boolean;
    children: TreeNode[];
}

function buildFileTree(files: any[]): TreeNode {
    const root: TreeNode = {
        name: `/ (Entire ${Synonyms.Asset})`,
        path: "/",
        key: null,
        isFolder: true,
        children: [],
    };

    for (const file of files) {
        const relativePath = file.relativePath || file.fileName || "";
        // Remove leading slash
        const cleanPath = relativePath.startsWith("/") ? relativePath.slice(1) : relativePath;
        const parts = cleanPath.split("/");

        let current = root;
        for (let i = 0; i < parts.length; i++) {
            const part = parts[i];
            if (!part) continue;

            const isLastPart = i === parts.length - 1;
            const isFileNode = isLastPart && isFile(file);

            // Look for existing child
            let child = current.children.find((c) => c.name === part && c.isFolder === !isFileNode);

            if (!child) {
                child = {
                    name: part,
                    path: parts.slice(0, i + 1).join("/"),
                    key: isFileNode ? file.key : null,
                    isFolder: !isFileNode,
                    children: [],
                };
                current.children.push(child);
            }

            current = child;
        }
    }

    // Sort: folders first (alphabetical), then files (alphabetical)
    const sortTree = (node: TreeNode) => {
        node.children.sort((a, b) => {
            if (a.isFolder && !b.isFolder) return -1;
            if (!a.isFolder && b.isFolder) return 1;
            return a.name.localeCompare(b.name);
        });
        node.children.forEach(sortTree);
    };
    sortTree(root);

    return root;
}

// File tree item component
function FileTreeItem({
    node,
    selectedKey,
    onSelect,
    expandedPaths,
    onToggle,
    depth,
    searchTerm,
}: {
    node: TreeNode;
    selectedKey: string | null;
    onSelect: (key: string | null, path: string) => void;
    expandedPaths: Set<string>;
    onToggle: (path: string) => void;
    depth: number;
    searchTerm: string;
}) {
    const isExpanded = expandedPaths.has(node.path);
    const isSelected =
        (selectedKey === null && node.path === "/") ||
        (selectedKey !== null && node.key === selectedKey);
    const hasChildren = node.children.length > 0;

    // Filter by search term
    const matchesSearch =
        !searchTerm ||
        node.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        node.path.toLowerCase().includes(searchTerm.toLowerCase());

    // Check if any descendant matches
    const hasMatchingDescendant = (n: TreeNode): boolean => {
        if (n.name.toLowerCase().includes(searchTerm.toLowerCase())) return true;
        return n.children.some(hasMatchingDescendant);
    };

    const shouldShow =
        !searchTerm || matchesSearch || (node.isFolder && hasMatchingDescendant(node));
    if (!shouldShow) return null;

    return (
        <div>
            <div
                style={{
                    display: "flex",
                    alignItems: "center",
                    padding: "4px 8px",
                    paddingLeft: `${depth * 20 + 8}px`,
                    cursor: "pointer",
                    borderRadius: "4px",
                    backgroundColor: isSelected
                        ? "var(--color-background-item-selected, rgba(9, 105, 218, 0.08))"
                        : "transparent",
                    fontWeight: isSelected ? 600 : 400,
                    fontSize: "14px",
                }}
                onClick={() => {
                    if (node.isFolder && node.path === "/") {
                        onSelect(null, "/"); // Root = process entire asset
                    } else if (!node.isFolder) {
                        onSelect(node.key, node.path);
                    } else {
                        // Clicking a non-root folder just toggles expand
                        onToggle(node.path);
                    }
                }}
            >
                {node.isFolder && hasChildren && (
                    <span
                        style={{ marginRight: "4px", display: "inline-flex" }}
                        onClick={(e) => {
                            e.stopPropagation();
                            onToggle(node.path);
                        }}
                    >
                        {isExpanded ? (
                            <Icon name="caret-down-filled" size="small" />
                        ) : (
                            <Icon name="caret-right-filled" size="small" />
                        )}
                    </span>
                )}
                {node.isFolder && !hasChildren && (
                    <span style={{ width: "16px", display: "inline-block" }} />
                )}
                <span style={{ marginRight: "6px", display: "inline-flex" }}>
                    {node.isFolder ? (
                        isExpanded ? (
                            <Icon name="folder-open" size="small" />
                        ) : (
                            <Icon name="folder" size="small" />
                        )
                    ) : (
                        <Icon name="file" size="small" />
                    )}
                </span>
                <span
                    style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                >
                    {node.name}
                </span>
            </div>
            {node.isFolder && isExpanded && (
                <div>
                    {node.children.map((child) => (
                        <FileTreeItem
                            key={child.path}
                            node={child}
                            selectedKey={selectedKey}
                            onSelect={onSelect}
                            expandedPaths={expandedPaths}
                            onToggle={onToggle}
                            depth={depth + 1}
                            searchTerm={searchTerm}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}

export default function WorkflowSelectorWithModal(props) {
    const { databaseId, assetId, setOpen, open, onWorkflowExecuted } = props;
    const [reload, setReload] = useState(true);
    const [allItems, setAllItems] = useState([]);
    const [selectedWorkflow, setSelectedWorkflow] = useState(null);
    const [selectedFileKey, setSelectedFileKey] = useState<string | null>(null);
    const [selectedFilePath, setSelectedFilePath] = useState<string>("/");
    const [assetFiles, setAssetFiles] = useState([]);
    const [loadingFiles, setLoadingFiles] = useState(false);
    const [apiError, setApiError] = useState(null);
    const [isExecuting, setIsExecuting] = useState(false);
    const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set(["/"]));
    const [fileSearchTerm, setFileSearchTerm] = useState("");
    const navigate = useNavigate();

    useEffect(() => {
        const getData = async () => {
            const itemsGlobal = await fetchDatabaseWorkflows({ databaseId: "GLOBAL" });
            const itemsDb = await fetchDatabaseWorkflows({ databaseId: databaseId });
            const items = [...itemsDb, ...itemsGlobal];
            if (items !== false && Array.isArray(items)) {
                setReload(false);
                setAllItems(items);
            }
        };
        if (reload) {
            getData();
        }
    }, [databaseId, reload]);

    // Fetch asset files when modal opens
    useEffect(() => {
        const fetchFiles = async () => {
            if (!open || !databaseId || !assetId) return;

            setLoadingFiles(true);
            setApiError(null);

            try {
                const [success, files] = await fetchAssetS3Files({
                    databaseId: databaseId,
                    assetId: assetId,
                    includeArchived: false,
                    basic: true,
                });

                if (success && files && Array.isArray(files)) {
                    setAssetFiles(files);
                } else if (!success) {
                    setApiError(
                        typeof files === "string"
                            ? files
                            : `Failed to load ${Synonyms.asset} files. Please try again.`
                    );
                }
            } catch (error) {
                console.error("Error fetching asset files:", error);
                setApiError(
                    `Failed to load ${Synonyms.asset} files: ${error.message || "Unknown error"}`
                );
            } finally {
                setLoadingFiles(false);
            }
        };

        fetchFiles();
    }, [open, databaseId, assetId]);

    // Build file tree from flat file list
    const fileTree = useMemo(() => {
        if (!assetFiles || assetFiles.length === 0) return null;
        return buildFileTree(assetFiles);
    }, [assetFiles]);

    const handleWorkflowSelection = (event) => {
        const selectedOption = event.detail.selectedOption.value;
        setSelectedWorkflow(selectedOption);
    };

    const handleFileSelect = (key: string | null, path: string) => {
        setSelectedFileKey(key);
        setSelectedFilePath(path);
    };

    const handleToggleExpanded = (path: string) => {
        setExpandedPaths((prev) => {
            const next = new Set(prev);
            if (next.has(path)) {
                next.delete(path);
            } else {
                next.add(path);
            }
            return next;
        });
    };

    const handleExecuteWorkflow = async () => {
        if (!selectedWorkflow) return;

        setApiError(null);
        setIsExecuting(true);

        const isGlobalWorkflow = selectedWorkflow.databaseId === "GLOBAL";

        try {
            const result = await runWorkflow({
                databaseId: databaseId,
                assetId: assetId,
                workflowId: selectedWorkflow.workflowId,
                ...(selectedFileKey && { fileKey: selectedFileKey }),
                isGlobalWorkflow: isGlobalWorkflow,
            });

            if (result !== false && Array.isArray(result)) {
                if (result[0] === false) {
                    const errorMessage =
                        result[1] || "Failed to execute workflow. Please try again.";
                    setApiError(errorMessage);
                } else {
                    if (typeof onWorkflowExecuted === "function") {
                        onWorkflowExecuted();
                    }
                    handleClose();
                }
            } else {
                setApiError("Received an invalid response from the server. Please try again.");
            }
        } catch (error) {
            console.error("Error executing workflow:", error);
            setApiError(`An unexpected error occurred: ${error.message || "Unknown error"}`);
        } finally {
            setIsExecuting(false);
        }
    };

    const handleClose = () => {
        setApiError(null);
        setSelectedFileKey(null);
        setSelectedFilePath("/");
        setFileSearchTerm("");
        setExpandedPaths(new Set(["/"]));
        setOpen(false);
    };

    return (
        <Modal
            onDismiss={handleClose}
            visible={open}
            closeAriaLabel="Close modal"
            size="medium"
            header="Execute Workflow"
        >
            <SpaceBetween direction="vertical" size="l">
                {apiError && (
                    <Alert type="error" header="Error executing workflow">
                        {apiError}
                    </Alert>
                )}
                <FormField label="Select Workflow">
                    <Select
                        onChange={(event) => {
                            const val = event.detail.selectedOption.value;
                            // Decode the string value back to object
                            const item = allItems.find(
                                (i) => `${i.workflowId}::${i.databaseId}` === val
                            );
                            setSelectedWorkflow(
                                item
                                    ? { workflowId: item.workflowId, databaseId: item.databaseId }
                                    : null
                            );
                        }}
                        options={allItems.map((item) => {
                            return {
                                label: `${item.workflowId} (${item.databaseId})`,
                                value: `${item.workflowId}::${item.databaseId}`,
                                description: item.description || undefined,
                            };
                        })}
                        selectedOption={
                            selectedWorkflow
                                ? {
                                      value: `${selectedWorkflow.workflowId}::${selectedWorkflow.databaseId}`,
                                      label: `${selectedWorkflow.workflowId} (${selectedWorkflow.databaseId})`,
                                  }
                                : null
                        }
                        filteringType="auto"
                        selectedAriaLabel="Selected"
                    />
                </FormField>

                <FormField
                    label={
                        <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                            Select File to Process
                            <Popover
                                dismissButton={false}
                                position="top"
                                size="medium"
                                triggerType="custom"
                                content={
                                    <Box padding="s">
                                        Select the root folder{" "}
                                        <strong>/ (Entire {Synonyms.Asset})</strong> to process all
                                        files in the {Synonyms.asset}, or select an individual file
                                        to process only that file.
                                    </Box>
                                }
                            >
                                <span
                                    style={{
                                        cursor: "help",
                                        color: "var(--color-text-link-default, #0972d3)",
                                    }}
                                >
                                    <Icon name="status-info" size="small" />
                                </span>
                            </Popover>
                        </span>
                    }
                    description={
                        selectedFileKey
                            ? `Selected: ${selectedFilePath}`
                            : `Selected: Entire ${Synonyms.Asset} (root folder)`
                    }
                >
                    {loadingFiles && (
                        <Box textAlign="center" padding="l">
                            <SpaceBetween direction="vertical" size="xs">
                                <Spinner size="normal" />
                                <Box color="text-body-secondary" fontSize="body-s">
                                    {`Loading ${Synonyms.asset} files...`}
                                </Box>
                            </SpaceBetween>
                        </Box>
                    )}
                    {!loadingFiles && fileTree && (
                        <SpaceBetween direction="vertical" size="xs">
                            <TextFilter
                                filteringText={fileSearchTerm}
                                filteringPlaceholder="Search files..."
                                onChange={({ detail }) => setFileSearchTerm(detail.filteringText)}
                            />
                            <div
                                style={{
                                    maxHeight: "300px",
                                    overflowY: "auto",
                                    border: "1px solid var(--ifm-toc-border-color, #d0d7de)",
                                    borderRadius: "6px",
                                    padding: "4px 0",
                                }}
                            >
                                <FileTreeItem
                                    node={fileTree}
                                    selectedKey={selectedFileKey}
                                    onSelect={handleFileSelect}
                                    expandedPaths={expandedPaths}
                                    onToggle={handleToggleExpanded}
                                    depth={0}
                                    searchTerm={fileSearchTerm}
                                />
                            </div>
                        </SpaceBetween>
                    )}
                    {!loadingFiles && !fileTree && assetFiles.length === 0 && (
                        <Box textAlign="center" padding="s" color="text-body-secondary">
                            No files found in this {Synonyms.asset}.
                        </Box>
                    )}
                </FormField>

                <Box textAlign="right">
                    <Button
                        variant="primary"
                        onClick={handleExecuteWorkflow}
                        disabled={!selectedWorkflow || isExecuting}
                        loading={isExecuting}
                    >
                        Execute Workflow
                    </Button>
                </Box>
            </SpaceBetween>
        </Modal>
    );
}
