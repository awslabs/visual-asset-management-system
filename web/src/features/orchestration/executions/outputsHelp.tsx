/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

/**
 * What an execution's Outputs list does and does not include.
 *
 * The end-state lambda indexes produced objects by listing four prefixes on the execution's asset
 * bucket (files / metadata / previews / results). Anything a pipeline writes to the AUXILIARY bucket
 * is never listed, so it is absent here — shared by the quick-view panel and the details page so both
 * describe the same scope.
 */
export const OUTPUTS_SCOPE_HELP = (
    <>
        <p className="mb-1">
            Lists the files, metadata and results this execution wrote to its output{" "}
            <strong>asset</strong>, recorded per file with its version.
        </p>
        <p>
            Files a pipeline writes to the <strong>auxiliary bucket</strong> are not listed —
            including special preview-file locations. Those are working and viewer-support files
            that are not tracked as asset outputs, so they exist without appearing here.
        </p>
    </>
);
