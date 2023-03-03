/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { StoragePutOutput } from "@aws-amplify/storage";
import { ProgressBarProps } from "@cloudscape-design/components";
import { NonCancelableCustomEvent } from "@cloudscape-design/components/interfaces";
import { StatusIndicatorProps } from "@cloudscape-design/components/status-indicator";

import { API, Storage, Cache } from "aws-amplify";
import { Metadata, MetadataApi } from "../../components/single/Metadata";
import { AssetDetail } from "../AssetUpload";
import { generateUUID } from "../../common/utils/utils";
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
    setPreviewUploadProgress!: (x: ProgressBarProps) => void;
}

class ProgresCallbackArgs {
    loaded!: number;
    total!: number;
}

async function uploadAssetToS3(
    file: File,
    key: string,
    metadata: { [k: string]: string },
    progressCallback: (progress: ProgresCallbackArgs) => void
): Promise<StoragePutOutput<Record<string, any>>> {
    console.log("upload", key, file);
    return Storage.put(key, file, { metadata, progressCallback });
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
    setPreviewUploadProgress,
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
            assetDetail.assetType = "." + assetDetail.Asset.name.split(".").pop();
            assetDetail.key = uuid + "/" + assetDetail.assetId + assetDetail.assetType;
            assetDetail.specifiedPipelines = [];
            assetDetail.previewLocation = {
                Bucket: config.bucket,
                Key: uuid + "/" + assetDetail.assetId + "." + assetDetail.Preview.name.split(".").pop(),
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
            const up1 = uploadAssetToS3(
                assetDetail.Asset,
                assetDetail.key,
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

            const up2 = (assetDetail?.previewLocation?.Key && uploadAssetToS3(
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
                })) || Promise.resolve();

            await Promise.all([up1, up2]).then((uploads) => {
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
                        }
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

                if(assetDetail.assetType === ".gltf") {
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
    };
}
