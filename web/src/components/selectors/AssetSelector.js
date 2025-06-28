/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useContext, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { fetchAllAssets, fetchDatabaseAssets } from "../../services/APIService";
import { Select } from "@cloudscape-design/components";
import { WorkflowContext } from "../../context/WorkflowContex";
/**
 * No viewer yet for cad and archive file formats
 */
import {
    columnarFileFormats,
    onlineViewer3DFileFormats,
    modelFileFormats,
    imageFileFormats,
    pcFileFormats,
} from "../../common/constants/fileFormats";
import Synonyms from "../../synonyms";

const AssetSelector = (props) => {
    const { database, pathViewType } = props;
    const [reload, setReload] = useState(true);
    const [allItems, setAllItems] = useState([]);
    const navigate = useNavigate();
    let asset, setAsset, setActiveTab, setAssetDatabaseId;
    const context = useContext(WorkflowContext);
    if (!pathViewType) {
        asset = context.asset;
        setAsset = context.setAsset;
        setActiveTab = context.setActiveTab;
        setAssetDatabaseId = context.setAssetDatabaseId;
    }

    useEffect(() => {
        const getData = async () => {
            let items;
            items =
                database && database.toUpperCase() !== "GLOBAL"
                    ? await fetchDatabaseAssets({ databaseId: database })
                    : await fetchAllAssets();
            if (items !== false && Array.isArray(items)) {
                setReload(false);
                if (pathViewType) {
                    items = items.filter((item) => {
                        if (item.databaseId.indexOf("#deleted") !== -1) {
                            return false;
                        }
                        if (pathViewType === "column" || pathViewType === "plot") {
                            return columnarFileFormats.includes(item.assetType);
                        } else if (pathViewType === "3d" || pathViewType === "model") {
                            return onlineViewer3DFileFormats.includes(item.assetType);
                        } else if (pathViewType === "pc") {
                            return pcFileFormats.includes(item.assetType);
                        } else if (pathViewType === "image") {
                            return imageFileFormats.includes(item.assetType);
                        }
                        return false;
                    });
                }
                setAllItems(items);
            }
        };
        if (reload) {
            getData();
        }
    }, [database, pathViewType, reload]);

    return (
        <Select
            selectedOption={asset || null}
            onChange={({ detail }) => {
                if (pathViewType) {
                    const assetFileName = detail.selectedOption.label;
                    const assetId = detail.selectedOption.value;
                    const databaseId = allItems.find(
                        (item) => item.assetId === assetId
                    )?.databaseId;
                    navigate(`/databases/${databaseId}/assets/${assetId}/file`);
                } else {
                    const assetId = detail.selectedOption.value;
                    const assetDatabaseId = allItems.find(
                        (item) => item.assetId === assetId
                    )?.databaseId;
                    console.log("assetDatabaseId", assetDatabaseId);
                    setAsset(detail.selectedOption);
                    setAssetDatabaseId(assetDatabaseId);
                    setActiveTab("asset");
                }
            }}
            placeholder={
                <>
                    Select starting {Synonyms.asset} from {database} database.
                </>
            }
            options={allItems.map((item) => {
                return {
                    label: `${item.assetName} (${item.assetType})`,
                    value: item.assetId,
                };
            })}
            filteringType="auto"
            selectedAriaLabel="Selected"
        />
    );
};

export default AssetSelector;
