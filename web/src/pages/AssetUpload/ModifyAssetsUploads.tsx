import {
    Box,
    BreadcrumbGroup,
    Button,
    Container,
    ExpandableSection,
    Form,
    FormField,
    Grid,
    Input,
    KeyValuePairs,
    SpaceBetween,
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
    getPreviewFileExtension,
    isPreviewExtensionAllowed,
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
    // The database's extension restriction; files it refuses are errors on their preview rows.
    const [restrictFileUploadsToExtensions, setRestrictFileUploadsToExtensions] =
        useState<string>("");

    const planned = useMemo(
        () =>
            markExisting(
                planUploadPaths(roots, {
                    flow: "addFiles",
                    destination,
                    folderNameMode,
                    restrictFileUploadsToExtensions,
                }),
                existingPaths
            ),
        [roots, destination, folderNameMode, existingPaths, restrictFileUploadsToExtensions]
    );
    const fileItems: FileUploadTableItem[] = useMemo(
        () => plannedToUploadItems(planned),
        [planned]
    );
    const pathErrorReason = blockingReason(planned);

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

    // The preview-file guidance is expanded by default and reopens when the selection gains
    // preview files; the user can still close it.
    const [guidanceExpanded, setGuidanceExpanded] = useState(true);
    useEffect(() => {
        if (hasPreviewFiles) setGuidanceExpanded(true);
    }, [hasPreviewFiles]);

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

    const hasRestrictions =
        !!restrictFileUploadsToExtensions &&
        restrictFileUploadsToExtensions.trim() !== "" &&
        restrictFileUploadsToExtensions.toLowerCase() !== ".all";

    const nothingSelected = fileItems.length === 0 && (isRootPath ? !previewFile : true);
    const uploadDisabled =
        nothingSelected ||
        !!previewFileError ||
        !!destinationError ||
        // Path and extension errors on the preview rows (D3).
        !!pathErrorReason;
    // The reason shown next to Finalize and Upload while it is disabled for one of these causes.
    const uploadBlockedReason = nothingSelected
        ? isRootPath
            ? `Please select at least one ${Synonyms.asset} file or preview file to upload.`
            : `Please select at least one ${Synonyms.asset} file to upload.`
        : destinationError || pathErrorReason;

    const assetLink = (
        <Link href={`#/databases/${assetDetail.databaseId}/assets/${assetDetail.assetId}`}>
            {assetDetail.assetName || assetDetail.assetId || "Loading..."}
        </Link>
    );

    const pageHeader = (
        <SpaceBetween direction="vertical" size="s">
            <Header
                variant="h1"
                description={`Add files to an existing ${Synonyms.asset}. Each file's final path is shown before the upload starts.`}
            >
                {`Modify ${Synonyms.Asset} Files`}
            </Header>
            <KeyValuePairs
                columns={3}
                items={[
                    { label: Synonyms.Asset, value: assetLink },
                    { label: Synonyms.Database, value: assetDetail.databaseId || "-" },
                    { label: "Destination", value: destinationError ? "-" : destination },
                ]}
            />
        </SpaceBetween>
    );

    const previewGuidance = (
        <ExpandableSection
            headerText="About preview files"
            expanded={guidanceExpanded}
            onChange={({ detail }) => setGuidanceExpanded(detail.expanded)}
            data-testid="preview-file-guidance"
        >
            <SpaceBetween direction="vertical" size="xs">
                <Box variant="p">
                    Files with <strong>{PREVIEW_FILE_PATTERN}</strong> in the filename are ingested
                    as preview files for their associated files. For example,{" "}
                    <code>model.gltf{PREVIEW_FILE_PATTERN}png</code> is used as a preview for{" "}
                    <code>model.gltf</code>.
                </Box>
                <ul style={{ margin: 0 }}>
                    <li>
                        You cannot upload a preview file for a file that is not part of this upload
                        or is already {`uploaded as part of the ${Synonyms.asset}.`}
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
                {hasPreviewFiles && (
                    <Box variant="p">
                        <strong>Note:</strong> Some of your selected files will be treated as
                        preview files based on their filenames.
                    </Box>
                )}
            </SpaceBetween>
        </ExpandableSection>
    );

    const restrictionsNotice = hasRestrictions && (
        <Alert type="warning" header="File upload restrictions">
            {`This ${Synonyms.database} only allows these extensions: `}
            <strong>{restrictFileUploadsToExtensions}</strong>. Preview files (containing{" "}
            {PREVIEW_FILE_PATTERN} in the filename) are exempt, but must still be one of{" "}
            {previewFileFormatsStr}.
        </Alert>
    );

    const formActions = (
        <SpaceBetween direction="horizontal" size="xs" alignItems="center">
            {uploadDisabled && uploadBlockedReason && (
                <Box color="text-status-error" fontSize="body-s">
                    {uploadBlockedReason}
                </Box>
            )}
            <Button variant="link" onClick={handleViewAsset}>
                Cancel
            </Button>
            <Button variant="primary" onClick={startUpload} disabled={uploadDisabled}>
                Finalize and Upload
            </Button>
        </SpaceBetween>
    );

    return (
        <Box padding={{ top: "xs", horizontal: "l", bottom: "l" }}>
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
                {showUploadWorkflow ? (
                    <SpaceBetween size="l" direction="vertical">
                        {pageHeader}
                        <AssetUploadWorkflow
                            assetDetail={assetDetail}
                            metadata={{}} // Pass empty metadata object to ensure the step is skipped
                            fileItems={fileItems}
                            onComplete={handleUploadComplete}
                            onCancel={handleCancel}
                            isExistingAsset={true}
                            keyPrefix={destination}
                        />
                    </SpaceBetween>
                ) : (
                    <Form header={pageHeader} actions={formActions}>
                        <SpaceBetween direction="vertical" size="l">
                            <Container header={<Header variant="h2">Select files</Header>}>
                                {/* Two columns when the container is wide enough, one below. */}
                                <Grid
                                    gridDefinition={[
                                        { colspan: { default: 12, xs: 7 } },
                                        { colspan: { default: 12, xs: 5 } },
                                    ]}
                                >
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
                                                applySelection([...roots, ...added], folderNameMode)
                                            }
                                        />
                                    </SpaceBetween>

                                    <SpaceBetween direction="vertical" size="m">
                                        {/* The overall preview applies to the asset root only. */}
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
                                        {restrictionsNotice}
                                        {previewGuidance}
                                    </SpaceBetween>
                                </Grid>
                            </Container>

                            {/* Every file's final asset path, before the upload starts */}
                            {planned.length > 0 && (
                                <UploadPreview
                                    flow="addFiles"
                                    destination={destination}
                                    roots={roots}
                                    planned={planned}
                                    folderNameMode={folderNameMode}
                                    onFolderNameModeChange={(mode) => applySelection(roots, mode)}
                                    onRemove={(ids) =>
                                        applySelection(removeFromRoots(roots, ids), folderNameMode)
                                    }
                                    onRemoveAll={() => applySelection([], "auto")}
                                    existingCheck={existingCheck}
                                />
                            )}
                        </SpaceBetween>
                    </Form>
                )}
            </SpaceBetween>
        </Box>
    );
}
