import { useLocation, useNavigate } from "react-router";
import { downloadAsset } from "../services/APIService";
import { FileTree } from "../components/filemanager/types/FileManagerTypes";
import { FileUploadTable, FileUploadTableItem } from "./AssetUpload/FileUploadTable";
import { useReducer, useState, useEffect } from "react";
import { useParams } from "react-router";
import axios from "axios";
import {
    Box,
    Button,
    Container,
    Header,
    SpaceBetween,
    StatusIndicator,
    Toggle,
    Alert,
} from "@cloudscape-design/components";

// Utility class for managing concurrent downloads
class DownloadQueue {
    private queue: Array<() => Promise<any>> = [];
    private activeDownloads = 0;
    private concurrencyLimit: number;

    constructor(concurrencyLimit = 5) {
        this.concurrencyLimit = concurrencyLimit;
    }

    add(downloadFn: () => Promise<any>): Promise<any> {
        return new Promise((resolve, reject) => {
            // Add the download function and its resolve/reject callbacks to the queue
            this.queue.push(async () => {
                try {
                    const result = await downloadFn();
                    resolve(result);
                } catch (error) {
                    reject(error);
                } finally {
                    this.activeDownloads--;
                    this.processQueue();
                }
            });

            // Process the queue immediately
            this.processQueue();
        });
    }

    private processQueue() {
        // If we can process more downloads and there are items in the queue
        if (this.activeDownloads < this.concurrencyLimit && this.queue.length > 0) {
            const downloadFn = this.queue.shift();
            if (downloadFn) {
                this.activeDownloads++;
                downloadFn();
            }
        }
    }
}

// Flatten the file tree into a list of files for easier processing
const flattenFileTree = (tree: FileTree): Array<FileTree> => {
    const files: Array<FileTree> = [];

    const processNode = (node: FileTree) => {
        // If it's a file (no subtree or empty subtree)
        if (!node.isFolder && (!node.subTree || node.subTree.length === 0)) {
            files.push(node);
        } else {
            // Process each child node
            node.subTree.forEach((child) => processNode(child));
        }
    };

    processNode(tree);
    return files;
};

// Create directory structure recursively
const createDirectoryStructure = async (directoryHandle: any, path: string): Promise<any> => {
    if (!path || path === "/") {
        return directoryHandle;
    }

    const parts = path.split("/").filter((p) => p);
    let currentHandle = directoryHandle;

    for (const part of parts) {
        currentHandle = await currentHandle.getDirectoryHandle(part, { create: true });
    }

    return currentHandle;
};

// Download a single file
const downloadSingleFile = async (
    assetId: string,
    databaseId: string,
    file: FileTree,
    directoryHandle: any,
    dispatch: any,
    flattenHierarchy: boolean = false,
    maxRetries = 3
): Promise<boolean> => {
    let retries = 0;

    while (retries <= maxRetries) {
        try {
            // Update status to "In Progress"
            dispatch({
                type: "UPDATE_STATUS",
                payload: { relativePath: file.relativePath, status: "In Progress" },
            });

            // Handle path based on flatten mode
            let fileName: string;
            let fileDirectoryHandle: any;

            if (flattenHierarchy) {
                // Flatten mode: use only the filename, ignore directory structure
                fileName = file.name;
                fileDirectoryHandle = directoryHandle;
            } else {
                // Preserve mode: maintain original directory structure
                const pathParts = file.relativePath.split("/");
                fileName = pathParts.pop() || file.name;
                const directoryPath = pathParts.join("/");

                // Create directory structure if needed
                fileDirectoryHandle = directoryPath
                    ? await createDirectoryStructure(directoryHandle, directoryPath)
                    : directoryHandle;
            }

            // Get file handle and writable
            const fileHandle = await fileDirectoryHandle.getFileHandle(fileName, { create: true });
            const writable = await fileHandle.createWritable();

            // Get download URL with new downloadType parameter and pass the versionId if available
            const response = await downloadAsset({
                databaseId,
                assetId,
                key: file.keyPrefix,
                versionId: file.versionId || "",
                downloadType: "assetFile",
            });

            if (response === false || !Array.isArray(response)) {
                throw new Error("Invalid response from downloadAsset");
            }

            if (response[0] === false) {
                throw new Error(`API Error: ${response[1]}`);
            }

            // Download the file
            const responseFile = await axios({
                url: response[1],
                method: "GET",
                responseType: "blob",
                onDownloadProgress: (progressEvent) => {
                    // Only update progress if we have valid progress data
                    if (progressEvent.total && progressEvent.loaded !== undefined) {
                        dispatch({
                            type: "UPDATE_PROGRESS",
                            payload: {
                                status: "In Progress",
                                relativePath: file.relativePath,
                                progress: progressEvent.loaded,
                                loaded: progressEvent.loaded,
                                total: progressEvent.total,
                            },
                        });
                    }
                },
            });

            // Write the file and close
            await writable.write(responseFile.data);
            await writable.close();

            // Update status to "Completed" with 100% progress
            const fileSize = responseFile.data.size;
            console.log(
                `File ${file.relativePath} downloaded successfully, size: ${fileSize} bytes`
            );

            // Ensure completion is properly set with final file size
            dispatch({
                type: "COMPLETE_FILE",
                payload: {
                    relativePath: file.relativePath,
                    loaded: fileSize,
                    total: fileSize,
                },
            });

            return true;
        } catch (error) {
            retries++;
            console.error(
                `Error downloading ${file.relativePath} (Attempt ${retries}/${maxRetries}):`,
                error
            );

            if (retries > maxRetries) {
                // Update status to "Failed" after all retries
                dispatch({
                    type: "UPDATE_STATUS",
                    payload: { relativePath: file.relativePath, status: "Failed" },
                });
                return false;
            }

            // Wait before retrying (exponential backoff)
            await new Promise((resolve) => setTimeout(resolve, 1000 * Math.pow(2, retries)));
        }
    }

    return false;
};

// Download all files in parallel with a concurrency limit
const downloadFilesInParallel = async (
    assetId: string,
    databaseId: string,
    files: Array<FileTree>,
    directoryHandle: any,
    dispatch: any,
    flattenHierarchy: boolean = false,
    concurrencyLimit = 5
): Promise<void> => {
    const downloadQueue = new DownloadQueue(concurrencyLimit);

    // Create an array of promises for all file downloads
    const downloadPromises = files.map((file) =>
        downloadQueue.add(() =>
            downloadSingleFile(
                assetId,
                databaseId,
                file,
                directoryHandle,
                dispatch,
                flattenHierarchy
            )
        )
    );

    // Wait for all downloads to complete
    const results = await Promise.allSettled(downloadPromises);

    console.log(`All downloads completed: ${results.length} files processed`);

    // Ensure all files are properly marked as completed or failed
    results.forEach((result, index) => {
        const file = files[index];
        if (result.status === "fulfilled" && result.value === true) {
            // Double-check that the file is marked as completed
            dispatch({
                type: "FORCE_COMPLETE",
                payload: {
                    relativePath: file.relativePath,
                },
            });
        } else if (
            result.status === "rejected" ||
            (result.status === "fulfilled" && result.value === false)
        ) {
            // Mark as failed if rejected or returned false
            dispatch({
                type: "UPDATE_STATUS",
                payload: {
                    relativePath: file.relativePath,
                    status: "Failed",
                },
            });
        }
    });
};

// Main function to download a folder
async function downloadFolder(
    assetId: string,
    databaseId: string,
    tree: FileTree,
    dispatch: any,
    flattenHierarchy: boolean = false,
    setSelectedFolderName?: (name: string) => void
): Promise<void> {
    try {
        // Check if File System Access API is supported
        if (!("showDirectoryPicker" in window)) {
            alert(
                "Your browser doesn't support the File System Access API. Please use a modern browser like Chrome or Edge."
            );
            return;
        }

        // Show directory picker
        // @ts-ignore
        const directoryHandle = await window.showDirectoryPicker();

        // Capture the selected folder name for display purposes
        if (setSelectedFolderName && directoryHandle.name) {
            setSelectedFolderName(directoryHandle.name);
        }

        // Flatten the file tree
        const files = flattenFileTree(tree);
        console.log(
            `Starting download of ${files.length} files in ${
                flattenHierarchy ? "flatten" : "preserve"
            } mode`
        );

        // Download all files in parallel
        await downloadFilesInParallel(
            assetId,
            databaseId,
            files,
            directoryHandle,
            dispatch,
            flattenHierarchy
        );

        console.log("All downloads have been processed");
    } catch (error) {
        console.error("Error downloading folder:", error);

        // If the user cancelled the directory picker, don't show an error
        if (error instanceof DOMException && error.name === "AbortError") {
            return;
        }

        alert(
            `Error downloading folder: ${error instanceof Error ? error.message : String(error)}`
        );
    }
}

// Convert file tree to table items
const convertFileTreeItemsToFileUploadTableItems = (fileTree: FileTree): FileUploadTableItem[] => {
    const allItems: FileUploadTableItem[] = [];

    const processNode = (node: FileTree) => {
        // If it's a file (no subtree or empty subtree)
        if (!node.isFolder && (!node.subTree || node.subTree.length === 0)) {
            // Ensure we have a valid size value
            const fileSize = node.size || 0;

            allItems.push({
                name: node.name,
                index: 0,
                size: fileSize,
                relativePath: node.relativePath,
                status: "Queued",
                progress: 0,
                startedAt: Date.now(),
                loaded: 0,
                total: fileSize, // Set total to the actual file size for proper progress calculation
                versionId: node.versionId || "", // Extract versionId from the node
            });
        } else {
            // Process each child node
            node.subTree.forEach((child) => processNode(child));
        }
    };

    processNode(fileTree);
    return allItems;
};

// Update indices for table items
const updateIndices = (fileUploadTableItems: FileUploadTableItem[]): FileUploadTableItem[] => {
    return fileUploadTableItems.map((item, index) => ({
        ...item,
        index,
    }));
};

// Reducer for managing download state
function assetDownloadReducer(
    state: FileUploadTableItem[],
    action: { type: string; payload: any }
): FileUploadTableItem[] {
    switch (action.type) {
        case "UPDATE_INDICES":
            return updateIndices(state);

        case "UPDATE_PROGRESS":
            return state.map((item) => {
                if (item.relativePath === action.payload.relativePath) {
                    // Prevent status regression - don't update if already completed
                    if (item.status === "Completed") {
                        console.log(
                            `Skipping progress update for completed file: ${item.relativePath}`
                        );
                        return item;
                    }

                    // Calculate progress safely
                    let progress = 0;
                    if (action.payload.total > 0) {
                        progress = Math.floor((action.payload.loaded / action.payload.total) * 100);
                    }

                    // Auto-complete when progress reaches 100%
                    const newStatus = progress >= 100 ? "Completed" : action.payload.status;

                    console.log(
                        `Progress update for ${item.relativePath}: ${progress}%, status: ${newStatus}`
                    );

                    return {
                        ...item,
                        status: newStatus,
                        size: action.payload.total,
                        progress: progress,
                        loaded: action.payload.loaded,
                        total: action.payload.total,
                    };
                } else {
                    return item;
                }
            });

        case "UPDATE_STATUS":
            return state.map((item) => {
                if (item.relativePath === action.payload.relativePath) {
                    // Prevent status regression from Completed to any other status
                    if (item.status === "Completed" && action.payload.status !== "Completed") {
                        console.log(
                            `Preventing status regression for completed file: ${item.relativePath}`
                        );
                        return item;
                    }

                    // If status is "Completed", ensure progress is 100%
                    const progress = action.payload.status === "Completed" ? 100 : item.progress;

                    console.log(
                        `Status update for ${item.relativePath}: ${action.payload.status}, progress: ${progress}%`
                    );

                    return {
                        ...item,
                        status: action.payload.status,
                        progress: progress,
                    };
                } else {
                    return item;
                }
            });

        case "COMPLETE_FILE":
            return state.map((item) => {
                if (item.relativePath === action.payload.relativePath) {
                    // Ensure we have valid values for loaded and total
                    const total = action.payload.total || item.total || 1;
                    const loaded = action.payload.loaded || total;

                    console.log(
                        `Completing file: ${item.relativePath}, loaded: ${loaded}, total: ${total}`
                    );

                    return {
                        ...item,
                        status: "Completed",
                        progress: 100, // Always set to 100% for completed files
                        loaded: loaded,
                        total: total,
                    };
                } else {
                    return item;
                }
            });

        case "FORCE_COMPLETE":
            return state.map((item) => {
                if (item.relativePath === action.payload.relativePath) {
                    // Only force complete if not already completed
                    if (item.status === "Completed") {
                        console.log(
                            `File already completed, skipping force complete: ${item.relativePath}`
                        );
                        return item;
                    }

                    console.log(`Force completing file: ${item.relativePath}`);

                    // Force the item to be completed with 100% progress
                    return {
                        ...item,
                        status: "Completed",
                        progress: 100,
                        loaded: item.total || 1,
                        total: item.total || 1,
                    };
                } else {
                    return item;
                }
            });

        case "RESET_ITEMS":
            return action.payload.map((item: FileUploadTableItem) => ({
                ...item,
                status: "Queued",
                progress: 0,
                startedAt: Date.now(),
                loaded: 0,
                total: item.size || 0, // Preserve the file size information
                // versionId is preserved via the spread operator
            }));

        default:
            return state;
    }
}

// Detect duplicate file names when flattening hierarchy
const detectDuplicateFileNames = (files: FileTree[]): string[] => {
    const fileNameCounts = new Map<string, number>();
    const duplicates: string[] = [];

    files.forEach((file) => {
        const fileName = file.name;
        const count = fileNameCounts.get(fileName) || 0;
        fileNameCounts.set(fileName, count + 1);

        if (count === 1) {
            // This is the second occurrence, so it's a duplicate
            duplicates.push(fileName);
        }
    });

    return duplicates;
};

// Compute final download path based on mode and selected folder
const computeFinalDownloadPath = (
    file: FileUploadTableItem,
    selectedFolder: string,
    flattenMode: boolean
): string => {
    if (!selectedFolder) return "Select folder first";

    if (flattenMode) {
        // Flatten mode: just the filename in the selected folder
        return `${selectedFolder}/${file.name}`;
    } else {
        // Preserve mode: maintain the original path structure
        return `${selectedFolder}/${file.relativePath}`;
    }
};

// Main component
export default function AssetDownloadsPage() {
    const { state } = useLocation();
    const { databaseId, assetId } = useParams();
    const navigate = useNavigate();
    const fileTree = state["fileTree"] as FileTree;
    const [resume, setResume] = useState(true);
    const [isDownloading, setIsDownloading] = useState(false);

    // Toggle state for flatten hierarchy mode (default to true as requested)
    const [flattenHierarchy, setFlattenHierarchy] = useState(true);
    const [selectedFolderName, setSelectedFolderName] = useState<string>("");

    // Initialize table items
    const fileUploadTableItems = convertFileTreeItemsToFileUploadTableItems(fileTree);
    const [fileUploadTableItemsState, dispatch] = useReducer(
        assetDownloadReducer,
        fileUploadTableItems
    );

    // Check for duplicate file names when in flatten mode
    const flattenedFiles = flattenFileTree(fileTree);
    const duplicateFileNames = flattenHierarchy ? detectDuplicateFileNames(flattenedFiles) : [];
    const hasDuplicates = duplicateFileNames.length > 0;

    // Update final download paths when flatten mode or selected folder changes
    const updatedTableItems = fileUploadTableItemsState.map((item) => ({
        ...item,
        finalDownloadPath: computeFinalDownloadPath(item, selectedFolderName, flattenHierarchy),
    }));

    // Update indices when items change
    useEffect(() => {
        dispatch({ type: "UPDATE_INDICES", payload: null });
    }, [fileUploadTableItemsState.length]);

    // Handle download
    const handleDownload = async () => {
        setIsDownloading(true);

        try {
            // Reset items if not resuming
            if (!resume) {
                dispatch({ type: "RESET_ITEMS", payload: fileUploadTableItems });
            }

            // Start download with flatten hierarchy parameter
            await downloadFolder(
                assetId!,
                databaseId!,
                fileTree,
                dispatch,
                flattenHierarchy,
                setSelectedFolderName
            );

            console.log("Download process completed successfully");
        } catch (error) {
            console.error("Error during download process:", error);
        } finally {
            setIsDownloading(false);
            setResume(false);
        }
    };

    // Get download statistics
    const getDownloadStats = () => {
        const total = fileUploadTableItemsState.length;
        const completed = fileUploadTableItemsState.filter(
            (item) => item.status === "Completed"
        ).length;
        const failed = fileUploadTableItemsState.filter((item) => item.status === "Failed").length;
        const inProgress = fileUploadTableItemsState.filter(
            (item) => item.status === "In Progress"
        ).length;
        const queued = fileUploadTableItemsState.filter((item) => item.status === "Queued").length;

        return { total, completed, failed, inProgress, queued };
    };

    // Check if all downloads are complete
    const isAllComplete = () => {
        const stats = getDownloadStats();
        return stats.total > 0 && stats.completed + stats.failed === stats.total;
    };

    // Handle return to asset view
    const handleReturnToAsset = () => {
        navigate(`/databases/${databaseId}/assets/${assetId}`);
    };

    // Force completion of any stuck downloads
    const handleForceComplete = () => {
        fileUploadTableItemsState.forEach((item) => {
            if (item.status === "In Progress" || item.status === "Queued") {
                dispatch({
                    type: "FORCE_COMPLETE",
                    payload: { relativePath: item.relativePath },
                });
            }
        });
    };

    const stats = getDownloadStats();
    const allComplete = isAllComplete();

    return (
        <Container header={<Header variant="h2">Downloading Folder ({fileTree.name})</Header>}>
            <SpaceBetween size="l" direction="vertical">
                {/* Download Mode Toggle */}
                <Box>
                    <SpaceBetween size="m" direction="vertical">
                        <Toggle
                            onChange={({ detail }) => setFlattenHierarchy(detail.checked)}
                            checked={flattenHierarchy}
                        >
                            {flattenHierarchy
                                ? "Flatten Asset Paths on Download"
                                : "Keep Asset Paths on Download"}
                        </Toggle>

                        {/* Error Alert for Duplicate Files */}
                        {flattenHierarchy && hasDuplicates && (
                            <Alert type="error" header="Duplicate File Names Detected">
                                The following files have duplicate names and cannot be downloaded in
                                flatten mode: {duplicateFileNames.join(", ")}. Please switch to
                                "Keep Asset Paths on Download" mode or rename the conflicting files.
                            </Alert>
                        )}
                    </SpaceBetween>
                </Box>

                <Box>
                    <SpaceBetween size="m" direction="vertical">
                        <SpaceBetween size="xs" direction="horizontal">
                            <Button
                                variant="primary"
                                onClick={handleDownload}
                                loading={isDownloading}
                                disabled={isDownloading || (flattenHierarchy && hasDuplicates)}
                            >
                                {resume ? "Start Download" : "Restart Download"}
                            </Button>

                            {!isDownloading && stats.inProgress > 0 && (
                                <Button onClick={handleForceComplete}>Mark All as Complete</Button>
                            )}

                            {allComplete && (
                                <Button onClick={handleReturnToAsset}>Return to View Asset</Button>
                            )}
                        </SpaceBetween>

                        <Box>
                            <StatusIndicator type={allComplete ? "success" : "in-progress"}>
                                {stats.completed} of {stats.total} files completed
                                {stats.failed > 0 && `, ${stats.failed} failed`}
                                {stats.inProgress > 0 && `, ${stats.inProgress} in progress`}
                            </StatusIndicator>
                        </Box>
                    </SpaceBetween>
                </Box>

                <FileUploadTable
                    allItems={updatedTableItems}
                    resume={resume}
                    onRetry={handleDownload}
                    mode={"Download"}
                />
            </SpaceBetween>
        </Container>
    );
}
