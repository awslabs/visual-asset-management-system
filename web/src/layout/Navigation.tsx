/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo } from "react";
import { useState } from "react";
import { SideNavigation, Spinner } from "@cloudscape-design/components";
import Button from "@cloudscape-design/components/button";
import { checkWebRoutesAllowed } from "../services/webRoutesCheck";
import config from "../config";
import Synonyms from "../synonyms";
import { useFeaturesEnabled } from "../context/RuntimeConfigContext";
import { addonNavAdminSections, addonNavMainSections } from "../addons/addonNavigation";
import { customNavSections } from "../custom/customNavigation";

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

/**
 * Recursively drops the entries (sections, links, expandable link groups) whose
 * `featuresEnabledRestriction` names a feature switch the deployment has not enabled; an entry
 * shows only when every switch it lists is on. The property is stripped from the entries kept,
 * since SideNavigation does not take it.
 */
function restrictNavItemsByFeatures(items: any[], enabledFeatures: string[]): any[] {
    const result: any[] = [];
    for (const { featuresEnabledRestriction, ...item } of items) {
        const restriction: string[] = Array.isArray(featuresEnabledRestriction)
            ? featuresEnabledRestriction
            : [];
        if (!restriction.every((feature) => enabledFeatures.includes(feature))) {
            continue;
        }
        result.push(
            Array.isArray(item.items)
                ? { ...item, items: restrictNavItemsByFeatures(item.items, enabledFeatures) }
                : item
        );
    }
    return result;
}

/**
 * A nav href as the route path the router checks: "#/databases/" becomes "/databases".
 * The root "/" keeps its slash.
 */
function normaliseHref(href: string): string {
    const path = href.replace("#", "");
    return path.length > 1 ? path.replace(/\/+$/, "") : path;
}

/**
 * Recursively collects all link hrefs from navigation items,
 * supporting both flat items arrays and nested expandable-link-group items.
 */
function collectRoutes(items: any[]): any[] {
    const routes: any[] = [];
    for (const item of items) {
        if (item.type === "link" && item.href) {
            routes.push({
                method: "GET",
                route__path: normaliseHref(item.href),
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
function filterNavItems(items: any[], allowedRoutes: any[]): any[] {
    const result: any[] = [];
    for (const item of items) {
        if (item.type === "divider") {
            result.push(item);
            continue;
        }
        if (item.type === "section" || item.type === "expandable-link-group") {
            const filteredChildren = (item.items || []).filter((child: any) =>
                allowedRoutes.includes(normaliseHref(child.href))
            );
            if (filteredChildren.length > 0) {
                result.push({ ...item, items: filteredChildren });
            }
            continue;
        }
        if (item.type === "link" && item.href) {
            if (allowedRoutes.includes(normaliseHref(item.href))) {
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

/**
 * The side-navigation entries. Any entry may carry `featuresEnabledRestriction: string[]`, the
 * feature switches that must all be enabled for it to show.
 */
function navItems(): any[] {
    return [
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
                { type: "link", text: `${Synonyms.Assets} and Files`, href: "#/assets/" },
                { type: "link", text: `Create ${Synonyms.Asset}`, href: "#/upload/" },
            ],
        },
        {
            type: "section",
            text: "Orchestrate & Automate",
            items: [
                { type: "link", text: "Pipelines", href: "#/pipelines/" },
                { type: "link", text: "Workflows", href: "#/workflows/" },
                { type: "link", text: "Executions", href: "#/executions/" },
            ],
        },
        // The add-ons' main-menu sections (addons/addonNavigation.ts)
        ...addonNavMainSections,
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
            ],
        },
        // The add-ons' admin-area sections (addons/addonNavigation.ts)
        ...addonNavAdminSections,
        {
            type: "section",
            text: "User",
            items: [{ type: "link", text: "API Key Management", href: "#/auth/api-keys/" }],
        },
        // A fork's sections (custom/customNavigation.ts, shipped empty)
        ...customNavSections,
    ];
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
    const filteredNavItems = useMemo(
        () => restrictNavItemsByFeatures(navItems(), JSON.parse(featuresKey)),
        [featuresKey]
    );

    const [navigationItems, setNavigationItems] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [loadFailed, setLoadFailed] = useState(false);
    const [loadAttempt, setLoadAttempt] = useState(0);

    // Re-checked whenever the item list changes (a feature switch arriving after mount adds links
    // that were never checked). A check that resolves after a newer one started is discarded.
    useEffect(() => {
        let stale = false;
        const allRoutes = collectRoutes(filteredNavItems);

        try {
            checkWebRoutesAllowed(allRoutes)
                .then((allowed) => {
                    if (stale) return;
                    const allowedRoutes = allowed.map((r) => r.route__path);

                    const filtered = filterNavItems(filteredNavItems, allowedRoutes);
                    setNavigationItems(filtered);
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
    }, [filteredNavItems, loadAttempt]);

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
            items={navigationItems}
            activeHref={activeHref}
            onFollow={onFollowHandler}
        />
    );
}
