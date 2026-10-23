/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as fs from "fs";
import * as path from "path";
import * as lambda from "aws-cdk-lib/aws-lambda";

/** The build-trigger handler every pipeline *CodeBuild construct runs behind its cr.Provider. */
export const CODEBUILD_TRIGGER_HANDLER_PATH = path.join(__dirname, "codeBuildTrigger", "index.py");

/** Lambda's limit on inline (ZipFile) function code. */
const INLINE_CODE_MAX_BYTES = 4096;

/**
 * The build-trigger handler as inline function code (handler `index.handler`). One source file
 * serves every pipeline *CodeBuild construct, so their triggers cannot drift; it stays inline so the trigger function
 * keeps its ZipFile code and no construct gains an asset.
 */
export function codeBuildTriggerCode(): lambda.Code {
    const source = fs.readFileSync(CODEBUILD_TRIGGER_HANDLER_PATH, "utf8");
    if (Buffer.byteLength(source, "utf8") > INLINE_CODE_MAX_BYTES) {
        throw new Error(
            `${CODEBUILD_TRIGGER_HANDLER_PATH} exceeds the ${INLINE_CODE_MAX_BYTES}-byte inline Lambda code limit`
        );
    }
    return lambda.Code.fromInline(source);
}
