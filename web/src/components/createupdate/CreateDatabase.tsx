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
}

// when a string is all lower case, return null, otherwise return the string "All lower case letters only"
function validateDatabaseNameLowercase(name: string) {
    return name.match(/^[-_a-zA-Z0-9]{3,63}$/) !== null
        ? null
        : "No special characters or spaces except - and _";
}

// when a string is between 4 and 64 characters, return null, otherwise return the string "Between 3 and 64 characters"
function validateDatabaseNameLength(name: string) {
    return name.length >= 4 && name.length <= 64 ? null : "Between 3 and 64 characters";
}

// databaseId cannot be named "GLOBAL" (case insenstive)
function validateDatabaseNameGlobal(name: string) {
    return name.toLowerCase() !== "global" ? null : "DatabaseId cannot be named GLOBAL";
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
    return description.length >= min && description.length <= max
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

    // eslint-disable-next-line no-mixed-operators
    const createOrUpdate = (initState && initState.databaseId && "Update") || "Create";

    const [selectedOptions, setSelectedOptions] = useState<MultiselectProps.Option[]>([]);
    const [buckets, setBuckets] = useState<BucketOption[]>([]);
    const [selectedBucket, setSelectedBucket] = useState<SelectProps.Option | null>(null);
    const [loadingBuckets, setLoadingBuckets] = useState(true);

    const [groupOptions, setGroupOptions] = useState<MultiselectProps.Option[]>([]);
    const [loadingGroups, setLoadingGroups] = useState(true);
    const [inProgress, setInProgress] = useState(false);
    const [formError, setFormError] = useState("");

    // Fetch buckets when component loads
    useEffect(() => {
        const loadBuckets = async () => {
            setLoadingBuckets(true);
            try {
                const bucketsData = await fetchBuckets();
                if (bucketsData && bucketsData.Items) {
                    // API returns an object with Items array
                    setBuckets(bucketsData.Items);
                    console.log("Loaded buckets:", bucketsData.Items);

                    // If there's only one bucket, select it by default
                    if (bucketsData.Items.length === 1) {
                        const bucket = bucketsData.Items[0];
                        const option = {
                            label: `${bucket.bucketName}${
                                bucket.baseAssetsPrefix ? ` - ${bucket.baseAssetsPrefix}` : ""
                            }`,
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
    }, []);

    return (
        <Modal
            onDismiss={() => {
                setOpen(false);
                setFormState({ databaseId: "", description: "" });
                setFormError("");
            }}
            visible={open}
            closeAriaLabel="Close modal"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            variant="link"
                            onClick={() => {
                                setOpen(false);
                                setFormState({ databaseId: "", description: "" });
                                setFormError("");
                            }}
                        >
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={() => {
                                setInProgress(true);
                                const isEdit = initState && initState.databaseId;

                                const apiCall = isEdit
                                    ? updateDatabase({
                                          databaseId: formState.databaseId,
                                          description: formState.description,
                                          defaultBucketId: formState.defaultBucketId || "",
                                          restrictMetadataOutsideSchemas:
                                              formState.restrictMetadataOutsideSchemas || false,
                                          restrictFileUploadsToExtensions:
                                              formState.restrictFileUploadsToExtensions || "",
                                      })
                                    : createDatabase({
                                          databaseId: formState.databaseId,
                                          description: formState.description,
                                          defaultBucketId: formState.defaultBucketId || "",
                                          restrictMetadataOutsideSchemas:
                                              formState.restrictMetadataOutsideSchemas || false,
                                          restrictFileUploadsToExtensions:
                                              formState.restrictFileUploadsToExtensions || "",
                                      });

                                apiCall
                                    .then((res) => {
                                        if (res && res[0]) {
                                            setOpen(false);
                                            setReload(true);
                                        } else {
                                            // Display the actual error message from the API
                                            let msg =
                                                res[1] || `Unable to ${createOrUpdate} database`;
                                            setFormError(msg);
                                        }
                                    })
                                    .catch((err) => {
                                        console.log(`${createOrUpdate} database error`, err);
                                        let msg =
                                            err.message || `Unable to ${createOrUpdate} database`;
                                        setFormError(msg);
                                    })
                                    .finally(() => {
                                        setInProgress(false);
                                    });
                            }}
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
                            {createOrUpdate} Database
                        </Button>
                    </SpaceBetween>
                </Box>
            }
            header={`${createOrUpdate} Database`}
        >
            <form onSubmit={(e) => e.preventDefault()}>
                <Form errorText={formError}>
                    <SpaceBetween direction="vertical" size="s">
                        <FormField
                            label="Database Name"
                            errorText={validateDatabaseName(formState.databaseId)}
                            constraintText="Required. No special chars or spaces except - and 4 and max 64"
                        >
                            <Input
                                value={formState.databaseId}
                                disabled={
                                    inProgress ||
                                    (initState && initState.databaseId && true) ||
                                    false
                                }
                                onChange={({ detail }) =>
                                    setFormState({ ...formState, databaseId: detail.value })
                                }
                                placeholder="Database Name"
                                data-testid="database-name"
                            />
                        </FormField>
                        <FormField
                            label="Database Description"
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
                                placeholder="Database Description"
                                data-testid="database-desc"
                            />
                        </FormField>
                        <FormField
                            label="Default Bucket and Prefix"
                            constraintText="Required. Select a bucket and prefix for this database."
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
                                    label: `${bucket.bucketName}${
                                        bucket.baseAssetsPrefix
                                            ? ` - ${bucket.baseAssetsPrefix}`
                                            : ""
                                    }`,
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
                            description="Restricts all metadata for components below this database to only allow fields based on applied metadata schema. If no schema is applied, this option is disregarded."
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
                    </SpaceBetween>
                </Form>
            </form>
        </Modal>
    );
}
