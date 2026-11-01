/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MoveFilesModal } from "./MoveFilesModal";
import type { FileTree } from "../types/FileManagerTypes";

jest.mock("../../../services/FileOperationsService", () => ({
    processMultipleFileOperations: jest.fn(),
}));
jest.mock("../components/FolderTreeView", () => ({
    FolderTreeView: ({ onFolderSelect }: any) => (
        <button onClick={() => onFolderSelect("/dest/")}>Pick destination</button>
    ),
}));
jest.mock("../components/AssetSelector", () => ({ AssetSelector: () => null }));

const { processMultipleFileOperations } = jest.requireMock(
    "../../../services/FileOperationsService"
);

const node = (name: string): FileTree => ({
    name,
    displayName: name,
    relativePath: `/${name}`,
    keyPrefix: `a1/${name}`,
    level: 1,
    expanded: false,
    subTree: [],
    isFolder: false,
});

const root: FileTree = {
    name: "root",
    displayName: "root",
    relativePath: "/",
    keyPrefix: "/",
    level: 0,
    expanded: true,
    subTree: [],
};

describe("MoveFilesModal results", () => {
    it("reads 'copied' after a copy with a partial failure", async () => {
        processMultipleFileOperations.mockResolvedValue([
            { filePath: "/a.glb", success: true },
            { filePath: "/b.glb", success: false, error: "Forbidden" },
        ]);
        render(
            <MoveFilesModal
                visible={true}
                onDismiss={jest.fn()}
                selectedFiles={[node("a.glb"), node("b.glb")]}
                currentAssetId="a1"
                databaseId="db1"
                fileTreeData={root}
                onSuccess={jest.fn()}
            />
        );

        fireEvent.click(screen.getByText("Copy"));
        fireEvent.click(screen.getByText("Pick destination"));
        fireEvent.click(screen.getByRole("button", { name: "Copy 2 Files" }));

        expect(await screen.findByText("Successfully copied 1 file(s)")).toBeInTheDocument();
        await waitFor(() =>
            expect(screen.getByText(/Files have been copied to:/)).toBeInTheDocument()
        );
        expect(screen.queryByText(/copyd/)).toBeNull();
    });
});
