/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createContext, Dispatch, useContext, useEffect, useReducer, useState } from "react";
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
} from "@cloudscape-design/components";
import { useNavigate } from "react-router";
import DatabaseSelector from "../components/selectors/DatabaseSelector";
import { previewFileFormats } from "../common/constants/fileFormats";
import { Metadata } from "../components/single/Metadata";
import { OptionDefinition } from "@cloudscape-design/components/internal/components/option/interfaces";
import { validateNonZeroLengthTextAsYouType } from "./AssetUpload/validations";
import { DisplayKV, FileUpload } from "./AssetUpload/components";
import ProgressScreen from "./AssetUpload/ProgressScreen";
import ControlledMetadata from "../components/metadata/ControlledMetadata";
import Synonyms from "../synonyms";
import onSubmit, { onUploadRetry, UploadExecutionProps } from "./AssetUpload/onSubmit";
import FolderUpload from "../components/form/FolderUpload";
import { FileUploadTable, FileUploadTableItem, shortenBytes } from "./AssetUpload/FileUploadTable";
import localforage from "localforage";

const previewFileFormatsStr = previewFileFormats.join(", ");

export class AssetDetail {
    isMultiFile: boolean = false;
    assetId?: string;
    assetName?: string;
    databaseId?: string;
    description?: string;
    bucket?: string;
    key?: string;
    assetType?: string;
    isDistributable?: boolean;
    Comment?: string;
    specifiedPipelines?: string[];
    previewLocation?: {
        Bucket?: string;
        Key?: string;
    };
    Asset?: FileUploadTableItem[];
    DirectoryHandle?: any;
    Preview?: File;
}

type UpdateAssetIdAction = {
    type: "UPDATE_ASSET_ID";
    payload: string;
};

type UpdateAssetDatabaseAction = {
    type: "UPDATE_ASSET_DATABASE";
    payload: string;
};

type UpdateAssetDistributableAction = {
    type: "UPDATE_ASSET_DISTRIBUTABLE";
    payload: boolean;
};

type UpdateAssetDescription = {
    type: "UPDATE_ASSET_DESCRIPTION";
    payload: string;
};

type UpdateAssetComment = {
    type: "UPDATE_ASSET_COMMENT";
    payload: string;
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
        Bucket?: string;
        Key?: string;
    };
};

type UpdateAssetPreview = {
    type: "UPDATE_ASSET_PREVIEW";
    payload: File;
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

type UpdateAssetBucket = {
    type: "UPDATE_ASSET_BUCKET";
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
    | UpdateAssetComment
    | UpdateAssetType
    | UpdateAssetPipelines
    | UpdateAssetPreviewLocation
    | UpdateAssetPreview
    | UpdateAssetDirectoryHandle
    | UpdateAssetFiles
    | UpdateAssetName
    | UpdateAssetBucket
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
                databaseId: assetDetailAction.payload,
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

        case "UPDATE_ASSET_COMMENT":
            return {
                ...assetDetailState,
                Comment: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_TYPE":
            return {
                ...assetDetailState,
                assetType: assetDetailAction.payload,
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

        case "UPDATE_ASSET_BUCKET":
            return {
                ...assetDetailState,
                bucket: assetDetailAction.payload,
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

const AssetPrimaryInfo = ({ setValid, showErrors }: AssetPrimaryInfoProps) => {
    const assetDetailContext = useContext(AssetDetailContext) as AssetDetailContextType;
    const { assetDetailState, assetDetailDispatch } = assetDetailContext;
    const [validationText, setValidationText] = useState<{
        assetId?: string;
        databaseId?: string;
        description?: string;
        Comment?: string;
    }>({});

    // Default `Comment` to an empty string so that it's optional and passes API validation
    useEffect(() => {
        if (!assetDetailState.Comment) {
            assetDetailDispatch({
                type: "UPDATE_ASSET_COMMENT",
                payload: "",
            });
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        const validation = {
            assetId: validateNonZeroLengthTextAsYouType(assetDetailState.assetId),
            databaseId: validateNonZeroLengthTextAsYouType(assetDetailState.databaseId),
            description: validateNonZeroLengthTextAsYouType(assetDetailState.description),
            Comment: "",
        };
        setValidationText(validation);

        const isValid = !(
            validation.assetId ||
            validation.databaseId ||
            validation.description ||
            validation.Comment
        );
        setValid(isValid);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [
        assetDetailState.Comment,
        assetDetailState.assetId,
        assetDetailState.databaseId,
        assetDetailState.description,
    ]);

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
                        }}
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
                    label={Synonyms.Database}
                    errorText={showErrors && validationText.databaseId}
                >
                    <DatabaseSelector
                        onChange={(x: any) => {
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_DATABASE",
                                payload: x.detail.selectedOption.value,
                            });
                        }}
                        selectedOption={{
                            label: assetDetailState.databaseId,
                            value: assetDetailState.databaseId,
                        }}
                        data-testid="database-selector"
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

                <FormField label="Comment">
                    <Input
                        value={assetDetailState.Comment || ""}
                        onChange={(e) => {
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_COMMENT",
                                payload: e.detail.value,
                            });
                        }}
                        data-testid="asset-comment-input"
                    />
                </FormField>
            </SpaceBetween>
        </Container>
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

    return (
        <Container header={<Header variant="h2">{Synonyms.Asset} Metadata</Header>}>
            <SpaceBetween direction="vertical" size="l">
                <ControlledMetadata
                    assetId={assetDetailState.assetId || ""}
                    databaseId={assetDetailState.databaseId || ""}
                    initialState={metadata}
                    store={(databaseId, assetId, record) => {
                        return new Promise((resolve) => {
                            setMetadata(record);
                            resolve(null);
                        });
                    }}
                    showErrors={showErrors}
                    setValid={setValid}
                    data-testid="controlled-metadata-grid"
                />
            </SpaceBetween>
        </Container>
    );
};

const getFilesFromFileHandles = async (fileHandles: any[]) => {
    const fileUploadTableItems: FileUploadTableItem[] = [];
    for (let i = 0; i < fileHandles.length; i++) {
        const file = (await fileHandles[i].handle.getFile()) as File;
        fileUploadTableItems.push({
            handle: fileHandles[i].handle,
            index: i,
            name: fileHandles[i].handle.name,
            size: file.size,
            relativePath: fileHandles[i].path,
            progress: 0,
            status: "Queued",
            loaded: 0,
            total: file.size,
        });
    }
    return fileUploadTableItems;
};

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

    useEffect(() => {
        if (assetDetailState.Asset?.length && assetDetailState.Asset.length > 0) {
            setValid(true);
        } else {
            setValid(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [assetDetailState]);

    return (
        <Container header={<Header variant="h2">Select Files to Upload</Header>}>
            <>
                <FormField>
                    <Toggle
                        onChange={({ detail }) => {
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_IS_MULTI_FILE",
                                payload: detail.checked,
                            });
                        }}
                        checked={assetDetailState.isMultiFile}
                    >
                        Folder Upload?
                    </Toggle>
                </FormField>
                <Grid gridDefinition={[{ colspan: { default: 6 } }, { colspan: { default: 6 } }]}>
                    <FolderUpload
                        label={assetDetailState.isMultiFile ? "Choose Folder" : "Choose File"}
                        description={
                            assetDetailState.Asset
                                ? "Total Files to Upload " + assetDetailState.Asset.length
                                : ""
                        }
                        multiFile={assetDetailState.isMultiFile}
                        errorText={
                            (!assetDetailState.Asset && showErrors && "Asset is required") ||
                            undefined
                        }
                        onSelect={async (directoryHandle: any, fileHandles: any[]) => {
                            const files = await getFilesFromFileHandles(fileHandles);
                            setFileUploadTableItems(files);
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_DIRECTORY_HANDLE",
                                payload: directoryHandle,
                            });
                            assetDetailDispatch({ type: "UPDATE_ASSET_FILES", payload: files });
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_IS_MULTI_FILE",
                                payload: files.length > 1,
                            });
                        }}
                    ></FolderUpload>

                    <FileUpload
                        label="Preview (Optional)"
                        disabled={false}
                        setFile={(file) => {
                            assetDetailDispatch({ type: "UPDATE_ASSET_PREVIEW", payload: file });
                        }}
                        fileFormats={previewFileFormatsStr}
                        file={assetDetailState.Preview}
                        data-testid="preview-file"
                    />
                </Grid>
            </>
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

    return (
        <SpaceBetween size="xs">
            <Header
                variant="h3"
                actions={<Button onClick={() => setActiveStepIndex(0)}>Edit</Button>}
            >
                Review
            </Header>
            <Container header={<Header variant="h2">{Synonyms.Asset} Detail</Header>}>
                <ColumnLayout columns={2} variant="text-grid">
                    {Object.keys(assetDetailState)
                        .filter((k) => k !== "Asset" && k !== "DirectoryHandle")
                        .sort()
                        .map((k) => (
                            <DisplayKV
                                key={k}
                                label={k}
                                value={assetDetailState[k as keyof AssetDetail]}
                            />
                        ))}
                </ColumnLayout>
            </Container>

            <Container header={<Header variant="h2">{Synonyms.Asset} Metadata</Header>}>
                <ColumnLayout columns={2} variant="text-grid">
                    {Object.keys(metadata).map((k) => (
                        <DisplayKV key={k} label={k} value={metadata[k as keyof Metadata]} />
                    ))}
                </ColumnLayout>
            </Container>
            {assetDetailState.Asset && (
                <FileUploadTable
                    allItems={assetDetailState.Asset}
                    resume={false}
                    showCount={false}
                    columnDefinitions={[
                        {
                            id: "filepath",
                            header: "Path",
                            cell: (item: FileUploadTableItem) => item.relativePath,
                            sortingField: "filepath",
                            isRowHeader: true,
                        },
                        {
                            id: "filesize",
                            header: "Size",
                            cell: (item: FileUploadTableItem) =>
                                item.total ? shortenBytes(item.total) : "0b",
                            sortingField: "filesize",
                            isRowHeader: true,
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
            {showUploadAndExecProgress && uploadExecutionProps && (
                <>
                    <ProgressScreen
                        assetDetail={assetDetailState}
                        execStatus={execStatus}
                        previewUploadProgress={previewUploadProgress}
                        allFileUploadItems={fileUploadTableItems}
                        onRetry={() => onUploadRetry(uploadExecutionProps)}
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
                            title: "Select Files to upload",
                            content: (
                                <AssetFileInfo
                                    setFileUploadTableItems={setFileUploadTableItems}
                                    setValid={(v: boolean) => {
                                        const newValidSteps = [...validSteps];
                                        newValidSteps[2] = v;
                                        setValidSteps(newValidSteps);
                                    }}
                                    showErrors={showErrorsForPage >= 2}
                                />
                            ),
                            isOptional: false,
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
                            <Header variant="h1">Create {Synonyms.Asset}</Header>
                        </TextContent>
                        <UploadForm />
                    </div>
                </Grid>
            </Box>
        </AssetDetailContext.Provider>
    );
}
