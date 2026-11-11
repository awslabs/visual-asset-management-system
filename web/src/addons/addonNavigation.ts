/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Navigation entries for the pages the optional add-ons add (addons/addonRoutes.tsx).
 * Upstream-owned. The navigation registry (layout/navigationRegistry.ts) places each list in the
 * side navigation:
 *
 * - `addonNavMainSections`: the main menu, after the core "Orchestrate & Automate" section and
 *   ahead of the divider, for pages every user works in.
 * - `addonNavAdminSections`: the admin and settings area below the divider, after the core
 *   "Admin - ..." sections, for administration pages.
 *
 * The fork's entries (custom/customNavigation.ts) still come after everything. Both lists are
 * filtered by the same `web`-constraint check as the core entries. Each entry has the Cloudscape
 * SideNavigation shape the core sections use (the `NavEntry` type); a link's href is its route path
 * with a leading `#` and a trailing `/`.
 *
 * An entry in either list renders in the side navigation unless it sets `placement: "profile"`,
 * which puts it in the top-bar profile menu, after the core profile entries: a top-level link, or a
 * section of links shown as a menu group headed by the section's text. A user's own pages go there.
 * A link may also set `requiresApiRoute: { method, path }` to be shown only when the API's
 * allowed-routes listing allows that route (advisory; the API still enforces it). A section, link or
 * group may also carry `featuresEnabledRestriction`, the feature switches (addons/addonFeatures.ts)
 * that must all be enabled for it to show; the registry drops it otherwise, and the navigation
 * re-checks when the switches arrive.
 */

import { addonFeaturesEnabled } from "./addonFeatures";

/** Main-menu sections, placed after the core "Orchestrate & Automate" section. */
export const addonNavMainSections: any[] = [
    {
        type: "section",
        text: "Virtual Workstations",
        featuresEnabledRestriction: [addonFeaturesEnabled.EDH_WORKSTATIONS],
        items: [
            { type: "link", text: "My Workstations", href: "#/edh/workstations/" },
            {
                type: "link",
                text: "Simulations",
                href: "#/edh/workstations/simulations/",
                featuresEnabledRestriction: [
                    addonFeaturesEnabled.EDH_WORKSTATIONS,
                    addonFeaturesEnabled.EDH_CONTAINERS,
                ],
            },
        ],
    },
];

/** Admin-area sections, placed after the core admin sections. */
export const addonNavAdminSections: any[] = [
    {
        type: "section",
        text: "Admin - Workstations",
        featuresEnabledRestriction: [addonFeaturesEnabled.EDH_WORKSTATIONS],
        items: [{ type: "link", text: "Workstation Admin", href: "#/edh/admin/" }],
    },
];
