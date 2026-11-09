/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect } from "react";
import { useState } from "react";
import { SideNavigation, SideNavigationProps, Spinner } from "@cloudscape-design/components";
import Button from "@cloudscape-design/components/button";
import { checkWebRoutesAllowed } from "../services/webRoutesCheck";
import config from "../config";
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
    // The `web` paths the API allowed, or null until the check has succeeded. The entries are
    // filtered at render time, so a link gated on the API allowed-routes listing
    // (`requiresApiRoute`) appears once the auth flow has cached that listing.
    const [allowedPaths, setAllowedPaths] = useState<string[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [loadFailed, setLoadFailed] = useState(false);
    const [loadAttempt, setLoadAttempt] = useState(0);

    useEffect(() => {
        // The profile entries are checked in the same batched call, so the empty state can tell
        // "nothing at all" from "your pages are in the profile menu".
        const allRoutes = webRouteChecksFor("side", "profile");

        try {
            checkWebRoutesAllowed(allRoutes)
                .then((allowed) => {
                    setAllowedPaths(allowed.map((r) => r.route__path));
                    setLoading(false);
                })
                .catch((error) => {
                    console.error(error);
                    setLoadFailed(true);
                    setLoading(false);
                });
        } catch (e) {}
    }, [loadAttempt]);

    const navigationItems = allowedPaths ? allowedEntriesFor("side", allowedPaths) : [];
    const hasProfilePages =
        allowedPaths !== null && allowedEntriesFor("profile", allowedPaths).length > 0;

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
