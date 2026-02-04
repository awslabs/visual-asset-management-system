import {
    Box,
    Button,
    Container,
    FormField,
    Grid,
    SpaceBetween,
    TextContent,
    Toggle,
} from "@cloudscape-design/components";
import Header from "@cloudscape-design/components/header";
import Alert from "@cloudscape-design/components/alert";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import React, { useEffect, useState, useMemo, useCallback } from "react";
import { AssetDetail } from "./AssetUpload";
import { useLocation, useNavigate, useParams } from "react-router";
import localforage from "localforage";
import { FileUploadTable, FileUploadTableItem, shortenBytes } from "./FileUploadTable";
import Synonyms from "../../synonyms";
import { Link } from "@cloudscape-design/components";
import { FileUpload } from "./components";
import DragDropFileUpload from "../../components/form/DragDropFileUpload";
import { previewFileFormats } from "../../common/constants/fileFormats";
import AssetUploadWorkflow from "./AssetUploadWorkflow";
import { Metadata } from "../../components/single/Metadata";
import { CompleteUploadResponse } from "../../services/AssetUploadService";
import { safeGetFile } from "../../utils/fileHandleCompat";
import { fetchAsset, fetchDatabase } from "../../services/APIService";
import { validateFiles, ValidationResult } from "../../utils/fileExtensionValidation";

// Maximum preview file size (5MB)
const MAX_PREVIEW_FILE_SIZE = 5 * 1024 * 1024;

// Constants
const previewFileFormatsStr = previewFileFormats.join(", ");

// Helper function to determine status indicator type
const getStatusIndicator = (status?: string) => {
    switch (status) {
        case "Queued":
            return "pending";
        case "In Progress":
            return "info";
        case "Completed":
            return "success";
        case "Failed":
            return "error";
        default:
            return "info";
    }
};

export async function verifyPermission(fileHandle: any, readWrite: any) {
    const options = {};
    if (readWrite) {
        //@ts-ignore
        options.mode = "readwrite";
    }
    // Check if permission was already granted. If so, return true.
    if ((await fileHandle.queryPermission(options)) === "granted") {
        return true;
    }
    // Request permission. If the user grants permission, return true.
    if ((await fileHandle.requestPermission(options)) === "granted") {
        return true;
    }
    // The user didn't grant permission, so return false.
    return false;
}

interface FinishUploadsProps {
    assetDetailState: AssetDetail;
    keyPrefix: string;
    isNewFiles: boolean;
}

const getFilesFromFileHandles = async (
    fileHandles: any[],
    prefix: string = ""
): Promise<FileUploadTableItem[]> => {
    const fileUploadTableItems: FileUploadTableItem[] = [];
    for (let i = 0; i < fileHandles.length; i++) {
        try {
            // Use our safe utility to get the file regardless of handle type
            const file = await safeGetFile(fileHandles[i].handle);

            // Prepend the folder path to the relative path if a prefix exists
            const relativePath = prefix
                ? prefix.endsWith("/")
                    ? prefix + fileHandles[i].path
                    : prefix + "/" + fileHandles[i].path
                : fileHandles[i].path;

            fileUploadTableItems.push({
                handle: fileHandles[i].handle,
                index: i,
                name: fileHandles[i].handle.name || file.name,
                size: file.size,
                relativePath: relativePath,
                progress: 0,
                status: "Queued",
                loaded: 0,
                total: file.size,
            });
        } catch (error) {
            console.error(`Error processing file at index ${i}:`, error);
            // Add a placeholder entry with error status
            fileUploadTableItems.push({
                handle: fileHandles[i].handle,
                index: i,
                name: fileHandles[i].handle.name || `File ${i}`,
                size: 0,
                relativePath: fileHandles[i].path || "",
                progress: 0,
                status: "Failed",
                loaded: 0,
                total: 0,
                error: "Browser compatibility issue: Cannot access file",
            });
        }
    }
    return fileUploadTableItems;
};

export default function ModifyAssetsUploadsPage() {
    const { state } = useLocation();
    const navigate = useNavigate();
    const { databaseId, assetId } = useParams();

    // Create a default AssetDetail if state.assetDetailState is undefined
    const defaultAssetDetail: AssetDetail = {
        assetId: assetId || "",
        databaseId: databaseId || "",
        assetName: "",
        Asset: [],
        isMultiFile: false,
    };

    const [assetDetail, setAssetDetail] = useState<AssetDetail>(
        state?.assetDetailState || defaultAssetDetail
    );
    const [showUploadWorkflow, setShowUploadWorkflow] = useState(false);
    // Initialize with empty array since we're uploading new files, not showing existing ones
    const [fileItems, setFileItems] = useState<FileUploadTableItem[]>([]);
    const [previewFile, setPreviewFile] = useState<File | null>(null);
    const [previewFileError, setPreviewFileError] = useState<string | undefined>(undefined);
    const [folderPath, setFolderPath] = useState<string>("");
    const [keyPrefix, setKeyPrefix] = useState<string>("");

    const [restrictFileUploadsToExtensions, setRestrictFileUploadsToExtensions] =
        useState<string>("");
    const [fileValidationResult, setFileValidationResult] = useState<ValidationResult | null>(null);
    const [selectionMode, setSelectionMode] = useState<"folder" | "files" | "both">(
        assetDetail.isMultiFile ? "folder" : "files"
    );

    // Update assetDetail when fileItems change
    useEffect(() => {
        setAssetDetail((prev) => ({
            ...prev,
            Asset: fileItems,
            // Don't automatically change isMultiFile based on file count
            // Keep the user's selection mode preference
            Preview: previewFile || undefined,
        }));
    }, [fileItems, previewFile]);

    // Extract folder information and asset details from state if available
    useEffect(() => {
        console.log("ModifyAssetsUploads - Full state received:", state);
        console.log("ModifyAssetsUploads - URL params:", { databaseId, assetId });

        if (state) {
            // Extract asset detail state if available
            if (state.assetDetailState) {
                console.log(
                    "ModifyAssetsUploads - Setting asset detail from state:",
                    state.assetDetailState
                );
                setAssetDetail(state.assetDetailState);
            }

            // Extract folder information if available
            if (state.fileTree) {
                const relativePath = state.fileTree.relativePath || "";
                setFolderPath(relativePath);

                // Ensure that "/" is properly recognized as a root path
                // If folderPath is "/", ensure keyPrefix is also "/"
                let prefix = state.fileTree.keyPrefix || "";
                if (relativePath === "/" || prefix === "/") {
                    prefix = "/";
                }
                setKeyPrefix(prefix);

                console.log("ModifyAssetsUploads - Path Info:", {
                    relativePath,
                    prefix,
                    originalKeyPrefix: state.fileTree.keyPrefix,
                });
            }
        }

        // Always ensure we have at least the URL params set
        if (assetId && databaseId) {
            setAssetDetail((prev) => ({
                ...prev,
                assetId: assetId,
                databaseId: databaseId,
                // Only update assetName if it's not already set and we don't have it from state
                assetName: prev.assetName || state?.assetDetailState?.assetName || assetId,
            }));
        }
    }, [state, assetId, databaseId]);

    // Fetch asset information directly if not available in state
    useEffect(() => {
        const fetchAssetInfo = async () => {
            // Only fetch if we don't have a proper asset name and we have the required params
            if (
                assetId &&
                databaseId &&
                (!assetDetail.assetName || assetDetail.assetName === assetId)
            ) {
                try {
                    console.log("ModifyAssetsUploads - Fetching asset info directly from API");
                    const assetInfo = await fetchAsset({
                        databaseId,
                        assetId,
                        showArchived: false,
                    });

                    if (assetInfo && assetInfo !== false) {
                        console.log("ModifyAssetsUploads - Fetched asset info:", assetInfo);
                        setAssetDetail((prev) => ({
                            ...prev,
                            assetName: assetInfo.assetName || assetInfo.assetId || prev.assetName,
                            description: assetInfo.description || prev.description,
                            isDistributable:
                                assetInfo.isDistributable !== undefined
                                    ? assetInfo.isDistributable
                                    : prev.isDistributable,
                        }));
                    }
                } catch (error) {
                    console.error("ModifyAssetsUploads - Error fetching asset info:", error);
                }
            }
        };

        fetchAssetInfo();
    }, [assetId, databaseId, assetDetail.assetName]);

    // Fetch database restrictions
    useEffect(() => {
        const fetchDatabaseRestrictions = async () => {
            if (databaseId) {
                try {
                    console.log("ModifyAssetsUploads - Fetching database restrictions");
                    const databaseInfo = await fetchDatabase({ databaseId });

                    console.log("ModifyAssetsUploads - Database response:", databaseInfo);

                    if (databaseInfo && databaseInfo !== false) {
                        console.log(
                            "ModifyAssetsUploads - restrictFileUploadsToExtensions:",
                            databaseInfo.restrictFileUploadsToExtensions
                        );

                        setRestrictFileUploadsToExtensions(
                            databaseInfo.restrictFileUploadsToExtensions || ""
                        );
                    }
                } catch (error) {
                    console.error("ModifyAssetsUploads - Error fetching database info:", error);
                }
            }
        };

        fetchDatabaseRestrictions();
    }, [databaseId]);

    // Validate files whenever fileItems or restrictions change
    useEffect(() => {
        if (fileItems && fileItems.length > 0) {
            const validationResult = validateFiles(fileItems, restrictFileUploadsToExtensions);
            setFileValidationResult(validationResult);
        } else {
            setFileValidationResult(null);
        }
    }, [fileItems, restrictFileUploadsToExtensions]);

    // Clear preview file when not uploading to root path
    useEffect(() => {
        const isRootPath = !keyPrefix || keyPrefix === "" || keyPrefix === "/";
        if (!isRootPath && previewFile) {
            setPreviewFile(null);
        }
    }, [keyPrefix, previewFile]);

    // Save to localforage when assetDetail changes
    useEffect(() => {
        if (assetDetail && assetDetail.assetId) {
            localforage
                .setItem(assetDetail.assetId, assetDetail)
                .then(() => {
                    console.log("Asset detail saved to local storage");
                })
                .catch((error) => {
                    console.error("Error saving asset detail to local storage:", error);
                });
        }
    }, [assetDetail]);

    // Handle file selection
    const handleFileSelection = useCallback(
        async (directoryHandle: any, fileHandles: any[]) => {
            try {
                console.log("handleFileSelection called with:", {
                    directoryHandle,
                    fileHandlesCount: fileHandles.length,
                    keyPrefix,
                });

                // Convert the file selection directly to FileUploadTableItems
                const newItems = await getFilesFromFileHandles(fileHandles, keyPrefix);

                console.log("File selection - new items:", newItems.length, newItems);

                // Combine with existing files if any exist
                setFileItems((prevItems) => {
                    console.log("Previous items count:", prevItems.length);

                    if (prevItems.length === 0) {
                        console.log("No previous items, returning new items");
                        return newItems;
                    }

                    // Create a map of existing file paths to avoid duplicates
                    const existingFilePaths = new Map(
                        prevItems.map((item) => [item.relativePath, item])
                    );

                    // Filter out any new files that would be duplicates
                    const uniqueNewFiles = newItems.filter(
                        (file) => !existingFilePaths.has(file.relativePath)
                    );

                    console.log("Unique new files:", uniqueNewFiles.length);

                    // Combine existing files with unique new files
                    const combinedFiles = [
                        ...prevItems,
                        ...uniqueNewFiles.map((file, idx) => ({
                            ...file,
                            index: prevItems.length + idx,
                        })),
                    ];

                    console.log("Combined files:", combinedFiles.length);
                    return combinedFiles;
                });
            } catch (error) {
                console.error("Error in handleFileSelection:", error);
            }
        },
        [keyPrefix]
    );

    // Check for preview files in the selected files
    const hasPreviewFiles = useMemo(() => {
        return fileItems.some((item) => item.name.includes(".previewFile."));
    }, [fileItems]);

    // Handle preview file selection with size validation
    const handlePreviewFileSelection = (file: File | null) => {
        if (file && file.size > MAX_PREVIEW_FILE_SIZE) {
            setPreviewFileError("Preview file exceeds maximum allowed size of 5MB");
            // Don't update the state with the oversized file
            return;
        }

        // Clear any previous error
        setPreviewFileError(undefined);

        // Update the state with the valid file
        setPreviewFile(file);
    };

    // Function to remove a file
    const handleRemoveFile = (index: number) => {
        if (fileItems) {
            const updatedFiles = fileItems.filter((item) => item.index !== index);

            // Update indices to be sequential
            const reindexedFiles = updatedFiles.map((item, idx) => ({
                ...item,
                index: idx,
            }));

            setFileItems(reindexedFiles);
        }
    };

    // Function to remove all files
    const handleRemoveAllFiles = () => {
        setFileItems([]);
    };

    // Handle upload completion
    const handleUploadComplete = (response: CompleteUploadResponse) => {
        console.log("Upload completed:", response);
        // Remove window beforeunload handler if it was set
        window.onbeforeunload = null;
    };

    // Handle cancel
    const handleCancel = () => {
        setShowUploadWorkflow(false);
    };

    // Handle view asset
    const handleViewAsset = () => {
        navigate(`/databases/${assetDetail.databaseId}/assets/${assetDetail.assetId}`);
    };

    // Start upload process
    const startUpload = () => {
        // Don't proceed if there's a preview file error
        if (previewFileError) {
            return;
        }
        setShowUploadWorkflow(true);
    };

    // Check if we're uploading to root path (including explicit "/" path)
    const isRootPath = !keyPrefix || keyPrefix === "" || keyPrefix === "/";

    // Debug log to help troubleshoot path issues
    console.log("ModifyAssetsUploads - Path Check:", {
        folderPath,
        keyPrefix,
        isRootPath,
    });

    return (
        <Box padding={{ top: false ? "s" : "m", horizontal: "l" }}>
            <SpaceBetween size="l" direction="vertical">
                <Grid gridDefinition={[{ colspan: { default: 12 } }]}>
                    <div>
                        <TextContent>
                            <Header variant="h1">Modify Asset Files</Header>
                        </TextContent>

                        {/* Asset Information */}
                        <Container header={<Header variant="h2">Asset Information</Header>}>
                            <SpaceBetween direction="vertical" size="m">
                                <Box variant="awsui-key-label">
                                    {Synonyms.Asset}:
                                    <Link
                                        href={`#/databases/${assetDetail.databaseId}/assets/${assetDetail.assetId}`}
                                        target="_blank"
                                    >
                                        {` ${
                                            assetDetail.assetName ||
                                            assetDetail.assetId ||
                                            "Loading..."
                                        }`}
                                    </Link>
                                </Box>
                                <Box variant="awsui-key-label">
                                    Database: {assetDetail.databaseId}
                                </Box>
                                {folderPath && (
                                    <Box variant="awsui-key-label">
                                        Upload Location: {folderPath}
                                    </Box>
                                )}
                            </SpaceBetween>
                        </Container>

                        {/* Display warning about file extension restrictions if they exist */}
                        {restrictFileUploadsToExtensions &&
                            restrictFileUploadsToExtensions.trim() !== "" &&
                            restrictFileUploadsToExtensions.toLowerCase() !== ".all" && (
                                <Container>
                                    <Alert header="File Upload Restrictions" type="warning">
                                        <SpaceBetween direction="vertical" size="xs">
                                            <div>
                                                This database has file upload restrictions in place.
                                                Only files with the following extensions are
                                                allowed:
                                            </div>
                                            <div style={{ marginTop: "8px" }}>
                                                <strong>{restrictFileUploadsToExtensions}</strong>
                                            </div>
                                            <div style={{ fontSize: "0.9em", marginTop: "8px" }}>
                                                <em>
                                                    Note: Preview files (containing .previewFile. in
                                                    the filename) are exempt from these
                                                    restrictions.
                                                </em>
                                            </div>
                                        </SpaceBetween>
                                    </Alert>
                                </Container>
                            )}

                        {/* Display file extension validation errors */}
                        {fileValidationResult && !fileValidationResult.isValid && (
                            <Container>
                                <Alert header="Invalid Files Selected" type="error">
                                    <SpaceBetween direction="vertical" size="xs">
                                        <div>
                                            The following files cannot be uploaded because their
                                            extensions are not allowed for this database:
                                        </div>
                                        <ul style={{ marginTop: "8px", marginBottom: "8px" }}>
                                            {fileValidationResult.invalidFiles.map(
                                                (file, index) => (
                                                    <li key={index}>
                                                        <strong>{file.fileName}</strong> - Extension{" "}
                                                        {file.extension} not allowed
                                                    </li>
                                                )
                                            )}
                                        </ul>
                                        <div>
                                            <strong>Allowed extensions:</strong>{" "}
                                            {fileValidationResult.allowedExtensions?.join(", ")}
                                        </div>
                                    </SpaceBetween>
                                </Alert>
                            </Container>
                        )}

                        {/* Show upload workflow or file selection UI */}
                        {showUploadWorkflow ? (
                            <AssetUploadWorkflow
                                assetDetail={assetDetail}
                                metadata={{}} // Pass empty metadata object to ensure the step is skipped
                                fileItems={fileItems}
                                onComplete={handleUploadComplete}
                                onCancel={handleCancel}
                                isExistingAsset={true}
                                keyPrefix={keyPrefix}
                            />
                        ) : (
                            <Container header={<Header variant="h2">Select Files</Header>}>
                                <SpaceBetween direction="vertical" size="l">
                                    <Alert header="Preview File Information" type="info">
                                        <p>
                                            Files with <strong>.previewFile.</strong> in the
                                            filename will be ingested as preview files for their
                                            associated files. For example,{" "}
                                            <code>model.gltf.previewFile.png</code> will be used as
                                            a preview for <code>model.gltf</code>.
                                        </p>
                                        <p>
                                            <strong>Important notes:</strong>
                                            <ul>
                                                <li>
                                                    You cannot upload a preview file for a file that
                                                    is not part of this upload or is already
                                                    uploaded as part of the asset.
                                                </li>
                                                <li>
                                                    Only{" "}
                                                    {previewFileFormats.map((ext, index) => (
                                                        <React.Fragment key={ext}>
                                                            {index > 0 && ", "}
                                                            <code>{ext}</code>
                                                        </React.Fragment>
                                                    ))}{" "}
                                                    are valid file extensions for preview files.
                                                </li>
                                                <li>Preview files must be 5MB or less in size.</li>
                                            </ul>
                                        </p>
                                        {hasPreviewFiles && (
                                            <p>
                                                <strong>Note:</strong> Some of your selected files
                                                will be treated as preview files based on their
                                                filenames.
                                            </p>
                                        )}
                                    </Alert>

                                    <Container>
                                        <Grid
                                            gridDefinition={
                                                isRootPath
                                                    ? [{ colspan: 6 }, { colspan: 6 }]
                                                    : [{ colspan: 12 }]
                                            }
                                        >
                                            {/* Asset Files Selection */}
                                            <SpaceBetween direction="vertical" size="m">
                                                <FormField
                                                    label="Asset Files"
                                                    description={
                                                        fileItems.length > 0
                                                            ? `Total Files to Upload: ${fileItems.length}`
                                                            : "Select a folder or multiple files"
                                                    }
                                                >
                                                    <SpaceBetween direction="vertical" size="xs">
                                                        <Toggle
                                                            onChange={({ detail }) => {
                                                                setAssetDetail((prev) => ({
                                                                    ...prev,
                                                                    isMultiFile: detail.checked,
                                                                }));
                                                                setSelectionMode(
                                                                    detail.checked
                                                                        ? "folder"
                                                                        : "files"
                                                                );
                                                            }}
                                                            checked={assetDetail.isMultiFile}
                                                        >
                                                            {assetDetail.isMultiFile
                                                                ? "Folder Upload"
                                                                : "File Upload"}
                                                        </Toggle>

                                                        <DragDropFileUpload
                                                            label=""
                                                            description=""
                                                            multiFile={true}
                                                            selectionMode={selectionMode}
                                                            onSelect={handleFileSelection}
                                                        />
                                                    </SpaceBetween>
                                                </FormField>
                                            </SpaceBetween>

                                            {/* Preview File Selection - Only show when uploading to root path (including "/") */}
                                            {isRootPath && (
                                                <FileUpload
                                                    label="Asset Overall Preview File (Optional)"
                                                    disabled={false}
                                                    setFile={handlePreviewFileSelection}
                                                    fileFormats={previewFileFormatsStr}
                                                    file={previewFile || undefined}
                                                    errorText={previewFileError}
                                                    description={`File types: ${previewFileFormatsStr}. Maximum allowed size: 5MB.`}
                                                    data-testid="preview-file"
                                                />
                                            )}
                                        </Grid>
                                    </Container>

                                    {/* Display selected files with remove option */}
                                    {fileItems && fileItems.length > 0 && (
                                        <Box padding={{ bottom: "l" }}>
                                            <FileUploadTable
                                                allItems={fileItems}
                                                resume={false}
                                                showCount={true}
                                                allowRemoval={true}
                                                onRemoveItem={handleRemoveFile}
                                                onRemoveAll={handleRemoveAllFiles}
                                                displayMode="selection"
                                            />
                                        </Box>
                                    )}

                                    {/* Upload Button */}
                                    <Box textAlign="right">
                                        <SpaceBetween direction="vertical" size="xs">
                                            {fileItems.length === 0 &&
                                                (isRootPath ? !previewFile : true) && (
                                                    <Box
                                                        color="text-status-error"
                                                        fontSize="body-s"
                                                    >
                                                        {isRootPath
                                                            ? "Please select at least one asset file or preview file to upload."
                                                            : "Please select at least one asset file to upload."}
                                                    </Box>
                                                )}
                                            <Button
                                                variant="primary"
                                                onClick={startUpload}
                                                disabled={
                                                    (fileItems.length === 0 &&
                                                        (isRootPath ? !previewFile : true)) ||
                                                    !!previewFileError ||
                                                    !!(
                                                        fileValidationResult &&
                                                        !fileValidationResult.isValid
                                                    )
                                                }
                                            >
                                                Finalize and Upload
                                            </Button>
                                        </SpaceBetween>
                                    </Box>
                                </SpaceBetween>
                            </Container>
                        )}
                    </div>
                </Grid>
            </SpaceBetween>
        </Box>
    );
}
