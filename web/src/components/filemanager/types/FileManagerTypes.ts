// Types and interfaces for the File Manager components

export interface FileKey {
    fileName: string;
    key: string;
    relativePath: string;
    isFolder: boolean;
    size?: number;
    dateCreatedCurrentVersion: string;
    versionId: string;
    storageClass?: string;
    isArchived: boolean;
    currentAssetVersionFileVersionMismatch?: boolean;
    primaryType?: string | null;
    previewFile?: string;
}

export interface FileTree {
    name: string;
    displayName: string;
    relativePath: string;
    keyPrefix: string;
    level: number;
    expanded: boolean;
    subTree: FileTree[];
    isFolder?: boolean;
    size?: number;
    dateCreatedCurrentVersion?: string;
    versionId?: string;
    isArchived?: boolean;
    currentAssetVersionFileVersionMismatch?: boolean;
    primaryType?: string | null;
    previewFile?: string;
}

export type LoadingPhase =
    | "initial"
    | "basic-loading"
    | "basic-complete"
    | "detailed-loading"
    | "complete";

export interface FileManagerStateValues {
    fileTree: FileTree; // Displayed tree (may be filtered)
    unfilteredFileTree: FileTree; // Unfiltered tree (always contains all data)
    selectedItem: FileTree | null;
    selectedItems: FileTree[]; // Array of selected items for multi-selection
    selectedItemPath: string | null; // Path of selected item for preservation during updates
    selectedItemPaths: string[]; // Paths of selected items for preservation during updates
    multiSelectMode: boolean; // Track if we're in multi-select mode
    lastSelectedIndex: number; // For shift-click range selection
    assetId: string;
    databaseId: string;
    loading: boolean;
    loadingPhase: LoadingPhase; // Track which phase of loading we're in
    loadingProgress: { current: number; total: number | null }; // Track loading progress
    error: string | null;
    searchTerm: string;
    searchResults: FileTree[];
    isSearching: boolean;
    refreshTrigger: number; // Used to trigger a refresh of the file list
    showArchived: boolean; // Toggle to show/hide archived files
    showNonIncluded: boolean; // Toggle to show/hide non-included files
    flattenedItems: FileTree[]; // Flattened array of all items for shift-selection
    totalAssetSize: number; // Total size of all files in the asset (excluding folders)
    paginationTokens: { basic: string | null; detailed: string | null }; // Track pagination tokens
    expandedFolders: Set<string>; // Set of relativePath strings for expanded folders
}

export type FileManagerState = FileManagerStateValues;

export interface FileManagerAction {
    type: string;
    payload: any;
}

export type FileManagerContextType = {
    state: FileManagerState;
    dispatch: React.Dispatch<FileManagerAction>;
};

// Props interfaces for components
export interface TreeItemProps {
    item: FileTree;
}

export interface SearchResultsProps {
    // No additional props needed - uses context
}

export interface DirectoryTreeProps {
    // No additional props needed - uses context
}

export interface FileInfoPanelProps {
    // No additional props needed - uses context
}

export interface CreateFolderModalProps {
    visible: boolean;
    onDismiss: () => void;
    onSubmit: (folderName: string) => void;
    parentFolder: string;
    isLoading: boolean;
}

export interface EnhancedFileManagerProps {
    assetName: string;
    assetFiles?: FileKey[];
    filePathToNavigate?: string; // Optional file path to navigate to and select
}

export interface SetPrimaryTypeModalProps {
    visible: boolean;
    onDismiss: () => void;
    selectedFiles: FileTree[];
    databaseId: string;
    assetId: string;
    onSuccess: () => void;
}
