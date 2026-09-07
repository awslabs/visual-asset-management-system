/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Derived-state engine.
 *
 * `getConfig()` does not rewrite the operator's configuration to satisfy a
 * constraint. Where a feature combination is invalid it rejects the configuration
 * and names the offending fields, so a change in deployment topology is never made
 * on the operator's behalf without their seeing it. The builder mirrors that
 * posture: it derives nothing, and every such constraint surfaces as a validation
 * error instead (see `validation.ts`).
 *
 * `applyDerived()` is therefore a pass-through. It is kept because
 * `ConfigBuilder.commitConfig()` funnels every field edit through it, which is the
 * single place a `getConfig()` auto-mutation would be mirrored if one existed.
 * Anything added here must correspond to an assignment `getConfig()` actually
 * performs on the operator's config — see the sync steps in the component README.
 *
 * The GovCloud CloudFront/Location constraints are not applied here either: they
 * are hard errors in `getConfig()`, and flipping the operator's front-end choice
 * would be fighting them. They surface as validation errors, alongside the
 * user-initiated "Apply GovCloud-safe defaults" action in the UI, which is
 * `applyGovCloudSafeDefaults()` below.
 */

import type { ConfigShape, DerivedChange } from "./types";
import { setByPath } from "./pathUtils";

export interface DerivedResult {
    config: ConfigShape;
    changes: DerivedChange[];
}

/**
 * Apply implied state. Returns the config plus the list of fields that were forced,
 * for visible "auto-adjusted" feedback in the UI. Idempotent, and currently forces
 * nothing, so `changes` is always empty.
 */
export function applyDerived(input: ConfigShape): DerivedResult {
    return { config: input, changes: [] };
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
