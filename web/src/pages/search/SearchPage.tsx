/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useParams } from "react-router";
import { Box, BreadcrumbGroup } from "@cloudscape-design/components";
import { ModernSearchContainer } from "../../components/search";
import Synonyms from "../../synonyms";
import { usePageTitle } from "../../hooks/usePageTitle";
import { useAllowedRoutes } from "../../features/orchestration/permissions/useAllowedRoutes";
import { routeDisabledReason } from "../../features/orchestration/permissions/routeDisabledReason";

interface NewSearchPageProps {}

const NewSearchPage: React.FC<NewSearchPageProps> = () => {
    const { databaseId } = useParams();
    usePageTitle(databaseId || null, `${Synonyms.Asset} and File Search`);
    const { loading: permissionsLoading, can } = useAllowedRoutes();
    const assetActionDisabledReasons = {
        create: routeDisabledReason(
            permissionsLoading,
            can("POST", "/assets"),
            `Your role does not allow creating ${Synonyms.assets}.`
        ),
        delete: routeDisabledReason(
            permissionsLoading,
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

            <ModernSearchContainer
                mode="full"
                databaseId={databaseId}
                allowedViews={["table", "card", "map"]}
                showPreferences={true}
                showBulkActions={true}
                assetActionDisabledReasons={assetActionDisabledReasons}
            />
        </Box>
    );
};

export default NewSearchPage;
