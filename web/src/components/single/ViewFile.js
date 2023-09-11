/* eslint-disable jsx-a11y/anchor-is-valid */
/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
import {
    Box,
    BreadcrumbGroup,
    Container,
    Grid,
    Header,
    SegmentedControl,
    SpaceBetween,
} from "@cloudscape-design/components";
import { useLocation, useParams } from "react-router";

import ControlledMetadata from "../metadata/ControlledMetadata";
import { fetchAsset } from "../../services/APIService";
/**
 * No viewer yet for cad and archive file formats
 */
import {
    columnarFileFormats,
    modelFileFormats,
    pcFileFormats,
    presentationFileFormats,
} from "../../common/constants/fileFormats";
import AssetVisualizer from "./AssetVisualizer";
import AssetSelectorWithModal from "../selectors/AssetSelectorWithModal";
import { ErrorBoundary } from "react-error-boundary";
import Synonyms from "../../synonyms";

const checkFileFormat = (fileName, isDirectory) => {
    if (isDirectory) {
        return "folder";
    }

    let filetype = fileName.split(".").pop();
    filetype = filetype.toLowerCase();
    if (modelFileFormats.includes(filetype) || modelFileFormats.includes("." + filetype)) {
        return "model";
    }
    if (pcFileFormats.includes(filetype) || pcFileFormats.includes("." + filetype)) {
        return "pc";
    }
    if (columnarFileFormats.includes(filetype) || columnarFileFormats.includes("." + filetype)) {
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

export default function ViewFile() {
    const { state } = useLocation();
    const { filename, key, isDirectory } = state;
    console.log(filename, key);
    const { databaseId, assetId, pathViewType } = useParams();

    const [reload, setReload] = useState(true);
    const [viewType, setViewType] = useState(null);
    const [asset, setAsset] = useState({});

    const [viewerOptions, setViewerOptions] = useState([]);
    const [viewerMode, setViewerMode] = useState("collapse");

    const changeViewerMode = (mode) => {
        if (mode === "fullscreen" && viewerMode === "fullscreen") {
            mode = "collapse";
        }
        setViewerMode(mode);
    };

    useEffect(() => {
        if (assetId && !isDirectory) {
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
    }, [assetId, isDirectory, viewerMode]);

    const changeViewType = (event) => {
        setViewType(event.detail.selectedId);
    };
    useEffect(() => {
        const getData = async () => {
            if (databaseId && assetId) {
                const item = await fetchAsset({ databaseId: databaseId, assetId: assetId });
                if (item !== false) {
                    console.log(item);
                    setAsset(item);

                    const defaultViewType = checkFileFormat(filename, isDirectory);
                    console.log("default view type", defaultViewType);
                    const newViewerOptions = [{ text: "Preview", id: "preview" }];
                    if (defaultViewType === "plot") {
                        newViewerOptions.push({ text: "Plot", id: "plot" });
                        newViewerOptions.push({ text: "Column", id: "column" });
                    } else if (defaultViewType === "model") {
                        newViewerOptions.push({ text: "Model", id: "model" });
                    } else if (defaultViewType === "pc") {
                        newViewerOptions.push({ text: "Point Cloud", id: "pc" });
                    } else if (defaultViewType === "html") {
                        newViewerOptions.push({ text: "HTML", id: "html" });
                    } else if (defaultViewType === "folder") {
                        newViewerOptions.push({ text: "Folder", id: "folder" });
                    }
                    setViewerOptions(newViewerOptions);
                    setViewType(defaultViewType);
                    setReload(false);
                }
            }
        };
        if (reload && !pathViewType) {
            getData();
        }
    }, [reload, assetId, databaseId, pathViewType, asset, filename, isDirectory]);

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
                                    {
                                        text: asset?.assetName,
                                        href: "/databases/" + databaseId + "/assets/" + assetId,
                                    },
                                    { text: "view " + filename },
                                ]}
                                ariaLabel="Breadcrumbs"
                            />
                            <Grid gridDefinition={[{ colspan: 4 }]}>
                                <h1>{asset?.assetName}</h1>
                            </Grid>
                            {!isDirectory && (
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
                                            <AssetVisualizer
                                                viewType={viewType}
                                                asset={asset}
                                                viewerMode={viewerMode}
                                                onViewerModeChange={(newViewerMode) =>
                                                    changeViewerMode(newViewerMode)
                                                }
                                            />
                                        </Container>
                                    </SpaceBetween>
                                </div>
                            )}
                            <ErrorBoundary
                                fallback={
                                    <div>
                                        Metadata failed to load due to an error. Contact your VAMS
                                        administrator for help.
                                    </div>
                                }
                            >
                                <ControlledMetadata
                                    databaseId={databaseId}
                                    assetId={assetId}
                                    prefix={key}
                                />
                            </ErrorBoundary>
                        </SpaceBetween>
                    </Box>
                </>
            )}
            {pathViewType && <AssetSelectorWithModal pathViewType={pathViewType} />}
        </>
    );
}
