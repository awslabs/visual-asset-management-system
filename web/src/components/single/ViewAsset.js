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
  SegmentedControl,
  SpaceBetween,
} from "@cloudscape-design/components";

import Metadata from "./Metadata";
import ImgViewer from "../viewers/ImgViewer";
import React, { useEffect, useState } from "react";
import { useParams } from "react-router";
import ThreeDimensionalPlotter from "../viewers/ThreeDimensionalPlotter";
import ColumnarViewer from "../viewers/ColumnarViewer";
import HTMLViewer from "../viewers/HTMLViewer";
import ModelViewer from "../viewers/ModelViewer";
import {
  fetchAsset,
  fetchWorkflowExecutions,
  fetchDatabaseWorkflows,
  downloadAsset,
} from "../../services/APIService";
/**
 * No viewer yet for cad and archive file formats
 */
import {
  columnarFileFormats,
  modelFileFormats,
  presentationFileFormats,
} from "../../common/constants/fileFormats";
import AssetSelectorWithModal from "../selectors/AssetSelectorWithModal";
import RelatedTableList from "../list/RelatedTableList";
import { WorkflowExecutionListDefinition } from "../list/list-definitions/WorkflowExecutionListDefinition";
import CreateUpdateAsset from "../createupdate/CreateUpdateAsset";
import { actionTypes } from "../createupdate/form-definitions/types/FormDefinition";
import WorkflowSelectorWithModal from "../selectors/WorkflowSelectorWithModal";

const checkFileFormat = (asset) => {
  let filetype;
  if(asset?.generated_artifacts?.gltf?.Key) {
    filetype = asset?.generated_artifacts?.gltf?.Key.split(".").pop();
  } else {
    filetype = asset.assetType;
  }

  filetype = filetype.toLowerCase();
  if (
    modelFileFormats.includes(filetype) ||
    modelFileFormats.includes("." + filetype)
  ) {
    return "model";
  }
  if (
    columnarFileFormats.includes(filetype) ||
    columnarFileFormats.includes("." + filetype)
  ) {
    return "plot";
  }
  if (
      presentationFileFormats.includes(filetype) ||
      presentationFileFormats.includes("." + filetype)
  ) {
    return "html";
  }
  return "preview";
};

export default function ViewAsset() {
  const { databaseId, assetId, pathViewType } = useParams();

  const [reload, setReload] = useState(true);
  const [viewType, setViewType] = useState(null);
  const [asset, setAsset] = useState({});

  const [viewerOptions, setViewerOptions] = useState([]);
  const [viewerMode, setViewerMode] = useState("collapse");
  const [downloadUrl, setDownloadUrl] = useState(null);
  const [openUpdateAsset, setOpenUpdateAsset] = useState(false);

  //worflow
  const [loading, setLoading] = useState(true);
  const [allItems, setAllItems] = useState([]);
  const [workflowOpen, setWorkflowOpen] = useState(false);

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
      const items = await fetchDatabaseWorkflows({databaseId: databaseId});
      console.log("items:", items);
      if (items !== false && Array.isArray(items)) {
        const newRows = [];
        for (let i = 0; i < items.length; i++) {
          const newParentRow = Object.assign({}, items[i]);
          newParentRow.name = newParentRow?.workflowId;
          newRows.push(newParentRow);
          const workflowId = newParentRow?.workflowId;
          const subItems = await fetchWorkflowExecutions({ databaseId: databaseId, assetId: assetId, workflowId: workflowId });
          console.log("subItems:", subItems);
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
        console.log("newRows", newRows);
        setLoading(false);
        setReload(false);
      }
    };
    if (reload) {
      getData();
    }
  }, [reload]);

  const changeViewerMode = (mode) => {
    if (mode === "fullscreen" && viewerMode === "fullscreen") {
      mode = "collapse";
    }

    setViewerMode(mode);
  };

  const fullscreenChangeHandler = (event) => {
    if (!document.fullscreenElement) {
      if (viewerMode === "fullscreen") {
        setViewerMode("collapse");
      }
    }
  };

  useEffect(() => {
    if (assetId) {
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
    }
    
  }, [assetId, viewerMode]);

  const changeViewType = (event) => {
    setViewType(event.detail.selectedId);
  };

  const handleOpenUpdateAsset = (mode) => {
    setOpenUpdateAsset(mode);
  };

  const handleAssetDownload = async (event) => {
    event.preventDefault();
    setDownloadUrl(null);
    setAssetDownloadError("");
    // note: the "version" parameter is not used by the frontend, but available via API
    const config = {
      body: {
        databaseId: databaseId,
        assetId: assetId,
      },
    };
    const result = await downloadAsset({databaseId: databaseId, assetId: assetId, config: config});
    if (result !== false && Array.isArray(result)) {
      if (result[0] === false) {
        setAssetDownloadError(`Unable to download asset. ${result[1]}`);
      } else {
        setAssetDownloadError("");
        setDownloadUrl(result[1]);
      }
    }
  };

  useEffect(() => {
    const getData = async () => {
      if (databaseId && assetId) {
        const item = await fetchAsset({databaseId: databaseId, assetId: assetId});
        if (item !== false) {
          console.log(item);
          setAsset(item);

          const defaultViewType = checkFileFormat(item);
          console.log("default view type", defaultViewType);
          const newViewerOptions = [{ text: "Preview", id: "preview" }];
          if (defaultViewType === "plot") {
            newViewerOptions.push({ text: "Plot", id: "plot" });
            newViewerOptions.push({ text: "Column", id: "column" });
          } else if (defaultViewType === "model") {
            newViewerOptions.push({ text: "Model", id: "model" });
          } else if (defaultViewType === "html") {
            newViewerOptions.push({ text: "HTML", id: "html" });
          }
          setViewerOptions(newViewerOptions);
          if (!window.location.hash) setViewType(defaultViewType);
          else {
            if (window.location.hash === "#preview") {
              setViewType("preview");
            }
            if (window.location.hash === "#model") {
              setViewType("model");
            } else if (window.location.hash === "#plot") {
              setViewType("plot");
            } else if (window.location.hash === "#column") {
              setViewType("column");
            } else if (window.location.hash === "#html") {
              setViewType("html");
            }
          }
        }
      }  
    };
    if (reload && !pathViewType) {
      getData();
    }
  }, [reload, assetId]);

  return (
    <>
      {assetId && <>
        <Box padding={{ top: "s", horizontal: "l" }}>
          <SpaceBetween direction="vertical" size="xs">
            <BreadcrumbGroup
              items={[
                { text: "Databases", href: "/databases/" },
                {
                  text: databaseId,
                  href: "/databases/" + databaseId + "/assets/",
                },
                { text: asset?.assetName },
              ]}
              ariaLabel="Breadcrumbs"
            />
            <Grid gridDefinition={[{ colspan: 4 }, { colspan: 8 }]}>
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
                    className="view-edit-asset-container"
                    header={
                      <div className="view-edit-asset-header">
                        <div className="asset-edit-button">
                          <Button onClick={() => handleOpenUpdateAsset(true)}>
                            Edit
                          </Button>
                        </div>
                        <Header variant="h2">Asset Details</Header>
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
                    {!downloadUrl && (
                      <div style={{ marginTop: "20px" }}>
                        <Button variant="primary" onClick={handleAssetDownload} disabled={asset?.isDistributable !== true}>
                          Generate Download Link
                        </Button>
                      </div>
                    )}
                    {downloadUrl && (
                      <div style={{ marginTop: "20px" }}>
                        <SpaceBetween direction="horizontal" size="xs">
                          <>
                            <Button
                              iconName="copy"
                              variant="loki"
                              onClick={() => {
                                navigator.clipboard.writeText(downloadUrl);
                              }}
                            >
                              Copy Link
                            </Button>
                            <Button
                              variant={"primary"}
                              href={downloadUrl}
                              external
                            >
                              Download
                            </Button>
                          </>
                        </SpaceBetween>
                      </div>
                    )}
                    <FormField errorText={assetDownloadError}></FormField>
                  </Container>
                  
                </div>
              )}
              <div id="view-edit-asset-right-column" className={viewerMode}>
                <SpaceBetween direction="vertical" size="m">
                  <Container
                    header={
                      <Grid gridDefinition={[{ colspan: 3 }, { colspan: 9 }]}>
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
                    <div className="visualizer-container">
                      <div className="visualizer-container-canvases">
                        {viewType === "preview" &&
                        asset?.previewLocation?.Key && (
                            <ImgViewer
                              assetKey={asset?.generated_artifacts?.preview?.Key || asset.previewLocation.Key}
                              altAssetKey={asset.previewLocation.Key}
                            />
                          )}
                        {viewType === "model" && (
                          <ModelViewer
                            assetKey={asset?.generated_artifacts?.gltf?.Key || asset?.assetLocation?.Key}
                            className="visualizer-container-canvas"
                          />
                        )}
                        {viewType === "plot" && (
                          <ThreeDimensionalPlotter
                            assetKey={asset?.assetLocation?.Key}
                            className="visualizer-container-canvas"
                          />
                        )}
                        {viewType === "column" && (
                          <ColumnarViewer assetKey={asset?.assetLocation?.Key} />
                        )}
                        {viewType === "html" && (
                            <HTMLViewer assetKey={asset?.assetLocation?.Key} />
                        )}
                      </div>

                      <div className="visualizer-footer">
                        <a
                          title="View Collapsed"
                          onClick={() => changeViewerMode("collapse")}
                          className={viewerMode === "collapse" ? "selected" : ""}
                        >
                          <svg
                            xmlns="http://www.w3.org/2000/svg"
                            height="24px"
                            viewBox="0 0 24 24"
                            width="24px"
                            fill="#000000"
                          >
                            <path d="M0 0h24v24H0V0z" fill="none" />
                            <path d="M19 11h-8v6h8v-6zm-2 4h-4v-2h4v2zm4-12H3c-1.1 0-2 .88-2 1.98V19c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V4.98C23 3.88 22.1 3 21 3zm0 16.02H3V4.97h18v14.05z" />
                          </svg>
                        </a>
                        <a
                          title="View Wide"
                          onClick={() => changeViewerMode("wide")}
                          className={viewerMode === "wide" ? "selected" : ""}
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
                              <rect fill="none" height="24" width="24" />
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
                          onClick={() => changeViewerMode("fullscreen")}
                          className={
                            viewerMode === "fullscreen" ? "selected" : ""
                          }
                        >
                          <svg
                            xmlns="http://www.w3.org/2000/svg"
                            height="24px"
                            viewBox="0 0 24 24"
                            width="24px"
                            fill="#000000"
                          >
                            <path d="M0 0h24v24H0V0z" fill="none" />
                            <path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z" />
                          </svg>
                        </a>
                      </div>
                    </div>
                  </Container>
                </SpaceBetween>
              </div>
            </Grid>
            <div
              style={{
                position: "relative",
                minHeight: "650px",
                width: "100%",
              }}
            >
              <div style={{ width: "100%" }}>
                <RelatedTableList
                  allItems={allItems}
                  loading={loading}
                  listDefinition={WorkflowExecutionListDefinition}
                  databaseId={databaseId}
                  setReload={setReload}
                  parentId={"workflowId"}
                  HeaderControls={WorkflowHeaderControls}
                />
              </div>
              <Metadata
                databaseId={databaseId}
                assetId={assetId}
              />
            </div>
          </SpaceBetween>
        </Box>
        <CreateUpdateAsset
          open={openUpdateAsset}
          setOpen={setOpenUpdateAsset}
          setReload={setReload}
          databaseId={databaseId}
          assetId={assetId}
          actionType={actionTypes.UPDATE}
        />
        <WorkflowSelectorWithModal
          assetId={assetId}
          databaseId={databaseId}
          open={workflowOpen}
          setOpen={setWorkflowOpen}
        />
      </>}
      {pathViewType && <AssetSelectorWithModal pathViewType={pathViewType} />}
    </>
  );
}
