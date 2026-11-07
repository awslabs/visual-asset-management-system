/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router";
import Box from "@cloudscape-design/components/box";
import BreadcrumbGroup from "@cloudscape-design/components/breadcrumb-group";
import { SearchTabsHost } from "../../searchPlugin";
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
    };

    return (
        <Box padding={{ top: "s", horizontal: "l" }}>
            <BreadcrumbGroup
                items={[
                    { text: Synonyms.Databases, href: "#/databases/" },
                    { text: "Search", href: "#/assets/" },
                    ...(databaseId
                        ? [
                              {
                                  text: databaseId,
                                  href: `#/databases/${databaseId}/assets/`,
                              },
                          ]
                        : []),
                    { text: Synonyms.Assets, href: "" },
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
