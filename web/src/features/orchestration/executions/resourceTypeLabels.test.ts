/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { RESOURCE_TYPE_LABELS, resourceTypeLabel } from "./resourceTypeLabels";

describe("resourceTypeLabel", () => {
    it("labels every type VAMS can stop", () => {
        expect(Object.keys(RESOURCE_TYPE_LABELS).sort()).toEqual([
            "batchJob",
            "deadlineCloudJob",
            "sageMakerPipelineExecution",
            "sageMakerTrainingJob",
            "stepFunctionsExecution",
        ]);
        expect(resourceTypeLabel("sageMakerTrainingJob")).toBe("SageMaker training job");
        expect(resourceTypeLabel("deadlineCloudJob")).toBe("Deadline Cloud job");
    });

    it("falls back to the raw type for one it does not know, never to an empty string", () => {
        expect(resourceTypeLabel("slurmJob")).toBe("slurmJob");
        expect(resourceTypeLabel("")).toBe("");
    });
});
