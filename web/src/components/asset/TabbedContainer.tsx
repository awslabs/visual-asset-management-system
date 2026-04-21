/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense, useState, useCallback, useEffect } from "react";
import { Container, Header, Tabs } from "@cloudscape-design/components";
import ErrorBoundary from "../common/ErrorBoundary";
import { LoadingSpinner } from "../common/LoadingSpinner";
import Synonyms from "../../synonyms";

// Lazy load the tab components
const FileManagerTab = React.lazy(() => import("./tabs/FileManagerTab"));
const AssetLinksTab = React.lazy(() => import("./tabs/AssetLinksTab"));
const WorkflowTab = React.lazy(() => import("./tabs/WorkflowTab"));
const CommentsTab = React.lazy(() => import("./tabs/CommentsTab"));
const VersionsTab = React.lazy(() => import("./tabs/VersionsTab"));

interface TabbedContainerProps {
    assetName: string;
    assetId: string;
    databaseId: string;
    onExecuteWorkflow: () => void;
    onWorkflowExecuted?: () => void; // Callback when workflow execution is complete
    workflowExecutedTrigger?: number; // Trigger value that changes when workflow is executed
    filePathToNavigate?: string; // Optional file path to navigate to in File Manager
    assetVersionId?: string; // Optional version ID to filter files and metadata
}

export const TabbedContainer: React.FC<TabbedContainerProps> = ({
    assetName,
    assetId,
    databaseId,
    onExecuteWorkflow,
    onWorkflowExecuted,
    workflowExecutedTrigger,
    filePathToNavigate,
    assetVersionId,
}) => {
    // Set File Manager tab as active by default, especially if we have a file path to navigate to
    const [activeTabId, setActiveTabId] = useState("file-manager");
    const [workflowRefreshTrigger, setWorkflowRefreshTrigger] = useState(0);

    // Watch for changes in the parent's trigger value
    useEffect(() => {
        if (workflowExecutedTrigger !== undefined && workflowExecutedTrigger > 0) {
            console.log(
                "TabbedContainer: workflowExecutedTrigger changed to",
                workflowExecutedTrigger,
                "- incrementing local trigger"
            );
            setWorkflowRefreshTrigger((prev) => prev + 1);
        }
    }, [workflowExecutedTrigger]);

    return (
        <ErrorBoundary componentName="Tabbed Container">
            <Container>
                <div style={{ marginBottom: "-20px" }}>
                    <Tabs
                        activeTabId={activeTabId}
                        onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
                        tabs={[
                            {
                                id: "file-manager",
                                label: assetVersionId
                                    ? `File Manager (v${assetVersionId})`
                                    : "File Manager",
                                content: (
                                    <Suspense
                                        fallback={<LoadingSpinner text="Loading File Manager..." />}
                                    >
                                        <FileManagerTab
                                            assetName={assetName}
                                            filePathToNavigate={filePathToNavigate}
                                            assetVersionId={assetVersionId}
                                        />
                                    </Suspense>
                                ),
                            },
                            {
                                id: "relationships",
                                label: "Relationships",
                                content: (
                                    <Suspense
                                        fallback={
                                            <LoadingSpinner text="Loading Relationships..." />
                                        }
                                    >
                                        <AssetLinksTab
                                            mode="view"
                                            assetId={assetId}
                                            databaseId={databaseId}
                                            isActive={activeTabId === "relationships"}
                                        />
                                    </Suspense>
                                ),
                            },
                            {
                                id: "workflows",
                                label: "Workflows",
                                content: (
                                    <Suspense
                                        fallback={<LoadingSpinner text="Loading Workflows..." />}
                                    >
                                        <WorkflowTab
                                            databaseId={databaseId}
                                            assetId={assetId}
                                            isActive={activeTabId === "workflows"}
                                            onExecuteWorkflow={onExecuteWorkflow}
                                            refreshTrigger={workflowRefreshTrigger}
                                        />
                                    </Suspense>
                                ),
                            },
                            {
                                id: "comments",
                                label: Synonyms.Comments,
                                content: (
                                    <Suspense
                                        fallback={
                                            <LoadingSpinner
                                                text={`Loading ${Synonyms.Comments}...`}
                                            />
                                        }
                                    >
                                        <CommentsTab
                                            databaseId={databaseId}
                                            assetId={assetId}
                                            isActive={activeTabId === "comments"}
                                        />
                                    </Suspense>
                                ),
                            },
                            {
                                id: "versions",
                                label: "Versions",
                                content: (
                                    <Suspense
                                        fallback={<LoadingSpinner text="Loading Versions..." />}
                                    >
                                        <VersionsTab
                                            databaseId={databaseId}
                                            assetId={assetId}
                                            isActive={activeTabId === "versions"}
                                        />
                                    </Suspense>
                                ),
                            },
                        ]}
                    />
                </div>
            </Container>
        </ErrorBoundary>
    );
};

export default TabbedContainer;
