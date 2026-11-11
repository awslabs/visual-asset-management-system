/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Derived-state engine.
 *
 * `getConfig()` rejects an invalid feature combination and names the offending fields rather than
 * rewriting the operator's configuration, so a change in deployment topology is never made on the
 * operator's behalf without their seeing it. Those rejections surface here as validation errors (see
 * `validation.ts`), and the VPC requirement is the worked example.
 *
 * `applyDerived()` carries the assignments `getConfig()` does make. Each one corresponds to a
 * normalization `getConfig()` performs, quoted by its warning text; an add-on's normalizations live
 * in its module under `addon/`, which `applyAddonDerived()` runs. Anything added must correspond to an
 * assignment `getConfig()` actually performs on the operator's config, and a normalization
 * `getConfig()` stops performing has to be deleted here — see the sync steps in the component README.
 * `ConfigBuilder.commitConfig()` funnels every field edit through it and shows the forced fields to
 * the user.
 *
 * The GovCloud CloudFront/Location constraints are not applied here: they are hard errors in
 * `getConfig()`, and flipping the operator's front-end choice would be fighting them. They surface as
 * validation errors, alongside the user-initiated "Apply GovCloud-safe defaults" action in the UI,
 * which is `applyGovCloudSafeDefaults()` below.
 */

import type { ConfigShape, DerivedChange } from "./types";
import { setByPath } from "./pathUtils";
import { applyAddonDerived } from "./addon";

export interface DerivedResult {
    config: ConfigShape;
    changes: DerivedChange[];
}

/**
 * Apply implied state. Returns the config plus the list of fields that were forced, for visible
 * "auto-adjusted" feedback in the UI. Idempotent; the input is not mutated.
 */
export function applyDerived(input: ConfigShape): DerivedResult {
    const changes: DerivedChange[] = [];
    const config = applyAddonDerived(input, changes);
    return { config, changes };
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
