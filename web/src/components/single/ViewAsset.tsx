/* eslint-disable jsx-a11y/anchor-is-valid */
/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    Box,
    BreadcrumbGroup,
    Button,
    Container, ExpandableSection,
    FormField,
    Grid,
    Header,
    Link,
    SegmentedControl,
    SpaceBetween,
    Spinner,
} from "@cloudscape-design/components";

import ControlledMetadata from "../metadata/ControlledMetadata";
import ImgViewer from "../viewers/ImgViewer";
import React, {createContext, Suspense, useEffect, useState} from "react";
import { useParams } from "react-router";
import {
    downloadAsset,
    fetchAsset,
    fetchDatabaseWorkflows,
    fetchWorkflowExecutions,
} from "../../services/APIService";
/**
 * No viewer yet for cad and archive file formats
 */
import AssetSelectorWithModal from "../selectors/AssetSelectorWithModal";
import RelatedTableList from "../list/RelatedTableList";
import { WorkflowExecutionListDefinition } from "../list/list-definitions/WorkflowExecutionListDefinition";
import WorkflowSelectorWithModal from "../selectors/WorkflowSelectorWithModal";
import localforage from "localforage";
import { ErrorBoundary } from "react-error-boundary";
import Synonyms from "../../synonyms";
import { UpdateAsset } from "../createupdate/UpdateAsset";
import {FileManager} from "../filemanager/FileManager";
import {AssetDetail} from "../../pages/AssetUpload";
import {FileUploadTableItem} from "../../pages/AssetUpload/FileUploadTable";

export default function ViewAsset() {
    const { databaseId, assetId, pathViewType } = useParams();

    const [assetDetail, setAssetDetail] = useState<AssetDetail>();
    const AssetDetailContext = createContext<AssetDetail | undefined>(undefined);
    const [reload, setReload] = useState(true);
    const [asset, setAsset] = useState<any>({});
    const [openUpdateAsset, setOpenUpdateAsset] = useState(false);

    //workflow
    const [loading, setLoading] = useState(true);
    const [allItems, setAllItems] = useState<any[]>([]);
    const [workflowOpen, setWorkflowOpen] = useState(false);
    const [containsIncompleteUploads, setContainsIncompleteUploads] = useState(false);

    const handleCreateWorkflow = () => {
        //@ts-ignore
        window.location = `/databases/${databaseId}/workflows/create`;
    };

    const WorkflowHeaderControls = () => {
        return (
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
        );
    };

    useEffect(() => {
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
            getData();
        }
    }, [reload, assetId, databaseId, asset]);

    const handleOpenUpdateAsset = (mode: boolean) => {
        setOpenUpdateAsset(mode);
    };

    useEffect(() => {
        const getData = async () => {
            if (databaseId && assetId) {
                const item = await fetchAsset({ databaseId: databaseId, assetId: assetId });
                if (item !== false) {
                    setAsset(item);
                }
                if(assetId) {
                    localforage.getItem(assetId).then((value: any) => {
                        if (value && value.Asset) {
                            console.log("Reading from localforage:", value);
                            for (let i = 0; i < value.Asset.length; i++) {
                                if (
                                    value.Asset[i].status !== "Completed" &&
                                    value.Asset[i].loaded !== value.Asset[i].total
                                ) {
                                    setContainsIncompleteUploads(true);
                                    break;
                                }
                            }
                            setAssetDetail(value)
                        } else {
                            setAssetDetail({
                                isMultiFile: item.isMultiFile,
                                assetId: assetId,
                                assetName: item.assetName,
                                databaseId: databaseId,
                                description: item.description,
                                bucket: item.assetLocation['Bucket'],
                                key: item.assetLocation['Key'],
                                assetType: item.assetType,
                                isDistributable: item.isDistributable,
                                Asset: []
                            })
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
        <>
            {assetId && (
                <>
                    <Box padding={{ top: "s", horizontal: "l" }}>
                        <SpaceBetween direction="vertical" size="l">
                            <BreadcrumbGroup
                                items={[
                                    { text: Synonyms.Databases, href: "/databases/" },
                                    {
                                        text: databaseId,
                                        href: "/databases/" + databaseId + "/assets/",
                                    },
                                    { text: asset?.assetName, href: "" },
                                ]}
                                ariaLabel="Breadcrumbs"
                            />
                            <Grid gridDefinition={[{ colspan: 4 }]}>
                                <h1>{asset?.assetName}</h1>
                            </Grid>
                            <div id="view-edit-asset-right-column">
                                {
                                    assetDetail && <FileManager asset={assetDetail} />
                                }
                            </div>
                            <ExpandableSection
                                headerText={"Asset Details"}
                            >

                                <div id="view-edit-asset-left-column">
                                    <Container
                                        header={
                                            <div className="view-edit-asset-header">
                                                <div className="asset-edit-button">
                                                    <Button
                                                        onClick={() =>
                                                            handleOpenUpdateAsset(true)
                                                        }
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
                                        {containsIncompleteUploads && (
                                            <>
                                                <h5>Finish Incomplete uploads</h5>
                                                <Link
                                                    href={`/databases/${databaseId}/assets/${assetId}/uploads`}
                                                >
                                                    {" "}
                                                    Finish Incomplete uploads{" "}
                                                </Link>
                                            </>
                                        )}
                                    </Container>
                                </div>

                            </ExpandableSection>
                            <ExpandableSection
                                headerText={"Metadata"}
                            >
                                <ErrorBoundary
                                    fallback={
                                        <div>
                                            Metadata failed to load due to an error. Contact your VAMS
                                            administrator for help.
                                        </div>
                                    }
                                >
                                    { databaseId && <ControlledMetadata databaseId={databaseId} assetId={assetId} /> }
                                </ErrorBoundary>
                            </ExpandableSection>
                            <ExpandableSection
                                headerText={"Workflows"}
                            >
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
                            </ExpandableSection>
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
        </>
    );
}
