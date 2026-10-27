/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    addFiles,
    getRootByPath,
    mergeFiles,
    normalizeFileManagerPath,
    resolveFileManagerPath,
} from "./FileManagerUtils";
import type { FileKey, FileTree } from "../types/FileManagerTypes";

jest.mock("../../../services/APIService", () => ({
    downloadAsset: jest.fn(),
}));

/**
 * The details pane's "View execution" link reads the workflow ids off the TREE NODE, and every node is
 * built here: the streamed listing goes through mergeFiles -> addFiles, and the on-demand file info is
 * merged onto the existing node. A dropped pass-through in either place leaves the link permanently
 * absent while the pane's own tests, which seed the node directly, stay green.
 */
const PROVENANCE = {
    changeSource: "workflowExecution",
    changeWorkflowId: "wf-1",
    changeWorkflowExecutionId: "exec-1",
};

const rootTree = (): FileTree => ({
    name: "asset-1",
    displayName: "asset-1",
    relativePath: "/",
    keyPrefix: "asset-1/",
    level: 0,
    expanded: true,
    subTree: [],
    isFolder: true,
});

/** A listing item for a file at `relativePath`; the S3 key carries the asset prefix. */
const fileKey = (relativePath: string, extra: Partial<FileKey> = {}): FileKey => ({
    fileName: relativePath.split("/").pop() || "",
    key: `asset-1${relativePath}`,
    relativePath,
    isFolder: false,
    dateCreatedCurrentVersion: "2026-01-01T00:00:00Z",
    versionId: "v1",
    isArchived: false,
    ...extra,
});

describe("addFiles provenance", () => {
    it("carries the workflow ids onto a root-level file node", () => {
        const tree = addFiles([fileKey("/model.glb", PROVENANCE)], rootTree());
        expect(getRootByPath(tree, "/model.glb")).toMatchObject(PROVENANCE);
    });

    it("carries the workflow ids onto a nested file node", () => {
        const tree = addFiles([fileKey("/out/model.glb", PROVENANCE)], rootTree());
        expect(getRootByPath(tree, "/out/model.glb")).toMatchObject(PROVENANCE);
        // The folder created on the way is not a workflow-written version.
        expect(getRootByPath(tree, "/out/")).not.toHaveProperty("changeWorkflowExecutionId");
    });

    it("leaves the ids absent on a file that carries none", () => {
        const tree = addFiles([fileKey("/model.glb")], rootTree());
        const node = getRootByPath(tree, "/model.glb") as FileTree;
        expect(node.changeWorkflowId).toBeUndefined();
        expect(node.changeWorkflowExecutionId).toBeUndefined();
    });
});

describe("mergeFiles provenance", () => {
    it("sets the workflow ids on an existing node from an update that carries them", () => {
        // The file-info fetch lands after the listing: the node already exists and must be updated
        // in place, not duplicated.
        const tree = addFiles([fileKey("/model.glb")], rootTree());
        const merged = mergeFiles(
            [fileKey("/model.glb", { versionId: "v2", ...PROVENANCE })],
            tree
        );
        expect(merged.subTree).toHaveLength(1);
        expect(getRootByPath(merged, "/model.glb")).toMatchObject(PROVENANCE);
    });

    it("keeps the ids when an update does not carry them", () => {
        // A basic listing item has no provenance keys; a detail already merged must survive the
        // next streamed refresh.
        const tree = addFiles([fileKey("/model.glb", PROVENANCE)], rootTree());
        const merged = mergeFiles([fileKey("/model.glb", { versionId: "v2" })], tree);
        expect(getRootByPath(merged, "/model.glb")).toMatchObject(PROVENANCE);
    });

    it("replaces the ids with an explicit blank, as the other provenance fields do", () => {
        // The guard is on `undefined`, not truthiness: a later write that stamps the ids blank must
        // not leave a stale link on the node.
        const tree = addFiles([fileKey("/model.glb", PROVENANCE)], rootTree());
        const merged = mergeFiles(
            [
                fileKey("/model.glb", {
                    changeSource: "upload",
                    changeWorkflowId: "",
                    changeWorkflowExecutionId: "",
                }),
            ],
            tree
        );
        expect(getRootByPath(merged, "/model.glb")).toMatchObject({
            changeSource: "upload",
            changeWorkflowId: "",
            changeWorkflowExecutionId: "",
        });
    });

    it("adds a new provenance-bearing file through the same merge", () => {
        const tree = addFiles([fileKey("/a.glb")], rootTree());
        const merged = mergeFiles([fileKey("/out/b.glb", PROVENANCE)], tree);
        expect(getRootByPath(merged, "/out/b.glb")).toMatchObject(PROVENANCE);
    });
});

/**
 * Deep links name a file by its asset-relative path, and not every producer writes the tree's own
 * form: workflow output rows store the path without its leading slash.
 */
describe("normalizeFileManagerPath", () => {
    it.each([
        ["out/sub/model.glb", "/out/sub/model.glb"],
        ["/out/sub/model.glb", "/out/sub/model.glb"],
        ["//out//sub///model.glb", "/out/sub/model.glb"],
        ["out/sub/", "/out/sub/"],
        ["", "/"],
        ["/", "/"],
    ])("maps %p to %p", (input, expected) => {
        expect(normalizeFileManagerPath(input)).toBe(expected);
    });
});

describe("resolveFileManagerPath", () => {
    const tree = () =>
        addFiles(
            [
                fileKey("/model.glb"),
                fileKey("/out/sub/model.glb"),
                {
                    ...fileKey("/out/empty/"),
                    fileName: "",
                    key: "asset-1/out/empty/",
                    isFolder: true,
                },
            ],
            rootTree()
        );

    it("finds a nested file by its path with the leading slash", () => {
        expect(resolveFileManagerPath(tree(), "/out/sub/model.glb")?.relativePath).toBe(
            "/out/sub/model.glb"
        );
    });

    it("finds a nested file by its path without the leading slash", () => {
        expect(resolveFileManagerPath(tree(), "out/sub/model.glb")?.relativePath).toBe(
            "/out/sub/model.glb"
        );
    });

    it("finds a root-level file without the leading slash", () => {
        expect(resolveFileManagerPath(tree(), "model.glb")?.relativePath).toBe("/model.glb");
    });

    it("finds a path written with repeated slashes", () => {
        expect(resolveFileManagerPath(tree(), "//out//sub/model.glb")?.relativePath).toBe(
            "/out/sub/model.glb"
        );
    });

    it.each(["/out/sub/", "out/sub/", "/out/sub", "out/sub"])("finds a folder from %p", (input) => {
        const node = resolveFileManagerPath(tree(), input);
        expect(node?.relativePath).toBe("/out/sub/");
        expect(node?.isFolder).toBe(true);
    });

    it("finds a folder that holds no files", () => {
        expect(resolveFileManagerPath(tree(), "out/empty")?.relativePath).toBe("/out/empty/");
    });

    it("returns the root for the root path and for an empty path", () => {
        const root = tree();
        expect(resolveFileManagerPath(root, "/")).toBe(root);
        expect(resolveFileManagerPath(root, "")).toBe(root);
    });

    it("returns null for a path the asset does not hold", () => {
        expect(resolveFileManagerPath(tree(), "out/sub/missing.glb")).toBeNull();
        expect(resolveFileManagerPath(tree(), "missing/")).toBeNull();
    });

    it("returns null without a tree", () => {
        expect(resolveFileManagerPath(null, "/model.glb")).toBeNull();
    });
});

/**
 * The execution page's input file link names the file by its full stored key: the asset's S3 root
 * key followed by the asset-relative path ("/{assetId}/out/sub/model.glb"). The tree root carries
 * that root key as its keyPrefix, taken from the listing's root entry (the object whose key is the
 * listed prefix itself). The trees here are built through mergeFiles, the streamed listing's path.
 */
describe("resolveFileManagerPath with the asset's full key", () => {
    /** The streamed listing of an asset whose root entry's S3 key is `rootKey`. */
    const treeUnder = (rootKey: string, relativePaths = ["/model.glb", "/out/sub/model.glb"]) => {
        const base = rootKey.endsWith("/") ? rootKey : `${rootKey}/`;
        const rootEntry: FileKey = {
            fileName: rootKey.endsWith("/") ? "" : rootKey.split("/").pop() || "",
            key: rootKey,
            relativePath: "/",
            isFolder: rootKey.endsWith("/"),
            dateCreatedCurrentVersion: "2026-01-01T00:00:00Z",
            versionId: "v1",
            isArchived: false,
        };
        const files = relativePaths.map((relativePath) =>
            fileKey(relativePath, { key: `${base}${relativePath.slice(1)}` })
        );
        return mergeFiles([rootEntry, ...files], { ...rootTree(), keyPrefix: "/" });
    };

    // Root keys as createAsset stores them in assetLocation.Key and the listing returns them on its
    // root entry: {baseAssetsPrefix}{assetId}/ for a new asset (a "/" baseAssetsPrefix normalizes
    // to ""), and the caller's key under the base prefix for a bucketExistingKey asset, which keeps
    // no trailing slash when the caller gave none.
    const shapes: [string, string][] = [
        ["a default asset under a '/' base prefix", "a1/"],
        ["a default asset under an 'assets/' base prefix", "assets/a1/"],
        ["a bucketExistingKey asset", "assets/scans/site-7/"],
        ["a bucketExistingKey asset with no trailing slash", "assets/legacy"],
    ];

    it.each(shapes)("carries the root key of %s on the tree root", (_label, rootKey) => {
        expect(treeUnder(rootKey).keyPrefix).toBe(rootKey);
    });

    describe.each(shapes)("for %s", (_label, rootKey) => {
        const root = rootKey.replace(/\/+$/, "");

        it.each([`/${root}/out/sub/model.glb`, `${root}/out/sub/model.glb`])(
            "finds a nested file from %p",
            (input) => {
                expect(resolveFileManagerPath(treeUnder(rootKey), input)?.relativePath).toBe(
                    "/out/sub/model.glb"
                );
            }
        );

        it("finds a root-level file from its full key", () => {
            expect(
                resolveFileManagerPath(treeUnder(rootKey), `/${root}/model.glb`)?.relativePath
            ).toBe("/model.glb");
        });

        it("finds a folder from its full key without the trailing slash", () => {
            expect(
                resolveFileManagerPath(treeUnder(rootKey), `/${root}/out/sub`)?.relativePath
            ).toBe("/out/sub/");
        });

        it.each([`/${root}/`, `/${root}`, `${root}/`, root])(
            "returns the root for the root key written %p",
            (input) => {
                const tree = treeUnder(rootKey);
                expect(resolveFileManagerPath(tree, input)).toBe(tree);
            }
        );

        // Controls: these hold before and after the full-key support.
        it("returns null for a full key the asset does not hold", () => {
            expect(resolveFileManagerPath(treeUnder(rootKey), `/${root}/missing.glb`)).toBeNull();
        });

        it("strips nothing from a path that only shares the root key's leading characters", () => {
            expect(resolveFileManagerPath(treeUnder(rootKey), `/${root}0/model.glb`)).toBeNull();
        });
    });

    it("strips nothing when the listing had no root entry", () => {
        // Without a root entry the root keeps its placeholder "/" key, so the root key is unknown.
        const tree = mergeFiles([fileKey("/model.glb", { key: "a1/model.glb" })], {
            ...rootTree(),
            keyPrefix: "/",
        });
        expect(tree.keyPrefix).toBe("/");
        expect(resolveFileManagerPath(tree, "/a1/model.glb")).toBeNull();
    });

    it("reads a path as asset-relative first, the form the URL write-back uses", () => {
        // The asset holds a folder named like its own root key; "/a1/x.glb" names that folder's
        // file, not the root-level x.glb, so a reload of the written-back URL selects the same node.
        const tree = treeUnder("a1/", ["/x.glb", "/a1/x.glb"]);
        expect(resolveFileManagerPath(tree, "/a1/x.glb")?.keyPrefix).toBe("a1/a1/x.glb");
        expect(resolveFileManagerPath(tree, "/a1/a1/x.glb")?.relativePath).toBe("/a1/x.glb");
    });
});
