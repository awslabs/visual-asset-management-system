import { createContext, useContext, useEffect, useReducer, useState } from "react";
import { useParams } from "react-router";
import { Alert } from "@cloudscape-design/components";
import { AssetDetailContext, AssetDetailContextType } from "../../context/AssetDetailContext";
import { fetchAssetS3Files } from "../../services/AssetVersionService";
import AssetPreviewModal from "./modals/AssetPreviewModal";
import { DirectoryTree, FileManagerContext } from "./components/FileTreeView";
import { FileDetailsPanel } from "./components/FileDetailsPanel";
import { ResizableSplitter } from "./components/ResizableSplitter";
import {
    FileKey,
    FileManagerState,
    FileManagerContextType,
    EnhancedFileManagerProps,
} from "./types/FileManagerTypes";
import { addFiles, getRootByPath } from "./utils/FileManagerUtils";
import { fileManagerReducer } from "./utils/FileManagerReducer";
import "./EnhancedFileManager.css";

// Main Component
export function EnhancedFileManager({ assetName, assetFiles = [], filePathToNavigate }: EnhancedFileManagerProps) {
    const { databaseId, assetId } = useParams();
    const { state: assetDetailState } = useContext(AssetDetailContext) as AssetDetailContextType;

    const initialState: FileManagerState = {
        fileTree: {
            name: assetName,
            displayName: assetName,
            relativePath: "/",
            keyPrefix: "/",
            level: 0,
            expanded: true,
            subTree: [],
        },
        selectedItem: null,
        selectedItems: [],
        multiSelectMode: false,
        lastSelectedIndex: -1,
        assetId: assetId!,
        databaseId: databaseId!,
        loading: true,
        error: null,
        searchTerm: "",
        searchResults: [],
        isSearching: false,
        refreshTrigger: 0,
        showArchived: false,
        showNonIncluded: false,
        flattenedItems: [],
        totalAssetSize: 0,
    };

    const [state, dispatch] = useReducer(fileManagerReducer, initialState);

    // Initial load of files
    useEffect(() => {
        if (assetFiles && assetFiles.length > 0) {
            // Apply filters based on state
            let filteredFiles = assetFiles;

            // Filter out archived files if showArchived is false
            if (!state.showArchived) {
                filteredFiles = filteredFiles.filter((file) => !file.isArchived);
            }

            // Filter for non-included files if showNonIncluded is true
            if (state.showNonIncluded) {
                filteredFiles = filteredFiles.filter(
                    (file) => file.currentAssetVersionFileVersionMismatch
                );
            }

            const fileTree = addFiles(filteredFiles, initialState.fileTree);
            dispatch({ type: "FETCH_SUCCESS", payload: fileTree });

            // Handle file path navigation if provided
            if (filePathToNavigate) {
                // Find the file in the tree by path
                const targetFile = getRootByPath(fileTree, filePathToNavigate);
                
                if (targetFile) {
                    // Select the file
                    dispatch({ type: "SELECT_ITEM", payload: { item: targetFile } });
                } else {
                    // File not found - show non-blocking error
                    dispatch({
                        type: "SET_ERROR",
                        payload: `File path "${filePathToNavigate}" does not exist in this asset.`
                    });
                    
                    // Clear error after 5 seconds
                    setTimeout(() => {
                        dispatch({ type: "CLEAR_ERROR", payload: null });
                    }, 5000);
                    
                    // Still select root so user can browse
                    dispatch({ type: "SELECT_ITEM", payload: { item: fileTree } });
                }
            } else if (
                // If there's exactly 1 file, select it by default
                assetFiles.length === 1 &&
                !assetFiles[0].isFolder &&
                !assetFiles[0].key.endsWith("/")
            ) {
                // Find the file in the tree
                const singleFile = fileTree.subTree.find(
                    (item) =>
                        item.keyPrefix === assetFiles[0].key &&
                        !item.isFolder &&
                        item.subTree.length === 0
                );

                if (singleFile) {
                    dispatch({ type: "SELECT_ITEM", payload: { item: singleFile } });
                } else {
                    // If we can't find the file directly (might be in a subfolder), select the root
                    dispatch({ type: "SELECT_ITEM", payload: { item: fileTree } });
                }
            } else {
                // Select the root item by default
                dispatch({ type: "SELECT_ITEM", payload: { item: fileTree } });
            }
        } else {
            dispatch({ type: "FETCH_SUCCESS", payload: initialState.fileTree });
        }
    }, [assetFiles, filePathToNavigate]);

    // Handle refreshing files when refreshTrigger changes
    useEffect(() => {
        // Skip the initial render
        if (state.refreshTrigger === 0) return;

        const refreshFiles = async () => {
            try {
                // Fetch the latest files
                const filesResponse = await fetchAssetS3Files({
                    databaseId: state.databaseId,
                    assetId: state.assetId,
                    includeArchived: state.showArchived,
                });

                // Improved error handling for API response
                if (!filesResponse) {
                    console.error("API Error: No response received");
                    dispatch({
                        type: "FETCH_ERROR",
                        payload: "Failed to refresh files: No response received",
                    });
                    return;
                }

                // Check if the response is in the expected format [boolean, data]
                if (!Array.isArray(filesResponse)) {
                    console.error("API Error: Unexpected response format", filesResponse);
                    dispatch({
                        type: "FETCH_ERROR",
                        payload: "Failed to refresh files: Unexpected response format",
                    });
                    return;
                }

                if (filesResponse[0] === false) {
                    const errorMessage = filesResponse[1] || "Failed to fetch files";
                    console.error("API Error:", errorMessage);
                    dispatch({
                        type: "FETCH_ERROR",
                        payload: `Failed to refresh files: ${errorMessage}`,
                    });
                    return;
                }

                const files = filesResponse[1];

                if (!files || !Array.isArray(files)) {
                    console.warn("No valid files array in response:", files);
                    // If no files were returned, just show an empty file tree
                    dispatch({ type: "FETCH_SUCCESS", payload: initialState.fileTree });
                    return;
                }

                // Log the files for debugging
                console.log("Files received from API:", files);

                // Apply non-included filter if enabled
                let filteredFiles = files;
                if (state.showNonIncluded) {
                    filteredFiles = filteredFiles.filter(
                        (file) => file.currentAssetVersionFileVersionMismatch
                    );
                }

                // Create a new file tree with the updated files
                const newFileTree = {
                    ...initialState.fileTree,
                    subTree: [], // Clear existing subtree
                };

                try {
                    // Add files to the tree with improved error handling
                    const updatedFileTree = addFiles(filteredFiles, newFileTree);

                    // Update the state with the new file tree
                    dispatch({ type: "FETCH_SUCCESS", payload: updatedFileTree });

                    // If the previously selected item still exists, select it again
                    if (state.selectedItem) {
                        const selectedPath = state.selectedItem.relativePath;
                        const newSelectedItem = getRootByPath(updatedFileTree, selectedPath);

                        if (newSelectedItem) {
                            dispatch({ type: "SELECT_ITEM", payload: { item: newSelectedItem } });
                        } else {
                            // If the selected item no longer exists, select the root
                            dispatch({ type: "SELECT_ITEM", payload: { item: updatedFileTree } });
                        }
                    }
                } catch (treeError) {
                    console.error("Error constructing file tree:", treeError);
                    dispatch({
                        type: "FETCH_ERROR",
                        payload: "Failed to construct file tree. Please try again.",
                    });
                }
            } catch (error) {
                console.error("Error refreshing files:", error);
                dispatch({
                    type: "FETCH_ERROR",
                    payload: "Failed to refresh files. Please try again.",
                });
            }
        };

        refreshFiles();
    }, [state.refreshTrigger, state.assetId, state.databaseId]);

    // State for the preview modal
    const [showPreviewModal, setShowPreviewModal] = useState(false);

    return (
        <FileManagerContext.Provider value={{ state, dispatch }}>
            {/* Non-blocking error alert */}
            {state.error && (
                <Alert
                    type="warning"
                    dismissible
                    onDismiss={() => dispatch({ type: "CLEAR_ERROR", payload: null })}
                    header="File Navigation"
                >
                    {state.error}
                </Alert>
            )}
            
            <div className="enhanced-file-manager">
                <ResizableSplitter
                    leftPanel={<DirectoryTree />}
                    rightPanel={<FileDetailsPanel />}
                    initialLeftWidth={300}
                    minLeftWidth={250}
                    maxLeftWidth={600}
                />
            </div>

            {/* Asset Preview Modal */}
            <AssetPreviewModal
                visible={showPreviewModal}
                onDismiss={() => setShowPreviewModal(false)}
                assetId={assetId || ""}
                databaseId={databaseId || ""}
                previewKey={
                    assetDetailState?.previewLocation?.Key ||
                    (assetDetailState?.previewLocation as any)?.key
                }
                assetName={assetName}
            />
        </FileManagerContext.Provider>
    );
}

// We don't need to export the context from here since it's already exported from FileTreeView
