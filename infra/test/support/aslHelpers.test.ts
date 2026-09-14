/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { declaredStageNames, jobDefinitionRefOf } from "./asl";

const DERIVED = {
    "Fn::Select": [
        1,
        {
            "Fn::Split": [
                "/",
                { "Fn::Select": [5, { "Fn::Split": [":", { Ref: "JobDefABC123" }] }] },
            ],
        },
    ],
};

describe("jobDefinitionRefOf", () => {
    test("returns the Ref inside a job-definition-name derivation", () => {
        expect(jobDefinitionRefOf(DERIVED)).toEqual("JobDefABC123");
    });

    test("rejects a raw Ref, a GetAtt, a plain string and a derivation with the wrong indices", () => {
        // A raw Ref is the ARN with a revision — a stream prefix built from it matches nothing.
        expect(jobDefinitionRefOf({ Ref: "JobDefABC123" })).toBeUndefined();
        expect(jobDefinitionRefOf({ "Fn::GetAtt": ["JobDefABC123", "Arn"] })).toBeUndefined();
        expect(jobDefinitionRefOf("SplatToolboxGpuJob-x")).toBeUndefined();
        expect(jobDefinitionRefOf(undefined)).toBeUndefined();
        const wrongIndex = JSON.parse(JSON.stringify(DERIVED));
        wrongIndex["Fn::Select"][0] = 0;
        expect(jobDefinitionRefOf(wrongIndex)).toBeUndefined();
    });
});

describe("declaredStageNames", () => {
    test("reads every module-level *_STATE_NAME string literal and nothing else", () => {
        const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vams-asl-helpers"));
        const file = path.join(dir, "openPipeline.py");
        fs.writeFileSync(
            file,
            [
                'BATCH_STATE_NAME = "Preview3dThumbnailBatchJob"',
                'PDAL_BATCH_STATE_NAME = "PdalConverterBatchJob"',
                // A trailing comment must not drop the literal from the join.
                'POTREE_BATCH_STATE_NAME = "PotreeConverterBatchJob"  # the second converter',
                '    NESTED_STATE_NAME = "indented-is-not-module-level"',
                'BATCH_STATE_NAME_FROM_ENV = os.environ.get("COSMOS_BATCH_STATE_NAME", "")',
                'OTHER = "value"',
            ].join("\n")
        );
        expect(declaredStageNames(file)).toEqual([
            "Preview3dThumbnailBatchJob",
            "PdalConverterBatchJob",
            "PotreeConverterBatchJob",
        ]);
        fs.rmSync(dir, { recursive: true, force: true });
    });
});
