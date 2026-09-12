/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Compare classification used to key on the asset-relative `key` alone, so `config.json` under
 * assetA and `config.json` under assetB — two different files — read as "N versions of one file"
 * and slipped through the same-file-versions gate. Identity is database + asset + key, and a
 * selection spanning assets is a separate signal a viewer must opt into.
 */

import {
    admitsCompareShape,
    deriveCompareContext,
    deriveCompareShape,
    deriveCrossAsset,
} from "./compareShape";
import { CompareModeConfig } from "./types";

const entry = (
    key: string,
    assetId?: string,
    databaseId?: string,
    versionId?: string
): { key: string; assetId?: string; databaseId?: string; versionId?: string } => ({
    key,
    assetId,
    databaseId,
    versionId,
});

const compareConfig = (overrides: Partial<CompareModeConfig> = {}): CompareModeConfig => ({
    enabled: true,
    minFiles: 2,
    maxFiles: 2,
    allowSameFileDifferentVersions: true,
    allowDifferentFiles: true,
    ...overrides,
});

describe("deriveCompareShape", () => {
    it("is same-file-versions for one file pinned to two versions", () => {
        expect(
            deriveCompareShape([
                entry("config.json", "a1", "d1", "v1"),
                entry("config.json", "a1", "d1", "v2"),
            ])
        ).toBe("same-file-versions");
    });

    it("is same-file-versions for one file pinned once and once as latest", () => {
        expect(
            deriveCompareShape([
                entry("config.json", "a1", "d1", "v1"),
                entry("config.json", "a1", "d1"),
            ])
        ).toBe("same-file-versions");
    });

    it("is different-files for the same key under two assets", () => {
        expect(
            deriveCompareShape([entry("config.json", "a1", "d1"), entry("config.json", "a2", "d1")])
        ).toBe("different-files");
    });

    it("is different-files for the same key and asset id under two databases", () => {
        expect(
            deriveCompareShape([entry("config.json", "a1", "d1"), entry("config.json", "a1", "d2")])
        ).toBe("different-files");
    });

    it("is different-files for two keys in one asset", () => {
        expect(deriveCompareShape([entry("a.json", "a1", "d1"), entry("b.json", "a1", "d1")])).toBe(
            "different-files"
        );
    });

    it("falls back to the key when no entry names an asset (legacy single-asset callers)", () => {
        expect(
            deriveCompareShape([entry("x.txt"), entry("x.txt", undefined, undefined, "v9")])
        ).toBe("same-file-versions");
        expect(deriveCompareShape([entry("x.txt"), entry("y.txt")])).toBe("different-files");
    });
});

describe("deriveCrossAsset", () => {
    it("is false when every entry is in the same asset", () => {
        expect(
            deriveCrossAsset([entry("a.json", "a1", "d1"), entry("b.json", "a1", "d1", "v2")])
        ).toBe(false);
    });

    it("is true when entries span assets", () => {
        expect(deriveCrossAsset([entry("a.json", "a1", "d1"), entry("a.json", "a2", "d1")])).toBe(
            true
        );
    });

    it("is true when entries span databases even with the same asset id", () => {
        expect(deriveCrossAsset([entry("a.json", "a1", "d1"), entry("a.json", "a1", "d2")])).toBe(
            true
        );
    });

    it("is false when no entry names an asset", () => {
        expect(deriveCrossAsset([entry("a.json"), entry("b.json")])).toBe(false);
    });
});

describe("deriveCompareContext", () => {
    it("bundles count, shape and the cross-asset signal", () => {
        expect(
            deriveCompareContext([
                entry("config.json", "a1", "d1"),
                entry("config.json", "a2", "d1"),
            ])
        ).toEqual({ fileCount: 2, shape: "different-files", crossAsset: true });
        expect(
            deriveCompareContext([
                entry("config.json", "a1", "d1", "v1"),
                entry("config.json", "a1", "d1", "v2"),
            ])
        ).toEqual({ fileCount: 2, shape: "same-file-versions", crossAsset: false });
    });
});

describe("admitsCompareShape", () => {
    const crossAssetSelection = deriveCompareContext([
        entry("config.json", "a1", "d1"),
        entry("config.json", "a2", "d1"),
    ]);
    const sameAssetVersions = deriveCompareContext([
        entry("config.json", "a1", "d1", "v1"),
        entry("config.json", "a1", "d1", "v2"),
    ]);
    const sameAssetFiles = deriveCompareContext([
        entry("a.json", "a1", "d1"),
        entry("b.json", "a1", "d1"),
    ]);

    it("refuses a cross-asset selection unless the viewer declares allowCrossAsset", () => {
        expect(admitsCompareShape(compareConfig(), crossAssetSelection)).toBe(false);
        expect(
            admitsCompareShape(compareConfig({ allowCrossAsset: false }), crossAssetSelection)
        ).toBe(false);
        expect(
            admitsCompareShape(compareConfig({ allowCrossAsset: true }), crossAssetSelection)
        ).toBe(true);
    });

    it("still applies the different-files gate to a cross-asset selection", () => {
        expect(
            admitsCompareShape(
                compareConfig({ allowCrossAsset: true, allowDifferentFiles: false }),
                crossAssetSelection
            )
        ).toBe(false);
    });

    it("gates same-file-versions and different-files independently of allowCrossAsset", () => {
        expect(
            admitsCompareShape(
                compareConfig({ allowSameFileDifferentVersions: false }),
                sameAssetVersions
            )
        ).toBe(false);
        expect(admitsCompareShape(compareConfig(), sameAssetVersions)).toBe(true);
        expect(
            admitsCompareShape(compareConfig({ allowDifferentFiles: false }), sameAssetFiles)
        ).toBe(false);
        expect(admitsCompareShape(compareConfig(), sameAssetFiles)).toBe(true);
    });

    it("treats a context without crossAsset as single-asset", () => {
        expect(
            admitsCompareShape(compareConfig(), { fileCount: 2, shape: "different-files" })
        ).toBe(true);
    });
});
