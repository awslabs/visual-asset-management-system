import { FileKey, FileTree } from "../types/FileManagerTypes";
import { downloadAsset } from "../../../services/APIService";

// Helper functions for file manager operations

// Calculate total size of all files in the tree (excluding folders)
export function calculateTotalAssetSize(fileTree: FileTree): number {
    let totalSize = 0;

    // Helper function to recursively traverse the tree
    function traverseTree(node: FileTree) {
        // Check if this is a file (not a folder)
        const isFolder =
            node.isFolder !== undefined
                ? node.isFolder
                : node.subTree.length > 0 || node.keyPrefix.endsWith("/");

        // If it's a file, add its size to the total
        if (!isFolder && node.size !== undefined) {
            totalSize += node.size;
        }

        // Recursively process all children
        for (const child of node.subTree) {
            traverseTree(child);
        }
    }

    // Start traversal from the root
    traverseTree(fileTree);
    return totalSize;
}

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

// Helper function to extract all parent folder paths from a file path
export function getParentFolderPaths(filePath: string): string[] {
    const parentPaths: string[] = [];

    // Always include root
    parentPaths.push("/");

    // If the path is just root, return early
    if (filePath === "/" || filePath === "") {
        return parentPaths;
    }

    // Remove leading slash if present for easier processing
    const cleanPath = filePath.startsWith("/") ? filePath.substring(1) : filePath;

    // Split by "/" and build up parent paths
    const parts = cleanPath.split("/");

    // For each part except the last (which is the file itself), build the folder path
    for (let i = 0; i < parts.length - 1; i++) {
        const folderPath = "/" + parts.slice(0, i + 1).join("/") + "/";
        parentPaths.push(folderPath);
    }

    return parentPaths;
}

// Helper function to check if a folder has any files beneath it (recursively)
export function hasFolderContent(folder: FileTree): boolean {
    // Check if any direct children are files
    const hasFiles = folder.subTree.some((item) => {
        const isFile =
            item.isFolder === false ||
            (item.isFolder === undefined &&
                item.subTree.length === 0 &&
                !item.keyPrefix.endsWith("/"));
        return isFile;
    });

    if (hasFiles) {
        return true;
    }

    // Recursively check subfolders
    return folder.subTree.some((item) => {
        const isSubfolder =
            item.isFolder === true ||
            (item.isFolder === undefined &&
                (item.subTree.length > 0 || item.keyPrefix.endsWith("/")));

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

/**
 * Merge new files into an existing tree, updating existing nodes instead of duplicating
 * Creates a new tree with updated nodes to ensure React detects changes
 */
export function mergeFiles(
    fileKeys: FileKey[],
    root: FileTree,
    expandedFolders?: Set<string>
): FileTree {
    // CRITICAL: Store original root node name/displayName at the START
    const originalRootName = root.name;
    const originalRootDisplayName = root.displayName;

    // Create a map of updates by relativePath
    const updatesMap = new Map<string, FileKey>();
    const newFilesMap = new Map<string, FileKey>();

    // Build map of existing nodes
    const existingNodesMap = new Map<string, FileTree>();
    const buildNodeMap = (node: FileTree): void => {
        existingNodesMap.set(node.relativePath, node);
        node.subTree.forEach((child) => buildNodeMap(child));
    };
    buildNodeMap(root);

    // Categorize incoming files as updates or new files
    for (const fileKey of fileKeys) {
        const normalizedPath =
            fileKey.isFolder || fileKey.key.endsWith("/")
                ? fileKey.relativePath.endsWith("/")
                    ? fileKey.relativePath
                    : fileKey.relativePath + "/"
                : fileKey.relativePath;

        if (existingNodesMap.has(normalizedPath)) {
            updatesMap.set(normalizedPath, fileKey);
        } else {
            newFilesMap.set(normalizedPath, fileKey);
        }
    }

    // If there are updates, create a new tree with updated nodes
    if (updatesMap.size > 0) {
        // Recursive function to update nodes and create new tree
        const updateNode = (node: FileTree): FileTree => {
            const update = updatesMap.get(node.relativePath);

            // Preserve expansion state from expandedFolders set if provided
            const shouldBeExpanded = expandedFolders
                ? expandedFolders.has(node.relativePath)
                : node.expanded;

            // Create new node (always create new object for React to detect change)
            const newNode: FileTree = {
                ...node,
                expanded: shouldBeExpanded,
                subTree: node.subTree.map((child) => updateNode(child)),
            };

            // Apply updates if this node has them
            if (update) {
                // Special handling for root node - DON'T update version data, only preserve name
                if (node.relativePath === "/" && node.level === 0) {
                    // CRITICAL: Never overwrite root name with empty values from API
                    if (node.name && node.displayName) {
                        newNode.name = node.name; // Preserve original name
                        newNode.displayName = node.displayName; // Preserve original displayName
                    }
                    // DO NOT update versionId, dateCreatedCurrentVersion, or isArchived for root
                    newNode.keyPrefix = update.key || newNode.keyPrefix; // Only update keyPrefix
                } else {
                    // For non-root nodes, update name if provided
                    if (update.fileName) {
                        newNode.name = update.fileName;
                        newNode.displayName = update.fileName;
                    }

                    // Apply all updates for non-root nodes
                    // IMPORTANT: Don't overwrite valid versionId with null/empty values
                    // This prevents streaming data (basic mode with null versionId) from overwriting
                    // on-demand fetched data (detailed mode with valid versionId)
                    const hasValidVersionId =
                        newNode.versionId &&
                        newNode.versionId !== null &&
                        newNode.versionId !== "null" &&
                        newNode.versionId !== "";
                    const updateHasValidVersionId =
                        update.versionId &&
                        update.versionId !== null &&
                        update.versionId !== "null" &&
                        update.versionId !== "";

                    // Only update versionId if the update has a valid one, or if current node doesn't have one
                    if (updateHasValidVersionId || !hasValidVersionId) {
                        newNode.versionId = update.versionId || newNode.versionId;
                    }

                    newNode.isArchived =
                        update.isArchived !== undefined ? update.isArchived : newNode.isArchived;
                    newNode.primaryType =
                        update.primaryType !== undefined ? update.primaryType : newNode.primaryType;
                    newNode.previewFile =
                        update.previewFile !== undefined ? update.previewFile : newNode.previewFile;
                    newNode.currentAssetVersionFileVersionMismatch =
                        update.currentAssetVersionFileVersionMismatch !== undefined
                            ? update.currentAssetVersionFileVersionMismatch
                            : newNode.currentAssetVersionFileVersionMismatch;
                    newNode.dateCreatedCurrentVersion =
                        update.dateCreatedCurrentVersion || newNode.dateCreatedCurrentVersion;
                    newNode.size = update.size !== undefined ? update.size : newNode.size;
                    newNode.keyPrefix = update.key || newNode.keyPrefix;
                }
            }

            return newNode;
        };

        root = updateNode(root);
    }

    // Add any new files
    const newFiles = Array.from(newFilesMap.values());
    if (newFiles.length > 0) {
        root = addFiles(newFiles, root, expandedFolders);
    }

    // CRITICAL: Restore original root node name/displayName at the END
    // Also ensure version data is NOT on root node
    if (originalRootName && originalRootDisplayName) {
        root.name = originalRootName;
        root.displayName = originalRootDisplayName;
        // Clear version data from root node (root doesn't have versions)
        delete root.versionId;
        delete root.dateCreatedCurrentVersion;
        delete root.isArchived;
    }

    return root;
}

export function addFiles(fileKeys: FileKey[], root: FileTree, expandedFolders?: Set<string>) {
    // Helper function to get parent directory path
    const getParentDirectory = (path: string) => {
        const parentPath = path.split("/").slice(0, -1).join("/");
        return parentPath === "" ? "" : parentPath;
    };

    try {
        // Filter out problematic entries
        const filteredFileKeys = fileKeys.filter((fileKey) => {
            // Skip entries with double slashes in the key (these are artifacts)
            if (fileKey.key.includes("//")) {
                return false;
            }

            return true;
        });

        // Track all created paths to avoid duplicates
        const createdPaths = new Set<string>();

        // Track folder paths that need version information
        const folderPathsToUpdate = new Map<string, FileKey>();

        // First, separate folders and files
        const folders: FileKey[] = [];
        const files: FileKey[] = [];

        // Find the root folder entry if it exists (empty fileName and relativePath "/")
        let rootFolderEntry: FileKey | undefined;

        filteredFileKeys.forEach((fileKey) => {
            // Check if this is the root folder entry
            if (
                fileKey.fileName === "" &&
                (fileKey.relativePath === "" || fileKey.relativePath === "/")
            ) {
                rootFolderEntry = fileKey;
                // Don't add it to the folders array, we'll handle it specially
                return;
            }

            // Improved folder detection logic
            const isFolder =
                fileKey.isFolder ||
                fileKey.key.endsWith("/") ||
                (fileKey.fileName === "" && fileKey.relativePath.endsWith("/"));

            if (isFolder) {
                folders.push({
                    ...fileKey,
                    isFolder: true, // Ensure isFolder is set correctly
                });

                // Store folder information for later updating
                const normalizedPath = fileKey.relativePath.endsWith("/")
                    ? fileKey.relativePath
                    : fileKey.relativePath + "/";
                folderPathsToUpdate.set(normalizedPath, fileKey);
            } else {
                files.push(fileKey);
            }
        });

        // If we found a root folder entry, ONLY update keyPrefix, NOT name/displayName or version data
        if (rootFolderEntry) {
            // Store the original root name/displayName before any updates
            const originalRootName = root.name;
            const originalRootDisplayName = root.displayName;

            // Only update the keyPrefix to match S3 structure
            root.keyPrefix = rootFolderEntry.key;

            // Ensure root name/displayName are preserved (never overwrite with empty values)
            if (originalRootName) {
                root.name = originalRootName;
            }
            if (originalRootDisplayName) {
                root.displayName = originalRootDisplayName;
            }

            // Mark the root path as created
            createdPaths.add("/");
        }

        // STEP 1: Process files first (and create folder paths as needed)
        for (const fileKey of files) {
            const relativePath = fileKey.relativePath;

            // Skip if we've already created this path (shouldn't happen for files, but just in case)
            if (createdPaths.has(relativePath)) {
                continue;
            }

            // Skip if this is a root folder entry (should have been handled already)
            if (relativePath === "/" || relativePath === "") {
                continue;
            }

            // For root level files
            if (
                !relativePath.includes("/") ||
                (relativePath.startsWith("/") && relativePath.lastIndexOf("/") === 0)
            ) {
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
                    isArchived: fileKey.isArchived,
                    currentAssetVersionFileVersionMismatch:
                        fileKey.currentAssetVersionFileVersionMismatch,
                    primaryType: fileKey.primaryType,
                    previewFile: fileKey.previewFile,
                });
            } else {
                // For nested files, find or create parent directories
                // First, ensure the path is properly formatted
                const cleanPath = relativePath.startsWith("/")
                    ? relativePath.substring(1)
                    : relativePath;
                const pathParts = cleanPath.split("/");

                // Build the path step by step to ensure all parent folders are created
                let currentPath = "";
                let currentNode = root;

                for (let i = 0; i < pathParts.length - 1; i++) {
                    const part = pathParts[i];
                    if (!part) continue; // Skip empty parts

                    // Update the current path
                    currentPath = currentPath ? `${currentPath}/${part}` : part;
                    const folderPath = `/${currentPath}/`;

                    // Check if this folder already exists
                    let folderNode = currentNode.subTree.find(
                        (item) =>
                            item.relativePath === folderPath ||
                            (item.name === part && item.isFolder)
                    );

                    if (!folderNode) {
                        // Determine if this folder should be expanded based on expandedFolders set
                        const shouldBeExpanded = expandedFolders
                            ? expandedFolders.has(folderPath)
                            : false;

                        // Create the folder node
                        folderNode = {
                            name: part,
                            displayName: part,
                            relativePath: folderPath,
                            keyPrefix: part,
                            level: currentNode.level + 1,
                            expanded: shouldBeExpanded,
                            subTree: [],
                            isFolder: true,
                        };

                        currentNode.subTree.push(folderNode);

                        // Mark this path as created
                        createdPaths.add(folderPath);
                    }

                    // Move to the next level
                    currentNode = folderNode;
                }

                // Now add the file to the final parent node
                currentNode.subTree.push({
                    name: fileKey.fileName,
                    displayName: fileKey.fileName,
                    relativePath: relativePath,
                    keyPrefix: fileKey.key,
                    level: currentNode.level + 1,
                    expanded: false,
                    subTree: [],
                    isFolder: false,
                    size: fileKey.size,
                    dateCreatedCurrentVersion: fileKey.dateCreatedCurrentVersion,
                    versionId: fileKey.versionId,
                    isArchived: fileKey.isArchived,
                    currentAssetVersionFileVersionMismatch:
                        fileKey.currentAssetVersionFileVersionMismatch,
                    primaryType: fileKey.primaryType,
                    previewFile: fileKey.previewFile,
                });
            }

            // Mark this path as created
            createdPaths.add(relativePath);
        }

        // STEP 2: Process versioned folders and update existing folder nodes
        for (const folderKey of folders) {
            const relativePath = folderKey.relativePath;

            // Ensure relativePath ends with a slash for folders
            const normalizedPath = relativePath.endsWith("/") ? relativePath : relativePath + "/";

            // Skip the root folder as we've already handled it
            if (normalizedPath === "/" || normalizedPath === "") {
                continue;
            }

            // Check if this folder path was already created during file processing
            if (createdPaths.has(normalizedPath)) {
                // Update existing folder with version information
                const existingFolder = getRootByPath(root, normalizedPath);
                if (existingFolder) {
                    // Update the existing folder with version information
                    existingFolder.dateCreatedCurrentVersion = folderKey.dateCreatedCurrentVersion;
                    existingFolder.versionId = folderKey.versionId;
                    existingFolder.isArchived = folderKey.isArchived;
                    existingFolder.currentAssetVersionFileVersionMismatch =
                        folderKey.currentAssetVersionFileVersionMismatch;
                    existingFolder.keyPrefix = folderKey.key; // Update the key prefix to match the versioned folder
                }
                continue;
            }

            // Extract folder name from path or use fileName
            let folderName = folderKey.fileName;
            if (!folderName || folderName.trim() === "") {
                // Extract the folder name from the path
                const pathParts = normalizedPath.split("/").filter(Boolean);
                folderName = pathParts[pathParts.length - 1] || "";
            }

            // For root level folders (directly under the root)
            if (normalizedPath.split("/").filter(Boolean).length === 1) {
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
                    isArchived: folderKey.isArchived,
                    currentAssetVersionFileVersionMismatch:
                        folderKey.currentAssetVersionFileVersionMismatch,
                });
            } else {
                // For nested folders
                const parentDir = getParentDirectory(normalizedPath.slice(0, -1)); // Remove trailing slash for getParentDirectory
                const parentPath = parentDir + "/";
                let parentNode = getRootByPath(root, parentPath);

                if (!parentNode) {
                    // Create parent directories if they don't exist
                    parentNode = addDirectories(root, parentDir);

                    // Mark parent directories as created
                    let currentPath = parentDir;
                    while (currentPath) {
                        createdPaths.add(currentPath + "/");
                        currentPath = getParentDirectory(currentPath);
                        if (currentPath === "") break;
                    }
                }

                // Check if this folder already exists in the parent's subtree
                const existingFolder = parentNode.subTree.find(
                    (item) => item.relativePath === normalizedPath
                );

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
                        isArchived: folderKey.isArchived,
                        currentAssetVersionFileVersionMismatch:
                            folderKey.currentAssetVersionFileVersionMismatch,
                    });
                }
            }

            // Mark this path as created
            createdPaths.add(normalizedPath);
        }

        // STEP 3: Update any remaining folder paths that were created during file processing
        // but need version information from the folderPathsToUpdate map
        folderPathsToUpdate.forEach((folderKey, normalizedPath) => {
            // Skip the root folder as we've already handled it
            if (normalizedPath === "/" || normalizedPath === "") {
                return;
            }

            if (createdPaths.has(normalizedPath)) {
                const existingFolder = getRootByPath(root, normalizedPath);
                if (existingFolder && !existingFolder.versionId) {
                    // Update the existing folder with version information
                    existingFolder.dateCreatedCurrentVersion = folderKey.dateCreatedCurrentVersion;
                    existingFolder.versionId = folderKey.versionId;
                    existingFolder.isArchived = folderKey.isArchived;
                    existingFolder.currentAssetVersionFileVersionMismatch =
                        folderKey.currentAssetVersionFileVersionMismatch;
                    existingFolder.keyPrefix = folderKey.key; // Update the key prefix to match the versioned folder
                }
            }
        });

        // STEP 4: Final check to remove any duplicate root entries
        // Sometimes a blank folder with path "/" can appear in the tree
        const duplicateRootEntries = root.subTree.filter(
            (item) => item.relativePath === "/" || item.relativePath === ""
        );

        if (duplicateRootEntries.length > 0) {
            // Remove duplicate root entries from the tree
            root.subTree = root.subTree.filter(
                (item) => item.relativePath !== "/" && item.relativePath !== ""
            );
        }

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
            downloadType: "assetFile",
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
export function searchFileTree(
    root: FileTree,
    searchTerm: string,
    results: FileTree[] = []
): FileTree[] {
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

    const units = ["B", "KB", "MB", "GB", "TB"];
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
