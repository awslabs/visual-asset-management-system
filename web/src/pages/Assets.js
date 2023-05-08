/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useNavigate } from "react-router-dom";
import { fetchAllAssets, fetchDatabaseAssets } from "../services/APIService";
import { AssetListDefinition } from "../components/list/list-definitions/AssetListDefinition";
import ListPage from "./ListPage";
import { useParams } from "react-router";

export default function Assets() {
    const navigate = useNavigate();

    const urlParams = useParams();
    return (
        <ListPage
            singularName={"asset"}
            singularNameTitleCase={"Asset"}
            pluralName={"assets"}
            pluralNameTitleCase={"Assets"}
            onCreateCallback={() => {
                if (urlParams.databaseId) {
                    navigate(`/upload/${urlParams.databaseId}`);
                } else {
                    navigate("/upload");
                }
            }}
            listDefinition={AssetListDefinition}
            fetchAllElements={fetchAllAssets}
            fetchElements={fetchDatabaseAssets}
        />
    );
}
