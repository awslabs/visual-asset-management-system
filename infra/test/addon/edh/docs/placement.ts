/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Where the core documentation may name the EDH Workstations add-on's internals. The add-on's pages
 * are the seven in `docs/developer/edh-workstations/`; a core page names the identifiers below only in
 * a registry that lists every add-on or on a page a documentation rule requires them on.
 * infra/test/docs/coreDocsAddonPlacement.test.ts applies this descriptor to every core page.
 */

import type { AddonDocsAllowance } from "../../../docs/support/addonDocsPlacement";

export const addon = "EDH Workstations";

export const pageDir = "developer/edh-workstations";

export const identifiers: RegExp[] = [
    // API routes and source paths: /addon/edh/..., handlers/addon/edh/, web/src/addons/edh/.
    /\baddons?\/edh\b/,
    /\bbackendAddons\/edh\b/,
    // Configuration keys, object types, constraint fields, tables, functions and stack outputs.
    /\buseEdhWorkstations\b/,
    /\bedh[A-Z]\w*/,
    /\bEdh[A-Z]\w*/,
    /\bEDH_[A-Z_]+/,
    /\bedh_\w+|\w_edh_\w*/,
    /\baddon_edhWorkstations\w*/,
    // Session keys and secrets.
    /temporary-session-edh/,
    /\bedh-session\b/,
    // Web pages and their navigation.
    /#\/edh\//,
    /Workstation Admin|Virtual Workstations|Admin - Workstations|My Workstations/,
    // Permission templates.
    /\bedh-(admin|user)\.json\b/,
    // CLI command groups.
    /\bvamscli edh\b/,
    /\bedh (workstation|catalog|admin|container)\b/,
    // Links into the add-on's pages.
    /\bedh-workstations\//,
];

export const allowed: AddonDocsAllowance[] = [
    {
        page: "developer/addons-overview.md",
        sections: ["## Current add-ons"],
        reason: "Registry: the add-on's row in the table of add-ons.",
    },
    {
        page: "additional/partner-integrations.md",
        sections: ["### Cloud Workstations"],
        reason: "Registry: the add-on's partner integration entry.",
    },
    {
        page: "cli/command-reference.md",
        sections: ["## Command Groups"],
        reason: "Registry: the row of the add-on's CLI command groups.",
    },
    {
        page: "api/addon.md",
        sections: ["## EDH Workstations add-on"],
        reason: "Rule-mandated: every add-on route is documented on the add-on API reference page.",
    },
    {
        page: "deployment/configuration-reference.md",
        sections: [
            "### EDH Workstations (`app.addons.useEdhWorkstations`)",
            "#### Restricted-partition constraints",
            "#### Subnet Requirements",
            "### AWS GovCloud deployment",
        ],
        reason: "Rule-mandated: the add-on's configuration section, and the partition, subnet and VPC rows its validation drives.",
    },
    {
        page: "additional/notices.md",
        sections: ["### EDH Workstations Add-on Notice"],
        reason: "Rule-mandated: the third-party notice for the software the add-on's recipes install.",
    },
    {
        page: "additional/revisions.md",
        sections: ["### 2.7.0"],
        reason: "Rule-mandated: the release notes of the release that ships the add-on.",
    },
];
