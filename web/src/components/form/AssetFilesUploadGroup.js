/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Grid, TextContent } from "@cloudscape-design/components";
import {
    cadFileFormats,
    columnarFileFormats,
    previewFileFormats,
    modelFileFormats,
    pcFileFormats,
    archiveFileFormats,
} from "../../common/constants/fileFormats";
import FileUploadControl from "./FileUploadControl";

const AssetFilesUploadGroup = (props) => {
    const { disabled } = props;
    return (
        <Grid gridDefinition={[{ colspan: { default: "6" } }, { colspan: { default: "6" } }]}>
            <div>
                <TextContent>Asset File</TextContent>
                <FileUploadControl
                    disabled={disabled}
                    controlName={"Asset"}
                    fileFormats={modelFileFormats
                        .concat(
                            columnarFileFormats.concat(
                                cadFileFormats.concat(archiveFileFormats.concat(pcFileFormats))
                            )
                        )
                        .join(",")}
                />
            </div>
            <div>
                <TextContent>Preview File</TextContent>
                <FileUploadControl
                    disabled={disabled}
                    controlName={"Preview"}
                    fileFormats={previewFileFormats.join(",")}
                />
            </div>
        </Grid>
    );
};

export default AssetFilesUploadGroup;
