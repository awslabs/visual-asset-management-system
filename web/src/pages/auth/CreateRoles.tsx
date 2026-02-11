import {
    Box,
    Button,
    Checkbox,
    Form,
    FormField,
    Modal,
    SpaceBetween,
    Input,
} from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { useState } from "react";

interface RoleFields {
    source: string;
    description: string;
    id: string;
    sourceIdentifier: string;
    createdOn: string;
    roleName: string;
    mfaRequired: boolean;
}

interface CreateConsraintProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
    initState: any;
}
const roleBody = {
    source: "INTERNAL_SYSTEM",
    description: "",
    id: "",
    roleName: "",
    sourceIdentifier: "",
    mfaRequired: false,
};

function validateNameLength(name: string) {
    if (name === undefined) return undefined;
    return name.length >= 3 && name.length <= 64 ? null : "Name to be between 3 and 64 characters";
}

function validateName(name: string) {
    if (name === undefined) return undefined;
    return validateNameLength(name);
}

function validateDescriptionLength(description: string) {
    if (description === undefined) return undefined;
    const min = 4,
        max = 256;
    return description.length >= min && description.length <= max
        ? null
        : `Description to be between ${min} and ${max} characters`;
}

export default function CreateTagType({
    open,
    setOpen,
    setReload,
    initState,
}: CreateConsraintProps) {
    const [inProgress, setInProgress] = useState(false);
    const createOrUpdate = (initState && "Update") || "Create";
    const [nameError, setNameError] = useState<string | null>(null);
    const [formError, setFormError] = useState("");
    const [formState, setFormState] = useState<RoleFields>({
        ...initState,
        source: initState?.source || "INTERNAL_SYSTEM",
    });

    return (
        <Modal
            visible={open}
            onDismiss={() => {
                setOpen(false);
                setFormState({
                    ...initState,
                    source: initState?.source || "INTERNAL_SYSTEM",
                });
                setFormError("");
            }}
            size="large"
            header={`${createOrUpdate} Role`}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            variant="link"
                            onClick={() => {
                                setOpen(false);
                                setFormState({
                                    ...initState,
                                    source: initState?.source || "INTERNAL_SYSTEM",
                                });
                                setInProgress(true);
                                setFormError("");
                            }}
                        >
                            Cancel
                        </Button>

                        <Button
                            variant="primary"
                            onClick={() => {
                                roleBody.description = formState.description;
                                roleBody.sourceIdentifier = formState.sourceIdentifier;
                                roleBody.roleName = formState.roleName;
                                roleBody.source = formState.source;
                                roleBody.mfaRequired = formState.mfaRequired || false;
                                setInProgress(true);
                                console.log("sending", roleBody);
                                if (createOrUpdate === "Create") {
                                    API.post("api", "roles", {
                                        body: roleBody,
                                    })
                                        .then((res) => {
                                            console.log("Create subs", res);
                                            setOpen(false);
                                            setReload(true);
                                            setFormState({
                                                ...initState,
                                                source: initState?.source || "INTERNAL_SYSTEM",
                                            });
                                            setFormError("");
                                        })
                                        .catch((err) => {
                                            console.log("Create subs error", err);
                                            if (err.response && err.response.status === 400) {
                                                const errorMessage =
                                                    "Role" + " already exists or is not valid";
                                                setNameError(errorMessage);
                                            }
                                            if (err.response && err.response.status === 403) {
                                                let msg = `Unable to ${createOrUpdate} role. Error: Request failed with status code 403`;
                                                setFormError(msg);
                                            }
                                        })
                                        .finally(() => {
                                            setInProgress(false);
                                        });
                                } else {
                                    API.put("api", "roles", {
                                        body: roleBody,
                                    })
                                        .then((res) => {
                                            console.log("Update subs", res);
                                            setOpen(false);
                                            setReload(true);
                                            setFormState({
                                                ...initState,
                                                source: initState?.source || "INTERNAL_SYSTEM",
                                            });
                                            setFormError("");
                                        })
                                        .catch((err) => {
                                            console.log("Update subs error", err);
                                            if (err.response && err.response.status === 403) {
                                                let msg = `Unable to ${createOrUpdate} role. Error: Request failed with status code 403`;
                                                setFormError(msg);
                                            }
                                        })
                                        .finally(() => {
                                            setInProgress(false);
                                        });
                                }
                            }}
                            disabled={
                                inProgress ||
                                validateName(formState.roleName) !== null ||
                                validateDescriptionLength(formState.description) !== null
                            }
                            data-testid={`${createOrUpdate}-authcriteria-button`}
                        >
                            {createOrUpdate} Role
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <Form errorText={formError}>
                <SpaceBetween direction="vertical" size="l">
                    <FormField
                        label="Name"
                        constraintText="Required. Enter Role Name"
                        errorText={nameError || validateName(formState.roleName)}
                    >
                        <Input
                            value={formState.roleName}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, roleName: detail.value });
                                setNameError("");
                            }}
                            placeholder="Enter Name"
                            data-testid="role"
                            disabled={createOrUpdate === "Update"}
                        />
                    </FormField>
                    <FormField
                        label="Source"
                        constraintText="Optional. Defaults to INTERNAL_SYSTEM"
                    >
                        <Input
                            value={formState.source}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, source: detail.value });
                            }}
                            placeholder="INTERNAL_SYSTEM"
                            data-testid="source-input"
                        />
                    </FormField>
                    <FormField
                        label="Source Identifier"
                        constraintText="Optional. Enter Source Identifier"
                    >
                        <Input
                            value={formState.sourceIdentifier}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, sourceIdentifier: detail.value });
                            }}
                            placeholder="Enter Source Identifier"
                            data-testid="source"
                        />
                    </FormField>
                    <FormField
                        label="Description"
                        constraintText="Required. Please enter description. Max 256 characters"
                        errorText={validateDescriptionLength(formState.description)}
                    >
                        <Input
                            value={formState.description}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, description: detail.value });
                            }}
                            placeholder="Enter description"
                            data-testid="description"
                        />
                    </FormField>
                    <FormField label="Options">
                        <Checkbox
                            onChange={({ detail }) => {
                                setFormState({ ...formState, mfaRequired: detail.checked });
                            }}
                            checked={formState.mfaRequired}
                            data-testid="mfaRequired"
                            description="To enable this role's access, users must log in using multi-factor authentication"
                        >
                            Require Multi-Factor Authentication
                        </Checkbox>
                    </FormField>
                </SpaceBetween>
            </Form>
        </Modal>
    );
}
