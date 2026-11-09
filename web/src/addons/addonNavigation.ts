/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Navigation entries for the pages the optional add-ons add (addons/addonRoutes.tsx).
 * Upstream-owned; no add-on ships a page today. The navigation registry
 * (layout/navigationRegistry.ts) places these after the core entries and before the fork's
 * (custom/customNavigation.ts), and filters them by the same `web`-constraint check. Each entry has
 * the Cloudscape SideNavigation shape the core sections use (the `NavEntry` type); a link's href is
 * its route path with a leading `#` and a trailing `/`.
 *
 * An entry renders in the side navigation unless it sets `placement: "profile"`, which puts it in
 * the top-bar profile menu: a top-level link, or a section of links shown as a menu group headed
 * by the section's text. A link may also set `requiresApiRoute: { method, path }` to be shown only
 * when the API's allowed-routes listing allows that route (advisory; the API still enforces it).
 */

export const addonNavSections: any[] = [];
