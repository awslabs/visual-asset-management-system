/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { CfnResource, IAspect } from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { IConstruct } from "constructs";

/** The construct id CDK gives an overflow policy it splits off a role's default policy. */
const OVERFLOW_ID = /^OverflowPolicy\d+$/;

/**
 * Gives each CDK-generated overflow policy the cdk-nag suppressions of the role it was split from.
 *
 * CDK moves the statements that do not fit a role's inline policy into `OverflowPolicy<n>` managed
 * policies (aws-iam Role.splitLargePolicy) while aspects run, after the builders have suppressed the
 * role's findings on its default policy (`NagSuppressions.addResourceSuppressions(fun, ..., true)`
 * covers only the children that exist at that call). The statements are the same, so their
 * suppressions are too: this aspect copies the `cdk_nag` metadata of the role and its default policy
 * onto each overflow policy, before cdk-nag (added after it, at a later priority) checks it (#1499).
 */
export class OverflowPolicyNagSuppressionAspect implements IAspect {
    public visit(node: IConstruct): void {
        if (!(node instanceof iam.CfnManagedPolicy) || !node.node.scope) return;
        const policy = node.node.scope; // the ManagedPolicy construct
        if (!OVERFLOW_ID.test(policy.node.id)) return;
        const role = policy.node.scope;
        if (!(role instanceof iam.Role)) return;
        const sources: CfnResource[] = [];
        const roleL1 = role.node.defaultChild;
        if (roleL1 instanceof CfnResource) sources.push(roleL1);
        const defaultPolicy = role.node.tryFindChild("DefaultPolicy");
        const defaultL1 = defaultPolicy?.node.tryFindChild("Resource");
        if (defaultL1 instanceof CfnResource) sources.push(defaultL1);
        const rules: any[] = [];
        for (const source of sources) {
            for (const rule of source.getMetadata("cdk_nag")?.rules_to_suppress ?? []) {
                if (!rules.some((r) => JSON.stringify(r) === JSON.stringify(rule)))
                    rules.push(rule);
            }
        }
        if (rules.length === 0) return;
        const existing = node.getMetadata("cdk_nag")?.rules_to_suppress ?? [];
        node.addMetadata("cdk_nag", { rules_to_suppress: [...existing, ...rules] });
    }
}
