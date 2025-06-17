/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense, useState } from "react";
import { Container, Header, Tabs } from "@cloudscape-design/components";
import ErrorBoundary from "../common/ErrorBoundary";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { FileKey } from "../filemanager/types/FileManagerTypes";

// Lazy load the tab components
const FileManagerTab = React.lazy(() => import("./tabs/FileManagerTab"));
const WorkflowTab = React.lazy(() => import("./tabs/WorkflowTab"));
const CommentsTab = React.lazy(() => import("./tabs/CommentsTab"));
const VersionsTab = React.lazy(() => import("./tabs/VersionsTab"));

interface TabbedContainerProps {
  assetName: string;
  assetFiles: FileKey[];
  assetId: string;
  databaseId: string;
  loadingFiles: boolean;
  onExecuteWorkflow: () => void;
}

export const TabbedContainer: React.FC<TabbedContainerProps> = ({
  assetName,
  assetFiles,
  assetId,
  databaseId,
  loadingFiles,
  onExecuteWorkflow,
}) => {
  const [activeTabId, setActiveTabId] = useState("file-manager");

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
                <Suspense fallback={<LoadingSpinner text="Loading File Manager..." />}>
                  <FileManagerTab
                    assetName={assetName}
                    assetFiles={assetFiles}
                    assetId={assetId}
                    databaseId={databaseId}
                    loading={loadingFiles}
                    onExecuteWorkflow={onExecuteWorkflow} // Keeping prop for compatibility
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
