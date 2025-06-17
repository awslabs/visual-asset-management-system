import { FileManagerState, FileManagerAction } from "../types/FileManagerTypes";
import { toggleExpanded, downloadFile, searchFileTree } from "./FileManagerUtils";

export function fileManagerReducer(state: FileManagerState, action: FileManagerAction): FileManagerState {
    switch (action.type) {
        case "TOGGLE_EXPANDED":
            return {
                ...state,
                fileTree: toggleExpanded(state.fileTree, action.payload.relativePath),
            };
            
        case "SELECT_ITEM":
            const { item, ctrlKey, shiftKey } = action.payload;
            
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
                    };
                } else {
                    // Add to selection
                    const newSelectedItems = [...state.selectedItems, item];
                    return {
                        ...state,
                        selectedItems: newSelectedItems,
                        selectedItem: item,
                        multiSelectMode: newSelectedItems.length > 1,
                        lastSelectedIndex: action.payload.itemIndex || -1,
                    };
                }
            } else if (shiftKey && state.lastSelectedIndex !== -1) {
                // Shift+click: select range
                // This is a simplified implementation - in a real app you'd need to track item indices
                // For now, just add the item to selection
                const isAlreadySelected = state.selectedItems.some(
                    selectedItem => selectedItem.relativePath === item.relativePath
                );
                
                if (!isAlreadySelected) {
                    const newSelectedItems = [...state.selectedItems, item];
                    return {
                        ...state,
                        selectedItems: newSelectedItems,
                        selectedItem: item,
                        multiSelectMode: newSelectedItems.length > 1,
                    };
                }
                return state;
            } else {
                // Regular click: single selection
                return {
                    ...state,
                    selectedItem: item,
                    selectedItems: [item],
                    multiSelectMode: false,
                    lastSelectedIndex: action.payload.itemIndex || -1,
                };
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
                    isSearching: false
                };
            }
            
            // Perform search
            const results = searchFileTree(state.fileTree, searchTerm);
            
            return {
                ...state,
                searchTerm,
                searchResults: results,
                isSearching: true
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
            
        default:
            return state;
    }
}
