/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Side-navigation sections for the pages the optional add-ons add (addons/addonRoutes.tsx).
 * Upstream-owned. layout/Navigation.tsx appends these after the core sections and before the fork's
 * (custom/customNavigation.ts), and filters them by the same `web`-constraint check. Each entry has
 * the Cloudscape SideNavigation shape the core sections use; a link's href is its route path with a
 * leading `#` and a trailing `/`. A section, link or group may also carry
 * `featuresEnabledRestriction`, the feature switches (addons/addonFeatures.ts) that must all be
 * enabled for it to show; Navigation drops it otherwise and re-checks when the switches arrive.
 */

import { addonFeaturesEnabled } from "./addonFeatures";

export const addonNavSections: any[] = [
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
    {
        type: "section",
        text: "Admin - Workstations",
        featuresEnabledRestriction: [addonFeaturesEnabled.EDH_WORKSTATIONS],
        items: [{ type: "link", text: "Workstation Admin", href: "#/edh/admin/" }],
    },
];
