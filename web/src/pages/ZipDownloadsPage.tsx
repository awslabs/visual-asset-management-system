/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Alert from "@cloudscape-design/components/alert";
import ZipDownloadsTable from "../components/zipDownloads/ZipDownloadsTable";
import {
    isZipDownloadsEnabled,
    ZIP_DISABLED_MESSAGE,
} from "../components/zipDownloads/zipDownloads";

/** Global "My zip downloads" page (#/zipdownloads): the caller's zip downloads across assets. */
const ZipDownloadsPage: React.FC = () => {
    if (!isZipDownloadsEnabled()) {
        return (
            <Alert type="info" statusIconAriaLabel="Info" header="Zip downloads are not available">
                {ZIP_DISABLED_MESSAGE}.
            </Alert>
        );
    }
    return <ZipDownloadsTable headerVariant="h1" />;
};

export default ZipDownloadsPage;
