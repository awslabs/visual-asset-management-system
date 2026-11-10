/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router";
import Box from "@cloudscape-design/components/box";
import BreadcrumbGroup from "@cloudscape-design/components/breadcrumb-group";
import { SearchTabsHost, searchPageTitle } from "../../searchPlugin";
import Synonyms from "../../synonyms";
import { usePageTitle } from "../../hooks/usePageTitle";
import { useAllowedRoutes } from "../../features/orchestration/permissions/useAllowedRoutes";
import { routeDisabledReason } from "../../features/orchestration/permissions/routeDisabledReason";

interface NewSearchPageProps {}

/**
 * Breadcrumbs, then the search provider host: the title row with its view switch and the active
 * view's body. Which views appear is the providers' decision.
 */
const NewSearchPage: React.FC<NewSearchPageProps> = () => {
    const { databaseId } = useParams();
    usePageTitle(databaseId || null, `${Synonyms.Asset} and File Search`);
    const { loading: permissionsLoading, failed: permissionsFailed, can } = useAllowedRoutes();
    const assetActionDisabledReasons = {
        create: routeDisabledReason(
            permissionsLoading,
            permissionsFailed,
            can("POST", "/assets"),
            `Your role does not allow creating ${Synonyms.assets}.`
        ),
        delete: routeDisabledReason(
            permissionsLoading,
            permissionsFailed,
            can("DELETE", "/database/{databaseId}/assets/{assetId}/archiveAsset"),
            `Your role does not allow archiving or deleting ${Synonyms.assets}.`
        ),
        // The route the asset page gates its Unarchive on. The shipped templates grant it through
        // route patterns the listing reports (database-admin) or not at all (database-user denies
        // it, database-readonly has no PUT), so no role loses an Unarchive it may perform.
        unarchive: routeDisabledReason(
            permissionsLoading,
            permissionsFailed,
            can("PUT", "/database/{databaseId}/assets/{assetId}/unarchiveAsset"),
            `Your role does not allow unarchiving ${Synonyms.assets}.`
        ),
    };

    return (
        <Box padding={{ top: "s", horizontal: "l" }}>
            {/* Where the page sits, ending with the page itself (its h1 below), with no step that
                links back to this page: the all-databases page stands alone, and a database's page
                sits under Databases. */}
            <BreadcrumbGroup
                items={[
                    ...(databaseId ? [{ text: Synonyms.Databases, href: "#/databases/" }] : []),
                    { text: searchPageTitle(databaseId), href: "" },
                ]}
                ariaLabel="Breadcrumbs"
            />

            <SearchTabsHost
                databaseId={databaseId}
                assetActionDisabledReasons={assetActionDisabledReasons}
            />
        </Box>
    );
};

export default NewSearchPage;
