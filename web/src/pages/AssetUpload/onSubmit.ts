import { StoragePutOutput } from "@aws-amplify/storage";
import { ProgressBarProps } from "@cloudscape-design/components";
import { NonCancelableCustomEvent } from "@cloudscape-design/components/interfaces";
import { StatusIndicatorProps } from "@cloudscape-design/components/status-indicator";

import { API, Storage, Cache } from "aws-amplify";
import { Metadata, put as saveMetadata } from "../../components/single/Metadata";
import { runWorkflow } from "../../services/APIService";
import { AssetDetail } from "../AssetUpload";

export type ExecStatusType = Record<string, StatusIndicatorProps.Type>;

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
    keyPrefix: string,
    metadata: { [k: string]: string },
    progressCallback: (progress: ProgresCallbackArgs) => void
): Promise<StoragePutOutput<Record<string, any>>> {
    const ext = `.${file?.name.split(".").pop()}`;
    const key = `${keyPrefix}${ext}`;
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
            // TODO duplicate logic with AssetFormDefinition and uploadAssetToS3
            const config = Cache.getItem("config");
            assetDetail.bucket = config.bucket;
            assetDetail.assetType = "." + assetDetail.Asset.name.split(".").pop();
            assetDetail.key = assetDetail.assetId + assetDetail.assetType;
            assetDetail.specifiedPipelines = [];
            assetDetail.previewLocation = {
                Bucket: config.bucket,
                Key: assetDetail.assetId + "." + assetDetail.Preview.name.split(".").pop(),
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
                            setExecStatus((p ) => ({
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
                        return saveMetadata(assetDetail.databaseId, assetDetail.assetId, metadata)
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
                    return Promise.reject("missing assetId or databaseId in assetDetail");
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
    };
}
