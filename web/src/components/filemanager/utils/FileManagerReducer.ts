import { FileManagerState, FileManagerAction, FileTree } from "../types/FileManagerTypes";
import { toggleExpanded, downloadFile, searchFileTree } from "./FileManagerUtils";

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

export function fileManagerReducer(state: FileManagerState, action: FileManagerAction): FileManagerState {
    switch (action.type) {
        case "TOGGLE_EXPANDED": {
            const updatedTree = toggleExpanded(state.fileTree, action.payload.relativePath);
            // Update flattened items whenever tree structure changes
            const updatedFlattenedItems = flattenFileTree(updatedTree);
            return {
                ...state,
                fileTree: updatedTree,
                flattenedItems: updatedFlattenedItems
            };
        }
            
        case "SELECT_ITEM": {
            const { item, ctrlKey, shiftKey } = action.payload;
            
            // Make sure we have flattened items
            const currentFlattenedItems = state.flattenedItems.length > 0 
                ? state.flattenedItems 
                : flattenFileTree(state.fileTree);
            
            // Find the current item's index in the flattened array
            const currentIndex = currentFlattenedItems.findIndex(
                flatItem => flatItem.relativePath === item.relativePath
            );
            
            if (ctrlKey) {
                // Ctrl+click: toggle selection
                const isAlreadySelected = state.selectedItems.some(
                    selectedItem => selectedItem.relativePath === item.relativePath
                );
                
                if (isAlreadySelected) {
                    // Remove from selection
                    const newSelectedItems = state.selectedItems.filter(
                        selectedItem => selectedItem.relativePath !== item.relativePath
                    );
                    return {
                        ...state,
                        selectedItems: newSelectedItems,
                        selectedItem: newSelectedItems.length > 0 ? newSelectedItems[newSelectedItems.length - 1] : null,
                        multiSelectMode: newSelectedItems.length > 1,
                        lastSelectedIndex: currentIndex,
                        flattenedItems: currentFlattenedItems
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
                        flattenedItems: currentFlattenedItems
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
                    ? [...state.selectedItems, ...itemsInRange.filter(
                        rangeItem => !state.selectedItems.some(
                            selectedItem => selectedItem.relativePath === rangeItem.relativePath
                        )
                      )]
                    : itemsInRange;
                
                return {
                    ...state,
                    selectedItems: newSelectedItems,
                    selectedItem: item,
                    multiSelectMode: newSelectedItems.length > 1,
                    lastSelectedIndex: currentIndex,
                    flattenedItems: currentFlattenedItems
                };
            } else {
                // Regular click: single selection
                return {
                    ...state,
                    selectedItem: item,
                    selectedItems: [item],
                    multiSelectMode: false,
                    lastSelectedIndex: currentIndex,
                };
            }
        }
            
        case "SELECT_MULTIPLE_ITEMS":
            return {
                ...state,
                selectedItems: action.payload.items,
                selectedItem: action.payload.items.length > 0 ? action.payload.items[action.payload.items.length - 1] : null,
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
            return {
                ...state,
                fileTree: action.payload,
                loading: false,
                error: null,
                // Reset selections when files are refreshed
                selectedItems: [],
                selectedItem: null,
                multiSelectMode: false,
                lastSelectedIndex: -1,
                flattenedItems: newFlattenedItems
            };
            
        case "FETCH_ERROR":
            return {
                ...state,
                loading: false,
                error: action.payload,
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
                    searchTerm: '',
                    searchResults: [],
                    isSearching: false,
                    // Reset flattened items to tree view when exiting search
                    flattenedItems: flattenFileTree(state.fileTree)
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
                flattenedItems: results
            };
            
        case "REFRESH_FILES":
            return {
                ...state,
                refreshTrigger: state.refreshTrigger + 1,
                loading: true
            };
            
        case "TOGGLE_SHOW_ARCHIVED":
            return {
                ...state,
                showArchived: !state.showArchived,
                refreshTrigger: state.refreshTrigger + 1,
                loading: true
            };
            
        case "TOGGLE_SHOW_NON_INCLUDED":
            return {
                ...state,
                showNonIncluded: !state.showNonIncluded,
                refreshTrigger: state.refreshTrigger + 1,
                loading: true
            };
            
        default:
            return state;
    }
}
