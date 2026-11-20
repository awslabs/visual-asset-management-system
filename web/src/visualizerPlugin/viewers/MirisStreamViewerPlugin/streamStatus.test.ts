/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import {
    acceptsMirisUpload,
    isStaleStream,
    latestVersionIdFromFileInfo,
    streamDisposition,
    toStatusFilePath,
} from "./streamStatus";

describe("streamDisposition", () => {
    it.each([
        ["notStreamed", "notStreamed"],
        ["invalidUuid", "invalidUuid"],
        ["notBound", "notBound"],
        ["error", "failed"],
        ["failed", "failed"],
        ["streamable", "stream"],
        ["unknown", "stream"],
        ["preview", "processing"],
    ])("maps %s to %s", (state, disposition) => {
        expect(streamDisposition(state)).toBe(disposition);
    });
});

describe("toStatusFilePath", () => {
    it.each([
        ["asset-1/folder/scene.usd", "/asset-1/folder/scene.usd"],
        ["/asset-1/folder/scene.usd", "/asset-1/folder/scene.usd"],
        ["assets/asset-1/scene.usd", "/assets/asset-1/scene.usd"],
        ["projects/site-a/sub/scene.usd", "/projects/site-a/sub/scene.usd"],
        ["//scene.usd", "/scene.usd"],
    ])("maps %s to %s", (assetKey, expected) => {
        expect(toStatusFilePath(assetKey)).toBe(expected);
    });
});

describe("isStaleStream", () => {
    const UNKNOWN = ["", null, undefined, "null"];
    it.each([
        ["v1", "v2", true],
        ["v1", "v1", false],
        ...UNKNOWN.map((source) => [source, "v2", false]),
        ...UNKNOWN.map((viewed) => ["v1", viewed, false]),
    ])("isStaleStream(%p, %p) is %p", (sourceVersionId, viewedVersionId, stale) => {
        expect(isStaleStream(sourceVersionId as string, viewedVersionId as string)).toBe(stale);
    });
});

describe("latestVersionIdFromFileInfo", () => {
    it("returns the version flagged isLatest", () => {
        expect(
            latestVersionIdFromFileInfo({
                versions: [
                    { versionId: "v1", isLatest: false },
                    { versionId: "v2", isLatest: true },
                ],
            })
        ).toBe("v2");
    });

    it("falls back to the first listed version", () => {
        expect(latestVersionIdFromFileInfo({ versions: [{ versionId: "v3" }] })).toBe("v3");
    });

    it("parses a JSON string response", () => {
        expect(
            latestVersionIdFromFileInfo(
                JSON.stringify({ versions: [{ versionId: "v4", isLatest: true }] })
            )
        ).toBe("v4");
    });

    it.each([[undefined], ["not json"], [{}], [{ versions: [{ versionId: "null" }] }]])(
        "returns undefined for %p",
        (fileInfo) => {
            expect(latestVersionIdFromFileInfo(fileInfo)).toBeUndefined();
        }
    );
});

describe("acceptsMirisUpload", () => {
    it.each(["/scene.usd", "/a/scene.usda", "/a/scene.usdc", "/a/b/scene.usdz", "/SCENE.USDZ"])(
        "accepts %s",
        (path) => {
            expect(acceptsMirisUpload(path)).toBe(true);
        }
    );

    it.each(["/scene.glb", "/scene.usd.txt", "/usd", "/folder/"])("rejects %s", (path) => {
        expect(acceptsMirisUpload(path)).toBe(false);
    });
});
