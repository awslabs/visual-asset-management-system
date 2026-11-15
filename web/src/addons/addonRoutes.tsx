/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Web routes for the pages the optional add-ons add. Upstream-owned. routes.tsx composes its route
 * table as core, then these, then the fork's (custom/customRoutes.tsx), so each one passes through
 * the same `web`-constraint check (checkWebRoutesAllowed) as a core page and is offered in the
 * constraint editor's route list. Pages are loaded with lazyWithRetry (utils/chunkLoadRecovery.ts)
 * like the core pages. A path that repeats an earlier route's path never replaces it: React Router
 * ranks equal paths by their order, so the core route keeps matching. A route's `active` is the href
 * of the navigation link it highlights (addons/addonNavigation.ts). Each route carries the feature
 * switches (addons/addonFeatures.ts) its add-on publishes in `featuresEnabledRestriction`: on a
 * deployment without them the route is not mounted (its URL reaches the not-found panel), not sent
 * to the web-route check and not offered in the constraint editor (routes.tsx `routeTableFor`). An
 * add-on's pages live in its own module under addons/.
 */

import type { RouteOption } from "../routes";
import { lazyWithRetry } from "../utils/chunkLoadRecovery";
import { addonFeaturesEnabled } from "./addonFeatures";

const EdhWorkstationsPage = lazyWithRetry(() => import("./edh/pages/EdhWorkstationsPage"));
const EdhAdminPage = lazyWithRetry(() => import("./edh/pages/EdhAdminPage"));

const EDH = [addonFeaturesEnabled.EDH_WORKSTATIONS];

export const addonRouteTable: RouteOption[] = [
    // EDH Workstations; the Simulations route opens the Workstations page on that tab, and exists
    // with the add-on's container jobs, as its navigation link does.
    {
        path: "/edh/workstations",
        Page: EdhWorkstationsPage,
        active: "#/edh/workstations/",
        featuresEnabledRestriction: EDH,
    },
    {
        path: "/edh/workstations/simulations",
        Page: EdhWorkstationsPage,
        active: "#/edh/workstations/simulations/",
        featuresEnabledRestriction: [...EDH, addonFeaturesEnabled.EDH_CONTAINERS],
    },
    {
        path: "/edh/admin",
        Page: EdhAdminPage,
        active: "#/edh/admin/",
        featuresEnabledRestriction: EDH,
    },
];
