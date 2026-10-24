/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as Config from "../../../../config/config";
import { WafScopedRuleExclusion } from "../../../constructs/wafv2-basic-construct";

/**
 * Web ACL rule exclusions for the EDH Workstations add-on, applied to the regional CfWafStack ACL.
 *
 * The AMI recipe admin routes carry Image Builder component scripts in their request bodies, and a
 * build component commonly reads the instance metadata service at `http://169.254.169.254/...`. The
 * Common Rule Set blocks such a body on EC2MetaDataSSRF_BODY (the metadata address) and on
 * GenericRFI_BODY (a URL holding an IPv4 address). VAMS stores those scripts for the Image Builder
 * build instance to run and never dereferences a URL in them, and the routes are admin-only at both
 * authorization tiers, so the two rules are relaxed under the recipe route prefix and keep blocking
 * on every other path. None while the add-on or its AMI builds are off.
 */
export function buildEdhWafRuleExclusions(config: Config.Config): WafScopedRuleExclusion[] {
    const edh = config.app.addons?.useEdhWorkstations;
    if (!edh?.enabled || !edh.amiBuilds?.enabled) {
        return [];
    }
    return [
        {
            managedRuleGroupName: "AWSManagedRulesCommonRuleSet",
            rules: [
                {
                    name: "EC2MetaDataSSRF_BODY",
                    label: "awswaf:managed:aws:core-rule-set:EC2MetaDataSSRF_Body",
                },
                {
                    name: "GenericRFI_BODY",
                    label: "awswaf:managed:aws:core-rule-set:GenericRFI_Body",
                },
            ],
            // The recipe collection and every /{recipeId} sub-route the add-on stack registers.
            uriPathPrefixes: [`/${Config.API_GATEWAY_STAGE_NAME}/addon/edh/admin/ami-recipes`],
            reapplyRuleName: "VAMS-EdhAmiRecipeBodyRules",
            // Rules the shipped policy counts or omits that still block a recipe body when a
            // policy enforces them: a body over 8 KB, and the shell, PowerShell, SQL and PHP
            // patterns component scripts carry.
            unrelaxedRules: [
                {
                    managedRuleGroupName: "AWSManagedRulesCommonRuleSet",
                    name: "SizeRestrictions_BODY",
                },
                { managedRuleGroupName: "AWSManagedRulesLinuxRuleSet" },
                { managedRuleGroupName: "AWSManagedRulesUnixRuleSet" },
                { managedRuleGroupName: "AWSManagedRulesWindowsRuleSet" },
                { managedRuleGroupName: "AWSManagedRulesSQLiRuleSet" },
                { managedRuleGroupName: "AWSManagedRulesPHPRuleSet" },
            ],
        },
    ];
}
