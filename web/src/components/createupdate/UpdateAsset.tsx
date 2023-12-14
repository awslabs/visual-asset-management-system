import { FileUpload, Modal, Select, SpaceBetween } from "@cloudscape-design/components";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Synonyms from "../../synonyms";
import Input from "@cloudscape-design/components/input";
import { useEffect, useState } from "react";
import { OptionDefinition } from "@cloudscape-design/components/internal/components/option/interfaces";
import { Storage, API } from "aws-amplify";
import { AssetDetail } from "../../pages/AssetUpload";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import { UploadAssetWorkflowApi } from "../../pages/AssetUpload/onSubmit";

interface UpdateAssetProps {
    asset: any;
    onClose: () => void;
    onComplete: () => void;
    isOpen: boolean;
}

const isDistributableOptions: OptionDefinition[] = [
    { label: "Yes", value: "true" },
    { label: "No", value: "false" },
];

const update = async (
    updatedAsset: any,
    files: File[],
    setProgress: (progress: number) => void,
    setError: (error: { isError: boolean; message: string }) => void,
    setComplete: (complete: boolean) => void
) => {
    let uploadBody = Object.assign({}, updatedAsset);
    uploadBody.bucket = updatedAsset.assetLocation.Bucket;
    uploadBody.key = updatedAsset.assetLocation.Key;
    uploadBody.Comment = updatedAsset.currentVersion.Comment;

    if (files && files.length > 0) {
        const newKey =
            "previews" +
            "/" +
            updatedAsset.assetId +
            "/" +
            updatedAsset.assetName +
            "." +
            files[0].name.split(".").pop();

        uploadBody.previewLocation = {
            Bucket: updatedAsset.assetLocation.Bucket,
            Key: newKey,
        };
        await Storage.put(newKey, files[0], {
            resumable: true,
            customPrefix: {
                public: "",
            },
            progressCallback(progress) {
                setProgress(Math.floor((progress.loaded / progress.total) * 100));
            },
            errorCallback: (err) => {
                setError({ isError: true, message: err });
            },
            completeCallback: (event) => {
                const body: Partial<UploadAssetWorkflowApi> = { uploadAssetBody: uploadBody };
                return API.post("api", "assets/uploadAssetWorkflow", {
                    "Content-type": "application/json",
                    body,
                })
                    .then((res) => {
                        setComplete(true);
                    })
                    .catch((err) => {
                        setError({ isError: true, message: err });
                    });
            },
        });
    } else {
        const body: Partial<UploadAssetWorkflowApi> = { uploadAssetBody: uploadBody };
        API.post("api", "assets/uploadAssetWorkflow", {
            "Content-type": "application/json",
            body,
        })
            .then((res) => {
                setComplete(true);
            })
            .catch((err) => {
                setError({ isError: true, message: err });
            });
    }
};
export const UpdateAsset = ({ asset, ...props }: UpdateAssetProps) => {
    const [assetDetail, setAssetDetail] = useState(asset);
    const [progress, setProgress] = useState(0);
    const [error, setError] = useState({ isError: false, message: "" });
    const [complete, setComplete] = useState(false);
    useEffect(() => {
        return () => {
            setAssetDetail(asset);
        };
    }, [asset]);

    const [value, setValue] = useState<File[]>([]);
    if (complete) {
        props.onComplete();
    }
    return (
        <Modal
            onDismiss={() => props.onClose()}
            visible={props.isOpen}
            closeAriaLabel="Close modal"
            size="medium"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={() => props.onClose()}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={() =>
                                update(assetDetail, value, setProgress, setError, setComplete)
                            }
                        >
                            Update Asset
                        </Button>
                    </SpaceBetween>
                </Box>
            }
            header="Update Asset"
        >
            <SpaceBetween direction="vertical" size="l">
                <FormField label={`${Synonyms.Asset} Name`}>
                    <Input
                        value={assetDetail.assetName || ""}
                        data-testid="assetid-input"
                        onChange={(e) => {
                            setAssetDetail((assetDetail: any) => ({
                                ...assetDetail,
                                assetName: e.detail.value,
                            }));
                        }}
                    />
                </FormField>
                <FormField label={`${Synonyms.Asset} Description`}>
                    <Input
                        value={assetDetail.description || ""}
                        data-testid="assetdescription-input"
                        onChange={(e) => {
                            setAssetDetail((assetDetail: any) => ({
                                ...assetDetail,
                                description: e.detail.value,
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
                                        (assetDetail.isDistributable === true ? "Yes" : "No") ===
                                        o.label
                                )
                                .pop() || null
                        }
                        onChange={({ detail }) => {
                            setAssetDetail((assetDetail: any) => ({
                                ...assetDetail,
                                isDistributable: detail.selectedOption.label === "Yes",
                            }));
                        }}
                        filteringType="auto"
                        selectedAriaLabel="Selected"
                        data-testid="isDistributable-select"
                    />
                </FormField>
                <FormField label="Preview">
                    <FileUpload
                        onChange={({ detail }) => setValue(detail.value)}
                        value={value}
                        i18nStrings={{
                            uploadButtonText: (e) => (e ? "Choose files" : "Choose file"),
                            dropzoneText: (e) =>
                                e ? "Drop files to upload" : "Drop file to upload",
                            removeFileAriaLabel: (e) => `Remove file ${e + 1}`,
                            limitShowFewer: "Show fewer files",
                            limitShowMore: "Show more files",
                            errorIconAriaLabel: "Error",
                        }}
                        accept={"image/*"}
                        multiple={false}
                        showFileLastModified
                        showFileSize
                        showFileThumbnail
                        tokenLimit={3}
                        constraintText="Image files only"
                    />
                </FormField>
                {progress > 0 && (
                    <ProgressBar
                        value={complete ? 100 : progress}
                        label={"Preview upload progress"}
                        status={error.isError ? "error" : complete ? "success" : "in-progress"}
                        additionalInfo={
                            error.isError ? error.message : complete ? "Upload complete" : ""
                        }
                    ></ProgressBar>
                )}
            </SpaceBetween>
        </Modal>
    );
};
