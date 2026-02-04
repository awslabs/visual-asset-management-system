/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect } from "react";
import { useState } from "react";
import { SideNavigation, Spinner } from "@cloudscape-design/components";
import { webRoutes } from "../services/APIService";
import config from "../config";
import Synonyms from "../synonyms";

const navHeader = {
    href: "/",
};

if (config.CUSTOMER_LOGO) {
    navHeader.logo = { alt: "logo", src: config.CUSTOMER_LOGO };
}

const defaultOnFollowHandler = (ev) => {};

function CenterSpinner() {
    return (
        <div
            aria-live="polite"
            aria-label="Loading page content."
            style={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                height: "100%",
            }}
        >
            <Spinner size="large" />
        </div>
    );
}

export function Navigation({
    activeHref,
    header = navHeader,
    onFollowHandler = defaultOnFollowHandler,
    user,
}) {
    let filteredNavItems = [
        {
            type: "section",
            text: "Manage",
            items: [
                { type: "link", text: Synonyms.Databases, href: "#/databases/" },
                { type: "link", text: "Assets and Files", href: "#/assets/" },
                { type: "link", text: `Create ${Synonyms.Asset}`, href: "#/upload/" },
            ],
        },
        // {
        //     type: "section",
        //     role: "assets",
        //     text: "Visualize",
        //     items: [
        //         { type: "link", text: "3D Model Viewer", href: "#/visualizers/model" },
        //         { type: "link", text: "3D Point Cloud Viewer", href: "#/visualizers/pc" },
        //         { type: "link", text: "3D Plotter", href: "#/visualizers/plot" },
        //         { type: "link", text: "Columnar Viewer", href: "#/visualizers/column" },
        //     ],
        // },
        // {
        //     type: "section",
        //     text: "Transform",
        //     items: [],
        // },
        {
            type: "section",
            text: "Orchestrate & Automate",
            items: [
                { type: "link", text: "Pipelines", href: "#/pipelines/" },
                { type: "link", text: "Workflows", href: "#/workflows/" },
            ],
        },
        {
            type: "divider",
        },
        {
            type: "section",
            text: "Admin",
            items: [
                { type: "link", text: "Access Control Contraints", href: "#/auth/constraints/" },
                {
                    type: "link",
                    text: "Roles",
                    href: "#/auth/roles/",
                },
                {
                    type: "link",
                    text: "Users in Roles",
                    href: "#/auth/userroles/",
                },
                ...(!window.DISABLE_COGNITO
                    ? [
                          {
                              type: "link",
                              text: "Cognito User Management",
                              href: "#/auth/cognitousers/",
                          },
                      ]
                    : []),
                {
                    type: "link",
                    text: "Metadata Schema",
                    href: "#/metadataschema/",
                },
                {
                    type: "link",
                    text: "Tags Management",
                    href: "#/auth/tags/",
                },
                {
                    type: "link",
                    text: "Subscription Management",
                    href: "#/auth/subscriptions/",
                },
                {
                    type: "link",
                    text: "Asset Ingestion",
                    href: "#/assetIngestion",
                },
            ],
        },
    ];
    const [navigationItems, setNavigationItems] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let allowedRoutes = [];

        let allRoutes = [];
        for (let navigationItem of filteredNavItems) {
            if (navigationItem.items) {
                for (let item of navigationItem.items) {
                    allRoutes.push({
                        method: "GET",
                        route__path: item.href.replace("#", ""),
                    });
                }
            }
        }
        try {
            webRoutes({ routes: allRoutes })
                .then((value) => {
                    if (value[0] === false) {
                        throw new Error("webRoutes - " + value[1]);
                    }

                    for (let allowedRoute of value.allowedRoutes) {
                        allowedRoutes.push("#" + allowedRoute.route__path);
                    }

                    for (let navigationItem of filteredNavItems) {
                        if (navigationItem.items) {
                            navigationItem.items = navigationItem.items.filter((item) => {
                                return allowedRoutes.includes(item.href);
                            });
                        }
                    }
                    filteredNavItems = filteredNavItems.filter((navigationItem) => {
                        return navigationItem.items?.length > 0;
                    });
                    setNavigationItems(filteredNavItems);
                    setLoading(false);
                })
                .catch((error) => {
                    console.error(error);
                    setNavigationItems([]);
                    setLoading(false);
                });
        } catch (e) {}
    }, []);

    return loading ? (
        <CenterSpinner />
    ) : navigationItems.length === 0 ? (
        <div
            style={{
                padding: "20px",
                textAlign: "center",
                color: "#5f6b7a",
            }}
        >
            <div style={{ marginBottom: "10px", fontSize: "16px", fontWeight: "bold" }}>
                No Access
            </div>
            <div style={{ fontSize: "14px", lineHeight: "1.5" }}>
                You don't have access to any web navigation pages. Please contact your administrator
                to request the necessary permissions.
            </div>
        </div>
    ) : (
        <SideNavigation
            header={config.CUSTOMER_LOGO ? navHeader : null}
            items={navigationItems}
            activeHref={activeHref}
            onFollow={onFollowHandler}
        />
    );
}
