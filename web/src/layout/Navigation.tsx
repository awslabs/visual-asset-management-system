/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect } from "react";
import { useState } from "react";
import { SideNavigation, Spinner } from "@cloudscape-design/components";
import { webRoutes } from "../services/APIService";
import config from "../config";
import Synonyms from "../synonyms";

const navHeader = {
    href: "/",
};

if (config.CUSTOMER_LOGO) {
    navHeader.logo = { alt: "logo", src: config.CUSTOMER_LOGO };
}

const defaultOnFollowHandler = (ev) => {};

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
 * Recursively collects all link hrefs from navigation items,
 * supporting both flat items arrays and nested expandable-link-group items.
 */
function collectRoutes(items) {
    const routes = [];
    for (const item of items) {
        if (item.type === "link" && item.href) {
            routes.push({
                method: "GET",
                route__path: item.href.replace("#", ""),
            });
        }
        if (item.type === "expandable-link-group" && item.items) {
            routes.push(...collectRoutes(item.items));
        }
        if (item.type === "section" && item.items) {
            routes.push(...collectRoutes(item.items));
        }
    }
    return routes;
}

/**
 * Recursively filters navigation items based on allowed routes.
 * For expandable-link-group, filters inner items and removes the group if empty.
 * Top-level links (e.g. Home) are kept if their href is allowed.
 */
function filterNavItems(items, allowedRoutes) {
    const result = [];
    for (const item of items) {
        if (item.type === "divider") {
            result.push(item);
            continue;
        }
        if (item.type === "section" || item.type === "expandable-link-group") {
            const filteredChildren = (item.items || []).filter((child) =>
                allowedRoutes.includes(child.href)
            );
            if (filteredChildren.length > 0) {
                result.push({ ...item, items: filteredChildren });
            }
            continue;
        }
        if (item.type === "link" && item.href) {
            if (allowedRoutes.includes(item.href)) {
                result.push(item);
            }
            continue;
        }
        // Keep other item types as-is
        result.push(item);
    }

    // Remove trailing dividers and dividers before nothing
    return result.filter((item, index, arr) => {
        if (item.type !== "divider") return true;
        // Remove divider if it's the last item or the next non-divider item doesn't exist
        const remaining = arr.slice(index + 1);
        return remaining.some((r) => r.type !== "divider");
    });
}

export function Navigation({
    activeHref,
    header = navHeader,
    onFollowHandler = defaultOnFollowHandler,
    user,
}) {
    const filteredNavItems = [
        {
            type: "link",
            text: "Home",
            href: "#/",
        },
        {
            type: "section",
            text: "Manage",
            items: [
                { type: "link", text: Synonyms.Databases, href: "#/databases/" },
                { type: "link", text: "Assets and Files", href: "#/assets/" },
                { type: "link", text: `Create ${Synonyms.Asset}`, href: "#/upload/" },
            ],
        },
        {
            type: "section",
            text: "Orchestrate & Automate",
            items: [
                { type: "link", text: "Pipelines", href: "#/pipelines/" },
                { type: "link", text: "Workflows", href: "#/workflows/" },
            ],
        },
        { type: "divider" },
        {
            type: "section",
            text: "Admin - Data",
            items: [
                { type: "link", text: "Metadata Schema", href: "#/metadataschema/" },
                { type: "link", text: "Tags Management", href: "#/auth/tags/" },
                {
                    type: "link",
                    text: "Subscription Management",
                    href: "#/auth/subscriptions/",
                },
            ],
        },
        {
            type: "section",
            text: "Admin - Auth",
            items: [
                {
                    type: "link",
                    text: "Access Control Constraints",
                    href: "#/auth/constraints/",
                },
                { type: "link", text: "Roles", href: "#/auth/roles/" },
                { type: "link", text: "Users in Roles", href: "#/auth/userroles/" },
                ...(!window.DISABLE_COGNITO
                    ? [
                          {
                              type: "link",
                              text: "User Management",
                              href: "#/auth/cognitousers/",
                          },
                      ]
                    : []),
                { type: "link", text: "API Key Management", href: "#/auth/api-keys/" },
            ],
        },
    ];

    const [navigationItems, setNavigationItems] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const allRoutes = collectRoutes(filteredNavItems);

        try {
            webRoutes({ routes: allRoutes })
                .then((value) => {
                    if (value[0] === false) {
                        throw new Error("webRoutes - " + value[1]);
                    }

                    const allowedRoutes = value.allowedRoutes.map((r) => "#" + r.route__path);

                    const filtered = filterNavItems(filteredNavItems, allowedRoutes);
                    setNavigationItems(filtered);
                    setLoading(false);
                })
                .catch((error) => {
                    console.error(error);
                    setNavigationItems([]);
                    setLoading(false);
                });
        } catch (e) {}
    }, []);

    return loading ? (
        <CenterSpinner />
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
            header={config.CUSTOMER_LOGO ? navHeader : null}
            items={navigationItems}
            activeHref={activeHref}
            onFollow={onFollowHandler}
        />
    );
}
