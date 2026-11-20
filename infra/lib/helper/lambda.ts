/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as Config from "../../config/config";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";

export function layerBundlingCommand(): string {
    //Command to install layer dependencies from the layer's committed requirements.txt and remove unneeded cache, tests, and boto libraries (automatically comes with lambda python container base)
    //Note: The removals drastically reduce layer sizes
    //Note: requirements.txt is the pinned export of the layer's lock file (uv.lock; the RapidPipeline EKS Kubernetes layer still carries a Poetry lock and its committed Poetry export until it moves to uv). The command never resolves
    //      dependencies: it installs the committed pins with pip, so a deploy cannot pick a version the lock does not name. Re-lock and re-export before changing a layer's pyproject.toml.
    //Note: The build tool (pip) is pinned so a layer build is reproducible
    return [
        "pip install --root-user-action=ignore --upgrade pip==26.2.1",
        "if [ ! -f requirements.txt ]; then echo 'This layer directory has no requirements.txt: run uv lock and uv export to create it (see the developer guide, layer customization)'; exit 1; fi",
        "pip install --root-user-action=ignore -r requirements.txt -t /asset-output/python",
        "rsync -rLv ./ /asset-output/python",
        "cd /asset-output",
        "find . -type d -name __pycache__ -prune -exec rm -rf {} +",
        "find . -type d -name tests -prune -exec rm -rf {} +",
        //'find . -type d -name *boto* -prune -exec rm -rf {} +' //Exclude for now to not break version dependency chain
    ].join(" && ");
}
