/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect as expectCDK, matchTemplate, MatchStyle } from "@aws-cdk/assert";
import * as cdk from "aws-cdk-lib";
import * as Infra from "../lib/core-stack";

test("Empty Stack", () => {
    const app = new cdk.App();
    // WHEN
    const stack = new Infra.CoreVAMSStack(app, "MyTestStack", {
        prod: false,
        stackName: `vams--test`,
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
