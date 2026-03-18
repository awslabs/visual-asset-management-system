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

export default function Databases() {
    const [metadataModalDatabaseId, setMetadataModalDatabaseId] = useState(null);
    const [showMapThumbnails, setShowMapThumbnails] = useState(false);

    const config = appCache.getItem("config");
    const useMapView = config?.featuresEnabled?.includes(featuresEnabled.LOCATIONSERVICES);
    const mapStyleUrl = config?.locationServiceApiUrl;

    const listDefinition = useMemo(
        () =>
            createDatabaseListDefinition({
                onMetadataClick: (databaseId) => setMetadataModalDatabaseId(databaseId),
                showMapThumbnails: showMapThumbnails && useMapView,
                MapThumbnailComponent: DatabaseMapThumbnail,
                mapStyleUrl,
            }),
        [showMapThumbnails, useMapView, mapStyleUrl]
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
                editEnabled={true}
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
