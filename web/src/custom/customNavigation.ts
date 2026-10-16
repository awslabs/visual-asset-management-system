/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here
 * do not conflict on upgrade.
 *
 * Side-navigation sections for the pages a fork adds, appended after the core sections in
 * layout/Navigation.tsx and filtered by the same `web`-constraint check. Each entry has the
 * Cloudscape SideNavigation shape the core sections use, for example
 * `{ type: "section", text: "...", items: [{ type: "link", text: "...", href: "#/custom/.../" }] }`;
 * a link's href is its route path from customRoutes.tsx with a leading `#` and a trailing `/`.
 */

export const customNavSections: any[] = [];
