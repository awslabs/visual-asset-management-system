/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as logs from "aws-cdk-lib/aws-logs";
import { IConstruct } from "constructs";

export class LogRetentionAspect implements cdk.IAspect {
    constructor(private readonly retentionDays: logs.RetentionDays) {}

    public visit(node: IConstruct): void {
        if (node instanceof logs.CfnLogGroup) {
            node.retentionInDays = this.retentionDays;
        }
    }
}
