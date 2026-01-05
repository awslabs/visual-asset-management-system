/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import {
    Box,
    Button,
    Form,
    FormField,
    Header,
    Input,
    Modal,
    Select,
    SpaceBetween,
    Table,
    Textarea,
} from "@cloudscape-design/components";
import { Optional } from "@cloudscape-design/components/internal/types";
import { API } from "aws-amplify";
import { useEffect, useState } from "react";
import { generateUUID } from "../../common/utils/utils";
import RoleGroupPermissionsTable, { RoleGroupPermission } from "./RoleGroupPermissionsTable";
import UserPermissionsTable, { UserPermission } from "./UserPermissionsTable";
// import { fetchMetadataSchema } from "../../services/APIService";
import {
    criteriaOperators,
    objectTypes,
    fieldNamesToObjectTypeMapping,
} from "../../common/constants/permissionConstraintTypes";

interface ConstraintCriteria {
    id: string;
    field: string;
    operator: string;
    value: string;
}

interface ConstraintFields {
    constraintId: string;
    name: string;
    description: string;
    appliesTo: string[];
    groupPermissions: RoleGroupPermission[];
    criteriaAnd: ConstraintCriteria[];
    criteriaOr: ConstraintCriteria[];
    userPermissions: UserPermission[];
    objectType: string;
}

interface CreateConstraintProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
    initState: any;
}

// when a string matches regex
function validateNameLowercase(name: string) {
    if (name === undefined) return null;
    return name.match(/^[-_a-zA-Z0-9]{3,63}$/) !== null
        ? null
        : "No special characters except - and _";
}

// when a string is between 3 and 64 characters, return null, otherwise return the string "Between 4 and 64 characters"
function validateNameLength(name: string) {
    if (name === undefined) return null;
    return name.length >= 3 && name.length <= 64 ? null : "Between 3 and 64 characters";
}

// chain together the above three functions, when they return null, then return null
function validateName(name: string) {
    if (name === undefined) return null;
    return validateNameLowercase(name) || validateNameLength(name);
}

// when a string is between the given min and max characters, return null, otherwise return an error message including the range
function validateDescriptionLength(description: string) {
    if (description === undefined) return null;
    const min = 4,
        max = 256;
    return description.length >= min && description.length <= max
        ? null
        : `Between ${min} and ${max} characters`;
}

export default function CreateConstraint({
    open,
    setOpen,
    setReload,
    initState,
}: CreateConstraintProps) {
    const [inProgress, setInProgress] = useState(false);
    const [formError, setFormError] = useState("");
    const createOrUpdate = (initState && "Update") || "Create";
    const [formState, setFormState] = useState<ConstraintFields>({
        constraintId: generateUUID(),
        ...initState,
    });

    const [selectedCriteriaAnd, setSelectedCriteriaAnd] = useState<ConstraintCriteria[]>([]);
    const [selectedCriteriaOr, setSelectedCriteriaOr] = useState<ConstraintCriteria[]>([]);
    // const [allMetadataChoiceFields, setAllMetadataChoiceFields] = useState<
    //     Record<string, string>[]
    // >([]);

    console.log("formState", formState, initState);

    // Filter out invalid criteria fields when editing an existing constraint
    useEffect(() => {
        if (initState && initState.objectType) {
            const validFields = fieldNamesToObjectTypeMapping[initState.objectType];
            if (validFields) {
                const validFieldValues = validFields.map((field) => field.value);

                const filterValidCriteria = (criteria: ConstraintCriteria[] | undefined) => {
                    if (!criteria) return criteria;
                    return criteria.filter((item) => validFieldValues.includes(item.field));
                };

                const filteredCriteriaAnd = filterValidCriteria(initState.criteriaAnd);
                const filteredCriteriaOr = filterValidCriteria(initState.criteriaOr);

                // Only update if filtering actually removed items
                if (
                    (initState.criteriaAnd &&
                        filteredCriteriaAnd?.length !== initState.criteriaAnd.length) ||
                    (initState.criteriaOr &&
                        filteredCriteriaOr?.length !== initState.criteriaOr.length)
                ) {
                    setFormState({
                        ...formState,
                        criteriaAnd: filteredCriteriaAnd || [],
                        criteriaOr: filteredCriteriaOr || [],
                    });
                }
            }
        }
    }, [initState]);

    // useEffect(() => {
    //     const getData = async () => {
    //         let api_repsonse_message = await fetchMetadataSchema();

    //         if (Array.isArray(api_repsonse_message)) {
    //             let metadataChoiceFields: Record<string, string>[] = [];
    //             metadataChoiceFields = api_repsonse_message.map((metadataChoiceField) => {
    //                 return {
    //                     label: metadataChoiceField.field,
    //                     value: metadataChoiceField.field,
    //                 };
    //             });
    //             setAllMetadataChoiceFields(metadataChoiceFields);
    //         }
    //     };

    //     getData();
    // }, []);

    function addNewConstraintAnd() {
        let criteriaAnd = formState.criteriaAnd;
        if (!criteriaAnd) {
            criteriaAnd = [];
        }
        criteriaAnd.push({
            // create a unique id using a uuid
            id: "c" + Math.random().toString(36).substr(2, 9),
            field: "",
            operator: "",
            value: "",
        });
        console.log("new constraint", criteriaAnd);
        setFormState({
            ...formState,
            criteriaAnd: criteriaAnd,
        });
    }

    function addNewConstraintOr() {
        let criteriaOr = formState.criteriaOr;
        if (!criteriaOr) {
            criteriaOr = [];
        }
        criteriaOr.push({
            // create a unique id using a uuid
            id: "c" + Math.random().toString(36).substr(2, 9),
            field: "",
            operator: "",
            value: "",
        });
        console.log("new constraint", criteriaOr);
        setFormState({
            ...formState,
            criteriaOr: criteriaOr,
        });
    }

    function validateCriteria(criteriaAnd: ConstraintCriteria[], criteriaOr: ConstraintCriteria[]) {
        let response = null;
        let totalLength = 0;
        if (!criteriaAnd && !criteriaOr) response = "Criteria (AND or OR) is required";
        if (criteriaAnd) totalLength += criteriaAnd.length;
        if (criteriaOr) totalLength += criteriaOr.length;
        if (totalLength === 0) response = "At least one criteria is required";
        if (totalLength > 30) response = "Maximum 30 criteria are allowed";
        const critAnd =
            criteriaAnd &&
            criteriaAnd
                .map((c) => {
                    console.log("c", c);
                    if (!c.field) return "Field is required";
                    if (!c.operator) return "Operator is required";
                    if (!c.value) return "Value is required";
                    return null;
                })
                .filter((x) => x !== null);
        if (critAnd && critAnd.length > 0) response = critAnd.join(", ");
        const critOr =
            criteriaOr &&
            criteriaOr
                .map((c) => {
                    console.log("c", c);
                    if (!c.field) return "Field is required";
                    if (!c.operator) return "Operator is required";
                    if (!c.value) return "Value is required";
                    return null;
                })
                .filter((x) => x !== null);
        if (critOr && critOr.length > 0) response = critOr.join(", ");
        console.log("criteria eval", response);
        return response;
    }

    function validateGroupAndUserPermissions(
        groupPermissions: RoleGroupPermission[],
        userPermissions: UserPermission[]
    ): Record<string, string | null> {
        let response: Record<string, string | null> = {
            groupError: null,
            userError: null,
        };
        if (
            (!groupPermissions || groupPermissions.length == 0) &&
            (!userPermissions || userPermissions.length == 0)
        ) {
            response["groupError"] = "At least one role group or user permission is required";
            response["userError"] = "At least one role group or user permission is required";
        }
        const gp =
            groupPermissions &&
            groupPermissions
                .map((gp) => {
                    if (!gp.groupId) return "Group Id is required";
                    if (!gp.permission) return "Permission is required";
                    return null;
                })
                .filter((x) => x !== null);
        if (gp && gp.length > 0) response["groupError"] = gp.join(", ");

        const up =
            userPermissions &&
            userPermissions
                .map((up) => {
                    if (!up.userId) return "User Id is required";
                    if (!up.permission) return "Permission is required";
                    return null;
                })
                .filter((x) => x !== null);
        if (up && up.length > 0) response["userError"] = up.join(", ");

        // count the occurrences of each groupId
        const groupCounts =
            groupPermissions &&
            groupPermissions
                .map((gp) => {
                    return `${gp.groupId} - ${gp.permission} - ${gp.permissionType}`;
                })
                .filter((x) => x !== null)
                .reduce((acc: { [key: string]: number }, curr: string) => {
                    if (acc[curr]) {
                        acc[curr]++;
                    } else {
                        acc[curr] = 1;
                    }
                    return acc;
                }, {});

        // count the occurrences of each userId
        const userCounts =
            userPermissions &&
            userPermissions
                .map((up) => {
                    return `${up.userId} - ${up.permission} - ${up.permissionType}`;
                })
                .filter((x) => x !== null)
                .reduce((acc: { [key: string]: number }, curr: string) => {
                    if (acc[curr]) {
                        acc[curr]++;
                    } else {
                        acc[curr] = 1;
                    }
                    return acc;
                }, {});

        if (groupCounts) {
            const violations = Object.keys(groupCounts).filter((key) => groupCounts[key] > 1);
            if (violations.length > 0) {
                response["groupError"] =
                    "Role Groups cannot have duplicate permissions. The following role groups should consolidate: " +
                    violations.join(", ");
            }
        }

        if (userCounts) {
            const violations = Object.keys(userCounts).filter((key) => userCounts[key] > 1);
            if (violations.length > 0) {
                response["userError"] =
                    "Users cannot have duplicate permissions. The following users should consolidate: " +
                    violations.join(", ");
            }
        }

        return response;
    }

    function validateObjectType(objectType: string) {
        let response = null;
        if (!objectType) response = "Object Type is required";
        return response;
    }

    return (
        <Modal
            visible={open}
            onDismiss={() => {
                setOpen(false);
                setFormError("");
            }}
            size="large"
            header={`${createOrUpdate} Access Control Constraint`}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            variant="link"
                            onClick={() => {
                                setOpen(false);
                                setFormError("");
                            }}
                        >
                            Cancel
                        </Button>

                        <Button
                            variant="primary"
                            onClick={() => {
                                setInProgress(true);
                                console.log("sending state", formState);
                                API.post("api", `auth/constraints/${formState.constraintId}`, {
                                    body: formState,
                                })
                                    .then((res) => {
                                        console.log("create auth criteria", res);
                                        setOpen(false);
                                        setReload(true);
                                        setFormState({
                                            constraintId: generateUUID(),
                                            ...initState,
                                        });
                                        setFormError("");
                                    })
                                    .catch((err) => {
                                        console.log("create auth criteria error", err);
                                        let msg = `Unable to ${createOrUpdate} constraints. Error: Request failed with status code ${err.response.status}`;
                                        setFormError(msg);
                                    })
                                    .finally(() => {
                                        setInProgress(false);
                                    });
                            }}
                            disabled={
                                inProgress ||
                                !(
                                    validateName(formState.constraintId) === null &&
                                    validateDescriptionLength(formState.description) === null &&
                                    validateCriteria(
                                        formState.criteriaAnd,
                                        formState.criteriaOr
                                    ) === null &&
                                    validateGroupAndUserPermissions(
                                        formState.groupPermissions,
                                        formState.userPermissions
                                    )["groupError"] === null &&
                                    validateGroupAndUserPermissions(
                                        formState.groupPermissions,
                                        formState.userPermissions
                                    )["userError"] === null &&
                                    validateObjectType(formState.objectType) === null
                                )
                            }
                            data-testid={`${createOrUpdate}-authcriteria-button`}
                        >
                            {createOrUpdate} Access Control Constraint
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <Form errorText={formError}>
                <SpaceBetween direction="vertical" size="l">
                    <FormField label="Constraint Id">
                        <Input value={formState.constraintId} disabled={true} />
                    </FormField>
                    <FormField
                        label="Constraint Name"
                        errorText={validateName(formState.name)}
                        constraintText="Required. All lower case, no special chars or spaces except - and _ only letters for first character min 4 and max 64"
                    >
                        <Input
                            value={formState.name}
                            disabled={
                                inProgress || (initState && initState.constraintId && true) || false
                            }
                            onChange={({ detail }) =>
                                setFormState({ ...formState, name: detail.value })
                            }
                            placeholder="Constraint Name"
                            data-testid="constraint-name"
                        />
                    </FormField>
                    <FormField
                        label="Constraint Description"
                        constraintText="Required. Max 256 characters"
                        errorText={validateDescriptionLength(formState.description)}
                    >
                        <Textarea
                            value={formState.description}
                            disabled={inProgress}
                            onChange={({ detail }) =>
                                setFormState({ ...formState, description: detail.value })
                            }
                            placeholder="Constraint Description"
                            data-testid="constraint-description"
                        />
                    </FormField>

                    <FormField
                        label="Role Group Permissions (OR)"
                        constraintText="Required."
                        stretch={true}
                        errorText={
                            validateGroupAndUserPermissions(
                                formState.groupPermissions,
                                formState.userPermissions
                            )["groupError"]
                        }
                    >
                        <RoleGroupPermissionsTable
                            permissions={formState.groupPermissions}
                            setPermissions={(groupPermissions) =>
                                setFormState({
                                    ...formState,
                                    groupPermissions,
                                })
                            }
                        />
                    </FormField>

                    <FormField
                        label="User Permissions (OR)"
                        constraintText="Required."
                        stretch={true}
                        errorText={
                            validateGroupAndUserPermissions(
                                formState.groupPermissions,
                                formState.userPermissions
                            )["userError"]
                        }
                    >
                        <UserPermissionsTable
                            permissions={formState.userPermissions}
                            setPermissions={(userPermissions) =>
                                setFormState({
                                    ...formState,
                                    userPermissions,
                                })
                            }
                        />
                    </FormField>

                    <FormField
                        label="Object Type"
                        constraintText="Required. Select one object type"
                        errorText={validateObjectType(formState.objectType)}
                    >
                        <Select
                            selectedOption={
                                objectTypes.find(
                                    (option) => option.value === formState.objectType
                                ) ?? null
                            }
                            placeholder="Object Type"
                            options={objectTypes}
                            onChange={(event) => {
                                setFormState({
                                    ...formState,
                                    objectType:
                                        event.detail.selectedOption.value ?? formState.objectType,
                                });
                            }}
                        />
                    </FormField>

                    <FormField
                        label="Criteria (AND)"
                        constraintText="Required."
                        stretch={true}
                        errorText={validateCriteria(formState.criteriaAnd, formState.criteriaOr)}
                    >
                        <Table
                            onSelectionChange={({ detail }) =>
                                setSelectedCriteriaAnd(detail.selectedItems)
                            }
                            trackBy="id"
                            selectedItems={selectedCriteriaAnd}
                            header={
                                <Header
                                    actions={
                                        <SpaceBetween direction="horizontal" size="xs">
                                            <Button onClick={addNewConstraintAnd}>
                                                Add Criteria
                                            </Button>
                                            <Button
                                                disabled={selectedCriteriaAnd.length === 0}
                                                onClick={() => {
                                                    const criteria = formState.criteriaAnd.filter(
                                                        (x) => {
                                                            return !selectedCriteriaAnd.find(
                                                                (y) => y.id === x.id
                                                            );
                                                        }
                                                    );

                                                    setFormState({
                                                        ...formState,
                                                        criteriaAnd: criteria,
                                                    });
                                                }}
                                            >
                                                Remove Criteria
                                            </Button>
                                        </SpaceBetween>
                                    }
                                >
                                    Criteria
                                </Header>
                            }
                            columnDefinitions={[
                                {
                                    id: "field",
                                    header: "Field",
                                    minWidth: 136,
                                    editConfig: {
                                        ariaLabel: "Field Name",
                                        editIconAriaLabel: "editable",
                                        validation(item, value) {
                                            if (value === undefined || value.length === 0) {
                                                return "Field name is required";
                                            }
                                        },
                                        editingCell: (item, { currentValue, setValue }) => {
                                            const value = currentValue ?? item.field;
                                            return (
                                                <Select
                                                    autoFocus={true}
                                                    expandToViewport={true}
                                                    selectedOption={
                                                        formState.objectType
                                                            ? fieldNamesToObjectTypeMapping[
                                                                  formState.objectType
                                                              ].find(
                                                                  (option) => option.value === value
                                                              ) ?? null
                                                            : null
                                                    }
                                                    onChange={(event) => {
                                                        setValue(
                                                            event.detail.selectedOption.value ??
                                                                item.field
                                                        );
                                                    }}
                                                    options={
                                                        formState.objectType
                                                            ? fieldNamesToObjectTypeMapping[
                                                                  formState.objectType
                                                              ]
                                                            : []
                                                    }
                                                />
                                            );
                                        },
                                    },
                                    cell: (item) => {
                                        return formState.objectType
                                            ? fieldNamesToObjectTypeMapping[
                                                  formState.objectType
                                              ].find((option) => option.value === item.field)?.label
                                            : null;
                                    },
                                },
                                {
                                    id: "operator",
                                    header: "Operator",
                                    minWidth: 136,
                                    editConfig: {
                                        ariaLabel: "Operator",
                                        editIconAriaLabel: "editable",
                                        validation(item, value) {
                                            if (value === undefined || value.length === 0) {
                                                return "Operator is required";
                                            }
                                        },
                                        editingCell: (item, { currentValue, setValue }) => {
                                            const value = currentValue ?? item.operator;
                                            return (
                                                <Select
                                                    autoFocus={true}
                                                    expandToViewport={true}
                                                    selectedOption={
                                                        criteriaOperators.find(
                                                            (option) => option.value === value
                                                        ) ?? null
                                                    }
                                                    onChange={(event) => {
                                                        setValue(
                                                            event.detail.selectedOption.value ??
                                                                item.operator
                                                        );
                                                    }}
                                                    options={criteriaOperators}
                                                />
                                            );
                                        },
                                    },
                                    cell: (item) => {
                                        return criteriaOperators.find(
                                            (option) => option.value === item.operator
                                        )?.label;
                                    },
                                },
                                {
                                    id: "value",
                                    header: "Criteria Values",
                                    minWidth: 136,
                                    cell: (e) => e.value,
                                    editConfig: {
                                        ariaLabel: "Criteria Values",
                                        validation(item, newValue) {
                                            function valid(value: string) {
                                                return value !== undefined && value.length > 0;
                                            }
                                            if (!valid(newValue || item.value)) {
                                                return "Criteria is required";
                                            }
                                        },
                                        editIconAriaLabel: "editable",
                                        errorIconAriaLabel: "Criteria Values Error",
                                        editingCell: (item, { currentValue, setValue }) => {
                                            return (
                                                <Input
                                                    autoFocus={true}
                                                    value={currentValue ?? item.value}
                                                    onChange={(event) =>
                                                        setValue(event.detail.value)
                                                    }
                                                />
                                            );
                                        },
                                    },
                                },
                            ]}
                            items={formState.criteriaAnd}
                            loadingText="Loading resources"
                            submitEdit={(item, column, newValue) => {
                                // do nothing
                                console.log("submitEdit", item, column, newValue);
                                if (column.id) {
                                    item[column.id as keyof ConstraintCriteria] =
                                        newValue as string;

                                    const criteria = [
                                        ...formState.criteriaAnd.filter((x) => x.id !== item.id),
                                        item,
                                    ];

                                    setFormState({ ...formState, criteriaAnd: criteria });
                                }
                            }}
                            selectionType="multi"
                            empty={
                                <Box textAlign="center" color="inherit">
                                    <b>No resources</b>
                                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                                        No resources to display.
                                    </Box>
                                    <Button onClick={addNewConstraintAnd}>Add Criteria</Button>
                                </Box>
                            }
                        />
                    </FormField>

                    <FormField
                        label="Criteria (OR)"
                        constraintText="Required."
                        stretch={true}
                        errorText={validateCriteria(formState.criteriaAnd, formState.criteriaOr)}
                    >
                        <Table
                            onSelectionChange={({ detail }) =>
                                setSelectedCriteriaOr(detail.selectedItems)
                            }
                            trackBy="id"
                            selectedItems={selectedCriteriaOr}
                            header={
                                <Header
                                    actions={
                                        <SpaceBetween direction="horizontal" size="xs">
                                            <Button onClick={addNewConstraintOr}>
                                                Add Criteria
                                            </Button>
                                            <Button
                                                disabled={selectedCriteriaOr.length === 0}
                                                onClick={() => {
                                                    const criteria = formState.criteriaOr.filter(
                                                        (x) => {
                                                            return !selectedCriteriaOr.find(
                                                                (y) => y.id === x.id
                                                            );
                                                        }
                                                    );

                                                    setFormState({
                                                        ...formState,
                                                        criteriaOr: criteria,
                                                    });
                                                }}
                                            >
                                                Remove Criteria
                                            </Button>
                                        </SpaceBetween>
                                    }
                                >
                                    Criteria
                                </Header>
                            }
                            columnDefinitions={[
                                {
                                    id: "field",
                                    header: "Field",
                                    minWidth: 136,
                                    editConfig: {
                                        ariaLabel: "Field Name",
                                        editIconAriaLabel: "editable",
                                        validation(item, value) {
                                            if (value === undefined || value.length === 0) {
                                                return "Field name is required";
                                            }
                                        },
                                        editingCell: (item, { currentValue, setValue }) => {
                                            const value = currentValue ?? item.field;
                                            return (
                                                <Select
                                                    autoFocus={true}
                                                    expandToViewport={true}
                                                    selectedOption={
                                                        formState.objectType
                                                            ? fieldNamesToObjectTypeMapping[
                                                                  formState.objectType
                                                              ].find(
                                                                  (option) => option.value === value
                                                              ) ?? null
                                                            : null
                                                    }
                                                    onChange={(event) => {
                                                        setValue(
                                                            event.detail.selectedOption.value ??
                                                                item.field
                                                        );
                                                    }}
                                                    options={
                                                        formState.objectType
                                                            ? fieldNamesToObjectTypeMapping[
                                                                  formState.objectType
                                                              ]
                                                            : []
                                                    }
                                                />
                                            );
                                        },
                                    },
                                    cell: (item) => {
                                        return formState.objectType
                                            ? fieldNamesToObjectTypeMapping[
                                                  formState.objectType
                                              ].find((option) => option.value === item.field)?.label
                                            : null;
                                    },
                                },
                                {
                                    id: "operator",
                                    header: "Operator",
                                    minWidth: 136,
                                    editConfig: {
                                        ariaLabel: "Operator",
                                        editIconAriaLabel: "editable",
                                        validation(item, value) {
                                            if (value === undefined || value.length === 0) {
                                                return "Operator is required";
                                            }
                                        },
                                        editingCell: (item, { currentValue, setValue }) => {
                                            const value = currentValue ?? item.operator;
                                            return (
                                                <Select
                                                    autoFocus={true}
                                                    expandToViewport={true}
                                                    selectedOption={
                                                        criteriaOperators.find(
                                                            (option) => option.value === value
                                                        ) ?? null
                                                    }
                                                    onChange={(event) => {
                                                        setValue(
                                                            event.detail.selectedOption.value ??
                                                                item.operator
                                                        );
                                                    }}
                                                    options={criteriaOperators}
                                                />
                                            );
                                        },
                                    },
                                    cell: (item) => {
                                        return criteriaOperators.find(
                                            (option) => option.value === item.operator
                                        )?.label;
                                    },
                                },
                                {
                                    id: "value",
                                    header: "Criteria Values",
                                    minWidth: 136,
                                    cell: (e) => e.value,
                                    editConfig: {
                                        ariaLabel: "Criteria Values",
                                        validation(item, newValue) {
                                            function valid(value: string) {
                                                return value !== undefined && value.length > 0;
                                            }
                                            if (!valid(newValue || item.value)) {
                                                return "Criteria is required";
                                            }
                                        },
                                        editIconAriaLabel: "editable",
                                        errorIconAriaLabel: "Criteria Values Error",
                                        editingCell: (item, { currentValue, setValue }) => {
                                            return (
                                                <Input
                                                    autoFocus={true}
                                                    value={currentValue ?? item.value}
                                                    onChange={(event) =>
                                                        setValue(event.detail.value)
                                                    }
                                                />
                                            );
                                        },
                                    },
                                },
                            ]}
                            items={formState.criteriaOr}
                            loadingText="Loading resources"
                            submitEdit={(item, column, newValue) => {
                                // do nothing
                                console.log("submitEdit", item, column, newValue);
                                if (column.id) {
                                    item[column.id as keyof ConstraintCriteria] =
                                        newValue as string;

                                    const criteria = [
                                        ...formState.criteriaOr.filter((x) => x.id !== item.id),
                                        item,
                                    ];

                                    setFormState({ ...formState, criteriaOr: criteria });
                                }
                            }}
                            selectionType="multi"
                            empty={
                                <Box textAlign="center" color="inherit">
                                    <b>No resources</b>
                                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                                        No resources to display.
                                    </Box>
                                    <Button onClick={addNewConstraintOr}>Add Criteria</Button>
                                </Box>
                            }
                        />
                    </FormField>
                </SpaceBetween>
            </Form>
        </Modal>
    );
}
// {
//    "identifier": "constraintId",
//    "name": "user defined name",
//    "description": "description",
//    "appliesTo": [
//        "vams:all_users", # or group identifier
//    ],
//    "created": "utc timestamp",
//    "updated": "utc timestamp",
//    "criteria": [
//      {
//        "field": "fieldname",
//        "operator": "contains", # one of contains, does not contain, is one of, is not one of
//        "value": "value" # or ["value", "value"]
//      }
//    ]
//  }
