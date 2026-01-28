import { FileManagerState, FileManagerAction, FileTree } from "../types/FileManagerTypes";
import {
    toggleExpanded,
    downloadFile,
    searchFileTree,
    calculateTotalAssetSize,
    addFiles,
    mergeFiles,
} from "./FileManagerUtils";

// Helper function to flatten the file tree into an array for shift-selection
function flattenFileTree(tree: FileTree, result: FileTree[] = []): FileTree[] {
    // Add the current node
    result.push(tree);

    // Recursively add all children if expanded
    if (tree.expanded && tree.subTree && tree.subTree.length > 0) {
        for (const child of tree.subTree) {
            flattenFileTree(child, result);
        }
    }

    return result;
}

// Helper function to collect all expanded folder paths from a tree
function collectExpandedFolders(tree: FileTree, expandedSet: Set<string> = new Set()): Set<string> {
    // If this node is expanded and is a folder, add it to the set
    if (tree.expanded) {
        const isFolder =
            tree.isFolder !== undefined
                ? tree.isFolder
                : tree.subTree.length > 0 || tree.keyPrefix.endsWith("/");

        if (isFolder) {
            expandedSet.add(tree.relativePath);
        }
    }

    // Recursively process all children
    if (tree.subTree && tree.subTree.length > 0) {
        for (const child of tree.subTree) {
            collectExpandedFolders(child, expandedSet);
        }
    }

    return expandedSet;
}

// Helper function to build a path-to-node map for fast lookups
function buildPathMap(
    tree: FileTree,
    map: Map<string, FileTree> = new Map()
): Map<string, FileTree> {
    map.set(tree.relativePath, tree);
    if (tree.subTree && tree.subTree.length > 0) {
        for (const child of tree.subTree) {
            buildPathMap(child, map);
        }
    }
    return map;
}

// Helper function to find an item by path using cached map
function findItemByPath(tree: FileTree, path: string): FileTree | null {
    const pathMap = buildPathMap(tree);
    return pathMap.get(path) || null;
}

// Helper function to apply filters to the tree
function applyFilters(tree: FileTree, showArchived: boolean, showNonIncluded: boolean): FileTree {
    // Create a copy of the tree
    const filteredTree = { ...tree };

    // Filter subTree recursively
    if (filteredTree.subTree && filteredTree.subTree.length > 0) {
        filteredTree.subTree = filteredTree.subTree
            .map((child) => applyFilters(child, showArchived, showNonIncluded))
            .filter((child) => {
                // Apply showArchived filter
                if (!showArchived && child.isArchived) {
                    return false;
                }

                // Apply showNonIncluded filter
                if (showNonIncluded && !child.currentAssetVersionFileVersionMismatch) {
                    return false;
                }

                return true;
            });
    }

    return filteredTree;
}

export function fileManagerReducer(
    state: FileManagerState,
    action: FileManagerAction
): FileManagerState {
    switch (action.type) {
        case "TOGGLE_EXPANDED": {
            const updatedTree = toggleExpanded(state.fileTree, action.payload.relativePath);
            // Update flattened items whenever tree structure changes
            const updatedFlattenedItems = flattenFileTree(updatedTree);

            // Update expandedFolders set
            const newExpandedFolders = collectExpandedFolders(updatedTree);

            return {
                ...state,
                fileTree: updatedTree,
                flattenedItems: updatedFlattenedItems,
                expandedFolders: newExpandedFolders,
            };
        }

        case "SELECT_ITEM": {
            const { item, ctrlKey, shiftKey } = action.payload;

            // Make sure we have flattened items
            const currentFlattenedItems =
                state.flattenedItems.length > 0
                    ? state.flattenedItems
                    : flattenFileTree(state.fileTree);

            // Find the current item's index in the flattened array
            const currentIndex = currentFlattenedItems.findIndex(
                (flatItem) => flatItem.relativePath === item.relativePath
            );

            if (ctrlKey) {
                // Ctrl+click: toggle selection
                const isAlreadySelected = state.selectedItems.some(
                    (selectedItem) => selectedItem.relativePath === item.relativePath
                );

                if (isAlreadySelected) {
                    // Remove from selection
                    const newSelectedItems = state.selectedItems.filter(
                        (selectedItem) => selectedItem.relativePath !== item.relativePath
                    );
                    return {
                        ...state,
                        selectedItems: newSelectedItems,
                        selectedItem:
                            newSelectedItems.length > 0
                                ? newSelectedItems[newSelectedItems.length - 1]
                                : null,
                        multiSelectMode: newSelectedItems.length > 1,
                        lastSelectedIndex: currentIndex,
                        flattenedItems: currentFlattenedItems,
                    };
                } else {
                    // Add to selection
                    const newSelectedItems = [...state.selectedItems, item];
                    return {
                        ...state,
                        selectedItems: newSelectedItems,
                        selectedItem: item,
                        multiSelectMode: newSelectedItems.length > 1,
                        lastSelectedIndex: currentIndex,
                        flattenedItems: currentFlattenedItems,
                    };
                }
            } else if (shiftKey && state.lastSelectedIndex !== -1 && currentIndex !== -1) {
                // Shift+click: select range
                const startIndex = Math.min(state.lastSelectedIndex, currentIndex);
                const endIndex = Math.max(state.lastSelectedIndex, currentIndex);

                // Get all items in the range
                const itemsInRange = currentFlattenedItems.slice(startIndex, endIndex + 1);

                // Combine with existing selection if ctrl is also pressed
                const newSelectedItems = ctrlKey
                    ? [
                          ...state.selectedItems,
                          ...itemsInRange.filter(
                              (rangeItem) =>
                                  !state.selectedItems.some(
                                      (selectedItem) =>
                                          selectedItem.relativePath === rangeItem.relativePath
                                  )
                          ),
                      ]
                    : itemsInRange;

                return {
                    ...state,
                    selectedItems: newSelectedItems,
                    selectedItem: item,
                    multiSelectMode: newSelectedItems.length > 1,
                    lastSelectedIndex: currentIndex,
                    flattenedItems: currentFlattenedItems,
                };
            } else {
                // Regular click: single selection
                return {
                    ...state,
                    selectedItem: item,
                    selectedItems: [item],
                    selectedItemPath: item.relativePath,
                    selectedItemPaths: [item.relativePath],
                    multiSelectMode: false,
                    lastSelectedIndex: currentIndex,
                };
            }
        }

        case "SELECT_MULTIPLE_ITEMS":
            return {
                ...state,
                selectedItems: action.payload.items,
                selectedItem:
                    action.payload.items.length > 0
                        ? action.payload.items[action.payload.items.length - 1]
                        : null,
                multiSelectMode: action.payload.items.length > 1,
            };

        case "CLEAR_SELECTION":
            return {
                ...state,
                selectedItems: [],
                selectedItem: null,
                multiSelectMode: false,
                lastSelectedIndex: -1,
            };

        case "DOWNLOAD_FILE":
            const handleDownloadFile = async () => {
                await downloadFile(state.assetId, state.databaseId, action.payload.key);
            };
            handleDownloadFile();
            return state;

        case "UPLOAD_FILES":
            return state;

        case "VIEW_FILE":
            return state;

        case "FETCH_SUCCESS":
            // Update flattened items whenever tree structure changes
            const newFlattenedItems = flattenFileTree(action.payload);
            // Calculate total asset size
            const totalSize = calculateTotalAssetSize(action.payload);
            return {
                ...state,
                unfilteredFileTree: action.payload,
                fileTree: action.payload,
                loading: false,
                error: null,
                // Reset selections when files are refreshed
                selectedItems: [],
                selectedItem: null,
                multiSelectMode: false,
                lastSelectedIndex: -1,
                flattenedItems: newFlattenedItems,
                totalAssetSize: totalSize,
            };

        case "FETCH_ERROR":
            return {
                ...state,
                loading: false,
                error: action.payload,
            };

        case "SET_ERROR":
            return {
                ...state,
                error: action.payload,
            };

        case "CLEAR_ERROR":
            return {
                ...state,
                error: null,
            };

        case "SET_LOADING":
            return {
                ...state,
                loading: true,
            };

        case "SET_SEARCH_TERM":
            const searchTerm = action.payload.searchTerm;

            if (!searchTerm) {
                return {
                    ...state,
                    searchTerm: "",
                    searchResults: [],
                    isSearching: false,
                    // Reset flattened items to tree view when exiting search
                    flattenedItems: flattenFileTree(state.fileTree),
                };
            }

            // Perform search
            const results = searchFileTree(state.fileTree, searchTerm);

            // When in search mode, use search results as flattened items for shift-selection
            return {
                ...state,
                searchTerm,
                searchResults: results,
                isSearching: true,
                // Use search results as flattened items for shift-selection
                flattenedItems: results,
            };

        case "REFRESH_FILES":
            return {
                ...state,
                refreshTrigger: state.refreshTrigger + 1,
                loading: true,
            };

        case "TOGGLE_SHOW_ARCHIVED":
            return {
                ...state,
                showArchived: !state.showArchived,
                refreshTrigger: state.refreshTrigger + 1,
                loading: true,
            };

        case "TOGGLE_SHOW_NON_INCLUDED":
            // This filter is client-side only, no need to refetch data
            // Re-apply filters to the UNFILTERED tree to get correct results
            const toggledShowNonIncluded = !state.showNonIncluded;
            const reFilteredTree = applyFilters(
                state.unfilteredFileTree,
                state.showArchived,
                toggledShowNonIncluded
            );
            const reFilteredFlattenedItems = flattenFileTree(reFilteredTree);

            return {
                ...state,
                showNonIncluded: toggledShowNonIncluded,
                fileTree: reFilteredTree,
                flattenedItems: reFilteredFlattenedItems,
                // Don't trigger refresh or set loading
            };

        case "MERGE_FILES": {
            // 1. Merge new files into UNFILTERED tree to preserve all data
            // Pass expandedFolders to preserve folder expansion states
            const mergedUnfilteredTree = mergeFiles(
                action.payload.files,
                state.unfilteredFileTree,
                state.expandedFolders
            );

            // 1.5. CRITICAL: Ensure root node name and displayName are preserved
            // This is essential because the initial tree has the asset name set
            if (state.unfilteredFileTree.name && state.unfilteredFileTree.displayName) {
                mergedUnfilteredTree.name = state.unfilteredFileTree.name;
                mergedUnfilteredTree.displayName = state.unfilteredFileTree.displayName;
            }

            // 2. Apply current filters to create the display tree
            const filteredTree = applyFilters(
                mergedUnfilteredTree,
                state.showArchived,
                state.showNonIncluded
            );

            // 3. Update flattened items
            const updatedFlattenedItems = flattenFileTree(filteredTree);

            // 4. Re-find selected items by path to preserve selection
            // Check if this is an on-demand fetch (preserveSelection flag)
            const isOnDemandFetch = (action.payload as any).preserveSelection === true;

            console.log(
                "🔄 MERGE_FILES - On-demand fetch:",
                isOnDemandFetch,
                "Files count:",
                action.payload.files.length
            );

            let newSelectedItem = state.selectedItem;
            let newSelectedItems = state.selectedItems;
            let newSelectedItemPath = state.selectedItemPath;
            let newSelectedItemPaths = state.selectedItemPaths;

            // If we have a selected item path, try to find it in the updated tree
            if (state.selectedItemPath) {
                // For on-demand fetch, look in unfiltered tree first to get the updated data
                const foundItem = isOnDemandFetch
                    ? findItemByPath(mergedUnfilteredTree, state.selectedItemPath) ||
                      findItemByPath(filteredTree, state.selectedItemPath)
                    : findItemByPath(filteredTree, state.selectedItemPath);

                if (foundItem) {
                    // Update with the new data from the tree
                    newSelectedItem = foundItem;
                    // Also update selectedItems array if this is the selected item
                    if (
                        state.selectedItems.length === 1 &&
                        state.selectedItems[0].relativePath === foundItem.relativePath
                    ) {
                        newSelectedItems = [foundItem];
                    }
                    console.log(
                        "✅ Updated selected item with new data:",
                        foundItem.relativePath,
                        "versionId:",
                        foundItem.versionId
                    );
                } else if (!isOnDemandFetch) {
                    // Selection was filtered out (only clear if not on-demand fetch)
                    newSelectedItem = null;
                    newSelectedItemPath = null;
                }
            }

            // If we have selected item paths, try to find them in the updated tree
            if (state.selectedItemPaths.length > 0) {
                const foundItems = state.selectedItemPaths
                    .map((path) => findItemByPath(filteredTree, path))
                    .filter((item) => item !== null) as FileTree[];

                if (foundItems.length > 0) {
                    newSelectedItems = foundItems;
                    newSelectedItemPaths = foundItems.map((item) => item.relativePath);
                } else if (!isOnDemandFetch) {
                    // Clear selection if items not found (only if not on-demand fetch)
                    newSelectedItems = [];
                    newSelectedItemPaths = [];
                }
            }

            // 5. Update loading phase and progress
            const newLoadingPhase = action.payload.loadingPhase || state.loadingPhase;
            const newLoadingProgress = action.payload.loadingProgress || state.loadingProgress;

            // 6. Calculate total asset size
            const totalSize = calculateTotalAssetSize(filteredTree);

            // 7. Check if selection was filtered out
            const selectionWasFiltered = state.selectedItemPath && !newSelectedItem;

            // Determine if we should be loading based on the phase
            // Keep loading true for all phases except 'complete' and 'initial'
            const shouldBeLoading = newLoadingPhase !== "complete" && newLoadingPhase !== "initial";

            return {
                ...state,
                unfilteredFileTree: mergedUnfilteredTree,
                fileTree: filteredTree,
                selectedItem: newSelectedItem,
                selectedItems: newSelectedItems,
                selectedItemPath: newSelectedItemPath,
                selectedItemPaths: newSelectedItemPaths,
                flattenedItems: updatedFlattenedItems,
                loadingPhase: newLoadingPhase,
                loadingProgress: newLoadingProgress,
                totalAssetSize: totalSize,
                paginationTokens: action.payload.paginationTokens || state.paginationTokens,
                // Set loading based on phase - true for all phases except 'complete'
                loading: shouldBeLoading,
                // Set error if selection was filtered out
                error: selectionWasFiltered
                    ? "Selected file was hidden by current filters"
                    : state.error,
            };
        }

        case "SET_LOADING_PHASE":
            return {
                ...state,
                loadingPhase: action.payload.phase,
                loadingProgress: action.payload.progress || state.loadingProgress,
                // Keep loading true until we reach 'complete' phase
                loading: action.payload.phase !== "complete",
            };

        case "INIT_LOADING": {
            // Initialize loading state for streaming
            // CRITICAL: Preserve the root node name and displayName
            const preservedName = state.fileTree.name;
            const preservedDisplayName = state.fileTree.displayName;

            console.log(
                "🔵 INIT_LOADING: Preserving name:",
                preservedName,
                "displayName:",
                preservedDisplayName
            );

            const preservedTree = {
                ...state.fileTree,
                name: preservedName,
                displayName: preservedDisplayName,
            };

            // Capture current expanded folders
            const currentExpandedFolders = collectExpandedFolders(state.fileTree);
            console.log(
                "📂 INIT_LOADING: Captured",
                currentExpandedFolders.size,
                "expanded folders"
            );

            return {
                ...state,
                // Preserve root node name/displayName in both trees
                unfilteredFileTree: preservedTree,
                fileTree: preservedTree,
                loading: true,
                loadingPhase: "basic-loading",
                loadingProgress: { current: 0, total: null },
                paginationTokens: { basic: null, detailed: null },
                // Store current selection paths for preservation
                selectedItemPath: state.selectedItem?.relativePath || null,
                selectedItemPaths: state.selectedItems.map((item) => item.relativePath),
                // Store current expanded folders for preservation
                expandedFolders: currentExpandedFolders,
            };
        }

        case "RESET_TREE": {
            // Reset tree to empty state while preserving loading state
            // Used during refresh to clear old data before loading new data
            const emptyTree = {
                ...action.payload,
                subTree: [],
            };

            return {
                ...state,
                unfilteredFileTree: emptyTree,
                fileTree: emptyTree,
                flattenedItems: [],
                totalAssetSize: 0,
                // Keep loading state active
                loading: true,
                loadingPhase: "basic-loading",
                loadingProgress: { current: 0, total: null },
            };
        }

        case "EXPAND_PATH_TO_ITEM": {
            // Expand all parent folders leading to a specific file path
            const targetPath = action.payload.path;

            // Import the helper function to get parent folder paths
            const { getParentFolderPaths } = require("./FileManagerUtils");
            const parentPaths = getParentFolderPaths(targetPath);

            console.log("📂 EXPAND_PATH_TO_ITEM: Expanding folders for path:", targetPath);
            console.log("📂 Parent paths to expand:", parentPaths);

            // Add all parent paths to expandedFolders
            const newExpandedFolders = new Set(state.expandedFolders);
            parentPaths.forEach((path: string) => newExpandedFolders.add(path));

            // Recursively update tree to expand the folders
            const expandFoldersInTree = (node: FileTree): FileTree => {
                const shouldBeExpanded = newExpandedFolders.has(node.relativePath);

                return {
                    ...node,
                    expanded: shouldBeExpanded,
                    subTree: node.subTree.map((child) => expandFoldersInTree(child)),
                };
            };

            const updatedTree = expandFoldersInTree(state.fileTree);
            const updatedFlattenedItems = flattenFileTree(updatedTree);

            return {
                ...state,
                fileTree: updatedTree,
                expandedFolders: newExpandedFolders,
                flattenedItems: updatedFlattenedItems,
            };
        }

        default:
            return state;
    }
}
