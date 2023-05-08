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
    Multiselect,
    MultiselectProps,
} from "@cloudscape-design/components";
import { useEffect, useState } from "react";
import { API } from "aws-amplify";

interface CreateDatabaseProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
    initState?: DatabaseFields;
}

interface DatabaseFields {
    databaseId: string;
    description: string;
    acl?: string[];
}

// when a string is all lower case, return null, otherwise return the string "All lower case letters only"
function validateDatabaseNameLowercase(name: string) {
    return name.match(/^[a-z0-9_-]+$/) !== null
        ? null
        : "All lower case letters only. No special characters except - and _";
}

// when a string is between 4 and 64 characters, return null, otherwise return the string "Between 4 and 64 characters"
function validateDatabaseNameLength(name: string) {
    return name.length >= 4 && name.length <= 64 ? null : "Between 4 and 64 characters";
}

// chain together the above three functions, when they return null, then return null
function validateDatabaseName(name: string) {
    return validateDatabaseNameLowercase(name) || validateDatabaseNameLength(name);
}

// when a string is between the given min and max characters, return null, otherwise return an error message including the range
function validateDatabaseDescriptionLength(description: string) {
    const min = 4,
        max = 256;
    return description.length >= min && description.length <= max
        ? null
        : `Between ${min} and ${max} characters`;
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

    const [groupOptions, setGroupOptions] = useState<MultiselectProps.Option[]>([]);
    const [loadingGroups, setLoadingGroups] = useState(true);
    const [inProgress, setInProgress] = useState(false);

    useEffect(() => {
        if (!loadingGroups) return;
        API.get("api", `auth/groups`, {}).then((res) => {
            console.log("auth groups", res);
            const opts: MultiselectProps.Option[] = res.claims.map((value: string) => ({
                value,
                label: value,
                description: "Unlabeled group",
            }));
            setGroupOptions(opts);
            setSelectedOptions(
                opts.filter(
                    (x) =>
                        formState.acl &&
                        x.value !== undefined &&
                        formState.acl.indexOf(x.value) > -1
                )
            );
            setLoadingGroups(false);
        });
    }, [formState.acl, loadingGroups]);

    return (
        <Modal
            onDismiss={() => setOpen(false)}
            visible={open}
            closeAriaLabel="Close modal"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={() => setOpen(false)}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={() => {
                                setInProgress(true);
                                API.put("api", `databases`, {
                                    body: {
                                        ...formState,
                                        acl: selectedOptions.map((option) => option.value),
                                    },
                                })
                                    .then((res) => {
                                        console.log("create database", res);
                                        setOpen(false);
                                        setReload(true);
                                    })
                                    .catch((err) => {
                                        console.log("create database error", err);
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
                                        null
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
                <Form>
                    <SpaceBetween direction="vertical" size="s">
                        <FormField
                            label="Database Name"
                            errorText={validateDatabaseName(formState.databaseId)}
                            constraintText="Required. All lower case, no special chars or spaces except - and _ only letters for first character min 4 and max 64"
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
                            label="Group Access Control List"
                            description="The groups that can access this database"
                        >
                            <Multiselect
                                selectedOptions={selectedOptions}
                                disabled={inProgress}
                                onChange={({ detail }) =>
                                    setSelectedOptions(
                                        detail.selectedOptions as MultiselectProps.Option[]
                                    )
                                }
                                deselectAriaLabel={(e) => `Remove ${e.label}`}
                                loadingText={loadingGroups ? "Loading..." : undefined}
                                options={groupOptions}
                                filteringType="auto"
                                placeholder="Choose options"
                                selectedAriaLabel="Selected"
                                data-testid="database-acl-multiselect"
                            />
                        </FormField>
                    </SpaceBetween>
                </Form>
            </form>
        </Modal>
    );
}
