/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    archiveNameError,
    buildZipSelection,
    defaultArchiveName,
    describeSelection,
    expiresIn,
    formatBytes,
    isTerminalStatus,
    isZipDownloadsEnabled,
    mergeZipJobs,
    progressPercent,
    withArchiveExtension,
    ZIP_POLL_INTERVAL_MS,
    zipRefetchInterval,
    ZipDownloadJob,
} from "./zipDownloads";

const job = (requestId: string, status: ZipDownloadJob["status"], over = {}): ZipDownloadJob => ({
    requestId,
    databaseId: "db1",
    assetId: "a1",
    status,
    archiveFormat: "zip",
    compression: "store",
    archiveFileName: `${requestId}.zip`,
    createdAt: "2026-11-06T00:00:00Z",
    ...over,
});

describe("isZipDownloadsEnabled", () => {
    it("is on only when the ZIPDOWNLOADS switch is present", () => {
        expect(isZipDownloadsEnabled({ featuresEnabled: ["ZIPDOWNLOADS"] })).toBe(true);
        expect(isZipDownloadsEnabled({ featuresEnabled: ["LOCATIONSERVICES"] })).toBe(false);
        expect(isZipDownloadsEnabled({ featuresEnabled: "NOOPENSEARCH,ZIPDOWNLOADS" })).toBe(true);
        expect(isZipDownloadsEnabled({})).toBe(false);
        expect(isZipDownloadsEnabled(null)).toBe(false);
    });
});

describe("buildZipSelection", () => {
    it("turns the asset root into the single whole-asset entry, whatever else is selected", () => {
        const result = buildZipSelection([
            { relativePath: "/", level: 0, isFolder: true },
            { relativePath: "/a.glb", level: 1, isFolder: false },
        ]);
        expect(result.entries).toEqual([{ type: "asset", relativePath: "/" }]);
        expect(result.wholeAsset).toBe(true);
    });

    it("keeps files with one leading slash and folders with one trailing slash", () => {
        const result = buildZipSelection([
            { relativePath: "models/a.glb", level: 2, isFolder: false },
            { relativePath: "/textures", level: 1, isFolder: true },
            { relativePath: "/maps/", keyPrefix: "x/maps/", level: 1 },
        ]);
        expect(result.entries).toEqual([
            { type: "file", relativePath: "/models/a.glb" },
            { type: "folder", relativePath: "/textures/" },
            { type: "folder", relativePath: "/maps/" },
        ]);
        expect(result.files).toBe(1);
        expect(result.folders).toBe(2);
    });

    it("drops duplicates and lists archived or deleted items as refused (#1125 M2)", () => {
        const result = buildZipSelection([
            { relativePath: "/a.glb", isFolder: false, level: 1 },
            { relativePath: "/a.glb", isFolder: false, level: 1 },
            { relativePath: "/old.glb", isFolder: false, level: 1, isArchived: true },
            { relativePath: "/gone.glb", isFolder: false, level: 1, isPermanentlyDeleted: true },
            { relativePath: "/gone.glb", isFolder: false, level: 1, isPermanentlyDeleted: true },
            { relativePath: "/olddir", isFolder: true, level: 1, isArchived: true },
        ]);
        expect(result.entries).toEqual([{ type: "file", relativePath: "/a.glb" }]);
        expect(result.refused).toEqual(["/old.glb", "/gone.glb", "/olddir/"]);
    });

    it("still refuses an archived item when the whole asset is selected too", () => {
        const result = buildZipSelection([
            { relativePath: "/", level: 0, isFolder: true },
            { relativePath: "/old.glb", isFolder: false, level: 1, isArchived: true },
        ]);
        expect(result.wholeAsset).toBe(true);
        expect(result.refused).toEqual(["/old.glb"]);
    });

    it("covers items inside a selected folder with the folder's entry (#1125 L6)", () => {
        const result = buildZipSelection([
            { relativePath: "/scans/a.e57", isFolder: false, level: 2 },
            { relativePath: "/scans", isFolder: true, level: 1 },
            { relativePath: "/scans/raw", isFolder: true, level: 2 },
            { relativePath: "/scans2/b.e57", isFolder: false, level: 2 },
        ]);
        expect(result.entries).toEqual([
            { type: "folder", relativePath: "/scans/" },
            { type: "file", relativePath: "/scans2/b.e57" },
        ]);
        expect(result.files).toBe(1);
        expect(result.folders).toBe(1);
        expect(result.refused).toEqual([]);
    });

    it("never sends a per-file version", () => {
        const result = buildZipSelection([
            { relativePath: "/a.glb", isFolder: false, level: 1, versionId: "v1" } as any,
        ]);
        expect(result.entries[0]).not.toHaveProperty("versionId");
    });
});

describe("describeSelection", () => {
    it("names files and folders, or the whole asset", () => {
        expect(describeSelection({ files: 1, folders: 2 }, "Whole asset")).toBe(
            "1 file and 2 folders"
        );
        expect(describeSelection({ files: 3 }, "Whole asset")).toBe("3 files");
        expect(describeSelection({ wholeAsset: true }, "Whole asset")).toBe("Whole asset");
    });
});

describe("archive names", () => {
    it("builds the default name from the asset name and the UTC time", () => {
        const at = new Date(Date.UTC(2026, 10, 6, 3, 4, 5));
        expect(defaultArchiveName("My Asset", "zip", at)).toBe("My Asset-20261106-030405.zip");
        expect(defaultArchiveName("a/b:c", "tar.gz", at)).toBe("a_b_c-20261106-030405.tar.gz");
        expect(defaultArchiveName("", "tar", at)).toBe("download-20261106-030405.tar");
    });

    it("swaps the extension when the format changes", () => {
        expect(withArchiveExtension("x.zip", "tar.gz")).toBe("x.tar.gz");
        expect(withArchiveExtension("x.tar.gz", "zip")).toBe("x.zip");
        expect(withArchiveExtension("x", "tar")).toBe("x.tar");
    });

    it("refuses slashes, control characters and over-long names", () => {
        expect(archiveNameError("")).toBeUndefined();
        expect(archiveNameError("ok name (1).zip")).toBeUndefined();
        expect(archiveNameError("a/b.zip")).toMatch(/slashes/);
        expect(archiveNameError("a\u0001.zip")).toMatch(/control/);
        expect(archiveNameError("x".repeat(201))).toMatch(/200/);
    });
});

describe("polling", () => {
    it("polls only while a job is queued or running, the surface is active and the tab visible", () => {
        const running = [job("2", "RUNNING"), job("1", "SUCCEEDED")];
        expect(zipRefetchInterval(running, true, true)).toBe(ZIP_POLL_INTERVAL_MS);
        expect(zipRefetchInterval([job("1", "QUEUED")], true, true)).toBe(ZIP_POLL_INTERVAL_MS);
        expect(zipRefetchInterval(running, false, true)).toBeNull();
        expect(zipRefetchInterval(running, true, false)).toBeNull();
        expect(
            zipRefetchInterval([job("1", "FAILED"), job("2", "CANCELLED")], true, true)
        ).toBeNull();
        expect(zipRefetchInterval([], true, true)).toBeNull();
    });

    it("treats the three end states as terminal", () => {
        expect(["SUCCEEDED", "FAILED", "CANCELLED"].every(isTerminalStatus)).toBe(true);
        expect(["QUEUED", "RUNNING", undefined].some((s) => isTerminalStatus(s))).toBe(false);
    });

    it("merges a fresh first page over the rows on screen, newest first", () => {
        const current = [job("3", "RUNNING"), job("1", "SUCCEEDED")];
        const fresh = [job("4", "QUEUED"), job("3", "SUCCEEDED")];
        const merged = mergeZipJobs(current, fresh);
        expect(merged.map((j) => `${j.requestId}:${j.status}`)).toEqual([
            "4:QUEUED",
            "3:SUCCEEDED",
            "1:SUCCEEDED",
        ]);
    });
});

describe("display helpers", () => {
    it("reports progress by bytes when known, else by files", () => {
        expect(
            progressPercent(job("1", "RUNNING", { progress: { bytesDone: 50, bytesTotal: 200 } }))
        ).toBe(25);
        expect(
            progressPercent(job("1", "RUNNING", { progress: { filesDone: 1, filesTotal: 4 } }))
        ).toBe(25);
        expect(progressPercent(job("1", "RUNNING", { progress: {} }))).toBeNull();
    });

    it("moves while the first, only file still streams (#1125 D11)", () => {
        // The container now reports bytes inside a member, before any file is done.
        expect(
            progressPercent(
                job("1", "RUNNING", {
                    progress: { filesDone: 0, filesTotal: 1, bytesDone: 412, bytesTotal: 1000 },
                })
            )
        ).toBe(41);
    });

    it("formats sizes and expiry", () => {
        expect(formatBytes(undefined)).toBe("-");
        expect(formatBytes(0)).toBe("0 B");
        expect(formatBytes(1536)).toBe("1.50 KB");
        const now = Date.parse("2026-11-06T00:00:00Z");
        expect(expiresIn("2026-11-13T00:00:00Z", now)).toBe("in 7 days");
        expect(expiresIn("2026-11-06T03:00:00Z", now)).toBe("in 3 hours");
        expect(expiresIn("2026-11-05T00:00:00Z", now)).toBe("Expired");
        expect(expiresIn(undefined, now)).toBe("-");
    });
});
