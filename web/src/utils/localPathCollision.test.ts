/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A download that keeps the asset's paths cannot write a file "/a.txt" and a file under "/a.txt/": no local
 * folder holds a file and a folder of one name (#2074, the rule of #2070).
 */

import { fileFolderCollision } from "./localPathCollision";

describe("fileFolderCollision", () => {
    it.each([
        [["/a.txt", "/a.txt/b.txt"], { file: "/a.txt", other: "/a.txt/b.txt" }],
        [["/a.txt/b.txt", "/a.txt"], { file: "/a.txt", other: "/a.txt/b.txt" }],
        [
            ["/model.glb", "/model.glb/sub/c.obj"],
            { file: "/model.glb", other: "/model.glb/sub/c.obj" },
        ],
        [["/d/x", "/d/x/y/z"], { file: "/d/x", other: "/d/x/y/z" }],
        // Written at the same local path: an empty segment names no folder
        [["//x", "/x/y"], { file: "//x", other: "/x/y" }],
        [["x", "/x/y"], { file: "x", other: "/x/y" }],
    ])("finds the file and a path in its folder: %j", (paths, expected) => {
        expect(fileFolderCollision(paths)).toEqual(expected);
    });

    it.each([
        [["/a.txt", "/a.txtx/b.txt"]],
        [["/a.txt", "/b/a.txt"]],
        [["/a", "/ab/c"]],
        [["/A.txt", "/a.txt/b.txt"]],
        [["/x", "//x"]],
        [[]],
    ])("no false positive: %j", (paths) => {
        expect(fileFolderCollision(paths)).toBeNull();
    });
});
