/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { ProgressBarProps } from "@cloudscape-design/components";
import { NonCancelableCustomEvent } from "@cloudscape-design/components/interfaces";
import { StatusIndicatorProps } from "@cloudscape-design/components/status-indicator";

import { API, Storage, Cache } from "aws-amplify";
import { Metadata, MetadataApi } from "../../components/single/Metadata";
import { AssetDetail } from "../AssetUpload";
import { generateUUID } from "../../common/utils/utils";
import { FileUploadTableItem } from "./FileUploadTable";

export type ExecStatusType = Record<string, StatusIndicatorProps.Type>;

class BucketKey {
    Bucket?: string;
    Key?: string;
}

class AssetPreprocessingBody {
    assetId?: string;
    databaseId?: string;
    original_asset!: BucketKey;
    preview!: BucketKey;
    gltf!: BucketKey;
    isMultiFile: boolean = false;
}

class UploadAssetWorkflowApi {
    assetPreprocessingBody?: AssetPreprocessingBody;
    uploadAssetBody!: AssetDetail;
    updateMetadataBody!: MetadataApi;
    executeWorkflowBody!: {
        workflowIds: string[];
    };
}

class OnSubmitProps {
    selectedWorkflows!: any;
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

class ProgressCallbackArgs {
    loaded!: number;
    total!: number;
}

function getUploadTaskPromise(
    index: number,
    key: string,
    f: File,
    metadata: { [p: string]: string },
    progressCallback: (index: number, progress: ProgressCallbackArgs) => void,
    completeCallback: (index: number, event: any) => void,
    errorCallback: (index: number, event: any) => void
) {
    return new Promise((resolve, reject) => {
        return Storage.put(key, f, {
            metadata,
            resumable: true,
            customPrefix: {
                public: "",
            },
            progressCallback: (progress: ProgressCallbackArgs) => {
                progressCallback(index, {
                    loaded: progress.loaded,
                    total: progress.total,
                });
            },
            completeCallback: (event: any) => {
                completeCallback(index, null);
                resolve(true);
            },
            errorCallback: (event: any) => {
                errorCallback(index, event);
                resolve(true);
            },
        });
    });
}

export function createAssetUploadPromises(
    isMultiFile: boolean,
    files: FileUploadTableItem[],
    keyPrefix: string,
    metadata: { [k: string]: string },
    moveToQueued: (index: number) => void,
    progressCallback: (index: number, progress: ProgressCallbackArgs) => void,
    completeCallback: (index: number, event: any) => void,
    errorCallback: (index: number, event: any) => void
) {
    const uploads = [];
    // KeyPrefix captures the entire path when a single file is selected as an asset
    // A promise begins executing right after creation, hence I am returning a function that returns a promise
    // so these promises can then be executed sequentially to track progress

    if (!isMultiFile) {
        uploads.push(
            async () =>
                await files[0].handle.getFile().then((f: File) => {
                    return getUploadTaskPromise(
                        0,
                        keyPrefix,
                        f,
                        metadata,
                        progressCallback,
                        completeCallback,
                        errorCallback
                    );
                })
        );
    } else {
        for (let i = 0; i < files.length; i++) {
            if (files[i].status !== "Completed") {
                moveToQueued(i);
                uploads.push(
                    async () =>
                        await files[i].handle.getFile().then((f: File) => {
                            let key = keyPrefix + files[i].relativePath;
                            return getUploadTaskPromise(
                                i,
                                key,
                                f,
                                metadata,
                                progressCallback,
                                completeCallback,
                                errorCallback
                            );
                        })
                );
            }
        }
    }
    return uploads;
}

export async function executeUploads(uploadPromises: any) {
    let result = Promise.resolve();
    for (let i = 0; i < uploadPromises.length; i++) {
        result = result.then(uploadPromises[i]);
    }
    return result;
}

async function uploadAssetToS3(
    file: File,
    key: string,
    metadata: { [k: string]: string },
    progressCallback: (progress: ProgressCallbackArgs) => void
) {
    console.log("upload", key, file);
    return Storage.put(key, file, { metadata, progressCallback });
}

const getAssetType = (assetDetail: AssetDetail) => {
    if (assetDetail.Asset?.length === 1) {
        return "." + assetDetail.Asset[0].name.split(".").pop();
    } else {
        return "folder";
    }
};

const getKeyPrefix = (uuid: string, assetDetail: AssetDetail) => {
    if (assetDetail.Asset?.length === 1) {
        return uuid + "/" + assetDetail.assetId + assetDetail.assetType;
    } else {
        return uuid + "/";
    }
};

export interface UploadExecutionProps {
    assetDetail: AssetDetail;
    updateProgressForFileUploadItem: (index: number, loaded: number, total: number) => void;
    moveToQueued: (index: number) => void;
    fileUploadComplete: (index: number, event: any) => void;
    fileUploadError: (index: number, event: any) => void;
    setPreviewUploadProgress: (x: ProgressBarProps) => void;
    uuid: string;
    prevAssetId?: string;
    selectedWorkflows: any;
    metadata: Metadata;
    setExecStatus: (x: ExecStatusType | ((x: ExecStatusType) => ExecStatusType)) => void;
    execStatus: ExecStatusType;
}

async function performUploads({
    assetDetail,
    updateProgressForFileUploadItem,
    moveToQueued,
    fileUploadComplete,
    fileUploadError,
    setPreviewUploadProgress,
    uuid,
    prevAssetId,
    selectedWorkflows,
    metadata,
    setExecStatus,
    execStatus,
}: UploadExecutionProps) {
    if (assetDetail.Asset && assetDetail.assetId && assetDetail.databaseId && assetDetail.key) {
        const uploads = createAssetUploadPromises(
            assetDetail.isMultiFile,
            assetDetail.Asset,
            assetDetail.key,
            {
                assetId: assetDetail.assetId,
                databaseId: assetDetail.databaseId,
            },
            moveToQueued,
            (index, progress) => {
                updateProgressForFileUploadItem(index, progress.loaded, progress.total);
            },
            (index, event) => {
                fileUploadComplete(index, event);
            },
            (index, event) => {
                console.log("Error Uploading", event);
                fileUploadError(index, event);
            }
        );
        executeUploads(uploads)
            .then(() => {})
            .catch((err) => {
                return Promise.reject(err);
            });

        const up2 =
            (assetDetail.Preview &&
                assetDetail?.previewLocation?.Key &&
                uploadAssetToS3(
                    assetDetail.Preview,
                    assetDetail.previewLocation?.Key,
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
                    })) ||
            Promise.resolve();

        await Promise.all([up2]).then((uploads) => {
            const body: UploadAssetWorkflowApi = {
                assetPreprocessingBody: {
                    assetId: assetDetail.assetId,
                    databaseId: assetDetail.databaseId,
                    gltf: {
                        Bucket: assetDetail.bucket,
                        Key: uuid + "/" + prevAssetId + ".gltf",
                    },
                    original_asset: {
                        Bucket: assetDetail.bucket,
                        Key: assetDetail.key,
                    },
                    preview: {
                        Bucket: assetDetail.bucket,
                        Key: uuid + "/" + prevAssetId + ".png",
                    },
                    isMultiFile: assetDetail.isMultiFile,
                },
                executeWorkflowBody: {
                    workflowIds: selectedWorkflows.map(
                        (wf: { workflowId: string }) => wf.workflowId
                    ),
                },
                updateMetadataBody: {
                    version: "1",
                    metadata,
                },
                uploadAssetBody: assetDetail,
            };

            if (assetDetail.assetType === ".gltf") {
                delete body.assetPreprocessingBody;
            }

            setExecStatus({
                ...execStatus,
                "Asset Detail": "in-progress",
            });
            return API.post("api", "assets/uploadAssetWorkflow", {
                "Content-type": "application/json",
                body,
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
        });
        window.onbeforeunload = null;
    }
}

function updateAssetDetail(assetDetail: AssetDetail) {
    // prefix with x so that we pass the assetId validation that requires this regex ^[a-z]([-_a-z0-9]){3,63}$
    const uuid = "x" + generateUUID();

    const prevAssetId = assetDetail.assetId;
    // TODO duplicate logic with AssetFormDefinition and uploadAssetToS3
    // duplicate except that the uuids are unique to this version
    const config = Cache.getItem("config");
    assetDetail.bucket = config.bucket;
    assetDetail.assetType = getAssetType(assetDetail);
    assetDetail.key = getKeyPrefix(uuid, assetDetail);
    assetDetail.specifiedPipelines = [];
    assetDetail.previewLocation = {
        Bucket: config.bucket,
        Key:
            "previews" +
            "/" +
            uuid +
            "/" +
            assetDetail.assetId +
            "." +
            assetDetail.Preview?.name.split(".").pop(),
    };

    assetDetail.assetName = assetDetail.assetId;
    assetDetail.assetId = uuid;
    return { uuid, prevAssetId };
}

export default function onSubmit({
    assetDetail,
    setFreezeWizardButtons,
    metadata,
    selectedWorkflows,
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
        if (assetDetail.Asset && assetDetail.assetId && assetDetail.databaseId) {
            const { uuid, prevAssetId } = updateAssetDetail(assetDetail);
            const execStatusNew: Record<string, StatusIndicatorProps.Type> = {
                "Asset Details": "pending",
            };
            setExecStatus(execStatusNew);
            window.onbeforeunload = function () {
                return "";
            };
            setShowUploadAndExecProgress(true);
            const uploadExecutionProps: UploadExecutionProps = {
                assetDetail,
                moveToQueued,
                updateProgressForFileUploadItem,
                fileUploadComplete,
                fileUploadError,
                setPreviewUploadProgress,
                uuid,
                prevAssetId,
                selectedWorkflows,
                metadata,
                setExecStatus,
                execStatus,
            };
            setUploadExecutionProps(uploadExecutionProps);
            await performUploads(uploadExecutionProps);
        } else {
            console.log("Asset detail not right");
            console.log(assetDetail);
        }
    };
}

export async function onUploadRetry(uploadExecutionProps: UploadExecutionProps) {
    console.log("Retrying uploads");
    window.onbeforeunload = function () {
        return "";
    };
    await performUploads(uploadExecutionProps);
}
