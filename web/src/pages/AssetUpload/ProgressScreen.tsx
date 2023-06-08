/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import {Box, Grid, SpaceBetween, TextContent} from "@cloudscape-design/components";

import ProgressBar, { ProgressBarProps } from "@cloudscape-design/components/progress-bar";
import StatusIndicator, {
    StatusIndicatorProps,
} from "@cloudscape-design/components/status-indicator";
import {FileUploadTable, FileUploadTableItem} from "./FileUploadTable";
import {AssetDetail} from "../AssetUpload";


class ProgressScreenProps {
    assetDetail!: AssetDetail
    previewUploadProgress?: ProgressBarProps;
    allFileUploadItems!: FileUploadTableItem[]
    execStatus!: Record<string, StatusIndicatorProps.Type>;
    onRetry!: () => void;
}

export default function ProgressScreen({
    assetDetail,
    previewUploadProgress,
    execStatus,
    allFileUploadItems,
    onRetry
}: ProgressScreenProps): JSX.Element {
    return (
        <Box padding={{ top: false ? "s" : "m", horizontal: "l" }}>
            <Grid gridDefinition={[{ colspan: { default: 12 } }]}>
                <div>
                    <SpaceBetween size="l" direction={"vertical"} >
                        <Box variant="awsui-key-label">Upload Progress</Box>

                        <FileUploadTable allItems={allFileUploadItems} onRetry={onRetry}/>
                        {
                            assetDetail.Preview &&  previewUploadProgress &&
                            <ProgressBar
                                status={previewUploadProgress.status}
                                value={previewUploadProgress.value}
                                label="Preview Upload Progress"
                            />
                        }
                        <Box variant="awsui-key-label">Exec Progress</Box>

                        {Object.keys(execStatus).map((label) => (
                            <div key={label}>
                                <StatusIndicator type={execStatus[label]}>{label}</StatusIndicator>
                            </div>
                        ))}
                        <div>
                            <TextContent>
                                Please do not close your browser window until processing completes.
                            </TextContent>
                        </div>
                    </SpaceBetween>
                    </div>
            </Grid>
        </Box>
    );
}
