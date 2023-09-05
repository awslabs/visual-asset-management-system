/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import AppLayout from "@cloudscape-design/components/app-layout";
import { Navigation } from "./layout/Navigation";
import LandingPage from "./pages/LandingPage";
import Spinner from "@cloudscape-design/components/spinner";

const Databases = React.lazy(() => import("./pages/Databases"));
const SearchPage = React.lazy(() => import("./pages/search/SearchPage"));
const Comments = React.lazy(() => import("./pages/Comments/Comments"));
const AssetUploadPage = React.lazy(() => import("./pages/AssetUpload"));
const ViewAsset = React.lazy(() => import("./components/single/ViewAsset"));
const Pipelines = React.lazy(() => import("./pages/Pipelines"));
const ViewPipeline = React.lazy(() => import("./components/single/ViewPipeline"));
const Workflows = React.lazy(() => import("./pages/Workflows"));
const CreateUpdateWorkflow = React.lazy(
    () => import("./components/createupdate/CreateUpdateWorkflow")
);
const Constraints = React.lazy(() => import("./pages/auth/Constraints"));
const FinishUploadsPage = React.lazy(() => import("./pages/FinishUploads"));
const MetadataSchema = React.lazy(() => import("./pages/MetadataSchema"));
const ViewFile = React.lazy(() => import("./components/single/ViewFile"));

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

function CenterSpinner() {
    return (
        <div
            aria-live="polite"
            aria-label="Loading page content."
            style={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                height: "100%",
            }}
        >
            <Spinner size="large" />
        </div>
    );
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
                        content={
                            <Suspense fallback={<CenterSpinner />}>
                                <Page />
                            </Suspense>
                        }
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
