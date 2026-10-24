/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../config/config";

/** An interface endpoint an enabled add-on needs in the VAMS VPC. */
export interface AddonInterfaceEndpoint {
    /** Construct id in the VPC stack; one a core endpoint block already created is not repeated. */
    id: string;
    service: ec2.InterfaceVpcEndpointAwsService;
}

/**
 * Interface endpoints the enabled add-ons call from the isolated subnets. The VPC builder
 * (`vpcBuilder-nestedStack.ts`) creates each one with the core endpoints when
 * `app.useGlobalVpc.addVpcEndpoints` is on, with private DNS, in the isolated subnets and behind
 * the shared endpoint security group. The ids are construct ids in the VPC stack, so an endpoint
 * keeps its logical id for as long as its id here stays the same.
 */
export function addonInterfaceEndpoints(config: Config.Config): AddonInterfaceEndpoint[] {
    const edh = config.app.addons.useEdhWorkstations;
    return [
        // The EDH containers feature registers job definitions with AWS Batch from the isolated
        // subnets; image pulls happen on EDH compute and the image builds run outside the VPC, so
        // it needs the Batch endpoint and neither ECR endpoint.
        ...(edh?.containers?.enabled === true
            ? [{ id: "BatchEndpoint", service: ec2.InterfaceVpcEndpointAwsService.BATCH }]
            : []),
        // The EDH AMI builds feature creates, starts and deletes Image Builder objects and
        // describes, tags and deregisters AMIs from the isolated subnets, and a build instance
        // placed in an isolated subnet registers with Systems Manager over ssmmessages and
        // ec2messages. Component and log traffic reaches Amazon S3 through the gateway endpoint.
        ...(edh?.enabled === true && edh?.amiBuilds?.enabled === true
            ? [
                  {
                      id: "ImageBuilderEndpoint",
                      service: ec2.InterfaceVpcEndpointAwsService.IMAGE_BUILDER,
                  },
                  { id: "EC2Endpoint", service: ec2.InterfaceVpcEndpointAwsService.EC2 },
                  {
                      id: "SSMMessagesEndpoint",
                      service: ec2.InterfaceVpcEndpointAwsService.SSM_MESSAGES,
                  },
                  {
                      id: "EC2MessagesEndpoint",
                      service: ec2.InterfaceVpcEndpointAwsService.EC2_MESSAGES,
                  },
              ]
            : []),
    ];
}
