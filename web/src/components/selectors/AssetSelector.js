/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useContext, useEffect, useState } from "react";
import { fetchAllAssets, fetchDatabaseAssets } from "../../services/APIService";
import { Select } from "@cloudscape-design/components";
import { WorkflowContext } from "../../context/WorkflowContex";
/**
 * No viewer yet for cad and archive file formats
 */
import {
    columnarFileFormats,
    modelFileFormats,
    pcFileFormats,
} from "../../common/constants/fileFormats";
import Synonyms from "../../synonyms";

const AssetSelector = (props) => {
    const { database, pathViewType } = props;
    const [reload, setReload] = useState(true);
    const [allItems, setAllItems] = useState([]);
    let asset, setAsset, setActiveTab;
    const context = useContext(WorkflowContext);
    if (!pathViewType) {
        asset = context.asset;
        setAsset = context.setAsset;
        setActiveTab = context.setActiveTab;
    }

    useEffect(() => {
        const getData = async () => {
            let items;
            if (database) {
                items = await fetchDatabaseAssets({ databaseId: database });
            } else {
                items = await fetchAllAssets();
            }
            if (items !== false && Array.isArray(items)) {
                setReload(false);
                if (pathViewType) {
                    items = items.filter((item) => {
                        if (item.databaseId.indexOf("#deleted") !== -1) {
                            return false;
                        }
                        if (pathViewType === "column" || pathViewType === "plot") {
                            if (columnarFileFormats.includes(item.assetType)) {
                                return true;
                            }
                            return false;
                        } else if (pathViewType === "3d") {
                            if (modelFileFormats.includes(item.assetType)) {
                                return true;
                            }
                            return false;
                        } else if (pathViewType === "pc") {
                            if (pcFileFormats.includes(item.assetType)) {
                                return true;
                            }
                            return false;
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
                    const assetId = detail.selectedOption.value;
                    const databaseId = allItems.find(
                        (item) => item.assetId === assetId
                    )?.databaseId;
                    window.location = `/databases/${databaseId}/assets/${assetId}#${pathViewType}`;
                } else {
                    setAsset(detail.selectedOption);
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
