/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Grid from "@cloudscape-design/components/grid";
import TextContent from "@cloudscape-design/components/text-content";
import ZipDownloadsTable from "../components/zipDownloads/ZipDownloadsTable";
import {
    DOWNLOAD_QUEUE_PAGE_TITLE,
    isZipDownloadsEnabled,
    ZIP_DISABLED_MESSAGE,
} from "../components/zipDownloads/zipDownloads";
import { usePageTitle } from "../hooks/usePageTitle";

/**
 * Global "My Download Queue" page (#/zipdownloads): the caller's download jobs across assets.
 * Laid out like the other list pages (API Key Management, Roles): the page shell Box, an h1, then
 * the container table.
 */
const ZipDownloadsPage: React.FC = () => {
    usePageTitle(DOWNLOAD_QUEUE_PAGE_TITLE);
    const enabled = isZipDownloadsEnabled();
    return (
        <Box padding={{ top: "m", horizontal: "l" }}>
            <Grid gridDefinition={[{ colspan: 12 }]}>
                <div>
                    <TextContent>
                        <h1>{DOWNLOAD_QUEUE_PAGE_TITLE}</h1>
                    </TextContent>
                </div>
            </Grid>
            <Grid gridDefinition={[{ colspan: 12 }]}>
                {enabled ? (
                    <ZipDownloadsTable />
                ) : (
                    <Alert
                        type="info"
                        statusIconAriaLabel="Info"
                        header="The Download Queue is not available"
                    >
                        {ZIP_DISABLED_MESSAGE}.
                    </Alert>
                )}
            </Grid>
            <div style={{ paddingBottom: "20px" }} />
        </Box>
    );
};

export default ZipDownloadsPage;
