/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect as expectCDK, matchTemplate, MatchStyle } from "@aws-cdk/assert";
import * as cdk from "aws-cdk-lib";
import * as Infra from "../lib/infra-stack";

test("Empty Stack", () => {
    const app = new cdk.App();
    // WHEN
    const stack = new Infra.VAMS(app, "MyTestStack", {
        env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'us-east-1' },
        prod: false,
        stackName: `vams--test`,
        ssmWafArnParameterName: '',
        ssmWafArnParameterRegion: '',
        ssmWafArn: '',
        stagingBucket: ''
    });
    // THEN
    expectCDK(stack).to(
        matchTemplate(
            {
                Resources: {},
            },
            MatchStyle.EXACT
        )
    );
});
