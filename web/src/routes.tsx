/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense, useEffect, useMemo, useState } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { checkWebRoutesAllowed, WebRouteCheck } from "./services/webRoutesCheck";
import AppLayout from "@cloudscape-design/components/app-layout";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { Navigation } from "./layout/Navigation";
import { ActiveNavHrefProvider } from "./layout/activeNavHref";
import LandingPage from "./pages/LandingPage";
import Spinner from "@cloudscape-design/components/spinner";
import { useNavigate } from "react-router";
import { ErrorBoundary } from "./components/common/ErrorBoundary";
import { appLayoutLabels } from "./common/helpers/labels";
import { addonRouteTable } from "./addons/addonRoutes";
import { customRouteTable } from "./custom/customRoutes";
import { isChunkLoadError, lazyWithRetry, reloadPage } from "./utils/chunkLoadRecovery";
import { useFeaturesEnabled } from "./context/RuntimeConfigContext";

const Databases = lazyWithRetry(() => import("./pages/Databases"));
const SearchPage = lazyWithRetry(() => import("./pages/search/SearchPage"));
const AssetUploadPage = lazyWithRetry(() => import("./pages/AssetUpload/AssetUpload"));
const ViewAsset = lazyWithRetry(() => import("./components/asset/ViewAsset"));
const PipelinesPage2 = lazyWithRetry(() => import("./pages/PipelinesPage2"));
const WorkflowsPage2 = lazyWithRetry(() => import("./pages/WorkflowsPage2"));
const WorkflowBuilderPage = lazyWithRetry(() => import("./pages/WorkflowBuilderPage"));
const PipelineBuilderPage = lazyWithRetry(() => import("./pages/PipelineBuilderPage"));
const TemplateListPage = lazyWithRetry(() => import("./pages/TemplateListPage"));
const TemplateBuilderPage = lazyWithRetry(() => import("./pages/TemplateBuilderPage"));
const WorkflowTriggersPage = lazyWithRetry(() => import("./pages/WorkflowTriggersPage"));
const ExecutionsPage = lazyWithRetry(() => import("./pages/ExecutionsPage"));
const ExecutionDetail = lazyWithRetry(() => import("./pages/ExecutionDetail"));
const Constraints = lazyWithRetry(() => import("./pages/auth/Constraints"));
const Tags = lazyWithRetry(() => import("./pages/Tag/Tags"));
const Subscriptions = lazyWithRetry(() => import("./pages/Subscription/Subscriptions"));
const Roles = lazyWithRetry(() => import("./pages/auth/Roles"));
const UserRoles = lazyWithRetry(() => import("./pages/auth/UserRoles"));
const CognitoUsers = lazyWithRetry(() => import("./pages/auth/CognitoUsers"));
const ApiKeys = lazyWithRetry(() => import("./pages/auth/ApiKeys"));
const ModifyAssetsUploadsPage = lazyWithRetry(
    () => import("./pages/AssetUpload/ModifyAssetsUploads")
);
const MetadataSchema = lazyWithRetry(() => import("./pages/MetadataSchema"));
const ViewFile = lazyWithRetry(() => import("./components/single/ViewFile"));
const AssetIngestion = lazyWithRetry(() => import("./components/single/AssetIngestion"));
const AssetDownloadsPage = lazyWithRetry(() => import("./pages/AssetDownload"));
const ComplianceSchemas = lazyWithRetry(() => import("./pages/ComplianceSchemas"));
const ComplianceQuarantine = lazyWithRetry(() => import("./pages/ComplianceQuarantine"));
const ComplianceCascades = lazyWithRetry(() => import("./pages/ComplianceCascades"));
const ComplianceAuditLog = lazyWithRetry(() => import("./pages/ComplianceAuditLog"));
const DatabaseCompliance = lazyWithRetry(() => import("./pages/DatabaseCompliance"));

export interface RouteOption {
    path: string;
    Page: React.FC;
    active: string;
    /**
     * Feature switches that must all be enabled for the route to exist on this deployment, as an
     * add-on page carries them (addons/addonRoutes.tsx). Without them the route is not mounted, not
     * sent to the web-route check and not offered in the constraint editor.
     */
    featuresEnabledRestriction?: string[];
}

const coreRouteTable: RouteOption[] = [
    { path: "/", Page: LandingPage, active: "#/" },
    { path: "/search", Page: SearchPage, active: "#/" },
    { path: "/search/:databaseId/assets", Page: SearchPage, active: "#/" },
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
        active: "#/assets/",
    },
    {
        path: "/databases/:databaseId/assets/:assetId/download",
        Page: AssetDownloadsPage,
        active: "#/assets/",
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
    {
        path: "/upload/:databaseId",
        Page: AssetUploadPage,
        active: "#/upload/",
    },
    { path: "/upload", Page: AssetUploadPage, active: "#/upload/" },
    //{ path: "/visualizers/:pathViewType", Page: ViewAsset, active: "/assets"},
    {
        path: "/databases/:databaseId/pipelines",
        Page: PipelinesPage2,
        active: "#/pipelines/",
    },
    { path: "/pipelines", Page: PipelinesPage2, active: "#/pipelines/" },
    {
        path: "/databases/:databaseId/pipelines/create",
        Page: PipelineBuilderPage,
        active: "#/pipelines/",
    },
    {
        path: "/databases/:databaseId/pipelines/:pipelineId/templates/create",
        Page: TemplateBuilderPage,
        active: "#/pipelines/",
    },
    {
        path: "/databases/:databaseId/pipelines/:pipelineId/templates/:templateId",
        Page: TemplateBuilderPage,
        active: "#/pipelines/",
    },
    {
        path: "/databases/:databaseId/pipelines/:pipelineId/templates",
        Page: TemplateListPage,
        active: "#/pipelines/",
    },
    {
        path: "/databases/:databaseId/pipelines/:pipelineId",
        Page: PipelineBuilderPage,
        active: "#/pipelines/",
    },
    {
        path: "/databases/:databaseId/workflows",
        Page: WorkflowsPage2,
        active: "#/workflows/",
    },
    { path: "/workflows", Page: WorkflowsPage2, active: "#/workflows/" },
    { path: "/executions", Page: ExecutionsPage, active: "#/executions/" },
    { path: "/executions/:executionId", Page: ExecutionDetail, active: "#/executions/" },
    {
        path: "/databases/:databaseId/workflows/:workflowId/triggers",
        Page: WorkflowTriggersPage,
        active: "#/workflows/",
    },
    {
        path: "/databases/:databaseId/workflows/:workflowId",
        Page: WorkflowBuilderPage,
        active: "#/workflows/",
    },
    {
        // Workflows are database-scoped, so the builder needs a :databaseId. The unscoped create
        // path serves the workflows list, whose create action picks a database first.
        path: "/workflows/create",
        Page: WorkflowsPage2,
        active: "#/workflows/",
    },
    {
        path: "/databases/:databaseId/workflows/create",
        Page: WorkflowBuilderPage,
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
        // Tag administration is scoped to one database (or GLOBAL), carried in the path the same way
        // the metadata-schema page carries it.
        path: "/auth/tags/:databaseId",
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
        path: "/auth/api-keys",
        Page: ApiKeys,
        active: "#/auth/api-keys/",
    },
    {
        path: "/metadataschema/:databaseId",
        Page: MetadataSchema,
        active: "#/metadataschema/",
    },
    {
        path: "/metadataschema",
        Page: MetadataSchema,
        active: "#/metadataschema/",
    },
    {
        path: "/databases/:databaseId/compliance",
        Page: DatabaseCompliance,
        active: "#/databases/",
    },
    {
        path: "/compliance/schemas",
        Page: ComplianceSchemas,
        active: "#/compliance/schemas/",
    },
    {
        path: "/compliance/quarantine",
        Page: ComplianceQuarantine,
        active: "#/compliance/quarantine/",
    },
    {
        path: "/compliance/cascades",
        Page: ComplianceCascades,
        active: "#/compliance/cascades/",
    },
    {
        path: "/compliance/audit",
        Page: ComplianceAuditLog,
        active: "#/compliance/audit/",
    },
    {
        path: "*",
        Page: LandingPage,
        active: "",
    },
];

/** Core routes, then the add-ons' (addons/addonRoutes.tsx), then a fork's (custom/customRoutes.tsx). */
export const routeTable: RouteOption[] = [
    ...coreRouteTable,
    ...addonRouteTable,
    ...customRouteTable,
];

/**
 * The routes of `routeTable` that exist on a deployment with these feature switches (the
 * `featuresEnabled` list of /api/secure-config): every route whose `featuresEnabledRestriction` the
 * deployment meets. The switches only decide which pages exist; whether a user may open one is still
 * the web-route check's answer.
 */
export function routeTableFor(enabledFeatures: string[], routes: RouteOption[] = routeTable) {
    return routes.filter((route) =>
        (route.featuresEnabledRestriction ?? []).every((feature) =>
            enabledFeatures.includes(feature)
        )
    );
}

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

/**
 * Rendered in place of a page whose render threw. Sits inside AppLayout's content slot so
 * the header, side navigation and footer stay on screen and the user keeps a way out.
 * A page whose code chunk could not be downloaded gets its own message; its Reload clears the
 * automatic-reload guard so the reload can recover it again later.
 */
export function PageErrorPanel({
    onHome,
    error = null,
}: {
    onHome: () => void;
    error?: Error | null;
}) {
    if (isChunkLoadError(error)) {
        return (
            <Box padding="l">
                <Alert
                    type="error"
                    statusIconAriaLabel="Error"
                    header="This page could not be downloaded"
                    action={
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button onClick={onHome}>Go to Home</Button>
                            <Button onClick={() => reloadPage()}>Reload</Button>
                        </SpaceBetween>
                    }
                >
                    Part of the application could not be downloaded, usually because the network
                    connection was interrupted or the application was updated. Reload the page to
                    try again.
                </Alert>
            </Box>
        );
    }
    return (
        <Box padding="l">
            <Alert
                type="error"
                statusIconAriaLabel="Error"
                header="Something went wrong on this page"
                action={
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={onHome}>Go to Home</Button>
                        <Button onClick={() => window.location.reload()}>Reload</Button>
                    </SpaceBetween>
                }
            >
                This page could not be displayed. The rest of the application is still available.
            </Alert>
        </Box>
    );
}

/**
 * Rendered in place of a page the user's role does not allow. The page's own data calls are
 * also refused by the API; this only explains why the page is not shown.
 */
export function NoAccessPanel({ onHome }: { onHome: () => void }) {
    return (
        <Box padding="l">
            <Alert
                type="warning"
                statusIconAriaLabel="Warning"
                header="You do not have access to this page"
                action={<Button onClick={onHome}>Go to Home</Button>}
            >
                Your roles do not allow this page. Contact your administrator if you need access.
            </Alert>
        </Box>
    );
}

/** Rendered for a URL that matches no page. */
export function NotFoundPanel({ onHome }: { onHome: () => void }) {
    return (
        <Box padding="l">
            <Alert
                type="info"
                statusIconAriaLabel="Info"
                header="Page not found"
                action={<Button onClick={onHome}>Go to Home</Button>}
            >
                No page exists at this address.
            </Alert>
        </Box>
    );
}

/**
 * Rendered in place of a page while the web-route permission check has failed, so access to the
 * page is not known. Retry runs the check again without a reload.
 */
export function RoutesCheckFailedPanel({
    onRetry,
    onHome,
}: {
    onRetry: () => void;
    onHome: () => void;
}) {
    return (
        <Box padding="l">
            <Alert
                type="error"
                statusIconAriaLabel="Error"
                header="Page access could not be checked"
                action={
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={onRetry}>Retry</Button>
                        <Button onClick={onHome}>Go to Home</Button>
                    </SpaceBetween>
                }
            >
                The check for which pages you can open did not complete, so this page is not shown.
            </Alert>
        </Box>
    );
}

export const AppRoutes = ({ navigationOpen, setNavigationOpen, user }: AppRoutesProps) => {
    const location = useLocation();
    const navigate = useNavigate();

    // The pages this deployment has. The switches can arrive after the first render (the secure
    // config lands after sign-in), so the table, and the check below, follow them.
    const featuresEnabled = useFeaturesEnabled();
    const featuresKey = featuresEnabled.join(",");
    // eslint-disable-next-line react-hooks/exhaustive-deps
    const activeRouteTable = useMemo(() => routeTableFor(featuresEnabled), [featuresKey]);

    const [loading, setLoading] = useState(true);
    const [checkAttempt, setCheckAttempt] = useState(0);
    const [routesCheckFailed, setRoutesCheckFailed] = useState(false);
    // A page may override its route's highlighted side-navigation href from its own resolved
    // state (layout/activeNavHref.tsx); null keeps the route's `active` value.
    const [activeNavOverride, setActiveNavOverride] = useState<string | null>(null);

    const retryRoutesCheck = () => {
        setRoutesCheckFailed(false);
        setLoading(true);
        setCheckAttempt((attempt) => attempt + 1);
    };

    //Used to detect duplicate/stacked hash route pathing and correct
    //Note: This will null out any state passing for the naviagation and may break a a state passing page if triggered
    useEffect(() => {
        const hashes = window.location.href.match(/#/g) || [];

        if (hashes.length > 1) {
            const segments = window.location.href.split("#/");
            const fragmentWeWant = segments.pop();

            console.log(
                "HashRoute Duplicate Detected, Re-routing to Last Hash:",
                `#/${fragmentWeWant}`
            );

            const url = new URL(window.location.href);
            url.hash = `#/${fragmentWeWant}`;

            window.location.href = url.toString();
        }
    }, [location, navigate]);

    const [allowedRoutes, setAllowedRoutes] = useState<string[]>(
        routeTable.map((route) => {
            return route.path;
        })
    );
    // The paths the last completed check covered. A page mounted after it (its switch arrived
    // later) shows the spinner until the check that includes it answers.
    const [checkedPaths, setCheckedPaths] = useState<string[] | null>(null);

    useEffect(() => {
        // Only the answer to the latest check is used. The check runs again when the switches
        // change or on Retry, and an earlier check can answer after a later one: its route set is
        // out of date, so its success or failure is dropped.
        let superseded = false;
        const allRoutes: WebRouteCheck[] = [];
        for (const route of activeRouteTable) {
            if (route.path) {
                allRoutes.push({
                    method: "GET",
                    route__path: route.path,
                });
            }
        }

        try {
            checkWebRoutesAllowed(allRoutes)
                .then((allowed) => {
                    if (superseded) return;
                    const allAllowedRoutes = allowed.map(
                        (allowedRoute) => allowedRoute.route__path
                    );

                    //If allowed routes doesn't contain * or / for the landing page, add that back so all users can get to the landing information page
                    if (!allAllowedRoutes.includes("/")) allAllowedRoutes.push("/");
                    if (!allAllowedRoutes.includes("*")) allAllowedRoutes.push("*");

                    setAllowedRoutes(allAllowedRoutes);
                    setCheckedPaths(allRoutes.map((route) => route.route__path));
                    setRoutesCheckFailed(false);
                    setLoading(false);
                })
                .catch((error) => {
                    if (superseded) return;
                    console.error(error);
                    // Access is unknown: "/" renders, and every other route shows the
                    // could-not-check panel with Retry.
                    setRoutesCheckFailed(true);
                    setLoading(false);
                });
        } catch (e) {}
        return () => {
            superseded = true;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [checkAttempt, featuresKey]);

    const buildRoute = (routeOptions: RouteOption, i = 0) => {
        const { path, active, Page } = routeOptions;
        return (
            <Route
                key={i}
                path={path}
                element={
                    <AppLayout
                        ariaLabels={appLayoutLabels}
                        headerSelector="#headerWrapper"
                        footerSelector="#appFooter"
                        disableContentPaddings={navigationOpen}
                        content={
                            loading ||
                            (!routesCheckFailed &&
                                checkedPaths !== null &&
                                !checkedPaths.includes(path)) ? (
                                <CenterSpinner />
                            ) : routesCheckFailed && path !== "/" ? (
                                <RoutesCheckFailedPanel
                                    onRetry={retryRoutesCheck}
                                    onHome={() => navigate("/")}
                                />
                            ) : path === "*" ? (
                                <NotFoundPanel onHome={() => navigate("/")} />
                            ) : !allowedRoutes.includes(path) ? (
                                <NoAccessPanel onHome={() => navigate("/")} />
                            ) : (
                                // Suspense catches the lazy-load promise, not a render error.
                                // Without a boundary here an uncaught error in any page
                                // unmounts the whole root and leaves a blank screen.
                                <ErrorBoundary
                                    fallback={(error) => (
                                        <PageErrorPanel
                                            onHome={() => navigate("/")}
                                            error={error}
                                        />
                                    )}
                                >
                                    <Suspense fallback={<CenterSpinner />}>
                                        <Page />
                                    </Suspense>
                                </ErrorBoundary>
                            )
                        }
                        navigation={
                            <Navigation
                                key={checkAttempt}
                                activeHref={activeNavOverride ?? active}
                                user={user}
                            />
                        }
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

    // Every route this deployment has is mounted, so a URL the role does not allow reaches the
    // no-access panel, and an unknown URL, or the page of an add-on the deployment does not run, the
    // not-found panel.
    return (
        <ActiveNavHrefProvider setActiveNavHref={setActiveNavOverride}>
            <Routes>{activeRouteTable.map(buildRoute)}</Routes>
        </ActiveNavHrefProvider>
    );
};
