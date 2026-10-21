/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Web routes for the pages the optional add-ons add. Upstream-owned. routes.tsx composes its route
 * table as core, then these, then the fork's (custom/customRoutes.tsx), so each one passes through
 * the same `web`-constraint check (checkWebRoutesAllowed) as a core page and is offered in the
 * constraint editor's route list. Pages are loaded with React.lazy like the core pages. A path that
 * repeats an earlier route's path never replaces it: React Router ranks equal paths by their order,
 * so the core route keeps matching. A route's `active` is the href of the navigation link it
 * highlights (addons/addonNavigation.ts). An add-on's pages live in its own module under addons/.
 */

import React from "react";
import type { RouteOption } from "../routes";

const EdhWorkstationsPage = React.lazy(() => import("./edh/pages/EdhWorkstationsPage"));
const EdhAdminPage = React.lazy(() => import("./edh/pages/EdhAdminPage"));

export const addonRouteTable: RouteOption[] = [
    // EDH Workstations; the Simulations route opens the Workstations page on that tab.
    { path: "/edh/workstations", Page: EdhWorkstationsPage, active: "#/edh/workstations/" },
    {
        path: "/edh/workstations/simulations",
        Page: EdhWorkstationsPage,
        active: "#/edh/workstations/simulations/",
    },
    { path: "/edh/admin", Page: EdhAdminPage, active: "#/edh/admin/" },
];
