import {
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
import { API } from "aws-amplify";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import { fetchTags, fetchtagTypes } from "../../services/APIService";
import { TagType } from "../../pages/Tag/TagType.interface";
import {
    validateRequiredTagTypeSelected,
    validateNonZeroLengthTextAsYouType,
} from "../../pages/AssetUpload/validations";

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
    setError: (error: { isError: boolean; message: string }) => void,
    setComplete: (complete: boolean) => void,
    isFormValid: boolean
) => {
    if (!isFormValid) return; // Don't attempt to update if form is not valid

    try {
        // Update asset metadata using the new API endpoint
        const updateAssetData = {
            assetId: updatedAsset.assetId,
            assetName: updatedAsset.assetName,
            databaseId: updatedAsset.databaseId,
            description: updatedAsset.description,
            isDistributable: updatedAsset.isDistributable,
            tags: updatedAsset.tags || [],
            Comment: updatedAsset.currentVersion?.Comment || "",
        };

        // Update asset metadata using the new API endpoint
        await API.put("api", `database/${updatedAsset.databaseId}/assets/${updatedAsset.assetId}`, {
            "Content-type": "application/json",
            body: updateAssetData,
        });

        // Mark as complete
        setComplete(true);
    } catch (err: any) {
        setError({ isError: true, message: err.message || "An error occurred during the update process" });
    }
};

export const UpdateAsset = ({ asset, ...props }: UpdateAssetProps) => {
    const [assetDetail, setAssetDetail] = useState(asset);
    const [error, setError] = useState({ isError: false, message: "" });
    const [complete, setComplete] = useState(false);
    const [isValid, setIsValid] = useState(true);
    const [isFormTouched, setIsFormTouched] = useState(false);
    const [inProgress, setInProgress] = useState(false);
    
    if (complete) {
        props.onComplete();
    }
    
    const [selectedTags, setSelectedTags] = useState<OptionDefinition[]>([]);

    const [validationText, setValidationText] = useState<{
        tags?: string;
        assetName?: string;
        description?: string;
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
            assetName: validateNonZeroLengthTextAsYouType(assetDetail.assetName),
            description: validateNonZeroLengthTextAsYouType(assetDetail.description),
            tags: validateRequiredTagTypeSelected(
                selectedTags.map((tag) => tag.value!),
                tagTypes
            ),
        };
        setValidationText(validation);

        const isValid = !(validation.assetName || validation.description || validation.tags);
        setIsValid(isValid);
    }, [selectedTags, assetDetail.assetName, assetDetail.description, isFormTouched]);

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
                                setInProgress(true);
                                setIsFormTouched(true);
                                update(
                                    assetDetail,
                                    setError,
                                    setComplete,
                                    isValid
                                );
                            }}
                            disabled={(inProgress && !error.isError) || !isValid}
                        >
                            Update Asset
                        </Button>
                    </SpaceBetween>
                </Box>
            }
            header="Update Asset"
        >
            <SpaceBetween direction="vertical" size="l">
                <FormField
                    label={`${Synonyms.Asset} Name`}
                    errorText={isFormTouched && validationText.assetName}
                >
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
                <FormField
                    label={`${Synonyms.Asset} Description`}
                    errorText={isFormTouched && validationText.description}
                >
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
                {error.isError && (
                    <ProgressBar
                        value={0}
                        label={"Update status"}
                        status={"error"}
                        additionalInfo={error.message}
                    ></ProgressBar>
                )}
                {complete && !error.isError && (
                    <ProgressBar
                        value={100}
                        label={"Update status"}
                        status={"success"}
                        additionalInfo={"Update complete"}
                    ></ProgressBar>
                )}
            </SpaceBetween>
        </Modal>
    );
};
