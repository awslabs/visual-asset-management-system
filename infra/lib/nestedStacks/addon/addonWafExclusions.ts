/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as Config from "../../../config/config";
import { WafScopedRuleExclusion } from "../../constructs/wafv2-basic-construct";

/**
 * Web ACL rule exclusions the enabled add-ons apply to the regional ACL (`buildWafStacks()` in
 * `lib/cf-waf-stack.ts`): managed rules relaxed under an add-on's own API route prefixes and
 * re-blocked on every other path. Empty while no add-on needs one.
 */
export function addonWafRuleExclusions(config: Config.Config): WafScopedRuleExclusion[] {
    // Each enabled add-on that needs an exclusion appends the ones it builds from `config` here.
    return [];
}
