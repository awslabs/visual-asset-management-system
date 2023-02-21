/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Route, Routes } from "react-router-dom";
import AppLayout from "@cloudscape-design/components/app-layout";
import LandingPage from "./pages/LandingPage";
import { Navigation } from "./layout/Navigation";
import Databases from "./pages/Databases";
import Assets from "./pages/Assets";
import AssetUploadPage from "./pages/AssetUpload";
import ViewAsset from "./components/single/ViewAsset";
import Pipelines from "./pages/Pipelines";
import ViewPipeline from "./components/single/ViewPipeline";
import Workflows from "./pages/Workflows";
import CreateUpdateWorkflow from "./components/createupdate/CreateUpdateWorkflow";
import React from "react";

const routeTable = [
  { path: "/", Page: LandingPage, active: "/" },
  { path: "/databases", Page: Databases, active: "/databases" },
  { path: "/databases/:databaseId/assets", Page: Assets, active: "/assets" },
  {
    path: "/databases/:databaseId/assets/:assetId",
    Page: ViewAsset,
    active: "/assets",
  },
  { path: "/assets/:assetId", Page: ViewAsset, active: "/assets" },
  { path: "/upload/:databaseId", Page: AssetUploadPage, active: "/upload" },
  { path: "/upload", Page: AssetUploadPage, active: "/upload" },
  { path: "/visualizers/:pathViewType", Page: ViewAsset, active: "/assets" },
  { path: "/assets", Page: Assets, active: "/assets" },
  {
    path: "/databases/:databaseId/pipelines",
    Page: Pipelines,
    active: "/pipelines",
  },
  { path: "/pipelines", Page: Pipelines, active: "/pipelines" },
  {
    path: "/pipelines/:pipelineName",
    Page: ViewPipeline,
    active: "/pipelines",
  },
  {
    path: "/databases/:databaseId/workflows",
    Page: Workflows,
    active: "/workflows",
  },
  { path: "/workflows", Page: Workflows, active: "/workflows" },
  {
    path: "/databases/:databaseId/workflows/:workflowId",
    Page: CreateUpdateWorkflow,
    active: "/workflows",
  },
  {
    path: "/workflows/create",
    Page: CreateUpdateWorkflow,
    active: "/workflows",
  },
  {
    path: "/databases/:databaseId/workflows/create",
    Page: CreateUpdateWorkflow,
    active: "/workflows",
  },
];

export const AppRoutes = (props) => {
  const { navigationOpen, setNavigationOpen } = props;

  const buildRoute = (routeOptions, i) => {
    const { path, active, Page } = routeOptions;
    return (
      <Route
        key={i}
        path={path}
        element={
          <AppLayout
            disableContentPaddings={navigationOpen}
            content={<Page />}
            navigation={<Navigation activeHref={active} />}
            navigationOpen={navigationOpen}
            onNavigationChange={({ detail }) => setNavigationOpen(detail.open)}
            toolsHide={true}
          />
        }
      />
    );
  };

  return <Routes>{routeTable.map(buildRoute)}</Routes>;
};
