/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense, useState, useCallback } from "react";
import { Container, Header, Tabs } from "@cloudscape-design/components";
import ErrorBoundary from "../common/ErrorBoundary";
import { LoadingSpinner } from "../common/LoadingSpinner";

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
    filePathToNavigate?: string; // Optional file path to navigate to in File Manager
}

export const TabbedContainer: React.FC<TabbedContainerProps> = ({
    assetName,
    assetId,
    databaseId,
    onExecuteWorkflow,
    onWorkflowExecuted,
    filePathToNavigate,
}) => {
    // Set File Manager tab as active by default, especially if we have a file path to navigate to
    const [activeTabId, setActiveTabId] = useState("file-manager");
    const [workflowRefreshTrigger, setWorkflowRefreshTrigger] = useState(0);

    // Function to refresh the workflow tab
    const refreshWorkflowTab = useCallback(() => {
        setWorkflowRefreshTrigger((prev) => prev + 1);
        if (onWorkflowExecuted) {
            onWorkflowExecuted();
        }
    }, [onWorkflowExecuted]);

    return (
        <ErrorBoundary componentName="Tabbed Container">
            <Container>
                <Tabs
                    activeTabId={activeTabId}
                    onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
                    tabs={[
                        {
                            id: "file-manager",
                            label: "File Manager",
                            content: (
                                <Suspense
                                    fallback={<LoadingSpinner text="Loading File Manager..." />}
                                >
                                    <FileManagerTab
                                        assetName={assetName}
                                        filePathToNavigate={filePathToNavigate}
                                    />
                                </Suspense>
                            ),
                        },
                        {
                            id: "relationships",
                            label: "Relationships",
                            content: (
                                <Suspense
                                    fallback={<LoadingSpinner text="Loading Relationships..." />}
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
                                <Suspense fallback={<LoadingSpinner text="Loading Workflows..." />}>
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
                            label: "Comments",
                            content: (
                                <Suspense fallback={<LoadingSpinner text="Loading Comments..." />}>
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
                                <Suspense fallback={<LoadingSpinner text="Loading Versions..." />}>
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
            </Container>
        </ErrorBoundary>
    );
};

export default TabbedContainer;
