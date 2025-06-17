import { FileKey, FileTree } from "../types/FileManagerTypes";
import { downloadAsset } from "../../../services/APIService";

// Helper functions for file manager operations

export function getRootByPath(root: FileTree | null, path: string): FileTree | null {
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
export function hasFolderContent(folder: FileTree): boolean {
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

export function addFiles(fileKeys: FileKey[], root: FileTree) {
    // Helper function to get parent directory path
    const getParentDirectory = (path: string) => {
        const parentPath = path.split("/").slice(0, -1).join("/");
        return parentPath === "" ? "" : parentPath;
    };
    
    // Enhanced logging for debugging
    console.log("Adding files to tree, total items:", fileKeys.length);
    
    try {
        // Filter out problematic entries
        const filteredFileKeys = fileKeys.filter(fileKey => {
            // Skip entries that are just the asset folder (empty fileName and relativePath)
            if (fileKey.fileName === "" && fileKey.relativePath === "") {
                return false;
            }
            
            // Skip entries with double slashes in the key (these are artifacts)
            if (fileKey.key.includes('//')) {
                console.warn("Skipping entry with double slashes:", fileKey);
                return false;
            }
            
            return true;
        });
        
        console.log("Filtered file keys:", filteredFileKeys.length);
        
        // Track all created paths to avoid duplicates
        const createdPaths = new Set<string>();
        
        // First, separate folders and files
        const folders: FileKey[] = [];
        const files: FileKey[] = [];
        
        filteredFileKeys.forEach(fileKey => {
            // Improved folder detection logic
            const isFolder = fileKey.isFolder || fileKey.key.endsWith('/') || 
                             (fileKey.fileName === "" && fileKey.relativePath.endsWith('/'));
            
            if (isFolder) {
                folders.push({
                    ...fileKey,
                    isFolder: true // Ensure isFolder is set correctly
                });
            } else {
                files.push(fileKey);
            }
        });
        
        console.log("Folders to process:", folders.length);
        console.log("Files to process:", files.length);
        
        // Process folders first - this ensures the folder structure is in place before adding files
        for (const folderKey of folders) {
            const relativePath = folderKey.relativePath;
            
            // Ensure relativePath ends with a slash for folders
            const normalizedPath = relativePath.endsWith('/') ? relativePath : relativePath + '/';
            
            // Skip if we've already created this path
            if (createdPaths.has(normalizedPath)) {
                continue;
            }
            
            // Extract folder name from path or use fileName
            const folderName = folderKey.fileName || 
                              (normalizedPath.split('/').filter(Boolean).pop() || '');
            
            // For root level folders
            if (normalizedPath === '/' || normalizedPath.split('/').filter(Boolean).length === 1) {
                    // Create node for root level folder
                    root.subTree.push({
                        name: folderName,
                        displayName: folderName,
                        relativePath: normalizedPath,
                        keyPrefix: folderKey.key,
                        level: 1,
                        expanded: false,
                        subTree: [],
                        isFolder: true,
                        size: folderKey.size,
                        dateCreatedCurrentVersion: folderKey.dateCreatedCurrentVersion,
                        versionId: folderKey.versionId,
                        isArchived: folderKey.isArchived
                    });
                
                console.log("Added root level folder:", normalizedPath);
            } else {
                // For nested folders
                const parentDir = getParentDirectory(normalizedPath);
                let parentNode = getRootByPath(root, parentDir + "/");
                
                if (!parentNode) {
                    // Create parent directories if they don't exist
                    console.log("Creating parent directories for:", normalizedPath);
                    parentNode = addDirectories(root, parentDir);
                }
                
                // Check if this folder already exists in the parent's subtree
                const existingFolder = parentNode.subTree.find(item => 
                    item.relativePath === normalizedPath);
                
                if (!existingFolder) {
                    // Add the folder to its parent
                    parentNode.subTree.push({
                        name: folderName,
                        displayName: folderName,
                        relativePath: normalizedPath,
                        keyPrefix: folderKey.key,
                        level: parentNode.level + 1,
                        expanded: false,
                        subTree: [],
                        isFolder: true,
                        size: folderKey.size,
                        dateCreatedCurrentVersion: folderKey.dateCreatedCurrentVersion,
                        versionId: folderKey.versionId,
                        isArchived: folderKey.isArchived
                    });
                    
                    console.log("Added nested folder:", normalizedPath);
                }
            }
            
            // Mark this path as created
            createdPaths.add(normalizedPath);
        }
        
        // Then process files
        for (const fileKey of files) {
            const relativePath = fileKey.relativePath;
            
            // Skip if we've already created this path (shouldn't happen for files, but just in case)
            if (createdPaths.has(relativePath)) {
                continue;
            }
            
            // For root level files
            if (!relativePath.includes('/') || relativePath.startsWith('/') && relativePath.lastIndexOf('/') === 0) {
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
                    versionId: fileKey.versionId,
                    isArchived: fileKey.isArchived
                });
                
                console.log("Added root level file:", relativePath);
            } else {
                // For nested files, find or create parent directories
                const parentDir = getParentDirectory(relativePath);
                let parentNode = getRootByPath(root, parentDir + "/");
                
                if (!parentNode) {
                    // Create parent directories if they don't exist
                    console.log("Creating parent directories for file:", relativePath);
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
                    versionId: fileKey.versionId,
                    isArchived: fileKey.isArchived
                });
                
                console.log("Added nested file:", relativePath);
            }
            
            // Mark this path as created
            createdPaths.add(relativePath);
        }
        
        console.log("File tree construction complete");
        return root;
    } catch (error) {
        console.error("Error in addFiles function:", error);
        // Handle the unknown error type properly
        const errorMessage = error instanceof Error ? error.message : String(error);
        throw new Error(`Failed to construct file tree: ${errorMessage}`);
    }
}

export function toggleExpanded(fileTree: FileTree, relativePath: string): FileTree {
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

export async function downloadFile(assetId: string, databaseId: string, keyPrefix: string) {
    try {
        const response = await downloadAsset({
            assetId: assetId,
            databaseId: databaseId,
            key: keyPrefix,
            versionId: "",
            downloadType: "assetFile"
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

// Search function to find matching files/folders
export function searchFileTree(root: FileTree, searchTerm: string, results: FileTree[] = []): FileTree[] {
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

// Helper functions for formatting
export function formatFileSize(size?: number): string {
    if (size === undefined) return "Unknown";
    if (size === 0) return "0 B";
    
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(size) / Math.log(1024));
    return `${(size / Math.pow(1024, i)).toFixed(2)} ${units[i]}`;
}

export function formatDate(dateString?: string): string {
    if (!dateString) return "Unknown";
    try {
        const date = new Date(dateString);
        return date.toLocaleString();
    } catch (e) {
        return dateString;
    }
}
