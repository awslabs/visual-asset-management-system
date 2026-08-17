import {
    Alert,
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
import { useEffect, useState } from "react";
import OptionDefinition from "../../components/createupdate/form-definitions/types/OptionDefinition";
import {
    fetchtagTypes,
    fetchTags,
    createTag,
    updateTag,
    fetchAllDatabases,
} from "../../services/APIService";
import ScopeBadge from "../../components/common/ScopeBadge";

interface TagFields {
    tagName: string;
    description: string;
    tagTypeName: string | undefined;
    databaseId?: string;
}

const GLOBAL_SCOPE_VALUE = "__global__";

/**
 * A tag's type must live in the tag's OWN scope, which is what the backend enforces:
 *   global tag  -> a GLOBAL tag type
 *   scoped tag  -> a tag type in that same database (a GLOBAL type is NOT offered, so a database's
 *                  tags are described only by that database's own categories)
 */
function isTagTypeVisibleForScope(tagType: any, databaseId?: string) {
    const typeDbId = tagType?.databaseId;
    const typeIsGlobal = !typeDbId || typeDbId === "GLOBAL";
    if (!databaseId || databaseId === "GLOBAL") return typeIsGlobal;
    return typeDbId === databaseId;
}

interface CreateTagProps {
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

// when a string is doesn't fit the regex
function validateNameLowercase(name: string) {
    if (name === undefined) return undefined;
    return name.match(/^[-_a-zA-Z0-9]{3,63}$/) !== null
        ? null
        : "No special characters except '-' and '_'";
}

// when a string is between 3 and 64 characters, return null, otherwise return the string "Between 4 and 64 characters"
function validateNameLength(name: string) {
    if (name === undefined) return undefined;
    return name.length >= 3 && name.length <= 64 ? null : "Name to be between 4 and 64 characters";
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
    if (str?.endsWith(strToRemove)) {
        return str.slice(0, -strToRemove.length);
    }
    return str;
}

export default function CreateTag({
    open,
    setOpen,
    setReload,
    initState,
    reloadChild,
    lockedDatabaseId,
}: CreateTagProps) {
    // The page's scope wins over anything in the form: a tag is created in the scope on screen.
    // "GLOBAL" is normalized away here because the request body omits databaseId for a global
    // entry, while ScopeBadge renders the sentinel.
    const lockedScope = lockedDatabaseId;
    const [inProgress, setInProgress] = useState(false);
    const createOrUpdate = (initState && "Update") || "Create";
    const [showModal, setShowModal] = useState(false);
    const [errorMessage, setErrorMessage] = useState("");
    const [formError, setFormError] = useState("");

    const [formState, setFormState] = useState<TagFields>({
        ...initState,
        ...(lockedDatabaseId && lockedDatabaseId !== "GLOBAL"
            ? { databaseId: lockedDatabaseId }
            : lockedDatabaseId === "GLOBAL"
            ? { databaseId: undefined }
            : {}),
    });
    const [selectedOption, setSelectedOption] = useState<any | null>(null);
    const [databases, setDatabases] = useState<any[]>([]);
    const [allTagTypes, setAllTagTypes] = useState<any[]>([]);
    // Names already taken by a database-specific tag. Creating a GLOBAL tag over one of these is
    // allowed, but it leaves two entries with the same name on the asset forms, so it is announced
    // before the user commits rather than only afterwards in the API's response.
    const [databaseScopedNames, setDatabaseScopedNames] = useState<string[]>([]);
    const [notice, setNotice] = useState<{
        header: string;
        body: string;
        closeForm: boolean;
    } | null>(null);

    const isUpdate = createOrUpdate === "Update";

    const tagBody = {
        tagName: formState.tagName,
        description: formState.description,
        tagTypeName: removeStringFromEnd(formState.tagTypeName, " [R]"), // Remove the " [R]" from the end of the tagTypeName we are getting from tagService.py on the backend to pass validation when updating
        // The scope goes on BOTH create and update: it is the storage partition key, so the
        // backend needs it to FIND the row. Omitting it on update looked in the GLOBAL partition
        // and reported the tag as not found. Immutability is enforced server-side by
        // comparing this value with the stored one, which also requires it to be sent.
        ...(formState.databaseId ? { databaseId: formState.databaseId } : {}),
    };

    const scopeOptions = [
        { label: "🌐 Global", value: GLOBAL_SCOPE_VALUE },
        ...databases.map((d: any) => ({ label: d.databaseId, value: d.databaseId })),
    ];
    const selectedScopeOption =
        scopeOptions.find((o) => o.value === (formState.databaseId || GLOBAL_SCOPE_VALUE)) ||
        scopeOptions[0];
    const handleModalClose = () => {
        setShowModal(false);
        setErrorMessage("");
    };

    const handleApiError = (err: any) => {
        if (err?.status === 500) {
            const errorMessage = err?.message || "Duplicate Tag";
            setErrorMessage(errorMessage);
            setShowModal(true);
        }
    };
    const [nameError, setNameError] = useState<string | null>(null);

    useEffect(() => {
        fetchAllDatabases().then((res) => {
            if (Array.isArray(res)) {
                setDatabases(res);
            }
        });
    }, []);

    // A GLOBAL create is allowed over a name a database already uses, so the form has to look
    // across scopes to warn about it. Only fetched for a GLOBAL create: a database-scoped create
    // is rejected outright by the backend when the name is global, and the error covers that.
    const creatingGlobal = !isUpdate && !formState.databaseId;
    useEffect(() => {
        if (!creatingGlobal) {
            setDatabaseScopedNames([]);
            return;
        }
        fetchTags({ scope: "all" }).then((res: any) => {
            const items = Array.isArray(res) ? res : [];
            setDatabaseScopedNames(
                items
                    .filter((t: any) => t?.databaseId && t.databaseId !== GLOBAL_SCOPE_VALUE)
                    .map((t: any) => t.tagName)
            );
        });
    }, [creatingGlobal]);

    const duplicatesDatabaseName =
        creatingGlobal && databaseScopedNames.includes(formState.tagName || "");

    // Only tag types in the tag's own scope are valid, so only that scope is fetched — the form can
    // then never offer an option the backend would reject.
    const tagTypeScope = formState.databaseId || lockedScope;
    useEffect(() => {
        const request =
            !tagTypeScope || tagTypeScope === "GLOBAL"
                ? fetchtagTypes({ scope: "global" })
                : fetchtagTypes({ databaseId: tagTypeScope });
        request.then((res: any) => {
            if (res && Array.isArray(res)) {
                setAllTagTypes(res);
            } else {
                setAllTagTypes([]);
            }
        });
    }, [tagTypeScope]);

    // Only tag types in the tag's own scope are offered (see isTagTypeVisibleForScope).
    const scopedTagTypeOptions = allTagTypes
        .filter((x: any) => isTagTypeVisibleForScope(x, formState.databaseId))
        .map((x: any) => ({ label: x.tagTypeName, value: x.tagTypeName }));

    useEffect(() => {
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
                                    createTag(tagBody)
                                        .then((response: any) => {
                                            console.log("API call successful", response);
                                            // A create can succeed WITH advisories (e.g. this
                                            // name also exists as a database-specific tag).
                                            // Those must be seen, so the form waits for an
                                            // acknowledgement instead of closing.
                                            const warnings =
                                                response?.message?.warnings || response?.warnings;
                                            if (Array.isArray(warnings) && warnings.length) {
                                                setNotice({
                                                    header: "Tag created with warnings",
                                                    body: warnings.join(" "),
                                                    closeForm: true,
                                                });
                                            } else {
                                                setOpen(false);
                                            }
                                            setReload(true);
                                            setFormState({
                                                ...initState,
                                            });
                                            setSelectedOption(null);
                                            setFormError("");
                                        })
                                        .catch((err) => {
                                            console.log("create tag error", err);
                                            if (err?.status === 500) {
                                                const errorMessage =
                                                    "Tag name " +
                                                    tagBody.tagName +
                                                    " already exists or is not valid";
                                                setNameError(errorMessage);
                                            } else {
                                                setFormError(
                                                    `Unable to ${createOrUpdate} tag. ${
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
                                    updateTag(tagBody)
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
                                            console.log("update tag error", err);
                                            setFormError(
                                                `Unable to ${createOrUpdate} tag. ${
                                                    err?.message ||
                                                    (err?.status
                                                        ? `Request failed with status code ${err.status}`
                                                        : "Unknown error")
                                                }`
                                            );
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
                            {`A database-specific tag named "${formState.tagName}" already exists. ` +
                                "Creating a global tag with the same name is allowed, but asset forms " +
                                "will list both entries until the database-specific tag is removed."}
                        </Alert>
                    )}
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
                        label="Scope"
                        constraintText={
                            isUpdate
                                ? "Scope is immutable and cannot be changed after creation."
                                : "Global tags are available to all databases; a database-scoped tag is only available within that database."
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
                                    const databaseId =
                                        value === GLOBAL_SCOPE_VALUE ? undefined : value;
                                    // Clear a selected tag type that is no longer valid for the new scope.
                                    const stillValid = allTagTypes.some(
                                        (x: any) =>
                                            x.tagTypeName === formState.tagTypeName &&
                                            isTagTypeVisibleForScope(x, databaseId)
                                    );
                                    setFormState({
                                        ...formState,
                                        databaseId,
                                        tagTypeName: stillValid ? formState.tagTypeName : undefined,
                                    });
                                    if (!stillValid) setSelectedOption(null);
                                }}
                                data-testid="tag-scope"
                            />
                        )}
                    </FormField>
                    <FormField
                        label="Tag Type"
                        constraintText="Required. Select one tag type"
                        errorText={validateTagType(formState.tagTypeName)}
                    >
                        <Select
                            selectedOption={selectedOption}
                            placeholder="Tag Type"
                            options={scopedTagTypeOptions}
                            onChange={({ detail }) => {
                                setSelectedOption(detail.selectedOption as any);
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
