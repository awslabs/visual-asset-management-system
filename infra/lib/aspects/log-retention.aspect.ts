/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as logs from "aws-cdk-lib/aws-logs";
import { IConstruct } from "constructs";

/**
 * Sets the retention period on every CloudWatch log group in the stack.
 *
 * This aspect is the single authority for log retention across VAMS. It visits every
 * `AWS::Logs::LogGroup` and assigns `retentionInDays` unconditionally, so a `retention` value passed
 * to an individual construct does not survive — the aspect runs last and overwrites it. Construct-level
 * declarations are kept aligned with the value applied here (`ONE_YEAR`) so that reading a construct
 * does not imply a retention the deployment does not actually get.
 *
 * ## Changing retention
 *
 * Change it in one place — the `new LogRetentionAspect(...)` call in `lib/core-stack.ts`. Editing a
 * `retention` property on an individual log group has no effect.
 *
 * A longer period is a common requirement in regulated environments — public-sector, defence, and
 * other industries whose audit-record retention obligations exceed a year. To extend retention for a
 * deployment, pass a longer `logs.RetentionDays` value (for example `TEN_YEARS`) at that call site.
 * Two things to weigh before doing so:
 *
 * - It applies to **every** log group, including high-volume Lambda execution logs, not only the
 *   audit groups. CloudWatch Logs storage is billed per GB-month for the whole retention window, so
 *   the cost scales with total ingestion, not with the number of groups.
 * - If only the audit groups need the longer period, prefer narrowing this aspect by log-group name
 *   over raising it globally — the audit groups are the ones created in
 *   `storageBuilder-nestedStack.ts` and exposed as `storageResources.cloudWatchAuditLogGroups`.
 *
 * `RetentionDays.INFINITE` disables expiry entirely and is deliberately not the default: unbounded
 * retention is an open-ended cost commitment.
 */
export class LogRetentionAspect implements cdk.IAspect {
    constructor(private readonly retentionDays: logs.RetentionDays) {}

    public visit(node: IConstruct): void {
        if (node instanceof logs.CfnLogGroup) {
            node.retentionInDays = this.retentionDays;
        }
    }
}
