/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
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
import Button from "@cloudscape-design/components/button";

import Wizard from "@cloudscape-design/components/wizard";

import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";

import DatabaseSelector from "../components/selectors/DatabaseSelector";
import {
    cadFileFormats,
    modelFileFormats,
    columnarFileFormats,
    previewFileFormats,
} from "../common/constants/fileFormats";

import MetadataTable, { Metadata } from "../components/single/Metadata";
import { fetchDatabaseWorkflows } from "../services/APIService";
import Table from "@cloudscape-design/components/table";
import { ProgressBarProps } from "@cloudscape-design/components/progress-bar";
import { StatusIndicatorProps } from "@cloudscape-design/components/status-indicator";
import { OptionDefinition } from "@cloudscape-design/components/internal/components/option/interfaces";
import {
    validateEntityIdAsYouType,
    validateNonZeroLengthTextAsYouType,
} from "./AssetUpload/validations";
import { DisplayKV, FileUpload } from "./AssetUpload/components";
import ProgressScreen from "./AssetUpload/ProgressScreen";
import onSubmit from "./AssetUpload/onSubmit";

const objectFileFormats = new Array().concat(cadFileFormats, modelFileFormats, columnarFileFormats);
const objectFileFormatsStr = objectFileFormats.join(", ");
const previewFileFormatsStr = previewFileFormats.join(", ");

export class AssetDetail {
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
                <ProgressScreen
                    execStatus={execStatus}
                    previewUploadProgress={previewUploadProgress}
                    assetUploadProgress={assetUploadProgress}
                />
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
                    onNavigate={({ detail }) => {
                        setActiveStepIndex(detail.requestedStepIndex);
                        console.log("detail on navigate", detail);
                    }}
                    activeStepIndex={activeStepIndex}
                    onSubmit={onSubmit({
                        assetDetail,
                        setFreezeWizardButtons,
                        metadata,
                        selectedWorkflows,
                        execStatus,
                        setExecStatus,
                        setShowUploadAndExecProgress,
                        setAssetUploadProgress,
                        setPreviewUploadProgress,
                    })}
                    allowSkipTo
                    steps={[
                        {
                            title: "Asset Details",
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

export default function AssetUploadPage() {
    return (
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
    );
}
