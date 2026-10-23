/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The host component presigns every selected file under its own database and asset and frames the
 * Lichtblick app with the URLs in the iframe's fragment. A presigned URL is a bearer credential:
 * it must never sit in the query string of the framed page's request (CloudFront and ALB access
 * logs record it) and never reach the console. A file the caller may not read is that file's state;
 * the other files still open.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import RoboticsDataViewerComponent from "./RoboticsDataViewerComponent";
import { DEFAULT_BASE_PATH, parseDeepLink } from "./roboticsDeepLink";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

const signed = (assetId: string, key: string) =>
    `https://s3.us-east-1.amazonaws.com/bucket/${assetId}${key}` +
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=ASIAEXAMPLE%2F20261023%2Fus-east-1" +
    "%2Fs3%2Faws4_request&X-Amz-Security-Token=abc%2B%2F%3D&X-Amz-Signature=deadbeef";

/** Presigns like the API: a signed URL per file, or the failure tuple with its HTTP status. */
const presignWith =
    (failures: Record<string, number> = {}) =>
    async ({ assetId, key }: any) =>
        key in failures
            ? [false, `request failed for ${key}`, failures[key]]
            : [true, signed(assetId, key)];

const baseProps = {
    assetId: "asset-1",
    databaseId: "db-1",
    viewerMode: "wide",
    onViewerModeChange: () => undefined,
};

const file = (key: string, assetId = "asset-1", databaseId = "db-1") => ({
    filename: key.slice(1),
    key,
    isDirectory: false,
    assetId,
    databaseId,
});

const iframeSrc = async (): Promise<string> => {
    const frame = await screen.findByTestId("robotics-viewer-iframe");
    return frame.getAttribute("src") ?? "";
};

const consoleCalls = () =>
    [console.log, console.warn, console.error, console.info, console.debug]
        .flatMap((fn) => (fn as jest.Mock).mock.calls)
        .map((args) => args.map(String).join(" "));

beforeEach(() => {
    mockDownloadAsset.mockReset();
    mockDownloadAsset.mockImplementation(presignWith());
    for (const level of ["log", "warn", "error", "info", "debug"] as const) {
        jest.spyOn(console, level).mockImplementation(() => undefined);
    }
});

afterEach(() => {
    jest.restoreAllMocks();
});

describe("RoboticsDataViewerComponent", () => {
    it("frames the app with the signed URL after # only", async () => {
        render(<RoboticsDataViewerComponent {...baseProps} assetKey="/logs/run.mcap" />);
        const src = await iframeSrc();
        const [beforeHash] = src.split("#");
        expect(beforeHash).toBe(DEFAULT_BASE_PATH);
        expect(beforeHash).not.toContain("?");
        expect(parseDeepLink(src)).toEqual(
            expect.objectContaining({
                source: "vams-remote",
                urls: [signed("asset-1", "/logs/run.mcap")],
            })
        );
    });

    it("never writes a signed URL to the console", async () => {
        render(
            <RoboticsDataViewerComponent
                {...baseProps}
                multiFileKeys={["/a.mcap", "/b.mcap"]}
                multiFiles={[file("/a.mcap"), file("/b.mcap")]}
            />
        );
        await iframeSrc();
        expect(consoleCalls().filter((line) => line.includes("X-Amz"))).toEqual([]);
    });

    it("presigns each file under its own database and asset", async () => {
        render(
            <RoboticsDataViewerComponent
                {...baseProps}
                assetVersionId="asset-version-7"
                multiFileKeys={["/a.mcap", "/b.mcap"]}
                multiFiles={[file("/a.mcap"), file("/b.mcap", "asset-2", "db-2")]}
            />
        );
        const src = await iframeSrc();
        expect(mockDownloadAsset).toHaveBeenCalledTimes(2);
        expect(mockDownloadAsset).toHaveBeenCalledWith(
            expect.objectContaining({
                databaseId: "db-1",
                assetId: "asset-1",
                key: "/a.mcap",
                assetVersionId: "asset-version-7",
                downloadType: "assetFile",
            })
        );
        // The asset version names a version of asset-1; it is not applied to another asset's file.
        const second = mockDownloadAsset.mock.calls.find(([params]) => params.key === "/b.mcap")[0];
        expect(second).toEqual(
            expect.objectContaining({ databaseId: "db-2", assetId: "asset-2", key: "/b.mcap" })
        );
        expect(second.assetVersionId).toBeUndefined();
        expect(parseDeepLink(src).urls).toEqual([
            signed("asset-1", "/a.mcap"),
            signed("asset-2", "/b.mcap"),
        ]);
    });

    it("opens the readable files and names each file it could not open", async () => {
        mockDownloadAsset.mockImplementation(presignWith({ "/b.mcap": 403, "/c.mcap": 410 }));
        render(
            <RoboticsDataViewerComponent
                {...baseProps}
                multiFileKeys={["/a.mcap", "/b.mcap", "/c.mcap"]}
                multiFiles={[file("/a.mcap"), file("/b.mcap", "asset-2"), file("/c.mcap")]}
            />
        );
        const src = await iframeSrc();
        expect(parseDeepLink(src).urls).toEqual([signed("asset-1", "/a.mcap")]);
        const banner = await screen.findByTestId("robotics-viewer-file-errors");
        expect(banner.textContent).toContain("b.mcap");
        expect(banner.textContent).toContain("not authorized");
        expect(banner.textContent).toContain("c.mcap");
        expect(banner.textContent).toContain("archived");
        expect(banner.textContent).not.toContain("a.mcap");
    });

    it("renders no iframe when no file could be prepared", async () => {
        mockDownloadAsset.mockImplementation(presignWith({ "/a.mcap": 404, "/b.mcap": 403 }));
        render(
            <RoboticsDataViewerComponent
                {...baseProps}
                multiFileKeys={["/a.mcap", "/b.mcap"]}
                multiFiles={[file("/a.mcap"), file("/b.mcap")]}
            />
        );
        const error = await screen.findByTestId("robotics-viewer-error");
        expect(error.textContent).toContain("None of the selected files");
        expect(error.textContent).toContain("could not be found");
        expect(error.textContent).toContain("not authorized");
        expect(screen.queryByTestId("robotics-viewer-iframe")).toBeNull();
    });

    it("shows a single file's failure as that file's state", async () => {
        mockDownloadAsset.mockImplementation(presignWith({ "/run.bag": 410 }));
        render(<RoboticsDataViewerComponent {...baseProps} assetKey="/run.bag" />);
        const error = await screen.findByTestId("robotics-viewer-error");
        expect(error.textContent).toContain("archived");
        expect(screen.queryByTestId("robotics-viewer-iframe")).toBeNull();
    });

    it.each([
        [["/a.mcap", "/b.bag"], /different types/],
        [["/a.bag", "/b.bag"], /one file at a time/],
        [["/a.ulg", "/b.ulg"], /one file at a time/],
    ])("refuses %j before requesting any URL", async (keys, message) => {
        render(
            <RoboticsDataViewerComponent
                {...baseProps}
                multiFileKeys={keys}
                multiFiles={keys.map((key) => file(key))}
            />
        );
        const error = await screen.findByTestId("robotics-viewer-error");
        expect(error.textContent).toMatch(message);
        expect(screen.queryByTestId("robotics-viewer-iframe")).toBeNull();
        expect(mockDownloadAsset).not.toHaveBeenCalled();
    });

    it("opens a .db3 by whole download with the configured size limit", async () => {
        render(
            <RoboticsDataViewerComponent
                {...baseProps}
                assetKey="/bags/run_0.db3"
                customParameters={{
                    basePath: "/viewers/robotics/index.html",
                    maxWholeFileBytes: 52428800,
                }}
            />
        );
        const link = parseDeepLink(await iframeSrc());
        expect(link.source).toBe("vams-download");
        expect(link.maxBytes).toBe("52428800");
        expect(link.urls).toEqual([signed("asset-1", "/bags/run_0.db3")]);
    });

    it("keeps the whole-file limit at 1 GiB when no override is configured", async () => {
        render(<RoboticsDataViewerComponent {...baseProps} assetKey="/flight.ulog" />);
        const link = parseDeepLink(await iframeSrc());
        expect(link.source).toBe("vams-download");
        expect(link.maxBytes).toBe("1073741824");
    });

    it("passes the pinned file version when one is viewed", async () => {
        render(<RoboticsDataViewerComponent {...baseProps} assetKey="/run.mcap" versionId="v-3" />);
        await iframeSrc();
        await waitFor(() =>
            expect(mockDownloadAsset).toHaveBeenCalledWith(
                expect.objectContaining({ key: "/run.mcap", versionId: "v-3" })
            )
        );
    });
});

describe("RoboticsDataViewerComponent whole-file limit", () => {
    const MiB = 1024 * 1024;
    const sized = (key: string, size?: number) => ({ ...file(key), size });

    it("refuses a split bag over the limit before requesting any URL, naming each file", async () => {
        render(
            <RoboticsDataViewerComponent
                {...baseProps}
                multiFileKeys={["/bag/run_0.db3", "/bag/run_1.db3"]}
                multiFiles={[
                    sized("/bag/run_0.db3", 600 * MiB),
                    sized("/bag/run_1.db3", 600 * MiB),
                ]}
            />
        );
        const error = await screen.findByTestId("robotics-viewer-error");
        expect(error.textContent).toBe(
            "The selected files are 1.2 GiB together (run_0.db3 600 MiB, run_1.db3 600 MiB), " +
                "larger than the 1 GiB limit for files opened by whole download."
        );
        expect(mockDownloadAsset).not.toHaveBeenCalled();
        expect(screen.queryByTestId("robotics-viewer-iframe")).toBeNull();
    });

    it("applies the configured limit", async () => {
        render(
            <RoboticsDataViewerComponent
                {...baseProps}
                multiFileKeys={["/a.db3", "/b.db3"]}
                multiFiles={[sized("/a.db3", 30 * MiB), sized("/b.db3", 30 * MiB)]}
                customParameters={{ maxWholeFileBytes: 50 * MiB }}
            />
        );
        const error = await screen.findByTestId("robotics-viewer-error");
        expect(error.textContent).toContain("larger than the 50 MiB limit");
        expect(mockDownloadAsset).not.toHaveBeenCalled();
    });

    it("opens a whole-file selection within the limit", async () => {
        render(
            <RoboticsDataViewerComponent
                {...baseProps}
                multiFileKeys={["/a.db3", "/b.db3"]}
                multiFiles={[sized("/a.db3", 512 * MiB), sized("/b.db3", 512 * MiB)]}
            />
        );
        const link = parseDeepLink(await iframeSrc());
        expect(link.source).toBe("vams-download");
        expect(link.urls).toHaveLength(2);
    });

    it("leaves a selection with an unknown size to the viewer's own check", async () => {
        render(
            <RoboticsDataViewerComponent
                {...baseProps}
                multiFileKeys={["/a.db3", "/b.db3"]}
                multiFiles={[sized("/a.db3", 2048 * MiB), sized("/b.db3")]}
            />
        );
        const link = parseDeepLink(await iframeSrc());
        expect(link.maxBytes).toBe("1073741824");
        expect(mockDownloadAsset).toHaveBeenCalledTimes(2);
    });

    it("does not limit range-read files", async () => {
        render(
            <RoboticsDataViewerComponent
                {...baseProps}
                multiFileKeys={["/a.mcap", "/b.mcap"]}
                multiFiles={[sized("/a.mcap", 2048 * MiB), sized("/b.mcap", 2048 * MiB)]}
            />
        );
        expect(parseDeepLink(await iframeSrc()).source).toBe("vams-remote");
    });
});
