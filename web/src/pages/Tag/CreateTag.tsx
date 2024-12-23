import {
    Box,
    Button,
    Form,
    FormField,
    Input,
    Modal,
    Select,
    SpaceBetween,
    Textarea,
} from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { useEffect, useState } from "react";
import OptionDefinition from "../../components/createupdate/form-definitions/types/OptionDefinition";
import { fetchtagTypes } from "../../services/APIService";
var tagTypes: any[] = [];

interface TagFields {
    tagName: string;
    description: string;
    tagTypeName: string | undefined;
}

interface CreateTagProps {
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
function validateTagType(selectedOption: string | undefined): string | null {
    return selectedOption === undefined ? "Please select a Tag Type" : null;
}

function removeStringFromEnd(str: string | undefined, strToRemove: string) {
    if(str?.endsWith(strToRemove)) {
        return str.slice(0, -strToRemove.length)
    }
    return str
}

export default function CreateTag({

    open,
    setOpen,
    setReload,
    initState,
    reloadChild,
}: CreateTagProps) {
    const [inProgress, setInProgress] = useState(false);
    const createOrUpdate = (initState && "Update") || "Create";
    const [showModal, setShowModal] = useState(false);
    const [errorMessage, setErrorMessage] = useState("");
    const [formError, setFormError] = useState("");

    const [formState, setFormState] = useState<TagFields>({
        ...initState,
    });
    const [selectedOption, setSelectedOption] = useState<OptionDefinition | null>(null);

    const tagBody = {
        tagName: formState.tagName,
        description: formState.description,
        tagTypeName: removeStringFromEnd(formState.tagTypeName, " [R]"), // Remove the " [R]" from the end of the tagTypeName we are getting from tagService.py on the backend to pass validation when updating
    };
    const handleModalClose = () => {
        setShowModal(false);
        setErrorMessage("");
    };

    const handleApiError = (err: any) => {
        if (err.response && err.response.status === 500) {
            const errorMessage = err.response.data.message || "Duplicate Tag";
            setErrorMessage(errorMessage);
            setShowModal(true);
        }
    };
    const [nameError, setNameError] = useState<string | null>(null);
    useEffect(() => {
        fetchtagTypes().then((res) => {
            tagTypes = [];
            if (res && Array.isArray(res)) {
                Object.values(res).map((x: any) => {
                    tagTypes.push({ label: x.tagTypeName, value: x.tagTypeName });
                });
            }
            return tagTypes;
        });
        const opts = {
            label: formState.tagTypeName,
            value: formState.tagTypeName,
        };
        setSelectedOption(opts);
    }, [formState.tagTypeName]);

    return (
        <Modal
            visible={open}
            onDismiss={() => {
                setOpen(false);
                setFormState({
                    ...initState,
                });
                setFormError("");
                if (createOrUpdate === "Create") setSelectedOption(null);
            }}
            size="large"
            header={`${createOrUpdate} Tag`}
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
                                if (createOrUpdate === "Create") setSelectedOption(null);
                            }}
                        >
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={() => {
                                setInProgress(true);
                                if (createOrUpdate === "Create") {
                                    API.post("api", "tags", {
                                        body: tagBody,
                                    })
                                        .then((response) => {
                                            console.log("API call successful", response);
                                            setOpen(false);
                                            setReload(true);
                                            setFormState({
                                                ...initState,
                                            });
                                            setSelectedOption(null);
                                            setFormError("");
                                        })
                                        .catch((err) => {
                                            console.log("create tag error", err);
                                            if (err.response && err.response.status === 500) {
                                                const errorMessage =
                                                    "Tag name " +
                                                    tagBody.tagName +
                                                    " already exists or is not valid";
                                                setNameError(errorMessage);
                                            }
                                            if (err.response && err.response.status === 403) {
                                                let msg = `Unable to ${createOrUpdate} tag. Error: Request failed with status code 403`;
                                                setFormError(msg);
                                            }
                                        })
                                        .finally(() => {
                                            setInProgress(false);
                                            reloadChild();
                                        });
                                } else {
                                    API.put("api", "tags", {
                                        body: tagBody,
                                    })
                                        .then((response) => {
                                            console.log("API call successful", response);
                                            setOpen(false);
                                            setReload(true);
                                            setFormState({
                                                ...initState,
                                            });
                                            setSelectedOption(null);
                                            setFormError("");
                                        })
                                        .catch((err) => {
                                            console.log("create tag error", err);
                                            if (err.response && err.response.status === 403) {
                                                let msg = `Unable to ${createOrUpdate} tag. Error: Request failed with status code 403`;
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
                                validateName(formState.tagName) !== null ||
                                validateDescriptionLength(formState.description) !== null ||
                                validateTagType(formState.tagTypeName) !== null
                            }
                            data-testid={`${createOrUpdate}-tag-button`}
                        >
                            {createOrUpdate} Tag
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
                        errorText={nameError || validateName(formState.tagName)}
                        constraintText="Required. All lower case, no special chars or spaces except '-' and '_' only letters for first character min 4 and max 64"
                    >
                        <Input
                            value={formState.tagName}
                            disabled={
                                inProgress ||
                                (initState && initState.tagId && true) ||
                                false ||
                                createOrUpdate === "Update"
                            }
                            onChange={({ detail }) => {
                                setFormState({ ...formState, tagName: detail.value });
                                setNameError("");
                            }}
                            placeholder="Tag Name"
                            data-testid="tag-name"
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
                            onChange={({ detail }) => {
                                setFormState({ ...formState, description: detail.value });
                            }}
                            placeholder="Tag Description"
                            data-testid="tag-description"
                        />
                    </FormField>
                    <FormField
                        label="Tag Type"
                        constraintText="Required. Select one tag type"
                        errorText={validateTagType(formState.tagTypeName)}
                    >
                        <Select
                            selectedOption={selectedOption}
                            placeholder="Tag Type"
                            options={tagTypes}
                            onChange={({ detail }) => {
                                setSelectedOption(detail.selectedOption as OptionDefinition);
                                setFormState({
                                    ...formState,
                                    tagTypeName: detail.selectedOption.value,
                                });
                            }}
                        />
                    </FormField>
                </SpaceBetween>
            </Form>
        </Modal>
    );
}
