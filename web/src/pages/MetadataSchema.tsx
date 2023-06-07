/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    Box,
    Button,
    Checkbox,
    FileUpload,
    Form,
    FormField,
    Grid,
    Input,
    Modal,
    Multiselect,
    MultiselectProps,
    Select,
    SelectProps,
    SpaceBetween,
    TextContent,
} from "@cloudscape-design/components";
import { createContext, useContext, useState } from "react";
import { generateUUID } from "../common/utils/utils";

import { API, Storage } from "aws-amplify";

import ListDefinition from "../components/list/list-definitions/types/ListDefinition";
import ColumnDefinition from "../components/list/list-definitions/types/ColumnDefinition";
import ListPageNoDatabase from "./ListPageNoDatabase";
import { NonCancelableEventHandler } from "@cloudscape-design/components/internal/events";
import { useNavigate, useParams } from "react-router";
import DatabaseSelectorWithModal from "../components/selectors/DatabaseSelectorWithModal";

export interface SchemaContextData {
    schemas: MetadataSchemaFields[];
    databaseId: string | null;
}

const SchemaContext = createContext<SchemaContextData>({ schemas: [], databaseId: null });

interface CreateMetadataFieldProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
    initState: any;
}

type DataTypes =
    | "string"
    | "textarea"
    | "number"
    | "boolean"
    | "date"
    | "location"
    | "controlled-list"
    | "inline-controlled-list";
export interface MetadataSchemaFields {
    id: string;
    field: string;
    dataType: DataTypes;
    required: boolean;
    databaseId: string;
    sequenceNumber: number;
    dependsOn: string[];
    // location fields
    useCenterPosition?: boolean;
    latitudeField?: string;
    longitudeField?: string;
    zoomLevelField?: string;

    // inline controlled list fields
    inlineControlledListValues?: string;
}

interface DatatypeSelectProps {
    value: string;
    onChange: NonCancelableEventHandler<SelectProps.ChangeDetail>;
    disabled: boolean;
}
function DatatypeSelect({ value, onChange, disabled }: DatatypeSelectProps) {
    const options: SelectProps.Option[] = [
        { value: "string", label: "Text" },
        { value: "textarea", label: "Multiline Text Area" },
        { value: "number", label: "Number" },
        { value: "boolean", label: "Boolean" },
        { value: "date", label: "Date" },
        { value: "location", label: "Location" },
        { value: "controlled-list", label: "Controlled List" },
        { value: "inline-controlled-list", label: "Inline Controlled List" },
    ];
    return (
        <Select
            selectedOption={options.find((x) => x.value === value) || null}
            disabled={disabled}
            onChange={onChange}
            options={options}
        />
    );
}

interface DependencyMultiselectProps {
    selectedOptions: string[];
    onChange: NonCancelableEventHandler<MultiselectProps.MultiselectChangeDetail>;
    disabled?: boolean;
    api?: typeof API;
}

function DependencyMultiselect({
    selectedOptions,
    onChange,
    disabled,
    api = API,
}: DependencyMultiselectProps) {
    const { schemas } = useContext(SchemaContext);

    return (
        <Multiselect
            options={schemas.map((x) => ({ value: x.field, label: x.field }))}
            selectedOptions={
                selectedOptions && selectedOptions.map((x) => ({ value: x, label: x }))
            }
            onChange={onChange}
            disabled={!(schemas?.length > 0) || disabled}
        />
    );
}

function validateField(field: string | null | undefined) {
    let response = null;
    if (!field) {
        response = "Field is required";
    }
    if (field && field.length < 4) {
        response = "Field must be at least 4 characters";
    }
    return response;
}

function validateDataType(dataType: string) {
    let response = null;
    if (!dataType) {
        response = "Data Type is required";
    }
    return response;
}

interface LocationSpecificFieldsProps {
    formState: MetadataSchemaFields;
    setFormState: (formState: MetadataSchemaFields) => void;
}

function InlineControlledListSpecificFields({
    formState,
    setFormState,
}: LocationSpecificFieldsProps) {
    if (formState.dataType !== "inline-controlled-list") {
        return null;
    }

    return (
        <>
            <FormField
                label="Inline Controlled List Values"
                constraintText="Define a list of values that can be selected from a dropdown."
                errorText={validateField(formState.inlineControlledListValues)}
            >
                <Input
                    value={formState.inlineControlledListValues!}
                    onChange={({ detail }) => {
                        setFormState({
                            ...formState,
                            inlineControlledListValues: detail.value,
                        });
                    }}
                />
            </FormField>
        </>
    );
}

function LocationSpecificFields({ formState, setFormState }: LocationSpecificFieldsProps) {
    if (formState.dataType !== "location") {
        return null;
    }

    if (!formState.useCenterPosition) {
        return (
            <FormField
                label="Use Center Position"
                constraintText="Use the center point of the map as the initial position."
            >
                <Checkbox
                    checked={formState.useCenterPosition || false}
                    onChange={({ detail }) => {
                        setFormState({
                            ...formState,
                            useCenterPosition: detail.checked,
                        });
                    }}
                />
            </FormField>
        );
    }

    return (
        <>
            <FormField
                label="Use Center Position"
                constraintText="Define a center point from the metadata."
            >
                <Checkbox
                    checked={formState.useCenterPosition}
                    onChange={({ detail }) => {
                        setFormState({
                            ...formState,
                            useCenterPosition: detail.checked,
                        });
                    }}
                />
            </FormField>
            <FormField
                label="Latitude Field"
                constraintText="The field from which to initialize the center point of the map."
                errorText={validateField(formState.latitudeField)}
            >
                <Input
                    value={formState.latitudeField || ""}
                    onChange={({ detail }) => {
                        setFormState({
                            ...formState,
                            latitudeField: detail.value,
                        });
                    }}
                />
            </FormField>
            <FormField
                label="Longitude Field"
                constraintText="The field from which to initialize the center point of the map."
                errorText={validateField(formState.longitudeField)}
            >
                <Input
                    value={formState.longitudeField || ""}
                    onChange={({ detail }) => {
                        setFormState({
                            ...formState,
                            longitudeField: detail.value,
                        });
                    }}
                />
            </FormField>
            <FormField
                label="Zoom Field"
                constraintText="The field from which to initialize the zoom level of the map."
                errorText={validateField(formState.zoomLevelField)}
            >
                <Input
                    value={formState.zoomLevelField || ""}
                    onChange={({ detail }) => {
                        setFormState({
                            ...formState,
                            zoomLevelField: detail.value,
                        });
                    }}
                />
            </FormField>
        </>
    );
}

function CreateMetadataField({ open, setOpen, setReload, initState }: CreateMetadataFieldProps) {
    const createOrUpdate = (initState && "Update") || "Create";

    const [inProgress, setInProgress] = useState(false);
    const { databaseId } = useContext(SchemaContext);
    const [formState, setFormState] = useState<MetadataSchemaFields>({
        id: generateUUID(),
        required: false,
        dataType: "string",
        ...initState,
    });

    return (
        <Modal
            visible={open}
            onDismiss={() => {
                setOpen(false);
            }}
            size="medium"
            header={`${createOrUpdate} Metadata Field`}
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
                                formState.required = formState.required ? true : false;
                                API.post("api", `metadataschema/${databaseId}`, {
                                    // API.post("api", `metadataschema/all`, {
                                    body: formState,
                                })
                                    .then((res) => {
                                        setOpen(false);
                                        setReload(true);
                                        setFormState({
                                            id: generateUUID(),
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
                                    validateField(formState.field) === null &&
                                    validateDataType(formState.dataType) === null
                                )
                            }
                            data-testid={`${createOrUpdate}-metadataschema-button`}
                        >
                            {createOrUpdate} Field
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <Form>
                <FormField
                    label="Field"
                    constraintText="The name of the field. Cannot be changed after the field is created."
                    errorText={validateField(formState.field)}
                >
                    <Input
                        value={formState.field}
                        disabled={createOrUpdate === "Update"}
                        onChange={({ detail }) => {
                            setFormState({
                                ...formState,
                                field: detail.value,
                            });
                        }}
                    />
                </FormField>
                <FormField
                    label="Data Type"
                    constraintText="The data type of the field. Cannot be changed after the field is created."
                    errorText={validateDataType(formState.dataType)}
                >
                    <DatatypeSelect
                        disabled={createOrUpdate === "Update"}
                        value={formState.dataType}
                        onChange={({ detail }) => {
                            if (detail.selectedOption.value) {
                                setFormState({
                                    ...formState,
                                    dataType: detail.selectedOption.value as DataTypes,
                                });
                            }
                        }}
                    />
                </FormField>
                <LocationSpecificFields formState={formState} setFormState={setFormState} />
                <InlineControlledListSpecificFields
                    formState={formState}
                    setFormState={setFormState}
                />
                <FormField label="Required" constraintText="Whether the field is required.">
                    <Select
                        selectedOption={
                            formState.required
                                ? { value: "true", label: "Required" }
                                : { value: "false", label: "Not Required" }
                        }
                        onChange={({ detail }) => {
                            if (detail.selectedOption.value) {
                                setFormState({
                                    ...formState,
                                    required: detail.selectedOption.value === "true",
                                });
                            }
                        }}
                        options={[
                            { value: "true", label: "Required" },
                            { value: "false", label: "Not Required" },
                        ]}
                    />
                </FormField>
                <FormField
                    label="Dependencies"
                    constraintText="Fields that must be selected before this field is selected."
                >
                    <DependencyMultiselect
                        selectedOptions={formState.dependsOn}
                        onChange={({ detail }) => {
                            const selected = detail.selectedOptions.map((x: any) => x.value);
                            setFormState({
                                ...formState,
                                dependsOn: selected,
                            });
                        }}
                    />
                </FormField>
                <FormField
                    label="Sequence Number"
                    constraintText="The order in which fields are displayed. Lower values are displayed first."
                >
                    <Input
                        value={formState?.sequenceNumber?.toString()}
                        type="number"
                        onChange={({ detail }) => {
                            setFormState({
                                ...formState,
                                sequenceNumber: Number.parseInt(detail.value),
                            });
                        }}
                    />
                </FormField>
                {/*
                <FormField
                    label="Database"
                    constraintText="The database that this field belongs to. Cannot be changed after the field is created."
                >
                    <DatabaseSelector
                        selectedOption={{
                            label: formState.databaseId,
                            value: formState.databaseId,
                        }}
                        onChange={({ detail }: any) => {
                            const selectedOption = detail.selectedOption;
                            console.log("selectedOption", selectedOption);
                            if (selectedOption && selectedOption.value) {
                                setFormState({
                                    ...formState,
                                    databaseId: selectedOption.value,
                                });
                            }
                        }}
                    />
                </FormField>
                */}
            </Form>
        </Modal>
    );
}

// In order to validate set of fields, wrap the list page in a
// context provider and set the list from the fetch all each time
// it is called so that it can be validated and leveraged in the
// create field form.

export const MetadataSchemaListDefinition = new ListDefinition({
    pluralName: "fields",
    pluralNameTitleCase: "Fields",
    visibleColumns: ["field", "dataType", "required", "sequenceNumber"],
    filterColumns: [{ name: "field", placeholder: "Field" }],
    elementId: "id",
    deleteFunction: async (item: any): Promise<[boolean, string]> => {
        try {
            console.log("delete item", item);
            const response: any = await API.del(
                "api",
                `metadataschema/${item.databaseId}/${item.field}`,
                {}
            );
            return [true, response.message];
        } catch (error: any) {
            console.log(error);
            return [false, error?.message];
        }
    },
    columnDefinitions: [
        new ColumnDefinition({
            id: "field",
            header: "Field",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "field",
        }),
        // sequenceNumber
        new ColumnDefinition({
            id: "sequenceNumber",
            header: "Sequence Number",
            cellWrapper: (props: any) => <>{props.children ? props.children : ""}</>,
            sortingField: "sequenceNumber",
        }),
        new ColumnDefinition({
            id: "dataType",
            header: "Data Type",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "dataType",
        }),
        new ColumnDefinition({
            id: "required",
            header: "Required",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "required",
        }),
    ],
});

class ProgressCallbackArgs {
    loaded!: number;
    total!: number;
}

async function uploadAssetToS3(
    file: File,
    key: string,
    metadata: { [k: string]: string },
    progressCallback: (progress: ProgressCallbackArgs) => void
) {
    console.log("upload", key, file);
    return Storage.put(key, file, { metadata, progressCallback });
}

function ControlledListFileUpload() {
    const { databaseId } = useParams();
    const [file, setFile] = useState<File[]>([]);

    const schemaKey = `metadataschema/${databaseId}/controlledlist.csv`;

    // form that takes a single file upload and places the file in s3 using Storage.put.
    // the file is then uploaded to the database's s3 bucket.

    return (
        <Form>
            <FormField label="File Upload">
                <FileUpload
                    accept=".csv"
                    multiple={false}
                    value={file}
                    i18nStrings={{
                        uploadButtonText: (e) => (e ? "Choose files" : "Choose file"),
                        dropzoneText: (e) => (e ? "Drop files to upload" : "Drop file to upload"),
                        removeFileAriaLabel: (e) => `Remove file ${e + 1}`,
                        limitShowFewer: "Show fewer files",
                        limitShowMore: "Show more files",
                        errorIconAriaLabel: "Error",
                    }}
                    onChange={({ detail }) => {
                        setFile(detail.value);
                        uploadAssetToS3(
                            detail.value[0],
                            schemaKey,
                            {},
                            (progress: ProgressCallbackArgs) => {
                                console.log("progress", progress);
                            }
                        ).then((result: any) => {
                            console.log("result", result);
                        });
                        console.log("file upload", detail);
                    }}
                />
            </FormField>
        </Form>
    );
}

export default function MetadataSchema() {
    const { databaseId } = useParams();
    const navigate = useNavigate();
    const [databaseSelectModelOpen, setDatabaseSelectModelOpen] = useState(
        databaseId === undefined
    );

    const [schemas, setSchemas] = useState<MetadataSchemaFields[]>([]);
    async function fetchAll(api = API) {
        const resp = await api.get("api", `metadataschema/${databaseId}`, {});
        if (resp.schemas) {
            // sort resp.schemas wrt sequenceNumber
            // undefined values go last
            resp.schemas.sort((a: any, b: any) => {
                if (a.sequenceNumber === undefined) {
                    return 1;
                }
                if (b.sequenceNumber === undefined) {
                    return -1;
                }
                return a.sequenceNumber - b.sequenceNumber;
            });
            setSchemas(resp.schemas);
            return resp.schemas;
        } else {
            return false;
        }
    }

    if (!databaseId) {
        return (
            <DatabaseSelectorWithModal
                open={databaseSelectModelOpen}
                setOpen={setDatabaseSelectModelOpen}
                onSelectorChange={(event: any) => {
                    const id = event?.detail?.selectedOption?.value;
                    navigate(`/metadataschema/${id}/create`);
                }}
            />
        );
    }

    return (
        <SchemaContext.Provider value={{ schemas, databaseId }}>
            <Box padding={{ top: "m", horizontal: "l" }}>
                <Grid gridDefinition={[{ colspan: 6 }]}>
                    <div>
                        <TextContent>
                            <h1>Controlled List Dataset Upload</h1>
                        </TextContent>
                        <ControlledListFileUpload />
                    </div>
                </Grid>
            </Box>
            <ListPageNoDatabase
                singularName={"Metadata Schema Field"}
                singularNameTitleCase={"Metadata Schema Field"}
                pluralName={"Metadata Schema Fields"}
                pluralNameTitleCase={"Metadata Schema Fields"}
                listDefinition={MetadataSchemaListDefinition}
                editEnabled={true}
                CreateNewElement={CreateMetadataField}
                fetchAllElements={fetchAll}
                fetchElements={fetchAll}
            />
        </SchemaContext.Provider>
    );
}
