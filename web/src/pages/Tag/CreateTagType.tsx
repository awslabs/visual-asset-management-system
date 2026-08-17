import {
    Alert,
    Box,
    Button,
    Checkbox,
    Form,
    FormField,
    Input,
    Modal,
    Select,
    SpaceBetween,
    Textarea,
} from "@cloudscape-design/components";
import { useEffect, useState } from "react";
import {
    createTagType,
    updateTagType,
    fetchAllDatabases,
    fetchtagTypes,
} from "../../services/APIService";
import OptionDefinition from "../../components/createupdate/form-definitions/types/OptionDefinition";
import ScopeBadge from "../../components/common/ScopeBadge";

interface TagTypeFields {
    tagTypeName: string;
    description: string;
    required: string;
    selectedOptions: any[] | null;
    databaseId?: string;
}

const GLOBAL_SCOPE_VALUE = "__global__";

interface CreateTagTypeProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
    reloadChild: () => void;
    initState: any;
    /**
     * The scope the page is administering (a databaseId, or "GLOBAL"). When supplied, Scope is
     * shown read-only and taken from here, so an entry can only be created in the scope on
     * screen — database selection stays a page-level choice rather than a second control here.
     */
    lockedDatabaseId?: string;
}

// when a string matches regex
function validateNameLowercase(name: string) {
    if (name === undefined) return undefined;
    return name.match(/^[-_a-zA-Z0-9]{3,63}$/) !== null
        ? null
        : "No special characters except '-' and '_'";
}

// when a string is between 3 and 64 characters, return null, otherwise return the string "Between 4 and 64 characters"
function validateNameLength(name: string) {
    if (name === undefined) return undefined;
    return name.length >= 3 && name.length <= 64 ? null : "Name to be between 3 and 64 characters";
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
    lockedDatabaseId,
}: CreateTagTypeProps) {
    // The page's scope wins: a tag type is created in the scope on screen. "GLOBAL" is normalized
    // away for the request body (which omits databaseId for a global entry) while ScopeBadge still
    // renders the sentinel.
    const lockedScope = lockedDatabaseId;
    const [inProgress, setInProgress] = useState(false);
    const [showModal, setShowModal] = useState(false);
    const [errorMessage, setErrorMessage] = useState("");
    const [formError, setFormError] = useState("");
    const createOrUpdate = (initState && "Update") || "Create";
    const isUpdate = createOrUpdate === "Update";
    const [formState, setFormState] = useState<TagTypeFields>({
        ...initState,
        ...(lockedDatabaseId && lockedDatabaseId !== "GLOBAL"
            ? { databaseId: lockedDatabaseId }
            : lockedDatabaseId === "GLOBAL"
            ? { databaseId: undefined }
            : {}),
    });
    const [databases, setDatabases] = useState<any[]>([]);
    // Names already taken by a database-specific tag type. A GLOBAL create over one of these is
    // allowed, but both entries then appear on the asset forms, so it is announced up front.
    const [databaseScopedNames, setDatabaseScopedNames] = useState<string[]>([]);
    const [notice, setNotice] = useState<{
        header: string;
        body: string;
        closeForm: boolean;
    } | null>(null);

    const creatingGlobal = !isUpdate && !formState.databaseId;
    useEffect(() => {
        if (!creatingGlobal) {
            setDatabaseScopedNames([]);
            return;
        }
        fetchtagTypes({ scope: "all" }).then((res: any) => {
            const items = Array.isArray(res) ? res : [];
            setDatabaseScopedNames(
                items
                    .filter((t: any) => t?.databaseId && t.databaseId !== "GLOBAL")
                    .map((t: any) => t.tagTypeName)
            );
        });
    }, [creatingGlobal]);

    const duplicatesDatabaseName =
        creatingGlobal && databaseScopedNames.includes(formState.tagTypeName || "");

    useEffect(() => {
        fetchAllDatabases().then((res) => {
            if (Array.isArray(res)) {
                setDatabases(res);
            }
        });
    }, []);

    const scopeOptions = [
        { label: "🌐 Global", value: GLOBAL_SCOPE_VALUE },
        ...databases.map((d: any) => ({ label: d.databaseId, value: d.databaseId })),
    ];
    const selectedScopeOption =
        scopeOptions.find((o) => o.value === (formState.databaseId || GLOBAL_SCOPE_VALUE)) ||
        scopeOptions[0];

    const tagtypeBody = {
        tagTypeName: formState.tagTypeName,
        description: formState.description,
        required: formState.required,
        // The scope goes on BOTH create and update: it is the storage partition key, so the
        // backend needs it to FIND the row. Omitting it on update looked in the GLOBAL partition
        // and reported the tag type as not found. Immutability is enforced server-side by
        // comparing this value with the stored one, which also requires it to be sent.
        ...(formState.databaseId ? { databaseId: formState.databaseId } : {}),
    };
    const handleModalClose = () => {
        setShowModal(false);
        setErrorMessage("");
    };

    const handleApiError = (err: any) => {
        if (err?.status === 500) {
            const errorMessage = err?.message || "Duplicate Tag Type";
            setErrorMessage(errorMessage);
            setShowModal(true);
        }
    };
    const [nameError, setNameError] = useState<string | null>(null);

    const [requiredError, setRequiredError] = useState<string | null>(null);

    // The Tag Type checkbox requires a boolean to hold and display the checked or not checked state. Since interface is a string,
    // created this, which initializes based on the string from formState.required
    const [requiredTagTypeChecked, setRequiredTagTypeChecked] = useState(
        formState.required === "True" ? true : false
    );

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
                                    createTagType(tagtypeBody)
                                        .then((res: any) => {
                                            // A create can succeed WITH advisories; the form
                                            // waits for an acknowledgement so they are seen.
                                            const warnings =
                                                res?.message?.warnings || res?.warnings;
                                            if (Array.isArray(warnings) && warnings.length) {
                                                setNotice({
                                                    header: "Tag type created with warnings",
                                                    body: warnings.join(" "),
                                                    closeForm: true,
                                                });
                                            } else {
                                                setOpen(false);
                                            }
                                            setReload(true);
                                            setFormError("");
                                            setFormState({
                                                ...initState,
                                            });
                                        })
                                        .catch((err) => {
                                            console.log("create tag-type ", err);
                                            if (err?.status === 500) {
                                                const errorMessage =
                                                    "Tag type name " +
                                                    tagtypeBody.tagTypeName +
                                                    " already exists or is not valid";
                                                setNameError(errorMessage);
                                            } else {
                                                setFormError(
                                                    `Unable to ${createOrUpdate} tag type. ${
                                                        err?.message ||
                                                        (err?.status
                                                            ? `Request failed with status code ${err.status}`
                                                            : "Unknown error")
                                                    }`
                                                );
                                            }
                                        })
                                        .finally(() => {
                                            setInProgress(false);
                                            reloadChild();
                                        });
                                } else {
                                    updateTagType(tagtypeBody)
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
                                            if (err?.status !== 500) {
                                                setFormError(
                                                    `Unable to ${createOrUpdate} tag type. ${
                                                        err?.message ||
                                                        (err?.status
                                                            ? `Request failed with status code ${err.status}`
                                                            : "Unknown error")
                                                    }`
                                                );
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
                        <Modal
                            onDismiss={() => {
                                const shouldClose = notice?.closeForm;
                                setNotice(null);
                                if (shouldClose) setOpen(false);
                            }}
                            visible={!!notice}
                            size="medium"
                            footer={
                                <Box float="right">
                                    <Button
                                        variant="primary"
                                        onClick={() => {
                                            const shouldClose = notice?.closeForm;
                                            setNotice(null);
                                            if (shouldClose) setOpen(false);
                                        }}
                                    >
                                        Ok
                                    </Button>
                                </Box>
                            }
                            header={notice?.header || ""}
                        >
                            <Alert type="warning">{notice?.body}</Alert>
                        </Modal>
                    </SpaceBetween>
                </Box>
            }
        >
            <Form errorText={formError}>
                <SpaceBetween direction="vertical" size="l">
                    {duplicatesDatabaseName && (
                        <Alert type="warning" header="This name is used by a database">
                            {`A database-specific tag type named "${formState.tagTypeName}" already ` +
                                "exists. Creating a global tag type with the same name is allowed, but " +
                                "asset forms will list both entries until the database-specific tag " +
                                "type is removed."}
                        </Alert>
                    )}
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
                        label="Scope"
                        constraintText={
                            isUpdate
                                ? "Scope is immutable and cannot be changed after creation."
                                : "Global tag types are available to all databases; a database-scoped tag type is only available within that database."
                        }
                    >
                        {isUpdate || lockedScope ? (
                            <ScopeBadge databaseId={formState.databaseId || lockedScope} />
                        ) : (
                            <Select
                                selectedOption={selectedScopeOption}
                                placeholder="Scope"
                                options={scopeOptions}
                                disabled={inProgress}
                                onChange={({ detail }) => {
                                    const value = detail.selectedOption.value as string;
                                    setFormState({
                                        ...formState,
                                        databaseId:
                                            value === GLOBAL_SCOPE_VALUE ? undefined : value,
                                    });
                                }}
                                data-testid="tagtype-scope"
                            />
                        )}
                    </FormField>
                    <FormField label="Options">
                        <Checkbox
                            onChange={({ detail }) => {
                                setRequiredTagTypeChecked(detail.checked); // update visual state boolean
                                setFormState({
                                    ...formState,
                                    required: detail.checked ? "True" : "False",
                                }); // update form state string
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
