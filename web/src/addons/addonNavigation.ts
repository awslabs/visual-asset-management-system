/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Side-navigation sections for the pages the optional add-ons add (addons/addonRoutes.tsx).
 * Upstream-owned; no add-on ships a page today. layout/Navigation.tsx appends these after the core
 * sections and before the fork's (custom/customNavigation.ts), and filters them by the same
 * `web`-constraint check. Each entry has the Cloudscape SideNavigation shape the core sections use;
 * a link's href is its route path with a leading `#` and a trailing `/`.
 */

export const addonNavSections: any[] = [];
