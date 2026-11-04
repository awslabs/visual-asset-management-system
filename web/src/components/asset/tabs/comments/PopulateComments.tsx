/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import VersionComments from "./VersionComments";

// Define the type for showMessage prop
type ShowMessageFunction = (props: {
    message: React.ReactNode;
    type: "error" | "warning" | "success" | "info";
    dismissible?: boolean;
    autoDismiss?: boolean;
    dismissTimeout?: number;
    onDismiss?: () => void;
}) => void;

interface CommentType {
    assetId: string;
    "assetVersionId:commentId": string;
    commentBody: string;
    commentOwnerID: string;
    commentOwnerUsername: string;
    dateCreated: string;
    dateEdited?: string;
}

interface AssetType {
    assetId: string;
    assetLocation: Object;
    assetName: string;
    assetType: string;
    currentVersion: {
        Comment: string;
        dateModified: string;
        S3Version: string;
        Version: string;
        description: string;
    };
    databaseId: string;
    description: string;
    executionId?: string;
    isDistributable: boolean;
    pipelineId?: string;
    specifiedPipelines?: Array<any>;
    versions?: Array<any>;
}

interface PopulateCommentsProps {
    loading: boolean;
    showLoading: (keepItemsDisplayed: boolean) => void;
    userId: string;
    asset: AssetType;
    allComments: CommentType[];
    setReload: (reload: boolean) => void;
    showMessage: ShowMessageFunction;
}

export default function PopulateComments(props: PopulateCommentsProps) {
    const { loading, showLoading, userId, asset, allComments, setReload, showMessage } = props;

    // Sort the comments so that the most recent comment appears at the bottom of the list
    allComments.sort(function (a: CommentType, b: CommentType) {
        return new Date(a.dateCreated).valueOf() - new Date(b.dateCreated).valueOf();
    });

    if (asset) {
        return (
            <div data-testid="expandableSectionDiv">
                <VersionComments
                    loading={loading}
                    showLoading={showLoading}
                    userId={userId}
                    setReload={setReload}
                    defaultExpanded={true}
                    comments={allComments}
                    showMessage={showMessage}
                />
            </div>
        );
    } else {
        return <div data-testid="expandableSectionDiv"></div>;
    }
}
