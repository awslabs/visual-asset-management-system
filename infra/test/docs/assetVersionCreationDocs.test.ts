/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Uploads and file edits never create an asset version. A version is recorded at asset creation
 * (version 0), by Create Version and by a revert. The user guide's version section and the API
 * reference's Asset Version definition say so, as concepts/files-and-versions.md already does.
 */

import { page, sectionOf } from "./support/docsText";

const versionSection = (): string =>
    sectionOf(page("user-guide/asset-management.md"), "### Creating a New Version");

const assetVersionDefinition = (): string =>
    page("api/assets.md")
        .split("\n")
        .find((line) => line.startsWith("-   **Asset Version**:")) ?? "";

describe("asset version creation documentation", () => {
    test("the version section and the definition exist (control)", () => {
        expect(versionSection()).not.toBe("");
        expect(assetVersionDefinition()).not.toBe("");
    });

    // TEMPORARY-TEST: pins the rewording of the two passages that said uploads create versions
    test.each([
        ["user-guide/asset-management.md Creating a New Version", versionSection],
        ["api/assets.md Asset Version", assetVersionDefinition],
    ])("%s does not say uploads or edits create a version", (_name, text) => {
        expect(text()).not.toMatch(/created automatically/i);
        expect(text()).not.toMatch(/when files are uploaded/i);
        expect(text()).toContain("version 0");
        expect(text()).toContain("concepts/files-and-versions.md");
    });
});
