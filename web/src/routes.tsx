/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense, useEffect, useState } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { webRoutes } from "./services/APIService";
import AppLayout from "@cloudscape-design/components/app-layout";
import { Navigation } from "./layout/Navigation";
import LandingPage from "./pages/LandingPage";
import Spinner from "@cloudscape-design/components/spinner";
import { useNavigate } from "react-router";

const Databases = React.lazy(() => import("./pages/Databases"));
const SearchPage = React.lazy(() => import("./pages/search/SearchPage"));
const AssetUploadPage = React.lazy(() => import("./pages/AssetUpload/AssetUpload"));
const ViewAsset = React.lazy(() => import("./components/asset/ViewAsset"));
const Pipelines = React.lazy(() => import("./pages/Pipelines"));
const ViewPipeline = React.lazy(() => import("./components/single/ViewPipeline"));
const Workflows = React.lazy(() => import("./pages/Workflows"));
const CreateUpdateWorkflow = React.lazy(
    () => import("./components/createupdate/CreateUpdateWorkflow")
);
const Constraints = React.lazy(() => import("./pages/auth/Constraints"));
const Tags = React.lazy(() => import("./pages/Tag/Tags"));
const Subscriptions = React.lazy(() => import("./pages/Subscription/Subscriptions"));
const Roles = React.lazy(() => import("./pages/auth/Roles"));
const UserRoles = React.lazy(() => import("./pages/auth/UserRoles"));
const CognitoUsers = React.lazy(() => import("./pages/auth/CognitoUsers"));
const ModifyAssetsUploadsPage = React.lazy(() => import("./pages/AssetUpload/ModifyAssetsUploads"));
const MetadataSchema = React.lazy(() => import("./pages/MetadataSchema"));
const ViewFile = React.lazy(() => import("./components/single/ViewFile"));
const AssetIngestion = React.lazy(() => import("./components/single/AssetIngestion"));
const AssetDownloadsPage = React.lazy(() => import("./pages/AssetDownload"));

interface RouteOption {
    path: string;
    Page: React.FC;
    active: string;
}

export const routeTable: RouteOption[] = [
    { path: "/", Page: LandingPage, active: "/" },
    { path: "/search", Page: SearchPage, active: "/" },
    { path: "/search/:databaseId/assets", Page: SearchPage, active: "/" },
    { path: "/assets", Page: SearchPage, active: "#/assets/" },
    { path: "/databases", Page: Databases, active: "#/databases/" },
    {
        path: "/databases/:databaseId/assets",
        Page: SearchPage,
        active: "#/assets/",
    },
    {
        path: "/databases/:databaseId/assets/:assetId",
        Page: ViewAsset,
        active: "/assets",
    },
    {
        path: "/databases/:databaseId/assets/:assetId/download",
        Page: AssetDownloadsPage,
        active: "/assets",
    },
    {
        path: "/databases/:databaseId/assets/:assetId/uploads",
        Page: ModifyAssetsUploadsPage,
        active: "#/assets/",
    },
    {
        path: "/databases/:databaseId/assets/:assetId/file/*",
        Page: ViewFile,
        active: "#/assets/",
    },
    {
        path: "/databases/:databaseId/assets/:assetId/file",
        Page: ViewFile,
        active: "#/assets/",
    },
    { path: "/assets/:assetId", Page: ViewAsset, active: "#/assets/" },
    {
        path: "/upload/:databaseId",
        Page: AssetUploadPage,
        active: "#/upload/",
    },
    { path: "/upload", Page: AssetUploadPage, active: "#/upload/" },
    //{ path: "/visualizers/:pathViewType", Page: ViewAsset, active: "/assets"},
    {
        path: "/databases/:databaseId/pipelines",
        Page: Pipelines,
        active: "#/pipelines/",
    },
    { path: "/pipelines", Page: Pipelines, active: "#/pipelines/" },
    {
        path: "/pipelines/:pipelineName",
        Page: ViewPipeline,
        active: "#/pipelines/",
    },
    {
        path: "/databases/:databaseId/workflows",
        Page: Workflows,
        active: "#/workflows/",
    },
    { path: "/workflows", Page: Workflows, active: "#/workflows/" },
    {
        path: "/databases/:databaseId/workflows/:workflowId",
        Page: CreateUpdateWorkflow,
        active: "#/workflows/",
    },
    {
        path: "/workflows/create",
        Page: CreateUpdateWorkflow,
        active: "#/workflows/",
    },
    {
        path: "/databases/:databaseId/workflows/create",
        Page: CreateUpdateWorkflow,
        active: "#/workflows/",
    },
    {
        path: "/auth/constraints",
        Page: Constraints,
        active: "#/auth/constraints/",
    },
    {
        path: "/auth/tags",
        Page: Tags,
        active: "#/auth/tags/",
    },
    {
        path: "/auth/subscriptions",
        Page: Subscriptions,
        active: "#/auth/subscriptions/",
    },
    {
        path: "/auth/roles",
        Page: Roles,
        active: "#/auth/roles/",
    },
    {
        path: "/assetIngestion",
        Page: AssetIngestion,
        active: "#/assetIngestion",
    },
    {
        path: "/auth/userroles",
        Page: UserRoles,
        active: "#/auth/userroles/",
    },
    {
        path: "/auth/cognitousers",
        Page: CognitoUsers,
        active: "#/auth/cognitousers/",
    },
    {
        path: "/metadataschema/:databaseId",
        Page: MetadataSchema,
        active: "#/metadataschema",
    },
    {
        path: "/metadataschema",
        Page: MetadataSchema,
        active: "#/metadataschema",
    },
    {
        path: "*",
        Page: LandingPage,
        active: "/",
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
    const location = useLocation();
    const navigate = useNavigate();

    const [loading, setLoading] = useState(true);

    //Used to detect duplicate/stacked hash route pathing and correct
    //Note: This will null out any state passing for the naviagation and may break a a state passing page if triggered
    useEffect(() => {
        console.log("Location changed: ", window.location.href);
        console.log("user", user);

        const hashes = window.location.href.match(/#/g) || [];
        console.log("Hash Count in URL: ", hashes.length);

        if (hashes.length > 1) {
            const { state } = location;
            console.log("Previous State Recorded: ", state);

            const segments = window.location.href.split("#/");

            //console.log('Total URL Segments Found: ', segments);

            const fragmentWeWant = segments.pop();
            console.log(
                "HashRoute Duplicate Detected, Re-routing to Last Hash:",
                `#/${fragmentWeWant}`
            );

            const url = new URL(window.location.href);
            url.hash = `#/${fragmentWeWant}`;

            console.log("Full URL Redirect:", url.href);

            window.location.href = url.toString();
        }
    }, [location, navigate]);

    let allAllowedRoutes: string[] = [];
    const [allowedRoutes, setAllowedRoutes] = useState<string[]>(
        routeTable.map((route) => {
            return route.path;
        })
    );

    useEffect(() => {
        let allRoutes = [];
        for (let route of routeTable) {
            if (route.path) {
                allRoutes.push({
                    method: "GET",
                    route__path: route.path,
                });
            }
        }

        try {
            webRoutes({ routes: allRoutes })
                .then((value) => {
                    if (value[0] === false) {
                        throw new Error("webRoutes - " + value[1]);
                    }

                    for (let allowedRoute of value.allowedRoutes) {
                        allAllowedRoutes.push(allowedRoute.route__path);
                    }

                    //If allowed routes doesn't contain * or / for the landing page, add that back so all users can get to the landing information page
                    if (!allAllowedRoutes.includes("/")) allAllowedRoutes.push("/");
                    if (!allAllowedRoutes.includes("*")) allAllowedRoutes.push("*");

                    setAllowedRoutes(allAllowedRoutes);
                    setLoading(false);
                })
                .catch((error) => {
                    console.error(error);
                    setAllowedRoutes([]);
                    setLoading(false);
                });
        } catch (e) {}
    }, []);

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
                            loading ? (
                                <CenterSpinner />
                            ) : (
                                <Suspense fallback={<CenterSpinner />}>
                                    <Page />
                                </Suspense>
                            )
                        }
                        navigation={<Navigation activeHref={active} user={user} />}
                        navigationOpen={navigationOpen}
                        onNavigationChange={({ detail }) => setNavigationOpen(detail.open)}
                        toolsHide={true}
                        maxContentWidth={Number.MAX_SAFE_INTEGER}
                        contentType="default"
                    />
                }
            />
        );
    };

    const filterRoute = (routeOptions: RouteOption) => {
        return allowedRoutes.includes(routeOptions.path);
    };

    return <Routes>{routeTable.filter(filterRoute).map(buildRoute)}</Routes>;
};
