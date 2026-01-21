/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { ProgressBarProps } from "@cloudscape-design/components";
import { NonCancelableCustomEvent } from "@cloudscape-design/components/interfaces";
import { StatusIndicatorProps } from "@cloudscape-design/components/status-indicator";

import { Metadata } from "../../components/single/Metadata";
import { AssetDetail } from "./AssetUpload";

export type ExecStatusType = Record<string, StatusIndicatorProps.Type>;

export interface UploadExecutionProps {
    assetDetail: AssetDetail;
    metadata: Metadata;
    setFreezeWizardButtons: (x: boolean) => void;
    setShowUploadAndExecProgress: (x: boolean) => void;
    execStatus: ExecStatusType;
    setExecStatus: (x: ExecStatusType | ((x: ExecStatusType) => ExecStatusType)) => void;
    moveToQueued: (index: number) => void;
    updateProgressForFileUploadItem: (index: number, loaded: number, total: number) => void;
    fileUploadComplete: (index: number, event: any) => void;
    fileUploadError: (index: number, event: any) => void;
    setPreviewUploadProgress: (x: ProgressBarProps) => void;
    setUploadExecutionProps: (x: UploadExecutionProps) => void;
}

class OnSubmitProps {
    metadata!: Metadata;
    assetDetail!: AssetDetail;
    setFreezeWizardButtons!: (x: boolean) => void;
    setShowUploadAndExecProgress!: (x: boolean) => void;
    execStatus!: ExecStatusType;
    setExecStatus!: (x: ExecStatusType | ((x: ExecStatusType) => ExecStatusType)) => void;
    moveToQueued!: (index: number) => void;
    updateProgressForFileUploadItem!: (index: number, loaded: number, total: number) => void;
    fileUploadComplete!: (index: number, event: any) => void;
    fileUploadError!: (index: number, event: any) => void;
    setPreviewUploadProgress!: (x: ProgressBarProps) => void;
    setUploadExecutionProps!: (x: UploadExecutionProps) => void;
}

/**
 * Retry upload handler (legacy - kept for compatibility)
 */
export function onUploadRetry(uploadExecutionProps: UploadExecutionProps) {
    console.log("Retrying uploads");
    window.onbeforeunload = function () {
        return "";
    };

    // Set the execution status to in-progress
    uploadExecutionProps.setExecStatus((prev) => ({
        ...prev,
        "Asset Details": "in-progress",
    }));

    // Reset all failed files to queued
    const failedItems =
        uploadExecutionProps.assetDetail.Asset?.filter((item) => item.status === "Failed") || [];
    failedItems.forEach((item) => {
        uploadExecutionProps.moveToQueued(item.index);
    });
}

/**
 * Wizard submit handler
 * This function is called when the user clicks "Upload Object" in the wizard.
 * It triggers the AssetUploadWorkflow component which uses the refactored UploadManager.
 */
export default function onSubmit({
    assetDetail,
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
}: OnSubmitProps) {
    return async (detail: NonCancelableCustomEvent<{}>) => {
        setFreezeWizardButtons(true);

        // Check if we have required asset details
        if (assetDetail.assetId && assetDetail.databaseId) {
            // Initialize with empty status
            setExecStatus({});

            // Set window beforeunload handler to prevent accidental navigation
            window.onbeforeunload = function () {
                return "";
            };

            // Show the upload progress screen (AssetUploadWorkflow)
            // This will use the refactored UploadManager component
            setShowUploadAndExecProgress(true);

            // Create upload execution props for potential retry
            const uploadExecutionProps: UploadExecutionProps = {
                assetDetail,
                metadata,
                setFreezeWizardButtons,
                setShowUploadAndExecProgress,
                execStatus,
                setExecStatus,
                moveToQueued,
                updateProgressForFileUploadItem,
                fileUploadComplete,
                fileUploadError,
                setPreviewUploadProgress,
                setUploadExecutionProps,
            };

            // Store the upload execution props for potential retry
            setUploadExecutionProps(uploadExecutionProps);
        } else {
            console.log("Asset detail not ready - missing required fields");
            console.log(assetDetail);
            setFreezeWizardButtons(false);
        }
    };
}
