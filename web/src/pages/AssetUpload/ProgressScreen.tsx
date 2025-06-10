/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { Box, Grid, Link, SpaceBetween, TextContent } from "@cloudscape-design/components";
import ProgressBar, { ProgressBarProps } from "@cloudscape-design/components/progress-bar";
import StatusIndicator, {
    StatusIndicatorProps,
} from "@cloudscape-design/components/status-indicator";
import { FileUploadTableItem } from "./FileUploadTable";
import { AssetDetail } from "../AssetUpload";
import Synonyms from "../../synonyms";
import UploadManager from "./UploadManager";
import { useState } from "react";
import { CompleteUploadResponse } from "../../services/AssetUploadService";

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
    const [uploadComplete, setUploadComplete] = useState(false);
    const [uploadError, setUploadError] = useState<Error | null>(null);
    const [uploadResponse, setUploadResponse] = useState<CompleteUploadResponse | null>(null);

    // Handle upload completion
    const handleUploadComplete = (response: CompleteUploadResponse) => {
        setUploadComplete(true);
        setUploadResponse(response);
    };

    // Handle upload error
    const handleUploadError = (error: Error) => {
        setUploadError(error);
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

                        {/* Use the new UploadManager component */}
                        <UploadManager
                            assetDetail={assetDetail}
                            metadata={{}} // We'll need to pass the actual metadata here
                            fileItems={allFileUploadItems}
                            onUploadComplete={handleUploadComplete}
                            onError={handleUploadError}
                            isExistingAsset={false}
                        />

                        {/* Preview upload progress if applicable */}
                        {assetDetail.Preview && previewUploadProgress && (
                            <ProgressBar
                                status={previewUploadProgress.status}
                                value={previewUploadProgress.value}
                                label="Preview Upload Progress"
                            />
                        )}

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
