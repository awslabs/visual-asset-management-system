/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A compare selection may span assets, and each asset is authorized independently (Casbin, per
 * asset). The diff therefore fetches every entry under ITS OWN database/asset — never the shared
 * top-level pair — and a denial on one side is that side's state, not the comparison's: the other
 * side still renders. Each side also carries its own version picker; switching one side's version
 * re-fetches that side alone.
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import TextDiffViewerComponent from "./TextDiffViewerComponent";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

const mockFetchFileVersions = jest.fn();
jest.mock("../../../services/AssetVersionService", () => ({
    fetchFileVersions: (...args: any[]) => mockFetchFileVersions(...args),
}));

// The diff library is code-split; stand in a component that prints both sides so the rendered diff
// is observable without pulling the real library into the unit run.
jest.mock("./dependencies", () => ({
    TextDiffDependencyManager: {
        loadDiffViewer: jest.fn().mockResolvedValue(undefined),
        getDiffViewer: () => (props: any) =>
            (
                <div
                    data-testid="diff"
                    data-compare-method={props.compareMethod}
                    data-hide-line-numbers={String(props.hideLineNumbers)}
                    data-show-diff-only={String(props.showDiffOnly)}
                    data-context-lines={String(props.extraLinesSurroundingDiff)}
                    data-split-view={String(props.splitView)}
                >
                    <span data-testid="diff-left">{props.oldValue}</span>
                    <span data-testid="diff-right">{props.newValue}</span>
                    <div data-testid="diff-left-title">{props.leftTitle}</div>
                    <div data-testid="diff-right-title">{props.rightTitle}</div>
                </div>
            ),
        getDiffMethod: () => ({ WORDS: "diffWords", LINES: "diffLines", CHARS: "diffChars" }),
    },
}));

// react-syntax-highlighter is only used for presentation; render the text so it can be asserted on.
jest.mock("react-syntax-highlighter", () => ({
    Light: Object.assign(({ children }: any) => <pre data-testid="highlighted">{children}</pre>, {
        registerLanguage: jest.fn(),
    }),
}));
jest.mock("react-syntax-highlighter/dist/esm/styles/hljs", () => ({ docco: {}, vs2015: {} }));
jest.mock("react-syntax-highlighter/dist/esm/languages/hljs/json", () => ({}));
jest.mock("react-syntax-highlighter/dist/esm/languages/hljs/xml", () => ({}));
jest.mock("react-syntax-highlighter/dist/esm/languages/hljs/plaintext", () => ({}));
jest.mock("react-syntax-highlighter/dist/esm/languages/hljs/htmlbars", () => ({}));
jest.mock("react-syntax-highlighter/dist/esm/languages/hljs/yaml", () => ({}));
jest.mock("react-syntax-highlighter/dist/esm/languages/hljs/ini", () => ({}));

const LEFT = {
    filename: "config.json",
    key: "config.json",
    isDirectory: false,
    assetId: "asset-A",
    databaseId: "db-1",
};
const RIGHT = {
    filename: "config.json",
    key: "config.json",
    isDirectory: false,
    assetId: "asset-B",
    databaseId: "db-1",
};

/** Presigned URL per asset so the text fetch can be told apart per side. */
const urlFor = (assetId: string, versionId?: string) =>
    `https://s3.example/${assetId}/config.json?v=${versionId ?? "latest"}`;

const bodies: Record<string, string> = {};

const renderDiff = (files = [LEFT, RIGHT]) =>
    render(
        <TextDiffViewerComponent
            assetId="asset-A"
            databaseId="db-1"
            viewerMode="wide"
            onViewerModeChange={() => undefined}
            compareMode
            compareFiles={files as any}
        />
    );

beforeEach(() => {
    jest.clearAllMocks();
    Object.keys(bodies).forEach((key) => delete bodies[key]);
    bodies[urlFor("asset-A")] = '{"a": 1}';
    bodies[urlFor("asset-B")] = '{"a": 2}';
    bodies[urlFor("asset-B", "v-old")] = '{"a": 0}';

    mockDownloadAsset.mockImplementation(async ({ assetId, versionId }: any) => [
        true,
        urlFor(assetId, versionId),
    ]);
    mockFetchFileVersions.mockResolvedValue([
        true,
        {
            versions: [
                { versionId: "v-latest", isLatest: true, lastModified: "2026-01-02T00:00:00Z" },
                { versionId: "v-old", isLatest: false, lastModified: "2026-01-01T00:00:00Z" },
            ],
        },
    ]);
    global.fetch = jest.fn(async (url: string) => ({
        ok: url in bodies,
        status: url in bodies ? 200 : 404,
        text: async () => bodies[url] ?? "",
    })) as any;
});

describe("TextDiffViewerComponent", () => {
    it("fetches each side under its own asset, defaulting to latest, and diffs them", async () => {
        renderDiff();

        await waitFor(() => expect(screen.getByTestId("diff")).toBeInTheDocument());
        expect(screen.getByTestId("diff-left").textContent).toBe('{"a": 1}');
        expect(screen.getByTestId("diff-right").textContent).toBe('{"a": 2}');

        const calls = mockDownloadAsset.mock.calls.map(([args]: any) => args);
        expect(calls).toEqual(
            expect.arrayContaining([
                expect.objectContaining({
                    assetId: "asset-A",
                    databaseId: "db-1",
                    versionId: undefined,
                }),
                expect.objectContaining({
                    assetId: "asset-B",
                    databaseId: "db-1",
                    versionId: undefined,
                }),
            ])
        );
        // Neither side was re-homed under the top-level asset.
        expect(calls.filter((c: any) => c.assetId === "asset-A")).toHaveLength(1);
        expect(calls.filter((c: any) => c.assetId === "asset-B")).toHaveLength(1);
    });

    it("shows a per-side not-authorized state on 403 and still renders the other side", async () => {
        mockDownloadAsset.mockImplementation(async ({ assetId, versionId }: any) =>
            assetId === "asset-B" ? [false, "Forbidden", 403] : [true, urlFor(assetId, versionId)]
        );

        renderDiff();

        await waitFor(() => expect(screen.getByText("Not authorized")).toBeInTheDocument());
        expect(screen.getByText("You are not authorized to view this file.")).toBeInTheDocument();
        // Left side content is still on screen; the whole diff did not collapse into one error.
        expect(screen.getByTestId("highlighted").textContent).toBe('{"a": 1}');
        expect(screen.queryByTestId("diff")).toBeNull();
        expect(screen.queryByText("Error loading diff")).toBeNull();
    });

    it("labels an archived version (410) per side", async () => {
        mockDownloadAsset.mockImplementation(async ({ assetId, versionId }: any) =>
            assetId === "asset-A"
                ? [false, "This file version has been archived and cannot be downloaded", 410]
                : [true, urlFor(assetId, versionId)]
        );

        renderDiff();

        await waitFor(() => expect(screen.getByText("Version archived")).toBeInTheDocument());
        expect(screen.getByTestId("highlighted").textContent).toBe('{"a": 2}');
    });

    it("re-fetches only the side whose version was changed", async () => {
        const { container } = renderDiff();
        await waitFor(() => expect(screen.getByTestId("diff")).toBeInTheDocument());
        expect(mockDownloadAsset).toHaveBeenCalledTimes(2);

        // Version lists were requested per side under that side's own asset.
        await waitFor(() => expect(mockFetchFileVersions).toHaveBeenCalledTimes(2));
        expect(mockFetchFileVersions.mock.calls.map(([args]: any) => args)).toEqual(
            expect.arrayContaining([
                expect.objectContaining({ assetId: "asset-A", filePath: "config.json" }),
                expect.objectContaining({ assetId: "asset-B", filePath: "config.json" }),
            ])
        );

        // Open the RIGHT picker and choose the older version (Cloudscape Select renders its
        // dropdown in a portal, so drive it through the component test-utils).
        const rightPicker = createWrapper(container).find('[data-side="right"]')?.findSelect();
        expect(rightPicker).toBeTruthy();
        await act(async () => {
            rightPicker?.openDropdown();
        });
        await act(async () => {
            rightPicker?.selectOptionByValue("v-old", { expandToViewport: true });
        });

        await waitFor(() => expect(screen.getByTestId("diff-right").textContent).toBe('{"a": 0}'));
        expect(screen.getByTestId("diff-left").textContent).toBe('{"a": 1}');

        // Exactly one more download, for asset-B pinned to v-old; asset-A was not touched.
        expect(mockDownloadAsset).toHaveBeenCalledTimes(3);
        expect(mockDownloadAsset.mock.calls[2][0]).toEqual(
            expect.objectContaining({ assetId: "asset-B", versionId: "v-old" })
        );
        // Picking a version does not refetch the version list.
        expect(mockFetchFileVersions).toHaveBeenCalledTimes(2);
    });

    // ---- Diff options (compact controls) ------------------------------------------------------

    /** Cloudscape wrappers for the controls bar; each control is reached by its label/aria-label. */
    const controls = (container: HTMLElement) =>
        createWrapper(container).find('[data-testid="text-diff-controls"]')!;

    it("starts as a line diff with line numbers, collapsing unchanged blocks around 3 lines", async () => {
        renderDiff();
        const diff = await screen.findByTestId("diff");
        expect(diff.dataset.compareMethod).toBe("diffLines");
        expect(diff.dataset.hideLineNumbers).toBe("false");
        expect(diff.dataset.showDiffOnly).toBe("true");
        expect(diff.dataset.contextLines).toBe("3");
        expect(diff.dataset.splitView).toBe("true");
    });

    it("switches the compare method to word- and character-level", async () => {
        const { container } = renderDiff();
        await screen.findByTestId("diff");

        const granularity = controls(container)
            .findAllSegmentedControls()
            .find((c) => c.findSegments().some((s) => s.getElement().textContent === "Character"))!;
        expect(granularity).toBeTruthy();

        await act(async () => {
            granularity.findSegmentById("words")!.click();
        });
        expect(screen.getByTestId("diff").dataset.compareMethod).toBe("diffWords");

        await act(async () => {
            granularity.findSegmentById("chars")!.click();
        });
        expect(screen.getByTestId("diff").dataset.compareMethod).toBe("diffChars");
    });

    it("hides line numbers and expands unchanged lines from the toggles", async () => {
        const { container } = renderDiff();
        await screen.findByTestId("diff");
        const toggles = controls(container).findAllToggles();
        const lineNumbers = toggles.find((t) => /Line numbers/.test(t.getElement().textContent!))!;
        const collapse = toggles.find((t) =>
            /Collapse unchanged/.test(t.getElement().textContent!)
        )!;

        await act(async () => {
            lineNumbers.findNativeInput().click();
        });
        expect(screen.getByTestId("diff").dataset.hideLineNumbers).toBe("true");

        // The context picker only makes sense while collapsing; it leaves with the toggle.
        expect(screen.getByLabelText("Context lines around each change")).toBeInTheDocument();
        await act(async () => {
            collapse.findNativeInput().click();
        });
        expect(screen.getByTestId("diff").dataset.showDiffOnly).toBe("false");
        expect(screen.queryByLabelText("Context lines around each change")).toBeNull();
    });

    it("passes the chosen context-line count through", async () => {
        const { container } = renderDiff();
        await screen.findByTestId("diff");
        // SegmentedControl embeds a narrow-viewport fallback Select, so reach ours via its wrapper.
        const contextPicker = createWrapper(container)
            .find('[data-testid="text-diff-context-lines"]')!
            .findSelect()!;
        expect(contextPicker).toBeTruthy();
        await act(async () => {
            contextPicker.openDropdown();
        });
        await act(async () => {
            contextPicker.selectOptionByValue("10", { expandToViewport: true });
        });
        expect(screen.getByTestId("diff").dataset.contextLines).toBe("10");
    });

    it("hands the library single-line, ellipsis-truncating titles so long labels are not clipped", async () => {
        renderDiff();
        await screen.findByTestId("diff");
        const left = screen.getByTestId("text-diff-title-left");
        const right = screen.getByTestId("text-diff-title-right");
        for (const title of [left, right]) {
            expect(title.style.whiteSpace).toBe("nowrap");
            expect(title.style.textOverflow).toBe("ellipsis");
            expect(title.style.overflow).toBe("hidden");
            expect(title.getAttribute("title")).toBe(title.textContent);
        }
        // Cross-asset: each label names its asset, and the full label is the tooltip.
        expect(left.textContent).toMatch(/config\.json \(latest\) · asset-A/);
        expect(right.textContent).toMatch(/config\.json \(latest\) · asset-B/);
        // The in-viewer side headers truncate the same way.
        expect(screen.getByTestId("text-diff-side-label-left").style.textOverflow).toBe("ellipsis");
    });

    it("carries both names in the single title block the inline layout renders", async () => {
        const { container } = renderDiff();
        await screen.findByTestId("diff");
        const layout = controls(container)
            .findAllSegmentedControls()
            .find((c) => c.findSegmentById("inline"))!;
        await act(async () => {
            layout.findSegmentById("inline")!.click();
        });
        expect(screen.getByTestId("diff").dataset.splitView).toBe("false");
        expect(screen.getByTestId("text-diff-title-left").textContent).toMatch(
            /asset-A\s+→\s+config\.json \(latest\) · asset-B/
        );
    });
});
