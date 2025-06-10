import {
    Box,
    Button,
    Container,
    Header,
    Icon,
    SpaceBetween,
    Spinner,
    Link,
    TextFilter,
    Modal,
    FormField,
    Input,
    Form,
} from "@cloudscape-design/components";
import { createContext, Dispatch, useContext, useEffect, useReducer, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { AssetDetailContext, AssetDetailContextType } from "../../context/AssetDetailContext";
import { downloadAsset, createFolder, fetchAssetFiles } from "../../services/APIService";
import "./EnhancedFileManager.css";

// Types and interfaces
export interface FileKey {
    fileName: string;
    key: string;
    relativePath: string;
    isFolder?: boolean;
    size?: number;
    dateCreatedCurrentVersion?: string;
    versionId?: string;
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
}

export interface FileManagerStateValues {
    fileTree: FileTree;
    selectedItem: FileTree | null;
    assetId: string;
    databaseId: string;
    loading: boolean;
    error: string | null;
    searchTerm: string;
    searchResults: FileTree[];
    isSearching: boolean;
    refreshTrigger: number; // Used to trigger a refresh of the file list
}

type FileManagerState = FileManagerStateValues;

export interface FileManagerAction {
    type: string;
    payload: any;
}

type FileManagerContextType = {
    state: FileManagerState;
    dispatch: Dispatch<FileManagerAction>;
};

const FileManagerContext = createContext<FileManagerContextType | undefined>(undefined);

// Helper functions
function getRootByPath(root: FileTree | null, path: string): FileTree | null {
    if (!root) {
        return null;
    }
    if (root.relativePath === path) {
        return root;
    } else {
        for (let subtree of root.subTree) {
            if (subtree.relativePath === path) {
                return subtree;
            } else {
                if (subtree.subTree.length > 0) {
                    const subTreeReturn = getRootByPath(subtree, path);
                    if (subTreeReturn) {
                        return subTreeReturn;
                    }
                }
            }
        }
    }
    return null;
}

// Helper function to check if a folder has any files beneath it (recursively)
function hasFolderContent(folder: FileTree): boolean {
    // Check if any direct children are files
    const hasFiles = folder.subTree.some(item => {
        const isFile = item.isFolder === false || 
            (item.isFolder === undefined && item.subTree.length === 0 && !item.keyPrefix.endsWith('/'));
        return isFile;
    });
    
    if (hasFiles) {
        return true;
    }
    
    // Recursively check subfolders
    return folder.subTree.some(item => {
        const isSubfolder = item.isFolder === true || 
            (item.isFolder === undefined && (item.subTree.length > 0 || item.keyPrefix.endsWith('/')));
        
        return isSubfolder && hasFolderContent(item);
    });
}

function addDirectories(root: FileTree, directories: string): FileTree {
    const parts = directories.split("/");
    let currentRoot = root;
    for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        let subTree = currentRoot.subTree.find((subTree) => subTree.name === part);
        if (subTree == null) {
            subTree = {
                name: part,
                displayName: part,
                relativePath: parts.slice(0, i + 1).join("/") + "/",
                keyPrefix: part,
                level: currentRoot.level + 1,
                expanded: false,
                subTree: [],
            };
            currentRoot.subTree.push(subTree);
        }
        currentRoot = subTree;
    }
    return currentRoot;
}

function addFiles(fileKeys: FileKey[], root: FileTree) {
    // Simple approach that works reliably
    const getParentDirectory = (path: string) => {
        const parentPath = path.split("/").slice(0, -1).join("/");
        return parentPath === "" ? "" : parentPath;
    };
    
    // Filter out problematic entries
    const filteredFileKeys = fileKeys.filter(fileKey => {
        // Skip entries that are just the asset folder (empty fileName and relativePath)
        if (fileKey.fileName === "" && fileKey.relativePath === "") {
            return false;
        }
        
        // Skip entries with double slashes in the key (these are artifacts)
        if (fileKey.key.includes('//')) {
            return false;
        }
        
        return true;
    });
    
    // Track all created paths to avoid duplicates
    const createdPaths = new Set<string>();
    
    // First, separate folders and files
    const folders: FileKey[] = [];
    const files: FileKey[] = [];
    
    filteredFileKeys.forEach(fileKey => {
        if (fileKey.isFolder || fileKey.key.endsWith('/')) {
            folders.push(fileKey);
        } else {
            files.push(fileKey);
        }
    });
    
    // Process folders first
    for (const folderKey of folders) {
        const relativePath = folderKey.relativePath;
        
        // Skip if we've already created this path
        if (createdPaths.has(relativePath)) {
            continue;
        }
        
        // For root level folders
        if (!relativePath.includes('/') || relativePath === relativePath.split('/')[0] + '/') {
            // Create node for root level folder
            root.subTree.push({
                name: folderKey.fileName || relativePath.replace('/', ''),
                displayName: folderKey.fileName || relativePath.replace('/', ''),
                relativePath: relativePath,
                keyPrefix: folderKey.key,
                level: 1,
                expanded: false,
                subTree: [],
                isFolder: true,
                size: folderKey.size,
                dateCreatedCurrentVersion: folderKey.dateCreatedCurrentVersion,
                versionId: folderKey.versionId
            });
        } else {
            // For nested folders
            const parentDir = getParentDirectory(relativePath);
            let parentNode = getRootByPath(root, parentDir + "/");
            
            if (!parentNode) {
                // Create parent directories if they don't exist
                parentNode = addDirectories(root, parentDir);
            }
            
            // Check if this folder already exists in the parent's subtree
            const existingFolder = parentNode.subTree.find(item => item.relativePath === relativePath);
            if (!existingFolder) {
                // Add the folder to its parent
                parentNode.subTree.push({
                    name: folderKey.fileName || relativePath.split('/').pop() || '',
                    displayName: folderKey.fileName || relativePath.split('/').pop() || '',
                    relativePath: relativePath,
                    keyPrefix: folderKey.key,
                    level: parentNode.level + 1,
                    expanded: false,
                    subTree: [],
                    isFolder: true,
                    size: folderKey.size,
                    dateCreatedCurrentVersion: folderKey.dateCreatedCurrentVersion,
                    versionId: folderKey.versionId
                });
            }
        }
        
        // Mark this path as created
        createdPaths.add(relativePath);
    }
    
    // Then process files
    for (const fileKey of files) {
        const relativePath = fileKey.relativePath;
        
        // Skip if we've already created this path (shouldn't happen for files, but just in case)
        if (createdPaths.has(relativePath)) {
            continue;
        }
        
        // For root level files
        if (!relativePath.includes('/')) {
            // Create node for root level file
            root.subTree.push({
                name: fileKey.fileName,
                displayName: fileKey.fileName,
                relativePath: relativePath,
                keyPrefix: fileKey.key,
                level: 1,
                expanded: false,
                subTree: [],
                isFolder: false,
                size: fileKey.size,
                dateCreatedCurrentVersion: fileKey.dateCreatedCurrentVersion,
                versionId: fileKey.versionId
            });
        } else {
            // For nested files, find or create parent directories
            const parentDir = getParentDirectory(relativePath);
            let parentNode = getRootByPath(root, parentDir + "/");
            
            if (!parentNode) {
                // Create parent directories if they don't exist
                parentNode = addDirectories(root, parentDir);
                
                // Mark parent directories as created
                let currentPath = parentDir;
                while (currentPath) {
                    createdPaths.add(currentPath + '/');
                    currentPath = getParentDirectory(currentPath);
                    if (currentPath === '') break;
                }
            }
            
            // Add the file to its parent
            parentNode.subTree.push({
                name: fileKey.fileName,
                displayName: fileKey.fileName,
                relativePath: relativePath,
                keyPrefix: fileKey.key,
                level: parentNode.level + 1,
                expanded: false,
                subTree: [],
                isFolder: false,
                size: fileKey.size,
                dateCreatedCurrentVersion: fileKey.dateCreatedCurrentVersion,
                versionId: fileKey.versionId
            });
        }
        
        // Mark this path as created
        createdPaths.add(relativePath);
    }
    
    return root;
}

function toggleExpanded(fileTree: FileTree, relativePath: string): FileTree {
    if (fileTree.relativePath === relativePath) {
        return {
            ...fileTree,
            expanded: !fileTree.expanded,
        };
    }
    return {
        ...fileTree,
        subTree: fileTree.subTree.map((subTree) => toggleExpanded(subTree, relativePath)),
    };
}

async function downloadFile(assetId: string, databaseId: string, keyPrefix: string) {
    try {
        const response = await downloadAsset({
            assetId: assetId,
            databaseId: databaseId,
            key: keyPrefix,
            version: "",
        });

        if (response !== false && Array.isArray(response)) {
            if (response[0] === false) {
                console.error("API Error with downloading file");
                return false;
            } else {
                const link = document.createElement("a");
                link.href = response[1];
                link.click();
                return true;
            }
        }
        return false;
    } catch (error) {
        console.error(error);
        return false;
    }
}

// Reducer function
// Search function to find matching files/folders
function searchFileTree(root: FileTree, searchTerm: string, results: FileTree[] = []): FileTree[] {
    // Check if the current node matches the search term
    if (root.name.toLowerCase().includes(searchTerm.toLowerCase())) {
        results.push(root);
    }
    
    // Recursively search through children
    for (const child of root.subTree) {
        searchFileTree(child, searchTerm, results);
    }
    
    return results;
}

// Create Folder Modal Component
interface CreateFolderModalProps {
    visible: boolean;
    onDismiss: () => void;
    onSubmit: (folderName: string) => void;
    parentFolder: string;
    isLoading: boolean;
}

function CreateFolderModal({ 
    visible, 
    onDismiss, 
    onSubmit, 
    parentFolder,
    isLoading
}: CreateFolderModalProps) {
    const [folderName, setFolderName] = useState("");
    const [error, setError] = useState("");
    
    // Reset state when modal opens/closes
    useEffect(() => {
        if (visible) {
            setFolderName("");
            setError("");
        }
    }, [visible]);
    
    // Validate folder name
    const validateFolderName = (name: string): boolean => {
        if (!name || name.trim() === "") {
            setError("Folder name cannot be empty");
            return false;
        }
        
        // Check for invalid characters (/, \, :, *, ?, ", <, >, |)
        const invalidChars = /[/\\:*?"<>|]/;
        if (invalidChars.test(name)) {
            setError("Folder name contains invalid characters (/, \\, :, *, ?, \", <, >, |)");
            return false;
        }
        
        setError("");
        return true;
    };
    
    const handleSubmit = () => {
        if (validateFolderName(folderName)) {
            onSubmit(folderName);
        }
    };
    
    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={`Create sub-folder in ${parentFolder || 'root'}`}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={onDismiss}>
                            Cancel
                        </Button>
                        <Button 
                            variant="primary" 
                            onClick={handleSubmit}
                            loading={isLoading}
                        >
                            Create
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <Form errorText={error}>
                <FormField
                    label="Folder name"
                    description="Enter a name for the new folder"
                >
                    <Input
                        value={folderName}
                        onChange={({ detail }) => setFolderName(detail.value)}
                        placeholder="New folder"
                        autoFocus
                    />
                </FormField>
            </Form>
        </Modal>
    );
}

function fileManagerReducer(state: FileManagerState, action: FileManagerAction): FileManagerState {
    switch (action.type) {
        case "TOGGLE_EXPANDED":
            return {
                ...state,
                fileTree: toggleExpanded(state.fileTree, action.payload.relativePath),
            };
            
        case "SELECT_ITEM":
            return {
                ...state,
                selectedItem: action.payload.item,
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
            
        default:
            return state;
    }
}

// Tree Item Component
function TreeItem({ item }: { item: FileTree }) {
    const { state, dispatch } = useContext(FileManagerContext)!;
    // Check if it's a folder by using isFolder property, or by having subTree items or if the keyPrefix ends with '/'
    const isFolder = item.isFolder !== undefined ? item.isFolder : (item.subTree.length > 0 || item.keyPrefix.endsWith('/'));
    const isSelected = state.selectedItem?.relativePath === item.relativePath;
    
    return (
        <div className="tree-item">
            <div 
                className={`tree-item-content ${isSelected ? 'selected' : ''}`}
                style={{ paddingLeft: `${item.level * 16}px` }}
                onClick={() => dispatch({ type: "SELECT_ITEM", payload: { item } })}
            >
                {isFolder && (
                    <span 
                        className="tree-item-caret"
                        onClick={(e) => {
                            e.stopPropagation();
                            dispatch({
                                type: "TOGGLE_EXPANDED",
                                payload: { relativePath: item.relativePath },
                            });
                        }}
                    >
                        {item.expanded ? (
                            <Icon name="caret-down-filled" />
                        ) : (
                            <Icon name="caret-right-filled" />
                        )}
                    </span>
                )}
                
                <span className="tree-item-icon">
                    {isFolder ? (
                        item.expanded ? (
                            <Icon name="folder-open" />
                        ) : (
                            <Icon name="folder" />
                        )
                    ) : (
                        <Icon name="file" />
                    )}
                </span>
                
                <span className="tree-item-name">
                    {item.displayName}
                </span>
            </div>
            
            {isFolder && item.expanded && (
                <div className="tree-item-children">
                    {item.subTree.map((child) => (
                        <TreeItem key={child.keyPrefix} item={child} />
                    ))}
                </div>
            )}
        </div>
    );
}

// Search Results Component
function SearchResults() {
    const { state, dispatch } = useContext(FileManagerContext)!;
    
    if (state.searchResults.length === 0) {
        return (
            <Box textAlign="center" padding="m">
                <div>No files or folders match your search</div>
            </Box>
        );
    }
    
    return (
        <div className="search-results">
            {state.searchResults.map((item) => {
                const isFolder = item.isFolder !== undefined ? item.isFolder : (item.subTree.length > 0 || item.keyPrefix.endsWith('/'));
                
                return (
                    <div 
                        key={item.keyPrefix}
                        className={`search-result-item ${state.selectedItem?.relativePath === item.relativePath ? 'selected' : ''}`}
                        onClick={() => dispatch({ type: "SELECT_ITEM", payload: { item } })}
                    >
                        <span className="search-result-icon">
                            {isFolder ? (
                                <Icon name="folder" />
                            ) : (
                                <Icon name="file" />
                            )}
                        </span>
                        <span className="search-result-name">
                            {item.displayName}
                        </span>
                        <span className="search-result-path">
                            {item.relativePath}
                        </span>
                    </div>
                );
            })}
        </div>
    );
}

// Directory Tree Component
function DirectoryTree() {
    const { state, dispatch } = useContext(FileManagerContext)!;
    
    if (state.loading) {
        return (
            <Box textAlign="center" padding="m">
                <Spinner size="normal" />
                <div>Loading files...</div>
            </Box>
        );
    }
    
    if (state.error) {
        return (
            <Box textAlign="center" padding="m" color="text-status-error">
                <div>Error loading files: {state.error}</div>
            </Box>
        );
    }
    
    return (
        <div className="directory-tree-container">
            <div className="search-box">
                <TextFilter
                    filteringText={state.searchTerm}
                    filteringPlaceholder="Search files and folders"
                    filteringAriaLabel="Search files and folders"
                    onChange={({ detail }) => 
                        dispatch({ 
                            type: "SET_SEARCH_TERM", 
                            payload: { searchTerm: detail.filteringText } 
                        })
                    }
                    countText={state.isSearching ? `${state.searchResults.length} matches` : undefined}
                />
            </div>
            
            {state.isSearching ? (
                <SearchResults />
            ) : (
                <div className="directory-tree">
                    <TreeItem item={state.fileTree} />
                </div>
            )}
        </div>
    );
}

// Helper functions for formatting
function formatFileSize(size?: number): string {
    if (size === undefined) return "Unknown";
    if (size === 0) return "0 B";
    
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(size) / Math.log(1024));
    return `${(size / Math.pow(1024, i)).toFixed(2)} ${units[i]}`;
}

function formatDate(dateString?: string): string {
    if (!dateString) return "Unknown";
    try {
        const date = new Date(dateString);
        return date.toLocaleString();
    } catch (e) {
        return dateString;
    }
}

// File Info Panel Component
function FileInfoPanel() {
    const { state, dispatch } = useContext(FileManagerContext)!;
    const navigate = useNavigate();
    const { databaseId, assetId } = useParams();
    const { state: assetDetailState } = useContext(AssetDetailContext) as AssetDetailContextType;
    const selectedItem = state.selectedItem;
    const isFolder = selectedItem?.isFolder !== undefined 
        ? selectedItem.isFolder 
        : (selectedItem?.subTree.length! > 0 || selectedItem?.keyPrefix.endsWith('/'));
    
    // State for create folder modal
    const [createFolderModalVisible, setCreateFolderModalVisible] = useState(false);
    const [isCreatingFolder, setIsCreatingFolder] = useState(false);
    
    if (!selectedItem) {
        return (
            <Box textAlign="center" padding="xl">
                <div>Select a file or folder to view details</div>
            </Box>
        );
    }
    
    const handleDownload = () => {
        dispatch({
            type: "DOWNLOAD_FILE",
            payload: { key: selectedItem.keyPrefix },
        });
    };
    
    const handleUpload = () => {
        navigate(`/databases/${databaseId}/assets/${assetId}/uploads`, {
            state: {
                fileTree: selectedItem,
                isNewFiles: true,
                assetDetailState: assetDetailState,
            },
        });
    };
    
    // Get folder name for upload button
    const folderName = selectedItem.name;
    
    const handleView = () => {
        navigate(`/databases/${databaseId}/assets/${assetId}/file`, {
            state: {
                filename: selectedItem.name,
                key: selectedItem.keyPrefix,
                isDirectory: isFolder,
                size: selectedItem.size,
                dateCreatedCurrentVersion: selectedItem.dateCreatedCurrentVersion,
                versionId: selectedItem.versionId
            },
        });
    };
    
    // Handle create folder
    const handleCreateFolder = async (newFolderName: string) => {
        setIsCreatingFolder(true);
        
        try {
            // Construct the full path for the new folder
            let relativeKey;
            
            // If we're at the root level
            if (selectedItem.relativePath === "/") {
                relativeKey = `${newFolderName}/`;
            } else {
                // If we're in a subfolder, use the selected item's keyPrefix
                // Make sure it ends with a slash
                const baseKey = selectedItem.keyPrefix.endsWith('/') 
                    ? selectedItem.keyPrefix 
                    : `${selectedItem.keyPrefix}/`;
                
                relativeKey = `${baseKey}${newFolderName}/`;
            }
            
            // Call the API to create the folder
            const [success, response] = await createFolder({
                databaseId,
                assetId,
                relativeKey
            });
            
            if (success) {
                // Refresh the file list
                dispatch({ 
                    type: "REFRESH_FILES",
                    payload: null
                });
                
                // Close the modal
                setCreateFolderModalVisible(false);
            } else {
                console.error("Failed to create folder:", response);
                // Handle error (could show an error message)
            }
        } catch (error) {
            console.error("Error creating folder:", error);
            // Handle error
        } finally {
            setIsCreatingFolder(false);
        }
    };
    
    return (
        <div className="file-info-panel">
            <CreateFolderModal
                visible={createFolderModalVisible}
                onDismiss={() => setCreateFolderModalVisible(false)}
                onSubmit={handleCreateFolder}
                parentFolder={folderName}
                isLoading={isCreatingFolder}
            />
            
            <div className="file-info-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
                <div style={{ flexShrink: 1, overflow: 'hidden', textOverflow: 'ellipsis', marginRight: '16px', maxWidth: '50%' }}>
                    <Header variant="h3">{selectedItem.displayName}</Header>
                </div>
                <div className="file-actions" style={{ flexShrink: 0, display: 'flex', flexWrap: 'nowrap', minWidth: 'fit-content' }}>
                    {isFolder ? (
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button 
                                iconName="upload" 
                                onClick={handleUpload}
                            >
                                Upload Files
                            </Button>
                            <Button 
                                iconName="folder" 
                                onClick={() => setCreateFolderModalVisible(true)}
                            >
                                Create Sub-Folder
                            </Button>
                            {hasFolderContent(selectedItem) && (
                                <Button 
                                    iconName="download" 
                                    onClick={() => {
                                        navigate(`/databases/${databaseId}/assets/${assetId}/download`, {
                                            state: {
                                                fileTree: selectedItem,
                                            },
                                        });
                                    }}
                                >
                                    Download Folder
                                </Button>
                            )}
                        </SpaceBetween>
                    ) : (
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button 
                                iconName="download" 
                                onClick={handleDownload}
                            >
                                Download File
                            </Button>
                            <Button 
                                iconName="external" 
                                onClick={handleView}
                            >
                                View File
                            </Button>
                        </SpaceBetween>
                    )}
                </div>
            </div>
            
            <div className="file-info-content">
                <div className="file-info-item">
                    <div className="file-info-label">Name:</div>
                    <div className="file-info-value">{selectedItem.name}</div>
                </div>
                
                <div className="file-info-item">
                    <div className="file-info-label">Path:</div>
                    <div className="file-info-value">{selectedItem.relativePath}</div>
                </div>
                
                <div className="file-info-item">
                    <div className="file-info-label">Type:</div>
                    <div className="file-info-value">{isFolder ? 'Folder' : 'File'}</div>
                </div>
                
                {!isFolder && selectedItem.size !== undefined && (
                    <div className="file-info-item">
                        <div className="file-info-label">Size:</div>
                        <div className="file-info-value">{formatFileSize(selectedItem.size)}</div>
                    </div>
                )}
                
                {selectedItem.dateCreatedCurrentVersion && (
                    <div className="file-info-item">
                        <div className="file-info-label">Date Created:</div>
                        <div className="file-info-value">{formatDate(selectedItem.dateCreatedCurrentVersion)}</div>
                    </div>
                )}
                
                {selectedItem.versionId && (
                    <div className="file-info-item">
                        <div className="file-info-label">Version ID:</div>
                        <div className="file-info-value">{selectedItem.versionId}</div>
                    </div>
                )}
            </div>
        </div>
    );
}

// Main Component
export function EnhancedFileManager({ assetName, assetFiles = [] }: { assetName: string, assetFiles?: FileKey[] }) {
    const { databaseId, assetId } = useParams();
    const { state: assetDetailState } = useContext(AssetDetailContext) as AssetDetailContextType;
    const navigate = useNavigate();
    
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
        assetId: assetId!,
        databaseId: databaseId!,
        loading: true,
        error: null,
        searchTerm: '',
        searchResults: [],
        isSearching: false,
        refreshTrigger: 0
    };
    
    const [state, dispatch] = useReducer(fileManagerReducer, initialState);
    
    // Initial load of files
    useEffect(() => {
        if (assetFiles && assetFiles.length > 0) {
            const fileTree = addFiles(assetFiles, initialState.fileTree);
            dispatch({ type: "FETCH_SUCCESS", payload: fileTree });
            
            // If there's exactly 1 file, select it by default
            if (assetFiles.length === 1 && !assetFiles[0].isFolder && !assetFiles[0].key.endsWith('/')) {
                // Find the file in the tree
                const singleFile = fileTree.subTree.find(item => 
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
    }, [assetFiles]);
    
    // Handle refreshing files when refreshTrigger changes
    useEffect(() => {
        // Skip the initial render
        if (state.refreshTrigger === 0) return;
        
        const refreshFiles = async () => {
            try {
                // Fetch the latest files
                const files = await fetchAssetFiles({ 
                    databaseId: state.databaseId, 
                    assetId: state.assetId 
                });
                
                if (files && Array.isArray(files)) {
                    // Create a new file tree with the updated files
                    const newFileTree = {
                        ...initialState.fileTree,
                        subTree: [] // Clear existing subtree
                    };
                    
                    const updatedFileTree = addFiles(files, newFileTree);
                    
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
                } else {
                    // If no files were returned, just show an empty file tree
                    dispatch({ type: "FETCH_SUCCESS", payload: initialState.fileTree });
                }
            } catch (error) {
                console.error("Error refreshing files:", error);
                dispatch({ 
                    type: "FETCH_ERROR", 
                    payload: "Failed to refresh files. Please try again." 
                });
            }
        };
        
        refreshFiles();
    }, [state.refreshTrigger, state.assetId, state.databaseId]);
    
    return (
        <Container header={<Header variant="h2">File Manager</Header>}>
            <FileManagerContext.Provider value={{ state, dispatch }}>
                <div className="enhanced-file-manager">
                    <div className="file-manager-tree">
                        <DirectoryTree />
                    </div>
                    <div className="file-manager-info">
                        <FileInfoPanel />
                    </div>
                </div>
            </FileManagerContext.Provider>
        </Container>
    );
}
