import {
    FileUpload,
    Modal,
    Select,
    SpaceBetween,
    Multiselect,
} from "@cloudscape-design/components";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Synonyms from "../../synonyms";
import Input from "@cloudscape-design/components/input";
import { useEffect, useState } from "react";
import { OptionDefinition } from "@cloudscape-design/components/internal/components/option/interfaces";
import { Storage, API } from "aws-amplify";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import { UploadAssetWorkflowApi } from "../../pages/AssetUpload/onSubmit";
import { fetchTags, fetchtagTypes } from "../../services/APIService";
import { TagType } from "../../pages/Tag/TagType.interface";
import { validateRequiredTagTypeSelected } from "../../pages/AssetUpload/validations";

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

var tags: any[] = [];
var assetTags: any[] = [];
var tagTypes: TagType[] = [];

const update = async (
    updatedAsset: any,
    files: File[],
    setProgress: (progress: number) => void,
    setError: (error: { isError: boolean; message: string }) => void,
    setComplete: (complete: boolean) => void,
    isFormValid: boolean
) => {
    if (!isFormValid) return; // Don't attempt to update if form is not valid

    let uploadBody = Object.assign({}, updatedAsset);
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
    const [isValid, setIsValid] = useState(true);
    const [isFormTouched, setIsFormTouched] = useState(false);

    const [value, setValue] = useState<File[]>([]);
    if (complete) {
        props.onComplete();
    }
    const [selectedTags, setSelectedTags] = useState<OptionDefinition[]>([]);

    const [validationText, setValidationText] = useState<{
        tags?: string;
    }>({});

    const [constraintText, setConstraintText] = useState<{
        tags?: string;
    }>({});

    useEffect(() => {
        setAssetDetail(asset);
        fetchTags().then((res) => {
            tags = [];
            if (res && Array.isArray(res)) {
                Object.values(res).map((x: any) => {
                    tags.push({ label: `${x.tagName} (${x.tagTypeName})`, value: x.tagName });
                });
            }
            return tags;
        });

        const tagTypesString = localStorage.getItem("tagTypes");
        const tagTypes = tagTypesString ? JSON.parse(tagTypesString) : [];
        const initTags = asset.tags
            ? asset.tags.map((tagName: string) => {
                  const tagType = tagTypes.find((type: any) => type.tags.includes(tagName));
                  const label = tagType ? `${tagName} (${tagType.tagTypeName})` : tagName;

                  return {
                      label: label,
                      value: tagName,
                  };
              })
            : [];

        setSelectedTags(initTags);
    }, [asset]);

    useEffect(() => {
        // Get Tag Types to enforce when they are required
        tagTypes = [];

        fetchtagTypes().then((res) => {
            tagTypes = res;

            if (tagTypes.length) {
                const requiredTagTypes = tagTypes.filter((tagType) => tagType.required === "True");

                if (requiredTagTypes.length) {
                    // Set constraint text if there are required tag types
                    setConstraintText({
                        tags:
                            "The following tag types, listed in parentheses, require at least one selection: " +
                            requiredTagTypes.map((tagType) => tagType.tagTypeName).join(", "),
                    });

                    //Set initial validation text
                    if (selectedTags.length) {
                        setValidationText({
                            ...validationText,
                            tags: validateRequiredTagTypeSelected(
                                selectedTags.map((tag) => tag.value!),
                                tagTypes
                            ),
                        });
                    }
                }
            }
        });
    }, []);

    useEffect(() => {
        // Form Validation Error Check
        const validation = {
            tags: validateRequiredTagTypeSelected(
                selectedTags.map((tag) => tag.value!),
                tagTypes
            ),
        };
        setValidationText(validation);

        const isValid = !validation.tags;
        setIsValid(isValid);
    }, [selectedTags, isFormTouched]);

    return (
        <Modal
            onDismiss={() => {
                setIsFormTouched(false);
                props.onClose();
            }}
            visible={props.isOpen}
            closeAriaLabel="Close modal"
            size="medium"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            variant="link"
                            onClick={() => {
                                setIsFormTouched(false);
                                props.onClose();
                            }}
                        >
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={() => {
                                setIsFormTouched(true);
                                update(
                                    assetDetail,
                                    value,
                                    setProgress,
                                    setError,
                                    setComplete,
                                    isValid
                                );
                            }}
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
                            setIsFormTouched(true);
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
                            setIsFormTouched(true);
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
                            setIsFormTouched(true);
                        }}
                        filteringType="auto"
                        selectedAriaLabel="Selected"
                        data-testid="isDistributable-select"
                    />
                </FormField>
                <FormField
                    label="Tags"
                    constraintText={constraintText.tags}
                    errorText={isFormTouched && validationText.tags}
                >
                    <Multiselect
                        selectedOptions={selectedTags}
                        placeholder="Tags"
                        options={tags}
                        onChange={({ detail }) => {
                            setSelectedTags(detail.selectedOptions as OptionDefinition[]);
                            assetTags = [];
                            detail.selectedOptions.forEach((x: any) => {
                                assetTags.push(x.value);
                            });
                            setAssetDetail((assetDetail: any) => ({
                                ...assetDetail,
                                tags: assetTags,
                            }));
                            setIsFormTouched(true);
                        }}
                    />
                </FormField>
                <FormField label="Preview">
                    <FileUpload
                        onChange={({ detail }) => {
                            setValue(detail.value);
                            setIsFormTouched(true);
                        }}
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
