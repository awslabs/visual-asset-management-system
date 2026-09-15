/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The download page is reachable by URL, so it must survive having no router state.
 *
 * `location.state` is null on any navigation the application did not perform itself — a refresh, a
 * bookmark, a shared link. The page read `state["fileTree"]` straight out of it, which threw
 * `TypeError: Cannot read properties of null` and handed the page to the error boundary. The boundary's
 * recovery action is "Reload", which repeats the same stateless navigation, so the single remedy offered
 * to the user could not work.
 *
 * The folder tree is chosen in the file manager and passed on navigation, so it genuinely cannot be
 * rebuilt from the URL. The right behaviour is therefore to explain that and offer a way back — which is
 * what these cases pin, along with the ordinary in-app path still rendering the download UI.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const mockNavigate = jest.fn();
let mockState: unknown = null;

jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useLocation: () => ({ state: mockState }),
    useNavigate: () => mockNavigate,
    useParams: () => ({ databaseId: "db1", assetId: "asset1" }),
}));

// The page imports axios for the actual file transfer. jest.config maps `^axios$` to
// `axios/dist/axios.js`, which the installed build does not ship, so the suite fails to load with a
// configuration error before any test runs. Mocking it here means the real module is never resolved —
// and nothing in these cases performs a transfer anyway.
jest.mock("axios", () => ({ __esModule: true, default: jest.fn() }));

jest.mock("../services/APIService", () => ({
    downloadAsset: jest.fn(),
    fetchAsset: jest.fn().mockResolvedValue({ assetName: "Seeded Asset" }),
}));
jest.mock("../services/FileOperationsService", () => ({
    generateBulkDownloadUrlMap: jest.fn().mockResolvedValue({}),
}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const AssetDownloadsPage = require("./AssetDownload").default;

const renderPage = () =>
    render(
        <MemoryRouter>
            <AssetDownloadsPage />
        </MemoryRouter>
    );

/** A minimal tree shaped like what the file manager hands over: one folder holding one file. */
const fileTree = {
    name: "models",
    displayName: "models",
    relativePath: "models",
    keyPrefix: "asset1/models/",
    level: 0,
    expanded: true,
    isFolder: true,
    subTree: [
        {
            name: "part.glb",
            displayName: "part.glb",
            relativePath: "models/part.glb",
            keyPrefix: "asset1/models/part.glb",
            level: 1,
            expanded: false,
            isFolder: false,
            subTree: [],
            size: 1024,
        },
    ],
};

describe("AssetDownloadsPage without router state", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("explains itself and offers a way back instead of throwing", async () => {
        mockState = null;
        // Rendering is the assertion: before the fix this threw inside the component.
        renderPage();
        await waitFor(() =>
            expect(screen.getByText(/Nothing selected to download/i)).toBeInTheDocument()
        );
        // The message must be actionable — saying the page is unusable without saying where to go
        // leaves the user exactly as stuck as the crash did.
        expect(screen.getByRole("button", { name: /Go to/i })).toBeInTheDocument();
    });

    it("survives a state object that exists but carries no tree", () => {
        // A partially-populated state is the other shape this can arrive in, e.g. a navigation that
        // passed only an asset name.
        mockState = { assetName: "Seeded Asset" };
        renderPage();
        expect(screen.getByText(/Nothing selected to download/i)).toBeInTheDocument();
    });

    it("still renders the download UI on the normal in-app navigation", async () => {
        // Positive control: the guard must not swallow the working path. Without this, deleting the
        // whole page body would satisfy the two cases above.
        mockState = { fileTree, assetName: "Seeded Asset" };
        renderPage();
        await waitFor(() =>
            expect(screen.queryByText(/Nothing selected to download/i)).not.toBeInTheDocument()
        );
        // Assert the heading by ROLE. A bare text match on the folder name hits several nodes (the
        // heading, the table's path cells), and "found multiple elements" is a failure even when the
        // page is working.
        expect(screen.getByRole("heading", { name: /Downloading Folder/i })).toBeInTheDocument();
    });
});
