/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import React, { useRef } from "react";
import { Box, Grid, TextContent } from "@cloudscape-design/components";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Button from "@cloudscape-design/components/button";

import FormField from "@cloudscape-design/components/form-field";

import { formatFileSize } from "../../components/form/FileUploadControl";

function getLang() {
    if (navigator.languages !== undefined) return navigator.languages[0];
    if (navigator.language) return navigator.language;
    return "en-us";
}

class FileUploadProps {
    label?: string;
    errorText?: string;
    fileFormats!: string;
    setFile!: (file: File) => void;
    file: File | undefined;
    disabled!: boolean;
}

export const FileUpload = ({
    errorText,
    fileFormats,
    disabled,
    setFile,
    file,
    label,
}: FileUploadProps) => {
    const inputRef = useRef<HTMLInputElement>(null);

    return (
        <FormField errorText={errorText} label={label} description={"File types: " + fileFormats}>
            <input
                ref={inputRef}
                type="file"
                accept={fileFormats}
                style={{ display: "none" }}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                    if (e.target.files && e.target.files.length > 0 && e.target.files[0]) {
                        setFile(e.target.files[0]);
                    }
                }}
            />
            <SpaceBetween size="l">
                <Button
                    disabled={disabled}
                    variant="normal"
                    iconName="upload"
                    onClick={(e) => {
                        inputRef?.current?.click();
                    }}
                >
                    Choose File
                </Button>
                <DisplayFileMeta file={file} />
            </SpaceBetween>
        </FormField>
    );
};
class DisplayFileMetaProps {
    file?: File;
}
export function DisplayFileMeta({ file }: DisplayFileMetaProps) {
    return (
        <Grid gridDefinition={[{ colspan: { default: 6 } }, { colspan: { default: 6 } }]}>
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
                            {new Date(file?.lastModified).toLocaleDateString(getLang(), {
                                weekday: "long",
                                year: "numeric",
                                month: "short",
                                day: "numeric",
                            })}
                        </small>
                    </TextContent>
                </>
            )}
        </Grid>
    );
}

class DisplayKVProps {
    label!: string;
    value!: any;
}

export function DisplayKV({ label, value }: DisplayKVProps): JSX.Element {
    if (value instanceof File) {
        return (
            <div>
                <Box variant="awsui-key-label">{label}</Box>
                <DisplayFileMeta file={value} />
            </div>
        );
    }

    if (typeof(value) =="boolean") {
        return (
            <div>
                <Box variant="awsui-key-label">{label}</Box>
                <div>{value ? "Yes" : "No"}</div>
            </div>
        );
    }

    return (
        <div>
            <Box variant="awsui-key-label">{label}</Box>
            <div>{value}</div>
        </div>
    );
}
