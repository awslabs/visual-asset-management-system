/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense, useEffect, useState } from "react";
import { Container, Tabs } from "@cloudscape-design/components";
import ErrorBoundary from "../common/ErrorBoundary";
import { LoadingSpinner } from "../common/LoadingSpinner";
import Synonyms from "../../synonyms";
import {
    DOWNLOAD_QUEUE_TAB_LABEL,
    isZipDownloadsEnabled,
    ZIP_DOWNLOADS_TAB_ID,
} from "../zipDownloads/zipDownloads";

// Lazy load the tab components
const FileManagerTab = React.lazy(() => import("./tabs/FileManagerTab"));
const AssetLinksTab = React.lazy(() => import("./tabs/AssetLinksTab"));
const AssetExecutionsTab = React.lazy(() => import("./tabs/AssetExecutionsTab"));
const CommentsTab = React.lazy(() => import("./tabs/CommentsTab"));
const VersionsTab = React.lazy(() => import("./tabs/VersionsTab"));
const ZipDownloadsTab = React.lazy(() => import("./tabs/ZipDownloadsTab"));

export { ZIP_DOWNLOADS_TAB_ID };

const DEFAULT_TAB_ID = "file-manager";
const BASE_TAB_IDS = [DEFAULT_TAB_ID, "relationships", "workflows", "comments", "versions"];

interface TabbedContainerProps {
    assetName: string;
    assetId: string;
    databaseId: string;
    filePathToNavigate?: string; // Optional file path to navigate to in File Manager
    assetVersionId?: string; // Optional version ID to filter files and metadata
    /** Bubbled-up notification when the user changes the file/folder selection. */
    onSelectedPathChange?: (path: string | null) => void;
    /**
     * Tab named by the page's `?tab=` query parameter. A change selects that tab without
     * remounting the page; an unknown id (or a tab the deployment does not show) is ignored.
     */
    requestedTabId?: string | null;
    /** Called when the user picks a tab, so the page can keep `?tab=` in step. */
    onTabChange?: (tabId: string) => void;
    /** Called after a version is created, reverted to, edited, archived or unarchived. */
    onVersionsChanged?: () => void;
}

export const TabbedContainer: React.FC<TabbedContainerProps> = ({
    assetName,
    assetId,
    databaseId,
    filePathToNavigate,
    assetVersionId,
    onSelectedPathChange,
    requestedTabId,
    onTabChange,
    onVersionsChanged,
}) => {
    // The feature switches are applied before the app renders, so one reading per mount is enough.
    const [zipDownloadsEnabled] = useState(() => isZipDownloadsEnabled());
    const isKnownTab = (id?: string | null): id is string =>
        !!id && (BASE_TAB_IDS.includes(id) || (zipDownloadsEnabled && id === ZIP_DOWNLOADS_TAB_ID));
    // File Manager is active by default (a file path to navigate to opens there); `?tab=` picks
    // another tab, such as the Download Queue a newly created zip job links to.
    const [activeTabId, setActiveTabId] = useState(() =>
        isKnownTab(requestedTabId) ? requestedTabId : DEFAULT_TAB_ID
    );
    useEffect(() => {
        if (isKnownTab(requestedTabId)) setActiveTabId(requestedTabId);
        else if (!requestedTabId) setActiveTabId(DEFAULT_TAB_ID);
        // isKnownTab only reads the mount-time feature switch.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [requestedTabId]);

    return (
        <ErrorBoundary componentName="Tabbed Container">
            <Container>
                <div style={{ marginBottom: "-20px" }}>
                    <Tabs
                        activeTabId={activeTabId}
                        onChange={({ detail }) => {
                            setActiveTabId(detail.activeTabId);
                            onTabChange?.(detail.activeTabId);
                        }}
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
                                            onSelectedPathChange={onSelectedPathChange}
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
                                label: "Executions",
                                content: (
                                    <Suspense
                                        fallback={<LoadingSpinner text="Loading Executions..." />}
                                    >
                                        <AssetExecutionsTab
                                            databaseId={databaseId}
                                            assetId={assetId}
                                            isActive={activeTabId === "workflows"}
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
                                            onVersionsChanged={onVersionsChanged}
                                        />
                                    </Suspense>
                                ),
                            },
                            // Download as Zip: present only when the deployment has the feature
                            ...(zipDownloadsEnabled
                                ? [
                                      {
                                          id: ZIP_DOWNLOADS_TAB_ID,
                                          label: DOWNLOAD_QUEUE_TAB_LABEL,
                                          content: (
                                              <Suspense
                                                  fallback={
                                                      <LoadingSpinner text="Loading download queue..." />
                                                  }
                                              >
                                                  <ZipDownloadsTab
                                                      databaseId={databaseId}
                                                      assetId={assetId}
                                                      isActive={
                                                          activeTabId === ZIP_DOWNLOADS_TAB_ID
                                                      }
                                                  />
                                              </Suspense>
                                          ),
                                      },
                                  ]
                                : []),
                        ]}
                    />
                </div>
            </Container>
        </ErrorBoundary>
    );
};

export default TabbedContainer;
