/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import {ProgressBarProps} from "@cloudscape-design/components";
import {NonCancelableCustomEvent} from "@cloudscape-design/components/interfaces";
import {StatusIndicatorProps} from "@cloudscape-design/components/status-indicator";

import {API, Storage, Cache} from "aws-amplify";
import {Metadata, MetadataApi} from "../../components/single/Metadata";
import {AssetDetail} from "../AssetUpload";
import {generateUUID} from "../../common/utils/utils";

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
    setAssetUploadProgress!: (x: ProgressBarProps) => void;
    doneUploadingCallBack!: (x: number) => void;
    setPreviewUploadProgress!: (x: ProgressBarProps) => void;
    setCanNavigateToAssetPage!: (x: boolean) => void;
}

class ProgressCallbackArgs {
    loaded!: number;
    total!: number;
}


function createAssetUploadPromises(
    files: File[],
    keyPrefix: string,
    metadata: { [k: string]: string },
    progressCallback: (progress: ProgressCallbackArgs) => void
) {
    const uploads = []
    // KeyPrefix captures the entire path when a single file is selected as an asset
    // A promise begins executing right after creation, hence I am returning a function that returns a promise
    // so these promises can then be executed sequentially to track progress
    if (files.length === 1) {
        uploads.push(() => Storage.put(keyPrefix, files[0], {
            metadata,
            progressCallback
        }));
    }
    else {
        for (let i = 0; i < files.length; i++) {
            uploads.push(() => Storage.put(keyPrefix + files[i].webkitRelativePath, files[i], {
                metadata,
                progressCallback
            }));
        }
    }
    return uploads;
}

async function executeUploads(uploadPromises: any, doneUploading: (x: number) => void) {
    let result = Promise.resolve();
    for (let i = 0; i < uploadPromises.length; i++) {
        result = result.then(uploadPromises[i]).then(() => {
            doneUploading(i)
        })
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
    return Storage.put(key, file, {metadata, progressCallback});
}

const getAssetType = (assetDetail: AssetDetail) => {
    if (assetDetail.Asset?.length === 1) {
        return "." + assetDetail.Asset[0].name.split(".").pop()
    } else {
        return 'folder'
    }
}

const getKeyPrefix = (uuid: string, assetDetail: AssetDetail) => {
    if (assetDetail.Asset?.length === 1) {
        return uuid + "/" + assetDetail.assetId + assetDetail.assetType;
    } else {
        return uuid + "/" + assetDetail.assetId +  "/";
    }
}

export default function onSubmit({
                                     assetDetail,
                                     setFreezeWizardButtons,
                                     metadata,
                                     selectedWorkflows,
                                     execStatus,
                                     setExecStatus,
                                     setShowUploadAndExecProgress,
                                     setAssetUploadProgress,
                                     doneUploadingCallBack,
                                     setPreviewUploadProgress,
                                     setCanNavigateToAssetPage,
                                 }: OnSubmitProps) {
    return async (detail: NonCancelableCustomEvent<{}>) => {
        setFreezeWizardButtons(true);
        if (
            assetDetail.Asset &&
            assetDetail.Preview &&
            assetDetail.assetId &&
            assetDetail.databaseId
        ) {
            // prefix with x so that we pass the assetId validation that requires this regex ^[a-z]([-_a-z0-9]){3,63}$
            const uuid = "x" + generateUUID();

            const prevAssetId = assetDetail.assetId;
            // TODO duplicate logic with AssetFormDefinition and uploadAssetToS3
            // duplicate except that the uuids are unique to this version
            const config = Cache.getItem("config");
            assetDetail.bucket = config.bucket;
            assetDetail.assetType = getAssetType(assetDetail)
            assetDetail.key = getKeyPrefix(uuid, assetDetail);
            assetDetail.specifiedPipelines = [];
            assetDetail.previewLocation = {
                Bucket: config.bucket,
                Key:
                    "previews"+
                    "/" +
                    uuid +
                    "/" +
                    assetDetail.assetId +
                    "." +
                    assetDetail.Preview.name.split(".").pop(),
            };

            assetDetail.assetName = assetDetail.assetId;
            assetDetail.assetId = uuid;

            const execStatusNew: Record<string, StatusIndicatorProps.Type> = {
                "Asset Details": "pending",
            };

            setExecStatus(execStatusNew);

            window.onbeforeunload = function () {
                return "";
            };
            setShowUploadAndExecProgress(true);
            const uploads = createAssetUploadPromises(assetDetail.Asset, assetDetail.key, {
                    assetId: assetDetail.assetId,
                    databaseId: assetDetail.databaseId,
                },
                (progress) => {
                    setAssetUploadProgress({
                        value: (progress.loaded / progress.total) * 100,
                    });
                }
            )
            const uploadComplete = executeUploads(uploads, doneUploadingCallBack)
                .then(() => {
                    setAssetUploadProgress({
                        status: 'success',
                        value: 100
                    })
                })
                .catch((err) => {
                setAssetUploadProgress({
                    status: "error",
                    value: 100,
                });
                return Promise.reject(err)
            })
            const up2 =
                (assetDetail?.previewLocation?.Key &&
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

            await Promise.all([uploadComplete, up2]).then((uploads) => {
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
                        isMultiFile: assetDetail.isMultiFile
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
            setCanNavigateToAssetPage(true);
            window.onbeforeunload = null;
        }
    };
}
