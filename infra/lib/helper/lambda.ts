/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as Config from "../../config/config";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";

export function layerBundlingCommand(): string {
    //Command to install layer dependencies from poetry files and remove unneeded cache, tests, and boto libraries (automatically comes with lambda python container base)
    //Note: The removals drastically reduce layer sizes
    //Note: The build tools are pinned so a layer build is reproducible; poetry is the version that wrote the layer lock files
    return [
        "pip install --upgrade pip==26.2.1",
        "pip install poetry==2.1.4",
        "pip install poetry-plugin-export==1.9.0",
        "if [ ! -f poetry.lock ]; then poetry lock; fi",
        "poetry export --without-hashes --format=requirements.txt > requirements.txt",
        "pip install -r requirements.txt -t /asset-output/python",
        "rsync -rLv ./ /asset-output/python",
        "cd /asset-output",
        "find . -type d -name __pycache__ -prune -exec rm -rf {} +",
        "find . -type d -name tests -prune -exec rm -rf {} +",
        //'find . -type d -name *boto* -prune -exec rm -rf {} +' //Exclude for now to not break version dependency chain
    ].join(" && ");
}
