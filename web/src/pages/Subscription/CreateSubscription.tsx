import {
    Box,
    Button,
    Form,
    FormField,
    Modal,
    Select,
    SpaceBetween,
    Input,
    Grid,
    Link,
} from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { Cache } from "aws-amplify";
import { useState, useEffect } from "react";
import OptionDefinition from "../../components/createupdate/form-definitions/types/OptionDefinition";
import CustomTable from "../../components/table/CustomTable";
import { fetchDatabase, fetchAllAssets } from "../../services/APIService";
import { featuresEnabled } from "../../common/constants/featuresEnabled";

interface SubscriptionFields {
    eventName: string;
    entityName: string;
    entityId: string;
    subscribers: string;
    entityValue: string;
    databaseId: string;
}

interface CreateSubscriptionProps {
    open: boolean;
    setOpen: (open: boolean) => void;
    setReload: (reload: boolean) => void;
    initState: any;
}

const ruleBody = {
    eventName: "Asset Version Change",
    entityName: "",
    entityId: "",
    subscribers: [""],
};

function validateUsers(users: string) {
    if (typeof users !== "string" || users.trim().length === 0) {
        return "Required. Please enter at least one User ID or resource account email.";
    }

    const userArray = users.split(",").map((user) => user.trim());

    //Valid user regex to see if at least 3 characters alphanumeric
    const isValidUser = userArray.every((user) => {
        return /^[\w\-\.\+\@]{3,256}$/.test(user);
    });

    return isValidUser
        ? null
        : "User IDs (comma seperated) should be at least 3 characters alphanumeric with support for special characters (. + - @). Direct email addresses are allowed too if it's non-user email like a resource account.";
}

export default function CreateSubscription({
    open,
    setOpen,
    setReload,
    initState,
}: CreateSubscriptionProps) {
    const [inProgress, setInProgress] = useState(false);
    const [nameError, setNameError] = useState<string | null>(null);
    const [optionError, setOptionError] = useState<string | null>(null);
    const [formError, setFormError] = useState("");
    const createOrUpdate = (initState && "Update") || "Create";
    const [formState, setFormState] = useState<SubscriptionFields>({
        ...initState,
    });

    const [selectedEvent, setSelectedEvent] = useState<OptionDefinition | null>({
        label: formState.eventName,
        value: formState.eventName,
    });
    const [selectedEntityType, setSelectedEntityType] = useState<OptionDefinition | null>({
        label: formState.entityName,
        value: formState.entityName,
    });
    const [searchedEntity, setSearchedEntity] = useState<string | null>(null);
    const [searchResult, setSearchResult] = useState<any | null>(null);

    //Enabled Features
    const config = Cache.getItem("config");
    const [useNoOpenSearch] = useState(
        config.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH)
    );

    const handleEntitySearch = async () => {
        try {
            if (searchedEntity && selectedEntityType) {
                let result;
                if (selectedEntityType.value === "Asset") {
                    if (!useNoOpenSearch) {
                        //Use OpenSearch API
                        const body = {
                            tokens: [],
                            operation: "AND",
                            from: 0,
                            size: 100,
                            query: searchedEntity,
                            filters: [
                                {
                                    query_string: {
                                        query: '(_rectype:("asset"))',
                                    },
                                },
                            ],
                        };
                        console.log("body", body);

                        result = await API.post("api", "search", {
                            "Content-type": "application/json",
                            body: body,
                        });
                        result = result?.hits?.hits;
                    } else {
                        //Use assets API
                        result = await fetchAllAssets();
                        result = result?.filter(
                            (item: any) => item.databaseId.indexOf("#deleted") === -1
                        );
                        result = result?.filter((item: any) =>
                            item.assetName.toLowerCase().includes(searchedEntity.toLowerCase())
                        );
                    }
                    // } else if (selectedEntityType.value === "Database") {
                    //     result = await fetchDatabase({ databaseId: searchedEntity });
                }
                if (result && Object.keys(result).length > 0) {
                    setSearchResult(result);
                } else {
                    setSearchResult(null);
                }
                setShowTable(true);
            }
        } catch (error) {
            console.error("Error fetching data:", error);
        }
    };

    // const databaseCols = [
    //     {
    //         id: "databaseId",
    //         header: "Database Name",
    //         cell: (item: any) => <Link href={`#/databases`}>{item.databaseName}</Link>,
    //         sortingField: "name",
    //         isRowHeader: true,
    //     },
    //     {
    //         id: "description",
    //         header: "Description",
    //         cell: (item: any) => item.description,
    //         sortingField: "alt",
    //     },
    // ];
    const assetCols = [
        {
            id: "assetId",
            header: "Asset Name",
            cell: (item: any) => (
                <Link href={`#/databases/${item.databaseName}/assets/${item.assetId}`}>
                    {item.assetName}
                </Link>
            ),
            sortingField: "name",
            isRowHeader: true,
        },
        {
            id: "databaseId",
            header: "Database Name",
            cell: (item: any) => item.databaseName,
            sortingField: "name",
            isRowHeader: true,
        },
        {
            id: "description",
            header: "Description",
            cell: (item: any) => item.description,
            sortingField: "alt",
        },
    ];
    // const databaseItems = searchResult
    //     ? [
    //           {
    //               databaseName: searchResult.databaseId || "",
    //               description: searchResult.description || "",
    //           },
    //       ]
    //     : [];

    const assetItems = Array.isArray(searchResult)
        ? !useNoOpenSearch
            ? searchResult.map((result) => ({
                  //Search API results
                  assetName: result._source.str_assetname || "",
                  databaseName: result._source.str_databaseid || "",
                  description: result._source.str_description || "",
                  assetId: result._source.str_assetid || "",
              }))
            : //FetchAllAssets API Results (No OpenSearch)
              searchResult.map((result) => ({
                  //Search API results
                  assetName: result.assetName || "",
                  databaseName: result.databaseId || "",
                  description: result.description || "",
                  assetId: result.assetId || "",
              }))
        : []; //No result

    const columns = assetCols; //selectedEntityType?.value === "Asset" ? assetCols : databaseCols;
    const items = assetItems; //selectedEntityType?.value === "Asset" ? assetItems : databaseItems;

    const [selectedItems, setSelectedItems] = useState<any[]>([]);
    const [showTable, setShowTable] = useState(false);

    const createBody = () => {
        ruleBody.eventName = formState.eventName;
        ruleBody.entityName = formState.entityName;
        ruleBody.entityId =
            selectedItems.length > 0
                ? selectedEntityType?.value === "Database"
                    ? selectedItems[0].databaseName
                    : selectedItems[0].assetId
                : "";

        ruleBody.subscribers = formState.subscribers
            ? (typeof formState.subscribers === "string" ? formState.subscribers.split(",") : [])
                  .map((subscriber) => subscriber.trim())
                  .filter((subscriber) => subscriber !== "")
            : [];
    };
    useEffect(() => {
        setNameError("");
    }, [createOrUpdate]);
    return (
        <Modal
            visible={open}
            onDismiss={() => {
                setOpen(false);
                setFormState({
                    ...initState,
                });
                setSelectedEvent(null);
                setSelectedEntityType(null);
                setSearchedEntity(null);
                setSearchResult(null);
                setShowTable(false);
                setSelectedItems([]);
                setFormError("");
            }}
            size="large"
            header={`${createOrUpdate} Subscription`}
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
                                setSelectedEvent(null);
                                setSelectedEntityType(null);
                                setSearchedEntity(null);
                                setSearchResult(null);
                                setShowTable(false);
                                setInProgress(false);
                                setNameError(null);
                                setOptionError(null);
                                setSelectedItems([]);
                                setFormError("");
                            }}
                        >
                            Cancel
                        </Button>

                        <Button
                            variant="primary"
                            onClick={() => {
                                setInProgress(true);
                                createBody();
                                if (createOrUpdate === "Create") {
                                    API.post("api", "subscriptions", {
                                        body: ruleBody,
                                    })
                                        .then((res) => {
                                            console.log("Create subs", res);
                                            setOpen(false);
                                            setReload(true);
                                            setFormState({
                                                ...initState,
                                            });
                                            setSelectedItems([]);
                                            setFormError("");
                                            setSelectedEvent(null);
                                            setSelectedEntityType(null);
                                            setSearchedEntity(null);
                                            setSearchResult(null);
                                            setShowTable(false);
                                        })
                                        .catch((err) => {
                                            console.log("Create subs error", err);
                                            if (err.response && err.response.status === 400) {
                                                const errorMessage =
                                                    "Subscription for this entity" +
                                                    " already exists or is not valid";
                                                setOptionError(errorMessage);
                                                setInProgress(true);
                                            }
                                            if (err.response && err.response.status === 403) {
                                                let msg = `Unable to add subscription. Error: Request failed with status code 403`;
                                                setFormError(msg);
                                            }
                                            setShowTable(false);
                                        })
                                        .finally(() => {
                                            setInProgress(false);
                                        });
                                } else {
                                    ruleBody.entityId = formState.entityId;
                                    // selectedEntityType?.value === "Database"
                                    //     ? formState.databaseId
                                    //     : formState.entityId;
                                    API.put("api", "subscriptions", {
                                        body: ruleBody,
                                    })
                                        .then((res) => {
                                            console.log("Update subs", res);
                                            setOpen(false);
                                            setReload(true);
                                            setFormState({
                                                ...initState,
                                            });
                                            setSelectedItems([]);
                                            setFormError("");
                                        })
                                        .catch((err) => {
                                            console.log("Update subs error", err);
                                            if (err.response && err.response.status === 403) {
                                                let msg = `Unable to update subscription. Error: Request failed with status code 403`;
                                                setFormError(msg);
                                            }
                                        })
                                        .finally(() => {
                                            setInProgress(false);
                                        });
                                }
                            }}
                            disabled={inProgress || validateUsers(formState.subscribers) !== null}
                            data-testid={`${createOrUpdate}-subscriptions-button`}
                        >
                            {createOrUpdate} Subscription
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <Form errorText={formError}>
                <SpaceBetween direction="vertical" size="l">
                    <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                        <FormField
                            label="Event Type"
                            constraintText="Required. Select one event type"
                        >
                            <Select
                                selectedOption={
                                    createOrUpdate === "Update"
                                        ? { label: formState.eventName, value: formState.eventName }
                                        : selectedEvent
                                }
                                placeholder="Event Types"
                                options={[
                                    {
                                        label: "Asset Version Change",
                                        value: "Asset Version Change",
                                    },
                                ]}
                                disabled={createOrUpdate === "Update"}
                                onChange={({ detail }) => {
                                    setSelectedEvent(detail.selectedOption as OptionDefinition);
                                    setFormState({
                                        ...formState,
                                        eventName: detail.selectedOption.label ?? "",
                                    });
                                }}
                            />
                        </FormField>
                        <FormField
                            label="Entity Type"
                            constraintText="Required. Select one entity type"
                        >
                            <Select
                                selectedOption={
                                    createOrUpdate === "Update"
                                        ? {
                                              label: formState.entityName,
                                              value: formState.entityName,
                                          }
                                        : selectedEntityType
                                }
                                placeholder="Entity Type"
                                options={[
                                    { label: "Asset", value: "Asset" },
                                    //{ label: "Database", value: "Database" },
                                ]}
                                disabled={createOrUpdate === "Update"}
                                onChange={({ detail }) => {
                                    setOptionError("");
                                    setSelectedEntityType(
                                        detail.selectedOption as OptionDefinition
                                    );
                                    setFormState({
                                        ...formState,
                                        entityName: detail.selectedOption.label ?? "",
                                    });
                                    setShowTable(false);
                                    setSearchedEntity(null);
                                    setSelectedItems([]);
                                }}
                            />
                        </FormField>
                    </Grid>
                    <FormField
                        errorText={optionError}
                        label="Entity Name"
                        constraintText="Required. Select one"
                    >
                        <Input
                            placeholder={createOrUpdate === "Update" ? "" : "Search"}
                            type={createOrUpdate === "Update" ? "text" : "search"}
                            value={
                                createOrUpdate === "Update"
                                    ? formState.entityValue
                                    : searchedEntity || ""
                            }
                            onChange={({ detail }) => {
                                setSearchedEntity(detail.value);
                                setShowTable(false);
                                setSelectedItems([]);
                                setOptionError("");
                            }}
                            onKeyDown={({ detail }) => {
                                if (detail.key === "Enter") {
                                    setOptionError("");
                                    handleEntitySearch();
                                }
                            }}
                            disabled={createOrUpdate === "Update"}
                            data-testid="entity-name-input"
                        />
                    </FormField>
                    {showTable && (
                        <FormField label="Entity">
                            <CustomTable
                                columns={columns}
                                items={items}
                                selectedItems={selectedItems}
                                setSelectedItems={setSelectedItems}
                                trackBy={
                                    "assetId"
                                    // selectedEntityType?.value === "Database"
                                    //     ? "databaseName"
                                    //     : "assetId"
                                }
                            />
                        </FormField>
                    )}

                    <FormField
                        label="Subscribers"
                        constraintText="Required. Please enter all the User IDs of the subscribers comma separated (or resource account emails)."
                        errorText={nameError}
                    >
                        <Input
                            value={formState.subscribers}
                            onChange={({ detail }) => {
                                setFormState({ ...formState, subscribers: detail.value });
                                setNameError(validateUsers(detail.value));
                            }}
                            placeholder="Enter all the subscribers"
                            data-testid="subscribers"
                        />
                    </FormField>
                </SpaceBetween>
            </Form>
        </Modal>
    );
}
