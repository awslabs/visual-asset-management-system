/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useId, useState } from "react";
import { useAllowedRoutes } from "../../../features/orchestration/permissions/useAllowedRoutes";
import { addonFeaturesEnabled } from "../../addonFeatures";
import { useFeaturesEnabled } from "../../../context/RuntimeConfigContext";
import { useEdhHealth } from "../api/adminQueries";
import type { EdhHealthReason } from "../api/admin";
import TabStrip from "../components/TabStrip";
import NotEnabledPanel from "../components/NotEnabledPanel";
import ImagesTab from "./ImagesTab";
import InstancesTab from "./InstancesTab";
import CapacityTab from "./CapacityTab";
import AmiRecipesTab from "./AmiRecipesTab";
import SettingsTab from "./SettingsTab";

type TabKey = "instances" | "capacity" | "images" | "amiRecipes" | "settings";

// Each tab's panel is gated on the GET route it reads; the templates are the addonApiRoutes.py strings.
// The instances resource is presented to administrators as EDH sessions.
const TABS: Array<{ key: TabKey; label: string; route: string; what: string }> = [
    {
        key: "instances",
        label: "Sessions",
        route: "/addon/edh/admin/instances",
        what: "the session views",
    },
    {
        key: "capacity",
        label: "Capacity",
        route: "/addon/edh/admin/capacity",
        what: "the capacity view",
    },
    { key: "images", label: "Images", route: "/addon/edh/admin/images", what: "the image catalog" },
    {
        key: "amiRecipes",
        label: "AMI Recipes",
        route: "/addon/edh/admin/ami-recipes",
        what: "the AMI recipes",
    },
    {
        key: "settings",
        label: "Settings",
        route: "/addon/edh/admin/settings",
        what: "the lease policy",
    },
];

/** What each health reason asks an operator to look at; the root key is re-synced by hand (it rotates on every EDH web-tier start). */
export const HEALTH_REASON_COPY: Record<EdhHealthReason, string> = {
    credentialRejected: "credential rejected — re-sync the EDH root key secret",
    unreachable: "unreachable",
    refused: "probe refused by EDH",
};

/** The copy for a reason, or the generic one for a reason this page does not model. */
const healthReasonCopy = (reason?: string) =>
    (reason && HEALTH_REASON_COPY[reason as EdhHealthReason]) || HEALTH_REASON_COPY.unreachable;

const reachableCopy = (latencyMs?: number) =>
    latencyMs == null ? "reachable" : `reachable (${latencyMs} ms)`;

const NoAccess: React.FC<{ what: string }> = ({ what }) => (
    <div
        role="status"
        className="p-4 rounded bg-surface-container border border-border-default text-text-primary"
    >
        You do not have access to {what}.
    </div>
);

const EdhAdminPage: React.FC = () => {
    const features = useFeaturesEnabled();
    const enabled = features.includes(addonFeaturesEnabled.EDH_WORKSTATIONS);
    // The AMI recipes tab is present only where the deployment builds AMIs.
    const amiRecipesEnabled = features.includes(addonFeaturesEnabled.EDH_AMI_RECIPES);
    // The tab the user chose; until then the page shows the first one it may read.
    const [chosenTab, setChosenTab] = useState<TabKey | null>(null);
    const tabIdBase = useId();
    const { loading: permissionsLoading, can } = useAllowedRoutes();
    const canHealth = !permissionsLoading && can("GET", "/addon/edh/admin/health");
    const health = useEdhHealth(enabled && canHealth);

    if (!enabled) {
        return (
            <div className="orchestration-root orchestration-page space-y-4 bg-surface min-h-full">
                <h1 className="text-text-primary">Workstation Admin</h1>
                <NotEnabledPanel />
            </div>
        );
    }

    const tabDomId = (key: TabKey) => `${tabIdBase}-tab-${key}`;
    const panelDomId = `${tabIdBase}-panel`;
    const tabs = amiRecipesEnabled ? TABS : TABS.filter((t) => t.key !== "amiRecipes");
    const current =
        tabs.find((t) => t.key === chosenTab) ??
        (permissionsLoading ? undefined : tabs.find((t) => can("GET", t.route))) ??
        tabs[0];
    const activeTab = current.key;
    const canCurrent = !permissionsLoading && can("GET", current.route);

    // A failed probe (EDH's own error answer, or no answer) reads in the error tone and is announced,
    // like the load failures in the tab bodies; a healthy or pending probe is a plain caption. A probe
    // that got no answer is reported ahead of the answer still cached from the previous one.
    const probeUnanswered = !health.isLoading && !!health.error;
    const healthFailed = probeUnanswered || health.data?.status === "error";
    const healthText = health.isLoading
        ? "Checking EDH…"
        : probeUnanswered
        ? "EDH is unreachable or rejected the configured credential"
        : health.data?.status === "ok"
        ? `EDH ${health.data.edhClusterId}: ${reachableCopy(health.data.latencyMs)}`
        : health.data?.status === "error"
        ? `EDH ${health.data.edhClusterId}: ${healthReasonCopy(health.data.reason)}`
        : null;

    return (
        <div className="orchestration-root orchestration-page space-y-4 bg-surface min-h-full">
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <h1 className="text-text-primary">Workstation Admin</h1>
                {canHealth && (
                    <div
                        role={healthFailed ? "alert" : "status"}
                        className={`text-sm ${
                            healthFailed ? "text-vams-error font-semibold" : "text-text-secondary"
                        }`}
                    >
                        {healthText}
                    </div>
                )}
            </div>

            <TabStrip
                tabs={tabs}
                active={activeTab}
                onChange={setChosenTab}
                ariaLabel="Workstation administration"
                tabId={tabDomId}
                panelId={panelDomId}
            />

            <div id={panelDomId} role="tabpanel" aria-labelledby={tabDomId(activeTab)} tabIndex={0}>
                {permissionsLoading ? (
                    <div className="p-8 text-center text-text-secondary">
                        Loading permissions...
                    </div>
                ) : !canCurrent ? (
                    <NoAccess what={current.what} />
                ) : activeTab === "images" ? (
                    <ImagesTab can={can} />
                ) : activeTab === "instances" ? (
                    <InstancesTab can={can} />
                ) : activeTab === "amiRecipes" ? (
                    <AmiRecipesTab can={can} />
                ) : activeTab === "settings" ? (
                    <SettingsTab can={can} />
                ) : (
                    <CapacityTab />
                )}
            </div>
        </div>
    );
};

export default EdhAdminPage;
