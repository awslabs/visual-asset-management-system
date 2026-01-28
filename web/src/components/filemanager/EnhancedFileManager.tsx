import {
    createContext,
    useContext,
    useEffect,
    useReducer,
    useState,
    useRef,
    useCallback,
} from "react";
import { useParams } from "react-router";
import { Alert } from "@cloudscape-design/components";
import { AssetDetailContext, AssetDetailContextType } from "../../context/AssetDetailContext";
import { fetchAssetS3FilesStreaming } from "../../services/APIService";
import { fetchFileInfo } from "../../services/APIService";
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
export function EnhancedFileManager({
    assetName,
    assetFiles = [],
    filePathToNavigate,
}: EnhancedFileManagerProps) {
    const { databaseId, assetId } = useParams();
    const { state: assetDetailState } = useContext(AssetDetailContext) as AssetDetailContextType;

    const initialTree = {
        name: assetName || "Loading...",
        displayName: assetName || "Loading...",
        relativePath: "/",
        keyPrefix: "/",
        level: 0,
        expanded: true,
        subTree: [],
    };

    const initialState: FileManagerState = {
        fileTree: initialTree,
        unfilteredFileTree: initialTree,
        selectedItem: null,
        selectedItems: [],
        selectedItemPath: null,
        selectedItemPaths: [],
        multiSelectMode: false,
        lastSelectedIndex: -1,
        assetId: assetId!,
        databaseId: databaseId!,
        loading: false,
        loadingPhase: "initial",
        loadingProgress: { current: 0, total: null },
        error: null,
        searchTerm: "",
        searchResults: [],
        isSearching: false,
        refreshTrigger: 0,
        showArchived: false,
        showNonIncluded: false,
        flattenedItems: [],
        totalAssetSize: 0,
        paginationTokens: { basic: null, detailed: null },
        expandedFolders: new Set<string>(),
    };

    const [state, dispatch] = useReducer(fileManagerReducer, initialState);

    // Track if we're currently loading to prevent duplicate loads
    const loadingRef = useRef(false);
    const hasInitializedRef = useRef(false);
    // Track if we've already navigated to the filePathToNavigate
    const hasNavigatedRef = useRef(false);

    // Update the tree name when assetName prop changes
    useEffect(() => {
        if (assetName && assetName !== state.fileTree.name) {
            console.log("📝 Updating tree name from", state.fileTree.name, "to", assetName);
            dispatch({
                type: "MERGE_FILES",
                payload: {
                    files: [],
                    loadingPhase: state.loadingPhase,
                    loadingProgress: state.loadingProgress,
                    paginationTokens: state.paginationTokens,
                },
            });
            // Update the tree with the new name
            const updatedTree = {
                ...state.fileTree,
                name: assetName,
                displayName: assetName,
            };
            dispatch({ type: "FETCH_SUCCESS", payload: updatedTree });
        }
    }, [assetName]);

    // Handle filePathToNavigate after basic data loading is complete
    useEffect(() => {
        // Only proceed if:
        // 1. We have a filePathToNavigate prop
        // 2. We haven't already navigated to it
        // 3. Basic loading is complete (basic-complete or complete phase)
        // 4. We're not in legacy mode (assetFiles is empty)
        if (
            filePathToNavigate &&
            !hasNavigatedRef.current &&
            (state.loadingPhase === "basic-complete" || state.loadingPhase === "complete") &&
            (!assetFiles || assetFiles.length === 0)
        ) {
            console.log("🎯 Attempting to navigate to:", filePathToNavigate);

            // Find the file in the tree by path
            const targetFile = getRootByPath(state.fileTree, filePathToNavigate);

            if (targetFile) {
                console.log("✅ Found target file, selecting:", targetFile.relativePath);
                // First, expand all parent folders leading to this file
                dispatch({ type: "EXPAND_PATH_TO_ITEM", payload: { path: filePathToNavigate } });
                // Then select the file
                dispatch({ type: "SELECT_ITEM", payload: { item: targetFile } });
                // Mark as navigated
                hasNavigatedRef.current = true;
            } else {
                console.log("❌ Target file not found:", filePathToNavigate);
                // File not found - show non-blocking error
                dispatch({
                    type: "SET_ERROR",
                    payload: `File path "${filePathToNavigate}" does not exist in this asset.`,
                });

                // Clear error after 5 seconds
                setTimeout(() => {
                    dispatch({ type: "CLEAR_ERROR", payload: null });
                }, 5000);

                // Still select root so user can browse
                dispatch({ type: "SELECT_ITEM", payload: { item: state.fileTree } });
                // Mark as navigated to prevent retrying
                hasNavigatedRef.current = true;
            }
        }
    }, [filePathToNavigate, state.loadingPhase, state.fileTree, assetFiles]);

    // Reset navigation flag when filePathToNavigate changes
    useEffect(() => {
        hasNavigatedRef.current = false;
    }, [filePathToNavigate]);

    // Handle assetId changes - reset and reload data for new asset
    useEffect(() => {
        // Only trigger refresh if we've already initialized (not on first mount)
        // This prevents double-loading on initial render
        if (hasInitializedRef.current && assetId) {
            console.log("🔄 Asset ID changed to:", assetId, "- resetting and reloading...");

            // Reset all tracking flags
            hasInitializedRef.current = false;
            loadingRef.current = false;
            hasNavigatedRef.current = false;

            // Trigger refresh to reload data for new asset
            dispatch({ type: "REFRESH_FILES", payload: null });
        }
    }, [assetId]);

    // Function to load files with streaming pagination
    const loadFilesStreaming = useCallback(
        async (basic: boolean, showArchived: boolean) => {
            if (!databaseId || !assetId) return;

            const phase = basic ? "basic-loading" : "detailed-loading";

            try {
                const stream = fetchAssetS3FilesStreaming({
                    databaseId,
                    assetId,
                    includeArchived: showArchived,
                    basic,
                });

                for await (const page of stream) {
                    if (!page.success) {
                        console.error(`Error in ${phase}:`, page.error);
                        dispatch({
                            type: "SET_ERROR",
                            payload: page.error || "Failed to load files",
                        });
                        break;
                    }

                    // Merge files into tree immediately
                    dispatch({
                        type: "MERGE_FILES",
                        payload: {
                            files: page.items,
                            loadingPhase: phase,
                            loadingProgress: {
                                current: page.pageNumber,
                                total: page.isLastPage ? page.pageNumber : null,
                            },
                            paginationTokens: {
                                basic: basic ? page.nextToken : null,
                                detailed: !basic ? page.nextToken : null,
                            },
                        },
                    });

                    if (page.isLastPage) {
                        break;
                    }
                }

                // Update phase to complete
                const nextPhase = basic ? "basic-complete" : "complete";
                console.log(`📊 Dispatching SET_LOADING_PHASE: ${nextPhase}`);
                dispatch({
                    type: "SET_LOADING_PHASE",
                    payload: {
                        phase: nextPhase,
                    },
                });
            } catch (error) {
                console.error(`Error in ${phase}:`, error);
                dispatch({
                    type: "SET_ERROR",
                    payload: `Failed to load files: ${error}`,
                });
            } finally {
                // CRITICAL: Always reset loadingRef when detailed loading completes
                // This ensures the flag is reset even if there are errors or state changes during tab switching
                if (!basic) {
                    loadingRef.current = false;
                }
            }
        },
        [databaseId, assetId]
    );

    // Initial load of files with streaming
    useEffect(() => {
        // Prevent duplicate loads
        if (loadingRef.current) {
            console.log("⚠️ Skipping load - already loading (loadingRef.current = true)");
            return;
        }
        if (!databaseId || !assetId) return;

        // If assetFiles prop is provided (legacy mode), use it
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
                    // First, expand all parent folders leading to this file
                    dispatch({
                        type: "EXPAND_PATH_TO_ITEM",
                        payload: { path: filePathToNavigate },
                    });
                    // Then select the file
                    dispatch({ type: "SELECT_ITEM", payload: { item: targetFile } });
                } else {
                    // File not found - show non-blocking error
                    dispatch({
                        type: "SET_ERROR",
                        payload: `File path "${filePathToNavigate}" does not exist in this asset.`,
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
            return;
        }

        // New streaming mode
        loadingRef.current = true;

        const loadFiles = async () => {
            // Initialize loading state FIRST to store selection paths
            dispatch({ type: "INIT_LOADING", payload: null });

            // Phase 1: Load basic data
            await loadFilesStreaming(true, state.showArchived);

            // Phase 2: Load detailed data
            await loadFilesStreaming(false, state.showArchived);

            // loadingRef is reset in loadFilesStreaming when detailed phase completes
            hasInitializedRef.current = true;
        };

        // Only run once on mount
        if (!hasInitializedRef.current) {
            loadFiles().catch((error) => {
                console.error("Error in initial load:", error);
                loadingRef.current = false;
                hasInitializedRef.current = false;
            });
        }
    }, [databaseId, assetId]);

    // Handle refreshing files when refreshTrigger changes
    useEffect(() => {
        // Skip the initial render
        if (state.refreshTrigger === 0) {
            return;
        }

        // IMPORTANT: Don't check loadingRef here for refresh
        // The REFRESH_FILES action is triggered by user clicking the refresh button
        // We should always honor that request and reset loadingRef
        loadingRef.current = true;

        const refreshFiles = async () => {
            try {
                // Initialize loading state FIRST
                dispatch({ type: "INIT_LOADING", payload: null });

                // Reset tree to empty state with current assetName (keeps loading active)
                dispatch({
                    type: "RESET_TREE",
                    payload: {
                        name: assetName,
                        displayName: assetName,
                        relativePath: "/",
                        keyPrefix: "/",
                        level: 0,
                        expanded: true,
                    },
                });

                // Phase 1: Load basic data
                await loadFilesStreaming(true, state.showArchived);

                // Phase 2: Load detailed data
                await loadFilesStreaming(false, state.showArchived);

                // loadingRef is reset in loadFilesStreaming when detailed phase completes
            } catch (error) {
                console.error("Error refreshing files:", error);
                dispatch({
                    type: "FETCH_ERROR",
                    payload: "Failed to refresh files. Please try again.",
                });
                // Reset on error
                loadingRef.current = false;
            }
        };

        refreshFiles();
    }, [state.refreshTrigger, state.assetId, state.databaseId, state.showArchived, assetName]);

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
