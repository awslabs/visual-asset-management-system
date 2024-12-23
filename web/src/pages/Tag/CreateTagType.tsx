import {
    Box,
    Button,
    Checkbox,
    Form,
    FormField,
    Input,
    Modal,
    SpaceBetween,
    Textarea,
} from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { useState } from "react";
import OptionDefinition from "../../components/createupdate/form-definitions/types/OptionDefinition";

interface TagTypeFields {
    tagTypeName: string;
    description: string;
    required: string;
    selectedOptions: OptionDefinition[] | null;
}

interface CreateTagTypeProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
    reloadChild: () => void;
    initState: any;
}

// when a string is all lower case, return null, otherwise return the string "All lower case letters only"
function validateNameLowercase(name: string) {
    if (name === undefined) return undefined;
    return name.match(/^[a-z0-9_-]+$/) !== null
        ? null
        : "All lower case letters only. No special characters except '-' and '_'";
}

// when a string is between 4 and 64 characters, return null, otherwise return the string "Between 4 and 64 characters"
function validateNameLength(name: string) {
    if (name === undefined) return undefined;
    return name.length >= 4 && name.length <= 64 ? null : "Name to be between 4 and 64 characters";
}

// chain together the above three functions, when they return null, then return null
function validateName(name: string) {
    if (name === undefined) return undefined;
    return validateNameLowercase(name) || validateNameLength(name);
}

// when a string is between the given min and max characters, return null, otherwise return an error message including the range
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
    reloadChild,
    initState,
}: CreateTagTypeProps) {
    const [inProgress, setInProgress] = useState(false);
    const [showModal, setShowModal] = useState(false);
    const [errorMessage, setErrorMessage] = useState("");
    const [formError, setFormError] = useState("");
    const createOrUpdate = (initState && "Update") || "Create";
    const [formState, setFormState] = useState<TagTypeFields>({
        ...initState,
    });
    const tagtypeBody = {
        tagTypeName: formState.tagTypeName,
        description: formState.description,
        required: formState.required
    };
    const handleModalClose = () => {
        setShowModal(false);
        setErrorMessage("");
    };

    const handleApiError = (err: any) => {
        if (err.response && err.response.status === 500) {
            const errorMessage = err.response.data.message || "Duplicate Tag Type";
            setErrorMessage(errorMessage);
            setShowModal(true);
        }
    };
    const [nameError, setNameError] = useState<string | null>(null);

    const [requiredError, setRequiredError] = useState<string | null>(null);

    // The Tag Type checkbox requires a boolean to hold and display the checked or not checked state. Since interface is a string,
    // created this, which initializes based on the string from formState.required
    const [requiredTagTypeChecked, setRequiredTagTypeChecked] = useState(formState.required === 'True' ? true : false);

    return (
        <Modal
            visible={open}
            onDismiss={() => {
                setOpen(false);
                setOpen(false);
                setFormError("");
                setFormState({
                    ...initState,
                });
            }}
            size="large"
            header={`${createOrUpdate} Tag Type`}
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
                                setFormError("");
                            }}
                        >
                            Cancel
                        </Button>

                        <Button
                            variant="primary"
                            onClick={() => {
                                setInProgress(true);
                                if (createOrUpdate === "Create") {
                                    API.post("api", "tag-types", {
                                        body: tagtypeBody,
                                    })
                                        .then((res) => {
                                            setOpen(false);
                                            setReload(true);
                                            setFormError("");
                                            setFormState({
                                                ...initState,
                                            });
                                        })
                                        .catch((err) => {
                                            console.log("create tag-type ", err);
                                            if (err.response && err.response.status === 500) {
                                                const errorMessage =
                                                    "Tag type name " +
                                                    tagtypeBody.tagTypeName +
                                                    " already exists or is not valid";
                                                setNameError(errorMessage);
                                            }
                                            if (err.response && err.response.status === 403) {
                                                let msg = `Unable to ${createOrUpdate} tag type. Error: Request failed with status code 403`;
                                                setFormError(msg);
                                            }
                                        })
                                        .finally(() => {
                                            setInProgress(false);
                                            reloadChild();
                                        });
                                } else {
                                    API.put("api", "tag-types", {
                                        body: tagtypeBody,
                                    })
                                        .then((res) => {
                                            setOpen(false);
                                            setReload(true);
                                            setFormState({
                                                ...initState,
                                            });
                                            setFormError("");
                                        })
                                        .catch((err) => {
                                            console.log("update tag-type ", err);
                                            handleApiError(err);
                                            if (err.response && err.response.status === 403) {
                                                let msg = `Unable to ${createOrUpdate} tag type. Error: Request failed with status code 403`;
                                                setFormError(msg);
                                            }
                                        })
                                        .finally(() => {
                                            setInProgress(false);
                                            reloadChild();
                                        });
                                }
                            }}
                            disabled={
                                inProgress ||
                                validateName(formState.tagTypeName) !== null ||
                                validateDescriptionLength(formState.description) !== null
                            }
                            data-testid={`${createOrUpdate}-tagtype-button`}
                        >
                            {createOrUpdate} Tag Type
                        </Button>
                        <Modal
                            onDismiss={handleModalClose}
                            visible={showModal}
                            size="small"
                            footer={
                                <Box float="right">
                                    <SpaceBetween direction="horizontal" size="xs">
                                        <Button variant="primary" onClick={handleModalClose}>
                                            Ok
                                        </Button>
                                    </SpaceBetween>
                                </Box>
                            }
                            header="Error"
                        >
                            <div>
                                <p>{errorMessage}</p>
                            </div>
                        </Modal>
                    </SpaceBetween>
                </Box>
            }
        >
            <Form errorText={formError}>
                <SpaceBetween direction="vertical" size="l">
                    <FormField
                        label="Name"
                        errorText={nameError || validateName(formState.tagTypeName)}
                        constraintText="Required. All lower case, no special chars or spaces except '-' and '_' only letters for first character min 4 and max 64"
                    >
                        <Input
                            value={formState.tagTypeName}
                            disabled={
                                inProgress ||
                                (initState && initState.tagId && true) ||
                                false ||
                                createOrUpdate === "Update"
                            }
                            onChange={({ detail }) => {
                                setFormState({ ...formState, tagTypeName: detail.value });
                                setNameError("");
                            }}
                            placeholder="Tag Type Name"
                            data-testid="tag-type-name"
                        />
                    </FormField>
                    <FormField
                        label="Description"
                        constraintText="Required. Max 256 characters"
                        errorText={validateDescriptionLength(formState.description)}
                    >
                        <Textarea
                            value={formState.description}
                            disabled={inProgress}
                            onChange={({ detail }) =>
                                setFormState({ ...formState, description: detail.value })
                            }
                            placeholder="Tag Type Description"
                            data-testid="tag-type-description"
                        />
                    </FormField>

                    <FormField
                        label="Options"
                    >
                        <Checkbox
                            onChange={({ detail }) => {
                                setRequiredTagTypeChecked(detail.checked); // update visual state boolean
                                setFormState({ ...formState, required: detail.checked ? 'True' : 'False' }); // update form state string
                                setRequiredError("");
                            }}
                            checked={requiredTagTypeChecked}
                            data-testid="required"
                            >
                            Require tag of this tag type on asset modification
                        </Checkbox>

                    </FormField>
                </SpaceBetween>
            </Form>
        </Modal>
    );
}
