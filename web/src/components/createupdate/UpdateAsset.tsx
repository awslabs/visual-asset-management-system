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
import { buildTagOptionGroups, readCachedTagTypes } from "../../common/utils/tagOptions";
import Input from "@cloudscape-design/components/input";
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { OptionDefinition } from "@cloudscape-design/components/internal/components/option/interfaces";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import { fetchTagsForAsset, fetchTagTypesForAsset, updateAsset } from "../../services/APIService";
import {
    fetchComplianceSchemas,
    bindableSchemas,
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

// The fields an asset save sends, with an absent value written as its default, so the form and the
// asset it was loaded from compare equal when nothing was edited. Tag order is not a change.
const assetFields = (asset: any) => ({
    assetName: asset?.assetName || "",
    description: asset?.description || "",
    isDistributable: asset?.isDistributable === true,
    tags: [...(asset?.tags || [])].sort(),
});

const assetFieldsChanged = (a: any, b: any): boolean =>
    JSON.stringify(assetFields(a)) !== JSON.stringify(assetFields(b));

// Saves the asset and resolves with the outcome; a refusal is reported through setError.
const update = async (
    updatedAsset: any,
    setError: (error: { isError: boolean; message: string }) => void
): Promise<boolean> => {
    setError({ isError: false, message: "" });
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

        // Update asset metadata using the new API endpoint. updateAsset resolves with
        // [false, message] for a rejected request rather than throwing.
        const result = await updateAsset({
            databaseId: updatedAsset.databaseId,
            assetId: updatedAsset.assetId,
            updateData: updateAssetData,
        });
        if (!Array.isArray(result) || result[0] !== true) {
            setError({
                isError: true,
                message:
                    (Array.isArray(result) && result[1]) ||
                    "An error occurred during the update process",
            });
            return false;
        }
        return true;
    } catch (err: any) {
        setError({
            isError: true,
            message: err.message || "An error occurred during the update process",
        });
        return false;
    }
};

export const UpdateAsset = ({ asset, ...props }: UpdateAssetProps) => {
    const { databaseId: routeDatabaseId } = useParams<{ databaseId: string }>();
    // The scope the tag-type constraint is read for: the asset's own database, or the route's when
    // the record in hand does not carry one. Never the unscoped list — that surfaces every other
    // database's required tag types as constraints on this form.
    const tagScopeDatabaseId = asset?.databaseId || routeDatabaseId;
    const [assetDetail, setAssetDetail] = useState(asset);
    // The asset as last saved; a save is sent only when the form differs from it.
    const [savedAsset, setSavedAsset] = useState(asset);
    // Set once a save lands while the modal is open, so closing it after a later refusal still
    // refreshes the view with what was saved.
    const savedWhileOpen = useRef(false);
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

    // The modal stays mounted while closed, so closing it clears a refused update's message.
    const close = () => {
        setIsFormTouched(false);
        setError({ isError: false, message: "" });
        setInProgress(false);
        if (savedWhileOpen.current) {
            savedWhileOpen.current = false;
            props.onComplete();
        } else {
            props.onClose();
        }
    };

    // Sends the asset save only when an asset field changed and the schema binding only when the
    // schema selection changed, in that order. A refused save sends no binding; any refusal keeps
    // the modal open with the message, and the update completes only when every request sent
    // succeeded. With nothing changed the modal closes without sending anything.
    const submit = async () => {
        setIsFormTouched(true);
        if (!isValid) return;
        const selectedValue = (selectedSchema?.value as string | undefined) || null;
        const sendSave = assetFieldsChanged(assetDetail, savedAsset);
        const sendBinding =
            canBindComplianceSchema &&
            !!assetDetail.databaseId &&
            !!assetDetail.assetId &&
            selectedValue !== initialSchemaValue;
        if (!sendSave && !sendBinding) {
            close();
            return;
        }

        setInProgress(true);
        setError({ isError: false, message: "" });
        if (sendSave) {
            const saved = await update(assetDetail, setError);
            if (!saved) return;
            setSavedAsset(assetDetail);
            savedWhileOpen.current = true;
        }
        if (sendBinding) {
            const [bound, message] = selectedValue
                ? await bindSchemaToAsset(
                      assetDetail.databaseId,
                      assetDetail.assetId,
                      selectedValue
                  )
                : await unbindSchemaFromAsset(assetDetail.databaseId, assetDetail.assetId);
            if (!bound) {
                setError({
                    isError: true,
                    message: sendSave
                        ? `${Synonyms.Asset} saved, but the compliance schema binding failed: ${message}`
                        : `The compliance schema binding failed: ${message}`,
                });
                return;
            }
            setInitialSchemaValue(selectedValue);
        }
        savedWhileOpen.current = false;
        setComplete(true);
    };

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
        setSavedAsset(asset);
        // Scope to global + the asset's database so tags from other databases are hidden.
        if (!tagScopeDatabaseId) return;
        fetchTagsForAsset({ databaseId: tagScopeDatabaseId }).then((res) => {
            tags.length = 0; // Clear without losing reference
            if (res && Array.isArray(res)) {
                // Grouped, scope-labelled and ordered by the shared helper so this picker and the
                // upload form present tags identically.
                buildTagOptionGroups(res, readCachedTagTypes()).forEach((group) =>
                    tags.push(group)
                );
            }
        });
        const tagTypes = readCachedTagTypes();
        const initTags = asset.tags
            ? asset.tags.map((tagName: string) => {
                  const tagType = tagTypes.find(
                      (type: any) => Array.isArray(type?.tags) && type.tags.includes(tagName)
                  );
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
            const [success, result] = await fetchComplianceSchemas(tagScopeDatabaseId);
            if (success && Array.isArray(result)) {
                setSchemaOptions(
                    bindableSchemas(result).map((s) => ({
                        label: s.schemaName,
                        value: s.schemaName,
                        description: s.description,
                    }))
                );
            }
            setLoadingSchemas(false);
        };
        loadSchemas();
    }, [canBindComplianceSchema, tagScopeDatabaseId]);

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
            onDismiss={close}
            visible={props.isOpen}
            closeAriaLabel="Close modal"
            size="medium"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={close}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={submit}
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
                        description={`Override the ${Synonyms.database}-level compliance schema for this ${Synonyms.asset}. Choose "Inherit" to use the ${Synonyms.database} schema. Offered: the GLOBAL schemas and those available to this ${Synonyms.asset}'s ${Synonyms.database} only.`}
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
