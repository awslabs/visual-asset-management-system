import React, { useState, useEffect } from "react";
import {
    Modal,
    Box,
    SpaceBetween,
    Button,
    FormField,
    SegmentedControl,
    Alert,
    ProgressBar,
    Header,
    Container,
} from "@cloudscape-design/components";
import { FileTree } from "../types/FileManagerTypes";
import { FolderTreeView } from "../components/FolderTreeView";
import { AssetSelector, Asset } from "../components/AssetSelector";
import {
    processMultipleFileOperations,
    FileOperationResult,
} from "../../../services/FileOperationsService";
import { addFiles } from "../utils/FileManagerUtils";
import "./MoveFilesModal.css";

export interface MoveFilesModalProps {
    visible: boolean;
    onDismiss: () => void;
    selectedFiles: FileTree[];
    currentAssetId: string;
    databaseId: string;
    fileTreeData: FileTree;
    onSuccess: (operation: "move" | "copy", results: FileOperationResult[]) => void;
}

type OperationMode = "move" | "copy";
type CopyTarget = "same" | "different";

export function MoveFilesModal({
    visible,
    onDismiss,
    selectedFiles,
    currentAssetId,
    databaseId,
    fileTreeData,
    onSuccess,
}: MoveFilesModalProps) {
    // State management
    const [operationMode, setOperationMode] = useState<OperationMode>("move");
    const [copyTarget, setCopyTarget] = useState<CopyTarget>("same");
    const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
    const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set(["/"]));
    const [selectedAsset, setSelectedAsset] = useState<Asset | null>(null);
    const [targetAssetFiles, setTargetAssetFiles] = useState<any[]>([]);
    const [targetFileTree, setTargetFileTree] = useState<FileTree | null>(null);
    const [isProcessing, setIsProcessing] = useState(false);
    const [processingProgress, setProcessingProgress] = useState(0);
    const [operationResults, setOperationResults] = useState<FileOperationResult[]>([]);
    const [showResults, setShowResults] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Reset state when modal opens/closes
    useEffect(() => {
        if (visible) {
            setOperationMode("move");
            setCopyTarget("same");
            setSelectedFolder(null);
            setExpandedFolders(new Set(["/"]));
            setSelectedAsset(null);
            setTargetAssetFiles([]);
            setTargetFileTree(null);
            setIsProcessing(false);
            setProcessingProgress(0);
            setOperationResults([]);
            setShowResults(false);
            setError(null);
        }
    }, [visible]);

    // Handle folder selection
    const handleFolderSelect = (folderPath: string) => {
        setSelectedFolder(folderPath);
        setError(null);
    };

    // Handle folder expansion
    const handleToggleExpanded = (folderPath: string) => {
        const newExpanded = new Set(expandedFolders);
        if (newExpanded.has(folderPath)) {
            newExpanded.delete(folderPath);
        } else {
            newExpanded.add(folderPath);
        }
        setExpandedFolders(newExpanded);
    };

    // Handle asset selection for cross-asset copy
    const handleAssetSelect = (asset: Asset | null) => {
        setSelectedAsset(asset);
        setSelectedFolder(null);
        setTargetFileTree(null);
        setError(null);
    };

    // Handle asset files load for cross-asset copy
    const handleAssetFilesLoad = (assetId: string, files: any[]) => {
        setTargetAssetFiles(files);

        // Build file tree for the target asset
        if (files.length > 0) {
            const initialTree: FileTree = {
                name: selectedAsset?.assetName || assetId,
                displayName: selectedAsset?.assetName || assetId,
                relativePath: "/",
                keyPrefix: "/",
                level: 0,
                expanded: true,
                subTree: [],
            };

            const tree = addFiles(files, initialTree);
            setTargetFileTree(tree);
            setExpandedFolders(new Set(["/"]));
        }
    };

    // Get current tree data based on mode and target
    const getCurrentTreeData = (): FileTree => {
        if (operationMode === "copy" && copyTarget === "different" && targetFileTree) {
            return targetFileTree;
        }
        return fileTreeData;
    };

    // Validate selection
    const isValidSelection = (): boolean => {
        if (!selectedFolder) return false;

        if (operationMode === "copy" && copyTarget === "different") {
            return selectedAsset !== null && selectedFolder !== null;
        }

        return selectedFolder !== null;
    };

    // Get button text
    const getButtonText = (): string => {
        const fileCount = selectedFiles.length;
        const fileText = fileCount === 1 ? "File" : "Files";
        return operationMode === "move"
            ? `Move ${fileCount} ${fileText}`
            : `Copy ${fileCount} ${fileText}`;
    };

    // Handle operation execution
    const handleExecuteOperation = async () => {
        if (!isValidSelection()) {
            setError("Please select a destination folder");
            return;
        }

        setIsProcessing(true);
        setProcessingProgress(0);
        setError(null);

        try {
            const filePaths = selectedFiles.map((file) => file.relativePath);
            const destinationAssetId =
                operationMode === "copy" && copyTarget === "different"
                    ? selectedAsset?.assetId
                    : undefined;

            const results = await processMultipleFileOperations(
                databaseId,
                currentAssetId,
                filePaths,
                selectedFolder!,
                operationMode,
                destinationAssetId
            );

            // Check if all operations were successful
            const allSuccessful = results.every((result) => result.success);
            
            // Always update the results
            setOperationResults(results);
            
            if (allSuccessful) {
                // Call onSuccess to refresh the tree view
                onSuccess(operationMode, results);
                
                // Close the modal automatically on complete success
                // Small delay to ensure parent state updates complete
                setTimeout(() => {
                    onDismiss();
                }, 100);
            } else {
                // Only show results UI if there were failures
                setShowResults(true);
            }
        } catch (error: any) {
            setError(error.message || `Failed to ${operationMode} files`);
        } finally {
            setIsProcessing(false);
            setProcessingProgress(100);
        }
    };

    // Handle modal dismiss
    const handleDismiss = () => {
        if (!isProcessing) {
            onDismiss();
        }
    };

    // Render operation results
    const renderResults = () => {
        if (!showResults) return null;

        const successCount = operationResults.filter((r) => r.success).length;
        const failureCount = operationResults.filter((r) => !r.success).length;

        return (
            <Container header={<Header variant="h3">Operation Results</Header>}>
                <SpaceBetween direction="vertical" size="s">
                    <Box>
                        <strong>Summary:</strong> {successCount} successful, {failureCount} failed
                    </Box>

                    {failureCount > 0 && (
                        <Alert type="error" header="Some operations failed">
                            <ul style={{ margin: 0, paddingLeft: "20px" }}>
                                {operationResults
                                    .filter((r) => !r.success)
                                    .map((result, index) => (
                                        <li key={index}>
                                            <strong>{result.filePath}:</strong> {result.error}
                                        </li>
                                    ))}
                            </ul>
                        </Alert>
                    )}

                    {successCount > 0 && (
                        <Alert
                            type="success"
                            header={`Successfully ${operationMode}d ${successCount} file(s)`}
                        >
                            Files have been {operationMode}d to: {selectedFolder}
                            {operationMode === "copy" &&
                                copyTarget === "different" &&
                                selectedAsset && <> in asset: {selectedAsset.assetName}</>}
                        </Alert>
                    )}
                </SpaceBetween>
            </Container>
        );
    };

    return (
        <Modal
            visible={visible}
            onDismiss={handleDismiss}
            size="large"
            header={`${operationMode === "move" ? "Move" : "Copy"} Files`}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={handleDismiss} disabled={isProcessing}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={handleExecuteOperation}
                            disabled={!isValidSelection() || isProcessing}
                            loading={isProcessing}
                        >
                            {getButtonText()}
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween direction="vertical" size="l">
                {/* Selected Files Summary */}
                <Container header={<Header variant="h3">Selected Files</Header>}>
                    <Box>
                        {selectedFiles.length === 1 ? (
                            <span>
                                Moving/copying: <strong>{selectedFiles[0].displayName}</strong>
                            </span>
                        ) : (
                            <span>
                                Moving/copying <strong>{selectedFiles.length} files</strong>
                            </span>
                        )}
                    </Box>
                </Container>

                {/* Operation Mode Toggle */}
                <FormField label="Operation">
                    <SegmentedControl
                        selectedId={operationMode}
                        onChange={({ detail }) =>
                            setOperationMode(detail.selectedId as OperationMode)
                        }
                        options={[
                            { text: "Move", id: "move" },
                            { text: "Copy", id: "copy" },
                        ]}
                    />
                </FormField>

                {/* Move Mode Note */}
                {operationMode === "move" && (
                    <Alert type="info" statusIconAriaLabel="Info">
                        This will archive the original file and a copy the latest version to the new
                        location.
                    </Alert>
                )}

                {/* Copy Target Selection */}
                {operationMode === "copy" && (
                    <FormField label="Copy to">
                        <SegmentedControl
                            selectedId={copyTarget}
                            onChange={({ detail }) =>
                                setCopyTarget(detail.selectedId as CopyTarget)
                            }
                            options={[
                                { text: "Same Asset", id: "same" },
                                { text: "Different Asset", id: "different" },
                            ]}
                        />
                    </FormField>
                )}

                {/* Asset Selector for Different Asset Copy */}
                {operationMode === "copy" && copyTarget === "different" && (
                    <AssetSelector
                        currentAssetId={currentAssetId}
                        currentDatabaseId={databaseId}
                        selectedAsset={selectedAsset}
                        onAssetSelect={handleAssetSelect}
                        onAssetFilesLoad={handleAssetFilesLoad}
                    />
                )}

                {/* Folder Selection */}
                {(operationMode === "move" ||
                    (operationMode === "copy" && copyTarget === "same") ||
                    (operationMode === "copy" && copyTarget === "different" && selectedAsset)) && (
                    <FormField
                        label="Destination Folder"
                        description="Select a folder to move/copy the files to"
                        errorText={error}
                    >
                        <FolderTreeView
                            treeData={getCurrentTreeData()}
                            selectedFolder={selectedFolder}
                            onFolderSelect={handleFolderSelect}
                            expandedFolders={expandedFolders}
                            onToggleExpanded={handleToggleExpanded}
                        />
                    </FormField>
                )}

                {/* Processing Progress */}
                {isProcessing && (
                    <Box>
                        <ProgressBar
                            value={processingProgress}
                            label="Processing files..."
                            description={`${operationMode === "move" ? "Moving" : "Copying"} ${
                                selectedFiles.length
                            } file(s)`}
                        />
                    </Box>
                )}

                {/* Operation Results */}
                {renderResults()}
            </SpaceBetween>
        </Modal>
    );
}
