/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The file page heading with a long file name (#1360).
 *
 * The heading is a raw h1 inside a Cloudscape Box, which sets a fixed body line-height; the h1
 * inherited it at its larger font size, so a name wrapping onto a second line overlapped the first.
 * jsdom does no layout, so what is pinned here is the contract the layout rests on: the heading
 * carries the class whose rule sets its own line-height and breaks a name with no spaces, it shows the
 * full name, and on a narrow screen the title takes the full width instead of half of it.
 */

import fs from "fs";
import path from "path";
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";

const LONG_NAME = "A long published assembly name_root_1_combined_without_any_spaces_x.glb";

jest.mock("../../services/APIService", () => ({
    fetchAssetWithStatus: jest.fn(
        async ({ databaseId, assetId }: { databaseId: string; assetId: string }) => [
            true,
            { assetId, databaseId, assetName: `Asset ${assetId}`, isDistributable: true },
        ]
    ),
    fetchFileInfo: jest.fn(
        async ({ assetId, fileKey }: { databaseId: string; assetId: string; fileKey: string }) => {
            const rel = decodeURIComponent(fileKey)
                .replace(new RegExp(`^/?${assetId}/`), "")
                .replace(/^\//, "");
            return [
                true,
                {
                    key: `${assetId}/${rel}`,
                    fileName: rel.split("/").pop(),
                    versions: [{ versionId: "v1", isLatest: true }],
                },
            ];
        }
    ),
}));
jest.mock("../../services/FileOperationsService", () => ({ archiveFile: jest.fn() }));
jest.mock("../metadata/FileMetadata", () => ({ __esModule: true, default: () => null }));
jest.mock("../filemanager/components/FileVersionsTable", () => ({
    FileVersionsTable: () => null,
}));
// The registry pulls in Vite's import.meta.glob, which jest cannot load; only types are used here.
jest.mock("../../visualizerPlugin/core/PluginRegistry", () => ({}));
jest.mock("../../visualizerPlugin/core/viewableExtensions", () => ({
    availableModesForFiles: () => ({ visualize: true, compare: false }),
}));
jest.mock("../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => true,
}));
jest.mock("../../visualizerPlugin/components/DynamicViewer", () => ({
    __esModule: true,
    default: () => <div data-testid="viewer" />,
}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const ViewFile = require("./ViewFile").default;

const viewFileCss = fs.readFileSync(path.join(__dirname, "ViewFile.css"), "utf8");

const openFile = async (name: string) => {
    render(
        <MemoryRouter
            initialEntries={[`/databases/db1/assets/assetA/file/${encodeURIComponent(name)}`]}
        >
            <Routes>
                <Route
                    path="/databases/:databaseId/assets/:assetId/file/*"
                    element={<ViewFile />}
                />
            </Routes>
        </MemoryRouter>
    );
    return waitFor(() => {
        const heading = screen.getByRole("heading", { level: 1 });
        expect(heading.textContent).toContain(name);
        return heading;
    });
};

describe("ViewFile heading with a long file name", () => {
    it("shows the full name in a heading with the wrapping class", async () => {
        const heading = await openFile(LONG_NAME);

        expect(heading).toHaveClass("viewfile-title");
        expect(heading.textContent).not.toContain("…");
    });

    it("gives the title the full width on a narrow screen", async () => {
        const heading = await openFile(LONG_NAME);

        // Cloudscape Grid resolves the breakpoint from the measured container width, and jsdom
        // measures 0, so this renders the narrow-screen (default) column. A plain { colspan: 6 }
        // rendered colspan-6 here: the title kept half the width of a phone screen. The desktop
        // half-width column is checked by the Playwright run against a deployed web UI.
        const column = heading.closest('[class*="colspan"]') as HTMLElement;
        expect(column).not.toBeNull();
        expect(column.className).toMatch(/colspan-12/);
        expect(column.className).not.toMatch(/colspan-6/);
    });

    it("styles the heading with its own line-height and breaks a name with no spaces", () => {
        const rule = viewFileCss.match(/\.viewfile-title\s*\{([^}]*)\}/);
        expect(rule).not.toBeNull();
        const body = rule ? rule[1] : "";
        expect(body).toMatch(/line-height:\s*var\(--line-height-heading-xl-[a-z0-9]+,\s*30px\)/);
        expect(body).toMatch(/font-size:\s*var\(--font-size-heading-xl-[a-z0-9]+,\s*24px\)/);
        expect(body).toMatch(/overflow-wrap:\s*anywhere/);
    });
});
