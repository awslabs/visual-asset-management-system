/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    Modal,
    Box,
    SpaceBetween,
    Button,
    Form,
    FormField,
    Input,
    Textarea,
    MultiselectProps,
    Select,
    SelectProps,
    Checkbox,
} from "@cloudscape-design/components";
import { useState, useEffect } from "react";
import { createDatabase, updateDatabase, fetchBuckets } from "../../services/APIService";
import {
    fetchComplianceSchemas,
    bindSchemaToDatabase,
    unbindSchemaFromDatabase,
    getDatabaseBindings,
} from "../../services/ComplianceService";
import { useAllowedRoutes } from "../../features/orchestration/permissions/useAllowedRoutes";
import Synonyms from "../../synonyms";

// Compliance routes behind the schema-binding field; the field is shown only when the caller
// may list schemas and bind one to the database.
const COMPLIANCE_SCHEMAS_API_ROUTE = "/compliance/schemas";
const COMPLIANCE_BIND_DATABASE_API_ROUTE = "/compliance/bind/{databaseId}";
// The bucket listing behind the default-bucket selector. It is an administrator route, so it is
// read only once the modal opens and only when the caller may call it: the Databases page keeps
// this component mounted (as the create modal and as the edit modal of a selected row) for
// callers who may only list databases.
const BUCKETS_API_ROUTE = "/buckets";

interface CreateDatabaseProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
    initState?: DatabaseFields;
}

interface DatabaseFields {
    databaseId: string;
    description: string;
    defaultBucketId?: string;
    restrictMetadataOutsideSchemas?: boolean;
    restrictFileUploadsToExtensions?: string;
}

interface BucketOption {
    bucketId: string;
    bucketName: string;
    baseAssetsPrefix: string;
    // The VAMS default asset bucket, which houses pipeline template data and execution-time run I/O.
    isDefault?: boolean;
    // The bucket's Region; absent on a bucket in the deployment Region registered without one.
    bucketRegion?: string | null;
    bucketAccountId?: string | null;
}

// "name - prefix (region) (VAMS default)", with the optional parts present when known.
function bucketOptionLabel(bucket: BucketOption): string {
    return `${bucket.bucketName}${bucket.baseAssetsPrefix ? ` - ${bucket.baseAssetsPrefix}` : ""}${
        bucket.bucketRegion ? ` (${bucket.bucketRegion})` : ""
    }${bucket.isDefault ? " (VAMS default)" : ""}`;
}

// null when the name holds only letters, digits, - and _; otherwise the character-set message
function validateDatabaseNameLowercase(name: string) {
    return name.match(/^[-_a-zA-Z0-9]*$/) !== null
        ? null
        : "No special characters or spaces except - and _";
}

// null when the name is 4-63 characters long; otherwise the length message
function validateDatabaseNameLength(name: string) {
    return name.length >= 4 && name.length <= 63 ? null : "Between 4 and 63 characters";
}

// databaseId cannot be named "GLOBAL" (case insenstive)
function validateDatabaseNameGlobal(name: string) {
    return name.toLowerCase() !== "global"
        ? null
        : `${Synonyms.Database} Id cannot be named GLOBAL`;
}

// chain together the above three functions, when they return null, then return null
function validateDatabaseName(name: string) {
    return (
        validateDatabaseNameLowercase(name) ||
        validateDatabaseNameLength(name) ||
        validateDatabaseNameGlobal(name)
    );
}

// when a string is between the given min and max characters, return null, otherwise return an error message including the range
function validateDatabaseDescriptionLength(description: string) {
    const min = 4,
        max = 256;
    // The API removes surrounding whitespace before applying its own length constraint, so
    // the trimmed length is what decides whether a value is accepted.
    const trimmed = description.trim();
    return trimmed.length >= min && trimmed.length <= max
        ? null
        : `Between ${min} and ${max} characters`;
}

// Validate file extensions format
function validateFileExtensions(extensions: string): string | null {
    if (!extensions || extensions.trim() === "") {
        return null; // Empty is valid (means no restrictions)
    }

    // Check for .all special case
    if (extensions.trim().toLowerCase() === ".all") {
        return null;
    }

    // Split by comma and validate each extension
    const extList = extensions.split(",").map((ext) => ext.trim());

    for (const ext of extList) {
        // Must start with a dot
        if (!ext.startsWith(".")) {
            return "Each extension must start with a dot (e.g., .jpg)";
        }
        // Must have at least one character after the dot
        if (ext.length < 2) {
            return "Extensions must have at least one character after the dot";
        }
        // Only allow alphanumeric characters after the dot
        if (!/^\.[a-zA-Z0-9]+$/.test(ext)) {
            return "Extensions can only contain letters and numbers after the dot";
        }
    }

    return null;
}

// The fields a database save sends, with an absent value written as its default, so a form and
// the database it was loaded from compare equal when nothing was edited.
function databaseFields(state: DatabaseFields) {
    return {
        databaseId: state.databaseId || "",
        description: state.description || "",
        defaultBucketId: state.defaultBucketId || "",
        restrictMetadataOutsideSchemas: state.restrictMetadataOutsideSchemas || false,
        restrictFileUploadsToExtensions: state.restrictFileUploadsToExtensions || "",
    };
}

function sameDatabaseFields(a: DatabaseFields, b: DatabaseFields): boolean {
    return JSON.stringify(databaseFields(a)) === JSON.stringify(databaseFields(b));
}

export default function CreateDatabase({
    open,
    setOpen,
    setReload,
    initState,
}: CreateDatabaseProps) {
    // const initFormState: DatabaseFields = {
    //     databaseId: "",
    //     description: "",
    // };
    // if (initState) {
    //     Object.assign(initFormState, initState);
    // }
    const [formState, setFormState] = useState<DatabaseFields>({
        databaseId: "",
        description: "",
        ...initState,
    });
    // The database as last saved: the record being edited, or null until a create succeeds. A
    // save is sent only when the form differs from it.
    const [savedState, setSavedState] = useState<DatabaseFields | null>(
        initState?.databaseId ? { ...initState } : null
    );

    // eslint-disable-next-line no-mixed-operators
    const createOrUpdate = (initState && initState.databaseId && "Update") || "Create";

    const [selectedOptions, setSelectedOptions] = useState<MultiselectProps.Option[]>([]);
    const [buckets, setBuckets] = useState<BucketOption[]>([]);
    const [selectedBucket, setSelectedBucket] = useState<SelectProps.Option | null>(null);
    const [loadingBuckets, setLoadingBuckets] = useState(false);

    const [groupOptions, setGroupOptions] = useState<MultiselectProps.Option[]>([]);
    const [loadingGroups, setLoadingGroups] = useState(true);
    const [inProgress, setInProgress] = useState(false);
    const [formError, setFormError] = useState("");

    // Compliance schema binding
    const { can: canCallRoute } = useAllowedRoutes();
    const canBindComplianceSchema =
        canCallRoute("GET", COMPLIANCE_SCHEMAS_API_ROUTE) &&
        canCallRoute("PUT", COMPLIANCE_BIND_DATABASE_API_ROUTE);
    const canListBuckets = canCallRoute("GET", BUCKETS_API_ROUTE);
    const [schemaOptions, setSchemaOptions] = useState<SelectProps.Option[]>([]);
    const [selectedSchema, setSelectedSchema] = useState<SelectProps.Option | null>(null);
    const [loadingSchemas, setLoadingSchemas] = useState(false);

    const [initialSchemaValue, setInitialSchemaValue] = useState<string | null>(null);

    // Everything the form reads is loaded when the modal opens, not on mount: the component is
    // mounted while closed.
    useEffect(() => {
        if (!open || !canBindComplianceSchema) return;
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
    }, [open, canBindComplianceSchema]);

    // Load current schema binding when editing
    useEffect(() => {
        if (!open || !canBindComplianceSchema || !initState?.databaseId) return;
        const loadBinding = async () => {
            const [success, result] = await getDatabaseBindings(initState.databaseId);
            if (success && typeof result !== "string") {
                const bound = result.databaseSchema || null;
                setSelectedSchema(bound ? { label: bound, value: bound } : null);
                setInitialSchemaValue(bound);
            }
        };
        loadBinding();
    }, [open, canBindComplianceSchema, initState?.databaseId]);

    // Fetch buckets the first time the modal opens
    const [bucketsRequested, setBucketsRequested] = useState(false);
    useEffect(() => {
        if (!open || !canListBuckets || bucketsRequested) return;
        setBucketsRequested(true);
        const loadBuckets = async () => {
            setLoadingBuckets(true);
            try {
                const bucketsData = await fetchBuckets();
                if (bucketsData && bucketsData.Items) {
                    // API returns an object with Items array
                    const items: BucketOption[] = bucketsData.Items;
                    setBuckets(items);
                    console.log("Loaded buckets:", items);

                    // Show the bucket the database already uses as selected.
                    const current = items.find(
                        (bucket) => bucket.bucketId === formState.defaultBucketId
                    );
                    if (current) {
                        setSelectedBucket({
                            label: bucketOptionLabel(current),
                            value: current.bucketId,
                        });
                    } else if (items.length === 1 && !formState.defaultBucketId) {
                        // If there's only one bucket and none is set yet, select it by default
                        const bucket = items[0];
                        const option = {
                            label: bucketOptionLabel(bucket),
                            value: bucket.bucketId,
                        };
                        setSelectedBucket(option);
                        setFormState((prevState) => ({
                            ...prevState,
                            defaultBucketId: bucket.bucketId,
                        }));
                        console.log("Auto-selected the only available bucket:", bucket.bucketId);
                    }
                } else {
                    console.error("Failed to fetch buckets or no buckets found:", bucketsData);
                }
            } catch (error) {
                console.error("Error fetching buckets:", error);
            } finally {
                setLoadingBuckets(false);
            }
        };

        loadBuckets();
    }, [open, canListBuckets, bucketsRequested, formState.defaultBucketId]);

    // An empty create form for the next database; it keeps the selected bucket so the selector and
    // the form agree.
    const resetCreateForm = () => {
        setFormState((prev) => ({
            databaseId: "",
            description: "",
            defaultBucketId: prev.defaultBucketId,
        }));
        setSavedState(null);
        setSelectedSchema(null);
        setInitialSchemaValue(null);
    };

    // Cancel or dismiss: the unsaved edits are dropped, so an edit form reopens on the database as
    // last saved and a create form reopens empty.
    const closeModal = () => {
        setOpen(false);
        setFormError("");
        if (!initState?.databaseId) {
            resetCreateForm();
            return;
        }
        if (savedState) {
            setFormState(savedState);
            const bucket = buckets.find((b) => b.bucketId === savedState.defaultBucketId);
            setSelectedBucket(
                bucket ? { label: bucketOptionLabel(bucket), value: bucket.bucketId } : null
            );
        }
        setSelectedSchema(
            initialSchemaValue ? { label: initialSchemaValue, value: initialSchemaValue } : null
        );
    };

    // Sends the database save only when a database field changed and the schema binding only when
    // the schema selection changed, in that order. A refused save sends no binding; any refusal
    // keeps the modal open with the message, and the modal closes only when every request sent
    // succeeded. With nothing changed it closes without sending anything.
    const submit = async () => {
        const selectedValue = (selectedSchema?.value as string | undefined) || null;
        const sendSave = savedState === null || !sameDatabaseFields(formState, savedState);
        const sendBinding = canBindComplianceSchema && selectedValue !== initialSchemaValue;
        if (!sendSave && !sendBinding) {
            closeModal();
            return;
        }

        setInProgress(true);
        setFormError("");
        try {
            if (sendSave) {
                const fields = databaseFields(formState);
                const res = savedState
                    ? await updateDatabase(fields)
                    : await createDatabase(fields);
                if (!res || !res[0]) {
                    // Display the actual error message from the API
                    setFormError(
                        (Array.isArray(res) && res[1]) ||
                            `Unable to ${createOrUpdate} ${Synonyms.database}`
                    );
                    return;
                }
                setSavedState({ ...formState });
                setReload(true);
            }

            if (sendBinding) {
                const [bound, message] = selectedValue
                    ? await bindSchemaToDatabase(formState.databaseId, selectedValue)
                    : await unbindSchemaFromDatabase(formState.databaseId);
                if (!bound) {
                    setFormError(
                        sendSave
                            ? `${Synonyms.Database} saved, but the compliance schema binding failed: ${message}`
                            : `The compliance schema binding failed: ${message}`
                    );
                    return;
                }
                setInitialSchemaValue(selectedValue);
            }

            // Every request sent succeeded. An edit form already holds what was saved.
            setOpen(false);
            if (!initState?.databaseId) resetCreateForm();
        } catch (err: any) {
            // Console logging only: a % specifier in the interpolated
            // value can at most garble this one log line; nothing is
            // executed, stored or returned from it.
            // nosemgrep: javascript.lang.security.audit.unsafe-formatstring.unsafe-formatstring
            console.log(`${createOrUpdate} database error`, err);
            setFormError(err?.message || `Unable to ${createOrUpdate} ${Synonyms.database}`);
        } finally {
            setInProgress(false);
        }
    };

    return (
        <Modal
            onDismiss={closeModal}
            visible={open}
            closeAriaLabel="Close modal"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={closeModal}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={submit}
                            disabled={
                                inProgress ||
                                !(
                                    validateDatabaseName(formState.databaseId) === null &&
                                    validateDatabaseDescriptionLength(formState.description) ===
                                        null &&
                                    formState.defaultBucketId &&
                                    validateFileExtensions(
                                        formState.restrictFileUploadsToExtensions || ""
                                    ) === null
                                )
                            }
                            data-testid={`${createOrUpdate}-database-button`}
                        >
                            {`${createOrUpdate} ${Synonyms.Database}`}
                        </Button>
                    </SpaceBetween>
                </Box>
            }
            header={`${createOrUpdate} ${Synonyms.Database}`}
        >
            <form onSubmit={(e) => e.preventDefault()}>
                <Form errorText={formError}>
                    <SpaceBetween direction="vertical" size="s">
                        <FormField
                            label={`${Synonyms.Database} Name`}
                            errorText={validateDatabaseName(formState.databaseId)}
                            constraintText="Required. 4-63 characters: letters, numbers, - and _ only."
                        >
                            <Input
                                value={formState.databaseId}
                                disabled={inProgress || savedState !== null}
                                onChange={({ detail }) =>
                                    setFormState({ ...formState, databaseId: detail.value })
                                }
                                placeholder={`${Synonyms.Database} Name`}
                                data-testid="database-name"
                            />
                        </FormField>
                        <FormField
                            label={`${Synonyms.Database} Description`}
                            constraintText="Required. Max 256 characters."
                            errorText={validateDatabaseDescriptionLength(formState.description)}
                        >
                            <Textarea
                                value={formState.description}
                                disabled={inProgress}
                                onChange={({ detail }) =>
                                    setFormState({ ...formState, description: detail.value })
                                }
                                rows={4}
                                placeholder={`${Synonyms.Database} Description`}
                                data-testid="database-desc"
                            />
                        </FormField>
                        <FormField
                            label="Default Bucket and Prefix"
                            constraintText={`Required. Select a bucket and prefix for this ${Synonyms.database}.`}
                            errorText={
                                !formState.defaultBucketId ? "A default bucket is required" : null
                            }
                        >
                            <Select
                                selectedOption={selectedBucket}
                                onChange={({ detail }) => {
                                    setSelectedBucket(detail.selectedOption);
                                    setFormState({
                                        ...formState,
                                        defaultBucketId: detail.selectedOption.value || "",
                                    });
                                }}
                                options={buckets.map((bucket) => ({
                                    label: bucketOptionLabel(bucket),
                                    value: bucket.bucketId,
                                }))}
                                placeholder="Select a bucket"
                                loadingText="Loading buckets"
                                statusType={loadingBuckets ? "loading" : "finished"}
                                disabled={inProgress}
                                data-testid="database-bucket"
                            />
                        </FormField>
                        <FormField
                            label="Restrict Metadata Outside Schemas"
                            description={`Restricts all metadata for components below this ${Synonyms.database} to only allow fields based on applied metadata schema. If no schema is applied, this option is disregarded.`}
                        >
                            <Checkbox
                                checked={formState.restrictMetadataOutsideSchemas || false}
                                onChange={({ detail }) =>
                                    setFormState({
                                        ...formState,
                                        restrictMetadataOutsideSchemas: detail.checked,
                                    })
                                }
                                disabled={inProgress}
                            >
                                Enable metadata schema restrictions
                            </Checkbox>
                        </FormField>
                        <FormField
                            label="Restrict File Upload Extensions"
                            description="Restricts file uploads through web and CLI to specific extensions (comma-delimited). Use '.all' or leave blank to allow all extensions (except system-wide restrictions). Does not apply to files directly edited in the S3 bucket."
                            constraintText="Optional. Comma-separated list of extensions (e.g., .jpg,.png,.pdf)"
                            errorText={validateFileExtensions(
                                formState.restrictFileUploadsToExtensions || ""
                            )}
                        >
                            <Input
                                value={formState.restrictFileUploadsToExtensions || ""}
                                onChange={({ detail }) =>
                                    setFormState({
                                        ...formState,
                                        restrictFileUploadsToExtensions: detail.value,
                                    })
                                }
                                placeholder="e.g., .jpg,.png,.pdf or leave blank for all"
                                disabled={inProgress}
                                data-testid="database-file-extensions"
                            />
                        </FormField>
                        {canBindComplianceSchema && (
                            <FormField
                                label="Compliance Schema"
                                description={`Bind a compliance schema to this ${Synonyms.database}. All ${Synonyms.assets} in the ${Synonyms.database} inherit this schema unless overridden at the ${Synonyms.asset} level.`}
                                constraintText="Optional. Schema can be changed later."
                            >
                                <Select
                                    selectedOption={selectedSchema}
                                    onChange={({ detail }) =>
                                        setSelectedSchema(
                                            detail.selectedOption?.value
                                                ? detail.selectedOption
                                                : null
                                        )
                                    }
                                    options={[
                                        { label: "None (no compliance schema)", value: "" },
                                        ...schemaOptions,
                                    ]}
                                    placeholder="Select a compliance schema"
                                    loadingText="Loading schemas"
                                    statusType={loadingSchemas ? "loading" : "finished"}
                                    disabled={inProgress}
                                    filteringType="auto"
                                    data-testid="database-compliance-schema"
                                />
                            </FormField>
                        )}
                    </SpaceBetween>
                </Form>
            </form>
        </Modal>
    );
}
