import {
    Box,
    Button,
    Form,
    FormField,
    Modal,
    Select,
    SpaceBetween,
    Multiselect,
    Input,
} from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { useState, useEffect } from "react";
import OptionDefinition from "../../components/createupdate/form-definitions/types/OptionDefinition";
import { fetchRoles } from "../../services/APIService";
var roles: any[] = [];
interface UserRoleFields {
    userId: string;
    roleName: any[];
}

interface CreateConsraintProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
    initState: any;
}
const userRoleBody = {
    userId: "",
    roleName: [""],
};

function validateEmails(emails: string) {
    if (typeof emails !== "string" || emails.trim().length === 0) {
        return "Required. Please enter at least one User ID (Email).";
    }

    const emailArray = emails.split(",").map((email) => email.trim());

    const isValidEmail = emailArray.every((email) => {
        return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
    });

    return isValidEmail ? null : "Use format user@domain";
}

export default function CreateTagType({
    open,
    setOpen,
    setReload,
    initState,
}: CreateConsraintProps) {
    const [inProgress, setInProgress] = useState(false);
    const [formError, setFormError] = useState("");
    const [nameError, setNameError] = useState<string | null>(null);
    const createOrUpdate = (initState && "Update") || "Create";
    const [formState, setFormState] = useState<UserRoleFields>({
        ...initState,
    });

    const [selectedRoles, setselectedRoles] = useState<OptionDefinition[]>([]);

    useEffect(() => {
        fetchRoles().then((res) => {
            roles = [];
            if (res && Array.isArray(res))
                Object.values(res).map((x: any) => {
                    roles.push({ label: x.roleName, value: x.roleName });
                });
            return roles;
        });
    }, []);

    return (
        <Modal
            visible={open}
            onDismiss={() => {
                setOpen(false);
                setFormState({
                    ...initState,
                });
                setNameError("");
                setFormError("");
                setselectedRoles([]);
            }}
            size="large"
            header={`${createOrUpdate} User in Role`}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button
                            variant="link"
                            onClick={() => {
                                setOpen(false);
                                setFormState({
                                    ...initState,
                                });
                                setInProgress(true);
                                setNameError("");
                                setFormError("");
                                setselectedRoles([]);
                            }}
                        >
                            Cancel
                        </Button>

                        <Button
                            variant="primary"
                            onClick={() => {
                                setInProgress(true);
                                userRoleBody.roleName = formState.roleName;
                                userRoleBody.userId = formState.userId;
                                if (createOrUpdate === "Create") {
                                    API.post("api", "user-roles", {
                                        body: userRoleBody,
                                    })
                                        .then((res) => {
                                            console.log("Create subs", res);
                                            setOpen(false);
                                            setReload(true);
                                            setFormState({
                                                ...initState,
                                            });
                                            setNameError("");
                                            setselectedRoles([]);
                                            setFormError("");
                                        })
                                        .catch((err) => {
                                            console.log("Create subs error", err);
                                            if (err.response && err.response.status === 400) {
                                                const errorMessage =
                                                    "Role for this user " +
                                                    " already exists or is not valid";
                                                setNameError(errorMessage);
                                            }
                                            if (err.response && err.response.status === 403) {
                                                let msg = `Unable to ${createOrUpdate} user role. Error: Request failed with status code 403`;
                                                setFormError(msg);
                                            }
                                        })
                                        .finally(() => {
                                            setInProgress(false);
                                        });
                                } else {
                                    API.put("api", "user-roles", {
                                        body: userRoleBody,
                                    })
                                        .then((res) => {
                                            console.log("Update subs", res);
                                            setOpen(false);
                                            setReload(true);
                                            setFormState({
                                                ...initState,
                                            });
                                            setNameError("");
                                            setFormError("");
                                            setselectedRoles([]);
                                        })
                                        .catch((err) => {
                                            console.log("Update subs error", err);
                                            if (err.response && err.response.status === 403) {
                                                let msg = `Unable to ${createOrUpdate} user role. Error: Request failed with status code 403`;
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
                                validateEmails(formState.userId) !== null ||
                                selectedRoles.length === 0
                            }
                            data-testid={`${createOrUpdate}-authcriteria-button`}
                        >
                            {createOrUpdate} User in Role
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <Form errorText={formError}>
                <SpaceBetween direction="vertical" size="l">
                    <FormField
                        label="Email"
                        constraintText="Required. Enter user ID (email) of user"
                        errorText={nameError}
                    >
                        <Input
                            value={formState.userId}
                            disabled={createOrUpdate === "Update"}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, userId: detail.value });
                                setNameError(validateEmails(detail.value));
                            }}
                            placeholder="Enter User ID (email)"
                            data-testid="email"
                        />
                    </FormField>
                    <FormField
                        label="Role Name"
                        constraintText="Required. Select at least one role"
                    >
                        <Multiselect
                            selectedOptions={
                                createOrUpdate === "Update"
                                    ? formState.roleName
                                        ? formState.roleName.map((role: string) => ({
                                              label: role,
                                              value: role,
                                          }))
                                        : []
                                    : selectedRoles
                            }
                            placeholder="Roles"
                            options={roles}
                            onChange={({ detail }) => {
                                setselectedRoles(detail.selectedOptions as OptionDefinition[]);
                                setFormState({
                                    ...formState,
                                    roleName: (detail.selectedOptions as OptionDefinition[]).map(
                                        (role) => role.value
                                    ),
                                });
                            }}
                        />
                    </FormField>
                </SpaceBetween>
            </Form>
        </Modal>
    );
}
