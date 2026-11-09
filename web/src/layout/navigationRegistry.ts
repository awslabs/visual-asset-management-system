/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The one navigation registry. Every page link in the web app is declared here (core), in
 * addons/addonNavigation.ts (add-on lane) or in custom/customNavigation.ts (fork lane, shipped
 * empty), and each top-level entry renders in one placement:
 *
 * - `side` (the default): the side navigation (layout/Navigation.tsx);
 * - `profile`: the top-bar profile menu (layout/useProfileMenuItems.ts, rendered by App.tsx).
 *
 * Within each placement the order is core, then add-on, then custom. Both placements are filtered by
 * the same `web`-constraint check (`checkWebRoutesAllowed`, one batched `auth/routes` call), so
 * the client derives no permission of its own. A link may also name `requiresApiRoute`: it is then
 * shown only when the API's allowed-routes listing (`GET /auth/routes/api/allowed`, cached by the auth
 * flow) allows that method and path. The link is hidden while the listing is unknown or failed (fail
 * closed); it is advisory only, and the API still enforces every call.
 */

import Synonyms from "../synonyms";
import { addonNavSections } from "../addons/addonNavigation";
import { customNavSections } from "../custom/customNavigation";
import { isApiRouteAllowed } from "../common/constants/authRoutes";
import type { WebRouteCheck } from "../services/webRoutesCheck";

/** Where a top-level navigation entry renders. */
export type NavPlacement = "side" | "profile";

/** An API route a link needs, checked against the API's allowed-routes listing. */
export interface ApiRouteRequirement {
    method: string;
    path: string;
}

/**
 * A navigation entry: the Cloudscape SideNavigation item shape (`link`, `section`,
 * `expandable-link-group`, `divider`) plus two optional registry fields, which are stripped before an
 * entry reaches Cloudscape.
 *
 * - `placement` applies to a top-level entry; nested items follow their parent. A `profile` entry
 *   is a top-level `link` or a `section` of links (rendered as a menu group headed by its `text`);
 *   other shapes are skipped with a console warning.
 * - `requiresApiRoute` applies to a link.
 *
 * A link's href is its route path with a leading `#` and a trailing `/`; it may carry a `?query`,
 * which the route check ignores.
 */
export interface NavEntry {
    type: "link" | "section" | "expandable-link-group" | "divider" | string;
    text?: string;
    href?: string;
    items?: NavEntry[];
    placement?: NavPlacement;
    requiresApiRoute?: ApiRouteRequirement;
    [key: string]: any;
}

/**
 * The core entries. A function because `window.DISABLE_COGNITO` and feature switches are read at
 * runtime, after the auth flow has loaded the deployment's configuration.
 */
export function coreNavEntries(): NavEntry[] {
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
                {
                    // The tenant-wide view of the API keys page. The web route is the same as the
                    // profile entry's, so the admin decision comes from the API listing.
                    type: "link",
                    text: "API Keys (all users)",
                    href: "#/auth/api-keys/?mode=admin",
                    requiresApiRoute: { method: "GET", path: "/auth/api-keys" },
                },
            ],
        },
        {
            type: "section",
            text: "Your pages",
            placement: "profile",
            items: [
                {
                    type: "link",
                    text: "API Key Management",
                    href: "#/auth/api-keys/?mode=user",
                },
            ],
        },
    ];
}

/** Every entry, in registry order: core, then add-on, then custom. */
export function allNavEntries(): NavEntry[] {
    return [...coreNavEntries(), ...addonNavSections, ...customNavSections];
}

function placementOf(entry: NavEntry): NavPlacement {
    return entry.placement === "profile" ? "profile" : "side";
}

/** Profile entries already reported as unsupported, so a re-render does not repeat the warning. */
const warnedProfileEntries = new WeakSet<object>();

function isSupportedProfileEntry(entry: NavEntry): boolean {
    if (entry.type === "link" && entry.href) return true;
    if (entry.type === "section") return true;
    if (!warnedProfileEntries.has(entry)) {
        warnedProfileEntries.add(entry);
        console.warn(
            `Navigation entry "${
                entry.text ?? entry.type
            }" is skipped: the profile placement supports a link or a section of links, not "${
                entry.type
            }".`
        );
    }
    return false;
}

/** The registry's top-level entries for a placement, before any check, in registry order. */
function rawEntriesFor(placement: NavPlacement): NavEntry[] {
    return allNavEntries().filter((entry) => placementOf(entry) === placement);
}

/** Whether the API listing allows a link's `requiresApiRoute`; unknown counts as not allowed. */
function apiRequirementMet(entry: NavEntry): boolean {
    if (!entry.requiresApiRoute) return true;
    const { method, path } = entry.requiresApiRoute;
    return isApiRouteAllowed(path, method) === true;
}

/** Drop links whose API requirement is not met and strip the registry-only fields. */
function prepare(entry: NavEntry): NavEntry | null {
    if (entry.type === "link" && !apiRequirementMet(entry)) return null;
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { placement, requiresApiRoute, ...rest } = entry;
    if (Array.isArray(entry.items)) {
        rest.items = entry.items.map(prepare).filter((child): child is NavEntry => child !== null);
    }
    return rest;
}

/** A profile section renders as a menu group of links; any other child is skipped with a warning. */
function profileLinksOnly(entry: NavEntry): NavEntry {
    if (entry.type !== "section") return entry;
    const items = (entry.items ?? []).filter((child) => {
        if (child.type === "link" && child.href) return true;
        if (!warnedProfileEntries.has(child)) {
            warnedProfileEntries.add(child);
            console.warn(
                `Navigation entry "${child.text ?? child.type}" in profile section "${
                    entry.text
                }" is skipped: a profile section holds links only.`
            );
        }
        return false;
    });
    return { ...entry, items };
}

/**
 * The entries for one placement, in registry order (core, add-on, custom), ready for Cloudscape:
 * links whose `requiresApiRoute` the cached API listing does not allow are dropped (an unknown
 * listing drops them too), unsupported profile shapes are skipped with a warning, and `placement`
 * and `requiresApiRoute` are stripped. Evaluated on every call, so a listing cached after the first
 * render is picked up on the next one.
 */
export function entriesFor(placement: NavPlacement): NavEntry[] {
    const entries =
        placement === "profile"
            ? rawEntriesFor(placement).filter(isSupportedProfileEntry).map(profileLinksOnly)
            : rawEntriesFor(placement);
    return entries.map(prepare).filter((entry): entry is NavEntry => entry !== null);
}

/**
 * A nav href as the route path the router checks: "#/databases/" becomes "/databases" and
 * "#/auth/api-keys/?mode=user" becomes "/auth/api-keys". The root "/" keeps its slash.
 */
export function normaliseHref(href: string): string {
    const path = href.replace("#", "").split("?")[0];
    return path.length > 1 ? path.replace(/\/+$/, "") : path;
}

/**
 * Recursively collects all link hrefs from navigation items,
 * supporting both flat items arrays and nested expandable-link-group items.
 */
export function collectRoutes(items: any[]): WebRouteCheck[] {
    const routes: WebRouteCheck[] = [];
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
 * The `web` routes to check for the given placements, collected from every registry entry of those
 * placements (whatever their `requiresApiRoute`), without duplicates.
 */
export function webRouteChecksFor(...placements: NavPlacement[]): WebRouteCheck[] {
    const seen = new Set<string>();
    const routes: WebRouteCheck[] = [];
    for (const placement of placements) {
        for (const route of collectRoutes(rawEntriesFor(placement))) {
            const key = `${route.method} ${route.route__path}`;
            if (!seen.has(key)) {
                seen.add(key);
                routes.push(route);
            }
        }
    }
    return routes;
}

/**
 * Recursively filters navigation items based on allowed routes.
 * For expandable-link-group, filters inner items and removes the group if empty.
 * Top-level links (e.g. Home) are kept if their href is allowed.
 */
export function filterNavItems(items: any[], allowedRoutes: any[]): any[] {
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

/** The entries of a placement the API allowed: `entriesFor` filtered by the allowed `web` paths. */
export function allowedEntriesFor(placement: NavPlacement, allowedPaths: string[]): NavEntry[] {
    return filterNavItems(entriesFor(placement), allowedPaths);
}
