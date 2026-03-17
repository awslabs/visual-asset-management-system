/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useContext, useEffect, useState, useRef } from "react";
import { Button, FormField, Grid, SpaceBetween, TextContent } from "@cloudscape-design/components";
import { AssetContext } from "../../context/AssetContex";

//@link https://stackoverflow.com/questions/10420352/converting-file-size-in-bytes-to-human-readable-string/10420404
export const formatFileSize = (bytes, si = false, dp = 1) => {
    const thresh = si ? 1000 : 1024;
    if (Math.abs(bytes) < thresh) {
        return bytes + " B";
    }
    const units = si
        ? ["kB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"]
        : ["KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB"];
    let u = -1;
    const r = 10 ** dp;
    do {
        bytes /= thresh;
        ++u;
    } while (Math.round(Math.abs(bytes) * r) / r >= thresh && u < units.length - 1);

    return bytes.toFixed(dp) + " " + units[u];
};

const FileUploadControl = (props) => {
    const { disabled, controlName, fileFormats } = props;
    const { formValues, setFormValues, formErrors } = useContext(AssetContext);
    const [file, setFile] = useState(null);
    const inputRef = useRef();

    useEffect(() => {
        setFile(formValues[controlName]);
    }, [formValues[controlName]]);

    useEffect(() => {
        if (file) {
            const newFormValues = Object.assign({}, formValues);
            newFormValues[controlName] = file;
            newFormValues.assetType = file.name.split(".").pop();
            setFormValues(newFormValues);
        }
    }, [file, controlName, setFormValues]);

    return (
        <>
            <FormField errorText={formErrors[controlName]}>
                <input
                    ref={inputRef}
                    type="file"
                    accept={fileFormats}
                    style={{ display: "none" }}
                    onChange={(e) => {
                        setFile(e.target.files[0]);
                    }}
                />
                <SpaceBetween size="l">
                    <Button
                        disabled={disabled}
                        variant="normal"
                        multiple
                        type="file"
                        iconName="upload"
                        onClick={(e) => {
                            inputRef.current.click();
                        }}
                    >
                        Choose File
                    </Button>
                    <Grid
                        gridDefinition={
                            (file && [
                                { colspan: { default: "6" } },
                                { colspan: { default: "6" } },
                            ]) ||
                            []
                        }
                    >
                        {file && (
                            <>
                                <TextContent>
                                    <strong>Filename:</strong>
                                    <br />
                                    <small>Size</small>
                                    <br />
                                    <small>Last Modified:</small>
                                </TextContent>
                                <TextContent>
                                    <strong>{file?.name}</strong>
                                    <br />
                                    <small>{formatFileSize(file?.size)}</small>
                                    <br />
                                    <small>
                                        {file?.lastModified.toLocaleString("en-US", {
                                            timeZone: "UTC",
                                        })}
                                    </small>
                                </TextContent>
                            </>
                        )}
                    </Grid>
                </SpaceBetween>
            </FormField>
        </>
    );
};

export default FileUploadControl;
