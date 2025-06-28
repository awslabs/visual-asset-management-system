import {
    Box,
    Button,
    Container,
    Grid,
    SpaceBetween,
    TextContent,
} from "@cloudscape-design/components";
import Header from "@cloudscape-design/components/header";
import React, { useEffect, useState } from "react";
import { AssetDetail } from "./AssetUpload";
import { useLocation, useNavigate, useParams } from "react-router";
import localforage from "localforage";
import { FileUploadTable, FileUploadTableItem } from "./FileUploadTable";
import Synonyms from "../../synonyms";
import { FileInfo, MultiFileSelect } from "../../components/multifile/MultiFileSelect";
import { Link } from "@cloudscape-design/components";
import { FileUpload } from "./components";
import { previewFileFormats } from "../../common/constants/fileFormats";
import AssetUploadWorkflow from "./AssetUploadWorkflow";
import { Metadata } from "../../components/single/Metadata";
import { CompleteUploadResponse } from "../../services/AssetUploadService";

// Maximum preview file size (5MB)
const MAX_PREVIEW_FILE_SIZE = 5 * 1024 * 1024;

// Constants
const previewFileFormatsStr = previewFileFormats.join(", ");

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

const convertToFileUploadTableItems = (
    fileInfo: FileInfo[],
    prefix: string = ""
): FileUploadTableItem[] => {
    return fileInfo.map((file, index) => {
        // Prepend the folder path to the relative path if a prefix exists
        const relativePath = prefix
            ? prefix.endsWith("/")
                ? prefix + file.path
                : prefix + "/" + file.path
            : file.path;

        return {
            index: index,
            name: file.path,
            size: 0,
            status: "Queued",
            progress: 0,
            loaded: 0,
            total: 0,
            startedAt: 0,
            handle: file.handle,
            relativePath: relativePath,
        };
    });
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
    const [fileItems, setFileItems] = useState<FileUploadTableItem[]>(assetDetail.Asset || []);
    const [metadata, setMetadata] = useState<Metadata>({});
    const [previewFile, setPreviewFile] = useState<File | null>(null);
    const [previewFileError, setPreviewFileError] = useState<string | undefined>(undefined);
    const [folderPath, setFolderPath] = useState<string>("");
    const [keyPrefix, setKeyPrefix] = useState<string>("");

    // Update assetDetail when fileItems change
    useEffect(() => {
        setAssetDetail((prev) => ({
            ...prev,
            Asset: fileItems,
            isMultiFile: fileItems.length > 1,
            Preview: previewFile || undefined,
        }));
    }, [fileItems, previewFile]);

    // Extract folder information and asset details from state if available
    useEffect(() => {
        if (state) {
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

                // If we have assetId and databaseId from URL params but no assetDetailState,
                // update the assetDetail with the available information
                if (!state.assetDetailState && assetId && databaseId) {
                    setAssetDetail((prev) => ({
                        ...prev,
                        assetId: assetId,
                        databaseId: databaseId,
                    }));
                }
            }
        }
    }, [state, assetId, databaseId]);

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
    const handleFileSelection = (fileSelection: FileInfo[]) => {
        const selectedItems = convertToFileUploadTableItems(fileSelection, keyPrefix);
        setFileItems(selectedItems);
    };

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
                                        {` ${assetDetail.assetName || assetDetail.assetId}`}
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

                        {/* Show upload workflow or file selection UI */}
                        {showUploadWorkflow ? (
                            <AssetUploadWorkflow
                                assetDetail={assetDetail}
                                metadata={metadata}
                                fileItems={fileItems}
                                onComplete={handleUploadComplete}
                                onCancel={handleCancel}
                                isExistingAsset={true}
                                keyPrefix={keyPrefix}
                            />
                        ) : (
                            <>
                                {/* Selected Files */}
                                <Container header={<Header variant="h2">Selected Files</Header>}>
                                    {fileItems.length > 0 ? (
                                        <FileUploadTable
                                            allItems={fileItems}
                                            resume={false}
                                            showCount={true}
                                            allowRemoval={true}
                                            onRemoveItem={(index) => {
                                                const updatedFiles = fileItems.filter(
                                                    (item) => item.index !== index
                                                );

                                                // Update indices to be sequential
                                                const reindexedFiles = updatedFiles.map(
                                                    (item, idx) => ({
                                                        ...item,
                                                        index: idx,
                                                    })
                                                );

                                                setFileItems(reindexedFiles);
                                            }}
                                        />
                                    ) : (
                                        <Box textAlign="center" padding="l">
                                            No files selected. Please add files below.
                                        </Box>
                                    )}
                                </Container>

                                {/* File Selection */}
                                <Container header={<Header variant="h2">Add Files</Header>}>
                                    <SpaceBetween direction="vertical" size="l">
                                        <Grid
                                            gridDefinition={
                                                isRootPath
                                                    ? [{ colspan: 6 }, { colspan: 6 }]
                                                    : [{ colspan: 12 }]
                                            }
                                        >
                                            {/* Asset Files Selection */}
                                            <MultiFileSelect onChange={handleFileSelection} />

                                            {/* Preview File Selection - Only show when uploading to root path (including "/") */}
                                            {isRootPath && (
                                                <FileUpload
                                                    label="Preview File (Optional)"
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
                                                        !!previewFileError
                                                    }
                                                >
                                                    Finalize and Upload
                                                </Button>
                                            </SpaceBetween>
                                        </Box>
                                    </SpaceBetween>
                                </Container>
                            </>
                        )}
                    </div>
                </Grid>
            </SpaceBetween>
        </Box>
    );
}
