/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import {
    Box,
    Button,
    Grid,
    Header,
    Modal,
    ProgressBarProps,
    SpaceBetween,
    StatusIndicatorProps,
    TextContent,
    Wizard,
} from "@cloudscape-design/components";
import localforage from "localforage";
import ProgressScreen from "./ProgressScreen";
import Synonyms from "../../synonyms";
import onSubmit, { onUploadRetry, UploadExecutionProps } from "./onSubmit";
import { AssetUploadProvider, useAssetUploadState } from "./state";
import { AssetPrimaryInfo } from "./AssetPrimaryInfo";
import { AssetUploadReview } from "./AssetUploadReview";
import { AssetFileInfo } from "./AssetFileInfo";
import { AssetMetadataInfo } from "./AssetMetadataInfo";

import type { FileUploadTableItem } from "./types";
import type { Metadata } from "../../components/single/Metadata";

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

const UploadForm = () => {
    const [state, dispatch] = useAssetUploadState();

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

    useEffect(() => {
        if (state.assetId && fileUploadTableItems.length > 0) {
            dispatch({ type: "UPDATE_ASSET_FILES", payload: fileUploadTableItems });
            localforage
                .setItem(state.assetId, {
                    ...state,
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
                        assetDetail={state}
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
                        if (detail.reason === "next" && state.pageValid) {
                            setActiveStepIndex(detail.requestedStepIndex);
                            dispatch({ type: "UPDATE_PAGE_VALIDITY", payload: false });
                        }
                    }}
                    activeStepIndex={activeStepIndex}
                    onSubmit={onSubmit({
                        assetDetail: state,
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
                            content: <AssetPrimaryInfo />,
                        },
                        {
                            title: `${Synonyms.Asset} Metadata`,
                            content: (
                                <AssetMetadataInfo metadata={metadata} setMetadata={setMetadata} />
                            ),
                            // Making this mandatory for now until form validation is implemented.
                            isOptional: false,
                        },
                        {
                            title: "Select Files to upload",
                            content: (
                                <AssetFileInfo setFileUploadTableItems={setFileUploadTableItems} />
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
    return (
        <AssetUploadProvider>
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
        </AssetUploadProvider>
    );
}
