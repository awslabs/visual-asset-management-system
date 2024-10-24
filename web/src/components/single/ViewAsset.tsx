/* eslint-disable jsx-a11y/anchor-is-valid */
/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    Alert,
    Box,
    BreadcrumbGroup,
    Button,
    Container,
    FormField,
    Grid,
    Header,
    Modal,
    SegmentedControl,
    SpaceBetween,
    Spinner,
    Link,
    Table,
    Select,
    Input,
    Form,
    AlertProps,
    ExpandableSection,
} from "@cloudscape-design/components";

import ControlledMetadata from "../metadata/ControlledMetadata";
import { useEffect, useReducer, useState } from "react";
import { Cache } from "aws-amplify";
import { useNavigate, useParams } from "react-router";
import {
    deleteAssetLink,
    fetchAsset,
    fetchAssetLinks,
    fetchAllAssets,
    fetchDatabaseWorkflows,
    fetchWorkflowExecutions,
    fetchtagTypes,
} from "../../services/APIService";
import { assetDetailReducer, AssetDetailContext } from "../../context/AssetDetailContext";
/**
 * No viewer yet for cad and archive file formats
 */
import RelatedTableList from "../list/RelatedTableList";
import { WorkflowExecutionListDefinition } from "../list/list-definitions/WorkflowExecutionListDefinition";
import WorkflowSelectorWithModal from "../selectors/WorkflowSelectorWithModal";
import localforage from "localforage";
import { ErrorBoundary } from "react-error-boundary";
import Synonyms from "../../synonyms";
import { UpdateAsset } from "../createupdate/UpdateAsset";
import { FileManager } from "../filemanager/FileManager";
import { AssetDetail } from "../../pages/AssetUpload";
import BellIcon from "../../resources/img/bellIcon.svg";
import CustomTable from "../table/CustomTable";
import { API } from "aws-amplify";
import { featuresEnabled } from "../../common/constants/featuresEnabled";

var userName = "";

fetchtagTypes().then((res) => {
    const tagTypesString = JSON.stringify(res);
    localStorage.setItem("tagTypes", tagTypesString);
});

export default function ViewAsset() {
    const { databaseId, assetId, pathViewType } = useParams();

    const subscriptionBody = {
        eventName: "Asset Version Change",
        entityName: "Asset",
        subscribers: [userName],
        entityId: assetId,
    };
    const checkBody = {
        userId: userName,
        assetId: assetId,
    };

    const [state, dispatch] = useReducer(assetDetailReducer, {} as AssetDetail);
    const [reload, setReload] = useState(true);
    const [showWorkflow, setShowWorkflow] = useState(true);
    const [asset, setAsset] = useState<any>({});
    const [deleteFromCache, setDeleteFromCache] = useState(false);
    const [openUpdateAsset, setOpenUpdateAsset] = useState(false);
    const navigate = useNavigate();

    //workflow
    const [loading, setLoading] = useState(true);
    const [allItems, setAllItems] = useState<any[]>([]);
    const [workflowOpen, setWorkflowOpen] = useState(false);
    const [containsIncompleteUploads, setContainsIncompleteUploads] = useState(false);

    //Enabled Features
    const config = Cache.getItem("config");
    const [useNoOpenSearch] = useState(
        config.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH)
    );

    const handleCreateWorkflow = () => {
        //@ts-ignore
        navigate(`/databases/${databaseId}/workflows/create`);
    };

    const WorkflowHeaderControls = () => {
        return (
            <>
                <div
                    style={{
                        width: "calc(100% - 40px)",
                        textAlign: "right",
                        position: "absolute",
                    }}
                >
                    <Button onClick={() => setWorkflowOpen(true)}>Execute Workflow</Button>
                    <span>&nbsp;&nbsp;&nbsp;&nbsp;</span>
                    <Button onClick={handleCreateWorkflow} variant="primary">
                        Create Workflow
                    </Button>
                </div>
            </>
        );
    };

    useEffect(() => {
        userName = localStorage.getItem("userName")!;
        const getData = async () => {
            setLoading(true);
            const items = await fetchDatabaseWorkflows({ databaseId: databaseId });
            if (items !== false && Array.isArray(items)) {
                const newRows = [];
                for (let i = 0; i < items.length; i++) {
                    const newParentRow = Object.assign({}, items[i]);
                    newParentRow.name = newParentRow?.workflowId;
                    newRows.push(newParentRow);
                    const workflowId = newParentRow?.workflowId;
                    const subItems = await fetchWorkflowExecutions({
                        databaseId: databaseId,
                        assetId: assetId,
                        workflowId: workflowId,
                    });
                    if (subItems !== false && Array.isArray(subItems)) {
                        for (let j = 0; j < subItems.length; j++) {
                            const newParentRowChild = Object.assign({}, subItems[j]);
                            newParentRowChild.parentId = workflowId;
                            newParentRowChild.name = newParentRowChild.executionId;
                            if (newParentRowChild.stopDate === "") {
                                newParentRowChild.stopDate = "N/A";
                            }
                            newRows.push(newParentRowChild);
                        }
                    }
                }
                setAllItems(newRows);
                setLoading(false);
                setReload(false);
            }
        };
        if (reload) {
            fetchtagTypes().then((res) => {
                const tagTypesString = JSON.stringify(res);
                localStorage.setItem("tagTypes", tagTypesString);
            });
            getData();
        }
    }, [reload, assetId, databaseId, asset]);

    useEffect(() => {
        if (deleteFromCache) {
            localforage
                .removeItem(assetId!)
                .then(function () {})
                .catch(function (err) {
                    console.log(err);
                });
        }
    }, [deleteFromCache]);

    const handleOpenUpdateAsset = (mode: boolean) => {
        setOpenUpdateAsset(mode);
    };

    const [subscribed, setSubscribed] = useState(false);
    const [showModal, setShowModal] = useState(false);
    const [messageVisible, setMessageVisible] = useState(false);
    const [disable, setDisable] = useState(false);
    const [message, setMessage] = useState("");
    const [alertLabel, setAlertLabel] = useState<AlertProps.Type>("success");
    const [nameErrror, setNameError] = useState("");
    const [showLinkModal, setShowLinkModal] = useState(false);
    const [apiResponse, setApiResponse] = useState<any>({});
    const [selectedItems, setSelectedItems] = useState<any[]>([]);
    const [selectedLinkType, setSelectedLinkType] = useState<any>();
    const [searchedEntity, setSearchedEntity] = useState("");
    const [showTable, setShowTable] = useState(false);
    const [searchResult, setSearchResult] = useState<any[]>([]);
    const [delDisabled, setDelDisable] = useState(false);
    const [addDisabled, setAddDisable] = useState(false);
    const [modalDisable, setModalDisable] = useState(false);
    const [showDeleteModal, setShowDeleteModal] = useState(false);
    const [modalTitle, setModalTitle] = useState("");
    const [formError, setFormError] = useState("");
    const [showLinkAlert, setShowLinkAlert] = useState(false);
    const [linkAlertMsg, setLinkAlertMsg] = useState("");

    const [modalAsset, setModalAsset] = useState({
        assetName: "",
        relationId: "",
    });

    useEffect(() => {
        const getResponse = async () => {
            const res = await fetchAssetLinks({ assetId: assetId });
            setApiResponse(res);
        };
        if (reload) {
            getResponse();
        }
    }, [reload, assetId]);

    useEffect(() => {
        checkSubscriptionStatus();
    }, []);
    const checkSubscriptionStatus = async () => {
        checkBody.userId = userName;
        try {
            const response = await API.post("api", "check-subscription", {
                body: checkBody,
            });

            if (response.message === "success") {
                setSubscribed(true);
            } else {
                setSubscribed(false);
            }
        } catch (error) {
            console.error("Error:", error);
        }
    };
    const alert = (msg: any, type: any) => {
        setMessage(msg);
        setMessageVisible(true);
        setTimeout(() => {
            setMessageVisible(false);
            setMessage("");
        }, 7000);
        setAlertLabel(type);
    };

    const handleSubscribe = async () => {
        setDisable(true);
        try {
            if (!userName) {
                alert("Username not available", "error");
                setDisable(false);
                setShowModal(false);

                return;
            }
            if (subscribed) {
                let eventName = (
                    <span key="eventName" style={{ fontStyle: "italic" }}>
                        {subscriptionBody.eventName}
                    </span>
                );
                let assetName = (
                    <span key="assetName" style={{ fontStyle: "italic" }}>
                        {asset.assetName}
                    </span>
                );

                let msg = (
                    <span>
                        You've successfully unsubscribed for {eventName} updates from {assetName}'.
                        To resume receiving updates, please subscribe again.
                    </span>
                );

                API.del("api", "unsubscribe", {
                    body: subscriptionBody,
                })
                    .then((response) => {
                        setSubscribed(false);
                        setShowModal(false);
                        setDisable(false);
                        alert(msg, "success");
                    })
                    .catch((err) => {
                        if (err.response.status === 403) {
                            alert(
                                "Unable to unsubscribe. Error: Request failed with status code 403",
                                "error"
                            );
                            setShowModal(false);
                            setDisable(false);
                        } else alert("Something went wrong. Please try again", "error");
                        console.log("Error", err);
                    });
            } else {
                let eventName = (
                    <span key="eventName" style={{ fontStyle: "italic" }}>
                        {subscriptionBody.eventName}
                    </span>
                );
                let assetName = (
                    <span key="assetName" style={{ fontStyle: "italic" }}>
                        {asset.assetName}
                    </span>
                );

                let msg = (
                    <span>
                        You've successfully signed up for receiving updates on {eventName} for{" "}
                        {assetName}. Please check your inbox and confirm the subscription.
                    </span>
                );

                API.post("api", "subscriptions", {
                    body: subscriptionBody,
                })
                    .then((response) => {
                        setSubscribed(true);
                        setShowModal(false);
                        setDisable(false);
                        alert(msg, "success");
                    })
                    .catch((err) => {
                        if (err.response.status === 403) {
                            alert(
                                "Unable to subscribe. Error: Request failed with status code 403",
                                "error"
                            );
                        } else alert("Something went wrong. Please try again", "error");
                        console.log("Error", err);
                        setShowModal(false);
                        setDisable(false);
                    });
            }
        } catch (error) {
            alert("Something went wrong. Please try again", "error");
            console.error("Error:", error);
            setShowModal(false);
            setDisable(false);
        }
    };

    const handleEntitySearch = async () => {
        try {
            if (searchedEntity) {
                let result;
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

                if (result && Object.keys(result).length > 0) {
                    setSearchResult(result);
                } else {
                    setSearchResult([]);
                }
                setShowTable(true);
            }
        } catch (error) {
            console.error("Error fetching data:", error);
        }
    };

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

    const assetItems = Array.isArray(searchResult)
        ? !useNoOpenSearch
            ? searchResult.map((result: any) => ({
                  //Search API results
                  assetName: result._source.str_assetname || "",
                  databaseName: result._source.str_databaseid || "",
                  description: result._source.str_description || "",
                  assetId: result._source.str_assetid || "",
              }))
            : //FetchAllAssets API Results (No OpenSearch)
              searchResult.map((result: any) => ({
                  //Search API results
                  assetName: result.assetName || "",
                  databaseName: result.databaseId || "",
                  description: result.description || "",
                  assetId: result.assetId || "",
              }))
        : []; //No result

    const assetLinkBody = {
        assetIdFrom: "",
        assetIdTo: "",
        relationshipType: "",
    };

    const deletModal = (item: any, title: any) => {
        setDelDisable(true);
        console.log(item);
        setShowDeleteModal(true);
        setModalTitle(title);
        setModalAsset({
            assetName: item.assetName,
            relationId: item.relationId,
        });
    };
    const deleteLink = async () => {
        try {
            let relationId = modalAsset.relationId;
            setModalDisable(true);

            let res = await deleteAssetLink({ relationId });
            const apiRes = await fetchAssetLinks({ assetId });
            setApiResponse(apiRes);
            setDelDisable(false);
            setShowDeleteModal(false);
            setModalDisable(false);
            if (res.response.status === 403) {
                setLinkAlertMsg(res.message);
                setShowLinkAlert(true);
                setTimeout(() => {
                    setShowLinkAlert(false);
                }, 10000);
            }
        } catch (error) {
            console.error("Error deleting asset:", error);
        }
    };

    const addLink = (linktype: any, selectAsset: any) => {
        let currAsset = assetId;
        switch (linktype) {
            case "parent":
                assetLinkBody.assetIdFrom = currAsset!;
                assetLinkBody.assetIdTo = selectAsset;
                assetLinkBody.relationshipType = "parent-child";
                break;
            case "child":
                assetLinkBody.assetIdFrom = selectAsset;
                assetLinkBody.assetIdTo = currAsset!;
                assetLinkBody.relationshipType = "parent-child";
                break;
            case "related":
                assetLinkBody.assetIdFrom = currAsset!;
                assetLinkBody.assetIdTo = selectAsset;
                assetLinkBody.relationshipType = "related";
                break;
        }
        console.log(assetLinkBody);
        API.post("api", "asset-links", {
            body: assetLinkBody,
        })
            .then((response) => {
                console.log("API call successful", response);
                setShowLinkModal(false);
                setSearchedEntity("");
                setSelectedLinkType(undefined);
                setAddDisable(false);
                setShowTable(false);
                fetchAssetLinks({ assetId: assetId }).then((res) => {
                    setApiResponse(res);
                });
                setFormError("");
                setNameError("");
            })
            .catch((err) => {
                if (err.response?.status === 400) {
                    setNameError(err.response.data.message);
                    console.log(err.response.data.message);
                } else {
                    console.log("create tag error", err);
                    let msg = `Unable to add ${linktype} link. Error: Request failed with status code ${err.response.status}`;
                    setFormError(msg);
                }
            })
            .finally(() => {
                setAddDisable(false);
            });
    };

    useEffect(() => {
        const getData = async () => {
            if (databaseId && assetId) {
                const item = await fetchAsset({ databaseId: databaseId, assetId: assetId });
                if (item !== false) {
                    setAsset(item);
                }
                if (assetId) {
                    localforage.getItem(assetId).then((value: any) => {
                        if (value && value.Asset) {
                            // console.log("Reading from localforage:", value);
                            for (let i = 0; i < value.Asset.length; i++) {
                                if (
                                    value.Asset[i].status !== "Completed" &&
                                    value.Asset[i].loaded !== value.Asset[i].total
                                ) {
                                    setContainsIncompleteUploads(true);
                                    break;
                                }
                            }
                            dispatch({
                                type: "SET_ASSET_DETAIL",
                                payload: {
                                    isMultiFile: value.isMultiFile,
                                    assetId: assetId,
                                    assetName: value.assetName,
                                    databaseId: databaseId,
                                    description: value.description,
                                    key: value.key || value.assetLocation["Key"],
                                    assetLocation: {
                                        Key: value.key || value.assetLocation["Key"],
                                    },
                                    assetType: value.assetType,
                                    isDistributable: value.isDistributable,
                                    Asset: value.Asset,
                                },
                            });
                        } else {
                            dispatch({
                                type: "SET_ASSET_DETAIL",
                                payload: {
                                    isMultiFile: item.isMultiFile,
                                    assetId: assetId,
                                    assetName: item.assetName,
                                    databaseId: databaseId,
                                    description: item.description,
                                    key: item.key || item.assetLocation["Key"],
                                    assetLocation: {
                                        Key: item.key || item.assetLocation["Key"],
                                    },
                                    assetType: item.assetType,
                                    isDistributable: item.isDistributable,
                                    Asset: [],
                                },
                            });
                        }
                    });
                }
            }
        };
        if (reload && !pathViewType) {
            getData();
        }
    }, [reload, assetId, databaseId, pathViewType, asset]);

    // @ts-ignore
    // @ts-ignore
    return (
        <AssetDetailContext.Provider value={{ state, dispatch }}>
            {assetId && (
                <>
                    <Box padding={{ top: "s", horizontal: "l" }}>
                        <SpaceBetween direction="vertical" size="l">
                            <BreadcrumbGroup
                                items={[
                                    { text: Synonyms.Databases, href: "#/databases/" },
                                    {
                                        text: databaseId,
                                        href: "#/databases/" + databaseId + "/assets/",
                                    },
                                    { text: asset?.assetName, href: "" },
                                ]}
                                ariaLabel="Breadcrumbs"
                            />
                            <Grid gridDefinition={[{ colspan: 4 }]}>
                                <Container
                                    header={
                                        <Header
                                            variant="h2"
                                            actions={
                                                asset && (
                                                    <Button
                                                        iconAlign="right"
                                                        iconUrl={subscribed ? BellIcon : ""}
                                                        variant={subscribed ? "normal" : "primary"}
                                                        onClick={() => setShowModal(true)}
                                                    >
                                                        {subscribed ? "Subscribed" : "Subscribe"}
                                                    </Button>
                                                )
                                            }
                                        >
                                            {asset?.assetName}
                                        </Header>
                                    }
                                >
                                    {messageVisible && <Alert type={alertLabel}>{message}</Alert>}
                                </Container>
                                <Modal
                                    onDismiss={() => {
                                        setShowDeleteModal(false);
                                        setDelDisable(false);
                                    }}
                                    visible={showDeleteModal}
                                    size="medium"
                                    footer={
                                        <Box float="right">
                                            <SpaceBetween direction="horizontal" size="xs">
                                                <Button
                                                    variant="link"
                                                    onClick={() => {
                                                        setShowDeleteModal(false);
                                                        setDelDisable(false);
                                                    }}
                                                >
                                                    No
                                                </Button>
                                                <Button
                                                    variant="primary"
                                                    disabled={modalDisable}
                                                    onClick={() => {
                                                        deleteLink();
                                                    }}
                                                >
                                                    Yes
                                                </Button>
                                            </SpaceBetween>
                                        </Box>
                                    }
                                    header={"Delete Link"}
                                >
                                    <div>
                                        <p>
                                            {`Do you want to delete ${modalTitle} link: '`}
                                            <i>{modalAsset.assetName}</i>
                                            {`' ?`}
                                        </p>
                                    </div>
                                </Modal>

                                <Modal
                                    onDismiss={() => {
                                        setShowModal(false);
                                    }}
                                    visible={showModal}
                                    footer={
                                        <Box float="right">
                                            <SpaceBetween direction="horizontal" size="xs">
                                                <Button
                                                    variant="link"
                                                    onClick={() => {
                                                        setShowModal(false);
                                                    }}
                                                >
                                                    No
                                                </Button>
                                                <Button
                                                    variant="primary"
                                                    loading={disable}
                                                    onClick={() => handleSubscribe()}
                                                >
                                                    Yes
                                                </Button>
                                            </SpaceBetween>
                                        </Box>
                                    }
                                    header={subscribed ? "Unsubscribe" : "Subscribe"}
                                >
                                    {subscribed ? (
                                        <p>
                                            Do you want to unsubscribe from{" "}
                                            <i>{subscriptionBody.eventName}</i> for{" "}
                                            <i>{asset.assetName}</i> asset?
                                        </p>
                                    ) : (
                                        <p>
                                            Do you want to subscribe to{" "}
                                            <i>{subscriptionBody.eventName}</i> for{" "}
                                            <i>{asset.assetName}</i> asset?
                                        </p>
                                    )}
                                </Modal>
                            </Grid>
                            <ExpandableSection headerText="Asset Details">
                                <div id="view-edit-asset-left-column">
                                    <Container
                                        header={
                                            <div className="view-edit-asset-header">
                                                <div className="asset-edit-button">
                                                    <Button
                                                        onClick={() => handleOpenUpdateAsset(true)}
                                                    >
                                                        Edit
                                                    </Button>
                                                </div>
                                                <Header variant="h2">
                                                    {Synonyms.Asset} Details
                                                </Header>
                                            </div>
                                        }
                                    >
                                        <h5>Id</h5>
                                        <>{asset?.assetId}</>
                                        <h5>Description</h5>
                                        <>{asset?.description}</>
                                        <h5>File Extension</h5>
                                        {asset?.assetType}
                                        <h5>Distributable</h5>
                                        <>{asset?.isDistributable === true ? "Yes" : "No"}</>
                                        <h5>Version</h5>
                                        <>{asset?.currentVersion?.Version}</>
                                        <h5>Date Modified</h5>
                                        {asset?.currentVersion?.DateModified}
                                        <h5>Tags</h5>
                                        {Array.isArray(asset?.tags) && asset.tags.length > 0
                                            ? asset.tags
                                                  .map((tag: any) => {
                                                      const tagType = JSON.parse(
                                                          localStorage.getItem("tagTypes") ||
                                                              "{'tagTypeName': '', 'tags': []}"
                                                      ).find((type: any) =>
                                                          type.tags.includes(tag)
                                                      );

                                                      //If tagType has required field add [R] to tag type name
                                                      if (tagType && tagType.required) {
                                                          tagType.tagTypeName += " [R]";
                                                      }

                                                      return tagType
                                                          ? `${tag} (${tagType.tagTypeName})`
                                                          : tag;
                                                  })
                                                  .join(", ")
                                            : "No tags available"}
                                        {containsIncompleteUploads && (
                                            <>
                                                <h5>Finish Incomplete uploads</h5>
                                                <Link
                                                    href={`#/databases/${databaseId}/assets/${assetId}/uploads`}
                                                >
                                                    {" "}
                                                    Finish Incomplete uploads{" "}
                                                </Link>
                                            </>
                                        )}
                                    </Container>
                                </div>
                            </ExpandableSection>
                            <div id="view-edit-asset-right-column">
                                {state && state.assetName && (
                                    <>
                                        <FileManager assetName={state.assetName} />
                                    </>
                                )}
                            </div>

                            {showWorkflow && (
                                <RelatedTableList
                                    allItems={allItems}
                                    loading={loading}
                                    listDefinition={WorkflowExecutionListDefinition}
                                    databaseId={databaseId}
                                    setReload={setReload}
                                    parentId={"workflowId"}
                                    //@ts-ignore
                                    HeaderControls={WorkflowHeaderControls}
                                />
                            )}
                            {!showWorkflow && (
                                <Container header={<Header variant="h2">Workflows</Header>}>
                                    <Alert
                                        statusIconAriaLabel="Error"
                                        type="error"
                                        header="Forbidden"
                                    >
                                        403 - Access Denied
                                    </Alert>
                                </Container>
                            )}
                            <Modal
                                visible={showLinkModal}
                                onDismiss={() => {
                                    setShowLinkModal(false);
                                    setSearchedEntity("");
                                    setSelectedLinkType(undefined);
                                    setShowTable(false);
                                    setFormError("");
                                    setNameError("");
                                }}
                                size="large"
                                header={"Add Linked Assets"}
                                footer={
                                    <Box float="right">
                                        <SpaceBetween direction="horizontal" size="xs">
                                            <Button
                                                variant="link"
                                                onClick={() => {
                                                    setShowLinkModal(false);
                                                    setSearchedEntity("");
                                                    setSelectedLinkType(undefined);
                                                    setShowTable(false);
                                                    setFormError("");
                                                    setNameError("");
                                                }}
                                            >
                                                Cancel
                                            </Button>
                                            <Button
                                                variant="primary"
                                                disabled={
                                                    addDisabled ||
                                                    !selectedLinkType ||
                                                    !selectedLinkType.value ||
                                                    !selectedItems[0]?.assetId
                                                }
                                                onClick={() => {
                                                    setAddDisable(true);
                                                    addLink(
                                                        selectedLinkType.value,
                                                        selectedItems[0]?.assetId
                                                    );
                                                }}
                                            >
                                                Add Links
                                            </Button>
                                        </SpaceBetween>
                                    </Box>
                                }
                            >
                                <Form errorText={formError}>
                                    <SpaceBetween direction="vertical" size="l">
                                        <FormField
                                            label="Relationship Type"
                                            constraintText="Required. Select one event type"
                                        >
                                            <Select
                                                selectedOption={selectedLinkType}
                                                placeholder="Relationship Types"
                                                options={[
                                                    {
                                                        label: "Parent To",
                                                        value: "parent",
                                                    },
                                                    {
                                                        label: "Child To",
                                                        value: "child",
                                                    },
                                                    {
                                                        label: "Related To",
                                                        value: "related",
                                                    },
                                                ]}
                                                onChange={({ detail }) => {
                                                    setSelectedLinkType(detail.selectedOption);
                                                    setShowTable(false);
                                                    setSearchedEntity("");
                                                }}
                                            />
                                        </FormField>

                                        <FormField
                                            label="Entity Name"
                                            constraintText="Input asset name. Press Enter to search."
                                            errorText={nameErrror}
                                        >
                                            <Input
                                                placeholder="Search"
                                                type="search"
                                                value={searchedEntity || ""}
                                                onChange={({ detail }) => {
                                                    console.log(detail.value);
                                                    setSearchedEntity(detail.value);
                                                    setShowTable(false);
                                                    setSelectedItems([]);
                                                    setNameError("");
                                                }}
                                                onKeyDown={({ detail }) => {
                                                    if (detail.key === "Enter") {
                                                        handleEntitySearch();
                                                    }
                                                }}
                                            />
                                        </FormField>
                                        {showTable && (
                                            <FormField label="Entity">
                                                <CustomTable
                                                    columns={assetCols}
                                                    items={assetItems}
                                                    selectedItems={selectedItems}
                                                    setSelectedItems={setSelectedItems}
                                                    trackBy={"assetId"}
                                                />
                                            </FormField>
                                        )}
                                    </SpaceBetween>
                                </Form>
                            </Modal>
                            <Container
                                header={
                                    <>
                                        <Header
                                            variant="h2"
                                            actions={
                                                <SpaceBetween direction="horizontal" size="xs">
                                                    <Button
                                                        variant="primary"
                                                        onClick={() => {
                                                            setShowLinkModal(true);
                                                        }}
                                                    >
                                                        Add Link
                                                    </Button>
                                                </SpaceBetween>
                                            }
                                        >
                                            Linked {Synonyms.Asset}s
                                        </Header>
                                        {showLinkAlert && (
                                            <Alert
                                                statusIconAriaLabel="Error"
                                                type="error"
                                                header="Forbidden"
                                                dismissible={true}
                                                onDismiss={() => {
                                                    setShowLinkAlert(false);
                                                }}
                                            >
                                                {linkAlertMsg}
                                            </Alert>
                                        )}
                                    </>
                                }
                            >
                                <Grid
                                    gridDefinition={[
                                        { colspan: 4 },
                                        { colspan: 4 },
                                        { colspan: 4 },
                                    ]}
                                >
                                    <Table
                                        columnDefinitions={[
                                            {
                                                id: "assetName",
                                                header: "Asset Name",
                                                cell: (item: any) => (
                                                    <Link
                                                        href={`#/databases/${item.databaseId}/assets/${item.assetId}`}
                                                    >
                                                        {item.assetName || "-"}
                                                    </Link>
                                                ),
                                                sortingField: "assetName",
                                                isRowHeader: true,
                                            },
                                            {
                                                id: "actions",
                                                header: "",
                                                cell: (item) => (
                                                    <Box float="right">
                                                        <Button
                                                            disabled={delDisabled}
                                                            iconName="remove"
                                                            variant="icon"
                                                            onClick={() =>
                                                                deletModal(item, "Parent")
                                                            }
                                                        ></Button>
                                                    </Box>
                                                ),
                                            },
                                        ]}
                                        items={apiResponse?.parent}
                                        loadingText="Loading Assets"
                                        sortingDisabled
                                        empty={
                                            <Box
                                                margin={{ vertical: "xs" }}
                                                textAlign="center"
                                                color="inherit"
                                            >
                                                <SpaceBetween size="m">
                                                    <b>No parent asset</b>
                                                </SpaceBetween>
                                            </Box>
                                        }
                                        header={<Header variant="h3">Parent Assets</Header>}
                                    />
                                    <Table
                                        columnDefinitions={[
                                            {
                                                id: "assetName",
                                                header: "Asset Name",
                                                cell: (item: any) => (
                                                    <Link
                                                        href={`#/databases/${item.databaseId}/assets/${item.assetId}`}
                                                    >
                                                        {item.assetName || "-"}
                                                    </Link>
                                                ),
                                                sortingField: "assetName",
                                                isRowHeader: true,
                                            },
                                            {
                                                id: "actions",
                                                header: "",
                                                cell: (item) => (
                                                    <Box float="right">
                                                        <Button
                                                            disabled={delDisabled}
                                                            iconName="remove"
                                                            variant="icon"
                                                            onClick={() =>
                                                                deletModal(item, "Child")
                                                            }
                                                        ></Button>
                                                    </Box>
                                                ),
                                            },
                                        ]}
                                        items={apiResponse?.child}
                                        loadingText="Loading Assets"
                                        sortingDisabled
                                        empty={
                                            <Box
                                                margin={{ vertical: "xs" }}
                                                textAlign="center"
                                                color="inherit"
                                            >
                                                <SpaceBetween size="m">
                                                    <b>No child asset</b>
                                                </SpaceBetween>
                                            </Box>
                                        }
                                        header={<Header variant="h3">Child Assets</Header>}
                                    />
                                    <Table
                                        columnDefinitions={[
                                            {
                                                id: "assetName",
                                                header: "Asset Name",
                                                cell: (item: any) => (
                                                    <Link
                                                        href={`#/databases/${item.databaseId}/assets/${item.assetId}`}
                                                    >
                                                        {item.assetName || "-"}
                                                    </Link>
                                                ),
                                                sortingField: "assetName",
                                                isRowHeader: true,
                                            },
                                            {
                                                id: "actions",
                                                header: "",
                                                cell: (item) => (
                                                    <Box float="right">
                                                        <Button
                                                            disabled={delDisabled}
                                                            iconName="remove"
                                                            variant="icon"
                                                            onClick={() =>
                                                                deletModal(item, "Related")
                                                            }
                                                        ></Button>
                                                    </Box>
                                                ),
                                            },
                                        ]}
                                        items={apiResponse?.relatedTo}
                                        loadingText="Loading Assets"
                                        sortingDisabled
                                        empty={
                                            <Box
                                                margin={{ vertical: "xs" }}
                                                textAlign="center"
                                                color="inherit"
                                            >
                                                <SpaceBetween size="m">
                                                    <b>No related asset</b>
                                                </SpaceBetween>
                                            </Box>
                                        }
                                        header={<Header variant="h3">Related Assets</Header>}
                                    />
                                </Grid>
                            </Container>

                            <ErrorBoundary
                                fallback={
                                    <div>
                                        Metadata failed to load due to an error. Contact your VAMS
                                        administrator for help.
                                    </div>
                                }
                            >
                                {databaseId && (
                                    <ControlledMetadata databaseId={databaseId} assetId={assetId} />
                                )}
                            </ErrorBoundary>
                        </SpaceBetween>
                    </Box>
                    {asset && (
                        <UpdateAsset
                            asset={asset}
                            isOpen={openUpdateAsset}
                            onClose={() => handleOpenUpdateAsset(false)}
                            onComplete={() => {
                                handleOpenUpdateAsset(false);
                                window.location.reload();
                            }}
                        />
                    )}
                    <WorkflowSelectorWithModal
                        assetId={assetId}
                        databaseId={databaseId}
                        open={workflowOpen}
                        setOpen={setWorkflowOpen}
                    />
                </>
            )}
        </AssetDetailContext.Provider>
    );
}
