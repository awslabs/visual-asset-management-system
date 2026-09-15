/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useNavigate } from "react-router-dom";
import ListPage from "../../../pages/ListPage";
import { AssetListDefinition } from "../../../components/list/list-definitions/AssetListDefinition";
import { fetchAllAssets, fetchDatabaseAssets } from "../../../services/APIService";
import Synonyms from "../../../synonyms";
import type { SearchProviderProps } from "../../core/types";

/**
 * The asset list: every accessible asset from the asset API, paged and filtered client-side. Needs
 * no search engine, so it is offered on every deployment. ListPage reads the database from the route.
 */
const AssetListProviderComponent: React.FC<SearchProviderProps> = ({ databaseId }) => {
    const navigate = useNavigate();

    const handleCreateAsset = () => {
        if (databaseId) {
            navigate(`/upload/${databaseId}`);
        } else {
            navigate("/upload");
        }
    };

    return (
        <ListPage
            singularName={Synonyms.Asset}
            singularNameTitleCase={Synonyms.Asset}
            pluralName={Synonyms.assets}
            pluralNameTitleCase={Synonyms.Assets}
            onCreateCallback={handleCreateAsset}
            listDefinition={AssetListDefinition}
            fetchAllElements={fetchAllAssets}
            fetchElements={fetchDatabaseAssets}
            hideDeleteButton={true}
        />
    );
};

export default AssetListProviderComponent;
