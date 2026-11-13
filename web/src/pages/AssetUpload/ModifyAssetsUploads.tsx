import {
    Box,
    BreadcrumbGroup,
    Button,
    Container,
    FormField,
    Grid,
    Input,
    SpaceBetween,
    TextContent,
} from "@cloudscape-design/components";
import Header from "@cloudscape-design/components/header";
import Alert from "@cloudscape-design/components/alert";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import React, { useEffect, useState, useMemo } from "react";
import { AssetDetail } from "./AssetUpload";
import { useLocation, useNavigate, useParams } from "react-router";
import { useSearchParams } from "react-router-dom";
import { FileUploadTableItem } from "./FileUploadTable";
import Synonyms from "../../synonyms";
import { Link } from "@cloudscape-design/components";
import { FileUpload } from "./components";
import DragDropFileUpload from "../../components/form/DragDropFileUpload";
import UploadPreview, {
    ExistingCheckState,
    blockingReason,
} from "../../components/form/UploadPreview";
import {
    ExistingState,
    FolderNameMode,
    UploadRoot,
    isSingleFolderSelection,
    markExisting,
    normalizeDestination,
    planUploadPaths,
    removeFromRoots,
} from "../../components/form/uploadPaths";
import { fetchExistingPaths, plannedToUploadItems } from "./uploadSelection";
import { previewFileFormats, PREVIEW_FILE_PATTERN } from "../../common/constants/fileFormats";
import { MAX_PREVIEW_FILE_SIZE } from "../../constants/uploadLimits";
import AssetUploadWorkflow from "./AssetUploadWorkflow";
import { Metadata } from "../../components/single/Metadata";
import { CompleteUploadResponse } from "../../services/AssetUploadService";
import { fetchAsset, fetchDatabase } from "../../services/APIService";
import {
    validateFiles,
    getPreviewFileExtension,
    isPreviewExtensionAllowed,
    ValidationResult,
} from "../../utils/fileExtensionValidation";
import { usePageTitle } from "../../hooks/usePageTitle";

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

export default function ModifyAssetsUploadsPage() {
    const location = useLocation();
    const { state } = location;
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
    usePageTitle(databaseId, assetDetail?.assetName || assetId, "Upload Files");
    const [showUploadWorkflow, setShowUploadWorkflow] = useState(false);
    const [previewFile, setPreviewFile] = useState<File | null>(null);
    const [previewFileError, setPreviewFileError] = useState<string | undefined>(undefined);

    // Destination folder: the ?folder= query parameter (survives a refresh or a deep link), else
    // the folder the page was opened from in the file manager, else the asset root.
    const [searchParams, setSearchParams] = useSearchParams();
    const [destinationInput, setDestinationInput] = useState<string>(
        () => searchParams.get("folder") ?? state?.fileTree?.relativePath ?? "/"
    );
    const { value: destination, error: destinationError } = normalizeDestination(destinationInput);

    // The selection (what the user added); the final paths are derived from it (uploadPaths.ts).
    const [roots, setRoots] = useState<UploadRoot[]>([]);
    const [folderNameMode, setFolderNameMode] = useState<FolderNameMode>("auto");
    const [existingPaths, setExistingPaths] = useState<Map<string, ExistingState> | null>(null);
    const [existingCheck, setExistingCheck] = useState<ExistingCheckState>("none");

    const planned = useMemo(
        () =>
            markExisting(
                planUploadPaths(roots, { flow: "addFiles", destination, folderNameMode }),
                existingPaths
            ),
        [roots, destination, folderNameMode, existingPaths]
    );
    const fileItems: FileUploadTableItem[] = useMemo(
        () => plannedToUploadItems(planned),
        [planned]
    );
    const pathErrorReason = blockingReason(planned);

    const [restrictFileUploadsToExtensions, setRestrictFileUploadsToExtensions] =
        useState<string>("");
    const [fileValidationResult, setFileValidationResult] = useState<ValidationResult | null>(null);

    const applySelection = (nextRoots: UploadRoot[], nextMode: FolderNameMode) => {
        // The override only applies to a single folder; reset it once the selection changes shape.
        setRoots(nextRoots);
        setFolderNameMode(isSingleFolderSelection(nextRoots) ? nextMode : "auto");
    };

    // Follow a navigation to this page with another ?folder= (or, without one, another file-manager
    // folder): the page stays mounted, so the initial state alone would keep the old destination
    // and write it back. A typed value that already means the same folder is left as typed.
    const folderParam = searchParams.get("folder");
    useEffect(() => {
        const wanted = folderParam ?? state?.fileTree?.relativePath;
        if (wanted === undefined || wanted === null) return;
        const target = normalizeDestination(wanted);
        setDestinationInput((prev) => {
            const current = normalizeDestination(prev);
            return !target.error && !current.error && current.value === target.value
                ? prev
                : wanted;
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [folderParam, location.key]);

    // Keep ?folder= in the URL in step with a valid destination.
    useEffect(() => {
        if (destinationError) return;
        if (searchParams.get("folder") === destination) return;
        const next = new URLSearchParams(searchParams);
        next.set("folder", destination);
        setSearchParams(next, { replace: true, state });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [destination, destinationError]);

    // Advisory existing-path check under the destination (debounced). The API authorizes the
    // listing; a refusal or failure only means no marks, and the API still enforces the upload.
    const hasSelection = roots.length > 0;
    useEffect(() => {
        if (!hasSelection || destinationError || !databaseId || !assetId) {
            setExistingPaths(null);
            setExistingCheck("none");
            return;
        }
        let cancelled = false;
        setExistingCheck("checking");
        const timer = setTimeout(async () => {
            const result = await fetchExistingPaths({ databaseId, assetId, destination });
            if (cancelled) return;
            setExistingPaths(result.paths);
            setExistingCheck(result.complete ? "done" : "incomplete");
        }, 400);
        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [hasSelection, destination, destinationError, databaseId, assetId]);

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
        if (destination !== "/" && previewFile) {
            setPreviewFile(null);
        }
    }, [destination, previewFile]);

    // Check for preview files in the selected files
    const hasPreviewFiles = useMemo(() => {
        return fileItems.some((item) => item.name.includes(PREVIEW_FILE_PATTERN));
    }, [fileItems]);

    // Handle preview file selection with size and extension validation
    const handlePreviewFileSelection = (file: File | null) => {
        if (file && file.size > MAX_PREVIEW_FILE_SIZE) {
            setPreviewFileError("Preview file exceeds maximum allowed size of 5MB");
            // Don't update the state with the oversized file
            return;
        }

        if (file && !isPreviewExtensionAllowed(file.name)) {
            setPreviewFileError(
                `Extension ${getPreviewFileExtension(
                    file.name
                )} is not allowed. Preview files must be one of: ${previewFileFormatsStr}`
            );
            // Don't update the state with the unsupported file
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
        // Don't proceed if there's a preview file, destination or path error
        if (previewFileError || destinationError || pathErrorReason) {
            return;
        }
        setShowUploadWorkflow(true);
    };

    // Check if we're uploading to root path
    const isRootPath = destination === "/";

    return (
        <Box padding={{ top: "xs", horizontal: "l" }}>
            <SpaceBetween size="xs" direction="vertical">
                <BreadcrumbGroup
                    items={[
                        { text: Synonyms.Databases, href: "#/databases/" },
                        {
                            text: "Search",
                            href: "#/assets/",
                        },
                        {
                            text: databaseId || "",
                            href: "#/databases/" + databaseId + "/assets/",
                        },
                        {
                            text: assetDetail?.assetName || assetId || "",
                            href: "#/databases/" + databaseId + "/assets/" + assetId,
                        },
                        { text: "Upload Files", href: "" },
                    ]}
                    ariaLabel="Breadcrumbs"
                />
                <SpaceBetween size="l" direction="vertical">
                    <Grid gridDefinition={[{ colspan: { default: 12 } }]}>
                        <SpaceBetween size="l" direction="vertical">
                            <TextContent>
                                <Header variant="h1">{`Modify ${Synonyms.Asset} Files`}</Header>
                            </TextContent>

                            {/* Asset Information */}
                            <Container
                                header={
                                    <Header variant="h2">{`${Synonyms.Asset} Information`}</Header>
                                }
                            >
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
                                        {Synonyms.Database}: {assetDetail.databaseId}
                                    </Box>
                                    <Box variant="awsui-key-label">
                                        Destination: {destinationError ? "-" : destination}
                                    </Box>
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
                                                    {`This ${Synonyms.database} has file upload restrictions in place.`}{" "}
                                                    Only files with the following extensions are
                                                    allowed:
                                                </div>
                                                <div style={{ marginTop: "8px" }}>
                                                    <strong>
                                                        {restrictFileUploadsToExtensions}
                                                    </strong>
                                                </div>
                                                <div
                                                    style={{ fontSize: "0.9em", marginTop: "8px" }}
                                                >
                                                    <em>
                                                        Note: Preview files (containing{" "}
                                                        {PREVIEW_FILE_PATTERN} in the filename) are
                                                        exempt from these restrictions, but must
                                                        still be one of {previewFileFormatsStr}.
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
                                            <div>The following files cannot be uploaded:</div>
                                            <ul style={{ marginTop: "8px", marginBottom: "8px" }}>
                                                {fileValidationResult.invalidFiles.map(
                                                    (file, index) => (
                                                        <li key={index}>
                                                            <strong>{file.fileName}</strong> -{" "}
                                                            {file.errorMessage ||
                                                                `Extension ${file.extension} not allowed`}
                                                        </li>
                                                    )
                                                )}
                                            </ul>
                                            {fileValidationResult.allowedExtensions !== null && (
                                                <div>
                                                    <strong>Allowed extensions:</strong>{" "}
                                                    {fileValidationResult.allowedExtensions.join(
                                                        ", "
                                                    )}
                                                </div>
                                            )}
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
                                    keyPrefix={destination}
                                />
                            ) : (
                                <Container header={<Header variant="h2">Select Files</Header>}>
                                    <SpaceBetween direction="vertical" size="l">
                                        <Alert header="Preview File Information" type="info">
                                            <p>
                                                Files with <strong>{PREVIEW_FILE_PATTERN}</strong>{" "}
                                                in the filename will be ingested as preview files
                                                for their associated files. For example,{" "}
                                                <code>model.gltf{PREVIEW_FILE_PATTERN}png</code>{" "}
                                                will be used as a preview for{" "}
                                                <code>model.gltf</code>.
                                            </p>
                                            <p>
                                                <strong>Important notes:</strong>
                                                <ul>
                                                    <li>
                                                        You cannot upload a preview file for a file
                                                        that is not part of this upload or is
                                                        already{" "}
                                                        {`uploaded as part of the ${Synonyms.asset}.`}
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
                                                    <li>
                                                        Preview files must be 5MB or less in size.
                                                    </li>
                                                </ul>
                                            </p>
                                            {hasPreviewFiles && (
                                                <p>
                                                    <strong>Note:</strong> Some of your selected
                                                    files will be treated as preview files based on
                                                    their filenames.
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
                                                {/* Destination and asset files selection */}
                                                <SpaceBetween direction="vertical" size="m">
                                                    <FormField
                                                        label="Destination folder"
                                                        description="Folder in the asset the files are added to. Every previewed path includes it."
                                                        errorText={destinationError}
                                                    >
                                                        <Input
                                                            value={destinationInput}
                                                            onChange={({ detail }) =>
                                                                setDestinationInput(detail.value)
                                                            }
                                                            ariaLabel="Destination folder"
                                                            placeholder="/"
                                                        />
                                                    </FormField>
                                                    <DragDropFileUpload
                                                        label={`${Synonyms.Asset} Files`}
                                                        description={
                                                            planned.length > 0
                                                                ? `Total Files to Upload: ${planned.length}`
                                                                : "Add files, a folder, or drop several at once. A folder keeps its name under the destination."
                                                        }
                                                        onAdd={(added) =>
                                                            applySelection(
                                                                [...roots, ...added],
                                                                folderNameMode
                                                            )
                                                        }
                                                    />
                                                </SpaceBetween>

                                                {/* Preview File Selection - Only show when uploading to root path (including "/") */}
                                                {isRootPath && (
                                                    <FileUpload
                                                        label={`${Synonyms.Asset} Overall Preview File (Optional)`}
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

                                        {/* Every file's final asset path, before the upload starts */}
                                        {planned.length > 0 && (
                                            <Box padding={{ bottom: "l" }}>
                                                <UploadPreview
                                                    flow="addFiles"
                                                    destination={destination}
                                                    roots={roots}
                                                    planned={planned}
                                                    folderNameMode={folderNameMode}
                                                    onFolderNameModeChange={(mode) =>
                                                        applySelection(roots, mode)
                                                    }
                                                    onRemove={(ids) =>
                                                        applySelection(
                                                            removeFromRoots(roots, ids),
                                                            folderNameMode
                                                        )
                                                    }
                                                    onRemoveAll={() => applySelection([], "auto")}
                                                    existingCheck={existingCheck}
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
                                                                ? `Please select at least one ${Synonyms.asset} file or preview file to upload.`
                                                                : `Please select at least one ${Synonyms.asset} file to upload.`}
                                                        </Box>
                                                    )}
                                                {(pathErrorReason || destinationError) && (
                                                    <Box
                                                        color="text-status-error"
                                                        fontSize="body-s"
                                                    >
                                                        {destinationError || pathErrorReason}
                                                    </Box>
                                                )}
                                                <Button
                                                    variant="primary"
                                                    onClick={startUpload}
                                                    disabled={
                                                        (fileItems.length === 0 &&
                                                            (isRootPath ? !previewFile : true)) ||
                                                        !!previewFileError ||
                                                        !!destinationError ||
                                                        !!pathErrorReason ||
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
                        </SpaceBetween>
                    </Grid>
                </SpaceBetween>
                <div style={{ paddingBottom: "20px" }} />
            </SpaceBetween>
        </Box>
    );
}
