/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The schema field editor and the asset version list take their text and icon colours from the
 * --vams-* theme tokens, so a fixed hex colour cannot come back and fail contrast in one theme.
 */
import fs from "fs";
import path from "path";

const SOURCES = [
    path.join(__dirname, "SchemaFieldEditor.tsx"),
    path.join(__dirname, "../asset/versions/components/AssetVersionList.tsx"),
];

describe("theme colours in the schema editor and the version list", () => {
    it.each(SOURCES)("%s has no hex colour literals", (file) => {
        const source = fs.readFileSync(file, "utf8");
        expect(source.match(/["'`]#[0-9a-fA-F]{3,8}\b/g) ?? []).toEqual([]);
    });
});
