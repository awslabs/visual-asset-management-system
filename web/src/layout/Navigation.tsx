/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo } from "react";
import { useState } from "react";
import { SideNavigation, SideNavigationProps, Spinner } from "@cloudscape-design/components";
import Button from "@cloudscape-design/components/button";
import { checkWebRoutesAllowed } from "../services/webRoutesCheck";
import config from "../config";
import { useFeaturesEnabled } from "../context/RuntimeConfigContext";
import { allowedEntriesFor, webRouteChecksFor } from "./navigationRegistry";

const navHeader: { href: string; logo?: { alt: string; src: string } } = {
    href: "#/",
};

if (config.CUSTOMER_LOGO) {
    navHeader.logo = { alt: "logo", src: config.CUSTOMER_LOGO };
}

const defaultOnFollowHandler = (ev: any) => {};

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

export function Navigation({
    activeHref,
    header = navHeader,
    onFollowHandler = defaultOnFollowHandler,
    user,
}: {
    activeHref: any;
    header?: any;
    onFollowHandler?: any;
    user: any;
}) {
    // Read live: the secure config that carries featuresEnabled lands after a first login, so an
    // entry restricted to a feature switch appears when it does rather than on the next reload.
    const featuresKey = JSON.stringify(useFeaturesEnabled());
    const enabledFeatures: string[] = useMemo(() => JSON.parse(featuresKey), [featuresKey]);

    // The `web` paths the API allowed, or null until the check has succeeded. The entries are
    // filtered at render time, so a link gated on the API allowed-routes listing
    // (`requiresApiRoute`) appears once the auth flow has cached that listing.
    const [allowedPaths, setAllowedPaths] = useState<string[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [loadFailed, setLoadFailed] = useState(false);
    const [loadAttempt, setLoadAttempt] = useState(0);

    // Re-checked whenever the feature switches change (one arriving after mount adds links that were
    // never checked) or on Retry. A check that resolves after a newer one started is discarded.
    useEffect(() => {
        let stale = false;
        // The profile entries are checked in the same batched call, so the empty state can tell
        // "nothing at all" from "your pages are in the profile menu".
        const allRoutes = webRouteChecksFor(["side", "profile"], enabledFeatures);

        try {
            checkWebRoutesAllowed(allRoutes)
                .then((allowed) => {
                    if (stale) return;
                    setAllowedPaths(allowed.map((r) => r.route__path));
                    // A re-check after a failed one (a feature switch arriving) clears the failure.
                    setLoadFailed(false);
                    setLoading(false);
                })
                .catch((error) => {
                    if (stale) return;
                    console.error(error);
                    setLoadFailed(true);
                    setLoading(false);
                });
        } catch (e) {}
        return () => {
            stale = true;
        };
    }, [enabledFeatures, loadAttempt]);

    const navigationItems = allowedPaths
        ? allowedEntriesFor("side", allowedPaths, enabledFeatures)
        : [];
    const hasProfilePages =
        allowedPaths !== null &&
        allowedEntriesFor("profile", allowedPaths, enabledFeatures).length > 0;

    const retryLoad = () => {
        setLoadFailed(false);
        setLoading(true);
        setLoadAttempt((attempt) => attempt + 1);
    };

    return loading ? (
        <CenterSpinner />
    ) : loadFailed ? (
        <div
            style={{
                padding: "20px",
                textAlign: "center",
                color: "var(--vams-text-secondary)",
            }}
        >
            <div style={{ marginBottom: "10px", fontSize: "16px", fontWeight: "bold" }}>
                Navigation could not be loaded
            </div>
            <div style={{ marginBottom: "10px", fontSize: "14px", lineHeight: "1.5" }}>
                The check for which pages you can open did not complete.
            </div>
            <Button onClick={retryLoad}>Retry</Button>
        </div>
    ) : navigationItems.length === 0 && hasProfilePages ? (
        <div
            style={{
                padding: "20px",
                textAlign: "center",
                color: "var(--vams-text-secondary)",
            }}
        >
            <div style={{ marginBottom: "10px", fontSize: "16px", fontWeight: "bold" }}>
                Your pages are in the profile menu
            </div>
            <div style={{ fontSize: "14px", lineHeight: "1.5" }}>
                Open the menu with your user name in the top bar to reach them.
            </div>
        </div>
    ) : navigationItems.length === 0 ? (
        <div
            style={{
                padding: "20px",
                textAlign: "center",
                color: "var(--vams-text-secondary)",
            }}
        >
            <div style={{ marginBottom: "10px", fontSize: "16px", fontWeight: "bold" }}>
                No Access
            </div>
            <div style={{ fontSize: "14px", lineHeight: "1.5" }}>
                You don't have access to any web navigation pages. Please contact your administrator
                to request the necessary permissions.
            </div>
        </div>
    ) : (
        <SideNavigation
            header={config.CUSTOMER_LOGO ? (navHeader as any) : undefined}
            items={navigationItems as SideNavigationProps.Item[]}
            activeHref={activeHref}
            onFollow={onFollowHandler}
        />
    );
}
