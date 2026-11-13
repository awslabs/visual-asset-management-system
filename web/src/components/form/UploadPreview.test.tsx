/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The upload preview table (#1462): its pager is labelled for assistive technology.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import UploadPreview from "./UploadPreview";
import { UploadRoot, planUploadPaths } from "./uploadPaths";

const looseFiles = (count: number): UploadRoot[] =>
    Array.from({ length: count }, (_, i) => {
        const name = `file-${String(i).padStart(3, "0")}.txt`;
        return { kind: "file", name, handle: new File(["x"], name) } as UploadRoot;
    });

const renderPreview = (roots: UploadRoot[]) => {
    const planned = planUploadPaths(roots, {
        flow: "createAsset",
        destination: "/",
        folderNameMode: "auto",
    });
    render(
        <UploadPreview
            flow="createAsset"
            destination="/"
            roots={roots}
            planned={planned}
            folderNameMode="auto"
            onFolderNameModeChange={jest.fn()}
            onRemove={jest.fn()}
            onRemoveAll={jest.fn()}
        />
    );
};

describe("UploadPreview", () => {
    it("D1: the pager's arrows and pages have accessible names", () => {
        renderPreview(looseFiles(120));
        expect(screen.getByRole("button", { name: "Next page" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Previous page" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Go to page 2" })).toBeTruthy();
    });

    it("D2: states path errors above the table and filters to them in one click", () => {
        const dup: UploadRoot[] = [
            ...looseFiles(60),
            { kind: "file", name: "file-000.txt", handle: new File(["x"], "file-000.txt") },
        ];
        renderPreview(dup);
        const alert = screen.getByText(/2 files have path errors/);
        expect(alert).toBeTruthy();
        // The duplicate rows sort to the top, but the count is stated outside the table either way.
        fireEvent.click(screen.getByRole("button", { name: "Show errors only" }));
        expect(screen.getAllByText("Duplicate path")).toHaveLength(2);
        expect(screen.queryByText("/file-059.txt")).toBeNull();
        expect(screen.queryByRole("button", { name: "Show errors only" })).toBeNull();
    });

    it("D2: wraps long paths so the status and remove columns stay in view", () => {
        const name = `${"very-long-segment-".repeat(6)}model.gltf`;
        renderPreview([{ kind: "file", name, handle: new File(["x"], name) }]);
        const cell = screen.getByText(`/${name}`);
        expect(cell.style.overflowWrap).toBe("anywhere");
        expect(screen.getByRole("button", { name: `Remove /${name}` })).toBeTruthy();
        expect(screen.getByText("Ready")).toBeTruthy();
    });
});
