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

interface NewSearchPageProps {}

const NewSearchPage: React.FC<NewSearchPageProps> = () => {
    const { databaseId } = useParams();
    usePageTitle(databaseId || null, `${Synonyms.Asset} and File Search`);

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
            />
        </Box>
    );
};

export default NewSearchPage;
