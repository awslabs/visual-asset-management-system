/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Auto-toggle (derived-state) engine.
 *
 * Mirrors the single auto-mutation config.ts performs (config.ts:458-479):
 * enabling ALB, OpenSearch Provisioned, or any container/VPC-bound pipeline
 * forces `useGlobalVpc.enabled = true`. This is a warning-grade convenience,
 * not an error — and it only ever turns VPC *on*, so it can never oscillate or
 * stomp unrelated user input.
 *
 * GovCloud's CloudFront/Location constraints are intentionally NOT auto-applied
 * here: config.ts treats them as hard errors, and silently flipping a user's
 * front-end choice would be fighting them. Those surface as validation errors
 * instead, with a user-initiated "Apply GovCloud-safe defaults" action in the UI.
 */

import type { ConfigShape, DerivedChange } from "./types";
import { getByPath, setByPath } from "./pathUtils";

/** Paths whose `enabled` flag implies a VPC is required. */
const VPC_IMPLYING_PATHS: { path: string; label: string }[] = [
    { path: "app.useAlb.enabled", label: "ALB" },
    { path: "app.openSearch.useProvisioned.enabled", label: "OpenSearch Provisioned" },
    { path: "app.pipelines.usePreviewPcPotreeViewer.enabled", label: "Point Cloud Potree Viewer" },
    { path: "app.pipelines.useSplatToolbox.enabled", label: "Gaussian Splatting" },
    { path: "app.pipelines.useGenAiMetadata3dLabeling.enabled", label: "GenAI Metadata Labeling" },
    { path: "app.pipelines.useRapidPipeline.useEcs.enabled", label: "RapidPipeline (ECS)" },
    { path: "app.pipelines.useRapidPipeline.useEks.enabled", label: "RapidPipeline (EKS)" },
    { path: "app.pipelines.useModelOps.enabled", label: "ModelOps" },
    { path: "app.pipelines.useIsaacLabTraining.enabled", label: "Isaac Lab Training" },
    { path: "app.pipelines.usePreview3dThumbnail.enabled", label: "3D Preview Thumbnail" },
    { path: "app.pipelines.useNvidiaCosmos.enabled", label: "NVIDIA Cosmos" },
    { path: "app.pipelines.useNvidiaGr00t.enabled", label: "NVIDIA Gr00t" },
];

export interface DerivedResult {
    config: ConfigShape;
    changes: DerivedChange[];
}

/**
 * Apply implied state. Returns a (possibly new) config plus a list of fields
 * that were forced, for visible "auto-adjusted" feedback. Idempotent.
 */
export function applyDerived(input: ConfigShape): DerivedResult {
    const changes: DerivedChange[] = [];

    const trigger = VPC_IMPLYING_PATHS.find(({ path }) => getByPath(input, path) === true);
    if (trigger && getByPath(input, "app.useGlobalVpc.enabled") !== true) {
        const next = setByPath(input, "app.useGlobalVpc.enabled", true);
        changes.push({
            path: "app.useGlobalVpc.enabled",
            to: true,
            reason: `Global VPC enabled — required by ${trigger.label}.`,
        });
        return { config: next, changes };
    }

    return { config: input, changes };
}

/**
 * User-initiated helper for the GovCloud error case: flip the config to a
 * GovCloud-safe front-end posture (CloudFront off, ALB on, Location off, VPC on).
 * Returned as a new config; the caller decides when to invoke it.
 */
export function applyGovCloudSafeDefaults(input: ConfigShape): ConfigShape {
    let next = input;
    next = setByPath(next, "app.useGlobalVpc.enabled", true);
    next = setByPath(next, "app.useCloudFront.enabled", false);
    next = setByPath(next, "app.useAlb.enabled", true);
    next = setByPath(next, "app.useLocationService.enabled", false);
    return next;
}
