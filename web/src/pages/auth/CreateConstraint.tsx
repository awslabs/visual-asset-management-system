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
import { useState } from "react";
import { generateUUID } from "../../common/utils/utils";
import GroupPermissionsTable, { GroupPermission } from "./GroupPermissionsTable";

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
    groupPermissions: GroupPermission[];
    criteria: ConstraintCriteria[];
}

interface CreateConsraintProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
    initState: any;
}

// when a string is all lower case, return null, otherwise return the string "All lower case letters only"
function validateNameLowercase(name: string) {
    if (name === undefined) return null;
    return name.match(/^[a-z0-9_-]+$/) !== null
        ? null
        : "All lower case letters only. No special characters except - and _";
}

// when a string is between 4 and 64 characters, return null, otherwise return the string "Between 4 and 64 characters"
function validateNameLength(name: string) {
    if (name === undefined) return null;
    return name.length >= 4 && name.length <= 64 ? null : "Between 4 and 64 characters";
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
}: CreateConsraintProps) {
    const [inProgress, setInProgress] = useState(false);

    const createOrUpdate = (initState && "Update") || "Create";
    const [formState, setFormState] = useState<ConstraintFields>({
        constraintId: generateUUID(),
        ...initState,
    });

    const [selectedCriteria, setSelectedCriteria] = useState<ConstraintCriteria[]>([]);

    console.log("formState", formState, initState);

    const criteriaOperators = [
        { label: "Contains", value: "contains" },
        { label: "Does Not Contain", value: "does_not_contain" },
        { label: "Is One Of", value: "is_one_of" },
        { label: "Is Not One Of", value: "is_not_one_of" },
    ];

    function addNewConstraint() {
        let criteria = formState.criteria;
        if (!criteria) {
            criteria = [];
        }
        criteria.push({
            // create a unique id using a uuid
            id: "c" + Math.random().toString(36).substr(2, 9),
            field: "",
            operator: "",
            value: "",
        });
        console.log("new constraint", criteria);
        setFormState({
            ...formState,
            criteria,
        });
    }

    function validateCriteria(criteria: ConstraintCriteria[]) {
        let response = null;
        if (!criteria) response = "Criteria is required";
        if (criteria && criteria.length === 0) response = "At least one criteria is required";
        if (criteria && criteria.length > 10) response = "Maximum 10 criteria are allowed";
        const crit =
            criteria &&
            criteria
                .map((c) => {
                    console.log("c", c);
                    if (!c.field) return "Field is required";
                    if (!c.operator) return "Operator is required";
                    if (!c.value) return "Value is required";
                    return null;
                })
                .filter((x) => x !== null);
        if (crit && crit.length > 0) response = crit.join(", ");
        console.log("criteria eval", response);
        return response;
    }

    function validateGroupPermissions(groupPermissions: GroupPermission[]): React.ReactNode {
        let response = null;
        if (!groupPermissions) response = "At least one group permission is required";
        const gp =
            groupPermissions &&
            groupPermissions
                .map((gp) => {
                    if (!gp.groupId) return "Group Id is required";
                    if (!gp.permission) return "Permission is required";
                    return null;
                })
                .filter((x) => x !== null);
        if (gp && gp.length > 0) response = gp.join(", ");

        // count the occurrences of each groupId
        const groupCounts =
            groupPermissions &&
            groupPermissions
                .map((gp) => {
                    return gp.groupId;
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
                response =
                    "Groups cannot have duplicate permissions. The following groups should consolidate: " +
                    violations.join(", ");
            }
        }

        return response;
    }

    return (
        <Modal
            visible={open}
            onDismiss={() => setOpen(false)}
            size="large"
            header={`${createOrUpdate} Constraint`}
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
                                    })
                                    .catch((err) => {
                                        console.log("create auth criteria error", err);
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
                                    validateCriteria(formState.criteria) === null &&
                                    validateGroupPermissions(formState.groupPermissions) === null
                                )
                            }
                            data-testid={`${createOrUpdate}-authcriteria-button`}
                        >
                            {createOrUpdate} Constraint
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <Form>
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
                        label="Group Permissions"
                        constraintText="Required."
                        stretch={true}
                        errorText={validateGroupPermissions(formState.groupPermissions)}
                    >
                        <GroupPermissionsTable
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
                        label="Criteria"
                        constraintText="Required."
                        stretch={true}
                        errorText={validateCriteria(formState.criteria)}
                    >
                        <Table
                            onSelectionChange={({ detail }) =>
                                setSelectedCriteria(detail.selectedItems)
                            }
                            trackBy="id"
                            selectedItems={selectedCriteria}
                            header={
                                <Header
                                    actions={
                                        <SpaceBetween direction="horizontal" size="xs">
                                            <Button onClick={addNewConstraint}>Add Criteria</Button>
                                            <Button
                                                disabled={selectedCriteria.length === 0}
                                                onClick={() => {
                                                    const criteria = formState.criteria.filter(
                                                        (x) => {
                                                            return !selectedCriteria.find(
                                                                (y) => y.id === x.id
                                                            );
                                                        }
                                                    );

                                                    setFormState({
                                                        ...formState,
                                                        criteria,
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
                                    cell: (item) => {
                                        return item.field;
                                    },
                                    editConfig: {
                                        ariaLabel: "Field Name",
                                        editIconAriaLabel: "editable",
                                        errorIconAriaLabel: "Field Name Error",
                                        validation(item, value): Optional<string> {
                                            if (value === undefined || value.length === 0) {
                                                return "Field name is required";
                                            }
                                        },
                                        editingCell: (item, { currentValue, setValue }) => {
                                            return (
                                                <Input
                                                    autoFocus={true}
                                                    value={currentValue ?? item.field}
                                                    onChange={(event) =>
                                                        setValue(event.detail.value)
                                                    }
                                                />
                                            );
                                        },
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
                            items={formState.criteria}
                            loadingText="Loading resources"
                            submitEdit={(item, column, newValue) => {
                                // do nothing
                                console.log("submitEdit", item, column, newValue);
                                if (column.id) {
                                    item[column.id as keyof ConstraintCriteria] =
                                        newValue as string;

                                    const criteria = [
                                        ...formState.criteria.filter((x) => x.id !== item.id),
                                        item,
                                    ];

                                    setFormState({ ...formState, criteria });
                                }
                            }}
                            selectionType="multi"
                            empty={
                                <Box textAlign="center" color="inherit">
                                    <b>No resources</b>
                                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                                        No resources to display.
                                    </Box>
                                    <Button onClick={addNewConstraint}>Add Criteria</Button>
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
