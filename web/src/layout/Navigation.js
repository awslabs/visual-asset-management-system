/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { SideNavigation } from "@cloudscape-design/components";
import config from "../config";
import Synonyms from "../synonyms";

const navHeader = {
    href: "/",
};

if (config.CUSTOMER_LOGO) {
    navHeader.logo = { alt: "logo", src: config.CUSTOMER_LOGO };
}

let navItems = [
    {
        type: "section",
        role: "assets",
        text: "Manage",
        items: [
            { type: "link", text: Synonyms.Databases, href: "/databases" },
            { type: "link", text: Synonyms.Assets, href: "/assets" },
            { type: "link", text: `Upload ${Synonyms.Asset}`, href: "/upload" },
            { type: "link", text: `Search`, href: "/search" },
        ],
    },
    {
        type: "section",
        role: "assets",
        text: "Visualize",
        items: [
            { type: "link", text: "3D Model Viewer", href: "/visualizers/model" },
            { type: "link", text: "3D Plotter", href: "/visualizers/plot" },
            { type: "link", text: "Columnar Viewer", href: "/visualizers/column" },
        ],
    },
    {
        type: "section",
        role: "pipelines",
        text: "Transform",
        items: [{ type: "link", text: "Pipelines", href: "/pipelines" }],
    },
    {
        type: "section",
        role: "workflows",
        text: "Orchestrate & Automate",
        items: [{ type: "link", text: "Workflows", href: "/workflows" }],
    },
    {
        type: "divider",
        role: "super-admin",
    },
    {
        type: "section",
        role: "super-admin",
        text: "Admin",
        items: [
            { type: "link", text: "Fine Grained Access Controls", href: "/auth/constraints" },
            {
                type: "link",
                text: "Metadata Schema",
                href: "/metadataschema/create",
            },
        ],
    },
];

const defaultOnFollowHandler = (ev) => {};

export function Navigation({
    activeHref,
    header = navHeader,
    items = navItems,
    onFollowHandler = defaultOnFollowHandler,
    user,
}) {
    let roles = [];
    try {
        roles = JSON.parse(user.signInUserSession.idToken.payload["vams:roles"]);
    } catch (e) {}
    return (
        <SideNavigation
            header={config.CUSTOMER_LOGO ? navHeader : null}
            items={items.filter((item) => {
                return (
                    item.role === undefined ||
                    roles.includes(item.role) ||
                    roles.includes("super-admin")
                );
            })}
            activeHref={activeHref}
            onFollow={onFollowHandler}
        />
    );
}
