/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";

import {
    Cards,
    TextFilter,
    Grid,
    Box,
    Button,
    Table,
    Select,
    Modal,
    Icon,
    TextContent,
} from "@cloudscape-design/components";

// @ts-ignore - legacy mock utility reference retained for future re-wire
import { highlightMatches } from "../../common/utils/utils";

import Synonyms from "../../synonyms";

const API: any = {};
const Storage: any = {};

export default function AssetGrid(props: any) {
    const [assetData, setAssetData] = useState<any[]>([]);

    const [loaded, setLoaded] = useState(false);
    const [filteredList, setFilteredList] = useState<any[]>([]);
    const { reload, setReload, database } = props;
    const [filterBy, setFilterBy] = useState("");
    const [assetId, setAssetId] = useState<any>(null);
    const [pipelineId, setPipelineId] = useState<any>(null);
    const [distributable, setDistributable] = useState<any>(null);

    useEffect(() => {
        const getData = async () => {
            setAssetData([]);
            setFilteredList([]);
            setLoaded(false);
            // const config = {
            //     body: { databaseId: database },
            //     headers: {},
            // };
            // const response = await API.post(
            //     "api",
            //     "/Assets/listAssets",
            //     config
            // );
            /** Mock **/
            const response = {
                message: [
                    {
                        specifiedPipelines: 1,
                        description:
                            "Lorem Ipsum is simply dummy text of the printing and typesetting industry. ",
                        assetId: "Asset 1 v1",
                        distributable: "Yes",
                        currentVersion: 1,
                        assetType: "glb",
                    },
                    {
                        specifiedPipelines: 1,
                        description:
                            "Lorem Ipsum is simply dummy text of the printing and typesetting industry. ",
                        assetId: "Asset 2 v1",
                        distributable: "Yes",
                        currentVersion: 1,
                        assetType: "glb",
                    },
                    {
                        specifiedPipelines: 1,
                        description:
                            "Lorem Ipsum is simply dummy text of the printing and typesetting industry. ",
                        assetId: "Asset 3 v1",
                        distributable: "Yes",
                        currentVersion: 1,
                        assetType: "glb",
                    },
                ],
            };
            setAssetData(response.message);
            setFilteredList(response?.message);
            setAssetId(null);
            setPipelineId(null);
            setDistributable(null);
            setFilterBy("");
        };
        getData();
        setReload(false);
    }, [reload, setReload]);

    useEffect(() => {
        if (!loaded) {
            setFilteredList([]);
            let newList: any[] = [];
            newList = assetData
                .slice()
                .filter((asset: any) => {
                    if (
                        filterBy === "" &&
                        pipelineId === null &&
                        assetId === null &&
                        distributable === null
                    )
                        return true;

                    if (pipelineId !== null) {
                        if (asset.pipelineId !== pipelineId.value) return false;
                    }

                    if (assetId !== null) {
                        if (asset.assetId !== assetId.value) return false;
                    }

                    if (distributable !== null) {
                        if (asset.isDistributable !== distributable.value) return false;
                    }

                    if (filterBy !== "") {
                        const filterList = filterBy.split(" ");
                        for (let i = 0; i < filterList.length; i++) {
                            const pipelines = asset?.specifiedPipelines.map((pipeline: any) =>
                                pipeline?.name?.toLowerCase()
                            );
                            for (let j = 0; j < pipelines.length; j++) {
                                if (pipelines[j]?.indexOf(filterList[i]) !== -1) {
                                    return true;
                                }
                            }
                            if (asset?.description?.toLowerCase().indexOf(filterList[i]) !== -1) {
                                return true;
                            }
                            if (asset?.assetId?.toLowerCase().indexOf(filterList[i]) !== -1) {
                                return true;
                            }
                            if (asset?.assetType?.toLowerCase().indexOf(filterList[i]) !== -1) {
                                return true;
                            }
                        }
                        return false;
                    }

                    return true;
                })
                .map((asset: any) => {
                    return {
                        specifiedPipelines: asset.specifiedPipelines,
                        description: highlightMatches(asset.description, filterBy),
                        assetId: highlightMatches(asset.assetId, filterBy),
                        distributable: asset.isDistributable,
                        currentVersion: asset.currentVersion,
                        assetType: highlightMatches(asset.assetType, filterBy),
                    };
                });
            setFilteredList(newList);
            setLoaded(true);
        }
    }, [assetData, assetId, distributable, filterBy, loaded, pipelineId]);

    const handleFindResources = (filterBy: any) => {
        setFilterBy(filterBy.toLowerCase());
        setLoaded(false);
    };

    const handleSelectPiplineById = (id: any) => {
        if (id.value === null) id = null;
        setPipelineId(id);
        setLoaded(false);
    };

    const handleSelectAssetById = (asset: any) => {
        if (asset.value === null) asset = null;
        setAssetId(asset);
        setLoaded(false);
    };

    const handleSelectByDistributable = (dist: any) => {
        if (dist.value === null) dist = null;
        setDistributable(dist);
        setLoaded(false);
    };

    const [pipelines, setPipelines] = useState<any[]>([]);

    useEffect(() => {
        const getPipelines = async () => {
            const config = {
                body: { databaseId: database }, // replace this with attributes you need
                headers: {}, // OPTIONAL
            };
            const response = await API.post("api", "/Pipelines/list", config);

            setPipelines(
                response?.message.map((pipeline: any) => ({
                    label: pipeline.pipelineId,
                    value: pipeline.pipelineId,
                    description: pipeline.description,
                }))
            );
        };
        if (pipelines.length === 0) {
            getPipelines();
        }
    }, [database, pipelines]);

    /**
     * Preview control section
     */
    const [openVersions, setOpenVersions] = useState(false);
    const [currentAsset, setCurrentAsset] = useState<any>(null);

    const handleOpenVersions = (asset: any) => {
        setCurrentAsset(asset);
        setOpenVersions(true);
    };

    const handleCloseVersions = () => {
        setCurrentAsset(null);
        setOpenVersions(false);
    };

    const handleRevertVersion = async (version: any) => {
        setLoaded(false);

        // API Call
        const apiConf = {
            body: Object.assign(currentAsset, {
                databaseId: database,
                assetId: currentAsset.assetId,
                key: currentAsset?.assetLocation?.Key,
                version: version,
            }),
        };

        // Upload Configuration
        await API.post("api", "/Assets/revertAsset", apiConf);

        setReload(true);
    };

    return (
        <>
            <Cards
                trackBy="assetId"
                cardDefinition={{
                    header: (e: any) => {
                        return <>{e.assetId}</>;
                    },
                    sections: [
                        {
                            id: "description",
                            content: (e: any) => e.description,
                        },
                        {
                            id: "assetType",
                            header: (
                                <Grid
                                    gridDefinition={[
                                        { colspan: { l: 4, m: 4, default: 4 } },
                                        { colspan: { l: 4, m: 4, default: 4 } },
                                        { colspan: { l: 4, m: 4, default: 4 } },
                                    ]}
                                >
                                    <div style={{ color: "var(--vams-text-secondary)" }}>
                                        {Synonyms.Asset} Type
                                    </div>
                                    <div style={{ color: "var(--vams-text-secondary)" }}>
                                        Distributable
                                    </div>
                                    <div style={{ color: "var(--vams-text-secondary)" }}>
                                        Version
                                    </div>
                                </Grid>
                            ),
                            content: (e: any) => {
                                return (
                                    <Grid
                                        gridDefinition={[
                                            { colspan: { l: 4, m: 4, default: 4 } },
                                            { colspan: { l: 4, m: 4, default: 4 } },
                                            { colspan: { l: 4, m: 4, default: 4 } },
                                        ]}
                                    >
                                        <div>{e.assetType}</div>
                                        <div>{e.isDistributable ? "Yes" : "No"}</div>
                                        <div>{e.currentVersion?.Version}</div>
                                    </Grid>
                                );
                            },
                        },
                        {
                            id: "pipeline",
                            header: "Pipeline",
                            content: (e: any) => {
                                return (
                                    <>
                                        {e.specifiedPipelines && e.specifiedPipelines.length > 0
                                            ? e.specifiedPipelines.map(
                                                  (pipeline: any, i: number) => {
                                                      return <div key={i}>{pipeline.name}</div>;
                                                  }
                                              )
                                            : "none"}
                                    </>
                                );
                            },
                        },
                        {
                            id: "links",
                            content: (e: any) => {
                                const asset = e;
                                const location = e?.assetLocation;
                                const key = location?.Key;
                                let signedURL: any;
                                (async () => {
                                    signedURL = await Storage.get(key);
                                })();
                                return (
                                    <Grid
                                        gridDefinition={[
                                            { colspan: { l: 4, m: 4, default: 4 } },
                                            { colspan: { l: 4, m: 4, default: 4 } },
                                            { colspan: { l: 4, m: 4, default: 4 } },
                                        ]}
                                    >
                                        <TextContent>
                                            <a
                                                href="#"
                                                onClick={(e) => {
                                                    e.preventDefault();
                                                    //handleOpenPreview(preview, location);
                                                }}
                                            >
                                                Preview
                                            </a>
                                        </TextContent>
                                        <TextContent>
                                            <a
                                                href="#"
                                                onClick={(e) => {
                                                    e.preventDefault();
                                                    window.location.href = signedURL;
                                                }}
                                            >
                                                Download
                                            </a>
                                        </TextContent>
                                        <TextContent>
                                            {e?.versions?.length > 0 && (
                                                <a
                                                    href="#"
                                                    onClick={(e) => {
                                                        e.preventDefault();
                                                        handleOpenVersions(asset);
                                                    }}
                                                >
                                                    Versions
                                                </a>
                                            )}
                                            {e?.versions?.length === 0 && (
                                                <span>No Other Versions</span>
                                            )}
                                        </TextContent>
                                    </Grid>
                                );
                            },
                        },
                    ],
                }}
                cardsPerRow={[{ cards: 3 }, { minWidth: 33, cards: 3 }]}
                items={[...new Set(filteredList)]}
                loadingText="Loading resources"
                // visibleSections={["description", "type", "size"]}
                empty={
                    <Box textAlign="center" color="inherit">
                        <b>No assets</b>
                        <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                            No Assets to display.
                        </Box>
                        <Button>Create Asset</Button>
                    </Box>
                }
                filter={
                    <Grid
                        gridDefinition={[
                            { colspan: { l: 6, m: 6, default: 6 } },
                            { colspan: { l: 2, m: 2, default: 2 } },
                            { colspan: { l: 2, m: 2, default: 2 } },
                            { colspan: { l: 2, m: 2, default: 2 } },
                        ]}
                    >
                        <TextFilter
                            filteringText={filterBy}
                            filteringPlaceholder={`Find ${Synonyms.assets}`}
                            filteringAriaLabel={`Filter ${Synonyms.assets}`}
                            onChange={({ detail }) => handleFindResources(detail.filteringText)}
                            countText={filterBy === "" ? "" : filteredList.length + " matches"}
                            {...({ style: { minWidth: "100%" } } as any)}
                        />
                        {assetData && (
                            <>
                                <Select
                                    selectedOption={assetId}
                                    onChange={({ detail }) =>
                                        handleSelectAssetById(detail.selectedOption)
                                    }
                                    options={
                                        ([{ label: <em>all</em>, value: null }] as any).concat(
                                            assetData.map((row: any) => {
                                                return { label: row.assetId, value: row.assetId };
                                            })
                                        ) as any
                                    }
                                    placeholder={`${Synonyms.Asset} Name`}
                                    selectedAriaLabel="Selected"
                                />
                                <Select
                                    selectedOption={pipelineId}
                                    onChange={({ detail }) =>
                                        handleSelectPiplineById(detail.selectedOption)
                                    }
                                    options={
                                        (
                                            [
                                                {
                                                    label: <em>all</em>,
                                                    value: null,
                                                },
                                            ] as any
                                        ).concat(
                                            [
                                                ...new Set(
                                                    [
                                                        ...assetData.map(
                                                            (asset: any) => asset.specifiedPipelines
                                                        ),
                                                    ].map((row: any) => row.name)
                                                ),
                                            ].map((pipeline: any) => {
                                                return { label: pipeline, value: pipeline };
                                            })
                                        ) as any
                                    }
                                    placeholder={`Pipeline`}
                                    selectedAriaLabel="Selected"
                                />
                                <Select
                                    selectedOption={distributable}
                                    onChange={({ detail }) =>
                                        handleSelectByDistributable(detail.selectedOption)
                                    }
                                    options={
                                        [
                                            { label: (<em>all</em>) as any, value: null as any },
                                            { label: "Yes", value: true as any },
                                            {
                                                label: "No",
                                                value: false as any,
                                            },
                                        ] as any
                                    }
                                    placeholder={`Distributable`}
                                    selectedAriaLabel="Selected"
                                />
                            </>
                        )}
                    </Grid>
                }
                header={
                    <Grid
                        gridDefinition={[
                            { colspan: { l: 6, m: 6, default: 6 } },
                            { colspan: { l: 6, m: 6, default: 6 } },
                        ]}
                    >
                        <div>
                            <TextContent>
                                <h2>
                                    {Synonyms.Assets} ({assetData.length})
                                </h2>
                            </TextContent>
                        </div>
                        <div style={{ textAlign: "right" }}>
                            <Button variant="primary">
                                <Icon name={"add-plus"} /> &nbsp;&nbsp;New {Synonyms.Asset}
                            </Button>
                        </div>
                    </Grid>
                }
            />
            {currentAsset && (
                <Modal
                    onDismiss={() => handleCloseVersions()}
                    visible={openVersions}
                    closeAriaLabel="Close versions"
                    size="medium"
                    header={currentAsset.assetId}
                >
                    {currentAsset && (
                        <Table
                            trackBy="version"
                            columnDefinitions={[
                                {
                                    id: "version",
                                    header: "Version",
                                    cell: (e: any) => e.Version,
                                    sortingField: "Version",
                                },
                                {
                                    id: "description",
                                    header: "Description",
                                    cell: (e: any) => e.description,
                                    sortingField: "description",
                                },
                                {
                                    id: "DateModified",
                                    header: "Date",
                                    cell: (e: any) =>
                                        e.DateModified.toLocaleString("en-US", { timeZone: "UTC" }),
                                    sortingField: "DateModified",
                                },
                                {
                                    id: "S3Version",
                                    header: "Action",
                                    cell: ((e: any, i: number) => (
                                        <Button
                                            variant="normal"
                                            key={i}
                                            onClick={() => handleRevertVersion(e.Version)}
                                        >
                                            Revert
                                        </Button>
                                    )) as any,
                                },
                            ]}
                            items={currentAsset.versions}
                            loadingText="Loading versions"
                            header={
                                <TextContent>
                                    <h2>Versions ({currentAsset.versions.length})</h2>
                                </TextContent>
                            }
                        />
                    )}
                </Modal>
            )}
        </>
    );
}
