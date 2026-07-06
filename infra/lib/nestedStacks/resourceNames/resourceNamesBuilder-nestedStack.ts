/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { NestedStack } from "aws-cdk-lib";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { Construct } from "constructs";
import * as Config from "../../../config/config";
import { ResourceNameRegistry } from "./resourceNameRegistry";

export interface ResourceNamesBuilderNestedStackProps {
    config: Config.Config;
    resourceNameRegistry: ResourceNameRegistry;
}

/**
 * Materializes every registered resource-name descriptor as an SSM String parameter under
 * the deployment's resource-name prefix. Backend Lambda functions resolve table, bucket,
 * and log group names from these parameters via VAMS_RESOURCE_PARAM_PREFIX.
 *
 * Each parameter's construct ID is derived from its paramKey, so registry additions,
 * removals, and renames map cleanly onto CloudFormation creates, deletes, and
 * replacements across deployments. This stack holds only SSM parameters — at one
 * CloudFormation resource per registration it has headroom for hundreds of entries
 * before approaching the per-stack resource limit.
 */
export class ResourceNamesBuilderNestedStack extends NestedStack {
    constructor(parent: Construct, name: string, props: ResourceNamesBuilderNestedStackProps) {
        super(parent, name);
        props.resourceNameRegistry.list().forEach((descriptor) => {
            new ssm.StringParameter(
                this,
                `ResourceNameParam-${descriptor.paramKey.replace(/\//g, "-")}`,
                {
                    parameterName: `${props.config.resourceNamesSSMParamPrefix}/${descriptor.paramKey}`,
                    stringValue: descriptor.value,
                }
            );
        });
    }
}
