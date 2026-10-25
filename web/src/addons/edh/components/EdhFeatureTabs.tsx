/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import TabStrip from "./TabStrip";

export type EdhFeatureTabKey = "workstations" | "simulations";

const TABS: Array<{ key: EdhFeatureTabKey; label: string }> = [
    { key: "workstations", label: "Workstations" },
    { key: "simulations", label: "Simulations" },
];

/** The DOM id of a feature tab; the page's panel names the active one. */
export const edhFeatureTabId = (key: EdhFeatureTabKey) => `edh-tab-${key}`;
export const EDH_FEATURE_PANEL_ID = "edh-tab-panel";

interface EdhFeatureTabsProps {
    active: EdhFeatureTabKey;
    onChange: (key: EdhFeatureTabKey) => void;
}

/** The Workstations / Simulations strip of the EDH page; the caller renders the selected panel. */
const EdhFeatureTabs: React.FC<EdhFeatureTabsProps> = ({ active, onChange }) => (
    <TabStrip
        tabs={TABS}
        active={active}
        onChange={onChange}
        ariaLabel="EDH features"
        tabId={edhFeatureTabId}
        panelId={EDH_FEATURE_PANEL_ID}
    />
);

export default EdhFeatureTabs;
