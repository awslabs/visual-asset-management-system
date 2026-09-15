import {
    Modal,
    Select,
    SelectProps,
    SpaceBetween,
    Multiselect,
} from "@cloudscape-design/components";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Synonyms from "../../synonyms";
import { buildTagOptionGroups } from "../../common/utils/tagOptions";
import Input from "@cloudscape-design/components/input";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { OptionDefinition } from "@cloudscape-design/components/internal/components/option/interfaces";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import { fetchTagsForAsset, fetchTagTypesForAsset, updateAsset } from "../../services/APIService";
import {
    fetchComplianceSchemas,
    fetchComplianceState,
    bindSchemaToAsset,
    unbindSchemaFromAsset,
} from "../../services/ComplianceService";
import { useAllowedRoutes } from "../../features/orchestration/permissions/useAllowedRoutes";
import { TagType } from "../../pages/Tag/TagType.interface";
import {
    validateRequiredTagTypeSelected,
    validateNonZeroLengthTextAsYouType,
    enforceableRequiredTagTypes,
} from "../../pages/AssetUpload/validations";

// Compliance routes behind the schema-binding field.
const COMPLIANCE_SCHEMAS_API_ROUTE = "/compliance/schemas";
const COMPLIANCE_BIND_ASSET_API_ROUTE = "/compliance/bind/{databaseId}/{assetId}";

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

const tags: any[] = [];
let assetTags: any[] = [];
let tagTypes: TagType[] = [];

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
        await updateAsset({
            databaseId: updatedAsset.databaseId,
            assetId: updatedAsset.assetId,
            updateData: updateAssetData,
        });

        // Mark as complete
        setComplete(true);
    } catch (err: any) {
        setError({
            isError: true,
            message: err.message || "An error occurred during the update process",
        });
    }
};

export const UpdateAsset = ({ asset, ...props }: UpdateAssetProps) => {
    const { databaseId: routeDatabaseId } = useParams<{ databaseId: string }>();
    // The scope the tag-type constraint is read for: the asset's own database, or the route's when
    // the record in hand does not carry one. Never the unscoped list — that surfaces every other
    // database's required tag types as constraints on this form.
    const tagScopeDatabaseId = asset?.databaseId || routeDatabaseId;
    const [assetDetail, setAssetDetail] = useState(asset);
    const [error, setError] = useState({ isError: false, message: "" });
    const [complete, setComplete] = useState(false);
    const [isValid, setIsValid] = useState(true);
    const [isFormTouched, setIsFormTouched] = useState(false);
    const [inProgress, setInProgress] = useState(false);

    // Compliance schema binding: the field is shown only when the caller may list schemas and
    // bind one to the asset. The asset-level override is loaded first so an untouched field
    // leaves the existing binding alone.
    const { can: canCallRoute } = useAllowedRoutes();
    const canBindComplianceSchema =
        canCallRoute("GET", COMPLIANCE_SCHEMAS_API_ROUTE) &&
        canCallRoute("PUT", COMPLIANCE_BIND_ASSET_API_ROUTE);
    const [schemaOptions, setSchemaOptions] = useState<SelectProps.Option[]>([]);
    const [selectedSchema, setSelectedSchema] = useState<SelectProps.Option | null>(null);
    const [initialSchemaValue, setInitialSchemaValue] = useState<string | null>(null);
    const [loadingSchemas, setLoadingSchemas] = useState(false);

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
        // Scope to global + the asset's database so tags from other databases are hidden.
        if (!tagScopeDatabaseId) return;
        fetchTagsForAsset({ databaseId: tagScopeDatabaseId }).then((res) => {
            tags.length = 0; // Clear without losing reference
            if (res && Array.isArray(res)) {
                // Grouped, scope-labelled and ordered by the shared helper so this picker and the
                // upload form present tags identically.
                const storedTypes = JSON.parse(localStorage.getItem("tagTypes") || "[]");
                buildTagOptionGroups(res, storedTypes).forEach((group) => tags.push(group));
            }
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

        // Scope to global + the asset's database. The unscoped list let required tag types
        // from OTHER databases block this form, because the tag picker only offers in-scope tags.
        if (!tagScopeDatabaseId) return;
        fetchTagTypesForAsset({ databaseId: tagScopeDatabaseId }).then((res) => {
            if (!Array.isArray(res)) {
                return;
            }
            tagTypes = res;

            if (tagTypes.length) {
                // A required tag type with no tags cannot be satisfied, so it is not announced as a
                // constraint either — the same set the validator enforces.
                const requiredTagTypes = enforceableRequiredTagTypes(tagTypes);

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
    }, [tagScopeDatabaseId]);

    useEffect(() => {
        if (!canBindComplianceSchema) return;
        const loadSchemas = async () => {
            setLoadingSchemas(true);
            const [success, result] = await fetchComplianceSchemas();
            if (success && Array.isArray(result)) {
                setSchemaOptions(
                    result.map((s) => ({
                        label: s.schemaName,
                        value: s.schemaName,
                        description: s.description,
                    }))
                );
            }
            setLoadingSchemas(false);
        };
        loadSchemas();
    }, [canBindComplianceSchema]);

    useEffect(() => {
        if (!canBindComplianceSchema || !asset?.databaseId || !asset?.assetId) return;
        const loadBinding = async () => {
            const [success, result] = await fetchComplianceState(asset.databaseId, asset.assetId);
            if (
                success &&
                typeof result !== "string" &&
                result.schemaSource === "asset" &&
                result.schemaName
            ) {
                setSelectedSchema({ label: result.schemaName, value: result.schemaName });
                setInitialSchemaValue(result.schemaName);
            }
        };
        loadBinding();
    }, [canBindComplianceSchema, asset?.databaseId, asset?.assetId]);

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
                            onClick={async () => {
                                setInProgress(true);
                                setIsFormTouched(true);
                                await update(assetDetail, setError, setComplete, isValid);
                                if (
                                    canBindComplianceSchema &&
                                    isValid &&
                                    assetDetail.databaseId &&
                                    assetDetail.assetId
                                ) {
                                    const selectedValue = selectedSchema?.value || null;
                                    if (selectedValue && selectedValue !== initialSchemaValue) {
                                        await bindSchemaToAsset(
                                            assetDetail.databaseId,
                                            assetDetail.assetId,
                                            selectedValue
                                        );
                                    } else if (!selectedValue && initialSchemaValue) {
                                        await unbindSchemaFromAsset(
                                            assetDetail.databaseId,
                                            assetDetail.assetId
                                        );
                                    }
                                }
                            }}
                            disabled={(inProgress && !error.isError) || !isValid}
                        >
                            {`Update ${Synonyms.Asset}`}
                        </Button>
                    </SpaceBetween>
                </Box>
            }
            header={`Update ${Synonyms.Asset}`}
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
                {canBindComplianceSchema && (
                    <FormField
                        label="Compliance Schema"
                        description={`Override the ${Synonyms.database}-level compliance schema for this ${Synonyms.asset}. Choose "Inherit" to use the ${Synonyms.database} schema.`}
                        constraintText={`Optional. ${Synonyms.Asset}-level binding overrides ${Synonyms.database}-level.`}
                    >
                        <Select
                            selectedOption={selectedSchema}
                            onChange={({ detail }) => {
                                setSelectedSchema(
                                    detail.selectedOption?.value ? detail.selectedOption : null
                                );
                                setIsFormTouched(true);
                            }}
                            options={[
                                { label: `Inherit from ${Synonyms.database}`, value: "" },
                                ...schemaOptions,
                            ]}
                            placeholder={`Inherit from ${Synonyms.database}`}
                            loadingText="Loading schemas"
                            statusType={loadingSchemas ? "loading" : "finished"}
                            filteringType="auto"
                            data-testid="asset-compliance-schema"
                        />
                    </FormField>
                )}
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
