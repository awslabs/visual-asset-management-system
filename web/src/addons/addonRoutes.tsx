/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Web routes for the pages the optional add-ons add. Upstream-owned; no add-on ships a page today.
 * routes.tsx composes its route table as core, then these, then the fork's (custom/customRoutes.tsx),
 * so each one passes through the same `web`-constraint check (checkWebRoutesAllowed) as a core page
 * and is offered in the constraint editor's route list. Pages are loaded with React.lazy like the
 * core pages. A path that repeats an earlier route's path never replaces it: React Router ranks
 * equal paths by their order, so the core route keeps matching.
 */

import type { RouteOption } from "../routes";

export const addonRouteTable: RouteOption[] = [];
