/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Side-navigation sections for the pages the optional add-ons add (addons/addonRoutes.tsx).
 * Upstream-owned. An add-on section goes in one of two places, and layout/Navigation.tsx splices
 * each list into the core sections there:
 *
 * - `addonNavMainSections`: the main menu, after the core "Orchestrate & Automate" section and
 *   ahead of the divider, for pages every user works in.
 * - `addonNavAdminSections`: the admin and settings area below the divider, after the core
 *   "Admin - ..." sections and ahead of "User", for administration pages.
 *
 * The fork's sections (custom/customNavigation.ts) still come after everything. Both lists are
 * filtered by the same `web`-constraint check as the core sections. Each entry has the Cloudscape
 * SideNavigation shape the core sections use; a link's href is its route path with a leading `#`
 * and a trailing `/`. A section, link or group may also carry `featuresEnabledRestriction`, the
 * feature switches (addons/addonFeatures.ts) that must all be enabled for it to show; Navigation
 * drops it otherwise and re-checks when the switches arrive.
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

/** Admin-area sections, placed after the core admin sections and before "User". */
export const addonNavAdminSections: any[] = [
    {
        type: "section",
        text: "Admin - Workstations",
        featuresEnabledRestriction: [addonFeaturesEnabled.EDH_WORKSTATIONS],
        items: [{ type: "link", text: "Workstation Admin", href: "#/edh/admin/" }],
    },
];
