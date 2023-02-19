/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { SideNavigation } from "@cloudscape-design/components";
import config from "../config";

const navHeader = {
  href: "/",
};

if (config.CUSTOMER_LOGO) {
  navHeader.logo = { alt: "logo", src: config.CUSTOMER_LOGO };
}

let navItems = [
  {
    type: "section",
    text: "Manage",
    items: [
      { type: "link", text: "Databases", href: "/databases" },
      { type: "link", text: "Assets", href: "/assets" },
      { type: "link", text: "Upload Asset", href: "/upload" },
    ],
  },
  {
    type: "section",
    text: "Visualize",
    items: [
      { type: "link", text: "3D Model Viewer", href: "/visualizers/3d" },
      { type: "link", text: "3D Plotter", href: "/visualizers/plot" },
      { type: "link", text: "Columnar Viewer", href: "/visualizers/column" },
    ],
  },
  {
    type: "section",
    text: "Transform",
    items: [{ type: "link", text: "Pipelines", href: "/pipelines" }],
  },
  {
    type: "section",
    text: "Orchestrate & Automate",
    items: [{ type: "link", text: "Workflows", href: "/workflows" }],
  },
  {
    type: "divider",
  },
  {
    type: "link",
    text: config.CUSTOMER_NAME || "Visual Asset Management System(VAMS)",
  },
];

const defaultOnFollowHandler = (ev) => {};

export function Navigation({
  activeHref,
  header = navHeader,
  items = navItems,
  onFollowHandler = defaultOnFollowHandler,
}) {
  return (
    <SideNavigation
      header={config.CUSTOMER_LOGO ? navHeader : null}
      items={items}
      activeHref={activeHref}
      onFollow={onFollowHandler}
    />
  );
}
