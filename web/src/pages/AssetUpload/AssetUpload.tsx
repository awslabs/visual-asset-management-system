/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, {
    createContext,
    Dispatch,
    useContext,
    useEffect,
    useReducer,
    useState,
    useMemo,
    useCallback,
    useRef,
} from "react";
import {
    Box,
    Button,
    ColumnLayout,
    Container,
    FormField,
    Grid,
    Header,
    Input,
    Modal,
    ProgressBarProps,
    Select,
    SpaceBetween,
    StatusIndicatorProps,
    Textarea,
    TextContent,
    Toggle,
    Wizard,
    Multiselect,
    Form,
    Link,
    Table,
} from "@cloudscape-design/components";
import { useNavigate } from "react-router";
import DatabaseSelector from "../../components/selectors/DatabaseSelector";
import { previewFileFormats } from "../../common/constants/fileFormats";
import { Metadata } from "../../components/single/Metadata";
import { OptionDefinition } from "@cloudscape-design/components/internal/components/option/interfaces";
import { validateNonZeroLengthTextAsYouType, validateRequiredTagTypeSelected } from "./validations";
import { DisplayKV, FileUpload } from "./components";
import AssetUploadWorkflow from "./AssetUploadWorkflow";
import { MetadataContainer } from "../../components/metadataV2";
import Synonyms from "../../synonyms";
import onSubmit, { onUploadRetry, UploadExecutionProps } from "./onSubmit";
import DragDropFileUpload from "../../components/form/DragDropFileUpload";
import { FileUploadTable, FileUploadTableItem, shortenBytes } from "./FileUploadTable";
import localforage from "localforage";
import { fetchTags, fetchtagTypes } from "../../services/APIService";
import { featuresEnabled } from "../../common/constants/featuresEnabled";
import { TagType } from "../Tag/TagType.interface";
import { AssetLinksTab } from "../../components/asset/tabs/AssetLinksTab";
import Alert from "@cloudscape-design/components/alert";
import { safeGetFile } from "../../utils/fileHandleCompat";
import {
    validateFiles,
    formatValidationErrors,
    ValidationResult,
} from "../../utils/fileExtensionValidation";

const previewFileFormatsStr = previewFileFormats.join(", ");
var tags: any[] = [];
var tagTypes: TagType[] = [];
var assetOptions: { label: string; value: string }[] = [];
var assetTags: string[] = [];

export class AssetDetail {
    isMultiFile: boolean = false;
    assetId?: string;
    assetName?: string;
    databaseId?: string;
    restrictMetadataOutsideSchemas?: boolean;
    restrictFileUploadsToExtensions?: string;
    description?: string;
    frontendTags?: { label: string; value: string }[];
    tags?: string[];
    assetLinksFe?: {
        parents?: any[];
        child?: any[];
        related?: any[];
    };
    assetLinks?: {
        parents?: any[];
        child?: any[];
        related?: any[];
    };
    assetLinksMetadata?: {
        parents?: { [assetId: string]: any[] };
        child?: { [assetId: string]: any[] };
        related?: { [assetId: string]: any[] };
    };
    key?: string;
    isDistributable?: boolean;
    specifiedPipelines?: string[];
    previewLocation?: {
        Key?: string;
    };
    Asset?: FileUploadTableItem[];
    DirectoryHandle?: any;
    Preview?: File | null;
}

type UpdateAssetIdAction = {
    type: "UPDATE_ASSET_ID";
    payload: string;
};

type UpdateAssetDatabaseAction = {
    type: "UPDATE_ASSET_DATABASE";
    payload: {
        databaseId: string;
        restrictMetadataOutsideSchemas?: boolean;
        restrictFileUploadsToExtensions?: string;
    };
};

type UpdateAssetDistributableAction = {
    type: "UPDATE_ASSET_DISTRIBUTABLE";
    payload: boolean;
};

type UpdateAssetDescription = {
    type: "UPDATE_ASSET_DESCRIPTION";
    payload: string;
};
type UpdateAssetTags = {
    type: "UPDATE_ASSET_TAGS";
    payload: string[];
};
type UpdateAssetFrontendTags = {
    type: "UPDATE_ASSET_FRONTEND_TAGS";
    payload: { label: string; value: string }[];
};
type UpdateAssetLinksFe = {
    type: "UPDATE_ASSET_LINKS_FE";
    payload: {
        parents: string[];
        child: string[];
        related: string[];
    };
};
type UpdateAssetLinks = {
    type: "UPDATE_ASSET_LINKs";
    payload: {
        parents: string[];
        child: string[];
        related: string[];
    };
};

type UpdateAssetLinksMetadata = {
    type: "UPDATE_ASSET_LINKS_METADATA";
    payload: {
        parents?: { [assetId: string]: any[] };
        child?: { [assetId: string]: any[] };
        related?: { [assetId: string]: any[] };
    };
};

type UpdateAssetType = {
    type: "UPDATE_ASSET_TYPE";
    payload: string;
};

type UpdateAssetPipelines = {
    type: "UPDATE_ASSET_PIPELINES";
    payload: string[];
};

type UpdateAssetPreviewLocation = {
    type: "UPDATE_ASSET_PREVIEW_LOCATION";
    payload: {
        Key?: string;
    };
};

type UpdateAssetPreview = {
    type: "UPDATE_ASSET_PREVIEW";
    payload: File | null;
};

type UpdateAssetDirectoryHandle = {
    type: "UPDATE_ASSET_DIRECTORY_HANDLE";
    payload: any;
};

type UpdateAssetFiles = {
    type: "UPDATE_ASSET_FILES";
    payload: FileUploadTableItem[];
};

type UpdateAssetName = {
    type: "UPDATE_ASSET_NAME";
    payload: string;
};

type UpdateAssetKey = {
    type: "UPDATE_ASSET_KEY";
    payload: string;
};

type UpdateAssetIsMultiFile = {
    type: "UPDATE_ASSET_IS_MULTI_FILE";
    payload: boolean;
};

type AssetDetailAction =
    | UpdateAssetIdAction
    | UpdateAssetDatabaseAction
    | UpdateAssetDistributableAction
    | UpdateAssetDescription
    | UpdateAssetTags
    | UpdateAssetFrontendTags
    | UpdateAssetLinksFe
    | UpdateAssetLinks
    | UpdateAssetLinksMetadata
    | UpdateAssetType
    | UpdateAssetPipelines
    | UpdateAssetPreviewLocation
    | UpdateAssetPreview
    | UpdateAssetDirectoryHandle
    | UpdateAssetFiles
    | UpdateAssetName
    | UpdateAssetKey
    | UpdateAssetIsMultiFile;

const assetDetailReducer = (
    assetDetailState: AssetDetail,
    assetDetailAction: AssetDetailAction
): AssetDetail => {
    switch (assetDetailAction.type) {
        case "UPDATE_ASSET_ID":
            return {
                ...assetDetailState,
                assetId: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_DATABASE":
            return {
                ...assetDetailState,
                databaseId: assetDetailAction.payload.databaseId,
                restrictMetadataOutsideSchemas:
                    assetDetailAction.payload.restrictMetadataOutsideSchemas || false,
                restrictFileUploadsToExtensions:
                    assetDetailAction.payload.restrictFileUploadsToExtensions || "",
            };
        case "UPDATE_ASSET_DISTRIBUTABLE":
            return {
                ...assetDetailState,
                isDistributable: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_DESCRIPTION":
            return {
                ...assetDetailState,
                description: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_TAGS":
            return {
                ...assetDetailState,
                tags: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_FRONTEND_TAGS":
            return {
                ...assetDetailState,
                frontendTags: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_LINKS_FE":
            return {
                ...assetDetailState,
                assetLinksFe: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_LINKs":
            return {
                ...assetDetailState,
                assetLinks: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_LINKS_METADATA":
            return {
                ...assetDetailState,
                assetLinksMetadata: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_PIPELINES":
            return {
                ...assetDetailState,
                specifiedPipelines: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_PREVIEW_LOCATION":
            return {
                ...assetDetailState,
                previewLocation: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_PREVIEW":
            return {
                ...assetDetailState,
                Preview: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_DIRECTORY_HANDLE":
            return {
                ...assetDetailState,
                DirectoryHandle: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_FILES":
            return {
                ...assetDetailState,
                Asset: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_NAME":
            return {
                ...assetDetailState,
                assetName: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_KEY":
            return {
                ...assetDetailState,
                key: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_IS_MULTI_FILE":
            return {
                ...assetDetailState,
                isMultiFile: assetDetailAction.payload,
            };
        default:
            return assetDetailState;
    }
};

type AssetDetailContextType = {
    assetDetailState: AssetDetail;
    assetDetailDispatch: Dispatch<AssetDetailAction>;
};

const AssetDetailContext = createContext<AssetDetailContextType | undefined>(undefined);

const isDistributableOptions: OptionDefinition[] = [
    { label: "Yes", value: "true" },
    { label: "No", value: "false" },
];

const CancelButtonModal = ({
    onDismiss,
    visible,
}: {
    onDismiss: (dismiss: boolean) => void;
    visible: boolean;
}) => {
    const navigate = useNavigate();
    return (
        <Modal
            onDismiss={() => onDismiss(false)}
            visible={visible}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={() => onDismiss(false)}>
                            Cancel
                        </Button>
                        <Button variant="primary" onClick={() => navigate("/assets")}>
                            Ok
                        </Button>
                    </SpaceBetween>
                </Box>
            }
            header="Do you want to cancel"
        >
            All unsaved changes will be lost
        </Modal>
    );
};

interface AssetPrimaryInfoProps {
    setValid: (validity: boolean) => void;
    showErrors: boolean;
}
interface AssetLinkingProps {
    setValid: (validity: boolean) => void;
    showErrors: boolean;
}

const AssetPrimaryInfo = ({ setValid, showErrors }: AssetPrimaryInfoProps) => {
    const assetDetailContext = useContext(AssetDetailContext) as AssetDetailContextType;
    const { assetDetailState, assetDetailDispatch } = assetDetailContext;
    const [validationText, setValidationText] = useState<{
        assetId?: string;
        databaseId?: string;
        description?: string;
        tags?: string;
    }>({});
    const [selectedTags, setSelectedTags] = useState<OptionDefinition[]>(
        assetDetailState.frontendTags as OptionDefinition[]
    );
    const [constraintText, setConstraintText] = useState<{
        tags?: string;
    }>({});
    const [tagsValid, setTagsValid] = useState(true);

    useEffect(() => {
        if (!assetDetailState.tags) {
            assetDetailDispatch({
                type: "UPDATE_ASSET_TAGS",
                payload: [],
            });
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        const validation = {
            assetId: validateNonZeroLengthTextAsYouType(assetDetailState.assetId),
            databaseId: validateNonZeroLengthTextAsYouType(assetDetailState.databaseId),
            description: validateNonZeroLengthTextAsYouType(assetDetailState.description),
            tags: validateRequiredTagTypeSelected(assetDetailState.tags, tagTypes),
        };
        setValidationText(validation);

        // Handle displaying error when tag is selected
        if (validation.tags && assetDetailState?.tags?.length) {
            // If the validation.tags string contains text and there are selected tags, it is not valid
            setTagsValid(false);
        }

        const isValid = !(
            validation.assetId ||
            validation.databaseId ||
            validation.description ||
            validation.tags
        );
        setValid(isValid);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [
        assetDetailState.assetId,
        assetDetailState.databaseId,
        assetDetailState.description,
        assetDetailState.tags,
    ]);

    useEffect(() => {
        // Get Tag Types to enforce when they are required
        tagTypes = [];

        fetchtagTypes().then((res) => {
            tagTypes = res;

            if (tagTypes.length) {
                const requiredTagTypes = tagTypes.filter((tagType) => tagType.required === "True");

                if (requiredTagTypes.length) {
                    // Set constraint text if there are required tag types
                    setConstraintText({
                        tags:
                            "The following tag types, listed in parentheses, require at least one selection: " +
                            requiredTagTypes.map((tagType) => tagType.tagTypeName).join(", "),
                    });

                    // Set initial validation text
                    setValidationText({
                        ...validationText,
                        tags: validateRequiredTagTypeSelected(assetDetailState.tags, tagTypes),
                    });
                }
            }
        });
    }, []);

    return (
        <Container header={<Header variant="h2">{Synonyms.Asset} Details</Header>}>
            <SpaceBetween direction="vertical" size="l">
                <FormField
                    label={`${Synonyms.Asset} Name`}
                    errorText={showErrors && validationText.assetId}
                >
                    <Input
                        value={assetDetailState.assetId || ""}
                        data-testid="assetid-input"
                        onChange={(e) => {
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_ID",
                                payload: e.detail.value,
                            });
                            // Also update the assetName property
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_NAME",
                                payload: e.detail.value,
                            });
                        }}
                    />
                </FormField>

                <FormField
                    label={Synonyms.Database}
                    errorText={showErrors && validationText.databaseId}
                >
                    <DatabaseSelector
                        onChange={(x: any) => {
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_DATABASE",
                                payload: {
                                    databaseId: x.detail.selectedOption.value,
                                    restrictMetadataOutsideSchemas:
                                        x.detail.selectedDatabase?.restrictMetadataOutsideSchemas ||
                                        false,
                                    restrictFileUploadsToExtensions:
                                        x.detail.selectedDatabase
                                            ?.restrictFileUploadsToExtensions || "",
                                },
                            });
                        }}
                        selectedOption={{
                            label: assetDetailState.databaseId,
                            value: assetDetailState.databaseId,
                        }}
                        data-testid="database-selector"
                    />
                </FormField>

                <FormField label="Is Distributable?">
                    <Select
                        options={isDistributableOptions}
                        selectedOption={
                            isDistributableOptions
                                .filter(
                                    (o) =>
                                        (assetDetailState.isDistributable === false
                                            ? "No"
                                            : "Yes") === o.label
                                )
                                .pop() || isDistributableOptions[0]
                        }
                        onChange={({ detail }) => {
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_DISTRIBUTABLE",
                                payload: detail.selectedOption.label === "Yes",
                            });
                        }}
                        filteringType="auto"
                        selectedAriaLabel="Selected"
                        data-testid="isDistributable-select"
                    />
                </FormField>

                <FormField
                    label="Description"
                    constraintText="Minimum 4 characters"
                    errorText={showErrors && validationText.description}
                >
                    <Textarea
                        value={assetDetailState.description || ""}
                        onChange={(e) => {
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_DESCRIPTION",
                                payload: e.detail.value,
                            });
                        }}
                        data-testid="asset-description-textarea"
                    />
                </FormField>

                <FormField
                    label="Tags"
                    constraintText={constraintText.tags}
                    errorText={showErrors || !tagsValid ? validationText.tags : null}
                >
                    <Multiselect
                        selectedOptions={selectedTags}
                        onChange={({ detail }) => {
                            assetTags = [];
                            detail.selectedOptions.forEach((x: any) => {
                                assetTags.push(x.value);
                            });
                            setSelectedTags(detail.selectedOptions as OptionDefinition[]);
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_TAGS",
                                payload: assetTags,
                            });
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_FRONTEND_TAGS",
                                payload: detail.selectedOptions as {
                                    label: string;
                                    value: string;
                                }[],
                            });
                        }}
                        placeholder="Tags"
                        options={tags}
                    />
                </FormField>
            </SpaceBetween>
        </Container>
    );
};

const AssetLinkingInfo = ({ setValid, showErrors }: AssetLinkingProps) => {
    const assetDetailContext = useContext(AssetDetailContext) as AssetDetailContextType;
    const { assetDetailState, assetDetailDispatch } = assetDetailContext;

    // Use ref to track previous data and prevent update loops
    const prevAssetLinksRef = useRef<string>();
    const isInitializedRef = useRef(false);

    // Handle asset links change from the new component - use useCallback to prevent recreation
    const handleAssetLinksChange = useCallback(
        (newAssetLinks: any) => {
            // Serialize the new data to compare with previous
            const newDataStr = JSON.stringify(newAssetLinks);

            // Only update if data actually changed
            if (prevAssetLinksRef.current === newDataStr) {
                return;
            }

            console.log(
                "handleAssetLinksChange called with:",
                JSON.stringify(newAssetLinks, null, 2)
            );

            prevAssetLinksRef.current = newDataStr;

            // Update the asset detail state with the new links and metadata
            assetDetailDispatch({
                type: "UPDATE_ASSET_LINKS_FE",
                payload: newAssetLinks.assetLinksFe,
            });
            assetDetailDispatch({
                type: "UPDATE_ASSET_LINKs",
                payload: newAssetLinks.assetLinks,
            });

            // Store the metadata in the asset detail state using proper dispatch
            if (newAssetLinks.assetLinksMetadata) {
                console.log("Updating asset links metadata:", newAssetLinks.assetLinksMetadata);
                assetDetailDispatch({
                    type: "UPDATE_ASSET_LINKS_METADATA",
                    payload: newAssetLinks.assetLinksMetadata,
                });
            }
        },
        [assetDetailDispatch]
    );

    // Convert existing data to new format - only create once on mount
    const initialData = useMemo(() => {
        // Only create initial data once when component mounts
        if (isInitializedRef.current) {
            // Return a stable reference after initialization
            return {
                assetLinksFe: {
                    parents: assetDetailState.assetLinksFe?.parents || [],
                    child: assetDetailState.assetLinksFe?.child || [],
                    related: assetDetailState.assetLinksFe?.related || [],
                },
                assetLinks: {
                    parents: assetDetailState.assetLinks?.parents || [],
                    child: assetDetailState.assetLinks?.child || [],
                    related: assetDetailState.assetLinks?.related || [],
                },
                assetLinksMetadata: {
                    parents: assetDetailState.assetLinksMetadata?.parents || {},
                    child: assetDetailState.assetLinksMetadata?.child || {},
                    related: assetDetailState.assetLinksMetadata?.related || {},
                },
            };
        }

        // First time initialization
        const data = {
            assetLinksFe: {
                parents: assetDetailState.assetLinksFe?.parents || [],
                child: assetDetailState.assetLinksFe?.child || [],
                related: assetDetailState.assetLinksFe?.related || [],
            },
            assetLinks: {
                parents: assetDetailState.assetLinks?.parents || [],
                child: assetDetailState.assetLinks?.child || [],
                related: assetDetailState.assetLinks?.related || [],
            },
            assetLinksMetadata: {
                parents: assetDetailState.assetLinksMetadata?.parents || {},
                child: assetDetailState.assetLinksMetadata?.child || {},
                related: assetDetailState.assetLinksMetadata?.related || {},
            },
        };

        isInitializedRef.current = true;
        prevAssetLinksRef.current = JSON.stringify(data);
        return data;
    }, []); // Empty dependency array - only create once

    return (
        <AssetLinksTab
            mode="upload"
            setValid={setValid}
            showErrors={showErrors}
            onAssetLinksChange={handleAssetLinksChange}
            initialData={initialData}
            databaseId={assetDetailState.databaseId}
            restrictMetadataOutsideSchemas={assetDetailState.restrictMetadataOutsideSchemas}
        />
    );
};

const AssetMetadataInfo = ({
    metadata,
    setMetadata,
    showErrors,
    setValid,
}: {
    metadata: Metadata;
    setMetadata: (metadata: Metadata) => void;
    showErrors: boolean;
    setValid: (v: boolean) => void;
}) => {
    const assetDetailContext = useContext(AssetDetailContext) as AssetDetailContextType;
    const { assetDetailState } = assetDetailContext;
    const [hasPendingChanges, setHasPendingChanges] = useState(false);
    const [hasRequiredFieldsFilled, setHasRequiredFieldsFilled] = useState(true);

    // Memoize the metadata array to prevent creating new array on every render
    const metadataArray = useMemo(() => {
        return Object.entries(metadata).map(([key, value]) => {
            // Check if value is an object with metadataValue and metadataValueType
            if (typeof value === "object" && value !== null && "metadataValue" in value) {
                return {
                    metadataKey: key,
                    metadataValue: (value as any).metadataValue,
                    metadataValueType: (value as any).metadataValueType || "string",
                };
            }
            // Otherwise treat as simple string value
            return {
                metadataKey: key,
                metadataValue: String(value),
                metadataValueType: "string" as const,
            };
        });
    }, [metadata]);

    // Memoize the change handler to prevent creating new function on every render
    const handleMetadataChange = useCallback(
        (data: any[]) => {
            const metadataObj: Metadata = {};
            data.forEach((item) => {
                if (item.metadataKey) {
                    // Store the full metadata record (with type) as the value
                    metadataObj[item.metadataKey] = {
                        metadataValue: item.metadataValue || "",
                        metadataValueType: item.metadataValueType || "string",
                    } as any;
                }
            });
            setMetadata(metadataObj);
        },
        [setMetadata]
    );

    // Handle changes to pending changes state from MetadataContainer
    // Use useCallback with empty deps to ensure stable reference
    const handleHasChangesChange = useCallback((hasChanges: boolean) => {
        setHasPendingChanges(hasChanges);
    }, []);

    // Handle changes to validation state from MetadataContainer
    // Use useCallback with empty deps to ensure stable reference
    const handleValidationChange = useCallback((isValid: boolean) => {
        setHasRequiredFieldsFilled(isValid);
    }, []);

    // Use ref to track previous validation state to prevent unnecessary setValid calls
    const prevValidRef = useRef<boolean>();

    // Update validation state based on both pending changes AND required fields
    useEffect(() => {
        // Invalid if:
        // 1. There are pending changes that need to be committed, OR
        // 2. Required fields are not filled
        // Valid only if no pending changes AND all required fields are filled
        const isValid = !hasPendingChanges && hasRequiredFieldsFilled;

        // Only call setValid if the value actually changed
        if (prevValidRef.current !== isValid) {
            prevValidRef.current = isValid;
            setValid(isValid);
        }
    }, [hasPendingChanges, hasRequiredFieldsFilled, setValid]);

    return (
        <MetadataContainer
            entityType="asset"
            entityId={assetDetailState.assetId || "temp-asset-id"}
            databaseId={assetDetailState.databaseId}
            mode="offline"
            initialData={metadataArray}
            onDataChange={handleMetadataChange}
            onHasChangesChange={handleHasChangesChange}
            onValidationChange={handleValidationChange}
            restrictMetadataOutsideSchemas={
                assetDetailState.restrictMetadataOutsideSchemas || false
            }
        />
    );
};

const getFilesFromFileHandles = async (fileHandles: any[]) => {
    const fileUploadTableItems: FileUploadTableItem[] = [];
    for (let i = 0; i < fileHandles.length; i++) {
        try {
            // Use our safe utility to get the file regardless of handle type
            const file = await safeGetFile(fileHandles[i].handle);
            fileUploadTableItems.push({
                handle: fileHandles[i].handle,
                index: i,
                name: fileHandles[i].handle.name || file.name,
                size: file.size,
                relativePath: fileHandles[i].path,
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

// Maximum preview file size (5MB)
const MAX_PREVIEW_FILE_SIZE = 5 * 1024 * 1024;

const AssetFileInfo = ({
    setFileUploadTableItems,
    setValid,
    showErrors,
}: {
    setFileUploadTableItems: (fileUploadTableItems: FileUploadTableItem[]) => void;
    setValid: (v: boolean) => void;
    showErrors: boolean;
}) => {
    const assetDetailContext = useContext(AssetDetailContext) as AssetDetailContextType;
    const { assetDetailState, assetDetailDispatch } = assetDetailContext;
    const [selectionMode, setSelectionMode] = useState<"folder" | "files" | "both">(
        assetDetailState.isMultiFile ? "folder" : "files"
    );
    const [previewFileError, setPreviewFileError] = useState<string | undefined>(undefined);
    const [fileValidationResult, setFileValidationResult] = useState<ValidationResult | null>(null);

    // Check for preview files in the selected files
    const hasPreviewFiles = useMemo(() => {
        if (!assetDetailState.Asset) return false;
        return assetDetailState.Asset.some((item) => item.name.includes(".previewFile."));
    }, [assetDetailState.Asset]);

    // Validate files whenever Asset files or restrictions change
    useEffect(() => {
        if (assetDetailState.Asset && assetDetailState.Asset.length > 0) {
            const validationResult = validateFiles(
                assetDetailState.Asset,
                assetDetailState.restrictFileUploadsToExtensions
            );
            setFileValidationResult(validationResult);
            // Set valid to false if there are validation errors
            setValid(validationResult.isValid);
        } else {
            // No files selected, clear validation and set as valid
            setFileValidationResult(null);
            setValid(true);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [assetDetailState.Asset, assetDetailState.restrictFileUploadsToExtensions]);

    // Function to remove a file
    const handleRemoveFile = (index: number) => {
        if (assetDetailState.Asset) {
            const updatedFiles = assetDetailState.Asset.filter((item) => item.index !== index);

            // Update indices to be sequential
            const reindexedFiles = updatedFiles.map((item, idx) => ({
                ...item,
                index: idx,
            }));

            setFileUploadTableItems(reindexedFiles);
            assetDetailDispatch({
                type: "UPDATE_ASSET_FILES",
                payload: reindexedFiles,
            });
        }
    };

    // Function to remove all files
    const handleRemoveAllFiles = () => {
        setFileUploadTableItems([]);
        assetDetailDispatch({
            type: "UPDATE_ASSET_FILES",
            payload: [],
        });
    };

    // Function to handle preview file selection with size validation
    const handlePreviewFileSelection = (file: File | null) => {
        if (file && file.size > MAX_PREVIEW_FILE_SIZE) {
            setPreviewFileError("Preview file exceeds maximum allowed size of 5MB");
            // Don't update the state with the oversized file
            return;
        }

        // Clear any previous error
        setPreviewFileError(undefined);

        // Update the state with the valid file
        assetDetailDispatch({
            type: "UPDATE_ASSET_PREVIEW",
            payload: file,
        });
    };

    return (
        <Container>
            <SpaceBetween direction="vertical" size="l">
                {/* Display warning about file extension restrictions if they exist */}
                {assetDetailState.restrictFileUploadsToExtensions &&
                    assetDetailState.restrictFileUploadsToExtensions.trim() !== "" &&
                    assetDetailState.restrictFileUploadsToExtensions.toLowerCase() !== ".all" && (
                        <Alert header="File Upload Restrictions" type="warning">
                            <SpaceBetween direction="vertical" size="xs">
                                <div>
                                    This database has file upload restrictions in place. Only files
                                    with the following extensions are allowed:
                                </div>
                                <div style={{ marginTop: "8px" }}>
                                    <strong>
                                        {assetDetailState.restrictFileUploadsToExtensions}
                                    </strong>
                                </div>
                                <div style={{ fontSize: "0.9em", marginTop: "8px" }}>
                                    <em>
                                        Note: Preview files (containing .previewFile. in the
                                        filename) are exempt from these restrictions.
                                    </em>
                                </div>
                            </SpaceBetween>
                        </Alert>
                    )}

                {/* Display file extension validation errors */}
                {fileValidationResult && !fileValidationResult.isValid && (
                    <Alert header="Invalid Files Selected" type="error">
                        <SpaceBetween direction="vertical" size="xs">
                            <div>
                                The following files cannot be uploaded because their extensions are
                                not allowed for this database:
                            </div>
                            <ul style={{ marginTop: "8px", marginBottom: "8px" }}>
                                {fileValidationResult.invalidFiles.map((file, index) => (
                                    <li key={index}>
                                        <strong>{file.fileName}</strong> - Extension{" "}
                                        {file.extension} not allowed
                                    </li>
                                ))}
                            </ul>
                            <div>
                                <strong>Allowed extensions:</strong>{" "}
                                {fileValidationResult.allowedExtensions?.join(", ")}
                            </div>
                        </SpaceBetween>
                    </Alert>
                )}

                <Alert header="Preview File Information" type="info">
                    <p>
                        Files with <strong>.previewFile.</strong> in the filename will be ingested
                        as preview files for their associated files. For example,{" "}
                        <code>model.gltf.previewFile.png</code> will be used as a preview for{" "}
                        <code>model.gltf</code>.
                    </p>
                    <p>
                        <strong>Important notes:</strong>
                        <ul>
                            <li>
                                You cannot upload a preview file for a file that is not part of this
                                upload or is already uploaded as part of the asset.
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
                            <strong>Note:</strong> Some of your selected files will be treated as
                            preview files based on their filenames.
                        </p>
                    )}
                </Alert>
                <Container>
                    <Grid
                        gridDefinition={[{ colspan: { default: 6 } }, { colspan: { default: 6 } }]}
                    >
                        <SpaceBetween direction="vertical" size="m">
                            <FormField
                                label="Asset Files"
                                description={
                                    assetDetailState.Asset
                                        ? `Total Files to Upload: ${assetDetailState.Asset.length}`
                                        : "Select a folder or multiple files (optional)"
                                }
                            >
                                <SpaceBetween direction="vertical" size="xs">
                                    <Toggle
                                        onChange={({ detail }) => {
                                            assetDetailDispatch({
                                                type: "UPDATE_ASSET_IS_MULTI_FILE",
                                                payload: detail.checked,
                                            });
                                            setSelectionMode(detail.checked ? "folder" : "files");
                                        }}
                                        checked={assetDetailState.isMultiFile}
                                    >
                                        {assetDetailState.isMultiFile
                                            ? "Folder Upload"
                                            : "File Upload"}
                                    </Toggle>

                                    <DragDropFileUpload
                                        label=""
                                        description=""
                                        multiFile={true}
                                        selectionMode={selectionMode}
                                        onSelect={async (
                                            directoryHandle: any,
                                            fileHandles: any[]
                                        ) => {
                                            // Get new files from the file handles
                                            const newFiles = await getFilesFromFileHandles(
                                                fileHandles
                                            );

                                            // Combine with existing files if any exist
                                            let combinedFiles = newFiles;
                                            if (
                                                assetDetailState.Asset &&
                                                assetDetailState.Asset.length > 0
                                            ) {
                                                // Create a map of existing file paths to avoid duplicates
                                                const existingFilePaths = new Map(
                                                    assetDetailState.Asset.map((item) => [
                                                        item.relativePath,
                                                        item,
                                                    ])
                                                );

                                                // Filter out any new files that would be duplicates
                                                const uniqueNewFiles = newFiles.filter(
                                                    (file) =>
                                                        !existingFilePaths.has(file.relativePath)
                                                );

                                                // Combine existing files with unique new files
                                                const existingFiles = [...assetDetailState.Asset];
                                                combinedFiles = [
                                                    ...existingFiles,
                                                    ...uniqueNewFiles.map((file, idx) => ({
                                                        ...file,
                                                        index: existingFiles.length + idx,
                                                    })),
                                                ];
                                            }

                                            setFileUploadTableItems(combinedFiles);
                                            assetDetailDispatch({
                                                type: "UPDATE_ASSET_DIRECTORY_HANDLE",
                                                payload: directoryHandle,
                                            });
                                            assetDetailDispatch({
                                                type: "UPDATE_ASSET_FILES",
                                                payload: combinedFiles,
                                            });
                                            assetDetailDispatch({
                                                type: "UPDATE_ASSET_IS_MULTI_FILE",
                                                payload: !!directoryHandle,
                                            });
                                        }}
                                    />
                                </SpaceBetween>
                            </FormField>
                        </SpaceBetween>

                        <FileUpload
                            label="Asset Overall Preview File (Optional)"
                            disabled={false}
                            setFile={handlePreviewFileSelection}
                            fileFormats={previewFileFormatsStr}
                            file={assetDetailState.Preview || undefined}
                            errorText={previewFileError}
                            description={`File types: ${previewFileFormatsStr}. Maximum allowed size: 5MB.`}
                            data-testid="preview-file"
                        />
                    </Grid>
                </Container>

                {/* Display selected files with remove option */}
                {assetDetailState.Asset && assetDetailState.Asset.length > 0 && (
                    <Box padding={{ bottom: "l" }}>
                        <FileUploadTable
                            allItems={assetDetailState.Asset}
                            resume={false}
                            showCount={true}
                            allowRemoval={true}
                            onRemoveItem={handleRemoveFile}
                            onRemoveAll={handleRemoveAllFiles}
                            displayMode="selection"
                        />
                    </Box>
                )}
            </SpaceBetween>
        </Container>
    );
};

const AssetUploadReview = ({
    metadata,
    setActiveStepIndex,
}: {
    metadata: Metadata;
    setActiveStepIndex: (step: number) => void;
}) => {
    const assetDetailContext = useContext(AssetDetailContext) as AssetDetailContextType;
    const { assetDetailState } = assetDetailContext;

    // Create a preview file item if it exists
    const previewFileItem = assetDetailState.Preview
        ? ({
              handle: { getFile: () => Promise.resolve(assetDetailState.Preview as File) },
              index: 99999, // Use a high index to distinguish from regular files
              name: assetDetailState.Preview.name,
              size: assetDetailState.Preview.size,
              relativePath: `previews/${assetDetailState.Preview.name}`,
              progress: 0,
              status: "Queued" as "Queued" | "In Progress" | "Completed" | "Failed", // Explicitly type the status
              loaded: 0,
              total: assetDetailState.Preview.size,
          } as FileUploadTableItem)
        : null;

    // Combine asset files and preview file for display
    const allFiles = [
        ...(assetDetailState.Asset || []),
        ...(previewFileItem ? [previewFileItem] : []),
    ];

    return (
        <SpaceBetween size="l">
            <Header
                variant="h3"
                actions={<Button onClick={() => setActiveStepIndex(0)}>Edit</Button>}
            >
                Review
            </Header>
            <Container header={<Header variant="h2">{Synonyms.Asset} Detail</Header>}>
                <ColumnLayout columns={2} variant="text-grid">
                    <SpaceBetween direction="vertical" size="xl">
                        {/* Left Column: Asset Name, Database, Description */}
                        <DisplayKV label="Asset Name" value={assetDetailState.assetName || ""} />
                        <DisplayKV label="Database" value={assetDetailState.databaseId || ""} />
                        <DisplayKV label="Description" value={assetDetailState.description || ""} />
                    </SpaceBetween>

                    <SpaceBetween direction="vertical" size="xl">
                        {/* Right Column: Is Distributable, Tags */}
                        <DisplayKV
                            label="Is Distributable"
                            value={assetDetailState.isDistributable === false ? "No" : "Yes"}
                        />
                        <DisplayKV
                            label="Tags"
                            value={
                                assetDetailState.frontendTags
                                    ? assetDetailState.frontendTags
                                          .map((tag: any) => tag?.label)
                                          .join(", ")
                                    : ""
                            }
                        />
                    </SpaceBetween>
                </ColumnLayout>
            </Container>
            <Container header={<Header variant="h2">Linked {Synonyms.Asset}s</Header>}>
                <ColumnLayout columns={3} variant="text-grid">
                    {assetDetailState.assetLinksFe && (
                        <>
                            {assetDetailState.assetLinksFe &&
                                Object.keys(assetDetailState.assetLinksFe).map((linkType) => {
                                    let label = linkType;
                                    if (linkType === "parents") {
                                        label = "Parent Assets";
                                    } else if (linkType === "child") {
                                        label = "Child Assets";
                                    } else if (linkType === "related") {
                                        label = "Related Assets";
                                    }

                                    const formattedValue =
                                        assetDetailState.assetLinksFe &&
                                        assetDetailState.assetLinksFe[
                                            linkType as keyof typeof assetDetailState.assetLinksFe
                                        ];

                                    return (
                                        <DisplayKV
                                            key={linkType}
                                            label={label}
                                            value={
                                                Array.isArray(formattedValue)
                                                    ? formattedValue.map((asset) => (
                                                          <div key={asset.assetId}>
                                                              {asset.assetName}
                                                          </div>
                                                      ))
                                                    : formattedValue
                                            }
                                        />
                                    );
                                })}
                        </>
                    )}
                </ColumnLayout>
            </Container>

            <Container header={<Header variant="h2">{Synonyms.Asset} Metadata</Header>}>
                <ColumnLayout columns={2} variant="text-grid">
                    {Object.keys(metadata).map((k) => {
                        const value = metadata[k as keyof Metadata];
                        // Check if value is an object with metadataValue
                        const displayValue =
                            typeof value === "object" && value !== null && "metadataValue" in value
                                ? (value as any).metadataValue
                                : String(value);
                        return <DisplayKV key={k} label={k} value={displayValue} />;
                    })}
                </ColumnLayout>
            </Container>
            {allFiles.length > 0 && (
                <FileUploadTable
                    allItems={allFiles}
                    resume={false}
                    showCount={false}
                    columnDefinitions={[
                        {
                            id: "type",
                            header: "Type",
                            cell: (item: FileUploadTableItem) => {
                                if (item.index === 99999) return "Preview File";
                                if (item.name.includes(".previewFile.")) return "Preview File";
                                return "Asset File";
                            },
                            sortingField: "type",
                            sortingComparator: (a: FileUploadTableItem, b: FileUploadTableItem) => {
                                const getType = (item: FileUploadTableItem) => {
                                    if (item.index === 99999) return "Preview File";
                                    if (item.name.includes(".previewFile.")) return "Preview File";
                                    return "Asset File";
                                };
                                return getType(a).localeCompare(getType(b));
                            },
                            isRowHeader: false,
                        },
                        {
                            id: "filepath",
                            header: "Path",
                            cell: (item: FileUploadTableItem) => item.relativePath,
                            sortingField: "relativePath",
                            sortingComparator: (a: FileUploadTableItem, b: FileUploadTableItem) =>
                                a.relativePath.localeCompare(b.relativePath),
                            isRowHeader: true,
                        },
                        {
                            id: "filesize",
                            header: "Size",
                            cell: (item: FileUploadTableItem) =>
                                item.total ? shortenBytes(item.total) : "0b",
                            sortingField: "total",
                            sortingComparator: (a: FileUploadTableItem, b: FileUploadTableItem) =>
                                a.total - b.total,
                            isRowHeader: false,
                        },
                    ]}
                />
            )}
        </SpaceBetween>
    );
};

const UploadForm = () => {
    const assetDetailContext = useContext(AssetDetailContext) as AssetDetailContextType;
    const { assetDetailState, assetDetailDispatch } = assetDetailContext;
    const [activeStepIndex, setActiveStepIndex] = useState(0);
    const [metadata, setMetadata] = useState<Metadata>({});
    const [fileUploadTableItems, setFileUploadTableItems] = useState<FileUploadTableItem[]>([]);
    const [freezeWizardButtons, setFreezeWizardButtons] = useState(false);
    const [showUploadAndExecProgress, setShowUploadAndExecProgress] = useState(false);
    const [uploadExecutionProps, setUploadExecutionProps] = useState<UploadExecutionProps>();
    const [previewUploadProgress, setPreviewUploadProgress] = useState<ProgressBarProps>({
        value: 0,
        status: "in-progress",
    });
    const [isCancelVisible, setCancelVisible] = useState(false);
    const [showErrorsForPage, setShowErrorsForPage] = useState(-1);
    const [validSteps, setValidSteps] = useState([false, false, false]);

    useEffect(() => {
        tags = [];

        fetchTags().then((res) => {
            if (res && Array.isArray(res)) {
                Object.values(res).map((x: any) => {
                    tags.push({ label: `${x.tagName} (${x.tagTypeName})`, value: x.tagName });
                });
            }
        });
        if (assetDetailState.assetId && fileUploadTableItems.length > 0) {
            assetDetailDispatch({ type: "UPDATE_ASSET_FILES", payload: fileUploadTableItems });
            localforage
                .setItem(assetDetailState.assetId, {
                    ...assetDetailState,
                    Asset: fileUploadTableItems,
                })
                .then(() => {})
                .catch(() => {
                    console.error("Error setting item in localforage");
                });
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [fileUploadTableItems]);

    const [execStatus, setExecStatus] = useState<Record<string, StatusIndicatorProps.Type>>({});

    const getUpdatedItemAfterProgress = (
        item: FileUploadTableItem,
        loaded: number,
        total: number
    ): FileUploadTableItem => {
        const progress = Math.round((loaded / total) * 100);
        const status = item.status;
        if (loaded === total) {
            return {
                ...item,
                loaded: loaded,
                total: total,
                progress: progress,
                status: "Completed",
            };
        }
        if (status === "Queued") {
            return {
                ...item,
                loaded: loaded,
                total: total,
                progress: progress,
                status: "In Progress",
                startedAt: Math.floor(new Date().getTime() / 1000),
            };
        } else {
            return {
                ...item,
                loaded: loaded,
                total: total,
                status: "In Progress",
                progress: progress,
            };
        }
    };
    const updateProgressForFileUploadItem = (index: number, loaded: number, total: number) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? getUpdatedItemAfterProgress(item, loaded, total) : item
            );
        });
    };

    const fileUploadComplete = (index: number, event: any) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? { ...item, status: "Completed", progress: 100 } : item
            );
        });
    };

    const fileUploadError = (index: number, event: any) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? { ...item, status: "Failed" } : item
            );
        });
    };
    const moveToQueued = (index: number) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? { ...item, status: "Queued" } : item
            );
        });
    };

    return (
        <Box padding={{ left: "l", right: "l" }}>
            {isCancelVisible && (
                <CancelButtonModal onDismiss={setCancelVisible} visible={isCancelVisible} />
            )}
            {showUploadAndExecProgress && (
                <>
                    <AssetUploadWorkflow
                        assetDetail={assetDetailState}
                        metadata={metadata}
                        fileItems={fileUploadTableItems}
                        onComplete={(response) => {
                            console.log("Upload completed:", response);
                            // Remove window beforeunload handler
                            window.onbeforeunload = null;
                        }}
                        onCancel={() => {
                            setShowUploadAndExecProgress(false);
                            setFreezeWizardButtons(false);
                            // Remove window beforeunload handler
                            window.onbeforeunload = null;
                        }}
                    />
                </>
            )}
            {!showUploadAndExecProgress && (
                <Wizard
                    i18nStrings={{
                        stepNumberLabel: (stepNumber) => `Step ${stepNumber}`,
                        collapsedStepsLabel: (stepNumber, stepsCount) =>
                            `Step ${stepNumber} of ${stepsCount}`,
                        skipToButtonLabel: (step, stepNumber) => `Skip to ${step.title}`,
                        navigationAriaLabel: "Steps",
                        cancelButton: "Cancel",
                        previousButton: "Previous",
                        nextButton: "Next",
                        submitButton: "Upload Object",
                        optional: "optional",
                    }}
                    isLoadingNextStep={freezeWizardButtons}
                    onCancel={(event) => {
                        setCancelVisible(true);
                    }}
                    onNavigate={({ detail }) => {
                        setShowErrorsForPage(activeStepIndex);
                        if (
                            validSteps[activeStepIndex] ||
                            activeStepIndex > detail.requestedStepIndex
                        ) {
                            setActiveStepIndex(detail.requestedStepIndex);
                        }
                    }}
                    activeStepIndex={activeStepIndex}
                    onSubmit={onSubmit({
                        assetDetail: assetDetailState,
                        setFreezeWizardButtons,
                        metadata,
                        execStatus,
                        setExecStatus,
                        setShowUploadAndExecProgress,
                        moveToQueued,
                        updateProgressForFileUploadItem,
                        fileUploadComplete,
                        fileUploadError,
                        setPreviewUploadProgress,
                        setUploadExecutionProps,
                    })}
                    allowSkipTo
                    steps={[
                        {
                            title: `${Synonyms.Asset} Details`,
                            isOptional: false,
                            content: (
                                <AssetPrimaryInfo
                                    setValid={(v: boolean) => {
                                        const newValidSteps = [...validSteps];
                                        newValidSteps[0] = v;
                                        setValidSteps(newValidSteps);
                                    }}
                                    showErrors={showErrorsForPage >= 0}
                                />
                            ),
                        },
                        {
                            title: `${Synonyms.Asset} Metadata`,
                            content: (
                                <AssetMetadataInfo
                                    setValid={(v: boolean) => {
                                        const newValidSteps = [...validSteps];
                                        newValidSteps[1] = v;
                                        setValidSteps(newValidSteps);
                                    }}
                                    showErrors={showErrorsForPage >= 1}
                                    metadata={metadata}
                                    setMetadata={setMetadata}
                                />
                            ),
                            isOptional: false,
                        },
                        {
                            title: `${Synonyms.Asset} Relationships`,
                            content: (
                                <AssetLinkingInfo
                                    setValid={(v: boolean) => {
                                        const newValidSteps = [...validSteps];
                                        newValidSteps[2] = v;
                                        setValidSteps(newValidSteps);
                                    }}
                                    showErrors={showErrorsForPage >= 2}
                                />
                            ),
                            isOptional: true,
                        },
                        {
                            title: "Select Files to upload",
                            content: (
                                <AssetFileInfo
                                    setFileUploadTableItems={setFileUploadTableItems}
                                    setValid={(v: boolean) => {
                                        const newValidSteps = [...validSteps];
                                        newValidSteps[3] = v;
                                        setValidSteps(newValidSteps);
                                    }}
                                    showErrors={showErrorsForPage >= 3}
                                />
                            ),
                            isOptional: true,
                        },
                        {
                            title: "Review and Upload",
                            content: (
                                <AssetUploadReview
                                    metadata={metadata}
                                    setActiveStepIndex={setActiveStepIndex}
                                />
                            ),
                        },
                    ]}
                />
            )}
        </Box>
    );
};

export default function AssetUploadPage() {
    const [state, dispatch] = useReducer(assetDetailReducer, {
        isMultiFile: false,
        isDistributable: true,
    });
    return (
        <AssetDetailContext.Provider
            value={{ assetDetailState: state, assetDetailDispatch: dispatch }}
        >
            <Box padding={{ top: false ? "s" : "m", horizontal: "l" }}>
                <Grid gridDefinition={[{ colspan: { default: 12 } }]}>
                    <div>
                        <TextContent>
                            <Header variant="h1">Create and Upload {Synonyms.Asset}</Header>
                        </TextContent>
                        <UploadForm />
                    </div>
                </Grid>
            </Box>
        </AssetDetailContext.Provider>
    );
}
