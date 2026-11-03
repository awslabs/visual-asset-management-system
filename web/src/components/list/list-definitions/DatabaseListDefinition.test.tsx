/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Databases list offers eight columns, several holding long single tokens (bucket names,
 * prefixes, extension lists). Unwrapped, the table was about 1,480 px wide at 1280 and 1440 px.
 * It wraps its cells, lets the long tokens break, and starts with Base Bucket Prefix hidden so seven
 * columns fit a 1280 px page beside the navigation.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { createDatabaseListDefinition } from "./DatabaseListDefinition";

describe("DatabaseListDefinition", () => {
    it("asks the table to wrap and offers all eight columns, with the prefix hidden", () => {
        const definition: any = createDatabaseListDefinition();
        expect(definition.wrapLines).toBe(true);
        expect(definition.visibleColumns).toEqual([
            "databaseId",
            "description",
            "assetCount",
            "metadata",
            "restrictMetadataOutsideSchemas",
            "restrictFileUploadsToExtensions",
            "bucketName",
            "baseAssetsPrefix",
        ]);
        expect(definition.hiddenColumns).toEqual(["baseAssetsPrefix"]);
    });

    it.each([
        ["bucketName", "vams-dev-assetbucket-0123456789abcdef"],
        ["baseAssetsPrefix", "assets/a-very-long-prefix-without-spaces/"],
        ["restrictFileUploadsToExtensions", ".glb,.gltf,.obj,.fbx,.usdz"],
    ])("lets a long %s value break anywhere", (id, value) => {
        const definition: any = createDatabaseListDefinition();
        const column = definition.columnDefinitions.find((c: any) => c.id === id);
        const Cell = column.CellWrapper;
        render(<Cell item={{ [id]: value }}>{value}</Cell>);
        expect(screen.getByText(value)).toHaveStyle({ overflowWrap: "anywhere" });
    });
});
