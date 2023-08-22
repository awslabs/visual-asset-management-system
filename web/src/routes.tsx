/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Route, Routes } from "react-router-dom";
import AppLayout from "@cloudscape-design/components/app-layout";
import LandingPage from "./pages/LandingPage";
import SearchPage from "./pages/search/SearchPage";
import { Navigation } from "./layout/Navigation";
import Databases from "./pages/Databases";
import Assets from "./pages/Assets";
import Comments from "./pages/Comments/Comments";
import AssetUploadPage from "./pages/AssetUpload";
import ViewAsset from "./components/single/ViewAsset";
import Pipelines from "./pages/Pipelines";
import ViewPipeline from "./components/single/ViewPipeline";
import Workflows from "./pages/Workflows";
import CreateUpdateWorkflow from "./components/createupdate/CreateUpdateWorkflow";
import Constraints from "./pages/auth/Constraints";
import FinishUploadsPage from "./pages/FinishUploads";
import MetadataSchema from "./pages/MetadataSchema";
import ViewFile from "./components/single/ViewFile";

interface RouteOption {
    path: string;
    Page: React.FC;
    active: string;
    roles?: string[];
}

const routeTable: RouteOption[] = [
    { path: "/", Page: LandingPage, active: "/" },
    { path: "/search", Page: SearchPage, active: "/" },
    { path: "/search/:databaseId/assets", Page: SearchPage, active: "/", roles: ["assets"] },
    { path: "/assets", Page: SearchPage, active: "/assets", roles: ["assets"] },
    { path: "/databases", Page: Databases, active: "/databases", roles: ["assets"] },
    {
        path: "/databases/:databaseId/assets",
        Page: SearchPage,
        active: "/assets",
        roles: ["assets"],
    },
    {
        path: "/databases/:databaseId/assets/:assetId",
        Page: ViewAsset,
        active: "/assets",
        roles: ["assets"],
    },
    {
        path: "/databases/:databaseId/assets/:assetId/uploads",
        Page: FinishUploadsPage,
        active: "/assets",
        roles: ["assets", "upload"],
    },
    {
        path: "/databases/:databaseId/assets/:assetId/file",
        Page: ViewFile,
        active: "/assets",
        roles: ["assets", "upload"],
    },
    { path: "/assets/:assetId", Page: ViewAsset, active: "/assets", roles: ["assets"] },
    {
        path: "/upload/:databaseId",
        Page: AssetUploadPage,
        active: "/upload",
        roles: ["assets", "upload"],
    },
    { path: "/comments", Page: Comments, active: "/comments" },
    { path: "/upload", Page: AssetUploadPage, active: "/upload", roles: ["assets", "upload"] },
    { path: "/visualizers/:pathViewType", Page: ViewAsset, active: "/assets", roles: ["assets"] },
    {
        path: "/databases/:databaseId/pipelines",
        Page: Pipelines,
        active: "/pipelines",
        roles: ["pipelines"],
    },
    { path: "/pipelines", Page: Pipelines, active: "/pipelines", roles: ["pipelines"] },
    {
        path: "/pipelines/:pipelineName",
        Page: ViewPipeline,
        active: "/pipelines",
        roles: ["pipelines"],
    },
    {
        path: "/databases/:databaseId/workflows",
        Page: Workflows,
        active: "/workflows",
        roles: ["workflows"],
    },
    { path: "/workflows", Page: Workflows, active: "/workflows", roles: ["workflows"] },
    {
        path: "/databases/:databaseId/workflows/:workflowId",
        Page: CreateUpdateWorkflow,
        active: "/workflows",
        roles: ["workflows"],
    },
    {
        path: "/workflows/create",
        Page: CreateUpdateWorkflow,
        active: "/workflows",
        roles: ["workflows"],
    },
    {
        path: "/databases/:databaseId/workflows/create",
        Page: CreateUpdateWorkflow,
        active: "/workflows",
        roles: ["workflows"],
    },
    {
        path: "/auth/constraints",
        Page: Constraints,
        active: "/auth/constraints",
        roles: ["super-admin"],
    },
    {
        path: "*",
        Page: LandingPage,
        active: "/",
    },
    {
        path: "/metadataschema/create",
        Page: MetadataSchema,
        active: "/metadataschema",
    },
    {
        path: "/metadataschema/:databaseId/create",
        Page: MetadataSchema,
        active: "/metadataschema",
    },
];

interface AppRoutesProps {
    navigationOpen: boolean;
    setNavigationOpen: (open: boolean) => void;
    user: any;
}

export const AppRoutes = ({ navigationOpen, setNavigationOpen, user }: AppRoutesProps) => {
    const buildRoute = (routeOptions: RouteOption, i: number = 0) => {
        const { path, active, Page } = routeOptions;
        return (
            <Route
                key={i}
                path={path}
                element={
                    <AppLayout
                        disableContentPaddings={navigationOpen}
                        content={<Page />}
                        navigation={<Navigation activeHref={active} user={user} />}
                        navigationOpen={navigationOpen}
                        onNavigationChange={({ detail }) => setNavigationOpen(detail.open)}
                        toolsHide={true}
                    />
                }
            />
        );
    };

    const roles = JSON.parse(user.signInUserSession.idToken.payload["vams:roles"]);

    const filterRoute = (routeOptions: RouteOption) => {
        if (routeOptions.roles) {
            if (roles.includes("super-admin")) return true;
            return routeOptions.roles.some((role) => roles.includes(role));
        } else {
            return true;
        }
    };

    return <Routes>{routeTable.filter(filterRoute).map(buildRoute)}</Routes>;
};
