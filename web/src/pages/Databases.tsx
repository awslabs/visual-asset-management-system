/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useMemo } from "react";
import { appCache } from "../services/appCache";
import { fetchAllDatabases } from "../services/APIService";
import CreateDatabase from "../components/createupdate/CreateDatabase";
import { createDatabaseListDefinition } from "../components/list/list-definitions/DatabaseListDefinition";
import ListPage from "./ListPage";
import Synonyms from "../synonyms";
import Modal from "@cloudscape-design/components/modal";
import Header from "@cloudscape-design/components/header";
import Toggle from "@cloudscape-design/components/toggle";
import { MetadataContainer } from "../components/metadataV2/MetadataContainer";
import { featuresEnabled } from "../common/constants/featuresEnabled";
import DatabaseMapThumbnail from "../components/search/SearchResults/DatabaseMapThumbnail";
import { usePageTitle } from "../hooks/usePageTitle";
import { useAllowedRoutes } from "../features/orchestration/permissions/useAllowedRoutes";

// The API route the database compliance page reads; the column linking to it is shown only
// when the caller may call it.
const COMPLIANCE_STATE_DATABASE_API_ROUTE = "/compliance/state/{databaseId}";
// The routes behind the Create, Edit and Delete Selected actions. A caller who may only list
// databases (for example the Compliance Admin template) sees the list without them.
const DATABASE_API_ROUTE = "/database";
const DATABASE_BY_ID_API_ROUTE = "/database/{databaseId}";
// The routes the database's assets page lists from: search, or the database's asset listing when
// the deployment runs without OpenSearch. A caller who may call neither gets the database name
// linked to the compliance page instead, or not linked.
const SEARCH_API_ROUTE = "/search";
const DATABASE_ASSETS_API_ROUTE = "/database/{databaseId}/assets";

export default function Databases() {
    usePageTitle(Synonyms.Databases);
    const [metadataModalDatabaseId, setMetadataModalDatabaseId] = useState(null);
    const [showMapThumbnails, setShowMapThumbnails] = useState(false);

    const config = appCache.getItem("config");
    const useMapView = config?.featuresEnabled?.includes(featuresEnabled.LOCATIONSERVICES);
    const mapStyleUrl = config?.locationServiceApiUrl;
    const { can: canCallRoute } = useAllowedRoutes();
    const showCompliance = canCallRoute("GET", COMPLIANCE_STATE_DATABASE_API_ROUTE);
    const canCreate = canCallRoute("POST", DATABASE_API_ROUTE);
    const canEdit = canCallRoute("PUT", DATABASE_BY_ID_API_ROUTE);
    const canDelete = canCallRoute("DELETE", DATABASE_BY_ID_API_ROUTE);
    const useNoOpenSearch = config?.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH);
    const canOpenAssets = useNoOpenSearch
        ? canCallRoute("GET", DATABASE_ASSETS_API_ROUTE)
        : canCallRoute("POST", SEARCH_API_ROUTE);
    const nameLinkTarget = canOpenAssets ? "assets" : showCompliance ? "compliance" : "none";

    const listDefinition = useMemo(
        () =>
            createDatabaseListDefinition({
                onMetadataClick: (databaseId: any) => setMetadataModalDatabaseId(databaseId),
                showMapThumbnails: showMapThumbnails && useMapView,
                MapThumbnailComponent: DatabaseMapThumbnail,
                mapStyleUrl,
                showCompliance,
                nameLinkTarget,
            }),
        [showMapThumbnails, useMapView, mapStyleUrl, showCompliance, nameLinkTarget]
    );

    const mapThumbnailToggle =
        useMapView && mapStyleUrl ? (
            <Toggle
                onChange={({ detail }) => setShowMapThumbnails(detail.checked)}
                checked={showMapThumbnails}
            >
                Show map thumbnails
            </Toggle>
        ) : null;

    return (
        <>
            <ListPage
                singularName={Synonyms.Database}
                singularNameTitleCase={Synonyms.Database}
                pluralName={Synonyms.databases}
                pluralNameTitleCase={Synonyms.Databases}
                listDefinition={listDefinition}
                CreateNewElement={CreateDatabase || undefined}
                fetchAllElements={fetchAllDatabases}
                fetchElements={fetchAllDatabases}
                createEnabled={canCreate}
                editEnabled={canEdit}
                hideDeleteButton={!canDelete}
                customFilterControls={mapThumbnailToggle}
            />
            <Modal
                visible={metadataModalDatabaseId !== null}
                onDismiss={() => setMetadataModalDatabaseId(null)}
                size="large"
                header={
                    <Header variant="h2">
                        {Synonyms.Database} Metadata: {metadataModalDatabaseId}
                    </Header>
                }
            >
                {metadataModalDatabaseId && (
                    <MetadataContainer
                        entityType="database"
                        entityId={metadataModalDatabaseId}
                        mode="online"
                    />
                )}
            </Modal>
        </>
    );
}
