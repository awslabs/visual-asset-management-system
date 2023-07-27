/* eslint-disable jsx-a11y/anchor-is-valid */
/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    Box,
    BreadcrumbGroup,
    Button,
    Container,
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
import React, { Suspense, useEffect, useState } from "react";
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
import CreateUpdateAsset from "../createupdate/CreateUpdateAsset";
import { actionTypes } from "../createupdate/form-definitions/types/FormDefinition";
import WorkflowSelectorWithModal from "../selectors/WorkflowSelectorWithModal";
import localforage from "localforage";
import { ErrorBoundary } from "react-error-boundary";
import Synonyms from "../../synonyms";
import { UpdateAsset } from "../createupdate/UpdateAsset";

const FolderViewer = React.lazy(() => import("../viewers/FolderViewer"));

export default function ViewAsset() {
    const { databaseId, assetId, pathViewType } = useParams();

    const [reload, setReload] = useState(true);
    const [viewType, setViewType] = useState("folder");
    const [asset, setAsset] = useState({});
    const [deleteFromCache, setDeleteFromCache] = useState(false);
    const [viewerOptions, setViewerOptions] = useState([]);
    const [viewerMode, setViewerMode] = useState("collapse");
    const [downloadUrl, setDownloadUrl] = useState(null);
    const [openUpdateAsset, setOpenUpdateAsset] = useState(false);

    //worflow
    const [loading, setLoading] = useState(true);
    const [allItems, setAllItems] = useState([]);
    const [workflowOpen, setWorkflowOpen] = useState(false);
    const [containsIncompleteUploads, setContainsIncompleteUploads] = useState(false);

    // error state
    const [assetDownloadError, setAssetDownloadError] = useState("");

    const handleCreateWorkflow = () => {
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
            localforage.getItem(assetId).then((value) => {
                if (value) {
                    //console.log("Reading from localforage:", value);
                    for (let i = 0; i < value.Asset.length; i++) {
                        if (
                            value.Asset[i].status !== "Completed" &&
                            value.Asset[i].loaded !== value.Asset[i].total
                        ) {
                            setContainsIncompleteUploads(true);
                            return;
                        }
                    }
                    //all downloads are complete. Can delete asset from browser cache
                    setDeleteFromCache(true);
                }
            });
        };
        if (reload) {
            getData();
        }
    }, [reload, assetId, databaseId]);

    useEffect(() => {
        if (deleteFromCache) {
            localforage
                .removeItem(assetId)
                .then(function () {
                    console.log("Removed item from localstorage", assetId);
                })
                .catch(function (err) {
                    console.log(err);
                });
        }
    }, [deleteFromCache]);

    const changeViewerMode = (mode) => {
        if (mode === "fullscreen" && viewerMode === "fullscreen") {
            mode = "collapse";
        }

        setViewerMode(mode);
    };

    useEffect(() => {
        if (assetId) {
            const fullscreenChangeHandler = (event) => {
                if (!document.fullscreenElement) {
                    if (viewerMode === "fullscreen") {
                        setViewerMode("collapse");
                    }
                }
            };
            const element = document.querySelector(
                "#view-edit-asset-right-column .visualizer-container"
            );
            element.removeEventListener("fullscreenchange", fullscreenChangeHandler);

            if (
                document.fullscreenElement ||
                document.webkitFullscreenElement ||
                document.mozFullScreenElement ||
                document.msFullscreenElement
            ) {
                if (document.exitFullscreen) {
                    document.exitFullscreen();
                } else if (document.mozCancelFullScreen) {
                    document.mozCancelFullScreen();
                } else if (document.webkitExitFullscreen) {
                    document.webkitExitFullscreen();
                } else if (document.msExitFullscreen) {
                    document.msExitFullscreen();
                }
            } else if (viewerMode === "fullscreen") {
                if (element.requestFullscreen) {
                    element.requestFullscreen();
                } else if (element.mozRequestFullScreen) {
                    element.mozRequestFullScreen();
                } else if (element.webkitRequestFullscreen) {
                    element.webkitRequestFullscreen(Element.ALLOW_KEYBOARD_INPUT);
                } else if (element.msRequestFullscreen) {
                    element.msRequestFullscreen();
                }
            }
            element.addEventListener("fullscreenchange", fullscreenChangeHandler);
            return () => {
                element.removeEventListener("fullscreenchange", fullscreenChangeHandler);
            };
        }
    }, [assetId, viewerMode]);

    const changeViewType = (event) => {
        setViewType(event.detail.selectedId);
    };

    const handleOpenUpdateAsset = (mode) => {
        setOpenUpdateAsset(mode);
    };

    useEffect(() => {
        const getData = async () => {
            if (databaseId && assetId) {
                const item = await fetchAsset({ databaseId: databaseId, assetId: assetId });
                if (item !== false) {
                    //console.log(item);
                    setAsset(item);
                    setViewerOptions([
                        { text: "Folder", id: "folder" },
                        { text: "Preview", id: "preview", disabled: !item.previewLocation },
                    ]);
                }
            }
        };
        if (reload && !pathViewType) {
            getData();
        }
    }, [reload, assetId, databaseId, pathViewType, asset]);

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
                                    { text: asset?.assetName },
                                ]}
                                ariaLabel="Breadcrumbs"
                            />
                            <Grid gridDefinition={[{ colspan: 4 }]}>
                                <h1>{asset?.assetName}</h1>
                            </Grid>
                            <Grid
                                gridDefinition={[
                                    { colspan: viewerMode === "collapse" ? 4 : 12 },
                                    { colspan: viewerMode === "collapse" ? 8 : 0 },
                                ]}
                            >
                                {viewerMode === "collapse" && (
                                    <div id="view-edit-asset-left-column">
                                        <Container
                                            class="view-edit-asset-container"
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
                                            <FormField errorText={assetDownloadError}></FormField>
                                        </Container>
                                    </div>
                                )}
                                <div id="view-edit-asset-right-column" className={viewerMode}>
                                    <SpaceBetween direction="vertical" size="m">
                                        <Container
                                            header={
                                                <Grid
                                                    gridDefinition={[
                                                        { colspan: 3 },
                                                        { colspan: 9 },
                                                    ]}
                                                >
                                                    <Box margin={{ bottom: "m" }}>
                                                        <Header variant="h2">Visualizer</Header>
                                                    </Box>
                                                    <SegmentedControl
                                                        label="Visualizer Control"
                                                        options={viewerOptions}
                                                        selectedId={viewType}
                                                        onChange={changeViewType}
                                                        className="visualizer-segment-control"
                                                    />
                                                </Grid>
                                            }
                                        >
                                            <Suspense
                                                fallback={
                                                    <div className="visualizer-container">
                                                        <div className="visualizer-container-spinner">
                                                            <Spinner />
                                                        </div>
                                                    </div>
                                                }
                                            >
                                                <div className="visualizer-container">
                                                    <div className="visualizer-container-canvases">
                                                        {viewType === "preview" &&
                                                            asset?.previewLocation?.Key && (
                                                                <ImgViewer
                                                                    assetKey={
                                                                        asset?.generated_artifacts
                                                                            ?.preview?.Key ||
                                                                        asset.previewLocation.Key
                                                                    }
                                                                    altAssetKey={
                                                                        asset.previewLocation.Key
                                                                    }
                                                                />
                                                            )}
                                                        {viewType === "folder" &&
                                                            asset.assetId &&
                                                            asset.databaseId && (
                                                                <FolderViewer
                                                                    assetId={asset?.assetId}
                                                                    databaseId={asset?.databaseId}
                                                                    assetName={asset?.assetName}
                                                                    isDistributable={
                                                                        asset?.isDistributable
                                                                    }
                                                                />
                                                            )}
                                                    </div>

                                                    <div className="visualizer-footer">
                                                        <a
                                                            title="View Collapsed"
                                                            onClick={() =>
                                                                changeViewerMode("collapse")
                                                            }
                                                            className={
                                                                viewerMode === "collapse"
                                                                    ? "selected"
                                                                    : ""
                                                            }
                                                        >
                                                            <svg
                                                                xmlns="http://www.w3.org/2000/svg"
                                                                height="24px"
                                                                viewBox="0 0 24 24"
                                                                width="24px"
                                                                fill="#000000"
                                                            >
                                                                <path
                                                                    d="M0 0h24v24H0V0z"
                                                                    fill="none"
                                                                />
                                                                <path d="M19 11h-8v6h8v-6zm-2 4h-4v-2h4v2zm4-12H3c-1.1 0-2 .88-2 1.98V19c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V4.98C23 3.88 22.1 3 21 3zm0 16.02H3V4.97h18v14.05z" />
                                                            </svg>
                                                        </a>
                                                        <a
                                                            title="View Wide"
                                                            onClick={() => changeViewerMode("wide")}
                                                            className={
                                                                viewerMode === "wide"
                                                                    ? "selected"
                                                                    : ""
                                                            }
                                                        >
                                                            <svg
                                                                xmlns="http://www.w3.org/2000/svg"
                                                                enableBackground="new 0 0 24 24"
                                                                height="24px"
                                                                viewBox="0 0 24 24"
                                                                width="24px"
                                                                fill="#000000"
                                                            >
                                                                <g>
                                                                    <rect
                                                                        fill="none"
                                                                        height="24"
                                                                        width="24"
                                                                    />
                                                                </g>
                                                                <g>
                                                                    <g>
                                                                        <path d="M2,4v16h20V4H2z M20,18H4V6h16V18z" />
                                                                    </g>
                                                                </g>
                                                            </svg>
                                                        </a>
                                                        <a
                                                            title="View Fullscreen"
                                                            onClick={() =>
                                                                changeViewerMode("fullscreen")
                                                            }
                                                            className={
                                                                viewerMode === "fullscreen"
                                                                    ? "selected"
                                                                    : ""
                                                            }
                                                        >
                                                            <svg
                                                                xmlns="http://www.w3.org/2000/svg"
                                                                height="24px"
                                                                viewBox="0 0 24 24"
                                                                width="24px"
                                                                fill="#000000"
                                                            >
                                                                <path
                                                                    d="M0 0h24v24H0V0z"
                                                                    fill="none"
                                                                />
                                                                <path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z" />
                                                            </svg>
                                                        </a>
                                                    </div>
                                                </div>
                                            </Suspense>
                                        </Container>
                                    </SpaceBetween>
                                </div>
                            </Grid>
                            <RelatedTableList
                                allItems={allItems}
                                loading={loading}
                                listDefinition={WorkflowExecutionListDefinition}
                                databaseId={databaseId}
                                setReload={setReload}
                                parentId={"workflowId"}
                                HeaderControls={WorkflowHeaderControls}
                            />
                            <ErrorBoundary
                                fallback={
                                    <div>
                                        Metadata failed to load due to an error. Contact your VAMS
                                        administrator for help.
                                    </div>
                                }
                            >
                                <ControlledMetadata databaseId={databaseId} assetId={assetId} />
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
                                window.location.reload(true);
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
            {pathViewType && <AssetSelectorWithModal pathViewType={pathViewType} />}
        </>
    );
}
