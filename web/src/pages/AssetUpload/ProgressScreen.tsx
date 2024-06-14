/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { Box, Grid, Link, SpaceBetween, TextContent } from "@cloudscape-design/components";

import ProgressBar, { ProgressBarProps } from "@cloudscape-design/components/progress-bar";
import StatusIndicator, {
    StatusIndicatorProps,
} from "@cloudscape-design/components/status-indicator";
import { FileUploadTable, FileUploadTableItem } from "./FileUploadTable";
import { AssetDetail } from "../AssetUpload";
import Synonyms from "../../synonyms";

class ProgressScreenProps {
    assetDetail!: AssetDetail;
    previewUploadProgress?: ProgressBarProps;
    allFileUploadItems!: FileUploadTableItem[];
    execStatus!: Record<string, StatusIndicatorProps.Type>;
    onRetry!: () => void;
}

export default function ProgressScreen({
    assetDetail,
    previewUploadProgress,
    execStatus,
    allFileUploadItems,
    onRetry,
}: ProgressScreenProps): JSX.Element {
    const get_completed_items = (items: FileUploadTableItem[]) => {
        return items.filter((item) => item.status === "Completed");
    };
    return (
        <Box padding={{ top: false ? "s" : "m", horizontal: "l" }}>
            <Grid gridDefinition={[{ colspan: { default: 12 } }]}>
                <div>
                    <SpaceBetween size="l" direction={"vertical"}>
                        <Box variant="awsui-key-label">
                            Upload Progress for {Synonyms.Asset}:
                            <Link
                                href={`#/databases/${assetDetail.databaseId}/assets/${assetDetail.assetId}`}
                                target="_blank"
                            >
                                {assetDetail.assetName}
                            </Link>
                        </Box>
                        <ProgressBar
                            status={
                                get_completed_items(allFileUploadItems).length ===
                                allFileUploadItems.length
                                    ? "success"
                                    : "in-progress"
                            }
                            value={
                                (get_completed_items(allFileUploadItems).length /
                                    allFileUploadItems.length) *
                                100
                            }
                            label="Overall Upload Progress"
                        />

                        <FileUploadTable
                            allItems={allFileUploadItems}
                            onRetry={onRetry}
                            resume={false}
                            showCount={true}
                        />
                        {assetDetail.Preview && previewUploadProgress && (
                            <ProgressBar
                                status={previewUploadProgress.status}
                                value={previewUploadProgress.value}
                                label="Preview Upload Progress"
                            />
                        )}
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
