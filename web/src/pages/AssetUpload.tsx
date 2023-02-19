/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useRef } from "react";
import {
    Box,
    ColumnLayout,
    Grid,
    Select,
    Textarea,
    TextContent,
} from "@cloudscape-design/components";
import { useParams } from "react-router";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Link from "@cloudscape-design/components/link";
import Button from "@cloudscape-design/components/button";

import Wizard from "@cloudscape-design/components/wizard";

import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";

import DatabaseSelector from "../components/selectors/DatabaseSelector";
import { formatFileSize } from "../components/form/FileUploadControl";
import {
    cadFileFormats,
    modelFileFormats,
    columnarFileFormats,
    previewFileFormats,
} from "../common/constants/fileFormats";

import MetadataTable, { Metadata, put as saveMetadata } from "../components/single/Metadata";
import { fetchDatabaseWorkflows, runWorkflow } from "../services/APIService";
import { API, Storage, Cache } from "aws-amplify";
import Table from "@cloudscape-design/components/table";
import ProgressBar, { ProgressBarProps } from "@cloudscape-design/components/progress-bar";
import StatusIndicator, {
    StatusIndicatorProps,
} from "@cloudscape-design/components/status-indicator";
import { StoragePutOutput } from "@aws-amplify/storage/lib-esm/types/Storage";
import { OptionDefinition } from "@cloudscape-design/components/internal/components/option/interfaces";

const objectFileFormats = new Array().concat(cadFileFormats, modelFileFormats, columnarFileFormats);
const objectFileFormatsStr = objectFileFormats.join(", ");
const previewFileFormatsStr = previewFileFormats.join(", ");

class FileUploadProps {
    label?: string;
    errorText?: string;
    fileFormats!: string;
    setFile!: (file: File) => void;
    file: File | undefined;
    disabled!: boolean;
}

function getLang() {
    if (navigator.languages != undefined) return navigator.languages[0];
    if (navigator.language) return navigator.language;
    return "en-us";
}
const FileUpload = ({
    errorText,
    fileFormats,
    disabled,
    setFile,
    file,
    label,
}: FileUploadProps) => {
    const inputRef = useRef<HTMLInputElement>(null);

    return (
        <FormField errorText={errorText} label={label} description={"File types: " + fileFormats}>
            <input
                ref={inputRef}
                type="file"
                accept={fileFormats}
                style={{ display: "none" }}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                    if (e.target.files && e.target.files.length > 0 && e.target.files[0]) {
                        setFile(e.target.files[0]);
                    }
                }}
            />
            <SpaceBetween size="l">
                <Button
                    disabled={disabled}
                    variant="normal"
                    iconName="upload"
                    onClick={(e) => {
                        inputRef?.current?.click();
                    }}
                >
                    Choose File
                </Button>
                <DisplayFileMeta file={file} />
            </SpaceBetween>
        </FormField>
    );
};
class DisplayFileMetaProps {
    file?: File;
}
function DisplayFileMeta({ file }: DisplayFileMetaProps) {
    return (
        <Grid gridDefinition={[{ colspan: { default: 6 } }, { colspan: { default: 6 } }]}>
            {file && (
                <>
                    <TextContent>
                        <strong>Filename:</strong>
                        <br />
                        <small>Size</small>
                        <br />
                        <small>Last Modified:</small>
                    </TextContent>
                    <TextContent>
                        <strong>{file?.name}</strong>
                        <br />
                        <small>{formatFileSize(file?.size)}</small>
                        <br />
                        <small>
                            {new Date(file?.lastModified).toLocaleDateString(getLang(), {
                                weekday: "long",
                                year: "numeric",
                                month: "short",
                                day: "numeric",
                            })}
                        </small>
                    </TextContent>
                </>
            )}
        </Grid>
    );
}

const validateEntityIdAsYouType = (s?: string): string | undefined => {
    if (!s) {
        return "Required field.";
    }

    if (!s.match(/^[a-z].*/)) {
        return "First character must be a lower case letter.";
    }

    if (s.length < 4) {
        return "Must be at least 4 characters.";
    }

    const valid = /^[a-z][a-z0-9-_]{3,63}$/;

    if (!s.match(valid)) {
        return "Invalid characters detected.";
    }
};

const validateNonZeroLengthTextAsYouType = (s?: string): string | undefined => {
    if (!s) {
        return "Required field.";
    }

    if (s.length < 4) {
        return "Must be at least 4 characters.";
    }
};

class AssetDetail {
    assetId?: string;
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
    Asset?: File;
    Preview?: File;
}

const workflowColumnDefns = [
    {
        id: "workflowId",
        header: "Workflow Id",
        cell: (e: any) => e.workflowId,
    },
    {
        id: "description",
        header: "Description",
        cell: (e: any) => e.description,
    },
    {
        id: "pipelines",
        header: "Pipelines",
        cell: (wf: any) => wf.specifiedPipelines?.functions?.map((fn: any) => fn.name).join(", "),
    },
];

const isDistributableOptions: OptionDefinition[] = [
    { label: "Yes", value: "true" },
    { label: "No", value: "false" },
];

const UploadForm = () => {
    const urlParams = useParams();
    const [databaseId, setDatabaseId] = useState({
        label: urlParams.databaseId,
        value: urlParams.databaseId,
    });
    const [activeStepIndex, setActiveStepIndex] = useState(0);

    const [assetDetail, setAssetDetail] = useState<AssetDetail>({ isDistributable: false });
    const [metadata, setMetadata] = useState<Metadata>({});

    const [workflows, setWorkflows] = useState<any>([]);
    const [selectedWorkflows, setSelectedWorkflows] = useState<any>([]);

    const [freezeWizardButtons, setFreezeWizardButtons] = useState(false);

    const [showUploadAndExecProgress, setShowUploadAndExecProgress] = useState(false);

    const [assetUploadProgress, setAssetUploadProgress] = useState<ProgressBarProps>({
        value: 0,
        status: "in-progress",
    });
    const [previewUploadProgress, setPreviewUploadProgress] = useState<ProgressBarProps>({
        value: 0,
        status: "in-progress",
    });

    const [execStatus, setExecStatus] = useState<Record<string, StatusIndicatorProps.Type>>({});

    useEffect(() => {
        if (!assetDetail?.databaseId) {
            return;
        }

        fetchDatabaseWorkflows({ databaseId: assetDetail.databaseId }).then((w) => {
            console.log("received workflows", w);
            setWorkflows(w);
        });
    }, [assetDetail.databaseId]);

    return (
        <Box padding={{ left: "l", right: "l" }}>
            {showUploadAndExecProgress && (
                <Box padding={{ top: false ? "s" : "m", horizontal: "l" }}>
                    <Grid gridDefinition={[{ colspan: { default: 12 } }]}>
                        <div>
                            <Box variant="awsui-key-label">Upload Progress</Box>
                            <ProgressBar
                                status={assetUploadProgress.status}
                                value={assetUploadProgress.value}
                                label="Asset Upload Progress"
                            />
                            <ProgressBar
                                status={previewUploadProgress.status}
                                value={previewUploadProgress.value}
                                label="Preview Upload Progress"
                            />
                            <Box variant="awsui-key-label">Exec Progress</Box>

                            {Object.keys(execStatus).map((label) => (
                                <div key={label}>
                                    <StatusIndicator type={execStatus[label]}>
                                        {label}
                                    </StatusIndicator>
                                </div>
                            ))}
                            <div>
                                <TextContent>
                                    Please do not close your browser window until processing
                                    completes.
                                </TextContent>
                            </div>
                        </div>
                    </Grid>
                </Box>
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
                    onNavigate={({ detail }) => setActiveStepIndex(detail.requestedStepIndex)}
                    activeStepIndex={activeStepIndex}
                    onSubmit={async (detail) => {
                        setFreezeWizardButtons(true);
                        if (
                            assetDetail.Asset &&
                            assetDetail.Preview &&
                            assetDetail.assetId &&
                            assetDetail.databaseId
                        ) {
                            // TODO duplicate logic with AssetFormDefinition and uploadAssetToS3
                            const config = Cache.getItem("config");
                            assetDetail.bucket = config.bucket;
                            assetDetail.assetType = "." + assetDetail.Asset.name.split(".").pop();
                            assetDetail.key = assetDetail.assetId + assetDetail.assetType;
                            assetDetail.specifiedPipelines = [];
                            assetDetail.previewLocation = {
                                Bucket: config.bucket,
                                Key:
                                    assetDetail.assetId +
                                    "." +
                                    assetDetail.Preview.name.split(".").pop(),
                            };

                            const execStatusNew: Record<string, StatusIndicatorProps.Type> = {
                                "Asset Details": "pending",
                            };
                            if (metadata && Object.keys(metadata).length > 0) {
                                execStatusNew["Metadata"] = "pending";
                            }
                            selectedWorkflows.forEach((wf: { workflowId: string }) => {
                                execStatusNew[wf.workflowId] = "pending";
                            });

                            setExecStatus(execStatusNew);

                            window.onbeforeunload = function () {
                                return "";
                            };
                            setShowUploadAndExecProgress(true);
                            const up1 = uploadAssetToS3(
                                assetDetail.Asset,
                                assetDetail.assetId,
                                {
                                    assetId: assetDetail.assetId,
                                    databaseId: assetDetail.databaseId,
                                },
                                (progress) => {
                                    setAssetUploadProgress({
                                        value: (progress.loaded / progress.total) * 100,
                                    });
                                }
                            )
                                .then((res) => {
                                    setAssetUploadProgress({
                                        status: "success",
                                        value: 100,
                                    });
                                })
                                .catch((err) => {
                                    setAssetUploadProgress({
                                        status: "error",
                                        value: 100,
                                    });
                                    return Promise.reject(err);
                                });

                            const up2 = uploadAssetToS3(
                                assetDetail.Preview,
                                assetDetail.assetId,
                                {
                                    assetId: assetDetail.assetId,
                                    databaseId: assetDetail.databaseId,
                                },
                                (progress) => {
                                    setPreviewUploadProgress({
                                        value: (progress.loaded / progress.total) * 100,
                                    });
                                }
                            )
                                .then((res) => {
                                    setPreviewUploadProgress({
                                        status: "success",
                                        value: 100,
                                    });
                                })
                                .catch((err) => {
                                    setPreviewUploadProgress({
                                        status: "error",
                                        value: 100,
                                    });
                                    return Promise.reject(err);
                                });

                            await Promise.all([up1, up2])
                                .then((uploads) => {
                                    setExecStatus({
                                        ...execStatus,
                                        "Asset Detail": "in-progress",
                                    });
                                    return API.put("api", "assets", {
                                        "Content-type": "application/json",
                                        body: assetDetail,
                                    })
                                        .then((res) => {
                                            setExecStatus((p) => ({
                                                ...p,
                                                "Asset Detail": "success",
                                            }));
                                        })
                                        .catch((err) => {
                                            console.log("err asset detail", err);
                                            setExecStatus((p) => ({
                                                ...p,
                                                "Asset Detail": "error",
                                            }));
                                            return Promise.reject(err);
                                        });
                                })
                                .then((res) => {
                                    setExecStatus((p) => ({
                                        ...p,
                                        Metadata: "in-progress",
                                    }));
                                    if (assetDetail.assetId && assetDetail.databaseId) {
                                        return saveMetadata(
                                            assetDetail.databaseId,
                                            assetDetail.assetId,
                                            metadata
                                        )
                                            .then((resul) => {
                                                setExecStatus((p) => ({
                                                    ...p,
                                                    Metadata: "success",
                                                }));
                                            })
                                            .catch((err) => {
                                                console.log("err metadata", err);
                                                setExecStatus((p) => ({
                                                    ...p,
                                                    Metadata: "error",
                                                }));
                                                return Promise.reject(err);
                                            });
                                    }
                                    return Promise.reject(
                                        "missing assetId or databaseId in assetDetail"
                                    );
                                })
                                .then((res) => {
                                    selectedWorkflows.forEach((wf: { workflowId: string }) => {
                                        execStatusNew[wf.workflowId] = "in-progress";
                                    });
                                    return Promise.all(
                                        selectedWorkflows.map((wf: { workflowId: string }) => {
                                            const wfArgs = {
                                                assetId: assetDetail.assetId,
                                                databaseId: assetDetail.databaseId,
                                                workflowId: wf.workflowId,
                                            };
                                            return runWorkflow(wfArgs)
                                                .then((result) => {
                                                    setExecStatus((previous) => {
                                                        const n = { ...previous };
                                                        n[wf.workflowId] = "success";
                                                        return n;
                                                    });
                                                })
                                                .catch((err) => {
                                                    console.log("err", wf, err);
                                                    setExecStatus((previous) => {
                                                        const n = { ...previous };
                                                        n[wf.workflowId] = "error";
                                                        return n;
                                                    });
                                                    return Promise.reject(err);
                                                });
                                        })
                                    );
                                });

                            window.onbeforeunload = null;
                        }
                        console.log("detail", detail);
                    }}
                    allowSkipTo
                    steps={[
                        {
                            title: "Asset Details",
                            info: <Link variant="info">Info</Link>,
                            description: (
                                <Box padding={{ right: "l", left: "l" }}>
                                    Each instance type includes one or more instance sizes, allowing
                                    you
                                </Box>
                            ),
                            isOptional: false,
                            content: (
                                <Container header={<Header variant="h2">Asset Details</Header>}>
                                    <SpaceBetween direction="vertical" size="l">
                                        <FormField
                                            label="Asset Name"
                                            constraintText="All lower case, no special chars or spaces except - and _ only letters for first character min 4 and max 64."
                                            errorText={validateEntityIdAsYouType(
                                                assetDetail.assetId
                                            )}
                                        >
                                            <Input
                                                value={assetDetail.assetId || ""}
                                                onChange={(e) => {
                                                    setAssetDetail((assetDetail) => ({
                                                        ...assetDetail,
                                                        assetId: e.detail.value,
                                                    }));
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
                                                                (assetDetail.isDistributable ===
                                                                true
                                                                    ? "Yes"
                                                                    : "No") === o.label
                                                        )
                                                        .pop() || null
                                                }
                                                onChange={({ detail }) => {
                                                    setAssetDetail((assetDetail) => ({
                                                        ...assetDetail,
                                                        isDistributable:
                                                            detail.selectedOption.label === "Yes",
                                                    }));
                                                }}
                                                filteringType="auto"
                                                selectedAriaLabel="Selected"
                                            />
                                        </FormField>

                                        <FormField
                                            label="Database"
                                            errorText={validateNonZeroLengthTextAsYouType(
                                                assetDetail.databaseId
                                            )}
                                        >
                                            <DatabaseSelector
                                                onChange={(x: any) => {
                                                    setDatabaseId(x.detail.selectedOption);
                                                    setAssetDetail((assetDetail) => ({
                                                        ...assetDetail,
                                                        databaseId: x.detail.selectedOption.value,
                                                    }));
                                                }}
                                                selectedOption={databaseId}
                                            />
                                        </FormField>

                                        <FormField
                                            label="Description"
                                            constraintText="Minimum 4 characters"
                                            errorText={validateNonZeroLengthTextAsYouType(
                                                assetDetail.description
                                            )}
                                        >
                                            <Textarea
                                                value={assetDetail.description || ""}
                                                onChange={(e) => {
                                                    setAssetDetail((assetDetail) => ({
                                                        ...assetDetail,
                                                        description: e.detail.value,
                                                    }));
                                                }}
                                            />
                                        </FormField>

                                        <FormField
                                            label="Comment"
                                            constraintText="Minimum 4 characters"
                                            errorText={validateNonZeroLengthTextAsYouType(
                                                assetDetail.Comment
                                            )}
                                        >
                                            <Input
                                                value={assetDetail.Comment || ""}
                                                onChange={(e) => {
                                                    setAssetDetail((assetDetail) => ({
                                                        ...assetDetail,
                                                        Comment: e.detail.value,
                                                    }));
                                                }}
                                            />
                                        </FormField>

                                        <Grid
                                            gridDefinition={[
                                                { colspan: { default: 6 } },
                                                { colspan: { default: 6 } },
                                            ]}
                                        >
                                            <FileUpload
                                                label="Asset"
                                                disabled={false}
                                                errorText={undefined}
                                                setFile={(file) => {
                                                    setAssetDetail((assetDetail) => ({
                                                        ...assetDetail,
                                                        Asset: file,
                                                    }));
                                                }}
                                                fileFormats={objectFileFormatsStr}
                                                file={assetDetail.Asset}
                                            />
                                            <FileUpload
                                                label="Preview"
                                                disabled={false}
                                                errorText={undefined}
                                                setFile={(file) => {
                                                    setAssetDetail((assetDetail) => ({
                                                        ...assetDetail,
                                                        Preview: file,
                                                    }));
                                                }}
                                                fileFormats={previewFileFormatsStr}
                                                file={assetDetail.Preview}
                                            />
                                        </Grid>
                                    </SpaceBetween>
                                </Container>
                            ),
                        },
                        {
                            title: "Asset Metadata",
                            content: (
                                <Container header={<Header variant="h2">Asset Metadata</Header>}>
                                    <SpaceBetween direction="vertical" size="l">
                                        <MetadataTable
                                            assetId={assetDetail.assetId || ""}
                                            databaseId={assetDetail.databaseId || ""}
                                            initialState={metadata}
                                            store={(databaseId, assetId, record) => {
                                                return new Promise((resolve) => {
                                                    console.log("resolve promise", resolve);
                                                    setMetadata(record);
                                                    resolve(null);
                                                });
                                            }}
                                        />
                                    </SpaceBetween>
                                </Container>
                            ),
                            isOptional: true,
                        },
                        {
                            title: "Workflow Actions",
                            content: (
                                <Container header={<Header variant="h2">Workflow Actions</Header>}>
                                    <SpaceBetween direction="vertical" size="l">
                                        <Table
                                            columnDefinitions={workflowColumnDefns}
                                            items={workflows}
                                            onSelectionChange={({ detail }) => {
                                                console.log("detail selection change", detail);
                                                setSelectedWorkflows(detail.selectedItems);
                                            }}
                                            selectedItems={selectedWorkflows}
                                            trackBy="workflowId"
                                            selectionType="multi"
                                            ariaLabels={{
                                                selectionGroupLabel: "Items selection",
                                                allItemsSelectionLabel: ({ selectedItems }) =>
                                                    `${selectedItems.length} ${
                                                        selectedItems.length === 1
                                                            ? "item"
                                                            : "items"
                                                    } selected`,
                                                itemSelectionLabel: ({ selectedItems }, item) => {
                                                    const isItemSelected = selectedItems.filter(
                                                        (i) => i.name === item.name
                                                    ).length;
                                                    return `${item.name} is ${
                                                        isItemSelected ? "" : "not"
                                                    } selected`;
                                                },
                                            }}
                                        />
                                    </SpaceBetween>
                                </Container>
                            ),
                            isOptional: true,
                        },
                        {
                            title: "Review and Upload",
                            content: (
                                <SpaceBetween size="xs">
                                    <Header
                                        variant="h3"
                                        actions={
                                            <Button onClick={() => setActiveStepIndex(0)}>
                                                Edit
                                            </Button>
                                        }
                                    >
                                        Review
                                    </Header>
                                    <Container header={<Header variant="h2">Asset Detail</Header>}>
                                        <ColumnLayout columns={2} variant="text-grid">
                                            {Object.keys(assetDetail).map((k) => (
                                                <DisplayKV
                                                    key={k}
                                                    label={k}
                                                    value={assetDetail[k as keyof AssetDetail]}
                                                />
                                            ))}
                                            {/* <div>
                                            <pre>{JSON.stringify(metadata, null, 2)}</pre>
                                        </div> */}
                                        </ColumnLayout>
                                    </Container>
                                    <Container
                                        header={<Header variant="h2">Asset Metadata</Header>}
                                    >
                                        <ColumnLayout columns={2} variant="text-grid">
                                            {Object.keys(metadata).map((k) => (
                                                <DisplayKV
                                                    key={k}
                                                    label={k}
                                                    value={metadata[k as keyof Metadata]}
                                                />
                                            ))}
                                        </ColumnLayout>
                                    </Container>
                                    <Container
                                        header={<Header variant="h2">Selected Workflows</Header>}
                                    >
                                        <Table
                                            columnDefinitions={workflowColumnDefns}
                                            items={selectedWorkflows}
                                        />
                                    </Container>
                                </SpaceBetween>
                            ),
                        },
                    ]}
                />
            )}
        </Box>
    );
};

class DisplayKVProps {
    label!: string;
    value!: any;
}

function DisplayKV({ label, value }: DisplayKVProps): JSX.Element {
    if (value instanceof File) {
        return (
            <div>
                <Box variant="awsui-key-label">{label}</Box>
                <DisplayFileMeta file={value} />
            </div>
        );
    }

    return (
        <div>
            <Box variant="awsui-key-label">{label}</Box>
            <div>{value}</div>
        </div>
    );
}

export default function AssetUploadPage({}) {
    return (
        <>
            <Box padding={{ top: false ? "s" : "m", horizontal: "l" }}>
                <Grid gridDefinition={[{ colspan: { default: 12 } }]}>
                    <div>
                        <TextContent>
                            <Header variant="h1">Create Asset</Header>
                        </TextContent>

                        <UploadForm />
                    </div>
                </Grid>
            </Box>
        </>
    );
}

class ProgresCallbackArgs {
    loaded!: number;
    total!: number;
}

async function uploadAssetToS3(
    file: File,
    keyPrefix: string,
    metadata: { [k: string]: string },
    progressCallback: (progress: ProgresCallbackArgs) => void
): Promise<StoragePutOutput<Record<string, any>>> {
    const ext = `.${file?.name.split(".").pop()}`;
    const key = `${keyPrefix}${ext}`;
    return Storage.put(key, file, { metadata, progressCallback });
}

{
    /*
PUT https://jjykh968wd.execute-api.us-east-1.amazonaws.com/assets
{
    "assetId": "blades",
    "databaseId": "notphillips",
    "description": "test",
    "bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x",
    "key": "blades.stl",
    "assetType": ".stl",
    "specifiedPipelines": [],
    "isDistributable": false,
    "Comment": "test",
    "previewLocation": {
        "Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x",
        "Key": "blades.png"
    },
    "Asset": {},
    "Preview": {}
}


// GET https://jjykh968wd.execute-api.us-east-1.amazonaws.com/assets
// {"message": {"Items": [{"assetType": ".stl", "executionId": "", "pipelineId": "", "currentVersion": {"Comment": "test", "S3Version": "8vzRjnkkqZNfkF9e4bGtswukOrKQjymP", "Version": "1", "description": "test", "specifiedPipelines": [], "DateModified": "February 17 2023 - 20:39:30", "FileSize": "1.101284MB"}, "versions": [], "authEdit": [], "specifiedPipelines": [], "previewLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "blades.png"}, "assetId": "blades", "assetLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "blades.stl"}, "assetName": "blades", "databaseId": "notphillips", "description": "test", "isDistributable": false}, {"assetType": ".glb", "executionId": "", "pipelineId": "", "currentVersion": {"Comment": "test", "S3Version": "c4.Jt1gUkWcriHC3c4k.QOeEFyFjj.zE", "Version": "1", "description": "test", "specifiedPipelines": [], "DateModified": "January 30 2023 - 19:36:36", "FileSize": "0.336576MB"}, "versions": [], "authEdit": [], "specifiedPipelines": [], "previewLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "boat.jpeg"}, "assetId": "boat", "assetLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "boat.glb"}, "assetName": "boat", "databaseId": "notphillips", "description": "test", "isDistributable": false}, {"assetType": ".STEP", "executionId": "", "pipelineId": "", "currentVersion": {"Comment": "lld_sizing_1-ez_p001225_step", "S3Version": "Kc2_08L0qKZoQq_Ipj5ZHq3UzwcSHkWB", "Version": "1", "description": "lld_sizing_1-ez_p001225_step", "specifiedPipelines": [], "DateModified": "January 25 2023 - 23:35:17", "FileSize": "1.573967MB"}, "versions": [], "authEdit": [], "specifiedPipelines": [], "previewLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "lld_sizing_1-ez_p001225_step.jpg"}, "assetId": "lld_sizing_1-ez_p001225_step", "assetLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "lld_sizing_1-ez_p001225_step.STEP"}, "assetName": "lld_sizing_1-ez_p001225_step", "databaseId": "phillips", "description": "lld_sizing_1-ez_p001225_step", "isDistributable": false}, {"assetType": ".glb", "executionId": "", "pipelineId": "", "currentVersion": {"Comment": "sa12_philips_laser_system_nexcimer_cart.glb", "S3Version": "MYQyBJZ7bEHtorxePw3DyfAovfEdSxSm", "Version": "1", "description": "sa12_philips_laser_system_nexcimer_cart.glb", "specifiedPipelines": [], "DateModified": "January 25 2023 - 22:46:20", "FileSize": "73.126132MB"}, "versions": [], "authEdit": [], "specifiedPipelines": [], "previewLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "sa12_philips_laser_system_nexcimer_cart.png"}, "assetId": "sa12_philips_laser_system_nexcimer_cart", "assetLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "sa12_philips_laser_system_nexcimer_cart.glb"}, "assetName": "sa12_philips_laser_system_nexcimer_cart", "databaseId": "phillips", "description": "sa12_philips_laser_system_nexcimer_cart.glb", "isDistributable": false}, {"assetType": ".STEP", "executionId": "", "pipelineId": "", "currentVersion": {"Comment": "TorqMax.STEP", "S3Version": "wVr9hB5GhJPz3UdGPSB4TjEpfQ4ZfDlj", "Version": "1", "description": "torqmax", "specifiedPipelines": [], "DateModified": "January 24 2023 - 20:45:19", "FileSize": "6.703137MB"}, "versions": [], "authEdit": [], "specifiedPipelines": [], "previewLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "torqmaxstep.png"}, "assetId": "torqmaxstep", "assetLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "torqmaxstep.STEP"}, "assetName": "torqmaxstep", "databaseId": "phillips", "description": "torqmax", "isDistributable": false}, {"assetType": ".stp", "executionId": "", "pipelineId": "", "currentVersion": {"Comment": "test", "S3Version": "ptYaadX35yvuL41c.RzsD6IWCS0WopTH", "Version": "1", "description": "test", "specifiedPipelines": [], "DateModified": "January 31 2023 - 19:05:17", "FileSize": "0.007634MB"}, "versions": [], "authEdit": [], "specifiedPipelines": [], "previewLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "test.pdf"}, "assetId": "test", "assetLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "test.stp"}, "assetName": "test", "databaseId": "nistsmartconnectedsystemsdivision", "description": "test", "isDistributable": false}, {"assetType": ".stp", "executionId": "", "pipelineId": "", "currentVersion": {"Comment": "test1", "S3Version": "acITU0i3zjvTMV1ppMaQ_sazXPqsJEab", "Version": "1", "description": "test1", "specifiedPipelines": [], "DateModified": "January 31 2023 - 19:07:48", "FileSize": "0.323601MB"}, "versions": [], "authEdit": [], "specifiedPipelines": [], "previewLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "test1.pdf"}, "assetId": "test1", "assetLocation": {"Bucket": "vams-dev-us-east-1-assetbucket1d025086-1jo3lq4rgcv4x", "Key": "test1.stp"}, "assetName": "test1", "databaseId": "nistsmartconnectedsystemsdivision", "description": "test1", "isDistributable": false}]}}




Run a workflow

POST /database/nistsmartconnectedsystemsdivision/assets/blades/workflows/workflow1


POST /database/nistsmartconnectedsystemsdivision/assets/blades2/workflows/workflow1



*/
}
