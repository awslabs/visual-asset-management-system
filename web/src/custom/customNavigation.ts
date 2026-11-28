/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here
 * do not conflict on upgrade.
 *
 * Navigation sections for the pages a fork adds. layout/navigationRegistry.ts places them after the
 * core and add-on sections and filters them by the same `web`-constraint check. A top-level entry goes
 * in the side navigation, or in the top-bar profile menu with `placement: "profile"`; a link can set
 * `requiresApiRoute`, and any entry `featuresEnabledRestriction` (navigationRegistry.ts documents
 * both). Each entry has the Cloudscape SideNavigation shape the core sections use, for example
 * `{ type: "section", text: "...", items: [{ type: "link", text: "...", href: "#/custom/.../" }] }`;
 * a link's href is its route path from customRoutes.tsx with a leading `#` and a trailing `/`.
 */

export const customNavSections: any[] = [];
