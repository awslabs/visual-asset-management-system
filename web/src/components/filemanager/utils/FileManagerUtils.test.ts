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
