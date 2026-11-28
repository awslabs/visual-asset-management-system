/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The folder download says why a file did not arrive (#2074).
 *
 * An asset can hold a file "/a.txt" and files under "/a.txt/". Keeping the asset's paths, no local folder
 * holds both, so the browser refused one of them with a TypeMismatchError: the page retried it three times,
 * marked the row Failed with no reason (the reason went only to the console) and kept the green success
 * icon on the summary. Now:
 *
 * -   Keep-paths mode refuses to start such a download, with an alert naming the file and a path in its
 *     folder, as the duplicate-names alert does in flatten mode (the rule Download as Zip and
 *     `vamscli assets download` apply, #2070). Flatten mode writes both and is unchanged.
 * -   A row that fails shows its reason, a type-mismatch write is not retried, and the summary is a warning
 *     (some files failed) or an error (none arrived), never the success icon.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";

const mockNavigate = jest.fn();
let mockState: unknown = null;

jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useLocation: () => ({ state: mockState }),
    useNavigate: () => mockNavigate,
    useParams: () => ({ databaseId: "db1", assetId: "asset1" }),
}));

jest.mock("axios", () => ({
    __esModule: true,
    default: jest.fn(async () => ({ data: { size: 4 } })),
}));

jest.mock("../services/APIService", () => ({
    downloadAsset: jest.fn(async () => [true, "https://example.invalid/file"]),
    fetchAsset: jest.fn().mockResolvedValue({ assetName: "Seeded Asset" }),
}));
jest.mock("../services/FileOperationsService", () => ({
    generateBulkDownloadUrlMap: jest.fn(async () => new Map()),
}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const AssetDownloadsPage = require("./AssetDownload").default;

const file = (relativePath: string) => {
    const name = relativePath.split("/").pop() as string;
    return {
        name,
        displayName: name,
        relativePath,
        keyPrefix: `asset1${relativePath}`,
        level: relativePath.split("/").length - 2,
        expanded: false,
        isFolder: false,
        subTree: [],
        size: 4,
    };
};

const folder = (relativePath: string, subTree: any[]) => {
    const name = relativePath.replace(/\/$/, "").split("/").pop() as string;
    return {
        name,
        displayName: name,
        relativePath,
        keyPrefix: `asset1${relativePath}`,
        level: 0,
        expanded: true,
        isFolder: true,
        subTree,
    };
};

/** The asset root holding the file "/a.txt" and the folder "/a.txt/" with "/a.txt/b.txt". */
const collidingTree = folder("/", [file("/a.txt"), folder("/a.txt/", [file("/a.txt/b.txt")])]);
const plainTree = folder("/", [file("/models/part.glb"), file("/models/other.glb")]);

const typeMismatch = () =>
    new DOMException(
        "The path supplied exists, but was not an entry of requested type.",
        "TypeMismatchError"
    );

/** A local folder handle; `refuse` names the files whose write the folder refuses with `error()`. */
const localFolder = (refuse: Set<string>, error: () => unknown = typeMismatch) => {
    const fileHandleCalls: string[] = [];
    const handle: any = {
        name: "picked",
        getDirectoryHandle: jest.fn(async () => handle),
        getFileHandle: jest.fn(async (fileName: string) => {
            fileHandleCalls.push(fileName);
            if (refuse.has(fileName)) {
                throw error();
            }
            return { createWritable: async () => ({ write: jest.fn(), close: jest.fn() }) };
        }),
    };
    return { handle, fileHandleCalls };
};

const renderPage = (fileTree: unknown) => {
    mockState = { fileTree, assetName: "Seeded Asset" };
    return render(
        <MemoryRouter>
            <AssetDownloadsPage />
        </MemoryRouter>
    );
};

const keepPaths = async (container: HTMLElement) => {
    const toggle = createWrapper(container).findToggle();
    if (!toggle) throw new Error("the download mode toggle is not on the page");
    await userEvent.click(toggle.findNativeInput().getElement());
    expect(screen.getByText(/Keep .* Paths on Download/)).toBeInTheDocument();
};

const startButton = () => screen.getByRole("button", { name: /Start Download/ });

/** The type of the summary's StatusIndicator (success, warning, error, in-progress), from its class. */
const summaryType = (container: HTMLElement): string => {
    let element: HTMLElement | null = screen.getByText(/of \d+ files completed/);
    while (element && element !== container) {
        const match = /status-(success|warning|error|in-progress)/.exec(element.className || "");
        if (match) return match[1];
        element = element.parentElement;
    }
    return "";
};

describe("AssetDownloadsPage keeps the asset's paths only when every file can be written", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        delete (window as any).showDirectoryPicker;
    });

    it("refuses a file and a folder of one name in Keep-paths mode, naming both", async () => {
        const picker = jest.fn();
        (window as any).showDirectoryPicker = picker;
        const { container } = renderPage(collidingTree);

        await keepPaths(container);

        const alert = screen.getByText(/is in a folder of the same name/);
        expect(alert.textContent).toContain('"/a.txt" is a file and "/a.txt/b.txt" is in a folder');
        expect(alert.textContent).toMatch(/Download the file and the folder separately/);
        expect(alert.textContent).toMatch(/Flatten .* Paths on Download/);
        expect(startButton()).toBeDisabled();

        await userEvent.click(startButton());
        expect(picker).not.toHaveBeenCalled();
    });

    it("downloads the same files in flatten mode, which writes them side by side", async () => {
        renderPage(collidingTree);

        expect(screen.getByText(/Flatten .* Paths on Download/)).toBeInTheDocument();
        expect(screen.queryByText(/is in a folder of the same name/)).not.toBeInTheDocument();
        expect(startButton()).toBeEnabled();
    });

    it("starts a Keep-paths download with no such pair", async () => {
        const { container } = renderPage(plainTree);

        await keepPaths(container);

        expect(screen.queryByText(/is in a folder of the same name/)).not.toBeInTheDocument();
        expect(startButton()).toBeEnabled();
    });
});

/** "/x" and "//x" (#1977), listed in the given order, beside another file. */
const emptySegmentTree = (doubleFirst: boolean) =>
    folder("/", [
        ...(doubleFirst ? [folder("//", [file("//x")])] : []),
        file("/x"),
        ...(doubleFirst ? [] : [folder("//", [file("//x")])]),
        file("/y"),
    ]);

describe("AssetDownloadsPage writes one file per local path in Keep-paths mode (#2084)", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it.each([false, true])(
        "writes /x, not //x, at x and says why //x was not written (//x listed first: %s)",
        async (doubleFirst) => {
            const local = localFolder(new Set());
            (window as any).showDirectoryPicker = jest.fn(async () => local.handle);
            const { container } = renderPage(emptySegmentTree(doubleFirst));

            await keepPaths(container);
            expect(screen.queryByText(/is in a folder of the same name/)).not.toBeInTheDocument();
            await userEvent.click(startButton());

            await waitFor(() =>
                expect(screen.getByText(/2 of 3 files completed, 1 failed/)).toBeInTheDocument()
            );
            expect(
                screen.getByText(
                    "Failed: another file of this download is saved at the same local path (/x)",
                    { exact: false }
                )
            ).toBeInTheDocument();
            expect(local.fileHandleCalls.filter((name) => name === "x")).toHaveLength(1);
            const { downloadAsset } = jest.requireMock("../services/APIService");
            const { generateBulkDownloadUrlMap } = jest.requireMock(
                "../services/FileOperationsService"
            );
            expect(generateBulkDownloadUrlMap.mock.calls[0][2]).toEqual(["asset1/x", "asset1/y"]);
            expect(downloadAsset.mock.calls.map((call: any[]) => call[0].key).sort()).toEqual([
                "asset1/x",
                "asset1/y",
            ]);
            expect(summaryType(container)).toBe("warning");
        }
    );

    it("keeps the duplicate-names alert for the same files in flatten mode", () => {
        renderPage(emptySegmentTree(false));

        expect(screen.getByText(/Duplicate File Names Detected/)).toBeInTheDocument();
        expect(startButton()).toBeDisabled();
    });
});

describe("AssetDownloadsPage says why a file failed", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("shows the reason on the row, does not retry a type mismatch, and warns in the summary", async () => {
        const local = localFolder(new Set(["part.glb"]));
        (window as any).showDirectoryPicker = jest.fn(async () => local.handle);
        const { container } = renderPage(plainTree);

        await userEvent.click(startButton());

        await waitFor(() =>
            expect(
                screen.getByText(
                    /Failed: the target folder already has a folder where this file goes/
                )
            ).toBeInTheDocument()
        );
        await waitFor(() =>
            expect(screen.getByText(/1 of 2 files completed, 1 failed/)).toBeInTheDocument()
        );
        expect(local.fileHandleCalls.filter((name) => name === "part.glb")).toHaveLength(1);
        expect(summaryType(container)).toBe("warning");
    });

    it("shows an error summary when no file arrived", async () => {
        const local = localFolder(new Set(["part.glb", "other.glb"]));
        (window as any).showDirectoryPicker = jest.fn(async () => local.handle);
        const { container } = renderPage(plainTree);

        await userEvent.click(startButton());

        await waitFor(() =>
            expect(screen.getByText(/0 of 2 files completed, 2 failed/)).toBeInTheDocument()
        );
        expect(summaryType(container)).toBe("error");
    });

    it("keeps the success summary when every file arrived", async () => {
        const local = localFolder(new Set());
        (window as any).showDirectoryPicker = jest.fn(async () => local.handle);
        const { container } = renderPage(plainTree);

        await userEvent.click(startButton());

        await waitFor(() => expect(screen.getByText(/2 of 2 files completed/)).toBeInTheDocument());
        expect(summaryType(container)).toBe("success");
    });
});
