/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The file page remounts its viewer when the file changes, including inside one asset version.
 *
 * A viewer loads its file asynchronously (a presigned URL request, then a download) and does not
 * cancel that work when its `files` prop changes. The page keys `DynamicViewer` so a different file
 * mounts a fresh viewer and the previous one's pending work lands on an unmounted component. The key
 * carried the asset version but not the file, so moving between two files of the same asset version
 * kept the viewer mounted, and a slow response for the first file could still be applied after the
 * second was selected.
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";

let mockLocation: { pathname: string; search: string; state: unknown };

jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useLocation: () => mockLocation,
    useNavigate: () => jest.fn(),
    useParams: () => ({ databaseId: "db1", assetId: "asset1" }),
}));

jest.mock("../../services/APIService", () => ({
    fetchAsset: jest.fn().mockResolvedValue({
        assetId: "asset1",
        databaseId: "db1",
        assetName: "Seeded Asset",
        isDistributable: true,
    }),
    fetchFileInfo: jest.fn(),
}));
jest.mock("../../services/FileOperationsService", () => ({ archiveFile: jest.fn() }));
jest.mock("../metadata/FileMetadata", () => ({ __esModule: true, default: () => null }));
jest.mock("../filemanager/components/FileVersionsTable", () => ({
    FileVersionsTable: () => null,
}));

const mockMounts: string[] = [];
jest.mock("../../visualizerPlugin/components/DynamicViewer", () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const ReactInMock = require("react");
    const MockDynamicViewer = (props: { files: { key: string }[] }) => {
        const firstKey = props.files[0]?.key;
        ReactInMock.useEffect(() => {
            mockMounts.push(firstKey);
            // Mount-only: the effect records which file each viewer instance was mounted for
            // eslint-disable-next-line react-hooks/exhaustive-deps
        }, []);
        return <div data-testid="viewer">{firstKey}</div>;
    };
    return { __esModule: true, default: MockDynamicViewer };
});

// eslint-disable-next-line @typescript-eslint/no-var-requires
const ViewFile = require("./ViewFile").default;

const fileState = (key: string, versionId: string, assetVersionId?: string) => ({
    filename: key.split("/").pop(),
    key,
    isDirectory: false,
    versionId,
    assetVersionId,
});

const at = (state: unknown) => {
    mockLocation = { pathname: "/databases/db1/assets/asset1/file", search: "", state };
};

beforeEach(() => {
    mockMounts.length = 0;
});

/** Renders the page and waits for the asset fetch to settle the view type, so later mounts are the file's. */
const renderSettled = async () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { fetchAsset } = require("../../services/APIService");
    const view = render(<ViewFile />);
    await waitFor(() => expect(fetchAsset).toHaveBeenCalled());
    await act(async () => {
        await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByTestId("viewer")).toBeInTheDocument());
    return { ...view, mountsBefore: mockMounts.length };
};

describe("ViewFile viewer key", () => {
    it("mounts a new viewer for another file of the same asset version", async () => {
        at(fileState("/models/a.glb", "s3-a", "3"));
        const { rerender, mountsBefore } = await renderSettled();
        expect(screen.getByTestId("viewer")).toHaveTextContent("/models/a.glb");

        at(fileState("/models/b.glb", "s3-b", "3"));
        rerender(<ViewFile />);
        await waitFor(() =>
            expect(screen.getByTestId("viewer")).toHaveTextContent("/models/b.glb")
        );

        expect(mockMounts.slice(mountsBefore)).toEqual(["/models/b.glb"]);
    });

    it("mounts a new viewer for another file outside an asset version", async () => {
        at(fileState("/models/a.glb", "s3-a"));
        const { rerender, mountsBefore } = await renderSettled();

        at(fileState("/models/b.glb", "s3-b"));
        rerender(<ViewFile />);
        await waitFor(() =>
            expect(screen.getByTestId("viewer")).toHaveTextContent("/models/b.glb")
        );

        expect(mockMounts.slice(mountsBefore)).toEqual(["/models/b.glb"]);
    });

    it("keeps the viewer mounted while the same file re-renders", async () => {
        at(fileState("/models/a.glb", "s3-a", "3"));
        const { rerender, mountsBefore } = await renderSettled();

        at(fileState("/models/a.glb", "s3-a", "3"));
        rerender(<ViewFile />);
        await act(async () => {
            await Promise.resolve();
        });

        expect(screen.getByTestId("viewer")).toHaveTextContent("/models/a.glb");
        expect(mockMounts.slice(mountsBefore)).toEqual([]);
    });
});
