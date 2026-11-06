/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The add-on lane of the ConfigBuilder. Each add-on whose configuration block lives under
 * infra/config/addon/ has one module in this directory; this file joins them into the tables the
 * core files splice in: `schema.ts` (`ADDON_FIELDS`), `validation.ts` (`ADDON_RULES`,
 * `ADDON_VPC_REQUIRING_FEATURES`), `defaults.ts` (`ADDON_DEFAULTS`), `derived.ts`
 * (`applyAddonDerived()`) and infra/test/config/configBuilderSync.test.ts (`ADDON_JSON_ONLY_PATHS`).
 */

import type { ConfigShape, DerivedChange, FieldMeta, Rule } from "../types";
import type { VpcRequiringFeature } from "../ruleHelpers";
import {
    applyEdhWorkstationsDerived,
    EDH_DEFAULTS,
    EDH_FIELDS,
    EDH_JSON_ONLY_PATHS,
    EDH_RULES,
    EDH_VPC_REQUIRING_FEATURES,
} from "./edhWorkstations";

/** Add-on form fields, appended to the core `FIELDS`. */
export const ADDON_FIELDS: FieldMeta[] = [...EDH_FIELDS];

/** Add-on rules, appended to the core `RULES`. */
export const ADDON_RULES: Rule[] = [...EDH_RULES];

/** Add-on features that require a VPC, appended to the core VPC-requiring features. */
export const ADDON_VPC_REQUIRING_FEATURES: VpcRequiringFeature[] = [...EDH_VPC_REQUIRING_FEATURES];

/** Add-on blocks of the template JSONs, spread into `app.addons` after the core add-ons. */
export const ADDON_DEFAULTS: ConfigShape = { ...EDH_DEFAULTS };

/** Add-on config paths edited only in the downloaded config.json. */
export const ADDON_JSON_ONLY_PATHS: readonly string[] = [...EDH_JSON_ONLY_PATHS];

/** Runs each add-on's normalizations, recording every forced field in `changes`. */
export function applyAddonDerived(input: ConfigShape, changes: DerivedChange[]): ConfigShape {
    return applyEdhWorkstationsDerived(input, changes);
}
