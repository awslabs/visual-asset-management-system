/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Where the core documentation may name the Miris Spatial Streaming add-on. The add-on's pages are in
 * `docs/developer/addon/miris-spatial-streaming/`; a core page names the add-on only in a registry
 * that lists every add-on or viewer, or on a page a documentation rule requires it on.
 * infra/test/docs/coreDocsAddonPlacement.test.ts applies this descriptor to every core page.
 */

import type { AddonDocsAllowance } from "../../../docs/support/addonDocsPlacement";

export const addon = "Miris Spatial Streaming";

export const pageDir = "developer/addon/miris-spatial-streaming";

export const identifiers: RegExp[] = [
    // API routes and source paths: /addon/miris/..., handlers/addon/miris/.
    /\baddons?\/miris\//,
    // Configuration keys, feature switches, environment variables, resources and metadata keys.
    /\buseMirisStreaming\b/,
    /\bMIRIS_[A-Z_]+/,
    /\bmiris[A-Z]\w*/,
    /\bMiris[A-Z]\w*/,
    /\bmiris_\w+/,
    /\bmiris-(stream-viewer|upload|stream-file-schema)\b/,
    // Links into the add-on's pages.
    /\bmiris-spatial-streaming\//,
];

export const allowed: AddonDocsAllowance[] = [
    {
        page: "developer/addons-overview.md",
        sections: ["## Current add-ons"],
        reason: "Registry: the add-on's row in the table of add-ons.",
    },
    {
        page: "additional/partner-integrations.md",
        sections: ["## Licensed Commercial Integrations", "#### Miris Spatial Streaming"],
        reason: "Registry: the add-on's partner integration row and entry.",
    },
    {
        page: "api/addon.md",
        sections: ["## Miris Spatial Streaming add-on"],
        reason: "Rule-mandated: every add-on route is documented on the add-on API reference page.",
    },
    {
        page: "deployment/configuration-reference.md",
        sections: [
            "### Miris Spatial Streaming (`app.addons.useMirisStreaming`)",
            "#### Restricted-partition constraints",
            "#### Subnet Requirements",
        ],
        reason: "Rule-mandated: the add-on's configuration section, and the partition and subnet rows its validation drives.",
    },
    {
        page: "concepts/permissions-model.md",
        sections: ["### Add-on routes"],
        reason: "Registry: the routes each add-on registers, with the object type each authorizes.",
    },
    {
        page: "developer/permissions.md",
        sections: ["### Available templates"],
        reason: "Registry: what each shipped permission template grants, which includes the add-on's two routes.",
    },
    {
        page: "developer/cdk.md",
        sections: ["## Feature Flags"],
        reason: "Registry: the add-on feature switches in ADDON_APP_FEATURES.",
    },
    {
        page: "developer/viewer-plugins.md",
        sections: ["### Available Feature Flags"],
        reason: "Registry: the feature switches that gate a viewer, with the viewers each gates.",
    },
    {
        page: "concepts/viewers.md",
        sections: ["## Add-on viewers"],
        reason: "Registry: the add-on viewers, with the formats each opens.",
    },
    {
        page: "additional/notices.md",
        sections: ["### Miris Spatial Streaming Notice"],
        reason: "Rule-mandated: the third-party notice for the hosted service the add-on calls.",
    },
    {
        page: "architecture/aws-resources.md",
        sections: ["## Add-on Resources"],
        reason: "Rule-mandated: the one pointer from the resource inventory to the add-on's resources page.",
    },
    {
        page: "deployment/uninstall.md",
        sections: ["## Pre-uninstall: add-ons"],
        reason: "Rule-mandated: the one pointer from the uninstall steps to the add-on's uninstall page.",
    },
    {
        page: "additional/revisions.md",
        sections: ["### 2.7.0"],
        reason: "Rule-mandated: the release notes of the release that ships the add-on.",
    },
];
